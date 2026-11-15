// Amplifier settings. One scope document (global / project / local) is edited at
// a time; settings pages are views over `draft`, and "Save changes" writes it back.
// Status, routing, update and reset come from the server, which runs the same
// code as the `amplifier` commands. All text enters the DOM as text nodes.

// ================================================================ constants
const NAV = [
  ["", [["home", "Home", "home"]]],
  ["Models", [["providers", "Providers", "chip"], ["routing", "Routing", "route"]]],
  ["Sessions", [["bundles", "Bundles", "package"], ["folders", "Folders", "folder"], ["notifications", "Notifications", "bell"]]],
  ["Access", [["keys", "API keys", "key"]]],
  ["Maintenance", [["update", "Update", "refresh"], ["reset", "Reset", "undo"]]],
  ["Advanced", [["other", "Other settings", "sliders"], ["file", "Settings file", "file"], ["effective", "In effect", "layers"]]],
];
const PAGES = {
  home: ["Home", null],
  providers: ["Providers", "The AI services Amplifier can call, in the order it tries them."],
  routing: ["Routing", "Which model does each kind of work."],
  bundles: ["Bundles", "The tools, agents and behaviours every session starts with."],
  folders: ["Folders", "Where the AI may write files."],
  notifications: ["Notifications", "How Amplifier tells you it's waiting for you."],
  keys: ["API keys", "Secrets your providers use, kept out of your settings files."],
  update: ["Update", "Get the latest Amplifier and its bundles."],
  reset: ["Reset", "Start fresh without losing what you want to keep."],
  other: ["Other settings", "Single values at the top level of the settings file."],
  file: ["Settings file", "The file itself, for anything the other pages don't cover."],
  effective: ["In effect", "What a session actually gets once every file is combined."],
};
const SCOPED = new Set(["providers", "routing", "bundles", "folders", "notifications", "other", "file"]);
const SCOPES = {
  global: ["Global", "Your defaults, everywhere"],
  project: ["Project", "Shared with everyone in this project"],
  local: ["Local", "Only you, only this project"],
};
const SECRET_KEY = /(api_key|token|secret|password)$/i;
const SECRET_NAME = /(KEY|TOKEN|SECRET|PASSWORD)$/i;
const PLACEHOLDER = /^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/;
const LIMIT_KEY = /(tokens|context_window|temperature|timeout|retries|retry_delay|concurrent|budget)/;

// Icons: 24px grid, 2px stroke. Strings are paths; [cx, cy, r] are circles.
const ICONS = {
  home: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  chip: ["M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M9 9h6v6H9z", "M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 15h3M1 9h3M1 15h3"],
  route: ["M6 3v12", [6, 18, 3], [18, 6, 3], "M18 9a9 9 0 0 1-9 9"],
  package: ["M21 8 12 3 3 8l9 5z", "M3 8v8l9 5 9-5V8", "M12 13v8"],
  folder: ["M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  bell: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  key: [[7.5, 15.5, 4.5], "M10.7 12.3 21 2", "M16 7l3 3", "M19 4l2 2"],
  refresh: ["M21 12a9 9 0 1 1-2.6-6.4L21 8", "M21 3v5h-5"],
  undo: ["M3 12a9 9 0 1 0 2.6-6.4L3 8", "M3 3v5h5"],
  sliders: ["M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"],
  file: ["M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M14 3v6h6"],
  layers: ["M12 2 2 7l10 5 10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", [12, 12, 3]],
  eyeOff: ["M3 3l18 18", "M10.6 5.1A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-2.2 3.2", "M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6", "M9.9 9.9a3 3 0 0 0 4.2 4.2"],
  pencil: ["M17 3l4 4L8 20H4v-4z"],
  plus: ["M12 5v14M5 12h14"],
  x: ["M6 6l12 12M18 6 6 18"],
  check: ["M20 6 9 17l-5-5"],
  pin: ["M12 17v5", "M9 3h6l-1 6 3 3v2H7v-2l3-3z"],
  plug: ["M9 2v6M15 2v6", "M6 8h12v4a6 6 0 0 1-12 0z", "M12 18v4"],
  play: ["M7 4l13 8-13 8z"],
  chevron: ["M9 6l6 6-6 6"],
  save: ["M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M7 3v5h8", "M7 21v-7h10v7"],
};

// ================================================================ state
let state = null; // server snapshot of all scopes (includes home status)
let scope = "global";
let tab = "home";
let draft = {};
let original = "{}";
const VIEWS = {};
// routing view state (not settings)
const R = { matrices: null, preview: null, result: null, live: true, busy: false, open: new Set(), pinning: null, pinForm: null, models: {}, arrive: true, seq: 0 };
// other page-local state
const P = { schemas: {}, revealed: {}, editingKey: null, jobs: {}, reset: null, resetRemove: null, resetFull: false, previewed: null };
const status = () => state?.status;

// ================================================================ DOM
// h(tag, props, ...kids). `text` is plain text; `md` is UI copy where `backticks` become <code>.
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = String(v);
    else if (k === "md") el.append(...md(String(v)));
    else if (k === "vars") for (const [n, val] of Object.entries(v)) el.style.setProperty(n, val);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const md = (s) => s.split("`").map((part, i) => (i % 2 ? h("code", { text: part }) : document.createTextNode(part)));
const $ = (id) => document.getElementById(id);
const clone = (v) => JSON.parse(JSON.stringify(v ?? {}));
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function icon(name) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", class: "icon", "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(k, v);
  for (const s of ICONS[name] || []) {
    const el = document.createElementNS(NS, Array.isArray(s) ? "circle" : "path");
    if (Array.isArray(s)) ["cx", "cy", "r"].forEach((a, i) => el.setAttribute(a, s[i]));
    else el.setAttribute("d", s);
    svg.append(el);
  }
  return svg;
}
// btn("Save", fn, { icon, kind: "primary" | "ghost" | "danger", small, only: icon-only, ...attrs })
function btn(label, onclick, { icon: ic, kind = "", small, only, ...attrs } = {}) {
  return h("button", {
    type: "button",
    class: ["btn", kind, small && "small", only && "icon-only"].filter(Boolean).join(" "),
    onclick,
    "aria-label": only ? label : null,
    title: only ? label : attrs.title,
    ...attrs,
  }, ic ? icon(ic) : null, only ? null : h("span", { text: label }));
}
const link = (label, href, { icon: ic, kind = "" } = {}) => h("a", { class: `btn ${kind}`, href }, ic ? icon(ic) : null, h("span", { text: label }));
const lamp = (cls, i) => h("span", { class: `lamp ${cls}`, "aria-hidden": "true", vars: i === undefined ? null : { "--i": String(i) } });
const tag = (text, cls = "") => h("span", { class: `tag ${cls}`, text });
const field = (label, input, hint) => h("label", { class: "field" }, h("span", { text: label }), input, hint ? h("small", { class: "hint", md: hint }) : null);
const textIn = (value, onchange, props = {}) => h("input", { type: "text", value: value ?? "", onchange: (e) => onchange(e.target.value), ...props });
const switchRow = (label, hint, checked, onchange) =>
  h("label", { class: "switch-row" }, h("span", {}, h("span", { text: label }), hint ? h("small", { class: "hint", md: hint }) : null), h("input", { type: "checkbox", role: "switch", checked, onchange: (e) => onchange(e.target.checked) }));
