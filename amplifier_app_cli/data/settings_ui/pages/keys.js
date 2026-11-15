// API keys: everything in keys.env plus every ${VAR} the settings use. The server
// marks which are secrets (`secret: true`); the rest are optional values.
import { h, btn, lede, panel, toast } from "../lib/dom.js";
import { S, getIn, providerName, PLACEHOLDER } from "../lib/store.js";
import { secretWidget, editKey } from "../lib/secret.js";
import { isNtfyTopic } from "./notifications.js";

export default {
  id: "keys", title: "API keys", crumb: "Secrets your providers use, kept out of your settings files.", group: "Access", icon: "key",
  view() {
    const { keys, scopes, home } = S.state;
    const usedBy = {};
    for (const sc of scopes) for (const p of getIn(sc.doc, ["config", "providers"], []) || []) {
      for (const v of Object.values(p?.config || {})) {
        const m = typeof v === "string" && v.match(PLACEHOLDER);
        if (m) (usedBy[m[1]] ||= new Set()).add(providerName(p));
      }
    }
    const card = (k) => h("div", { class: "keycard" },
      h("div", { class: "who" }, h("code", { text: k.name }), h("span", { class: "hint", text: isNtfyTopic(k.name) ? "Your phone notifications topic" : usedBy[k.name] ? `Used by ${[...usedBy[k.name]].join(", ")}` : k.referenced ? "Used by your settings" : "Not used by any provider" }),
        k.shadowed ? h("p", { class: "notice stop", md: `Overridden: your shell or system environment sets a different \`${k.name}\`, and that one is used. To use the saved key, run \`launchctl unsetenv ${k.name}\` (macOS) and remove any \`export ${k.name}=\` from your shell profile, then open a new terminal.` }) : null),
      secretWidget(k.name, k.secret ? "Missing" : "Not set (provider default)"));
    const nk = h("input", { type: "text", class: "code", placeholder: "NEW_KEY_NAME", "aria-label": "New key name" });
    const addKey = () => {
      const n = nk.value.trim();
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(n)) return toast("Use letters, digits and underscores, like `MY_API_KEY`.");
      if (!keys.some((k) => k.name === n)) keys.push({ name: n, stored: false, in_env: false, referenced: false, secret: true, preview: null });
      editKey(n);
    };
    const values = keys.filter((k) => !k.secret);
    return [
      lede(`Saved in \`${home}/keys.env\`, which only you can read. Values are masked; use the eye to reveal one at a time.`,
        "Values exported in the terminal that started this page win over keys.env, exactly as when you run Amplifier. Keys you save here work right away."),
      h("section", { class: "panel" }, keys.filter((k) => k.secret).map(card), h("div", { class: "adder" }, nk, btn("Add a key", addKey, { icon: "plus" }))),
      values.length ? panel("Other values your settings read", "Optional, like custom endpoints or context sizes. When one isn't set, the provider uses its default.", values.map(card)) : null,
    ];
  },
};
