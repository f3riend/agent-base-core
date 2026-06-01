import { appPath, esc } from "./social-media-api.js"
import { socialCreate, socialDelete, socialList, socialPut } from "./social-media-data.js"

const LS_CATEGORIES = "sm_tag_categories_v1"
const LS_VIEW = "sm_tags.view"
const LS_FILTER_CAT = "sm_tags.filterCat"
const MAX_DESC = 300

const TAG_COLORS = [
  { id: "purple", hex: "#8b5cf6", bg: "#f5f3ff" },
  { id: "blue", hex: "#3b82f6", bg: "#eff6ff" },
  { id: "green", hex: "#22c55e", bg: "#f0fdf4" },
  { id: "orange", hex: "#f97316", bg: "#fff7ed" },
  { id: "red", hex: "#ef4444", bg: "#fef2f2" },
  { id: "pink", hex: "#ec4899", bg: "#fdf2f8" },
  { id: "gray", hex: "#64748b", bg: "#f1f5f9" },
  { id: "black", hex: "#0f172a", bg: "#f8fafc" },
]

const DEFAULT_CATEGORIES = [
  { id: "kampanya", name: "Kampanya", color: "#8b5cf6" },
  { id: "urun", name: "Ürün", color: "#3b82f6" },
  { id: "icerik-turu", name: "İçerik Türü", color: "#22c55e" },
  { id: "durum", name: "Durum", color: "#eab308" },
  { id: "platform", name: "Platform", color: "#ec4899" },
]

const state = {
  tickets: [],
  usage: {},
  categories: loadCategories(),
  editingId: null,
  drawerOpen: false,
  drawerMode: "tag",
  draft: { name: "", description: "", color: "purple", categoryId: "kampanya" },
  categoryDraft: { id: "", name: "", color: "#8b5cf6" },
  uiTab: "tags",
  viewMode: localStorage.getItem(LS_VIEW) === "list" ? "list" : "grid",
  filterCat: localStorage.getItem(LS_FILTER_CAT) || "all",
  search: "",
  openMenuId: null,
  status: "",
  loading: false,
  saving: false,
}

function loadCategories() {
  try {
    const raw = localStorage.getItem(LS_CATEGORIES)
    if (!raw) return [...DEFAULT_CATEGORIES]
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed) || !parsed.length) return [...DEFAULT_CATEGORIES]
    return parsed.map((c) => ({
      id: String(c.id || "").trim() || crypto.randomUUID(),
      name: String(c.name || "").trim() || "Kategori",
      color: String(c.color || "#8b5cf6"),
    }))
  } catch {
    return [...DEFAULT_CATEGORIES]
  }
}

function saveCategories() {
  localStorage.setItem(LS_CATEGORIES, JSON.stringify(state.categories))
}

function splitDoc(row) {
  const data = row && typeof row === "object" ? row : {}
  const id = String(row?.id ?? data?.id ?? "")
  return { id, data }
}

function colorMeta(id) {
  return TAG_COLORS.find((c) => c.id === id) || TAG_COLORS[0]
}

function categoryById(id) {
  return state.categories.find((c) => c.id === id) || state.categories[0] || DEFAULT_CATEGORIES[0]
}

function mapTicket(row) {
  const { id, data } = splitDoc(row)
  const color = String(data.color || "purple").trim() || "purple"
  const categoryId = String(data.categoryId || data.category_id || "kampanya").trim() || "kampanya"
  return {
    id,
    name: String(data.name ?? ""),
    description: String(data.description ?? ""),
    color: TAG_COLORS.some((c) => c.id === color) ? color : "purple",
    categoryId: state.categories.some((c) => c.id === categoryId) ? categoryId : state.categories[0]?.id || "kampanya",
  }
}

