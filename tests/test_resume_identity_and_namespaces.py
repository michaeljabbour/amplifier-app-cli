"""Legacy credential identity and namespace restoration without provider calls."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_cli.runtime.config import (
    _apply_provider_overrides,
    resume_credential_overrides,
)
from amplifier_app_cli.session_spawner import (
    _extract_bundle_context,
    _extract_mention_mappings,
    resume_sub_session,
)
from amplifier_app_cli.lib.mention_loading.app_resolver import AppMentionResolver
from amplifier_foundation.mentions import BaseMentionResolver
from amplifier_foundation import Bundle
from tests.test_working_dir_pre_init import _make_child_session_mock


def legacy():
    return [
        {
            "module": "provider-anthropic",
            "source": "unchanged-source",
            "config": {
                "api_key": "[REDACTED]",
                "base_url": "https://example.test",
                "default_model": "saved-model",
                "priority": 0,
                "reasoning_effort": "medium",
                "thinking_budget_tokens": 32000,
            },
        }
    ]


def profiles():
    return [
        {
            "module": "provider-anthropic",
            "id": name,
            "config": {
                "api_key": "test-key",
                "base_url": "https://example.test",
                "priority": 99,
                "default_model": "different-model",
            },
        }
        for name in ("primary", "sonnet", "haiku")
    ]


def test_legacy_shared_credential_restored_without_changing_saved_config():
    before = legacy()
    result = _apply_provider_overrides(
        before, resume_credential_overrides(before, profiles())
    )
    expected = deepcopy(before)
    expected[0]["config"]["api_key"] = "test-key"
    assert result == expected
    assert before[0]["config"]["api_key"] == "[REDACTED]"


@pytest.mark.parametrize("difference", ["api_key", "base_url"])
def test_disagreeing_named_profiles_do_not_guess_legacy_credentials(difference):
    live = profiles()
    live[-1]["config"][difference] = "different"
    saved = legacy()
    assert (
        _apply_provider_overrides(saved, resume_credential_overrides(saved, live))
        == saved
    )


def test_exact_identity_wins_and_unknown_named_identity_is_not_migrated():
    saved = legacy()
    saved[0]["id"] = "sonnet"
    live = profiles()
    live[1]["config"]["api_key"] = "exact-key"
    assert (
        _apply_provider_overrides(saved, resume_credential_overrides(saved, live))[0][
            "config"
        ]["api_key"]
        == "exact-key"
    )
    saved[0]["id"] = "removed-profile"
    assert (
        _apply_provider_overrides(saved, resume_credential_overrides(saved, live))
        == saved
    )


def test_foundation_namespaces_survive_wrapper_and_missing_module_paths(tmp_path):
    bundle = Bundle(name="demo", base_path=tmp_path)
    foundation = BaseMentionResolver(bundles={"demo": bundle})
    wrapped = AppMentionResolver(foundation_resolver=foundation)
    assert _extract_mention_mappings(wrapped) == {"demo": str(tmp_path)}
    coordinator = SimpleNamespace(
        get=lambda name: None, get_capability=lambda name: wrapped
    )
    assert _extract_bundle_context(SimpleNamespace(coordinator=coordinator)) == {
        "module_paths": {},
        "mention_mappings": {"demo": str(tmp_path)},
    }
    wrapped._bundle_mappings = {"demo": tmp_path / "explicit"}
    assert _extract_mention_mappings(wrapped)["demo"] == str(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_mapping", [True, False])
async def test_resume_can_resolve_skills_during_initialize(tmp_path, saved_mapping):
    skills = tmp_path / "skills"
    skills.mkdir()
    child = _make_child_session_mock([])
    parent_resolver = AppMentionResolver(bundle_mappings={"demo": tmp_path})
    parent = MagicMock()
    parent.coordinator.get_capability.side_effect = lambda name: (
        parent_resolver if name == "mention_resolver" else None
    )
    metadata = {
        "config": {"session": {"orchestrator": "loop-basic"}},
        "agent_name": "test_agent",
        "parent_id": "parent",
        "working_dir": str(tmp_path),
        "bundle_context": {
            "module_paths": {},
            "mention_mappings": {"demo": str(tmp_path)} if saved_mapping else {},
        },
    }
    store = MagicMock()
    store.exists.return_value = True
    store.load.return_value = ([], metadata)

    async def initialize():
        resolver = child.coordinator.get_capability("mention_resolver")
        assert resolver.resolve("@demo:skills") == skills
        assert child.coordinator.get_capability("mention_deduplicator") is not None

    child.initialize = AsyncMock(side_effect=initialize)
    with (
        patch("amplifier_app_cli.session_spawner.AmplifierSession", return_value=child),
        patch("amplifier_app_cli.session_store.SessionStore", return_value=store),
        patch(
            "amplifier_app_cli.paths.create_foundation_resolver",
            return_value=MagicMock(),
        ),
        patch("amplifier_app_cli.ui.CLIApprovalSystem"),
        patch("amplifier_app_cli.ui.CLIDisplaySystem"),
    ):
        await resume_sub_session(
            sub_session_id="child", instruction="continue", parent_session=parent
        )
    child.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_root_resume_capability_supplies_live_parent():
    from amplifier_app_cli.session_runner import register_session_spawning

    parent = MagicMock()
    capabilities = {}
    parent.coordinator.register_capability.side_effect = capabilities.__setitem__
    resume = AsyncMock(return_value={"ok": True})
    with patch("amplifier_app_cli.session_spawner.resume_sub_session", resume):
        register_session_spawning(parent)
        await capabilities["session.resume"]("child", "continue")
    assert resume.call_args.kwargs["parent_session"] is parent
