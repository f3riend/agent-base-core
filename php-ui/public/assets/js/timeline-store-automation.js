function authHeaders(withJson = false) {
  const h = {}
  if (withJson) h["Content-Type"] = "application/json"
  const tok = window.__AGENTBASE__?.accessToken || ""
  if (tok) h.Authorization = "Bearer " + tok
  return h
}

async function apiRequest(path, options = {}) {
  const base = (window.__AGENTBASE__?.apiBase || "").replace(/\/+$/, "")
  const res = await fetch(base + path, options)
  const txt = await res.text()
  let json = null
  try { json = txt ? JSON.parse(txt) : null } catch { json = null }
  if (!res.ok) throw new Error(String((json && (json.error || json.detail)) || txt || `HTTP ${res.status}`))
  return json
}

function esc(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;")
}

function callLucide() {
  try { window.lucide?.createIcons?.() } catch {}
}

async function socialList(collection) {
  // YENİ: products koleksiyonu için /api/internal/items'a yönlendir (auth yok).
  // Diğer koleksiyonlar (product_reviews, product_faq, vb.) için MySQL endpoint
  // dene, hata olursa boş döner (graceful).
  if (collection === "products") {
    try {
      const base = (window.__AGENTBASE__?.apiBase || "").replace(/\/+$/, "")
      const resp = await fetch(base + "/api/internal/items?user_id=1&limit=200")
      if (!resp.ok) throw new Error("HTTP " + resp.status)
      const data = await resp.json()
      const items = Array.isArray(data?.data) ? data.data : Array.isArray(data) ? data : []
      // Mevcut JS shape'ine map et: id string + alanlar
      return items.map((it) => ({
        id: String(it.id),
        name: it.name || "",
        category: it.category || "",
        price: it.price || 0,
        stock: it.stock || 0,
        status: it.status || "active",
        store_id: it.store_id,
        store_name: it.store_name || "",
      }))
    } catch (e) {
      console.warn("[timeline-store-automation] /api/internal/items fail:", e)
      return []
    }
  }
  // Diğer koleksiyonlar — eski yol (MySQL/JWT olmadan boş döner)
  try {
    return await apiRequest(`/social-data/collections/${encodeURIComponent(collection)}`, {
      headers: authHeaders(false),
    })
  } catch {
    return []
  }
}

async function socialCreate(collection, body) {
  return apiRequest(`/social-data/collections/${encodeURIComponent(collection)}`, {
    method: "POST",
    headers: authHeaders(true),
    body: JSON.stringify(body || {}),
  })
}