async function loadUsageCounts() {
  const counts = {}
  const bump = (id) => {
    const k = String(id || "").trim()
    if (!k) return
    counts[k] = (counts[k] || 0) + 1
  }
  const scan = (rows) => {
    for (const row of Array.isArray(rows) ? rows : []) {
      const { data } = splitDoc(row)
      const snap = data.snapshot && typeof data.snapshot === "object" ? data.snapshot : {}
      const tid = data.selectedTicketId ?? snap.selectedTicketId ?? data.ticketId
      bump(tid)
    }
  }
  try {
    const [drafts, posts] = await Promise.all([
      socialList("composer_drafts").catch(() => []),
      socialList("scheduled_posts").catch(() => []),
    ])
    scan(drafts)
    scan(posts)
  } catch {
    /* usage is optional */
  }
  return counts
}

function filteredTickets() {
  const q = state.search.trim().toLowerCase()
  return state.tickets.filter((tk) => {
    if (state.filterCat !== "all" && tk.categoryId !== state.filterCat) return false
    if (!q) return true
    return (
      tk.name.toLowerCase().includes(q) ||
      tk.description.toLowerCase().includes(q) ||
      categoryById(tk.categoryId).name.toLowerCase().includes(q)
    )
  })
}

function countTagsInCategory(catId) {
  return state.tickets.filter((t) => t.categoryId === catId).length
}

async function refresh() {
  state.loading = true
  render()
  try {
    const [rows, usage] = await Promise.all([socialList("tickets"), loadUsageCounts()])
    state.tickets = (Array.isArray(rows) ? rows : []).map(mapTicket)
    state.usage = usage
    state.status = ""
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
  } finally {
    state.loading = false
    render()
  }
}

function openTagDrawer(ticket) {
  state.drawerMode = "tag"
  state.drawerOpen = true
  state.openMenuId = null
  if (ticket) {
    state.editingId = ticket.id
    state.draft = {
      name: ticket.name,
      description: ticket.description,
      color: ticket.color,
      categoryId: ticket.categoryId,
    }
  } else {
    state.editingId = null
    state.draft = {
      name: "",
      description: "",
      color: "purple",
      categoryId: state.filterCat !== "all" ? state.filterCat : state.categories[0]?.id || "kampanya",
    }
  }
  render()
}

function openCategoryDrawer(cat) {
  state.drawerMode = "category"
  state.drawerOpen = true
  state.openMenuId = null
  if (cat) {
    state.categoryDraft = { id: cat.id, name: cat.name, color: cat.color }
  } else {
    state.categoryDraft = { id: "", name: "", color: "#8b5cf6" }
  }
  render()
}

function closeDrawer() {
  state.drawerOpen = false
  state.editingId = null
  state.saving = false
  render()
}

function renderTabs() {
  return `<nav class="sm-tags-tabs" role="tablist">
  <button type="button" role="tab" data-act="tag-tab" data-tab="tags" class="sm-tags-tabs__btn${state.uiTab === "tags" ? " is-active" : ""}">Etiketler</button>
  <button type="button" role="tab" data-act="tag-tab" data-tab="categories" class="sm-tags-tabs__btn${state.uiTab === "categories" ? " is-active" : ""}">Kategoriler</button>
</nav>`
}

function renderCategoryRow() {
  const cards = state.categories
    .map((cat) => {
      const n = countTagsInCategory(cat.id)
      const active = state.filterCat === cat.id ? " is-active" : ""
      return `<button type="button" data-act="tag-filter-cat" data-cat="${esc(cat.id)}" class="sm-tags-cat-card${active}">
  <span class="sm-tags-cat-card__dot" style="background:${esc(cat.color)}"></span>
  <p class="sm-tags-cat-card__title">${esc(cat.name)}</p>
  <p class="sm-tags-cat-card__count">${n} etiket</p>
</button>`
    })
    .join("")
  return `<div class="sm-tags-section-head">
  <div>
    <h2>Kategoriler</h2>
    <p>Etiketlerinizi gruplamak için kategori seçin</p>
  </div>
  <button type="button" class="sm-premium-btn sm-premium-btn--ghost" data-act="tag-manage-categories" style="padding:0.45rem 0.85rem;font-size:0.75rem">⚙ Kategori yönet</button>
</div>
<div class="sm-tags-cat-row">
  <button type="button" data-act="tag-filter-cat" data-cat="all" class="sm-tags-cat-card${state.filterCat === "all" ? " is-active" : ""}">
  <span class="sm-tags-cat-card__dot" style="background:#94a3b8"></span>
  <p class="sm-tags-cat-card__title">Tümü</p>
  <p class="sm-tags-cat-card__count">${state.tickets.length} etiket</p>
</button>
  ${cards}
  <button type="button" data-act="tag-add-category" class="sm-tags-cat-card sm-tags-cat-card--add">+ Kategori ekle</button>
</div>`
}

