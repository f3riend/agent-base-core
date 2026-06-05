// Mağazalar — halkalar + ürün grid + sağ panel + modaller.
// Faz 3 backend: /social-media/stores ve /social-media/products (Bearer auth).
//
// Mevcut tasarım korunur, sadece genişler:
//   - Ürün kartı: rating yıldız + rating_count + trend_pct (yeşil ▲ / kırmızı ▼)
//   - Sağ panel: Haftalık Performans + Yorumlar (max 5) + SSS (max 3 accordion)
//   - Ürün modal: brand, description, rating(+count), dinamik images/reviews/faqs

(function () {
  "use strict"

  const root = document.getElementById("stores-app")
  if (!root) return

  const apiBase = (window.__AGENTBASE__?.apiBase || root.dataset.apiBase || "").replace(/\/+$/, "")
  // user_id artık Bearer token'dan resolve ediliyor; data attribute info amaçlı.
  const userId = (root.dataset.userId || "1").trim() || "1"

  const ringsEl = root.querySelector("[data-rings]")
  const gridEl = root.querySelector("[data-product-grid]")
  const sideEl = root.querySelector("[data-side-panel]")

  const state = {
    stores: [],
    activeStoreId: null,  // UUID string
    items: [],            // ProductListItem[]
    selectedItemId: null, // UUID string
    selectedDetail: null, // ProductRead (nested children)
  }

  // ---------- Utilities ----------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;")
  }

  function fmtPrice(v, currency = "TRY") {
    if (v == null || v === "") return "—"
    const n = Number(v)
    if (!Number.isFinite(n)) return String(v)
    const sym = currency === "TRY" ? "TL" : esc(currency)
    return n.toLocaleString("tr-TR", { maximumFractionDigits: 2 }) + " " + sym
  }

  function fmtNumber(v) {
    if (v == null || v === "") return "—"
    const n = Number(v)
    if (!Number.isFinite(n)) return String(v)
    return n.toLocaleString("tr-TR")
  }

  function initials(name) {
    return String(name || "?").trim().split(/\s+/).map((w) => w[0] || "").join("").slice(0, 2).toUpperCase()
  }

  function activeStore() {
    return state.stores.find((s) => s.id === state.activeStoreId) || null
  }

  function ratingStarsHtml(rating, count) {
    const r = Number(rating)
    if (!Number.isFinite(r) || r <= 0) return ""
    const c = Number(count)
    const cText = Number.isFinite(c) && c > 0 ? `<span style="color:#9ca3af;">(${fmtNumber(c)})</span>` : ""
    return `<span style="color:#f59e0b;">★</span> <span style="color:#374151;">${r.toFixed(1)}</span> ${cText}`
  }

  function ratingFullStarsHtml(rating) {
    const r = Math.max(0, Math.min(5, Math.round(Number(rating) || 0)))
    return `<span style="color:#f59e0b; letter-spacing:0.05em;">${"★".repeat(r)}<span style="color:#e5e7eb;">${"★".repeat(5 - r)}</span></span>`
  }

  function trendBadgeHtml(pct) {
    if (pct == null || pct === "") return ""
    const n = Number(pct)
    if (!Number.isFinite(n) || n === 0) return ""
    const up = n > 0
    const color = up ? "#16a34a" : "#dc2626"
    const arrow = up ? "▲" : "▼"
    return `<span style="color:${color}; font-weight:600;">${arrow} ${Math.abs(n).toFixed(1)}%</span>`
  }

  // ---------- Auth & API ----------
  function authHeaders(json = false) {
    const h = { "Accept": "application/json" }
    if (json) h["Content-Type"] = "application/json"
    const token = String(window.__AGENTBASE__?.accessToken || "").trim()
    if (token) h["Authorization"] = "Bearer " + token
    return h
  }

  async function _request(method, path, body) {
    const init = { method, headers: authHeaders(body !== undefined) }
    if (body !== undefined) init.body = JSON.stringify(body)
    const res = await fetch(apiBase + path, init)
    if (res.status === 204) return null
    let parsed = null
    try { parsed = await res.json() } catch { parsed = null }
    if (!res.ok) {
      const detail = parsed?.detail || parsed?.error || ("HTTP " + res.status)
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail))
    }
    return parsed
  }
  const apiGet    = (path)       => _request("GET", path)
  const apiPost   = (path, body) => _request("POST", path, body || {})
  const apiPatch  = (path, body) => _request("PATCH", path, body || {})
  const apiDelete = (path)       => _request("DELETE", path)

  // ---------- Loaders ----------
  async function loadStores(preserveActive = false) {
    try {
      const data = await apiGet(`/social-media/stores`)
      state.stores = Array.isArray(data) ? data : []
    } catch (e) {
      console.warn("[stores] list failed:", e)
      state.stores = []
    }
    if (!preserveActive || !state.stores.some((s) => s.id === state.activeStoreId)) {
      state.activeStoreId = state.stores.length ? state.stores[0].id : null
    }
    renderRings()
    if (state.activeStoreId != null) await loadItems(state.activeStoreId)
    else renderGrid()
  }

  async function loadItems(storeId) {
    try {
      const data = await apiGet(`/social-media/products?store_id=${encodeURIComponent(storeId)}`)
      state.items = Array.isArray(data) ? data : []
    } catch (e) {
      console.warn("[stores] items failed:", e)
      state.items = []
    }
    state.selectedItemId = null
    state.selectedDetail = null
    renderGrid()
    renderSidePanel()
  }

  async function loadItemDetail(productId) {
    try {
      state.selectedDetail = await apiGet(`/social-media/products/${encodeURIComponent(productId)}`)
    } catch (e) {
      console.warn("[stores] detail failed:", e)
      state.selectedDetail = null
    }
    renderSidePanel()
  }

  // ---------- Render: halkalar ----------
  function renderRings() {
    if (!state.stores.length) {
      ringsEl.innerHTML = '<p style="color:#9ca3af; padding:0.5rem;">Henüz mağaza yok. + Yeni Mağaza ile başla.</p>'
      return
    }
    const ringHTML = (store) => {
      const active = store.id === state.activeStoreId
      const ring = active ? "0 0 0 3px #111827, 0 0 0 5px #fff" : "0 0 0 1px #e5e7eb"
      const logo = String(store.logo_url || "").trim()
      const avatar = logo
        ? `<img src="${esc(logo)}" alt="${esc(store.name || "")}" style="width:100%; height:100%; object-fit:cover;">`
        : `<div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; background:#f3f4f6; color:#374151; font-weight:700; font-size:1.1rem;">${esc(initials(store.name))}</div>`
      return [
        `<button type="button" data-ring data-store-id="${esc(store.id)}" data-store-name="${esc(store.name)}" title="${esc(store.name)}"`,
        `        style="display:flex; flex-direction:column; align-items:center; gap:0.35rem; background:transparent; border:0; cursor:pointer; padding:0;">`,
        `  <span style="width:56px; height:56px; border-radius:9999px; overflow:hidden; box-shadow:${ring};">${avatar}</span>`,
        `  <span style="font-size:0.7rem; color:#374151; max-width:72px; text-align:center; line-height:1.15; word-break:break-word;">${esc(store.name)}</span>`,
        `  <button type="button" data-act="ring-ctx" data-store-id="${esc(store.id)}" data-store-name="${esc(store.name)}" title="Aksiyonlar" aria-label="Mağaza aksiyonları" style="font-size:0.7rem; color:#6b7280; background:transparent; border:0; cursor:pointer; padding:0.1rem 0.3rem;">⋮</button>`,
        `</button>`,
      ].join("")
    }
    ringsEl.innerHTML = state.stores.map(ringHTML).join("")
    ringsEl.querySelectorAll("[data-ring]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-act='ring-ctx']")) return
        const id = String(el.dataset.storeId || "")
        if (id) selectStore(id)
      })
      el.addEventListener("contextmenu", (e) => {
        e.preventDefault()
        openContextMenu(e.clientX, e.clientY, {
          id: String(el.dataset.storeId || ""),
          name: el.dataset.storeName || "",
        })
      })
    })
    ringsEl.querySelectorAll("[data-act='ring-ctx']").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation()
        const rect = btn.getBoundingClientRect()
        openContextMenu(rect.right, rect.bottom, {
          id: String(btn.dataset.storeId || ""),
          name: btn.dataset.storeName || "",
        })
      })
    })
  }

  // ---------- Render: ürün grid + kart ----------
  function renderGrid() {
    if (state.activeStoreId == null) {
      gridEl.innerHTML = '<p style="color:#9ca3af; padding:1rem;">Bir mağaza seç.</p>'
      return
    }
    if (!state.items.length) {
      gridEl.innerHTML = `<p style="color:#9ca3af; padding:1rem;">Bu mağazada ürün yok. Sağ üstte ⋯ menüsünden "Ürün Ekle" ile ekleyebilirsin.</p>`
      return
    }
    gridEl.innerHTML = state.items.map(productCard).join("")
    gridEl.querySelectorAll("[data-item]").forEach((card) => {
      card.addEventListener("click", () => {
        const id = String(card.dataset.itemId || "")
        if (id) {
          state.selectedItemId = id
          state.selectedDetail = null
          renderSidePanel()      // ara durum (yükleniyor)
          void loadItemDetail(id)
        }
      })
    })
  }

  function productCard(item) {
    const img = String(item.thumb_url || "").trim()
    const placeholder = `<div style="width:100%; aspect-ratio:4/3; background:#f3f4f6; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-size:1.4rem;">📦</div>`
    const visual = img
      ? `<img src="${esc(img)}" alt="${esc(item.name || "")}" style="width:100%; aspect-ratio:4/3; object-fit:cover; display:block;">`
      : placeholder

    const stars = ratingStarsHtml(item.rating, item.rating_count)
    const trend = trendBadgeHtml(item.trend_pct)

    const metaParts = []
    if (item.stock != null && item.stock !== "") metaParts.push(`Stok: ${esc(item.stock)}`)
    if (item.brand) metaParts.push(esc(item.brand))
    const discountText = formatDiscountInline(item)
    if (discountText) metaParts.push(discountText)

    return [
      `<article data-item data-item-id="${esc(item.id)}" style="background:#fff; border:1px solid #e5e7eb; border-radius:0.65rem; cursor:pointer; overflow:hidden; transition:transform .12s;"`,
      `         onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='translateY(0)'">`,
      visual,
      `  <div style="padding:0.6rem 0.7rem;">`,
      `    <div style="display:flex; justify-content:space-between; gap:0.4rem; align-items:flex-start;">`,
      `      <strong style="font-size:0.9rem; line-height:1.2; word-break:break-word;">${esc(item.name || "—")}</strong>`,
      `    </div>`,
      `    <div style="margin-top:0.25rem; color:#111827; font-weight:700;">${esc(fmtPrice(item.price, item.currency))}</div>`,
      stars ? `    <div style="margin-top:0.2rem; font-size:0.78rem;">${stars}</div>` : "",
      trend ? `    <div style="margin-top:0.15rem; font-size:0.75rem;">${trend}</div>` : "",
      metaParts.length ? `    <div style="margin-top:0.2rem; color:#6b7280; font-size:0.75rem;">${metaParts.join(" · ")}</div>` : "",
      `  </div>`,
      `</article>`,
    ].filter(Boolean).join("")
  }

  function formatDiscountInline(item) {
    if (item.discount == null || item.discount === "") return ""
    const v = Number(item.discount)
    if (!Number.isFinite(v) || v <= 0) return ""
    if (item.discount_type === "percentage") return `%${v} ind`
    if (item.discount_type === "fixed") return `−${v.toLocaleString("tr-TR")} ind`
    return `−${v} ind`
  }

  // ---------- Render: sağ panel ----------
  function renderSidePanel() {
    if (!state.selectedItemId) {
      sideEl.innerHTML = `
        <h3 style="margin:0 0 0.5rem; font-size:0.85rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.04em;">Seçili Ürün</h3>
        <p style="margin:0; color:#9ca3af; font-size:0.85rem;">Bir karta tıklayarak detayları gör.</p>`
      return
    }
    const detail = state.selectedDetail
    if (!detail) {
      sideEl.innerHTML = `<p style="margin:0; color:#9ca3af; font-size:0.85rem;">Yükleniyor…</p>`
      return
    }

    const firstImg = String(detail.images?.[0]?.url || "").trim()
    const visual = firstImg
      ? `<img src="${esc(firstImg)}" alt="${esc(detail.name || "")}" style="width:100%; border-radius:0.5rem; margin-top:0.5rem;">`
      : ""

    const stars = ratingStarsHtml(detail.rating, detail.rating_count)
    const discountText = formatDiscountInline(detail)

    const dl = [
      `<dl style="display:grid; grid-template-columns:auto 1fr; gap:0.2rem 0.6rem; font-size:0.85rem; margin:0;">`,
      `  <dt style="color:#6b7280;">Fiyat:</dt><dd style="margin:0;">${esc(fmtPrice(detail.price, detail.currency))}</dd>`,
      discountText ? `<dt style="color:#6b7280;">İndirim:</dt><dd style="margin:0;">${esc(discountText)}</dd>` : "",
      detail.stock != null ? `<dt style="color:#6b7280;">Stok:</dt><dd style="margin:0;">${esc(detail.stock)}</dd>` : "",
      detail.category ? `<dt style="color:#6b7280;">Kategori:</dt><dd style="margin:0;">${esc(detail.category)}</dd>` : "",
      detail.brand ? `<dt style="color:#6b7280;">Marka:</dt><dd style="margin:0;">${esc(detail.brand)}</dd>` : "",
      stars ? `<dt style="color:#6b7280;">Puan:</dt><dd style="margin:0;">${stars}</dd>` : "",
      detail.id != null ? `<dt style="color:#6b7280;">ID:</dt><dd style="margin:0; font-family:monospace; font-size:0.75rem;">${esc(String(detail.id).slice(0, 8))}…</dd>` : "",
      `</dl>`,
    ].filter(Boolean).join("")

    const description = detail.description
      ? `<p style="margin:0.6rem 0 0; color:#374151; font-size:0.85rem; line-height:1.45;">${esc(detail.description)}</p>`
      : ""

    // HAFTALIK PERFORMANS
    const hasWeekly = detail.weekly_sales != null || detail.weekly_revenue != null || detail.trend_pct != null
    const weeklyHtml = hasWeekly ? [
      `<section style="margin-top:0.9rem; padding-top:0.7rem; border-top:1px solid #e5e7eb;">`,
      `  <h4 style="margin:0 0 0.4rem; font-size:0.75rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.05em;">Haftalık Performans</h4>`,
      `  <dl style="display:grid; grid-template-columns:auto 1fr; gap:0.15rem 0.6rem; font-size:0.85rem; margin:0;">`,
      detail.weekly_sales != null ? `<dt style="color:#6b7280;">Satış:</dt><dd style="margin:0;">${esc(fmtNumber(detail.weekly_sales))} adet</dd>` : "",
      detail.weekly_revenue != null ? `<dt style="color:#6b7280;">Gelir:</dt><dd style="margin:0;">${esc(fmtPrice(detail.weekly_revenue, detail.currency))}</dd>` : "",
      detail.trend_pct != null ? `<dt style="color:#6b7280;">Trend:</dt><dd style="margin:0;">${trendBadgeHtml(detail.trend_pct) || "—"}</dd>` : "",
      `  </dl>`,
      `</section>`,
    ].filter(Boolean).join("") : ""

    // YORUMLAR (max 5)
    const reviews = Array.isArray(detail.reviews) ? detail.reviews : []
    const shown = reviews.slice(0, 5)
    const reviewsHtml = reviews.length ? [
      `<section style="margin-top:0.9rem; padding-top:0.7rem; border-top:1px solid #e5e7eb;">`,
      `  <h4 style="margin:0 0 0.4rem; font-size:0.75rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.05em;">Yorumlar <span style="color:#9ca3af; text-transform:none; letter-spacing:0;">(${reviews.length})</span></h4>`,
      `  <ul style="list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:0.35rem;">`,
      shown.map((r) => [
        `<li style="padding:0.4rem 0; border-bottom:1px solid #f3f4f6;">`,
        `  <div style="font-size:0.8rem;">${ratingFullStarsHtml(r.rating)}</div>`,
        r.content ? `<p style="margin:0.2rem 0 0; color:#374151; font-size:0.82rem; line-height:1.4;">${esc(r.content)}</p>` : "",
        r.review_date ? `<small style="color:#9ca3af; font-size:0.7rem;">${esc(r.review_date)}</small>` : "",
        `</li>`,
      ].filter(Boolean).join("")).join(""),
      `  </ul>`,
      reviews.length > 5 ? `<button type="button" data-act="view-all-reviews" style="margin-top:0.4rem; background:transparent; border:0; color:#2563eb; cursor:pointer; font-size:0.8rem; padding:0;">Tümünü gör (${reviews.length})</button>` : "",
      `</section>`,
    ].filter(Boolean).join("") : ""

    // SSS (max 3 accordion)
    const faqs = Array.isArray(detail.faqs) ? detail.faqs : []
    const shownFaqs = faqs.slice(0, 3)
    const faqsHtml = faqs.length ? [
      `<section style="margin-top:0.9rem; padding-top:0.7rem; border-top:1px solid #e5e7eb;">`,
      `  <h4 style="margin:0 0 0.4rem; font-size:0.75rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.05em;">Sıkça Sorulan Sorular</h4>`,
      `  <div style="display:flex; flex-direction:column; gap:0.35rem;">`,
      shownFaqs.map((f) => [
        `<details style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:0.4rem; padding:0.4rem 0.55rem;">`,
        `  <summary style="cursor:pointer; font-size:0.82rem; color:#111827; font-weight:500;">${esc(f.question || "—")}</summary>`,
        f.answer ? `<p style="margin:0.35rem 0 0; color:#374151; font-size:0.8rem; line-height:1.45;">${esc(f.answer)}</p>` : "",
        `</details>`,
      ].filter(Boolean).join("")).join(""),
      `  </div>`,
      `</section>`,
    ].filter(Boolean).join("") : ""

    const dataEntryHtml = dataEntrySectionHtml(String(detail.id || ""))

    sideEl.innerHTML = [
      `<h3 style="margin:0 0 0.5rem; font-size:0.85rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.04em;">Seçili Ürün</h3>`,
      `<h4 style="margin:0 0 0.4rem; font-size:1rem;">${esc(detail.name || "—")}</h4>`,
      dl,
      description,
      visual,
      weeklyHtml,
      reviewsHtml,
      faqsHtml,
      dataEntryHtml,
    ].filter(Boolean).join("")
  }

  function dataEntrySectionHtml(productId) {
    if (!productId) return ""
    const pid = esc(productId)
    return [
      `<section style="margin-top:0.9rem; padding-top:0.7rem; border-top:1px solid #e5e7eb;">`,
      `  <h4 style="margin:0 0 0.4rem; font-size:0.75rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.05em;">Veri Ekle</h4>`,
      `  <details style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:0.4rem; margin-bottom:0.4rem;">`,
      `    <summary style="cursor:pointer; padding:0.45rem 0.6rem; font-size:0.82rem; color:#111827; font-weight:500;">+ Yorum Ekle</summary>`,
      `    <form data-act="add-review" data-product-id="${pid}" style="padding:0 0.6rem 0.55rem; display:flex; flex-direction:column; gap:0.35rem;">`,
      `      <select name="rating" style="padding:0.35rem 0.45rem; border:1px solid #e5e7eb; border-radius:0.35rem; background:#fff; font-size:0.82rem;">`,
      `        <option value="">Puan seç</option>`,
      `        <option value="5">★★★★★ 5</option>`,
      `        <option value="4">★★★★☆ 4</option>`,
      `        <option value="3">★★★☆☆ 3</option>`,
      `        <option value="2">★★☆☆☆ 2</option>`,
      `        <option value="1">★☆☆☆☆ 1</option>`,
      `      </select>`,
      `      <input name="review_date" type="text" placeholder="25 Aralık 2024" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; font-size:0.82rem;">`,
      `      <textarea name="content" rows="2" placeholder="Yorum içeriği" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; resize:vertical; font-family:inherit; font-size:0.82rem;"></textarea>`,
      `      <div style="display:flex; gap:0.4rem; align-items:center; justify-content:space-between;">`,
      `        <small data-result style="color:#6b7280; font-size:0.7rem;"></small>`,
      `        <button type="submit" style="padding:0.35rem 0.85rem; background:#111827; color:#fff; border:0; border-radius:0.35rem; cursor:pointer; font-size:0.78rem; font-weight:600;">Yorumu Kaydet</button>`,
      `      </div>`,
      `    </form>`,
      `  </details>`,
      `  <details style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:0.4rem;">`,
      `    <summary style="cursor:pointer; padding:0.45rem 0.6rem; font-size:0.82rem; color:#111827; font-weight:500;">+ SSS Ekle</summary>`,
      `    <form data-act="add-faq" data-product-id="${pid}" style="padding:0 0.6rem 0.55rem; display:flex; flex-direction:column; gap:0.35rem;">`,
      `      <input name="question" type="text" placeholder="Soru" required style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; font-size:0.82rem;">`,
      `      <textarea name="answer" rows="2" placeholder="Cevap" required style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; resize:vertical; font-family:inherit; font-size:0.82rem;"></textarea>`,
      `      <div style="display:flex; gap:0.4rem; align-items:center; justify-content:space-between;">`,
      `        <small data-result style="color:#6b7280; font-size:0.7rem;"></small>`,
      `        <button type="submit" style="padding:0.35rem 0.85rem; background:#111827; color:#fff; border:0; border-radius:0.35rem; cursor:pointer; font-size:0.78rem; font-weight:600;">SSS Kaydet</button>`,
      `      </div>`,
      `    </form>`,
      `  </details>`,
      `</section>`,
    ].join("")
  }

  function selectStore(id) {
    state.activeStoreId = String(id)
    renderRings()
    void loadItems(state.activeStoreId)
  }

  // ---------- Context menu ----------
  const ctxEl = document.getElementById("stores-ctx-menu")
  let ctxStore = null

  function openContextMenu(x, y, store) {
    ctxStore = store
    ctxEl.hidden = false
    ctxEl.style.left = x + "px"
    ctxEl.style.top = y + "px"
  }

  function closeContextMenu() {
    ctxEl.hidden = true
    ctxStore = null
  }

  ctxEl.querySelectorAll("button[data-ctx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const act = btn.dataset.ctx
      const s = ctxStore
      closeContextMenu()
      if (!s) return
      if (act === "add-product") openCreateProductModal(s)
      else if (act === "view-products") openViewProductsModal(s)
    })
  })

  document.addEventListener("click", (e) => {
    if (!ctxEl.hidden && !ctxEl.contains(e.target)) closeContextMenu()
  })
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeContextMenu()
      closeAllModals()
    }
  })

  // ---------- Modallar ----------
  function openModal(name) {
    const m = document.querySelector(`[data-modal='${name}']`)
    if (!m) return null
    m.hidden = false
    const result = m.querySelector("[data-modal-result]")
    if (result) result.innerHTML = ""
    return m
  }
  function closeModal(name) {
    const m = document.querySelector(`[data-modal='${name}']`)
    if (m) m.hidden = true
  }
  function closeAllModals() {
    document.querySelectorAll("[data-modal]").forEach((m) => { m.hidden = true })
  }

  document.querySelectorAll("[data-modal]").forEach((m) => {
    m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true })
  })
  document.querySelectorAll("[data-modal-cancel]").forEach((b) => {
    b.addEventListener("click", () => { closeAllModals() })
  })

  document.querySelectorAll("[data-act='open-create-store']").forEach((b) => {
    b.addEventListener("click", () => openCreateStoreModal())
  })

  function openCreateStoreModal() {
    const m = openModal("create-store")
    if (!m) return
    const f = m.querySelector("[data-modal-form='create-store']")
    if (f) f.reset()
  }

  function openCreateProductModal(store) {
    const m = openModal("create-product")
    if (!m) return
    m.querySelector("[data-modal-title-store]").textContent = store.name || `#${String(store.id).slice(0, 8)}`
    const f = m.querySelector("[data-modal-form='create-product']")
    f.reset()
    f.elements.store_id.value = String(store.id)
    // dinamik listeleri temizle, varsayılan 1 image satırı bırak
    resetDynamicLists(f)
  }

  async function openViewProductsModal(store) {
    const m = openModal("view-products")
    if (!m) return
    m.querySelector("[data-modal-title-store]").textContent = store.name || `#${String(store.id).slice(0, 8)}`
    const grid = m.querySelector("[data-fullscreen-grid]")
    grid.innerHTML = '<p style="color:#9ca3af; padding:1rem;">Yükleniyor…</p>'
    try {
      const data = await apiGet(`/social-media/products?store_id=${encodeURIComponent(store.id)}`)
      const items = Array.isArray(data) ? data : []
      grid.innerHTML = items.length ? items.map(productCard).join("") : '<p style="color:#9ca3af; padding:1rem;">Bu mağazada ürün yok.</p>'
    } catch (e) {
      grid.innerHTML = `<p style="color:#dc2626; padding:1rem;">${esc(e.message || String(e))}</p>`
    }
  }

  // ---------- Dinamik satırlar (image / review / faq) ----------
  function resetDynamicLists(form) {
    const imgs = form.querySelector("[data-images-list]")
    if (imgs) {
      imgs.innerHTML = ""
      addImageRow(form, "")
    }
    const revs = form.querySelector("[data-reviews-list]")
    if (revs) revs.innerHTML = ""
    const faqs = form.querySelector("[data-faqs-list]")
    if (faqs) faqs.innerHTML = ""
  }

  function addImageRow(form, value = "") {
    const list = form.querySelector("[data-images-list]")
    if (!list) return
    const row = document.createElement("div")
    row.setAttribute("data-image-row", "")
    row.style.cssText = "display:flex; gap:0.35rem; align-items:center;"
    row.innerHTML = `
      <input type="url" placeholder="https://…" value="${esc(value)}" style="flex:1; padding:0.4rem 0.55rem; border:1px solid #e5e7eb; border-radius:0.4rem;">
      <button type="button" data-remove-row title="Sil"
              style="padding:0.3rem 0.55rem; background:#fff; color:#dc2626; border:1px solid #fecaca; border-radius:0.4rem; cursor:pointer;">−</button>
    `
    list.appendChild(row)
  }

  function addReviewRow(form) {
    const list = form.querySelector("[data-reviews-list]")
    if (!list) return
    const row = document.createElement("div")
    row.setAttribute("data-review-row", "")
    row.style.cssText = "display:flex; flex-direction:column; gap:0.3rem; padding:0.45rem; background:#f9fafb; border:1px solid #e5e7eb; border-radius:0.4rem;"
    row.innerHTML = `
      <div style="display:grid; grid-template-columns:90px 1fr auto; gap:0.35rem; align-items:center;">
        <select data-r-rating style="padding:0.35rem 0.45rem; border:1px solid #e5e7eb; border-radius:0.35rem; background:#fff;">
          <option value="">Puan</option>
          <option value="5">★★★★★ 5</option>
          <option value="4">★★★★☆ 4</option>
          <option value="3">★★★☆☆ 3</option>
          <option value="2">★★☆☆☆ 2</option>
          <option value="1">★☆☆☆☆ 1</option>
        </select>
        <input data-r-date type="text" placeholder="25 Aralık 2024" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem;">
        <button type="button" data-remove-row title="Sil"
                style="padding:0.25rem 0.5rem; background:#fff; color:#dc2626; border:1px solid #fecaca; border-radius:0.35rem; cursor:pointer;">−</button>
      </div>
      <textarea data-r-content rows="2" placeholder="Yorum içeriği" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; resize:vertical; font-family:inherit;"></textarea>
    `
    list.appendChild(row)
  }

  function addFaqRow(form) {
    const list = form.querySelector("[data-faqs-list]")
    if (!list) return
    const row = document.createElement("div")
    row.setAttribute("data-faq-row", "")
    row.style.cssText = "display:flex; flex-direction:column; gap:0.3rem; padding:0.45rem; background:#f9fafb; border:1px solid #e5e7eb; border-radius:0.4rem;"
    row.innerHTML = `
      <div style="display:grid; grid-template-columns:1fr auto; gap:0.35rem; align-items:center;">
        <input data-f-question type="text" placeholder="Soru" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem;">
        <button type="button" data-remove-row title="Sil"
                style="padding:0.25rem 0.5rem; background:#fff; color:#dc2626; border:1px solid #fecaca; border-radius:0.35rem; cursor:pointer;">−</button>
      </div>
      <textarea data-f-answer rows="2" placeholder="Cevap" style="padding:0.35rem 0.5rem; border:1px solid #e5e7eb; border-radius:0.35rem; resize:vertical; font-family:inherit;"></textarea>
    `
    list.appendChild(row)
  }

  // Modal düğmeleri (+ ekleme / − silme) — delegated
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null
    if (!t) return
    if (t.matches("[data-add-image]")) {
      const form = t.closest("form"); if (form) addImageRow(form)
    } else if (t.matches("[data-add-review]")) {
      const form = t.closest("form"); if (form) addReviewRow(form)
    } else if (t.matches("[data-add-faq]")) {
      const form = t.closest("form"); if (form) addFaqRow(form)
    } else if (t.matches("[data-remove-row]")) {
      const row = t.closest("[data-image-row], [data-review-row], [data-faq-row]")
      if (row) row.remove()
    }
  })

  // ---------- Form submit: mağaza oluştur ----------
  document.querySelector("[data-modal-form='create-store']")?.addEventListener("submit", async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    const r = form.querySelector("[data-modal-result]")
    // Backend sadece name + rating + logo_url kabul ediyor.
    // Form'daki owner/instagram/banner_url tasarım bütünlüğü için duruyor
    // ama payload'a koymuyoruz (extra alanları gönderirsek de Pydantic default
    // 'ignore' ile reddetmez — yine de minimal payload göndermek temiz).
    const fd = new FormData(form)
    const name = String(fd.get("name") || "").trim()
    if (!name) { if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Mağaza adı gerekli.</p>`; return }
    const body = {
      name,
      logo_url: String(fd.get("logo_url") || "").trim() || null,
      banner_url: String(fd.get("banner_url") || "").trim() || null,
    }
    if (r) r.innerHTML = '<p style="color:#6b7280; font-size:0.85rem; margin:0.4rem 0 0;">Gönderiliyor…</p>'
    try {
      const resp = await apiPost("/social-media/stores", body)
      if (r) r.innerHTML = `<p style="color:#16a34a; font-weight:600; margin:0.4rem 0 0;">Mağaza oluşturuldu: ID=${esc(String(resp?.id || "").slice(0, 8))}…</p>`
      await loadStores()
      if (resp?.id) selectStore(String(resp.id))
      setTimeout(() => closeModal("create-store"), 400)
    } catch (err) {
      if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Hata: ${esc(err.message || String(err))}</p>`
    }
  })

  // ---------- Form submit: ürün oluştur (nested) ----------
  document.querySelector("[data-modal-form='create-product']")?.addEventListener("submit", async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    const r = form.querySelector("[data-modal-result]")
    const fd = new FormData(form)

    const name = String(fd.get("name") || "").trim()
    if (!name) { if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Ürün adı gerekli.</p>`; return }

    const numOrNull = (k) => {
      const v = fd.get(k); if (v == null || String(v).trim() === "") return null
      const n = Number(v); return Number.isFinite(n) ? n : null
    }
    const strOrNull = (k) => {
      const v = String(fd.get(k) || "").trim(); return v || null
    }

    // Dinamik listeleri topla
    const images = Array.from(form.querySelectorAll("[data-image-row]"))
      .map((row, idx) => {
        const url = String(row.querySelector("input")?.value || "").trim()
        return url ? { url, sort_order: idx } : null
      })
      .filter(Boolean)

    const reviews = Array.from(form.querySelectorAll("[data-review-row]"))
      .map((row) => {
        const rating = Number(row.querySelector("[data-r-rating]")?.value)
        const review_date = String(row.querySelector("[data-r-date]")?.value || "").trim() || null
        const content = String(row.querySelector("[data-r-content]")?.value || "").trim() || null
        const any = Number.isFinite(rating) || review_date || content
        return any ? {
          rating: Number.isFinite(rating) && rating > 0 ? rating : null,
          review_date,
          content,
        } : null
      })
      .filter(Boolean)

    const faqs = Array.from(form.querySelectorAll("[data-faq-row]"))
      .map((row) => {
        const question = String(row.querySelector("[data-f-question]")?.value || "").trim() || null
        const answer = String(row.querySelector("[data-f-answer]")?.value || "").trim() || null
        return (question || answer) ? { question, answer } : null
      })
      .filter(Boolean)

    const body = {
      store_id: String(fd.get("store_id") || ""),
      name,
      description: strOrNull("description"),
      category: strOrNull("category"),
      brand: strOrNull("brand"),
      currency: "TRY",
      status: "active",
      price: numOrNull("price"),
      discount: numOrNull("discount"),
      discount_type: strOrNull("discount_type"),
      stock: numOrNull("stock"),
      rating: numOrNull("rating"),
      rating_count: numOrNull("rating_count"),
      images,
      reviews,
      faqs,
    }

    if (r) r.innerHTML = '<p style="color:#6b7280; font-size:0.85rem; margin:0.4rem 0 0;">Gönderiliyor…</p>'
    try {
      const resp = await apiPost("/social-media/products", body)
      if (r) r.innerHTML = `<p style="color:#16a34a; font-weight:600; margin:0.4rem 0 0;">Ürün eklendi: ${esc(resp?.name || "—")}</p>`
      if (state.activeStoreId === body.store_id) await loadItems(state.activeStoreId)
      setTimeout(() => closeModal("create-product"), 500)
    } catch (err) {
      if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Hata: ${esc(err.message || String(err))}</p>`
    }
  })

  // ---------- Sağ panel "Veri Ekle" submit'leri (event delegation) ----------
  document.addEventListener("submit", async (e) => {
    const form = e.target instanceof HTMLFormElement ? e.target : null
    if (!form) return
    const act = form.dataset.act
    if (act === "add-review") {
      e.preventDefault()
      await handleAddReview(form)
    } else if (act === "add-faq") {
      e.preventDefault()
      await handleAddFaq(form)
    }
  })

  async function handleAddReview(form) {
    const pid = String(form.dataset.productId || "")
    if (!pid) return
    const result = form.querySelector("[data-result]")
    const fd = new FormData(form)
    const ratingRaw = fd.get("rating")
    const ratingNum = Number(ratingRaw)
    const body = {
      rating: Number.isFinite(ratingNum) && ratingNum > 0 ? ratingNum : null,
      review_date: String(fd.get("review_date") || "").trim() || null,
      content: String(fd.get("content") || "").trim() || null,
    }
    if (result) { result.textContent = "Gönderiliyor…"; result.style.color = "#6b7280" }
    try {
      await apiPost(`/social-media/products/${encodeURIComponent(pid)}/reviews`, body)
      if (result) { result.textContent = "Yorum eklendi ✓"; result.style.color = "#16a34a" }
      form.reset()
      if (state.selectedItemId === pid) await loadItemDetail(pid)
    } catch (err) {
      if (result) { result.textContent = "Hata: " + (err.message || String(err)); result.style.color = "#dc2626" }
    }
  }

  async function handleAddFaq(form) {
    const pid = String(form.dataset.productId || "")
    if (!pid) return
    const result = form.querySelector("[data-result]")
    const fd = new FormData(form)
    const body = {
      question: String(fd.get("question") || "").trim() || null,
      answer: String(fd.get("answer") || "").trim() || null,
    }
    if (!body.question && !body.answer) {
      if (result) { result.textContent = "Soru veya cevap girilmeli."; result.style.color = "#dc2626" }
      return
    }
    if (result) { result.textContent = "Gönderiliyor…"; result.style.color = "#6b7280" }
    try {
      await apiPost(`/social-media/products/${encodeURIComponent(pid)}/faqs`, body)
      if (result) { result.textContent = "SSS eklendi ✓"; result.style.color = "#16a34a" }
      form.reset()
      if (state.selectedItemId === pid) await loadItemDetail(pid)
    } catch (err) {
      if (result) { result.textContent = "Hata: " + (err.message || String(err)); result.style.color = "#dc2626" }
    }
  }

  // ---------- Init ----------
  void loadStores()
})()
