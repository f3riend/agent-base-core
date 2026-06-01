export function cfg() {
  return window.__AGENTBASE__ || {}
}

export function T(k) {
  const loc = cfg().uiLocale || "tr"
  const row = window.__UI_STRINGS__?.[loc]?.[k]
  return row != null ? String(row) : k
}

export function appPath(p) {
  const bp = String(cfg().basePath || "").replace(/\/$/, "")
  const path = p.startsWith("/") ? p : "/" + p
  return bp + path
}

export function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

export function token() {
  return String(cfg().accessToken || "").trim()
}

export function authHeaders(json) {
  const h = new Headers()
  if (json) h.set("Content-Type", "application/json")
  const t = token()
  if (t) h.set("Authorization", "Bearer " + t)
  return h
}

export function apiBase() {
  return String(cfg().apiBase || "/api").replace(/\/$/, "")
}

export async function apiRequest(path, init) {
  const url = apiBase() + (path.startsWith("/") ? path : "/" + path)
  const res = await fetch(url, init)
  const raw = await res.text()
  let data = {}
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    if (!res.ok) throw new Error("HTTP " + res.status + ": " + raw.slice(0, 200))
    return {}
  }
  if (!res.ok) {
    const d = data.detail
    const msg =
      (typeof data.error === "string" && data.error) ||
      (typeof d === "string" && d) ||
      (Array.isArray(d) && d.map((x) => (x && x.msg) || "").filter(Boolean).join(" ")) ||
      "HTTP " + res.status
    throw new Error(msg)
  }
  return data
}