function renderToolbar(total) {
  const catOptions = `<option value="all"${state.filterCat === "all" ? " selected" : ""}>Tümü</option>${state.categories
    .map((c) => `<option value="${esc(c.id)}"${state.filterCat === c.id ? " selected" : ""}>${esc(c.name)}</option>`)
    .join("")}`
  return `<div class="sm-tags-toolbar">
  <div class="sm-tags-toolbar__meta">
    <strong>Etiketler</strong>
    <span>Toplam ${total} etiket</span>
  </div>
  <select class="sm-premium-select sm-tags-filter-select" data-act="tag-filter-select" aria-label="Kategori">${catOptions}</select>
  <input type="search" class="sm-tags-search" data-act="tag-search" value="${esc(state.search)}" placeholder="Etiket ara…" aria-label="Etiket ara"/>
  <div class="sm-premium-view-toggle" role="group" aria-label="Görünüm">
    <button type="button" data-act="tag-view-mode" data-mode="grid" class="sm-premium-view-toggle__btn${state.viewMode === "grid" ? " is-active" : ""}" title="Kart">⊞</button>
    <button type="button" data-act="tag-view-mode" data-mode="list" class="sm-premium-view-toggle__btn${state.viewMode === "list" ? " is-active" : ""}" title="Liste">≡</button>
  </div>
</div>`
}

function renderTagCard(tk) {
  const cm = colorMeta(tk.color)
  const cat = categoryById(tk.categoryId)
  const usage = state.usage[tk.id] || 0
  const menuOpen = state.openMenuId === tk.id
  const pillBg = `${cat.color}18`
  const listCls = state.viewMode === "list" ? " sm-tags-card--list" : ""

  return `<article class="sm-tags-card${listCls}" data-tag-id="${esc(tk.id)}">
  <div class="sm-tags-card__top">
    <span class="sm-tags-card__dot" style="background:${esc(cm.hex)}"></span>
    <h3 class="sm-tags-card__title">${esc(tk.name || "İsimsiz etiket")}</h3>
    <div class="sm-tags-card__menu-wrap">
      <button type="button" class="sm-tags-card__menu-btn" data-act="tag-menu-toggle" data-id="${esc(tk.id)}" aria-label="Menü">⋯</button>
      ${
        menuOpen
          ? `<div class="sm-tags-card__dropdown" data-stop="1">
        <button type="button" data-act="tag-edit" data-id="${esc(tk.id)}">Düzenle</button>
        <button type="button" class="is-danger" data-act="tag-del" data-id="${esc(tk.id)}">Sil</button>
      </div>`
          : ""
      }
    </div>
  </div>
  <span class="sm-tags-card__pill" style="color:${esc(cat.color)};background:${esc(pillBg)}">${esc(cat.name)}</span>
  <p class="sm-tags-card__desc">${esc(tk.description || "Açıklama eklenmemiş.")}</p>
  <p class="sm-tags-card__usage">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>
    ${usage} içerikte kullanıldı
  </p>
</article>`
}

function renderTagsPanel() {
  const items = filteredTickets()
  const gridCls = state.viewMode === "list" ? "sm-tags-grid sm-tags-grid--list" : "sm-tags-grid"
  const cards =
    items.length === 0
      ? `<p class="sm-premium-empty">Henüz etiket yok veya filtreye uygun sonuç bulunamadı.</p>`
      : `<div class="${gridCls}">${items.map(renderTagCard).join("")}</div>`
  return `${renderCategoryRow()}${renderToolbar(items.length)}${cards}`
}

