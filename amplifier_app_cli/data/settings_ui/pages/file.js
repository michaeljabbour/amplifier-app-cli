// Settings file: the YAML itself, for anything the other pages don't cover.
import { h, btn } from "../lib/dom.js";
import { S, dirty, writeScope } from "../lib/store.js";

export default {
  id: "file", title: "Settings file", crumb: "The file itself, for anything the other pages don't cover.", group: "Advanced", icon: "file", scoped: true, readsBrokenFile: true,
  view(s) {
    const locked = dirty();
    const ta = h("textarea", { class: "yaml", spellcheck: false, disabled: locked, "aria-label": `Contents of ${s.path}` });
    ta.value = s.yaml;
    return [
      locked ? h("p", { class: "notice wait", text: "You have unsaved changes on other pages. Save or discard them before editing the file directly." }) : null,
      s.error ? h("p", { class: "notice stop", text: s.error }) : null,
      h("section", { class: "panel" }, ta,
        h("div", { class: "adder" }, h("span", { class: "hint", text: "Amplifier sorts keys and drops comments whenever it saves this file." }),
          btn(s.exists ? "Save file" : "Create file", () => writeScope(S.scope, { yaml: ta.value }, "Saved the settings file."), { icon: "save", kind: "primary", disabled: locked }))),
    ];
  },
};