document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id)
  const els = {
    productList: $("tsws-products-grid"),
    search: $("tsws-search"),
    storeFilter: $("tsws-store-filter"),
    addProductBtn: $("tsop-add-product"),
    multiCount: $("tsop-multi-count"),
    selectedName: $("tsws-selected-product-name"),
    selectedKicker: $("tsws-selected-product-kicker"),
    selectedThumb: $("tsop-selected-thumb"),
    tabContent: $("tsws-tab-content"),
    insightList: $("tsws-insight-list"),
    pendingList: $("tsws-pending-list"),
    pendingCount: $("tsws-pending-count"),
    eventFeed: $("tsws-event-feed"),
    eventCount: $("tsws-event-count"),
    timelineFeed: $("tsws-operation-timeline"),
    timelineCount: $("tsws-timeline-count"),
    panelOverview: $("tsop-panel-overview"),
    panelInsights: $("tsop-panel-insights"),
    panelReviews: $("tsop-panel-reviews"),
    panelFaq: $("tsop-panel-faq"),
    panelTickets: $("tsop-panel-tickets"),
    panelOperations: $("tsop-panel-operations"),
    panelHistory: $("tsop-panel-history"),
    historyList: $("tsop-history-list"),
    rightTabs: Array.from(document.querySelectorAll(".tsop-right-tabs button")),
    chatTabs: Array.from(document.querySelectorAll(".tsop-chat-tabs button")),
    modeButtons: Array.from(document.querySelectorAll("[data-ai-mode]")),
    aiPresence: $("tsop-ai-presence"),
    aiPresenceText: $("tsop-ai-presence-text"),
    chatLog: $("tsws-chat-log"),
    chatScroll: document.querySelector(".tsws-v2-chat-log-wrapper"),
    chatInput: $("tsws-chat-input"),
    chatForm: $("tsws-chat-form"),
    quickActionButtons: Array.from(document.querySelectorAll("[data-chat-seed]")),
    bulkActionButtons: Array.from(document.querySelectorAll("[data-bulk-action]")),
    productModal: $("tsop-product-modal"),
    productForm: $("tsop-product-form"),
    productStock: $("tsop-product-stock"),
    productImages: $("tsop-product-images"),
    productDescription: $("tsop-product-description"),
    reviewsList: $("tsop-reviews-list"),
    faqList: $("tsop-faq-list"),
    ticketList: $("tsop-ticket-list"),
    reviewForm: $("tsop-review-form"),
    faqForm: $("tsop-faq-form"),
    ticketForm: $("tsop-ticket-form"),
    contextMenu: $("tsws-context-menu"),
  }

  if (!(els.productList instanceof HTMLElement) || !(els.chatForm instanceof HTMLFormElement) || !(els.chatInput instanceof HTMLTextAreaElement)) return

  const s = {
    stores: [],
    products: [],
    filtered: [],
    selectedProductId: "",
    selectedIds: new Set(),
    selectedDetail: {},
    tab: "overview",
    chatTab: "chat",
    conversationId: "",
    chatHistory: [],
    liveToolStates: [],
    runtimeInsights: [],
    events: [],
    timeline: [],
    pendingActions: [],
    context: { selectedStore: "", selectedProduct: "", selectedOrder: "" },
    lastOperationId: "",
    streamNodes: {},
    streamBuffers: {},
    aiMode: "analiz",
    thinkingNode: null,
    lastOperationStatus: "",
    streamState: {},
    presenceTimer: null,
    operationBubble: null,
    lastOperationBubbleAt: 0,
    activeContextQuery: "",
    flowMode: "analytics",
  }
  const SHOW_OPERATION_NOISE = false

  const now = () => new Date().toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
  const badgeLabel = (code) => ({ ai_insight: "AI", risk: "Risk", trending: "Trend", sales_drop: "Satis Dususu" }[code] || code)
  const statusLabel = (status) => ({
    pending: "Bekleniyor",
    queued: "Sirada",
    running: "Isleniyor",
    completed: "Tamamlandi",
    failed: "Basarisiz",
    cancelled: "Iptal",
  }[String(status || "").toLowerCase()] || "Isleniyor")

  const trendClass = (v) => (Number(v || 0) >= 0 ? "is-up" : "is-down")
  const thumb = (p) => `<div class="tsop-thumb-circle">${esc(String(p?.name || "?").slice(0, 1).toUpperCase())}</div>`

  function renderStoreOptions() {
    if (!(els.storeFilter instanceof HTMLSelectElement)) return
    const categories = Array.from(new Set(s.products.map((x) => String(x.category || "").trim()).filter(Boolean))).sort()
    els.storeFilter.innerHTML = '<option value="">Tum Kategoriler</option>' + categories.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("")
  }

  function applyFilters() {
    const q = (els.search instanceof HTMLInputElement ? els.search.value : "").trim().toLowerCase()
    const category = (els.storeFilter instanceof HTMLSelectElement ? els.storeFilter.value : "").trim()
    s.filtered = s.products.filter((p) => {
      if (category && String(p.category || "") !== category) return false
      if (q && !String(p.name || "").toLowerCase().includes(q)) return false
      return true
    })
    renderProductList()
  }

  function renderProductList() {
    if (!s.filtered.length) {
      els.productList.innerHTML = '<div class="tsws-empty">Urun bulunamadi.</div>'
      return
    }
    els.productList.innerHTML = s.filtered
      .map((p) => `
        <article class="tsop-product-item${p.id === s.selectedProductId ? " is-active" : ""}" data-product-id="${esc(p.id)}">
          <label class="tsop-check-wrap"><input type="checkbox" data-multi-id="${esc(p.id)}" ${s.selectedIds.has(p.id) ? "checked" : ""}></label>
          <div class="tsop-product-avatar">${thumb(p)}</div>
          <div class="tsop-product-copy">
            <h4>${esc(p.name)}</h4>
            <p>${esc(p.category)} • ${Number(p.price || 0).toFixed(0)} TL</p>
            <div class="tsop-badges">
              ${(Array.isArray(p.aiBadges) ? p.aiBadges : []).map((b) => `<span>${esc(badgeLabel(b))}</span>`).join("")}
            </div>
          </div>
          <div class="tsop-product-trend ${trendClass(p.trendPct)}">${Number(p.trendPct || 0).toFixed(1)}%</div>
        </article>
      `)
      .join("")
    if (els.multiCount instanceof HTMLElement) els.multiCount.textContent = `${s.selectedIds.size} secili`
  }

  function renderFeed(container, rows, emptyText = "Bos") {
    if (!(container instanceof HTMLElement)) return
    if (!rows.length) {
      container.innerHTML = `<div class="tsws-empty">${esc(emptyText)}</div>`
      return
    }
    container.innerHTML = rows
      .map((x) => `
        <article class="tsop-feed-item ${x.state ? `is-${esc(x.state)}` : ""}">
          <div class="tsop-feed-dot"></div>
          <div class="tsop-feed-copy">
            <strong>${esc(x.text || "")}</strong>
            <small>${esc(x.at || "")}</small>
          </div>
        </article>
      `)
      .join("")
  }

  function setRightPanel(activeTab) {
    if (!els.rightTabs.length) return
    const map = {
      overview: els.panelOverview,
      insights: els.panelInsights,
      reviews: els.panelReviews,
      faq: els.panelFaq,
      tickets: els.panelTickets,
      operations: els.panelOperations,
      history: els.panelHistory,
    }
    Object.keys(map).forEach((key) => {
      const el = map[key]
      if (!(el instanceof HTMLElement)) return
      el.classList.toggle("tsop-pane-hidden", key !== activeTab)
      el.classList.toggle("tsop-pane-active", key === activeTab)
    })
  }

  function toKeyword(text) {
    const raw = String(text || "").toLocaleLowerCase("tr-TR")
    const candidates = [
      "bluetooth",
      "kargo",
      "teslimat",
      "iade",
      "pil",
      "sarj",
      "baglanti",
      "gecikme",
      "ses",
      "fiyat",
      "stok",
    ]
    for (const key of candidates) {
      if (raw.includes(key)) return key
    }
    return ""
  }

  function isExplicitOperationMessage(text) {
    const t = String(text || "").toLocaleLowerCase("tr-TR")
    const keys = [
      "kampanya",
      "post olustur",
      "reels",
      "hikaye",
      "banner",
      "takvime ekle",
      "icerik olustur",
      "yayinla",
      "schedule",
    ]
    return keys.some((k) => t.includes(k))
  }

  function isAnalyticsMessage(text) {
    const t = String(text || "").toLocaleLowerCase("tr-TR")
    const keys = [
      "neden dustu",
      "analiz et",
      "problem ne",
      "ne oluyor",
      "neden boyle",
      "bunu nasil duzeltiriz",
      "kisa ozet",
      "yorumlari incele",
    ]
    return keys.some((k) => t.includes(k))
  }

  function resolveFlowModeFromPayload(payload) {
    const domain = String(payload?.domain || "").toLowerCase()
    const intent = String(payload?.intent || "").toLowerCase()
    const message = String(payload?.message || "")
    if (domain === "content_ops" || domain === "publishing" || domain === "scheduling") return "operation"
    if (intent.includes("campaign") || intent.includes("banner") || intent.includes("caption")) return "operation"
    if (isExplicitOperationMessage(message)) return "operation"
    return "analytics"
  }

  function shouldRenderOperationArtifacts(payload = null) {
    if (payload) return resolveFlowModeFromPayload(payload) === "operation"
    return s.flowMode === "operation"
  }

  function resolvePreviewUrl(url, seed = "asset") {
    const raw = String(url || "").trim()
    if (/^https?:\/\//i.test(raw)) return raw
    const safeSeed = String(seed || "asset").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 24) || "asset"
    return `https://picsum.photos/seed/${safeSeed}_preview/960/720`
  }

  function renderRightSide() {
    const p = s.products.find((x) => x.id === s.selectedProductId)
    if (!p) {
      if (els.selectedName instanceof HTMLElement) els.selectedName.textContent = "Urun sec"
      if (els.selectedKicker instanceof HTMLElement) els.selectedKicker.textContent = "-"
      if (els.selectedThumb instanceof HTMLElement) els.selectedThumb.innerHTML = ""
      if (els.tabContent instanceof HTMLElement) els.tabContent.innerHTML = '<div class="tsws-empty">Urun sec.</div>'
      setRightPanel(s.tab)
      return
    }
    if (els.selectedName instanceof HTMLElement) els.selectedName.textContent = p.name
    if (els.selectedKicker instanceof HTMLElement) els.selectedKicker.textContent = `${p.category} • ${Number(p.price || 0).toFixed(0)} TL`
    if (els.selectedThumb instanceof HTMLElement) els.selectedThumb.innerHTML = thumb(p)
    const detail = s.selectedDetail || {}
    const overview = detail.overview || {}
    const contextKey = String(s.activeContextQuery || "").trim().toLocaleLowerCase("tr-TR")
    const reviewRows = (detail.reviews || [])
      .filter((x) => {
        if (!contextKey) return true
        const txt = `${String(x.comment || "")} ${String(x.author || "")}`.toLocaleLowerCase("tr-TR")
        return txt.includes(contextKey)
      })
      .slice(0, 6)
    const ticketRows = (detail.tickets || [])
      .filter((x) => {
        if (!contextKey) return String(x.status || "open").toLowerCase() !== "resolved"
        const txt = `${String(x.title || "")} ${String(x.issueType || "")} ${String(x.detail || "")}`.toLocaleLowerCase("tr-TR")
        return txt.includes(contextKey)
      })
      .slice(0, 6)
    if (els.selectedKicker instanceof HTMLElement) {
      const baseKicker = `${p.category} • ${Number(p.price || 0).toFixed(0)} TL`
      els.selectedKicker.textContent = contextKey ? `${baseKicker} • baglam: ${contextKey}` : baseKicker
    }
    els.tabContent.innerHTML = `
      <div class="tsop-stats-grid">
        <article><p>7 Gun Satis</p><strong>${Number(overview.sales || 0)}</strong></article>
        <article><p>7 Gun Ciro</p><strong>${Number(overview.revenue || 0).toFixed(0)} TL</strong></article>
        <article><p>Ortalama Puan</p><strong>${Number(overview.rating || 0).toFixed(1)}</strong></article>
        <article><p>Iade Orani</p><strong>${Number(overview.returnRate || 0).toFixed(1)}%</strong></article>
      </div>
      <div class="tsop-inline-questions">
        ${reviewRows.slice(0, 2).map((x) => `<div class="tsop-row"><span>${esc(x.comment)}</span><strong>${esc(x.author)}</strong></div>`).join("") || '<div class="tsws-empty">Musteri sorusu henuz yok.</div>'}
      </div>
    `
    renderFeed(els.insightList, s.runtimeInsights.slice(0, 8), "AI icgorusu henuz yok.")
    renderFeed(els.pendingList, s.pendingActions.slice(0, 8), "Bekleyen adim yok.")
    renderFeed(els.eventFeed, s.events.slice(0, 10), "Canli akis henuz yok.")
    renderFeed(els.timelineFeed, s.timeline.slice(0, 12), "Operasyon akisi bos.")
    renderFeed(els.historyList, (detail.history || []).map((x) => ({ text: x.event || "-", at: x.at || "", state: "completed" })), "Gecmis kaydi yok.")
    renderFeed(
      els.reviewsList,
      reviewRows.map((x) => ({
        text: `${String(x.comment || "-")} (${Number(x.rating || 0).toFixed(1)})`,
        at: String(x.author || ""),
        state: "completed",
      })),
      "Henuz yorum yok."
    )
    renderFeed(
      els.faqList,
      (detail.faq || []).map((x) => ({
        text: `S: ${String(x.question || "-")} • C: ${String(x.answer || "-")}`,
        at: String(x.updatedAt || x.createdAt || ""),
        state: "completed",
      })),
      "Henuz SSS yok."
    )
    renderFeed(
      els.ticketList,
      ticketRows.map((x) => ({
        text: `${String(x.title || x.issueType || "Destek kaydi")} - ${String(x.detail || "")}`,
        at: String(x.status || "open"),
        state: "pending",
      })),
      "Henuz destek kaydi yok."
    )
    if (els.pendingCount instanceof HTMLElement) els.pendingCount.textContent = String(s.pendingActions.length)
    if (els.eventCount instanceof HTMLElement) els.eventCount.textContent = String(s.events.length)
    if (els.timelineCount instanceof HTMLElement) els.timelineCount.textContent = String(s.timeline.length)
    setRightPanel(s.tab)
  }

  function appendChat(kind, text, meta = "", persist = true, variant = "") {
    const row = document.createElement("div")
    row.className = `tsop-chat-msg tsop-chat-${kind}${variant ? ` ${variant}` : ""}`
    row.innerHTML = `<p>${esc(text)}</p>${meta ? `<small>${esc(meta)}</small>` : ""}`
    els.chatLog.appendChild(row)
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
    if (persist) {
      s.chatHistory.push({ role: kind === "user" ? "user" : "assistant", content: String(text || "") })
      s.chatHistory = s.chatHistory.slice(-50)
    }
  }

  function sanitizeQuickReplies(rows) {
    const banned = new Set(["detaylandir", "simdilik takipte kal", "hemen ilerleyelim", "bunu ac"])
    const out = []
    for (const row of Array.isArray(rows) ? rows : []) {
      const txt = String(row || "").trim()
      if (!txt) continue
      const key = txt.toLocaleLowerCase("tr-TR")
      if (banned.has(key)) continue
      out.push(txt)
      if (out.length >= 2) break
    }
    return out
  }

  function upsertCompactOperationBubble({ title = "Operasyon", summary = "", details = [], imageUrl = "" } = {}) {
    const nowMs = Date.now()
    if (s.operationBubble instanceof HTMLElement && nowMs - s.lastOperationBubbleAt > 120000) {
      s.operationBubble = null
    }
    if (!(s.operationBubble instanceof HTMLElement)) {
      const wrap = document.createElement("article")
      wrap.className = "tsop-card-message tsop-operation-compact"
      wrap.innerHTML = `
        <details>
          <summary><strong>${esc(title)}</strong><span class="tsop-operation-compact-kicker">Operasyon detayi</span></summary>
          <div class="tsop-operation-compact-body"></div>
        </details>
      `
      els.chatLog.appendChild(wrap)
      s.operationBubble = wrap
    }
    s.lastOperationBubbleAt = nowMs
    const detailsRoot = s.operationBubble.querySelector(".tsop-operation-compact-body")
    if (!(detailsRoot instanceof HTMLElement)) return
    const block = document.createElement("div")
    block.className = "tsop-operation-compact-item"
    block.innerHTML = `
      ${summary ? `<p>${esc(summary)}</p>` : ""}
      ${details.length ? `<ul>${details.map((x) => `<li>${esc(String(x || ""))}</li>`).join("")}</ul>` : ""}
      ${imageUrl ? `<img src="${esc(resolvePreviewUrl(imageUrl, String(title || "operation")))}" alt="operation preview">` : ""}
    `
    detailsRoot.prepend(block)
    while (detailsRoot.children.length > 4) detailsRoot.lastElementChild?.remove()
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
  }

  function setPresence(state, text = "", ttlMs = 0) {
    const normalized = String(state || "idle")
    if (els.aiPresence instanceof HTMLElement) {
      els.aiPresence.dataset.presence = normalized
    }
    if (els.aiPresenceText instanceof HTMLElement && text) {
      els.aiPresenceText.textContent = text
    }
    if (s.presenceTimer) {
      clearTimeout(s.presenceTimer)
      s.presenceTimer = null
    }
    if (ttlMs > 0) {
      s.presenceTimer = setTimeout(() => {
        if (els.aiPresence instanceof HTMLElement) els.aiPresence.dataset.presence = "idle"
        if (els.aiPresenceText instanceof HTMLElement) els.aiPresenceText.textContent = "AI hazir"
      }, ttlMs)
    }
  }

  function upsertThinkingBubble(text) {
    const msg = String(text || "").trim()
    if (!msg) return
    if (!(s.thinkingNode instanceof HTMLElement)) {
      const row = document.createElement("div")
      row.className = "tsop-chat-msg tsop-chat-assistant is-thinking tsop-chat-thinking-live"
      row.innerHTML = `<p>${esc(msg)}</p><small>Dusunuyor...</small>`
      els.chatLog.appendChild(row)
      s.thinkingNode = row
    } else {
      const p = s.thinkingNode.querySelector("p")
      if (p instanceof HTMLElement) p.textContent = msg
    }
    setPresence("thinking", "Sessiz analiz suruyor...")
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
  }

  function clearThinkingBubble() {
    if (!(s.thinkingNode instanceof HTMLElement)) return
    s.thinkingNode.remove()
    s.thinkingNode = null
    setPresence("watching", "Baglam izleniyor", 2200)
  }

  function renderAssistantMessage(payload) {
    const message = String(payload?.message || "").trim()
    if (message) appendChat("assistant", message, now(), true, `tsop-chat-type-${String(payload?.type || "analysis")}`)
    const sections = Array.isArray(payload?.sections) ? payload.sections : []
    const actions = (Array.isArray(payload?.suggested_actions) ? payload.suggested_actions : []).slice(0, 2)
    const quickActions = (Array.isArray(payload?.quick_actions) ? payload.quick_actions : []).slice(0, 2)
    const quickReplies = sanitizeQuickReplies(payload?.quick_replies || [])
    if (!sections.length && !actions.length) return
    upsertCompactOperationBubble({
      title: "Asistan notu",
      summary: sections[0]?.content || "",
      details: [
        ...sections.flatMap((section) => Array.isArray(section?.items) ? section.items.slice(0, 1) : []),
        ...actions,
        ...quickReplies,
        ...quickActions.map((x) => String(x?.label || x?.action || "")),
      ].filter(Boolean).slice(0, 4),
    })
  }

  function getStreamNode(streamId) {
    const key = String(streamId || "").trim()
    if (!key) return null
    if (s.streamNodes[key] instanceof HTMLElement) return s.streamNodes[key]
    const weight = String((s.streamState[key] || {}).weight || "normal")
    const row = document.createElement("div")
    row.className = `tsop-chat-msg tsop-chat-assistant is-thinking tsop-chat-weight-${weight}`
    row.setAttribute("data-stream-id", key)
    row.innerHTML = "<p></p><small>Yaziyor...</small>"
    els.chatLog.appendChild(row)
    s.streamNodes[key] = row
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
    return row
  }

  function ensureStreamBuffer(streamId) {
    const key = String(streamId || "").trim()
    if (!key) return null
    if (!s.streamBuffers[key]) s.streamBuffers[key] = { queue: [], timer: null, pending: "" }
    return s.streamBuffers[key]
  }

  function typingDelayFor(chunk, weight = "normal") {
    const txt = String(chunk || "")
    if (!txt) return 45
    const last = txt.slice(-1)
    const mode = String(weight || "normal")
    const speed = mode === "critical" ? 0.92 : mode === "important" ? 1.0 : mode === "light" ? 0.86 : 0.95
    const sentencePause = mode === "critical" ? 260 : mode === "important" ? 230 : mode === "light" ? 150 : 200
    const paragraphPause = mode === "critical" ? 340 : mode === "important" ? 300 : mode === "light" ? 230 : 280
    if (/\n\n$/.test(txt)) return 300
    if (/\n$/.test(txt)) return 180
    if (/\.\.\.$/.test(txt)) return Math.round(230 * speed)
    if (/[.!?]/.test(last)) return sentencePause
    if (/[,:;]/.test(last)) return Math.round(140 * speed)
    if (/\n/.test(last)) return paragraphPause
    return Math.round((45 + Math.min(80, txt.length * 2)) * speed)
  }

  function splitForCadence(delta) {
    const text = String(delta || "")
    if (!text) return []
    const parts = text.match(/([^\s.,!?;:\n]+[\s]?|[.,!?;:]+[\s]?|\n+)/g) || []
    return parts
      .map((x) => x || "")
      .filter(Boolean)
  }

  function transitionHintDelay(nextChunk) {
    const txt = String(nextChunk || "").toLowerCase()
    if (txt.includes("ote yandan") || txt.includes("derine indik")) return 190
    if (txt.includes("ilk sinyaller") || txt.includes("dikkat ceken")) return 170
    return 0
  }

  function flushStreamQueue(streamId) {
    const key = String(streamId || "").trim()
    const state = ensureStreamBuffer(key)
    if (!state) return
    if (!state.queue.length) {
      state.timer = null
      return
    }
    const next = state.queue.shift()
    const row = getStreamNode(key)
    if (row instanceof HTMLElement) {
      const p = row.querySelector("p")
      if (p instanceof HTMLElement) p.textContent = `${p.textContent || ""}${String(next || "")}`
    }
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
    const weight = String((s.streamState[key] || {}).weight || "normal")
    state.timer = setTimeout(() => flushStreamQueue(key), typingDelayFor(next, weight) + transitionHintDelay(next))
  }

  function appendStreamChunk(streamId, delta) {
    const key = String(streamId || "").trim()
    const state = ensureStreamBuffer(key)
    if (!state) return
    const pieces = splitForCadence(delta)
    if (!pieces.length) return
    pieces.forEach((piece) => state.queue.push(piece))
    if (!state.timer) state.timer = setTimeout(() => flushStreamQueue(key), 20)
  }

  function drainStreamQueue(streamId) {
    const key = String(streamId || "").trim()
    const state = ensureStreamBuffer(key)
    if (!state) return
    if (state.timer) {
      clearTimeout(state.timer)
      state.timer = null
    }
    const rest = state.queue.splice(0, state.queue.length).join("")
    if (!rest) return
    const row = getStreamNode(key)
    if (!(row instanceof HTMLElement)) return
    const p = row.querySelector("p")
    if (p instanceof HTMLElement) p.textContent = `${p.textContent || ""}${rest}`
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
  }

  function completeStreamMessage(payload) {
    const streamId = String(payload?.stream_id || "").trim()
    if (streamId) drainStreamQueue(streamId)
    const row = streamId ? s.streamNodes[streamId] : null
    if (row instanceof HTMLElement) {
      row.classList.remove("is-thinking")
      row.classList.add(`tsop-chat-type-${String(payload?.type || "analysis")}`)
      row.classList.add(`tsop-chat-tone-${String(payload?.tone || "analysis")}`)
      row.classList.add(`tsop-chat-weight-${String(payload?.weight || "normal")}`)
      const small = row.querySelector("small")
      if (small instanceof HTMLElement) {
        const confidence = Number(payload?.confidence || 0)
        small.textContent = confidence > 0 ? `${now()} • guven ${(confidence * 100).toFixed(0)}%` : now()
      }
      const p = row.querySelector("p")
      const finalMessage = String(payload?.message || "").trim()
      if (p instanceof HTMLElement && finalMessage) p.textContent = finalMessage
      delete s.streamNodes[streamId]
      delete s.streamBuffers[streamId]
      delete s.streamState[streamId]
    } else if (String(payload?.message || "").trim()) {
      appendChat(
        "assistant",
        String(payload.message),
        now(),
        true,
        `tsop-chat-type-${String(payload?.type || "analysis")} tsop-chat-weight-${String(payload?.weight || "normal")}`
      )
    }
    const tone = String(payload?.tone || "").toLowerCase()
    if (tone === "warning" || tone === "alert") setPresence("alert", "Onemli sinyal algilandi", 3200)
    else setPresence("active", "AI aktif olarak yanitliyor", 2600)
    const hasSections = Array.isArray(payload?.sections) && payload.sections.length
    const hasActions = Array.isArray(payload?.suggested_actions) && payload.suggested_actions.length
    if ((hasSections || hasActions) && shouldRenderOperationArtifacts(payload)) {
      const compactActions = (Array.isArray(payload?.quick_actions) ? payload.quick_actions : []).slice(0, 2)
      const compactReplies = sanitizeQuickReplies(payload?.quick_replies || [])
      upsertCompactOperationBubble({
        title: String(payload?.intent || "").includes("campaign") ? "Kampanya operasyonu" : "Operasyon notu",
        summary: String(payload?.analysis_summary || payload?.message || "").split(".")[0] || "",
        details: [
          ...(payload.sections || []).flatMap((section) => Array.isArray(section?.items) ? section.items.slice(0, 1) : []),
          ...((payload.suggested_actions || []).slice(0, 2)),
          ...compactReplies,
          ...compactActions.map((x) => String(x?.label || x?.action || "")),
        ].filter(Boolean).slice(0, 4),
      })
    }
  }

  function appendToolStateCard() {
    const existing = els.chatLog.querySelector(".tsop-tool-timeline")
    if (existing) existing.remove()
    if (!SHOW_OPERATION_NOISE) return
    if (!s.liveToolStates.length) return
    const completed = s.liveToolStates.filter((x) => String(x.status || "") === "completed").length
    const running = s.liveToolStates.length - completed
    if (running <= 0 && completed > 0) return
    const title = running > 0 ? `${running} adim isleniyor` : `${completed}/${s.liveToolStates.length} adim tamamlandi`
    const card = document.createElement("article")
    card.className = "tsop-tool-timeline"
    card.innerHTML = `
      <details>
        <summary>${esc(title)}</summary>
        <ul>
          ${s.liveToolStates.filter((x) => String(x.status || "") !== "completed").map((x) => `
            <li class="tsop-tool-step is-${esc(x.status)}">
              <span class="tsop-tool-dot"></span>
              <span class="tsop-tool-label">${esc(x.message || x.description || "Operasyon adimi tamamlandi")}</span>
              <small>${esc(statusLabel(x.status))}</small>
            </li>
          `).join("")}
        </ul>
      </details>
    `
    els.chatLog.appendChild(card)
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
  }

  function upsertTool(tool, status) {
    const idx = s.liveToolStates.findIndex((x) => x.tool === tool)
    if (idx >= 0) {
      s.liveToolStates[idx].status = status
      s.liveToolStates[idx].message = s.liveToolStates[idx].message || ""
      s.liveToolStates[idx].description = s.liveToolStates[idx].description || ""
    } else s.liveToolStates.push({ tool, status, message: "", description: "" })
    s.liveToolStates = s.liveToolStates
      .slice(-10)
      .sort((a, b) => (a.status === "completed" ? 1 : 0) - (b.status === "completed" ? 1 : 0))
    appendToolStateCard()
  }

  function splitSse(chunk) {
    return chunk.split("\n\n").map((raw) => raw.trim()).filter(Boolean).map((raw) => {
      const lines = raw.split("\n")
      let eventName = "message"
      const data = []
      lines.forEach((line) => {
        if (line.startsWith("event:")) eventName = line.slice(6).trim()
        if (line.startsWith("data:")) data.push(line.slice(5).trim())
      })
      let parsed = {}
      try { parsed = JSON.parse(data.join("\n")) } catch { parsed = { raw: data.join("\n") } }
      return { eventName, data: parsed }
    })
  }

  function onStreamEvent(eventName, data) {
    if (eventName === "thinking") {
      const msg = String(data.message || "").trim()
      if (msg) upsertThinkingBubble(msg)
      renderRightSide()
      return
    }
    if (eventName === "tool_state") {
      const tool = String(data.tool || "")
      const status = String(data.status || "pending").toLowerCase()
      upsertTool(tool, status)
      const idx = s.liveToolStates.findIndex((x) => x.tool === tool)
      if (idx >= 0) {
        s.liveToolStates[idx].message = String(data.description || data.message || "")
        s.liveToolStates[idx].description = String(data.description || "")
      }
      appendToolStateCard()
      if (status === "completed" && SHOW_OPERATION_NOISE) {
        s.timeline.unshift({
          kind: "Islem",
          text: String(data.description || data.message || "Operasyon adimi tamamlandi"),
          at: now(),
          state: "completed",
        })
      }
      renderRightSide()
      return
    }
    if (eventName === "assistant_message_chunk") {
      clearThinkingBubble()
      const streamId = String(data?.stream_id || "").trim()
      if (streamId) {
        s.streamState[streamId] = {
          ...(s.streamState[streamId] || {}),
          weight: String(data?.weight || "normal"),
        }
      }
      appendStreamChunk(data?.stream_id, data?.delta)
      return
    }
    if (eventName === "assistant_message_complete") {
      clearThinkingBubble()
      s.flowMode = resolveFlowModeFromPayload(data || {})
      completeStreamMessage(data || {})
      const txt = String(data?.message || "").trim()
      if (txt) s.activeContextQuery = toKeyword(txt)
      if (txt) {
        const sev = String(data?.severity || "").toLowerCase()
        const state = sev === "critical" || sev === "warning" ? "pending" : "completed"
        if (SHOW_OPERATION_NOISE) s.timeline.unshift({ kind: "AI", text: txt, at: now(), state })
        s.runtimeInsights.unshift({ kind: "AI Icgorusu", text: txt, at: now(), state: "completed" })
      }
      if (Array.isArray(data?.suggested_actions)) {
        s.runtimeInsights.unshift({ kind: "Oneri", text: `${data.suggested_actions.length} aksiyon onerildi`, at: now(), state: "completed" })
      }
      renderRightSide()
      return
    }
    if (eventName === "assistant_message") {
      s.flowMode = resolveFlowModeFromPayload(data || {})
      s.activeContextQuery = toKeyword(String(data?.message || ""))
      renderAssistantMessage(data || {})
      renderRightSide()
      return
    }
    if (eventName === "card") {
      if (!shouldRenderOperationArtifacts()) return
      upsertCompactOperationBubble({
        title: String(data.title || "Operasyon karti"),
        summary: String(data.description || ""),
        imageUrl: String(data.preview_image || ""),
      })
      s.runtimeInsights.unshift({ kind: "AI Icgorusu", text: String(data.title || data.description || "-"), at: now(), state: "completed" })
      renderRightSide()
      return
    }
    if (eventName === "event") {
      const evtType = String(data.type || "").toLowerCase()
      const evtText = String(data.description || data.type || "event")
      if (SHOW_OPERATION_NOISE) s.events.unshift({ kind: "Operasyon", text: evtText, at: now(), state: "running" })
      if (evtType === "scheduled_post_created" || evtType === "scheduled_post_updated") {
        try {
          window.dispatchEvent(new CustomEvent("sm-scheduled-post-created", { detail: data || {} }))
        } catch {}
      }
      if ((data.type || "").includes("review") || (data.description || "").toLowerCase().includes("sikayet")) {
        s.runtimeInsights.unshift({ kind: "Uyari", text: String(data.description || data.type || "-"), at: now(), state: "running" })
      }
      if ((evtType === "scheduled_post_created" || evtType === "scheduled_post_updated") && shouldRenderOperationArtifacts()) {
        upsertCompactOperationBubble({ title: "Takvim durumu", summary: evtText })
      }
      renderRightSide()
      return
    }
    if (eventName === "pending_action") {
      if (!shouldRenderOperationArtifacts()) return
      s.pendingActions.unshift({ kind: "Bekleyen", text: String(data.title || data.id || "pending_action"), at: now(), state: "pending" })
      upsertCompactOperationBubble({ title: "Onay gerekli", summary: String(data.title || "Takvime eklememi ister misin?") })
      renderRightSide()
      return
    }
    if (eventName === "generated_asset") {
      // Visual preview is always rendered to keep draft realism.
      const msg = String(data.message || data.preview || data.image_url || "Gorsel varlik olusturuldu")
      s.flowMode = "operation"
      if (SHOW_OPERATION_NOISE) s.timeline.unshift({ kind: "Uretim", text: msg, at: now(), state: "completed" })
      s.runtimeInsights.unshift({ kind: "Varlik", text: msg, at: now(), state: "completed" })
      upsertCompactOperationBubble({
        title: "Varlik onizleme",
        summary: msg,
        imageUrl: resolvePreviewUrl(String(data.image_url || data.preview || ""), String(s.selectedProductId || "asset")),
      })
      renderRightSide()
      return
    }
    if (eventName === "message") {
      appendChat("assistant", String(data.content || ""), now())
      return
    }
    if (eventName === "operation") {
      const id = String(data.operation_id || data.task_id || "")
      if (id) s.lastOperationId = id
      const status = String(data.status || "").toLowerCase()
      if (SHOW_OPERATION_NOISE && status && status !== s.lastOperationStatus && status !== "running") {
        s.lastOperationStatus = status
        s.timeline.unshift({
          kind: "Operasyon",
          text: `Operasyon ${statusLabel(status)}`,
          at: now(),
          state: status === "completed" ? "completed" : "running",
        })
      }
      renderRightSide()
      return
    }
    if (eventName === "replay_start" || eventName === "replay_complete") {
      if (SHOW_OPERATION_NOISE) s.timeline.unshift({ kind: "Gecmis", text: eventName === "replay_start" ? "Gecmis operasyon akisi yukleniyor..." : "Gecmis operasyon akisi hazir", at: now(), state: "completed" })
      renderRightSide()
      return
    }
    if (eventName === "replay_event") {
      onStreamEvent(String(data.event || "event"), data.data || {})
      return
    }
    if (eventName === "done") {
      if (data.conversation_id) s.conversationId = String(data.conversation_id)
      clearThinkingBubble()
      if (SHOW_OPERATION_NOISE) s.timeline.unshift({ kind: "Tamamlandi", text: "Operasyon tamamlandi", at: now(), state: "completed" })
      renderRightSide()
    }
  }

  async function streamRequest(url, options) {
    const res = await fetch(url, options)
    if (!res.ok || !res.body) throw new Error(await res.text())
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split("\n\n")
      buffer = chunks.pop() || ""
      chunks.forEach((chunk) => splitSse(chunk + "\n\n").forEach((evt) => onStreamEvent(evt.eventName, evt.data)))
    }
  }

  async function runPipeline(message, source = "chat") {
    s.flowMode = isExplicitOperationMessage(message) ? "operation" : "analytics"
    if (isAnalyticsMessage(message)) s.flowMode = "analytics"
    const selected = Array.from(s.selectedIds)
    const req = {
      message,
      context: {
        product_id: s.selectedProductId || selected[0] || "",
        store_id: s.context.selectedStore || "",
        order_id: s.context.selectedOrder || "",
        mode: s.aiMode || "analiz",
        product_ids: selected,
      },
      history: s.chatHistory.slice(-20).map((x) => ({ role: x.role, content: x.content })),
      conversation_id: s.conversationId || null,
    }
    appendChat("user", message, now())
    setPresence("thinking", "Analiz suruyor...")
    s.liveToolStates = []
    appendToolStateCard()
    const base = (window.__AGENTBASE__?.apiBase || "").replace(/\/+$/, "")
    try {
      // YENİ: /api/internal/chat (JWT istemiyor, JSON response)
      const resp = await fetch(base + "/api/internal/chat?user_id=1", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: message,
          session_id: s.conversationId || null,
        }),
      })
      if (!resp.ok) throw new Error("HTTP " + resp.status)
      const data = await resp.json()
      if (data?.session_id && !s.conversationId) {
        s.conversationId = data.session_id
      }
      const answer = data?.answer || data?.response || "Cevap alinamadi."
      appendChat("assistant", answer, now())
      // recommendations / data alanları varsa kullanıcı için kısaca özet
      if (Array.isArray(data?.recommendations) && data.recommendations.length) {
        const recs = data.recommendations
          .slice(0, 3)
          .map((r) => "• " + (r.suggestion || r.intent))
          .join("\n")
        appendChat("assistant", "Öneriler:\n" + recs, now())
      }
      setPresence("idle", "AI hazir", 1500)
    } catch (err) {
      setPresence("alert", "AI baglantisinda gecici sorun var", 3500)
      appendChat("assistant", err instanceof Error ? err.message : "Pipeline hatasi", now())
    }
  }

  async function reconnectOperation(opId) {
    // YENİ: SSE stream artık kullanılmıyor — operation reconnect no-op.
    // /api/internal/chat zaten JSON cevap döndürdüğü için stream şart değil.
    const id = String(opId || s.lastOperationId || "").trim()
    if (!id) return
    setPresence("idle", "AI hazir", 800)
  }

  async function loadDetail(productId) {
    setPresence("watching", "Yeni urun baglami inceleniyor...")
    const [products, reviews, faq, tickets, metrics, assets, historyRes] = await Promise.all([
      socialList("products"),
      socialList("product_reviews"),
      socialList("product_faq"),
      socialList("product_support_tickets"),
      socialList("product_metrics_daily"),
      socialList("product_assets"),
      // YENİ: /api/internal/humanized-timeline (auth yok). productId filtresi henüz yok,
      // genel timeline döner; UI tarafında ürün-spesifik filter yapılabilir.
      (async () => {
        try {
          const base = (window.__AGENTBASE__?.apiBase || "").replace(/\/+$/, "")
          const resp = await fetch(base + "/api/internal/humanized-timeline?user_id=1&limit=30")
          if (!resp.ok) return { items: [] }
          const data = await resp.json()
          return { items: Array.isArray(data?.data) ? data.data : [] }
        } catch {
          return { items: [] }
        }
      })(),
    ])
    const item = (Array.isArray(products) ? products : []).find((x) => String(x.id || "") === productId) || {}
    const byProduct = (rows) => (Array.isArray(rows) ? rows.filter((x) => String(x.productId || "") === productId) : [])
    const reviewRows = byProduct(reviews)
    const faqRows = byProduct(faq)
    const ticketRows = byProduct(tickets)
    const metricRows = byProduct(metrics)
    const assetRows = byProduct(assets)
    const history = Array.isArray(historyRes.items) ? historyRes.items.map((x) => ({ at: x.timestamp || "", event: x.summary || x.kind || "-" })) : []
    const sales7d = metricRows.slice(-7).reduce((acc, x) => acc + Number(x.sales || 0), 0)
    const revenue7d = metricRows.slice(-7).reduce((acc, x) => acc + Number(x.revenue || 0), 0)
    const returnAvg = metricRows.length ? metricRows.slice(-7).reduce((acc, x) => acc + Number(x.returnRate || 0), 0) / Math.max(1, metricRows.slice(-7).length) : 0
    const ratingAvg = reviewRows.length ? reviewRows.reduce((acc, x) => acc + Number(x.rating || 0), 0) / Math.max(1, reviewRows.length) : 0
    const detail = {
      overview: {
        sales: sales7d || Number(item.sales || 0),
        revenue: revenue7d,
        rating: ratingAvg,
        returnRate: returnAvg,
      },
      insights: ticketRows.slice(0, 8).map((x) => ({ type: "support", text: String(x.issueType || x.title || "Destek sinyali") })),
      reviews: reviewRows,
      orders: metricRows.slice(-7).map((x, idx) => ({ id: String(x.id || `metric_${idx}`), date: String(x.date || ""), status: "recorded", amount: Number(x.revenue || 0) })),
      history: [...history, ...assetRows.slice(0, 8).map((x) => ({ at: String(x.createdAt || x.updatedAt || ""), event: String(x.kind || "Asset guncellendi") }))],
      faq: faqRows,
      tickets: ticketRows,
      images: Array.isArray(item.images) ? item.images : assetRows.map((x) => String(x.url || "")).filter(Boolean),
    }
    s.selectedDetail = detail
    s.runtimeInsights = [...(detail.insights || []).map((x) => ({ kind: x.type || "insight", text: x.text || "-", at: now() })), ...s.runtimeInsights].slice(0, 10)
    renderRightSide()
    setPresence("watching", "Baglam guncel ve izleniyor", 2800)
  }

  async function loadData() {
    const products = await socialList("products")
    s.stores = []
    s.products = Array.isArray(products) ? products : []
    renderStoreOptions()
    applyFilters()
    if (!s.selectedProductId && s.filtered.length) {
      s.selectedProductId = s.filtered[0].id
      s.context.selectedProduct = s.selectedProductId
      await loadDetail(s.selectedProductId)
    } else if (!s.filtered.length) {
      if (els.chatLog instanceof HTMLElement && !els.chatLog.children.length) {
        appendChat("assistant", "Henüz ürün eklenmedi. Sag ustten + Urun Ekle ile baslayabilirsin.", now(), false)
      }
    }
  }

  els.productList.addEventListener("click", async (e) => {
    const row = e.target instanceof HTMLElement ? e.target.closest("[data-product-id]") : null
    if (!(row instanceof HTMLElement)) return
    const pid = String(row.getAttribute("data-product-id") || "")
    if (!pid) return
    const check = e.target instanceof HTMLElement ? e.target.closest("[data-multi-id]") : null
    if (check instanceof HTMLInputElement) {
      if (check.checked) s.selectedIds.add(pid)
      else s.selectedIds.delete(pid)
      renderProductList()
      return
    }
    s.selectedProductId = pid
    s.context.selectedProduct = pid
    await loadDetail(pid)
    renderProductList()
  })

  els.productList.addEventListener("contextmenu", (e) => {
    const row = e.target instanceof HTMLElement ? e.target.closest("[data-product-id]") : null
    if (!(row instanceof HTMLElement) || !(els.contextMenu instanceof HTMLElement)) return
    e.preventDefault()
    els.contextMenu.hidden = false
    els.contextMenu.style.left = `${e.clientX}px`
    els.contextMenu.style.top = `${e.clientY}px`
    s.selectedProductId = String(row.getAttribute("data-product-id") || "")
  })

  document.addEventListener("click", (e) => {
    const cmd = e.target instanceof HTMLElement ? e.target.closest("[data-chat-seed]") : null
    if (cmd instanceof HTMLElement && els.chatInput instanceof HTMLTextAreaElement) {
      els.chatInput.value = String(cmd.getAttribute("data-chat-seed") || "")
      els.chatInput.focus()
    }
    const ctx = e.target instanceof HTMLElement ? e.target.closest("[data-context-action]") : null
    if (ctx instanceof HTMLElement) {
      const map = {
        create_campaign: "Bu urun icin kampanya olustur",
        generate_banner: "Bu urun icin banner olustur",
        analyze_reviews: "Bu urunun yorumlarini analiz et",
        view_timeline: "Bu urunun gecmis eventlerini goster",
        open_chat: "Bu urun icin operasyon ozeti hazirla",
      }
      const message = map[String(ctx.getAttribute("data-context-action") || "")] || "Bu urun icin analiz yap"
      if (els.contextMenu instanceof HTMLElement) els.contextMenu.hidden = true
      void runPipeline(message, "context_menu")
    }
    const qAction = e.target instanceof HTMLElement ? e.target.closest("[data-quick-action]") : null
    if (qAction instanceof HTMLElement) {
      const cmd = String(qAction.getAttribute("data-quick-command") || "").trim()
      const action = String(qAction.getAttribute("data-quick-action") || "").trim()
      const fallback = action ? `Bu urun icin ${action} adimini baslat` : "Bu urun icin operasyon adimi baslat"
      void runPipeline(cmd || fallback, "quick_action")
    }
    if (els.contextMenu instanceof HTMLElement) {
      const inside = e.target instanceof HTMLElement ? e.target.closest("#tsws-context-menu") : null
      if (!inside) els.contextMenu.hidden = true
    }
  })

  if (els.search instanceof HTMLInputElement) {
    els.search.addEventListener("input", applyFilters)
  }
  if (els.storeFilter instanceof HTMLSelectElement) {
    els.storeFilter.addEventListener("change", () => {
      s.context.selectedStore = els.storeFilter.value || ""
      applyFilters()
    })
  }

  els.rightTabs.forEach((btn) =>
    btn.addEventListener("click", () => {
      s.tab = String(btn.getAttribute("data-tab") || "overview")
      els.rightTabs.forEach((x) => x.classList.toggle("is-active", x === btn))
      renderRightSide()
    })
  )

  els.chatTabs.forEach((btn) =>
    btn.addEventListener("click", () => {
      s.chatTab = String(btn.getAttribute("data-chat-tab") || "chat")
      els.chatTabs.forEach((x) => x.classList.toggle("is-active", x === btn))
    })
  )

  els.modeButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      const mode = String(btn.getAttribute("data-ai-mode") || "analiz")
      s.aiMode = mode
      els.modeButtons.forEach((x) => x.classList.toggle("is-active", x === btn))
    })
  )

  els.bulkActionButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      const action = String(btn.getAttribute("data-bulk-action") || "analyze_reviews")
      const count = s.selectedIds.size
      if (!count) return
      const msg = `${count} urun icin toplu ${action} islemi baslat`
      void runPipeline(msg, "bulk")
    })
  )

  els.chatForm.addEventListener("submit", (e) => {
    e.preventDefault()
    const msg = els.chatInput.value.trim()
    if (!msg) return
    els.chatInput.value = ""
    void runPipeline(msg, "chat")
  })
  els.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      els.chatForm.requestSubmit()
    }
  })

  if (els.addProductBtn instanceof HTMLButtonElement && els.productModal instanceof HTMLElement) {
    els.addProductBtn.addEventListener("click", () => (els.productModal.hidden = false))
  }
  document.querySelectorAll("[data-close-modal]").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (els.productModal instanceof HTMLElement) els.productModal.hidden = true
    })
  )

  if (els.productForm instanceof HTMLFormElement) {
    els.productForm.addEventListener("submit", async (e) => {
      e.preventDefault()
      const rawImages = String((els.productImages || {}).value || "").trim()
      const body = {
        name: String(($("tsop-product-name") || {}).value || "").trim(),
        category: String(($("tsop-product-category") || {}).value || "").trim(),
        price: Number(String(($("tsop-product-price") || {}).value || 0)),
        stock: Number(String((els.productStock || {}).value || 0)),
        description: String((els.productDescription || {}).value || "").trim(),
        images: rawImages ? rawImages.split(",").map((x) => String(x || "").trim()).filter(Boolean) : [],
        sales: 0,
        trendPct: 0,
        aiBadges: [],
      }
      if (!body.name) return
      await socialCreate("products", body)
      if (els.productModal instanceof HTMLElement) els.productModal.hidden = true
      await loadData()
    })
  }

  if (els.reviewForm instanceof HTMLFormElement) {
    els.reviewForm.addEventListener("submit", async (e) => {
      e.preventDefault()
      if (!s.selectedProductId) return
      const body = {
        productId: s.selectedProductId,
        author: String(($("tsop-review-author") || {}).value || "Anonim").trim() || "Anonim",
        rating: Number(String(($("tsop-review-rating") || {}).value || 0)) || 0,
        comment: String(($("tsop-review-comment") || {}).value || "").trim(),
        createdAt: new Date().toISOString(),
      }
      if (!body.comment) return
      await socialCreate("product_reviews", body)
      await loadDetail(s.selectedProductId)
    })
  }

  if (els.faqForm instanceof HTMLFormElement) {
    els.faqForm.addEventListener("submit", async (e) => {
      e.preventDefault()
      if (!s.selectedProductId) return
      const body = {
        productId: s.selectedProductId,
        question: String(($("tsop-faq-question") || {}).value || "").trim(),
        answer: String(($("tsop-faq-answer") || {}).value || "").trim(),
        createdAt: new Date().toISOString(),
      }
      if (!body.question || !body.answer) return
      await socialCreate("product_faq", body)
      await loadDetail(s.selectedProductId)
    })
  }

  if (els.ticketForm instanceof HTMLFormElement) {
    els.ticketForm.addEventListener("submit", async (e) => {
      e.preventDefault()
      if (!s.selectedProductId) return
      const body = {
        productId: s.selectedProductId,
        title: String(($("tsop-ticket-title") || {}).value || "").trim(),
        issueType: String(($("tsop-ticket-issue") || {}).value || "").trim(),
        detail: String(($("tsop-ticket-detail") || {}).value || "").trim(),
        status: "open",
        createdAt: new Date().toISOString(),
      }
      if (!body.title || !body.issueType) return
      await socialCreate("product_support_tickets", body)
      await loadDetail(s.selectedProductId)
    })
  }

  window.tswsReconnectOperation = (operationId) => reconnectOperation(operationId)
  void loadData().then(() => {
    renderRightSide()
    setPresence("idle", "AI hazir")
  }).catch((err) => appendChat("assistant", err instanceof Error ? err.message : "Yukleme hatasi", now(), false))
  callLucide()
})