function renderCategoriesPanel() {
  const rows = state.categories
    .map((cat) => {
      const n = countTagsInCategory(cat.id)
      return `<div class="sm-tags-cat-manage">
  <div class="flex items-center gap-3 min-w-0">
    <span class="sm-tags-cat-card__dot" style="background:${esc(cat.color)};margin:0"></span>
    <div class="min-w-0">
      <p class="font-semibold text-neutral-900 m-0">${esc(cat.name)}</p>
      <p class="text-xs text-neutral-500 m-0 mt-0.5">${n} etiket</p>
    </div>
  </div>
  <div class="flex gap-1 shrink-0">
    <button type="button" data-act="tag-edit-category" data-id="${esc(cat.id)}" class="sm-premium-btn sm-premium-btn--ghost" style="padding:0.4rem 0.75rem;font-size:0.75rem">Düzenle</button>
    <button type="button" data-act="tag-del-category" data-id="${esc(cat.id)}" class="sm-premium-btn sm-premium-btn--danger-outline" style="padding:0.4rem 0.75rem;font-size:0.75rem">Sil</button>
  </div>
</div>`
    })
    .join("")
  return `<div class="sm-tags-section-head">
  <div>
    <h2>Kategori yönetimi</h2>
    <p>Etiket gruplarınızı düzenleyin veya yeni kategori ekleyin</p>
  </div>
  <button type="button" class="sm-premium-btn sm-premium-btn--primary" data-act="tag-add-category">+ Kategori ekle</button>
</div>
<div class="sm-tags-categories-panel">${rows || `<p class="sm-premium-empty">Henüz kategori yok.</p>`}</div>`
}

function renderPreview() {
  const cm = colorMeta(state.draft.color)
  const cat = categoryById(state.draft.categoryId)
  const title = state.draft.name.trim() || "Etiket Adı"
  const desc = state.draft.description.trim() || "Etiket açıklaması burada görünecek."
  return `<div class="sm-tags-preview">
  <p class="sm-tags-preview__label">Ön izleme</p>
  <div class="sm-tags-preview__card" style="--sm-tag-preview-bg:${esc(cm.bg)};--sm-tag-preview-accent:${esc(cm.hex)}">
    <div class="sm-tags-preview__icon">🏷</div>
    <div>
      <span class="sm-tags-preview__pill">${esc(title)}</span>
      <p class="sm-tags-preview__sub">${esc(cat.name)} · ${esc(desc)}</p>
    </div>
  </div>
</div>`
}