const lede = (...lines) => h("div", { class: "lede" }, lines.map((l) => (typeof l === "string" ? h("p", { md: l }) : l)));
function panel(title, hint, ...body) {
  return h("section", { class: "panel" }, title ? h("header", { class: "panel-head" }, h("h3", { text: title }), hint ? h("p", { class: "hint", md: hint }) : null) : null, body);
}
// a list with remove buttons and an add row
function listEditor({ items, show, onRemove, onAdd, placeholder, label, two }) {
  const inputs = two
    ? [h("input", { type: "text", placeholder: placeholder[0], "aria-label": `${label} name` }), h("input", { type: "text", class: "code", placeholder: placeholder[1], "aria-label": `${label} address` })]
    : [h("input", { type: "text", class: "code", placeholder, "aria-label": label })];
  return [
    items.length ? h("ul", { class: "list" }, items.map((it, i) => h("li", {}, show(it), btn("Remove", () => onRemove(it, i), { icon: "trash", only: true, kind: "ghost danger" })))) : null,
    h("div", { class: `adder${two ? " two" : ""}` }, inputs, btn("Add", () => { const v = inputs.map((x) => x.value.trim()); if (v.every(Boolean)) onAdd(...v); }, { icon: "plus" })),
  ];
}
// key/value rows with remove buttons and an add row
function kvEditor(entries, { onSet, onDel, onAdd, badge, listId }) {
  const nk = h("input", { type: "text", list: listId, placeholder: "name", "aria-label": "New setting name" });
  const nv = h("input", { type: "text", placeholder: "value", "aria-label": "New setting value" });
  return h("div", { class: "kv" },
    entries.map(([k, v]) => [
      h("span", { class: "k" }, h("code", { text: k }), badge?.(k)),
      typeof v === "boolean"
        ? h("span", {}, h("input", { type: "checkbox", role: "switch", checked: v, "aria-label": k, onchange: (e) => onSet(k, e.target.checked) }))
        : textIn(typeof v === "object" ? JSON.stringify(v) : v ?? "", (val) => onSet(k, coerce(val, v)), { type: typeof v === "number" ? "number" : "text" }),
      btn(`Remove ${k}`, () => onDel(k), { icon: "trash", only: true, kind: "ghost danger" }),
    ]),
    nk, nv, btn("Add", () => { if (nk.value.trim()) onAdd(nk.value.trim(), parseLoose(nv.value)); }, { icon: "plus" }));
}
function toast(msg) {
  const t = h("div", { md: msg });
  $("toast").append(t);
  setTimeout(() => t.remove(), 4000);
}
function fmtBytes(n) {
  if (n === null || n === undefined) return "large";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i && n < 10 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}
function fmtAgo(hrs) {
  if (hrs === null || hrs === undefined) return null;
  if (hrs < 1) return `${plural(Math.max(1, Math.round(hrs * 60)), "minute")} ago`;
  return hrs < 48 ? `${plural(Math.round(hrs), "hour")} ago` : `${plural(Math.round(hrs / 24), "day")} ago`;
}
const providerName = (p) => p.id || String(p.module).replace(/^provider-/, "");

// ================================================================ server
async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json", "X-Amplifier-Settings": "1" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: `The settings page answered ${res.status}.` }));
  if (!res.ok) throw Object.assign(new Error(data.error || "Request failed."), { status: res.status, data });
  return data;
}
const cur = () => state.scopes.find((s) => s.name === scope);
const dirty = () => JSON.stringify(draft) !== original;
function adopt(next) {
  state = next;
  draft = clone(cur().doc);
  original = JSON.stringify(draft);
  render();
}
// fresh status without touching unsaved edits
async function refreshStatus() {
  try {
    const next = await api("/api/state");
    Object.assign(state, { status: next.status, keys: next.keys });
  } catch { /* keep the last status */ }
  renderNav();
  rerenderOn("home", "update");
}
async function load() {
  try {
    adopt(await api("/api/state"));
  } catch (e) {
    $("main").replaceChildren(h("p", { class: "notice stop", md: e.message }));
  }
}
async function save() {
  $("save").disabled = true;
  try {
    const res = await api("/api/scope", { scope, doc: draft, version: cur().version });
    R.models = {};
    adopt(res);
    toast(`Saved ${SCOPES[scope][0].toLowerCase()} settings.`);
    for (const k of res.moved_keys || []) toast(`Moved a pasted key into keys.env as \`${k}\`.`);
    if (tab === "routing" || tab === "providers") refreshRouting({ matrices: true });
  } catch (e) {
    showError(e);
  } finally {
    $("save").disabled = false;
  }
}
function showError(e) {
  const box = h("div", { class: "notice stop" }, h("div", {}, h("strong", { md: e.message }), e.data?.errors ? h("ul", {}, e.data.errors.map((m) => h("li", { text: m }))) : null));
  if (e.status === 409 && !e.data?.exists) box.append(btn("Reload and drop my changes", load, { icon: "refresh" }));
  $("main").prepend(box);
  box.scrollIntoView({ block: "nearest" });
}

// ================================================================ draft helpers
function getIn(obj, path, fallback) {
  let o = obj;
  for (const k of path) {
    if (o === null || typeof o !== "object" || !(k in o)) return fallback;
    o = o[k];
  }
  return o;
}
const get = (path, fallback) => getIn(draft, path, fallback);
function set(path, value) {
  let o = draft;
  for (const k of path.slice(0, -1)) {
    if (o[k] === null || typeof o[k] !== "object") o[k] = {};
    o = o[k];
  }
  o[path.at(-1)] = value;
  changed();
}
function del(path) {
  const parent = getIn(draft, path.slice(0, -1));
  if (parent && typeof parent === "object") delete parent[path.at(-1)];
  prune(draft);
  changed();
}
// drop empty containers so a save never leaves `routing: {overrides: {}}` behind
function prune(o) {
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      prune(v);
      if (!Object.keys(v).length) delete o[k];
    }
  }
}
function changed() {
  $("savebar").hidden = !dirty();
}
// what a session sees: the last scope that sets it, using the draft for this scope
function layered(path) {
  let val;
  let from = null;
  for (const sc of state.scopes) {
    const v = getIn(sc.name === scope ? draft : sc.doc, path);
    if (v !== undefined) [val, from] = [v, sc.name];
  }
  return { val, from };
}
function layeredOverrides() {
  const overrides = {};
  const owner = {};
  for (const sc of state.scopes) {
    const o = getIn(sc.name === scope ? draft : sc.doc, ["routing", "overrides"], {});
    if (o && typeof o === "object") for (const [role, v] of Object.entries(o)) [overrides[role], owner[role]] = [v, sc.name];
  }
  return { overrides, owner };
}
function coerce(text, like) {
  const n = Number(text);
  return typeof like === "number" && text.trim() !== "" && Number.isFinite(n) ? n : text;
}
function parseLoose(text) {
  const t = text.trim();
  if (t === "true" || t === "false") return t === "true";
  return t !== "" && Number.isFinite(Number(t)) ? Number(t) : text;
}
const show = (v) => (typeof v === "string" ? v : JSON.stringify(v));
const truthy = (v) => v === true || v === "true";

// ================================================================ shell
function attention() {
  const st = status();
  if (!st || st.error) return [];
  const out = [];
  if (!st.providers.length) out.push({ level: "stop", text: "No providers are set up, so Amplifier can't start a session.", go: "providers", action: "Add a provider", icon: "plus" });
  for (const p of st.providers) for (const k of p.missing_keys) out.push({ level: "stop", text: `${p.name} has no API key (\`${k}\` is empty).`, go: "keys", action: "Add the key", icon: "key" });
  if (st.routing.missing) out.push({ level: "stop", text: `The routing matrix \`${st.routing.matrix}\` isn't on this machine.`, go: "routing", action: "Choose a matrix", icon: "route" });
  else if (st.routing.unserved?.length) out.push({ level: "wait", text: `${plural(st.routing.unserved.length, "role")} in ${st.routing.matrix} have no model you can use: ${st.routing.unserved.join(", ")}.`, go: "routing", action: "Fix routing", icon: "route" });
  const every = st.updates.every_hours || 24;
  if (st.updates.hours_ago === null || st.updates.hours_ago > every * 6) out.push({ level: "wait", text: st.updates.hours_ago === null ? "Amplifier has never checked for updates." : `Amplifier last checked for updates ${fmtAgo(st.updates.hours_ago)}.`, go: "update", action: "Check now", icon: "refresh" });
  return out;
}

function renderNav() {
  const att = attention();
  const flagFor = (id) => att.find((a) => a.go === id || (id === "providers" && a.go === "keys"));
  $("nav").replaceChildren(...NAV.map(([label, items]) => h("div", { class: "nav-group" },
    label ? h("span", { text: label }) : null,
    items.map(([id, text, ic]) => {
      const flag = flagFor(id);
      return h("a", { href: `#${id}`, "aria-current": id === tab ? "page" : null }, icon(ic), h("span", { text }), flag ? lamp(`sm ${flag.level === "stop" ? "dead" : "winner unverified"}`) : null);
    }))));
  if (status()?.version) $("version").textContent = `Version ${status().version.display}`;
}

