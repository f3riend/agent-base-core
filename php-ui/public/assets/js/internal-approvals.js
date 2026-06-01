// Kural Tabanlı Onaylar — /api/internal/approvals/types + /api/internal/approvals
// Dinamik sekme: server'dan dönen her approval_type için bir sekme.
// approvals-app.js (mevcut SocialDocument-tabanlı) ile çakışmaz —
// kendi DOM root'u (#internal-approvals-root).

(function () {
  "use strict"

  const root = document.getElementById("internal-approvals-root")
  if (!root) return

  const apiBase = (root.dataset.apiBase || "").replace(/\/+$/, "")
  const token = root.dataset.token || ""
  // user_id: PHP tarafından data-user-id ile geçirilir. Default 3 — orchestration_api
  // DEFAULT_USER_ID (db.py:11) ile aynı. Backend X-API-Key veya user_id query
  // alanından auth context kurar; auth katmanı yok.
  const userId = (root.dataset.userId || "3").trim() || "3"
  const tabsEl = root.querySelector("#ia-tabs")
  const listEl = root.querySelector("#ia-list")

  // Sayfa moduna göre approval_type sekme filtresi.
  // /campaign-management/* → banner_approval + campaign_approval + generic
  // /social-media/*       → post_approval + story_approval + generic
  const pageMode = String(window.location.pathname || "").includes("/campaign-management")
    ? "campaign"
    : "social"
  const SOCIAL_APPROVAL_TYPES = ["post_approval", "story_approval", "generic_approval"]
  const CAMPAIGN_APPROVAL_TYPES = ["banner_approval", "campaign_approval", "generic_approval"]
  const ALLOWED_TYPES = pageMode === "campaign" ? CAMPAIGN_APPROVAL_TYPES : SOCIAL_APPROVAL_TYPES

  let state = {
    types: [],
    activeType: null, // null = tümü
    items: [],
    loading: false,
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

  async function apiGet(path) {
    const url = apiBase + path + (path.includes("?") ? "&" : "?") + "user_id=" + encodeURIComponent(userId)
    const res = await fetch(url, { headers: authHeaders(false) })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  async function apiPost(path, body) {
    const url = apiBase + path + (path.includes("?") ? "&" : "?") + "user_id=" + encodeURIComponent(userId)
    const res = await fetch(url, {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify(body || {}),
    })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  function renderTabs() {
    if (!state.types.length) {
      tabsEl.innerHTML = '<span style="color:#9ca3af;">Henüz onay türü yok</span>'
      return
    }
    const all = '<button type="button" data-tab="" class="ia-tab' +
      (state.activeType ? "" : " is-active") +
      '" style="' + tabBtnStyle(!state.activeType) + '">' +
      'Tümü (' + state.types.reduce((acc, t) => acc + (t.pending || 0), 0) + ')</button>'
    const rest = state.types
      .map((t) => {
        const active = state.activeType === t.approval_type
        return '<button type="button" data-tab="' + esc(t.approval_type) + '" class="ia-tab' +
          (active ? " is-active" : "") +
          '" style="' + tabBtnStyle(active) + '">' +
          esc(t.label || t.approval_type) +
          ' (' + (t.pending || 0) + ')</button>'
      })
      .join("")
    tabsEl.innerHTML = all + rest
    tabsEl.querySelectorAll(".ia-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.activeType = btn.dataset.tab || null
        renderTabs()
        loadItems()
      })
    })
  }

  function tabBtnStyle(active) {
    return [
      "padding:0.4rem 0.85rem",
      "border-radius:9999px",
      "border:1px solid " + (active ? "#4338ca" : "#d1d5db"),
      "background:" + (active ? "#eef2ff" : "#fff"),
      "color:" + (active ? "#3730a3" : "#374151"),
      "cursor:pointer",
      "font-size:0.85rem",
    ].join(";")
  }

  function renderList() {
    if (state.loading) {
      listEl.innerHTML = '<p style="color:#9ca3af;">Yükleniyor…</p>'
      return
    }
    if (!state.items.length) {
      listEl.innerHTML = '<p style="color:#9ca3af; padding:1rem; text-align:center;">' +
        'Bu sekmede onay bekleyen kayıt yok.</p>'
      return
    }
    listEl.innerHTML = state.items
      .map((item) => renderCard(item))
      .join("")
    // Aksiyon butonlarına olay bağla
    listEl.querySelectorAll("[data-act='approve']").forEach((btn) => {
      btn.addEventListener("click", () => onApprove(parseInt(btn.dataset.id, 10)))
    })
    listEl.querySelectorAll("[data-act='reject']").forEach((btn) => {
      btn.addEventListener("click", () => onReject(parseInt(btn.dataset.id, 10)))
    })
  }

  function renderCard(item) {
    const p = item.proposal || {}
    const content = (p.task_payload || {}).content || {}
    const channel = content.channel || p.channel || "-"
    const headline = content.headline || p.reason || "İçerik"
    const body = content.body || ""
    const accounts = ((p.task_payload || {}).content || {}).accounts ||
      p.accounts || []
    return [
      '<article style="border:1px solid #e5e7eb; border-radius:0.75rem; background:#fff; padding:1rem;">',
      '  <div style="display:flex; justify-content:space-between; gap:1rem;">',
      '    <div>',
      '      <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.4rem;">',
      '        <span style="background:#eef2ff; color:#3730a3; padding:0.15rem 0.55rem; border-radius:9999px; font-size:0.7rem; font-weight:600; text-transform:uppercase;">' +
      esc(item.approval_type || "generic") + '</span>',
      '        <span style="color:#6b7280; font-size:0.75rem;">#' + esc(item.id) + ' · ' + esc(channel) + '</span>',
      '      </div>',
      '      <h3 style="margin:0 0 0.25rem; font-size:1rem;">' + esc(headline) + '</h3>',
      '      <p style="margin:0; color:#4b5563; font-size:0.875rem;">' + esc(body) + '</p>',
      (accounts && accounts.length
        ? '      <p style="margin:0.4rem 0 0; color:#6b7280; font-size:0.75rem;">Hesaplar: ' +
          esc(accounts.join(", ")) + '</p>'
        : ''),
      '      <p style="margin:0.4rem 0 0; color:#9ca3af; font-size:0.75rem;">' +
      esc(item.reason || "") + ' · ' + esc(item.created_at || "") + '</p>',
      '    </div>',
      '    <div style="display:flex; flex-direction:column; gap:0.5rem; flex-shrink:0;">',
      '      <button type="button" data-act="approve" data-id="' + esc(item.id) +
      '" style="padding:0.5rem 1rem; background:#16a34a; color:#fff; border:0; border-radius:0.5rem; cursor:pointer; font-weight:600;">Onayla</button>',
      '      <button type="button" data-act="reject" data-id="' + esc(item.id) +
      '" style="padding:0.5rem 1rem; background:#fff; color:#dc2626; border:1px solid #fecaca; border-radius:0.5rem; cursor:pointer;">Reddet</button>',
      '    </div>',
      '  </div>',
      '</article>',
    ].join("\n")
  }

  async function loadTypes() {
    try {
      const data = await apiGet("/api/internal/approvals/types")
      const raw = Array.isArray(data?.types) ? data.types : []
      // Sayfa moduna göre yalnızca ilgili approval_type'ları göster.
      state.types = raw.filter((t) => ALLOWED_TYPES.includes(t?.approval_type))
    } catch (e) {
      console.warn("[internal-approvals] types load failed:", e)
      state.types = []
    }
    renderTabs()
  }

  async function loadItems() {
    state.loading = true
    renderList()
    try {
      const qs = state.activeType ? "?approval_type=" + encodeURIComponent(state.activeType) : ""
      const data = await apiGet("/api/internal/approvals" + qs)
      const raw = Array.isArray(data?.data) ? data.data : []
      // "Tümü" sekmesinde de sayfa moduna ait approval_type'ları filtrele.
      state.items = raw.filter((it) => ALLOWED_TYPES.includes(it?.approval_type))
    } catch (e) {
      console.warn("[internal-approvals] list load failed:", e)
      state.items = []
    } finally {
      state.loading = false
    }
    renderList()
  }

  async function onApprove(id) {
    if (!confirm("Bu kuralı onaylıyor musun?")) return
    try {
      const res = await apiPost("/api/internal/approvals/" + id + "/approve", {})
      console.log("approve result:", res)
      // Listeyi tazele
      await loadTypes()
      await loadItems()
    } catch (e) {
      alert("Onay hatası: " + e.message)
    }
  }

  async function onReject(id) {
    const feedback = prompt("Ret sebebi (opsiyonel):", "")
    if (feedback === null) return
    try {
      const res = await apiPost("/api/internal/approvals/" + id + "/reject", { feedback: feedback || "" })
      console.log("reject result:", res)
      await loadTypes()
      await loadItems()
    } catch (e) {
      alert("Ret hatası: " + e.message)
    }
  }

  async function init() {
    await loadTypes()
    await loadItems()
  }

  init()
})()
