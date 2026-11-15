// GET when there is no body, POST JSON otherwise. Errors carry the server's message,
// HTTP status and payload (e.g. `errors`, `exists`).
export async function api(path, body) {
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
