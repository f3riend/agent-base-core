// Mağazalar — halkalar + ürün grid + sağ panel + modaller (yeni mağaza,
// ürün ekle, ürünleri gör) + context menu (sağ tık + ⋮).
// Backend: /commerce-platform/stores + /commerce-platform/stores/{id}/items
// Mutate:  /commerce-platform/internal/create-store ve /create-product

(function () {
  "use strict"

  const root = document.getElementById("stores-app")
  if (!root) return

  const apiBase = (root.dataset.apiBase || "").replace(/\/+$/, "")
  const userId = (root.dataset.userId || "1").trim() || "1"

  const ringsEl = root.querySelector("[data-rings]")
  const gridEl = root.querySelector("[data-product-grid]")
  const sideEl = root.querySelector("[data-side-panel]")

  const state = {
    stores: [],
    activeStoreId: null,
    items: [],
    selectedItemId: null,
  }

  // ---------- Utilities ----------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;")
  }

  function fmtPrice(v) {
    if (v == null || v === "") return "—"
    const n = Number(v)
    if (!Number.isFinite(n)) return String(v)
    return n.toLocaleString("tr-TR", { maximumFractionDigits: 2 }) + " TL"
  }

  function initials(name) {
    return String(name || "?").trim().split(/\s+/).map((w) => w[0] || "").join("").slice(0, 2).toUpperCase()
  }

  function activeStore() {
    return state.stores.find((s) => s.id === state.activeStoreId) || null
  }

  async function apiGet(path) {
    const res = await fetch(apiBase + path, { headers: { "Accept": "application/json" } })
    if (!res.ok) throw new Error("HTTP " + res.status)
    return res.json()
  }

  async function apiPost(path, body) {
    const res = await fetch(apiBase + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    })
    let parsed = null
    try { parsed = await res.json() } catch { parsed = null }
    if (!res.ok) {
      const detail = parsed?.detail || parsed?.error || ("HTTP " + res.status)
      throw new Error(String(detail))
    }
    return parsed
  }

  // ---------- Loaders ----------
  async function loadStores(preserveActive = false) {
    try {
      const { data } = await apiGet(`/commerce-platform/stores?user_id=${encodeURIComponent(userId)}`)
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
      const { data } = await apiGet(`/commerce-platform/stores/${encodeURIComponent(storeId)}/items`)
      state.items = Array.isArray(data) ? data : []
    } catch (e) {
      console.warn("[stores] items failed:", e)
      state.items = []
    }
    state.selectedItemId = null
    renderGrid()
    renderSidePanel()
  }

  // ---------- Render ----------
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
        const id = Number(el.dataset.storeId)
        if (id) selectStore(id)
      })
      el.addEventListener("contextmenu", (e) => {
        e.preventDefault()
        openContextMenu(e.clientX, e.clientY, {
          id: Number(el.dataset.storeId),
          name: el.dataset.storeName || "",
        })
      })
    })
    ringsEl.querySelectorAll("[data-act='ring-ctx']").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation()
        const rect = btn.getBoundingClientRect()
        openContextMenu(rect.right, rect.bottom, {
          id: Number(btn.dataset.storeId),
          name: btn.dataset.storeName || "",
        })
      })
    })
  }

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
        const id = Number(card.dataset.itemId)
        if (id) { state.selectedItemId = id; renderSidePanel() }
      })
    })
  }

  function productCard(item) {
    const img = String(item.image_url || "").trim()
    const placeholder = `<div style="width:100%; aspect-ratio:4/3; background:#f3f4f6; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-size:1.4rem;">📦</div>`
    const visual = img
      ? `<img src="${esc(img)}" alt="${esc(item.name || "")}" style="width:100%; aspect-ratio:4/3; object-fit:cover; display:block;">`
      : placeholder
    const disc = item.discount_percent
    const stock = item.stock
    const metaParts = []
    if (stock != null && stock !== "") metaParts.push(`Stok: ${esc(stock)}`)
    if (disc != null && disc !== "" && Number(disc) > 0) metaParts.push(`%${esc(disc)} ind`)
    return [
      `<article data-item data-item-id="${esc(item.id)}" style="background:#fff; border:1px solid #e5e7eb; border-radius:0.65rem; cursor:pointer; overflow:hidden; transition:transform .12s;"`,
      `         onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='translateY(0)'">`,
      visual,
      `  <div style="padding:0.6rem 0.7rem;">`,
      `    <div style="display:flex; justify-content:space-between; gap:0.4rem; align-items:flex-start;">`,
      `      <strong style="font-size:0.9rem; line-height:1.2; word-break:break-word;">${esc(item.name || "—")}</strong>`,
      `    </div>`,
      `    <div style="margin-top:0.25rem; color:#111827; font-weight:700;">${esc(fmtPrice(item.price))}</div>`,
      metaParts.length ? `    <div style="margin-top:0.2rem; color:#6b7280; font-size:0.75rem;">${metaParts.join(" · ")}</div>` : "",
      `  </div>`,
      `</article>`,
    ].filter(Boolean).join("")
  }

  function renderSidePanel() {
    if (!state.selectedItemId) {
      sideEl.innerHTML = `
        <h3 style="margin:0 0 0.5rem; font-size:0.85rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.04em;">Seçili Ürün</h3>
        <p style="margin:0; color:#9ca3af; font-size:0.85rem;">Bir karta tıklayarak detayları gör.</p>`
      return
    }
    const item = state.items.find((i) => i.id === state.selectedItemId)
    if (!item) { sideEl.innerHTML = "" ; return }
    const img = String(item.image_url || "").trim()
    const visual = img
      ? `<img src="${esc(img)}" alt="${esc(item.name || "")}" style="width:100%; border-radius:0.5rem; margin-top:0.5rem;">`
      : ""
    sideEl.innerHTML = [
      `<h3 style="margin:0 0 0.5rem; font-size:0.85rem; color:#6b7280; text-transform:uppercase; letter-spacing:0.04em;">Seçili Ürün</h3>`,
      `<h4 style="margin:0 0 0.4rem; font-size:1rem;">${esc(item.name || "—")}</h4>`,
      `<dl style="display:grid; grid-template-columns:auto 1fr; gap:0.2rem 0.6rem; font-size:0.85rem; margin:0;">`,
      `  <dt style="color:#6b7280;">Fiyat:</dt><dd style="margin:0;">${esc(fmtPrice(item.price))}</dd>`,
      item.discount_percent != null ? `<dt style="color:#6b7280;">İndirim:</dt><dd style="margin:0;">${esc(item.discount_percent)}%</dd>` : "",
      item.stock != null ? `<dt style="color:#6b7280;">Stok:</dt><dd style="margin:0;">${esc(item.stock)}</dd>` : "",
      item.category ? `<dt style="color:#6b7280;">Kategori:</dt><dd style="margin:0;">${esc(item.category)}</dd>` : "",
      item.id != null ? `<dt style="color:#6b7280;">ID:</dt><dd style="margin:0;">#${esc(item.id)}</dd>` : "",
      `</dl>`,
      visual,
    ].filter(Boolean).join("")
  }

  function selectStore(id) {
    state.activeStoreId = id
    renderRings()
    void loadItems(id)
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

  // ---------- Modals ----------
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
    m.querySelector("[data-modal-title-store]").textContent = store.name || `#${store.id}`
    const f = m.querySelector("[data-modal-form='create-product']")
    f.reset()
    f.elements.store_id.value = String(store.id)
  }

  async function openViewProductsModal(store) {
    const m = openModal("view-products")
    if (!m) return
    m.querySelector("[data-modal-title-store]").textContent = store.name || `#${store.id}`
    const grid = m.querySelector("[data-fullscreen-grid]")
    grid.innerHTML = '<p style="color:#9ca3af; padding:1rem;">Yükleniyor…</p>'
    try {
      const { data } = await apiGet(`/commerce-platform/stores/${encodeURIComponent(store.id)}/items`)
      const items = Array.isArray(data) ? data : []
      grid.innerHTML = items.length ? items.map(productCard).join("") : '<p style="color:#9ca3af; padding:1rem;">Bu mağazada ürün yok.</p>'
    } catch (e) {
      grid.innerHTML = `<p style="color:#dc2626; padding:1rem;">${esc(e.message || String(e))}</p>`
    }
  }

  // Form submit handlers
  document.querySelector("[data-modal-form='create-store']")?.addEventListener("submit", async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    const body = formBody(form)
    body.user_id = Number(userId) || 1
    const r = form.querySelector("[data-modal-result]")
    if (r) r.innerHTML = '<p style="color:#6b7280; font-size:0.85rem; margin:0.4rem 0 0;">Gönderiliyor…</p>'
    try {
      const resp = await apiPost("/commerce-platform/internal/create-store", body)
      if (r) r.innerHTML = `<p style="color:#16a34a; font-weight:600; margin:0.4rem 0 0;">Mağaza oluşturuldu: ID=${esc(resp?.data?.id)}</p>`
      await loadStores()
      if (resp?.data?.id) selectStore(Number(resp.data.id))
      setTimeout(() => closeModal("create-store"), 400)
    } catch (err) {
      if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Hata: ${esc(err.message || String(err))}</p>`
    }
  })

  document.querySelector("[data-modal-form='create-product']")?.addEventListener("submit", async (e) => {
    e.preventDefault()
    const form = e.currentTarget
    const body = formBody(form)
    body.user_id = Number(userId) || 1
    if (body.store_id != null) body.store_id = Number(body.store_id)
    const r = form.querySelector("[data-modal-result]")
    if (r) r.innerHTML = '<p style="color:#6b7280; font-size:0.85rem; margin:0.4rem 0 0;">Gönderiliyor…</p>'
    try {
      const resp = await apiPost("/commerce-platform/internal/create-product", body)
      if (r) r.innerHTML = `<p style="color:#16a34a; font-weight:600; margin:0.4rem 0 0;">Ürün eklendi: ID=${esc(resp?.data?.id)}</p>`
      if (state.activeStoreId === body.store_id) await loadItems(state.activeStoreId)
      setTimeout(() => closeModal("create-product"), 400)
    } catch (err) {
      if (r) r.innerHTML = `<p style="color:#dc2626; margin:0.4rem 0 0;">Hata: ${esc(err.message || String(err))}</p>`
    }
  })

  function formBody(form) {
    const fd = new FormData(form)
    const out = {}
    for (const [k, raw] of fd.entries()) {
      const v = String(raw || "").trim()
      if (!v) continue
      const input = form.elements[k]
      out[k] = input && input.type === "number" ? Number(v) : v
    }
    return out
  }

  // ---------- Init ----------
  void loadStores()
})()