function renderTagDrawer() {
  const isEdit = Boolean(state.editingId)
  const dis = state.saving ? "disabled" : ""
  const descLen = state.draft.description.length
  const catOptions = state.categories
    .map((c) => `<option value="${esc(c.id)}"${state.draft.categoryId === c.id ? " selected" : ""}>${esc(c.name)}</option>`)
    .join("")
  const colorBtns = TAG_COLORS.map((c) => {
    const active = state.draft.color === c.id
    return `<button type="button" data-act="tag-pick-color" data-color="${esc(c.id)}" class="sm-tags-color-btn${active ? " is-active" : ""}" style="background:${esc(c.hex)};color:${esc(c.hex)}" aria-label="${esc(c.id)}" aria-pressed="${active}">
      ${active ? '<svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"/></svg>' : ""}
    </button>`
  }).join("")

  return `<div class="sm-tags-drawer-root" data-act="tag-close-drawer-bg">
  <aside class="sm-tags-drawer" data-stop="1" role="dialog" aria-modal="true" aria-labelledby="sm-tag-drawer-title">
    <header class="sm-tags-drawer__header">
      <div>
        <h2 id="sm-tag-drawer-title" class="sm-tags-drawer__title">${isEdit ? "Etiketi düzenle" : "Yeni etiket"}</h2>
        <p class="sm-tags-drawer__desc">Takviminizde içerik oluştururken etiketleri kullanabilirsiniz.</p>
      </div>
      <button type="button" class="sm-tags-drawer__close" data-act="tag-close-drawer" aria-label="Kapat">✕</button>
    </header>
    <form id="sm-tag-form" data-act="tag-form" class="sm-tags-drawer__body">
      <div class="sm-tags-field">
        <label for="tag-name">Etiket adı</label>
        <input id="tag-name" class="sm-tags-input" value="${esc(state.draft.name)}" placeholder="Örn. Kampanya" required ${dis}/>
      </div>
      <div class="sm-tags-field">
        <label for="tag-desc">Açıklama (opsiyonel)</label>
        <p class="sm-tags-field__hint">Bu etiketin ne için kullanılacağını açıklayın.</p>
        <textarea id="tag-desc" class="sm-tags-textarea" maxlength="${MAX_DESC}" placeholder="Bu etiketin ne için kullanılacağını açıklayın…" ${dis}>${esc(state.draft.description)}</textarea>
        <p class="sm-tags-char-count"><span data-tag-desc-count>${descLen}</span>/${MAX_DESC}</p>
      </div>
      <div class="sm-tags-field">
        <label for="tag-category">Kategori</label>
        <select id="tag-category" class="sm-tags-select" ${dis}>${catOptions}</select>
      </div>
      <div class="sm-tags-field">
        <label>Renk seçimi</label>
        <p class="sm-tags-field__hint">Etiketinizi temsil edecek bir renk seçin.</p>
        <div class="sm-tags-colors">${colorBtns}</div>
      </div>
      ${renderPreview()}
    </form>
    <footer class="sm-tags-drawer__footer">
      <button type="button" class="sm-tags-btn-delete" data-act="tag-del-drawer" ${isEdit ? "" : "disabled"} ${dis}>Sil</button>
      <div class="sm-tags-drawer__footer-right">
        <button type="button" class="sm-premium-btn sm-premium-btn--ghost" data-act="tag-close-drawer" ${dis}>İptal</button>
        <button type="submit" form="sm-tag-form" class="sm-premium-btn sm-premium-btn--primary" ${dis}>Kaydet</button>
      </div>
    </footer>
  </aside>
</div>`
}

function renderCategoryDrawer() {
  const isEdit = Boolean(state.categoryDraft.id)
  const dis = state.saving ? "disabled" : ""
  return `<div class="sm-tags-drawer-root" data-act="tag-close-drawer-bg">
  <aside class="sm-tags-drawer" data-stop="1" role="dialog" aria-modal="true">
    <header class="sm-tags-drawer__header">
      <div>
        <h2 class="sm-tags-drawer__title">${isEdit ? "Kategoriyi düzenle" : "Yeni kategori"}</h2>
        <p class="sm-tags-drawer__desc">Etiketlerinizi gruplamak için kategori oluşturun.</p>
      </div>
      <button type="button" class="sm-tags-drawer__close" data-act="tag-close-drawer" aria-label="Kapat">✕</button>
    </header>
    <form id="sm-tag-cat-form" data-act="tag-category-form" class="sm-tags-drawer__body">
      <div class="sm-tags-field">
        <label for="tag-cat-name">Kategori adı</label>
        <input id="tag-cat-name" class="sm-tags-input" value="${esc(state.categoryDraft.name)}" placeholder="Örn. Kampanya" required ${dis}/>
      </div>
      <div class="sm-tags-field">
        <label for="tag-cat-color">Renk</label>
        <input id="tag-cat-color" type="color" class="sm-tags-input" value="${esc(state.categoryDraft.color)}" ${dis}/>
      </div>
    </form>
    <footer class="sm-tags-drawer__footer">
      <span></span>
      <div class="sm-tags-drawer__footer-right">
        <button type="button" class="sm-premium-btn sm-premium-btn--ghost" data-act="tag-close-drawer" ${dis}>İptal</button>
        <button type="submit" form="sm-tag-cat-form" class="sm-premium-btn sm-premium-btn--primary" ${dis}>Kaydet</button>
      </div>
    </footer>
  </aside>
</div>`
}

function renderDrawer() {
  if (!state.drawerOpen) return ""
  return state.drawerMode === "category" ? renderCategoryDrawer() : renderTagDrawer()
}