function renderHead() {
  const [title, crumb] = PAGES[tab];
  const s = state && cur();
  const scopebox = SCOPED.has(tab) && state
    ? h("div", { class: "scopebox" },
        h("div", { class: "scope", role: "radiogroup", "aria-label": "Which settings file to edit" },
          state.scopes.map((sc) => h("button", {
            type: "button", role: "radio", "aria-checked": String(sc.name === scope), disabled: !sc.available,
            title: sc.available ? SCOPES[sc.name][1] : "Run amplifier settings inside a project folder to edit project settings.",
            onclick: () => switchScope(sc.name), text: SCOPES[sc.name][0],
          }))),
        h("span", { class: "hint", text: `${SCOPES[s.name][1]}: ${s.path}` }))
    : null;
  $("pagehead").replaceChildren(h("div", { class: "pagehead-inner" }, h("div", {}, h("h1", { text: title }), crumb ? h("p", { class: "crumb", text: crumb }) : null), scopebox));
}

function render() {
  renderNav();
  renderHead();
  changed();
  renderMain();
}
function switchScope(name) {
  if (name === scope || (dirty() && !confirm("Switch files and lose your unsaved changes?"))) return;
  scope = name;
  adopt(state);
  if (tab === "routing") refreshRouting({});
}
function renderMain() {
  const s = cur();
  const kids = SCOPED.has(tab) && s.error && tab !== "file"
    ? [h("div", { class: "notice stop" }, h("p", {}, h("strong", { text: "This settings file can't be read, so editing is turned off. " }), s.error), link("Fix the file", "#file", { icon: "file" }))]
    : VIEWS[tab](s);
  $("main").replaceChildren(...kids.flat().filter(Boolean));
}
function rerenderOn(...tabs) {
  if (tabs.includes(tab)) renderMain();
}
const scopeNote = () => (scope === "global" ? null : h("p", { class: "notice", text: `Changes here apply only in this project${scope === "project" ? ", for everyone who uses it" : ", and only for you"}. Anything you don't set comes from your global settings.` }));

// ================================================================ HOME
VIEWS.home = () => {
  const st = status();
  if (!st) return [h("p", { class: "quiet-note", text: "Checking Amplifier…" })];
  if (st.error) return [h("p", { class: "notice stop", text: st.error })];
  const att = attention();
  const worst = att.some((a) => a.level === "stop") ? "dead" : att.length ? "winner unverified" : "winner";
  const def = st.providers.find((p) => p.default);
  const upd = st.updates;
  const secrets = state.keys.filter((k) => SECRET_NAME.test(k.name));
  const saved = secrets.filter((k) => k.stored || k.in_env).length;
  const missing = secrets.filter((k) => k.referenced && !(k.stored || k.in_env)).length;
  const stale = upd.hours_ago === null || upd.hours_ago > (upd.every_hours || 24) * 6;
  const row = (go, lampCls, ic, what, detail) => h("a", { class: "status-row", href: `#${go}` }, lamp(lampCls), icon(ic), h("span", { class: "what", text: what }), h("span", { class: "detail" }, detail), icon("chevron"));
  const top = att[0]; // the main button fixes the most urgent problem
  return [
    h("section", { class: "hero" },
      h("div", { class: "hero-status" }, lamp(worst),
        h("div", {},
          h("h2", { text: att.length ? `${plural(att.length, "thing needs", "things need")} your attention` : "Amplifier is ready" }),
          h("p", { text: `Version ${st.version.display}, core ${st.version.core}. ${upd.hours_ago === null ? "Never checked for updates." : `Checked for updates ${fmtAgo(upd.hours_ago)}.`}` }))),
      h("div", { class: "hero-actions" },
        top ? link(top.action, `#${top.go}`, { icon: top.icon, kind: "primary" }) : null,
        btn("Check for updates", () => { location.hash = "#update"; startJob("update-check"); }, { icon: "refresh", kind: top ? "" : "primary" }),
        link("Change routing", "#routing", { icon: "route" }),
        link("Reset Amplifier", "#reset", { icon: "undo", kind: "ghost" }))),
    att.length ? h("div", { class: "attention" }, att.map((a, i) => h("div", { class: `notice ${a.level}` }, h("span", { md: a.text }), i ? link(a.action, `#${a.go}`, { icon: a.icon, kind: "small" }) : null))) : null,
    h("nav", { class: "status-board", "aria-label": "Status" },
      row("providers", st.providers.length && !st.providers.some((p) => p.missing_keys.length) ? "winner" : "dead", "chip", "Providers",
        st.providers.length ? [`${plural(st.providers.length, "provider")}. Default `, h("strong", { text: def.name }), def.model ? [" with ", h("code", { text: def.model })] : null] : "None set up yet"),
      row("routing", st.routing.missing ? "dead" : st.routing.unserved?.length ? "winner unverified" : "winner", "route", "Routing",
        st.routing.missing ? `Matrix ${st.routing.matrix} is missing` : [h("strong", { text: st.routing.matrix }), `, ${st.routing.roles - st.routing.unserved.length} of ${plural(st.routing.roles, "role")} served${st.routing.pinned.length ? `, ${st.routing.pinned.length} pinned by you` : ""}`]),
      row("bundles", "winner", "package", "Bundles", [h("strong", { text: st.bundle.active }), st.bundle.always ? `, plus ${st.bundle.always} always loaded` : ""]),
      row("keys", missing ? "dead" : "winner", "key", "API keys", `${plural(saved, "key")} saved${missing ? `, ${missing} missing` : ""}`),
      row("update", stale ? "winner unverified" : "winner", "refresh", "Updates", upd.hours_ago === null ? "Never checked" : `Checked ${fmtAgo(upd.hours_ago)}${upd.every_hours ? `, then every ${upd.every_hours} hours` : ""}`),
      row("other", st.unread_top_level.length ? "fallback" : "winner", "sliders", "Settings file", st.unread_top_level.length ? `${plural(st.unread_top_level.length, "setting")} the CLI doesn't read` : "Every setting is one Amplifier reads")),
  ];
};

// ================================================================ ROUTING
async function refreshRouting({ matrices = false } = {}) {
  const seq = ++R.seq;
  try {
    if (matrices || !R.matrices) R.matrices = (await api("/api/routing/matrices")).matrices;
    const inUse = layered(["routing", "matrix"]).val || "balanced";
    if (!R.preview || !R.matrices.some((m) => m.name === R.preview)) R.preview = inUse;
    const { overrides } = layeredOverrides();
    R.busy = true;
    rerenderOn("routing");
    // fast offline pass first, then the live check (a few seconds per provider at most)
    for (const live of R.live ? [false, true] : [false]) {
      const res = await api("/api/routing/resolve", { matrix: R.preview, overrides, live });
      if (seq !== R.seq) return;
      R.result = res;
      rerenderOn("routing");
    }
  } catch (e) {
    if (seq !== R.seq) return;
    R.result = { error: e.message, matrix: R.preview };
  }
  R.busy = false;
  rerenderOn("routing", "providers");
}

function previewMatrix(name) {
  Object.assign(R, { preview: name, pinning: null, pinForm: null, arrive: true });
  R.open.clear();
  refreshRouting({});
}

VIEWS.routing = () => {
  const intro = lede("Amplifier gives every task a role, like coding, quick lookups or deep reasoning. A routing matrix lists the models to try for each role, in order; the first one you can use gets the work.");
  if (!R.matrices) {
    if (!R.busy) refreshRouting({});
    return [intro, h("p", { class: "quiet-note", text: "Reading your routing matrices…" })];
  }
  if (!R.matrices.length) return [intro, h("p", { class: "notice wait", md: "No routing matrices are downloaded yet. Run `amplifier routing list` once to fetch them, then reload this page." })];
  const inUse = layered(["routing", "matrix"]);
  return [intro, plates(inUse.val || "balanced"), board(inUse.val || "balanced", inUse.from)];
};

function plates(inUseName) {
  return h("div", { class: "plates", role: "group", "aria-label": "Routing matrices" },
    R.matrices.map((m) => {
      const pct = m.total ? Math.round((m.covered / m.total) * 100) : 0;
      return h("button", { type: "button", class: "plate", "aria-pressed": String(m.name === R.preview), title: m.description, onclick: () => previewMatrix(m.name) },
        h("span", { class: "plate-top" }, h("span", { class: "plate-name", text: m.name }), m.name === inUseName ? tag("in use", "lit") : m.origin === "yours" ? tag("yours") : null),
        h("span", { class: `meter${pct === 100 ? "" : pct ? " partial" : ""}` }, h("i", { vars: { "--fill": `${pct}%` } })),
        h("span", { class: "plate-foot", text: m.total ? `${m.covered}/${m.total} roles covered` : "no roles" }));
    }));
}

