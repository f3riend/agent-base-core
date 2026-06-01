// Tetikleyiciler — orchestration_api /api/internal/structured-rules*
// Liste, aktif/pasif toggle, sil aksiyonları. internal-approvals.js ile aynı
// stil/IIFE deseni: dataset üzerinden api_base + token + user_id okunur.

(function () {
  "use strict"

  const root = document.getElementById("triggers-root")
  if (!root) return

  const apiBase = (root.dataset.apiBase || "").replace(/\/+$/, "")
  const token = root.dataset.token || ""
  const userId = (root.dataset.userId || "3").trim() || "3"

  let state = {
    loading: true,
    rules: [],
  }

  function authHeaders(withJson = false) {
    const h = {}
    if (withJson) h["Content-Type"] = "application/json"
    if (token) h.Authorization = "Bearer " + token
    return h
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
  }

  function withUserId(path) {
    return path + (path.includes("?") ? "&" : "?") + "user_id=" + encodeURIComponent(userId)
  }

  async function apiGet(path) {
    const res = await fetch(apiBase + withUserId(path), { headers: authHeaders(false) })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  async function apiPatch(path) {
    const res = await fetch(apiBase + withUserId(path), {
      method: "PATCH",
      headers: authHeaders(true),
    })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  async function apiDelete(path) {
    const res = await fetch(apiBase + withUserId(path), {
      method: "DELETE",
      headers: authHeaders(false),
    })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  function formatDate(raw) {
    if (!raw) return "—"
    try {
      const d = new Date(raw)
      if (Number.isNaN(d.getTime())) return String(raw)
      return d.toLocaleString("tr-TR", {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
    } catch {
      return String(raw)
    }
  }

  function ruleEvent(rule) {
    const t = rule && rule.trigger
    if (t && typeof t === "object" && t.event_type) return t.event_type
    return rule && rule.trigger_event ? rule.trigger_event : "—"
  }

  function ruleChannel(rule) {
    const c = rule && rule.content
    if (c && typeof c === "object" && c.channel) return c.channel
    return "—"
  }

  function ruleTemplate(rule) {
    const c = rule && rule.content
    if (c && typeof c === "object" && c.template) return c.template
    return rule && rule.target_template ? rule.target_template : "—"
  }

  function render() {
    if (state.loading) {
      root.innerHTML = '<p style="color:#9ca3af; padding:1rem;">Yükleniyor…</p>'
      return
    }
    if (!state.rules.length) {
      root.innerHTML = '<p style="color:#9ca3af; padding:1.5rem; text-align:center; border:1px dashed #e5e7eb; border-radius:0.75rem;">Henüz aktif tetikleyici kural yok. Zaman Tüneli\'nden yeni kural ekleyin.</p>'
      return
    }
    root.innerHTML = state.rules.map(renderCard).join("")
    root.querySelectorAll("[data-act='toggle']").forEach((btn) => {
      btn.addEventListener("click", () => onToggle(parseInt(btn.dataset.id, 10), btn.dataset.next === "true"))
    })
    root.querySelectorAll("[data-act='delete']").forEach((btn) => {
      btn.addEventListener("click", () => onDelete(parseInt(btn.dataset.id, 10), btn.dataset.name || ""))
    })
  }

  function renderCard(rule) {
    const enabled = Boolean(rule.enabled)
    const nextEnabled = !enabled
    const name = esc(rule.name || `Kural #${rule.id}`)
    const eventType = esc(ruleEvent(rule))
    const channel = esc(ruleChannel(rule))
    const tpl = esc(ruleTemplate(rule))
    const lastFired = formatDate(rule.last_fired_at)
    const fireCount = Number(rule.fire_count || 0)
    const confidence = typeof rule.parse_confidence === "number"
      ? Math.round(rule.parse_confidence * 100) + "%"
      : "—"
    const explanation = esc(rule.explanation || rule.natural_language || "")
    const statusColor = enabled ? "#16a34a" : "#9ca3af"
    const statusLabel = enabled ? "Aktif" : "Pasif"
    return [
      '<article style="border:1px solid #e5e7eb; border-radius:0.75rem; background:#fff; padding:1rem;">',
      '  <div style="display:flex; justify-content:space-between; gap:1rem; align-items:flex-start;">',
      '    <div style="flex:1; min-width:0;">',
      '      <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.4rem; flex-wrap:wrap;">',
      '        <span style="background:' + statusColor + '15; color:' + statusColor + '; padding:0.15rem 0.55rem; border-radius:9999px; font-size:0.7rem; font-weight:700; text-transform:uppercase;">' + statusLabel + '</span>',
      '        <span style="color:#6b7280; font-size:0.75rem;">#' + esc(rule.id) + '</span>',
      '        <span style="background:#eef2ff; color:#3730a3; padding:0.15rem 0.55rem; border-radius:9999px; font-size:0.7rem;">' + eventType + '</span>',
      channel !== "—" ? '        <span style="background:#fef3c7; color:#92400e; padding:0.15rem 0.55rem; border-radius:9999px; font-size:0.7rem;">' + channel + '</span>' : '',
      tpl !== "—" ? '        <span style="background:#ecfdf5; color:#065f46; padding:0.15rem 0.55rem; border-radius:9999px; font-size:0.7rem;">tpl: ' + tpl + '</span>' : '',
      '      </div>',
      '      <h3 style="margin:0 0 0.25rem; font-size:1rem;">' + name + '</h3>',
      explanation ? '      <p style="margin:0; color:#4b5563; font-size:0.85rem; line-height:1.4;">' + explanation + '</p>' : '',
      '      <div style="margin-top:0.5rem; display:flex; gap:1rem; color:#6b7280; font-size:0.75rem; flex-wrap:wrap;">',
      '        <span>Son tetiklenme: <strong style="color:#111827;">' + esc(lastFired) + '</strong></span>',
      '        <span>Tetiklenme sayısı: <strong style="color:#111827;">' + fireCount + '</strong></span>',
      '        <span>Parse güveni: <strong style="color:#111827;">' + confidence + '</strong></span>',
      '      </div>',
      '    </div>',
      '    <div style="display:flex; flex-direction:column; gap:0.5rem; flex-shrink:0;">',
      '      <button type="button" data-act="toggle" data-id="' + esc(rule.id) + '" data-next="' + (nextEnabled ? "true" : "false") + '" style="padding:0.4rem 0.85rem; background:' + (enabled ? "#fff" : "#16a34a") + '; color:' + (enabled ? "#dc2626" : "#fff") + '; border:1px solid ' + (enabled ? "#fecaca" : "#16a34a") + '; border-radius:0.5rem; cursor:pointer; font-weight:600; font-size:0.85rem;">' + (enabled ? "Pasifleştir" : "Etkinleştir") + '</button>',
      '      <button type="button" data-act="delete" data-id="' + esc(rule.id) + '" data-name="' + name + '" style="padding:0.4rem 0.85rem; background:#fff; color:#6b7280; border:1px solid #e5e7eb; border-radius:0.5rem; cursor:pointer; font-size:0.85rem;">Sil</button>',
      '    </div>',
      '  </div>',
      '</article>',
    ].filter(Boolean).join("\n")
  }

  async function loadRules() {
    state.loading = true
    render()
    try {
      const data = await apiGet("/api/internal/structured-rules")
      state.rules = Array.isArray(data?.data) ? data.data : []
    } catch (e) {
      console.warn("[triggers] list failed:", e)
      root.innerHTML = '<p style="color:#dc2626; padding:1rem;">Kurallar yüklenemedi: ' + esc(e.message) + '</p>'
      state.loading = false
      return
    }
    state.loading = false
    render()
  }

  async function onToggle(id, nextEnabled) {
    try {
      await apiPatch("/api/internal/structured-rules/" + id + "/enabled?enabled=" + (nextEnabled ? "true" : "false"))
      await loadRules()
    } catch (e) {
      alert("Durum değiştirilemedi: " + e.message)
    }
  }

  async function onDelete(id, name) {
    if (!window.confirm("\"" + name + "\" kuralını silmek istiyor musun?")) return
    try {
      await apiDelete("/api/internal/structured-rules/" + id)
      await loadRules()
    } catch (e) {
      alert("Silinemedi: " + e.message)
    }
  }

  loadRules()
})()