function render() {
  const root = document.getElementById("sm-tags-root")
  if (!root) return
  const panel = state.uiTab === "categories" ? renderCategoriesPanel() : renderTagsPanel()
  root.innerHTML = `
${state.status ? `<p class="mb-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">${esc(state.status)}</p>` : ""}
${state.loading ? `<p class="mb-4 text-sm text-neutral-500">Yükleniyor…</p>` : ""}
${renderTabs()}
${panel}
<p class="mt-8 text-xs text-neutral-500"><a href="${esc(appPath("/social-media"))}" class="font-medium text-emerald-800 hover:underline">← Takvime dön</a></p>`
  document.querySelectorAll(".sm-tags-drawer-root").forEach((el) => el.remove())
  if (state.drawerOpen) {
    document.body.insertAdjacentHTML("beforeend", renderDrawer())
    document.body.classList.add("sm-tags-drawer-open")
  } else {
    document.body.classList.remove("sm-tags-drawer-open")
  }
}

function readTagFormFromDom() {
  const name = document.getElementById("tag-name")?.value?.trim() || ""
  const description = (document.getElementById("tag-desc")?.value || "").slice(0, MAX_DESC).trim()
  const categoryId = document.getElementById("tag-category")?.value || state.draft.categoryId
  return { name, description, color: state.draft.color, categoryId }
}

async function saveTag() {
  const body = readTagFormFromDom()
  if (!body.name) return
  state.saving = true
  render()
  try {
    const payload = {
      name: body.name,
      description: body.description,
      color: body.color,
      categoryId: body.categoryId,
    }
    if (state.editingId) await socialPut("tickets", state.editingId, payload, true)
    else await socialCreate("tickets", { ...payload, createdAt: new Date().toISOString() })
    closeDrawer()
    await refresh()
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
    state.saving = false
    render()
  }
}

async function deleteTag(id) {
  if (!window.confirm("Bu etiket silinsin mi?")) return
  try {
    await socialDelete("tickets", id)
    if (state.editingId === id) closeDrawer()
    await refresh()
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
    render()
  }
}

function saveCategoryFromDom() {
  const name = document.getElementById("tag-cat-name")?.value?.trim() || ""
  const color = document.getElementById("tag-cat-color")?.value || "#8b5cf6"
  if (!name) return
  if (state.categoryDraft.id) {
    const idx = state.categories.findIndex((c) => c.id === state.categoryDraft.id)
    if (idx >= 0) state.categories[idx] = { ...state.categories[idx], name, color }
  } else {
    const id = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 40) || `cat-${Date.now()}`
    if (state.categories.some((c) => c.id === id)) {
      state.categories.push({ id: `${id}-${Date.now()}`, name, color })
    } else {
      state.categories.push({ id, name, color })
    }
  }
  saveCategories()
  closeDrawer()
  render()
}

function deleteCategory(id) {
  const cat = state.categories.find((c) => c.id === id)
  if (!cat) return
  const n = countTagsInCategory(id)
  if (n > 0 && !window.confirm(`"${cat.name}" kategorisinde ${n} etiket var. Silmek istediğinize emin misiniz?`)) return
  if (n === 0 && !window.confirm(`"${cat.name}" kategorisi silinsin mi?`)) return
  state.categories = state.categories.filter((c) => c.id !== id)
  if (state.filterCat === id) state.filterCat = "all"
  saveCategories()
  render()
}

document.addEventListener("submit", async (e) => {
  const form = e.target instanceof HTMLFormElement ? e.target : null
  if (!form) return
  if (form.matches("[data-act='tag-form']")) {
    e.preventDefault()
    await saveTag()
    return
  }
  if (form.matches("[data-act='tag-category-form']")) {
    e.preventDefault()
    saveCategoryFromDom()
  }
})