function board(inUseName, inUseFrom) {
  const res = R.result;
  const m = R.matrices.find((x) => x.name === R.preview);
  const isInUse = R.preview === inUseName;
  const head = h("div", { class: "board-head" },
    h("h2", {}, R.preview, isInUse ? tag(inUseFrom ? `in use, set in ${inUseFrom} settings` : "in use by default", "lit") : null),
    m?.description ? h("p", { class: "desc", text: m.description }) : null,
    h("p", { class: "meta" }, m?.updated ? h("span", { text: `Updated ${m.updated}` }) : null, h("span", { text: m?.origin === "yours" ? "Your matrix" : "Built in" }), m?.replaces ? h("span", { text: "Replaces the built-in matrix with the same name" }) : null),
    h("div", { class: "head-actions" },
      isInUse ? null : btn(`Use ${R.preview}`, () => { set(["routing", "matrix"], R.preview); refreshRouting({}); toast(`Save to start using ${R.preview}.`); }, { icon: "check", kind: "primary" }),
      switchRow("Check live model lists", null, R.live, (on) => { R.live = on; refreshRouting({}); })));
  const legend = h("div", { class: "legend" },
    [["winner", "Gets the work"], ["winner unverified", "Gets the work, model list not checked"], ["fallback", "Backup you can use"], ["unconfigured", "Provider not set up"], ["no-match", "No model by that name"]]
      .map(([cls, text]) => h("span", {}, lamp(`sm ${cls}`), text)));

  if (!res || res.matrix !== R.preview) return h("section", { class: "board" }, head, legend, h("p", { class: "pad quiet-note", text: "Working out which model serves each role…" }));
  if (res.error) return h("section", { class: "board" }, head, h("div", { class: "pad" }, h("p", { class: "notice stop", md: res.error })));

  const notes = [];
  if (R.busy && R.live) notes.push(h("p", { class: "notice", md: "Checking each provider's live model list, so names like `claude-sonnet-*` show the exact model they pick." }));
  if (!R.busy && res.live && res.unverified.length) notes.push(h("p", { class: "notice wait", text: `Couldn't fetch the model list from ${res.unverified.join(", ")}, so those roles aren't confirmed. Test the connection on the Providers page to see why.` }));
  const unserved = res.roles.filter((r) => r.problem);
  if (unserved.length) notes.push(h("p", { class: "notice stop", text: `${unserved.length === 1 ? "One role has" : `${unserved.length} roles have`} no model you can use: ${unserved.map((r) => r.name).join(", ")}. Pin a model, add a provider, or choose another matrix.` }));

  const el = h("section", { class: `board${R.arrive ? " arrive" : ""}` },
    head, legend,
    notes.length ? h("div", { class: "pad notes" }, notes) : null,
    h("div", { role: "list" }, res.roles.map((role, i) => roleLine(role, i, res))),
    boardFoot(res, m));
  if (!R.busy) R.arrive = false;
  return el;
}

function flatConfig(cfg) {
  const flat = [];
  const walk = (o, p) => Object.entries(o || {}).forEach(([k, v]) => (v && typeof v === "object" ? walk(v, `${p}${k}.`) : flat.push([`${p}${k}`, v])));
  walk(cfg, "");
  return flat;
}
const configTags = (cfg) => flatConfig(cfg).map(([k, v]) => h("span", { class: "tag", title: k, text: `${k.split(".").at(-1).replaceAll("_", " ")}: ${v}` }));
const isUnverified = (c, res) => res.live && !c.verified && res.unverified.includes(c.provider);
const WHY = { winner: "Gets this role's work", fallback: "Backup if the one above fails", unconfigured: "Skipped: this provider isn't set up", "no-match": "Skipped: the provider lists no model by this name" };

function roleLine(role, i, res) {
  const open = R.open.has(role.name);
  const w = role.winner !== null ? role.candidates[role.winner] : null;
  const { owner } = layeredOverrides();
  const toggle = () => { R.open[open ? "delete" : "add"](role.name); renderMain(); };
  const serving = w
    ? h("div", { class: "serving" }, h("code", { class: "serving-model" }, w.model, w.resolved ? h("span", { class: "resolved", text: ` → ${w.resolved}` }) : null), h("div", { class: "serving-meta" }, h("span", { text: `via ${w.provider}` }), configTags(w.config)))
    : h("div", { class: "serving problem", text: role.problem === "no-match" ? "No listed model matches" : "No provider set up for this role" });
  return h("div", { class: `line${open ? " open" : ""}`, role: "listitem" },
    h("div", { class: "line-row", role: "button", tabindex: "0", "aria-expanded": String(open), onclick: toggle, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } } },
      lamp(w ? `winner${isUnverified(w, res) ? " unverified" : ""}` : "dead", i),
      h("div", {}, h("div", { class: "role-name" }, role.name, role.overridden ? tag(owner[role.name] ? `pinned in ${owner[role.name]}` : "pinned", "act") : null), role.description ? h("div", { class: "role-desc", text: role.description }) : null),
      serving,
      h("div", { class: "chain", title: `${role.candidates.length} models in this role's list` }, role.candidates.slice(0, 8).map((c) => lamp(`sm ${c.state}`)), role.candidates.length > 8 ? h("span", { class: "hint", text: `+${role.candidates.length - 8}` }) : null),
      h("span", { class: "chev" }, icon("chevron"))),
    open ? lineDetail(role, res, owner[role.name]) : null);
}

function lineDetail(role, res, ownerScope) {
  // fold runs of skipped candidates from one provider (gemini ships six patterns)
  const groups = [];
  for (const c of role.candidates) {
    const last = groups.at(-1);
    if (last && c.state === "unconfigured" && last[0].state === "unconfigured" && last[0].provider === c.provider) last.push(c);
    else groups.push([c]);
  }
  const step = (g) => {
    const c = g[0];
    const many = g.length > 1;
    return h("li", { class: c.state },
      lamp(`${c.state}${c.state === "winner" && isUnverified(c, res) ? " unverified" : ""}`),
      h("div", {},
        h("code", { class: "wf-model", title: many ? g.map((x) => x.model).join("\n") : null, text: many ? `${c.provider} / ${g.length} model patterns` : `${c.provider} / ${c.model}${c.resolved ? ` → ${c.resolved}` : ""}` }),
        h("div", { class: "serving-meta" }, h("span", { class: "hint", text: WHY[c.state] || c.state }), many ? null : configTags(c.config))));
  };
  const steps = role.candidates.length ? h("ol", { class: "waterfall", "aria-label": `Models tried for ${role.name}, in order` }, groups.map(step)) : h("p", { class: "hint", text: "This matrix has no models for this role yet." });
  let side;
  if (R.pinning === role.name) side = pinEditor(role, res);
  else if (role.overridden && ownerScope && ownerScope !== scope) side = h("div", { class: "pin" }, h("h4", { text: `Pinned in ${ownerScope} settings` }), h("p", { class: "hint", text: `Switch to ${SCOPES[ownerScope][0]} at the top to change it, or pin a different model here to override it for this file.` }), btn("Pin a different model here", () => openPin(role.name), { icon: "pin" }));
  else if (role.overridden) side = h("div", { class: "pin" }, h("h4", { text: "You've pinned this role" }), h("p", { class: "hint", text: "Your model is tried first; the list shows what follows it." }), h("div", { class: "actions" }, btn("Change pin", () => openPin(role.name), { icon: "pencil" }), btn("Remove pin", () => { del(["routing", "overrides", role.name]); refreshRouting({}); }, { icon: "trash", kind: "danger" })));
  else side = h("div", { class: "pin" }, h("h4", { text: "Want a different model here?" }), h("p", { class: "hint", text: "Pin one of your providers' models to this role. It applies to whichever matrix is in use." }), btn("Pin a model", () => openPin(role.name), { icon: "pin", kind: "primary" }));
  return h("div", { class: "line-detail" }, h("div", {}, steps), side);
}

function openPin(roleName) {
  R.pinning = roleName;
  R.open.add(roleName);
  renderMain();
}

async function loadModels(provider) {
  if (R.models[provider]) return;
  R.models[provider] = "loading";
  try {
    R.models[provider] = await api(`/api/routing/models?provider=${encodeURIComponent(provider)}`);
  } catch (e) {
    R.models[provider] = { models: null, error: e.message };
  }
  rerenderOn("routing", "providers");
}

