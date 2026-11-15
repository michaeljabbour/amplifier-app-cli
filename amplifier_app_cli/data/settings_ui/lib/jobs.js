// Background commands (update check, update, reset plan, reset) and their console.
import { api } from "./api.js";
import { h, lamp, toast } from "./dom.js";
import { paint, refreshStatus } from "./store.js";

export const jobs = {};

export async function startJob(kind, options = {}) {
  try {
    jobs[kind] = { ...(await api("/api/jobs", { kind, options })), options: JSON.stringify(options) };
    pollJob(kind);
    paint();
  } catch (e) { toast(e.message); }
}

async function pollJob(kind) {
  const job = jobs[kind];
  try {
    jobs[kind] = { ...(await api(`/api/jobs?id=${job.id}`)), options: job.options };
  } catch {
    jobs[kind] = { ...job, lost: true, running: false };
  }
  paint("update", "reset", "home");
  if (jobs[kind].running) setTimeout(() => pollJob(kind), 800);
  else if (kind.startsWith("update")) refreshStatus();
}

export const confirmUpdate = () => {
  if (confirm("Update Amplifier now? It replaces the program running this page, so you'll reopen it with `amplifier settings` when it's done.")) startJob("update");
};

export function jobConsole(job) {
  if (!job) return null;
  const st = job.lost ? "lost" : job.running ? "running" : job.exit_code === 0 ? "ok" : "failed";
  const label = { running: `Running for ${job.seconds}s`, ok: "Finished", failed: `Stopped with exit code ${job.exit_code}`, lost: "The settings page lost contact" }[st];
  return h("section", { class: "console", "aria-live": "polite" },
    h("div", { class: "console-head" }, lamp(st === "ok" ? "winner" : st === "running" ? "winner unverified" : "dead"), h("span", { text: label }), h("span", { class: "spacer" }), h("code", { text: job.command })),
    h("pre", { text: st === "lost" ? `Amplifier was probably replaced while this ran, which stops this page.\nThe command keeps going; its full output is in ${job.log}.\nRun amplifier settings again to reopen this page.` : job.output || "Starting…" }));
}