document.addEventListener("click", async (e) => {
  const t = e.target instanceof Element ? e.target.closest("[data-act]") : null
  if (!t) {
    if (state.openMenuId) {
      state.openMenuId = null
      render()
    }
    return
  }
  if (t.closest("[data-stop]")) {
    e.stopPropagation()
  }
  const act = t.getAttribute("data-act")

  if (act === "tag-open-create" || act === "tag-create") {
    e.preventDefault()
    state.uiTab = "tags"
    openTagDrawer(null)
    return
  }
  if (act === "tag-close-drawer" || act === "tag-close-drawer-bg") {
    if (act === "tag-close-drawer-bg" && t.closest("[data-stop]")) return
    closeDrawer()
    return
  }
  if (act === "tag-tab") {
    const tab = t.getAttribute("data-tab")
    if (tab === "tags" || tab === "categories") {
      state.uiTab = tab
      state.openMenuId = null
      render()
    }
    return
  }
  if (act === "tag-filter-cat") {
    const cat = t.getAttribute("data-cat") || "all"
    state.filterCat = cat
    localStorage.setItem(LS_FILTER_CAT, cat)
    render()
    return
  }
  if (act === "tag-view-mode") {
    const mode = t.getAttribute("data-mode")
    if (mode === "grid" || mode === "list") {
      state.viewMode = mode
      localStorage.setItem(LS_VIEW, mode)
      render()
    }
    return
  }
  if (act === "tag-pick-color") {
    const color = t.getAttribute("data-color")
    if (color && TAG_COLORS.some((c) => c.id === color)) {
      state.draft.color = color
      render()
    }
    return
  }
  if (act === "tag-menu-toggle") {
    const id = t.getAttribute("data-id") || ""
    state.openMenuId = state.openMenuId === id ? null : id
    render()
    e.stopPropagation()
    return
  }
  if (act === "tag-edit" && t.getAttribute("data-id")) {
    const tk = state.tickets.find((x) => x.id === t.getAttribute("data-id"))
    if (tk) openTagDrawer(tk)
    return
  }
  if (act === "tag-del" && t.getAttribute("data-id")) {
    await deleteTag(t.getAttribute("data-id") || "")
    return
  }
  if (act === "tag-del-drawer" && state.editingId) {
    await deleteTag(state.editingId)
    return
  }
  if (act === "tag-manage-categories") {
    state.uiTab = "categories"
    render()
    return
  }
  if (act === "tag-add-category") {
    openCategoryDrawer(null)
    return
  }
  if (act === "tag-edit-category") {
    const cat = state.categories.find((c) => c.id === t.getAttribute("data-id"))
    if (cat) openCategoryDrawer(cat)
    return
  }
  if (act === "tag-del-category") {
    deleteCategory(t.getAttribute("data-id") || "")
    return
  }
  if (state.openMenuId && !t.closest(".sm-tags-card__menu-wrap")) {
    state.openMenuId = null
    render()
  }
})

document.addEventListener("input", (e) => {
  const t = e.target
  if (!(t instanceof HTMLElement)) return
  if (t.id === "tag-desc") {
    const len = String(t.value || "").length
    const el = document.querySelector("[data-tag-desc-count]")
    if (el) el.textContent = String(len)
    state.draft.description = String(t.value || "").slice(0, MAX_DESC)
  }
  if (t.id === "tag-name") state.draft.name = t.value
  if (t.id === "tag-category") state.draft.categoryId = t.value
  if (t.matches("[data-act='tag-search']")) {
    state.search = t.value
    render()
    return
  }
  if (t.id === "tag-category" || t.id === "tag-name" || t.id === "tag-desc") {
    const previewRoot = document.querySelector(".sm-tags-drawer__body")
    if (previewRoot && state.drawerOpen && state.drawerMode === "tag") {
      const preview = renderPreview()
      const old = previewRoot.querySelector(".sm-tags-preview")
      if (old) old.outerHTML = preview
    }
  }
})

document.addEventListener("change", (e) => {
  const t = e.target
  if (!(t instanceof HTMLElement)) return
  if (t.matches("[data-act='tag-filter-select']")) {
    state.filterCat = t.value || "all"
    localStorage.setItem(LS_FILTER_CAT, state.filterCat)
    render()
  }
})

void refresh()