function setFlat(obj, dotted, value) {
  const parts = dotted.split(".");
  let o = obj;
  for (const p of parts.slice(0, -1)) { if (!o[p] || typeof o[p] !== "object") o[p] = {}; o = o[p]; }
  if (value === "") delete o[parts.at(-1)];
  else o[parts.at(-1)] = value === "true" ? true : value === "false" ? false : value;
  prune(obj);
}

function pinEditor(role, res) {
  const providers = res.providers;
  if (!providers.length) return h("div", { class: "pin" }, h("p", { text: "Add a provider first, then come back to pin a model." }), link("Go to Providers", "#providers", { icon: "chip" }));
  const existing = (role.override?.candidates || []).find((c) => c && typeof c === "object");
  const w = role.winner !== null ? role.candidates[role.winner] : null;
  const start = existing || (w && providers.some((p) => p.name === w.provider) ? w : null) || { provider: providers[0].name, model: "", config: {} };
  const form = R.pinForm?.role === role.name ? R.pinForm : (R.pinForm = { role: role.name, provider: start.provider, model: start.model, config: clone(start.config), fallback: existing ? role.override.candidates.includes("base") : true });
  const ml = R.models[form.provider];
  if (!ml) loadModels(form.provider);
  const ptype = providers.find((p) => p.name === form.provider)?.type;
  const knobs = Object.entries(res.known_config[form.provider] || res.known_config[ptype] || {});
  const current = Object.fromEntries(flatConfig(form.config).map(([k, v]) => [k, String(v)]));
  const commit = () => {
    if (!form.model.trim()) return toast("Choose a model to pin.");
    const cand = { provider: form.provider, model: form.model.trim(), ...(Object.keys(form.config).length ? { config: clone(form.config) } : {}) };
    set(["routing", "overrides", role.name], { ...(role.description ? { description: role.description } : {}), candidates: form.fallback ? [cand, "base"] : [cand] });
    Object.assign(R, { pinning: null, pinForm: null });
    toast(`Pinned ${cand.model} to ${role.name}. Save to keep it.`);
    refreshRouting({});
  };
  return h("div", { class: "pin" },
    h("h4", { text: `Pin a model to ${role.name}` }),
    field("Provider", h("select", { onchange: (e) => { Object.assign(form, { provider: e.target.value, config: {} }); renderMain(); } }, providers.map((p) => h("option", { value: p.name, selected: p.name === form.provider, text: p.name === p.type ? p.name : `${p.name} (${p.type})` })))),
    field("Model",
      h("input", { type: "text", class: "code", value: form.model, list: `models-${role.name}`, placeholder: "exact name, or a pattern like claude-sonnet-*", oninput: (e) => { form.model = e.target.value; } }),
      ml === "loading" ? "Loading this provider's models…" : ml?.models ? `${ml.models.length} models available. Start typing to filter.` : ml ? "This provider's model list isn't available here, so type an exact name or a pattern like `claude-sonnet-*`." : null),
    h("datalist", { id: `models-${role.name}` }, (ml?.models || []).map((m) => h("option", { value: m }))),
    knobs.length ? h("div", { class: "fields" }, knobs.map(([k, values]) => field(k.split(".").at(-1).replaceAll("_", " "), h("select", { onchange: (e) => setFlat(form.config, k, e.target.value) }, h("option", { value: "", text: "Provider default" }), values.map((v) => h("option", { value: v, selected: current[k] === v, text: v })))))) : null,
    switchRow("Fall back to the matrix's list", "If your model isn't available.", form.fallback, (on) => { form.fallback = on; }),
    h("div", { class: "actions" }, btn("Pin model", commit, { icon: "pin", kind: "primary" }), btn("Cancel", () => { Object.assign(R, { pinning: null, pinForm: null }); renderMain(); }, { kind: "ghost" })));
}

function boardFoot(res, m) {
  const have = new Set(res.roles.map((r) => r.name));
  const roleIn = h("input", { type: "text", list: "known-roles", placeholder: "e.g. security-audit", "aria-label": "Role to add" });
  const nameIn = h("input", { type: "text", placeholder: `${R.preview}-mine`, "aria-label": "Name for the new matrix" });
  const addRole = () => {
    const name = roleIn.value.trim();
    if (!name) return;
    if (!have.has(name)) res.roles.push({ name, description: res.known_roles[name] || "", candidates: [], winner: null, problem: "no-provider", overridden: false });
    openPin(name);
  };
  const saveAs = async (overwrite = false) => {
    const roles = Object.fromEntries(res.roles.filter((r) => r.candidates.length).map((r) => [r.name, { description: r.description, candidates: r.candidates.map(({ provider, model, config }) => ({ provider, model, config })) }]));
    const n = nameIn.value.trim() || `${R.preview}-mine`;
    try {
      await api("/api/routing/matrix", { name: n, description: `Based on ${R.preview}${res.roles.some((r) => r.overridden) ? ", with your pins" : ""}.`, roles, overwrite });
      toast(`Saved ${n} to your matrices.`);
      Object.assign(R, { preview: n, arrive: true });
      await refreshRouting({ matrices: true });
    } catch (e) {
      if (e.data?.exists && confirm(`${e.message} Replace it?`)) return saveAs(true);
      toast(e.message);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete your matrix ${R.preview}? This removes ${m.path}.`)) return;
    try {
      await api("/api/routing/matrix/delete", { name: R.preview });
      toast(`Deleted ${R.preview}.`);
      R.preview = null;
      refreshRouting({ matrices: true });
    } catch (e) { toast(e.message); }
  };
  return h("div", { class: "board-foot" },
    h("div", { class: "foot-item" }, field("Add a role this matrix doesn't have", roleIn, "Agents that ask for it get the model you pin."), btn("Add role", addRole, { icon: "plus" })),
    h("datalist", { id: "known-roles" }, Object.entries(res.known_roles).filter(([r]) => !have.has(r)).map(([r, d]) => h("option", { value: r, label: d }))),
    h("div", { class: "foot-item" }, field("Save this view as your own matrix", nameIn, "Includes your pins. Same result as `amplifier routing create`."), btn("Save as matrix", () => saveAs(false), { icon: "save" })),
    m?.origin === "yours" ? btn("Delete this matrix", remove, { icon: "trash", kind: "danger" }) : null);
}

// ================================================================ PROVIDERS
async function loadSchema(module) {
  if (P.schemas[module]) return;
  P.schemas[module] = "loading";
  try {
    P.schemas[module] = await api(`/api/provider-schema?module=${encodeURIComponent(module)}`);
  } catch {
    P.schemas[module] = { fields: [], reads: [] };
  }
  rerenderOn("providers");
}

// a masked secret with show / change, used on Providers and API keys
function secretWidget(name, emptyText = "Not set") {
  const info = state.keys.find((k) => k.name === name) || { stored: false, in_env: false, preview: null };
  const has = info.stored || info.in_env;
  const shown = P.revealed[name];
  if (P.editingKey === name) {
    const input = h("input", { type: "password", autocomplete: "off", placeholder: "Paste the new value", "aria-label": `New value for ${name}` });
    const saveIt = async () => {
      try {
        const st = await api("/api/key", { name, value: input.value });
        Object.assign(state, { keys: st.keys, status: st.status });
        P.editingKey = null;
        delete P.revealed[name];
        R.models = {};
        toast(`Saved \`${name}\`.`);
        renderNav();
        renderMain();
      } catch (e) { toast(e.message); }
    };
    setTimeout(() => input.focus(), 0);
    return h("div", { class: "secret" }, input, btn("Save", saveIt, { icon: "check", kind: "primary" }), btn("Cancel", () => { P.editingKey = null; renderMain(); }, { icon: "x", only: true, kind: "ghost" }));
  }
  const toggleShow = async () => {
    if (shown !== undefined) delete P.revealed[name];
    else {
      try { P.revealed[name] = (await api("/api/key/reveal", { name })).value; } catch (e) { return toast(e.message); }
    }
    renderMain();
  };
  return h("div", { class: "secret" },
    h("code", { class: `value${has ? "" : " empty"}`, title: has ? (info.stored ? "Saved in keys.env" : "Set in the shell that started this page") : null, text: shown ?? (has ? info.preview : emptyText) }),
    has ? btn(shown !== undefined ? "Hide" : "Show", toggleShow, { icon: shown !== undefined ? "eyeOff" : "eye", only: true, kind: "ghost", "aria-pressed": String(shown !== undefined) }) : null,
    btn(has ? "Change" : SECRET_NAME.test(name) ? "Add key" : "Set", () => { P.editingKey = name; renderMain(); }, has ? { icon: "pencil", only: true, kind: "ghost" } : { icon: "plus", kind: SECRET_NAME.test(name) ? "primary" : "" }));
}

