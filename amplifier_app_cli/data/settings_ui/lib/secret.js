// A masked keys.env value with show / copy / change, used on Providers, API keys
// and Notifications. Values are only fetched when the user asks to see one.
import { api } from "./api.js";
import { h, btn, toast } from "./dom.js";
import { keyInfo, adoptKeys, paint } from "./store.js";

const revealed = {};
let editing = null;

export const reveal = (name, value) => { revealed[name] = value; };
export function editKey(name) { editing = name; paint(); }

export async function saveKey(name, value) {
  try {
    adoptKeys(await api("/api/key", { name, value }));
    return true;
  } catch (e) {
    toast(e.message);
    return false;
  }
}

export function secretWidget(name, emptyText = "Not set") {
  const info = keyInfo(name) || { stored: false, in_env: false, preview: null, secret: true };
  const has = info.stored || info.in_env;
  const shown = revealed[name];
  if (editing === name) {
    const input = h("input", { type: "password", autocomplete: "off", placeholder: "Paste the new value", "aria-label": `New value for ${name}` });
    const saveIt = async () => {
      editing = null;
      delete revealed[name];
      if (await saveKey(name, input.value)) toast(`Saved \`${name}\`.`);
      else { editing = name; }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveIt(); });
    setTimeout(() => input.focus(), 0);
    return h("div", { class: "secret" }, input, btn("Save", saveIt, { icon: "check", kind: "primary" }), btn("Cancel", () => { editing = null; paint(); }, { icon: "x", only: true, kind: "ghost" }));
  }
  const toggleShow = async () => {
    if (shown !== undefined) delete revealed[name];
    else {
      try { revealed[name] = (await api("/api/key/reveal", { name })).value; } catch (e) { return toast(e.message); }
    }
    paint();
  };
  return h("div", { class: "secret" },
    h("code", { class: `value${has ? "" : " empty"}`, title: has ? (info.stored ? "Saved in keys.env" : "Set in the shell that started this page") : null, text: shown ?? (has ? info.preview : emptyText) }),
    has ? btn(shown !== undefined ? "Hide" : "Show", toggleShow, { icon: shown !== undefined ? "eyeOff" : "eye", only: true, kind: "ghost", "aria-pressed": String(shown !== undefined) }) : null,
    shown !== undefined ? btn("Copy", () => navigator.clipboard.writeText(shown).then(() => toast("Copied.")), { icon: "copy", only: true, kind: "ghost" }) : null,
    btn(has ? "Change" : info.secret ? "Add key" : "Set", () => editKey(name), has ? { icon: "pencil", only: true, kind: "ghost" } : { icon: "plus", kind: info.secret ? "primary" : "" }));
}