function providerItem(p, i, ordered, commit, serves, inUseMatrix) {
  const name = providerName(p);
  const cfg = p.config || {};
  const touch = () => set(["config", "providers"], ordered);
  const setCfg = (k, v) => { p.config = { ...(p.config || {}), [k]: v }; touch(); };
  const delCfg = (k) => { const c = { ...(p.config || {}) }; delete c[k]; p.config = c; touch(); renderMain(); };
  const schema = P.schemas[p.module];
  if (!schema) loadSchema(p.module);
  const fields = schema?.fields || [];
  const reads = schema?.reads || [];
  const shown = new Set(["default_model", "priority"]);
  const ml = R.models[name];

  const secretField = (id, label, v) => {
    shown.add(id);
    const m = typeof v === "string" && v.match(PLACEHOLDER);
    if (m) return field(label, secretWidget(m[1]), `Stored in keys.env as \`${m[1]}\`.`);
    return field(label, textIn(v ? show(v) : "", (val) => val.trim() && setCfg(id, val.trim()), { type: "password", class: "code", autocomplete: "off", placeholder: "Paste a key" }), v ? "Pasted into settings. It moves to keys.env when you save." : "Saved to keys.env, never to the settings file.");
  };
  const conn = [
    ...fields.filter((f) => f.field_type === "secret").map((f) => secretField(f.id, f.display_name || f.id, cfg[f.id])),
    ...Object.entries(cfg).filter(([k]) => SECRET_KEY.test(k) && !fields.some((f) => f.id === k)).map(([k, v]) => secretField(k, k, v)),
    ...fields.filter((f) => f.field_type === "text").map((f) => {
      shown.add(f.id);
      return field(f.display_name || f.id, textIn(cfg[f.id] === undefined ? "" : show(cfg[f.id]), (val) => (val.trim() ? setCfg(f.id, val.trim()) : delCfg(f.id)), { class: "code", placeholder: f.default ? `${f.default} (default)` : "default" }));
    }),
  ];
  const behave = fields.filter((f) => f.field_type === "boolean" || f.field_type === "choice").map((f) => {
    shown.add(f.id);
    const v = cfg[f.id];
    if (f.field_type === "boolean") return switchRow(f.display_name || f.id, f.prompt, v === undefined ? truthy(f.default) : truthy(v), (on) => setCfg(f.id, typeof v === "boolean" ? on : String(on)));
    return field(f.display_name || f.id, h("select", { onchange: (e) => (e.target.value ? setCfg(f.id, e.target.value) : delCfg(f.id)) },
      h("option", { value: "", text: f.default ? `Provider default (${f.default})` : "Provider default" }),
      (f.choices || []).map((c) => h("option", { value: c, selected: String(v) === c, text: c }))));
  });
  const limits = [...new Set([...reads, ...Object.keys(cfg)])].filter((k) => LIMIT_KEY.test(k) && !shown.has(k)).sort().map((k) => {
    shown.add(k);
    const v = cfg[k];
    return field(k.replaceAll("_", " "), textIn(v === undefined ? "" : show(v), (val) => (val.trim() ? setCfg(k, coerce(val, typeof v === "string" ? "" : 0)) : delCfg(k)), { class: "code", inputmode: "decimal", placeholder: "provider default" }));
  });
  const rest = Object.entries(cfg).filter(([k]) => !shown.has(k));
  const connTag = ml === "loading" ? tag("checking…") : ml?.models ? tag(`connected, ${ml.models.length} models`, "lit") : ml ? tag("models not listed", "wait") : null;
  const type = String(p.module).replace(/^provider-/, "");

  return h("li", {},
    h("div", { class: "rank", text: String(i + 1) }),
    h("div", { class: "prov-body" },
      h("div", { class: "prov-head" },
        h("h3", { text: name }),
        type !== name ? h("code", { class: "type", text: type }) : null,
        i === 0 ? tag("default", "act") : null,
        connTag,
        h("span", { class: "spacer" }),
        btn("Test connection", () => { delete R.models[name]; loadModels(name); renderMain(); }, { icon: "plug", small: true }),
        btn(`Move ${name} up`, () => { const a = [...ordered]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; commit(a); }, { icon: "up", only: true, kind: "ghost", disabled: i === 0 }),
        btn(`Move ${name} down`, () => { const a = [...ordered]; [a[i + 1], a[i]] = [a[i], a[i + 1]]; commit(a); }, { icon: "down", only: true, kind: "ghost", disabled: i === ordered.length - 1 }),
        btn(`Remove ${name}`, () => { if (confirm(`Remove ${name}? Roles it serves fall back to the next model in their list.`)) commit(ordered.filter((x) => x !== p)); }, { icon: "trash", only: true, kind: "ghost danger" })),
      ml && ml !== "loading" && !ml.models && ml.error ? h("p", { class: "notice wait", text: ml.error }) : null,
      serves[name]?.length ? h("p", { class: "hint", text: `Gets the work for ${plural(serves[name].length, "role")} in ${inUseMatrix}: ${serves[name].join(", ")}.` }) : null,
      h("div", { class: "fields" },
        field("Name", textIn(p.id, (v) => { if (v.trim()) p.id = v.trim(); else delete p.id; touch(); }), "How routing and `amplifier provider edit` refer to it."),
        field("Default model", textIn(cfg.default_model, (v) => setCfg("default_model", v), { class: "code", list: `pm-${i}`, onfocus: () => loadModels(name) }), ml?.models ? `${ml.models.length} models available. Start typing to filter.` : "Used when routing doesn't choose one."),
        h("datalist", { id: `pm-${i}` }, (ml?.models || []).map((m) => h("option", { value: m })))),
      conn.length ? h("div", { class: "group" }, h("h4", { text: "Connection" }), h("div", { class: "fields" }, conn)) : null,
      behave.length ? h("div", { class: "group" }, h("h4", { text: "Behaviour" }), behave) : null,
      limits.length ? h("div", { class: "group" }, h("h4", { text: "Limits and tokens" }), h("p", { class: "hint", text: "Settings this provider reads. Leave one empty to use the provider's default." }), h("div", { class: "fields" }, limits)) : null,
      h("details", { class: "group" },
        h("summary", { text: rest.length ? `Other options (${rest.length})` : "Add another option" }),
        kvEditor(rest, { onSet: setCfg, onDel: delCfg, onAdd: (k, v) => { setCfg(k, v); renderMain(); }, listId: `reads-${i}` }),
        h("datalist", { id: `reads-${i}` }, reads.filter((k) => !(k in cfg)).map((k) => h("option", { value: k }))),
        p.source ? h("p", { class: "hint", md: `Installed from \`${p.source}\`` }) : null)));
}

VIEWS.providers = () => {
  const list = (get(["config", "providers"], []) || []).filter((p) => p && typeof p === "object");
  const prio = (p) => Number(p.config?.priority ?? 999) || 999;
  const ordered = [...list].sort((a, b) => prio(a) - prio(b));
  const commit = (arr) => {
    arr.forEach((p, i) => { p.config = { ...(p.config || {}), priority: i + 1 }; });
    set(["config", "providers"], arr);
    renderMain();
  };
  const inUseMatrix = layered(["routing", "matrix"]).val || "balanced";
  const serves = {};
  if (R.result?.roles && R.result.matrix === inUseMatrix) for (const r of R.result.roles) if (r.winner !== null) (serves[r.candidates[r.winner].provider] ||= []).push(r.name);
  if (!R.result && !R.busy) refreshRouting({});

  const mod = h("select", { "aria-label": "Provider type" }, state.known_providers.map((k) => h("option", { value: k.module, text: k.module.replace(/^provider-/, "") })));
  const id = h("input", { type: "text", placeholder: "e.g. anthropic-work", "aria-label": "Name" });
  const model = h("input", { type: "text", class: "code", placeholder: "optional", "aria-label": "Default model" });
  const add = () => {
    const k = state.known_providers.find((x) => x.module === mod.value);
    const nm = id.value.trim() || k.module.replace(/^provider-/, "");
    if (ordered.some((p) => providerName(p) === nm)) return toast(`You already have a provider called ${nm}.`);
    commit([...ordered, { id: nm, module: k.module, source: k.source, config: { priority: ordered.length + 1, ...(model.value.trim() ? { default_model: model.value.trim() } : {}) } }]);
    toast(`Added ${nm}. Add its key, then save.`);
  };
  return [
    scopeNote(),
    ordered.length ? h("ol", { class: "panel chainlist" }, ordered.map((p, i) => providerItem(p, i, ordered, commit, serves, inUseMatrix))) : h("p", { class: "notice", text: scope === "global" ? "No providers yet. Add your first one below." : "This file doesn't change any providers." }),
    panel("Add a provider", "Amplifier installs it the next time it starts. For sign-in providers like ChatGPT or Copilot, use `amplifier provider add`.",
      h("div", { class: "pad" }, h("div", { class: "fields" }, field("Type", mod), field("Name", id), field("Default model", model)), h("div", { class: "actions" }, btn("Add provider", add, { icon: "plus", kind: "primary" })))),
  ];
};

// ================================================================ BUNDLES, FOLDERS, NOTIFICATIONS
VIEWS.bundles = () => {
  const added = get(["bundle", "added"], {}) || {};
  const app = get(["bundle", "app"], []) || [];
  const eff = state.effective.doc.bundle || {};
  const short = (u) => u.replace(/^git\+https:\/\/github\.com\//, "").replace(/@main$/, "");
  return [
    scopeNote(),
    panel("Main bundle", null, h("div", { class: "pad" },
      field("Bundle", textIn(get(["bundle", "active"], ""), (v) => (v.trim() ? set(["bundle", "active"], v.trim()) : del(["bundle", "active"])), { list: "bundle-names", placeholder: eff.active ? `inherits ${eff.active}` : "foundation" }), "Leave empty for the default. Same as `amplifier bundle use`."),
      h("datalist", { id: "bundle-names" }, ["foundation", ...Object.keys({ ...(eff.added || {}), ...added })].map((n) => h("option", { value: n }))))),
    panel(`Always loaded (${app.length})`, "Added on top of the main bundle in every session, in this order.",
      listEditor({ items: app, label: "Bundle address", placeholder: "git+https://github.com/org/repo@main", show: (u) => h("code", { title: u, text: short(u) }),
        onRemove: (_, i) => { const a = app.filter((__, j) => j !== i); a.length ? set(["bundle", "app"], a) : del(["bundle", "app"]); renderMain(); },
        onAdd: (u) => { if (!app.includes(u)) { set(["bundle", "app"], [...app, u]); renderMain(); } } })),
    panel("Short names", "Names you can switch to with `amplifier bundle use <name>`.",
      listEditor({ items: Object.entries(added), two: true, label: "Bundle", placeholder: ["short name", "git+https://github.com/org/repo@main"], show: ([k, v]) => h("div", { class: "two" }, h("strong", { text: k }), h("code", { title: v, text: short(v) })),
        onRemove: ([k]) => { del(["bundle", "added", k]); renderMain(); },
        onAdd: (k, v) => { set(["bundle", "added", k], v); renderMain(); } })),
  ];
};

function setFsList(key, list) {
  const tools = clone(get(["modules", "tools"], []) || []);
  let t = tools.find((x) => x && x.module === "tool-filesystem");
  if (!t) tools.push((t = { module: "tool-filesystem", config: {} }));
  t.config = { ...(t.config || {}), [key]: list };
  if (!list.length) delete t.config[key];
  if (!Object.keys(t.config).length) delete t.config;
  const keep = tools.filter((x) => !(x.module === "tool-filesystem" && !x.config && Object.keys(x).length === 1));
  if (keep.length) set(["modules", "tools"], keep);
  else del(["modules", "tools"]);
  renderMain();
}
VIEWS.folders = () => {
  const tools = get(["modules", "tools"], []);
  const cfg = (Array.isArray(tools) ? tools.find((t) => t?.module === "tool-filesystem") : null)?.config || {};
  const folderPanel = (key, title, hint, placeholder) => {
    const list = cfg[key] || [];
    return panel(`${title} (${list.length})`, hint, listEditor({ items: list, label: title, placeholder, show: (d) => h("code", { text: d }), onRemove: (d) => setFsList(key, list.filter((x) => x !== d)), onAdd: (v) => { if (!list.includes(v)) setFsList(key, [...list, v]); } }));
  };
  return [
    lede("By default the AI can only write inside the folder you start it in. Allow more folders here, or block folders it should never touch."),
    scopeNote(),
    folderPanel("allowed_write_paths", "Also allowed", "Same as `amplifier allowed-dirs add`. Use full paths.", "/Users/you/scratch"),
    folderPanel("denied_write_paths", "Never allowed", "Same as `amplifier denied-dirs add`. These win over allowed folders.", "/Users/you/.ssh"),
  ];
};

VIEWS.notifications = () => [
  scopeNote(),
  panel(null, null, h("div", { class: "pad" },
    switchRow("Desktop notification", "When the assistant finishes and is waiting for you.", truthy(get(["config", "notifications", "desktop", "enabled"])), (on) => set(["config", "notifications", "desktop", "enabled"], on)),
    switchRow("Push to your phone", "Through ntfy.sh. Set your topic with `amplifier notify ntfy`; it's kept in keys.env because anyone with it can read your notifications.", truthy(get(["config", "notifications", "push", "enabled"])), (on) => set(["config", "notifications", "push", "enabled"], on)))),
];

// ================================================================ KEYS
VIEWS.keys = () => {
  const usedBy = {};
  for (const sc of state.scopes) for (const p of getIn(sc.doc, ["config", "providers"], []) || []) {
    for (const v of Object.values(p?.config || {})) {
      const m = typeof v === "string" && v.match(PLACEHOLDER);
      if (m) (usedBy[m[1]] ||= new Set()).add(providerName(p));
    }
  }
  const card = (k) => h("div", { class: "keycard" },
    h("div", { class: "who" }, h("code", { text: k.name }), h("span", { class: "hint", text: usedBy[k.name] ? `Used by ${[...usedBy[k.name]].join(", ")}` : k.referenced ? "Used by your settings" : "Not used by any provider" })),
    secretWidget(k.name, SECRET_NAME.test(k.name) ? "Missing" : "Not set (provider default)"));
  const nk = h("input", { type: "text", class: "code", placeholder: "NEW_KEY_NAME", "aria-label": "New key name" });
  const addKey = () => {
    const n = nk.value.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(n)) return toast("Use letters, digits and underscores, like `MY_API_KEY`.");
    if (!state.keys.some((k) => k.name === n)) state.keys.push({ name: n, stored: false, in_env: false, referenced: false, preview: null });
    P.editingKey = n;
    renderMain();
  };
  const vars = state.keys.filter((k) => !SECRET_NAME.test(k.name));
  return [
    lede(`Saved in \`${state.home}/keys.env\`, which only you can read. Values are masked; use the eye to reveal one at a time.`),
    h("section", { class: "panel" }, state.keys.filter((k) => SECRET_NAME.test(k.name)).map(card), h("div", { class: "adder" }, nk, btn("Add a key", addKey, { icon: "plus" }))),
    vars.length ? panel("Other values your settings read", "Optional, like custom endpoints or context sizes. When one isn't set, the provider uses its default.", vars.map(card)) : null,
  ];
};

// ================================================================ UPDATE + RESET
async function startJob(kind, options = {}) {
  try {
    P.jobs[kind] = await api("/api/jobs", { kind, options });
    if (kind === "reset-plan") P.previewed = JSON.stringify(options);
    pollJob(kind);
    renderMain();
  } catch (e) { toast(e.message); }
}
async function pollJob(kind) {
  const job = P.jobs[kind];
  try {
    P.jobs[kind] = await api(`/api/jobs?id=${job.id}`);
  } catch {
    P.jobs[kind] = { ...job, lost: true, running: false };
  }
  rerenderOn("update", "reset");
  if (P.jobs[kind].running) setTimeout(() => pollJob(kind), 800);
  else if (kind.startsWith("update")) refreshStatus();
}
function jobConsole(job) {
  if (!job) return null;
  const st = job.lost ? "lost" : job.running ? "running" : job.exit_code === 0 ? "ok" : "failed";
  const label = { running: `Running for ${job.seconds}s`, ok: "Finished", failed: `Stopped with exit code ${job.exit_code}`, lost: "The settings page lost contact" }[st];
  return h("section", { class: "console", "aria-live": "polite" },
    h("div", { class: "console-head" }, lamp(st === "ok" ? "winner" : st === "running" ? "winner unverified" : "dead"), h("span", { text: label }), h("span", { class: "spacer" }), h("code", { text: job.command })),
    h("pre", { text: st === "lost" ? `Amplifier was probably replaced while this ran, which stops this page.\nThe command keeps going; its full output is in ${job.log}.\nRun amplifier settings again to reopen this page.` : job.output || "Starting…" }));
}

VIEWS.update = () => {
  const check = P.jobs["update-check"];
  const run = P.jobs.update;
  const busy = check?.running || run?.running;
  const st = status();
  return [
    h("section", { class: "hero" },
      h("div", {},
        h("h2", { text: `Version ${st?.version?.display || "…"}` }),
        h("p", { text: st?.updates?.hours_ago == null ? "Amplifier hasn't checked for updates yet." : `Checked ${fmtAgo(st.updates.hours_ago)}. Updating pulls the latest Amplifier, its bundles and modules; your settings, keys and sessions stay.` })),
      h("div", { class: "hero-actions" },
        btn(check?.running ? "Checking…" : "Check for updates", () => startJob("update-check"), { icon: "refresh", kind: "primary", disabled: busy }),
        btn(run?.running ? "Updating…" : "Update now", () => { if (confirm("Update Amplifier now? It replaces the program running this page, so you'll reopen it with `amplifier settings` when it's done.")) startJob("update"); }, { icon: "play", disabled: busy }))),
    jobConsole(run || check) || h("p", { class: "hint", md: "Same as `amplifier update --check-only` and `amplifier update -y`. Output appears here as it runs." }),
  ];
};

VIEWS.reset = () => {
  if (!P.reset) {
    api("/api/reset/categories").then((r) => { P.reset = r; P.resetRemove ||= new Set(r.categories.filter((c) => c.default_remove).map((c) => c.name)); renderMain(); }).catch((e) => toast(e.message));
    return [h("p", { class: "quiet-note", text: "Measuring what's in your Amplifier folder…" })];
  }
  const { categories, unmanaged, home } = P.reset;
  const opts = P.resetFull ? { full: true } : { remove: [...P.resetRemove] };
  const plan = P.jobs["reset-plan"];
  const previewed = P.previewed === JSON.stringify(opts) && plan && !plan.running && plan.exit_code === 0;
  const busy = plan?.running || P.jobs.reset?.running;
  const removing = categories.filter((c) => P.resetFull || P.resetRemove.has(c.name));
  const total = removing.reduce((n, c) => (n === null || c.bytes === null ? null : n + c.bytes), 0);
  const warn = { projects: "Your session history. Clearing it can't be undone.", keys: "You'd need to add every API key again.", settings: "You'd need to set Amplifier up again." };
  return [
    lede("Reset reinstalls Amplifier and clears what you choose from your Amplifier folder. The safe default only clears things that rebuild themselves."),
    panel("What to clear", `From ${home}`,
      h("ul", { class: "choices" }, categories.map((c) => h("li", {},
        h("input", { type: "checkbox", id: `rc-${c.name}`, checked: P.resetFull || P.resetRemove.has(c.name), disabled: P.resetFull, onchange: (e) => { P.resetRemove[e.target.checked ? "add" : "delete"](c.name); renderMain(); } }),
        h("label", { for: `rc-${c.name}` }, h("strong", { text: c.name[0].toUpperCase() + c.name.slice(1) }), h("span", { class: "hint", text: ` ${c.description}` }), warn[c.name] ? h("div", { class: "hint", text: warn[c.name] }) : null),
        h("span", { class: "size", text: c.exists ? fmtBytes(c.bytes) : "empty" })))),
      h("details", { class: "pad" }, h("summary", { text: `Always kept: ${plural(unmanaged.length, "other item")} reset doesn't manage` }), h("p", { class: "hint", text: unmanaged.join(", ") || "Nothing else." }))),
    h("section", { class: "panel danger-zone" }, h("div", { class: "pad" },
      switchRow("Full reset", "Deletes the whole Amplifier folder, including the items listed as always kept.", P.resetFull, (on) => { P.resetFull = on; renderMain(); }))),
    h("div", { class: "actions" },
      btn("Preview reset", () => startJob("reset-plan", opts), { icon: "eye", disabled: busy || (!P.resetFull && !P.resetRemove.size) }),
      btn("Reset now", () => { if (confirm(`Reset Amplifier now?\n\nThis clears ${P.resetFull ? "your entire Amplifier folder" : removing.map((c) => c.name).join(", ")}${total ? ` (${fmtBytes(total)})` : ""} and reinstalls Amplifier. This page stops while it reinstalls; reopen it with amplifier settings.`)) startJob("reset", opts); },
        { icon: "undo", kind: "danger primary", disabled: busy || !previewed, title: previewed ? null : "Preview first, so you can see exactly what will happen." }),
      h("span", { class: "hint", text: previewed ? `Clears ${fmtBytes(total)}.` : "Preview first to see exactly what will happen." })),
    jobConsole(P.jobs.reset || plan),
  ];
};

// ================================================================ ADVANCED
VIEWS.other = () => {
  const known = new Set(state.cli_top_level_keys);
  const isEmpty = (v) => v && typeof v === "object" && !Object.keys(v).length;
  const unread = Object.keys(draft).filter((k) => !known.has(k));
  return [
    scopeNote(),
    unread.length ? h("div", { class: "notice wait" }, h("span", { text: `${plural(unread.length, "setting")} in this file ${unread.length === 1 ? "isn't" : "aren't"} read by the Amplifier CLI. Most are left from older versions, though other Amplifier apps share this file and may use some. Token limits that take effect live on each provider.` }), link("Provider limits", "#providers", { icon: "chip", kind: "small" })) : null,
    panel(null, null, h("div", { class: "pad" },
      kvEditor(Object.entries(draft).filter(([, v]) => v === null || typeof v !== "object" || isEmpty(v)), {
        onSet: (k, v) => set([k], v), onDel: (k) => { del([k]); renderMain(); }, onAdd: (k, v) => { set([k], v); renderMain(); },
        badge: (k) => (known.has(k) ? null : tag("not read", "wait")),
      }))),
  ];
};

VIEWS.file = (s) => {
  const locked = dirty();
  const ta = h("textarea", { class: "yaml", spellcheck: false, disabled: locked, "aria-label": `Contents of ${s.path}` });
  ta.value = s.yaml;
  const saveYaml = async () => {
    try {
      adopt(await api("/api/scope", { scope, yaml: ta.value, version: s.version }));
      toast("Saved the settings file.");
    } catch (e) { showError(e); }
  };
  return [
    locked ? h("p", { class: "notice wait", text: "You have unsaved changes on other pages. Save or discard them before editing the file directly." }) : null,
    s.error ? h("p", { class: "notice stop", text: s.error }) : null,
    h("section", { class: "panel" }, ta, h("div", { class: "adder" }, h("span", { class: "hint", text: "Amplifier sorts keys and drops comments whenever it saves this file." }), btn(s.exists ? "Save file" : "Create file", saveYaml, { icon: "save", kind: "primary", disabled: locked }))),
  ];
};

VIEWS.effective = () => [
  lede(`For a session started in \`${state.cwd}\`: global settings, then project, then local, with later ones winning. Saved files only.`),
  state.effective.error ? h("p", { class: "notice stop", text: state.effective.error }) : null,
  h("section", { class: "panel" },
    h("div", { class: "provenance" }, Object.entries(state.effective.provenance).sort().map(([k, sc]) => tag(`${k}: ${sc}`, sc === "global" ? "" : "act"))),
    h("pre", { class: "view", text: state.effective.yaml || "Nothing is set yet." })),
];

// ================================================================ boot
function onHash() {
  const id = location.hash.slice(1);
  const next = PAGES[id] ? id : "home";
  if (next === "routing" && tab !== "routing") R.arrive = true;
  if (next === "reset" && tab !== "reset") P.reset = null;
  tab = next;
  if (state) { render(); $("main").focus({ preventScroll: true }); window.scrollTo(0, 0); }
}
window.addEventListener("hashchange", onHash);
window.addEventListener("beforeunload", (e) => { if (state && dirty()) e.preventDefault(); });
$("save").addEventListener("click", save);
$("discard").addEventListener("click", () => { adopt(state); if (tab === "routing") refreshRouting({}); });
document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (dirty()) save(); } });
onHash();
load();
