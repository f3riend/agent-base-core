import { apiBase, appPath, esc, T } from "./social-media-api.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { CAMPAIGN_USER_TEMPLATES, USER_TEMPLATES } from "./social-media-constants.js"
import {
  deleteStorageImages,
  isManagedUploadStorageUrl,
  socialCreate,
  socialDelete,
  socialList,
  socialPatchFields,
  TS,
} from "./social-media-data.js"

const userCollection = CAMPAIGN_MODE ? CAMPAIGN_USER_TEMPLATES : USER_TEMPLATES

const state = {
  templates: [],
  editingId: null,
  draft: { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" },
  uploading: false,
  status: "",
  loading: false,
  uiTab: "post",
  viewMode: "grid",
  editorOpen: false,
}

function sizeMeta(outputSize) {
  const k = String(outputSize || "post_4_5")
  if (k === "story") return { label: "Hikaye (9:16)", dim: "1088 × 1920 px" }
  if (k === "square") return { label: "Kare (1:1)", dim: "1024 × 1024 px" }
  return { label: "Gönderi (4:5)", dim: "1088 × 1360 px" }
}

function filteredTemplates() {
  if (CAMPAIGN_MODE) return state.templates
  if (state.uiTab === "story") return state.templates.filter((t) => String(t.outputSize || "") === "story")
  return state.templates.filter((t) => String(t.outputSize || "post_4_5") !== "story")
}

async function uploadFiles(files) {
  const list = Array.from(files || [])
  const out = []
  for (const file of list) {
    const fd = new FormData()
    fd.append("file", file)
    const res = await fetch(apiBase() + "/social-media/image/upload", { method: "POST", body: fd })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || "upload")
    const url = String(data.url || "").trim()
    if (url) out.push(url)
  }
  return out
}

function mapTemplate(row) {
  const data = row && typeof row === "object" ? row : {}
  const id = String(row?.id ?? data?.id ?? "")
  const urls = Array.isArray(data.imageUrls) ? data.imageUrls.map((u) => String(u ?? "").trim()).filter(Boolean) : []
  return {
    id,
    title: String(data.title ?? data.name ?? "").trim(),
    prompt: String(data.prompt ?? data.description ?? "").trim(),
    imageUrls: urls,
    outputSize: String(data.outputSize ?? "post_4_5"),
  }
}

async function refresh() {
  state.loading = true
  render()
  try {
    const rows = await socialList(userCollection)
    state.templates = (Array.isArray(rows) ? rows : []).map(mapTemplate)
    state.status = ""
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
  } finally {
    state.loading = false
    render()
  }
}

function setPageCopy() {
  const titleEl = document.querySelector("[data-sm-templates-title]")
  const introEl = document.querySelector("[data-sm-templates-intro]")
  if (titleEl) titleEl.textContent = CAMPAIGN_MODE ? "Kampanya Şablonları" : "Şablonlar"
  if (introEl) {
    introEl.textContent = CAMPAIGN_MODE
      ? "Banner üretiminde kullanılacak layout, logo ve AI talimat şablonlarını yönetin."
      : "Sosyal medya paylaşımlarınız için hazır şablonları keşfedin."
  }
}

function renderTabs() {
  if (CAMPAIGN_MODE) return ""
  return `<nav class="sm-premium-tabs" role="tablist">
  <button type="button" role="tab" data-act="tpl-tab" data-tab="post" class="sm-premium-tabs__btn${state.uiTab === "post" ? " is-active" : ""}">Post Şablonları</button>
  <button type="button" role="tab" data-act="tpl-tab" data-tab="story" class="sm-premium-tabs__btn${state.uiTab === "story" ? " is-active" : ""}">Hikaye Şablonları</button>
</nav>`
}

function renderToolbar() {
  return `<div class="sm-premium-toolbar">
  <select class="sm-premium-select" aria-label="Filtre" disabled>
    <option>Tümü</option>
  </select>
  <div class="sm-premium-view-toggle" role="group" aria-label="Görünüm">
    <button type="button" data-act="tpl-view-mode" data-mode="grid" class="sm-premium-view-toggle__btn${state.viewMode === "grid" ? " is-active" : ""}" title="Kart">⊞</button>
    <button type="button" data-act="tpl-view-mode" data-mode="list" class="sm-premium-view-toggle__btn${state.viewMode === "list" ? " is-active" : ""}" title="Liste">≡</button>
  </div>
</div>`
}

function renderTemplateCard(tpl) {
  const layout = String(tpl.imageUrls[0] || "").trim()
  const meta = sizeMeta(tpl.outputSize)
  const mediaCls = CAMPAIGN_MODE ? "sm-premium-card__media sm-premium-card__media--banner" : "sm-premium-card__media"
  const listBody =
    state.viewMode === "list"
      ? `<div class="sm-premium-card__body" style="flex:1">
    <div>
      <h3 class="sm-premium-card__title">${esc(tpl.title || "İsimsiz")}</h3>
      <p class="sm-premium-card__meta">${esc(meta.dim)}</p>
    </div>
    <span class="flex gap-1">
      <button type="button" data-act="tpl-edit" data-id="${esc(tpl.id)}" class="sm-premium-btn sm-premium-btn--ghost" style="padding:0.35rem 0.65rem;font-size:0.7rem">Düzenle</button>
      <button type="button" data-act="tpl-del" data-id="${esc(tpl.id)}" class="sm-premium-btn sm-premium-btn--danger-outline" style="padding:0.35rem 0.65rem;font-size:0.7rem">Sil</button>
    </span>
  </div>`
      : `<div class="sm-premium-card__body">
    <div>
      <h3 class="sm-premium-card__title">${esc(tpl.title || "İsimsiz")}</h3>
      <p class="sm-premium-card__meta">${esc(meta.dim)}</p>
    </div>
    <button type="button" class="sm-premium-card__menu" data-act="tpl-edit" data-id="${esc(tpl.id)}" aria-label="Düzenle">⋯</button>
  </div>`

  const cardStyle = state.viewMode === "list" ? ' style="display:flex;align-items:stretch"' : ""
  const mediaStyle = state.viewMode === "list" ? ' style="width:120px;flex-shrink:0;aspect-ratio:auto;min-height:80px"' : ""

  return `<article class="sm-premium-card"${cardStyle}>
  <div class="${mediaCls}"${mediaStyle}>
    ${
      layout
        ? `<img src="${esc(layout)}" alt=""/>`
        : `<div style="display:flex;height:100%;align-items:center;justify-content:center;color:#94a3b8;font-size:0.75rem">Önizleme yok</div>`
    }
  </div>
  ${listBody}
</article>`
}

function renderGrid() {
  const items = filteredTemplates()
  const gridCls = state.viewMode === "list" ? "sm-premium-grid sm-premium-grid--list" : "sm-premium-grid"
  const cards = items.map(renderTemplateCard).join("")
  const createCard = `<button type="button" data-act="tpl-open-create" class="sm-premium-card sm-premium-card--create">
  <span class="sm-premium-card--create__icon">+</span>
  <span class="sm-premium-card--create__label">Yeni şablon oluştur</span>
  <span class="sm-premium-card--create__hint">Görsel yükleyerek veya şablon seçerek başlayın</span>
</button>`
  if (!items.length) {
    return `<div class="${gridCls}">${createCard}</div>`
  }
  return `<div class="${gridCls}">${cards}${createCard}</div>`
}

function renderSizePicker() {
  if (CAMPAIGN_MODE) return ""
  const sizes = [
    { id: "post_4_5", label: "Gönderi (4:5)", dim: "1088 × 1360 px" },
    { id: "square", label: "Kare (1:1)", dim: "1024 × 1024 px" },
    { id: "story", label: "Hikaye (9:16)", dim: "1088 × 1920 px" },
  ]
  return `<div class="sm-premium-field">
  <label>Çıktı boyutu</label>
  <select id="tpl-output-size" class="sm-premium-hidden-select" aria-hidden="true" tabindex="-1">
    <option value="square" ${state.draft.outputSize === "square" ? "selected" : ""}>square</option>
    <option value="post_4_5" ${state.draft.outputSize === "post_4_5" ? "selected" : ""}>post_4_5</option>
    <option value="story" ${state.draft.outputSize === "story" ? "selected" : ""}>story</option>
  </select>
  <div class="sm-premium-size-grid">
    ${sizes
      .map((s) => {
        const icon = s.id === "square" ? "□" : s.id === "story" ? "▯" : "▭"
        return `<button type="button" data-act="tpl-set-size" data-size="${esc(s.id)}" class="sm-premium-size-card${state.draft.outputSize === s.id ? " is-active" : ""}">
      <div class="sm-premium-size-card__icon">${icon}</div>
      <div class="sm-premium-size-card__label">${esc(s.label)}</div>
      <div class="sm-premium-size-card__dim">${esc(s.dim)}</div>
    </button>`
      })
      .join("")}
  </div>
</div>`
}

function renderEditorModal() {
  if (!state.editorOpen) return ""
  const layoutU = String(state.draft.imageUrls[0] || "").trim()
  const logoU = String(state.draft.imageUrls[1] || "").trim()
  const busy = state.uploading
  const dis = busy ? "disabled" : ""

  const campaignVisual = CAMPAIGN_MODE
    ? `<div class="sm-premium-modal__split" style="grid-template-columns:1fr">
  <div class="grid gap-3 sm:grid-cols-2">
    <div class="sm-premium-upload-zone">
      <p class="text-xs font-semibold text-neutral-800">Layout görseli</p>
      <input id="tpl-file-layout" type="file" accept="image/*" class="hidden"/>
      ${layoutU ? `<img src="${esc(layoutU)}" alt="" class="mx-auto mt-2 max-h-28 rounded-lg border object-contain"/>` : `<p class="mt-2 text-xs text-neutral-500">Henüz yok</p>`}
      <button type="button" data-act="tpl-pick-layout" class="sm-premium-btn sm-premium-btn--ghost mt-2" ${dis}>Dosya seç</button>
    </div>
    <div class="sm-premium-upload-zone" style="background:#faf5ff;border-color:#e9d5ff">
      <p class="text-xs font-semibold text-neutral-800">Logo (isteğe bağlı)</p>
      <input id="tpl-file-logo" type="file" accept="image/*" class="hidden"/>
      ${logoU ? `<img src="${esc(logoU)}" alt="" class="mx-auto mt-2 max-h-20 rounded-lg border object-contain"/>` : `<p class="mt-2 text-xs text-neutral-500">Henüz yok</p>`}
      <button type="button" data-act="tpl-pick-logo" class="sm-premium-btn sm-premium-btn--ghost mt-2" ${dis} ${layoutU ? "" : "disabled"}>Logo yükle</button>
    </div>
  </div>
</div>`
    : ""

  return `<div class="sm-premium-modal-root" data-act="tpl-close-editor-bg">
  <div class="sm-premium-modal sm-premium-modal--editor" data-stop="1" role="dialog" aria-modal="true">
    <header class="sm-premium-modal__header">
      <div>
        <h2 class="sm-premium-modal__title">İçerik şablonları</h2>
        <p class="sm-premium-modal__desc">Şablon adı ve AI talimatını düzenleyin. Görseller revize akışında referans olarak kullanılır.</p>
      </div>
      <button type="button" class="sm-premium-modal__close" data-act="tpl-close-editor" aria-label="Kapat">✕</button>
    </header>
    <form data-act="tpl-form" class="sm-premium-modal__body">
      <div class="sm-premium-modal__split sm-premium-modal__split--balanced">
        <aside class="sm-premium-modal__preview-col">
          <span class="sm-premium-kicker">Şablon önizlemesi</span>
          <input id="tpl-file-layout" type="file" accept="image/*" class="hidden"/>
          <div data-act="tpl-drop-layout" class="sm-premium-upload-drop">
            ${
              layoutU
                ? `<div class="group relative"><img src="${esc(layoutU)}" alt="" class="mx-auto max-h-36 w-full rounded-xl border border-slate-200 object-contain"/>
            <button type="button" data-act="tpl-pick-layout" class="sm-premium-btn sm-premium-btn--ghost mt-2 w-full" style="padding:0.45rem;font-size:0.75rem" ${dis}>Görseli değiştir</button></div>`
                : `<div class="sm-premium-upload-drop__icon">↑</div>
            <p class="sm-premium-upload-drop__label">Dosya seç veya sürükle</p>
            <p class="sm-premium-upload-drop__hint">Ctrl+V ile yapıştırabilirsiniz</p>
            <button type="button" data-act="tpl-pick-layout" class="sm-premium-btn sm-premium-btn--ghost mt-2 w-full" style="padding:0.45rem;font-size:0.75rem" ${dis}>Dosya seç</button>`
            }
          </div>
          ${CAMPAIGN_MODE ? campaignVisual : ""}
        </aside>
        <div class="sm-premium-modal__form-col">
          <div class="sm-premium-field">
            <label for="tpl-title">Şablon adı</label>
            <input id="tpl-title" class="sm-premium-input" value="${esc(state.draft.title)}" placeholder="Örn. Minimal Konfor" required />
          </div>
          <div class="sm-premium-field">
            <label for="tpl-prompt">AI talimatı / revize metni</label>
            <div class="sm-premium-editor">
              <div class="sm-premium-editor__toolbar">
                <div class="sm-premium-editor__tools"><span>B</span><span>I</span><span>U</span><span>≡</span><span>•</span><span>🔗</span></div>
                <span class="sm-premium-editor__ai">✦ AI</span>
              </div>
              <textarea id="tpl-prompt" class="sm-premium-editor__area" rows="7" placeholder="Şablon kullanıldığında AI'ya iletilecek talimat…" required>${esc(state.draft.prompt)}</textarea>
            </div>
          </div>
          ${renderSizePicker()}
        </div>
      </div>
      <footer class="sm-premium-modal__footer">
        <button type="button" class="sm-premium-btn sm-premium-btn--ghost" data-act="tpl-cancel">İptal</button>
        <button type="submit" class="sm-premium-btn sm-premium-btn--primary" ${dis}>Kaydet</button>
      </footer>
    </form>
  </div>
</div>`
}

function render() {
  const root = document.getElementById("sm-templates-root")
  if (!root) return
  setPageCopy()

  root.innerHTML = `
${state.status ? `<p class="mb-4 rounded-xl border px-3 py-2 text-sm ${state.status.startsWith("✓") ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-red-200 bg-red-50 text-red-800"}">${esc(state.status)}</p>` : ""}
${state.loading ? `<p class="mb-4 text-sm text-neutral-500">Yükleniyor…</p>` : ""}
${renderTabs()}
${renderToolbar()}
${renderGrid()}
<p class="mt-8 text-xs text-neutral-500"><a href="${esc(appPath(CAMPAIGN_MODE ? "/campaign-management" : "/social-media"))}" class="font-medium text-emerald-800 hover:underline">← Takvime dön</a></p>
${renderEditorModal()}`
}

document.addEventListener("submit", async (e) => {
  const form = e.target instanceof HTMLFormElement ? e.target : null
  if (!form || !form.matches("[data-act='tpl-form']")) return
  e.preventDefault()
  const title = document.getElementById("tpl-title")?.value?.trim() || ""
  const prompt = document.getElementById("tpl-prompt")?.value?.trim() || ""
  const outputSizeEl = document.getElementById("tpl-output-size")
  const outputSize =
    outputSizeEl instanceof HTMLSelectElement ? outputSizeEl.value : state.draft.outputSize
  const layout = String(state.draft.imageUrls[0] || "").trim()
  if (CAMPAIGN_MODE && !layout) {
    state.status = T("tplCampaignNeedLayoutSave") || "Layout görseli gerekli."
    render()
    return
  }
  if (!title || !prompt) return
  const logo = String(state.draft.imageUrls[1] || "").trim()
  const tail = state.draft.imageUrls.slice(2).map((u) => String(u || "").trim()).filter(Boolean)
  const imageUrls = layout ? [layout, ...(logo ? [logo] : []), ...tail] : [...state.draft.imageUrls]
  try {
    const base = { title, prompt, imageUrls, outputSize, updatedAt: TS }
    if (state.editingId) await socialPatchFields(userCollection, state.editingId, base)
    else await socialCreate(userCollection, { ...base, createdAt: new Date().toISOString() })
    state.editingId = null
    state.draft = { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" }
    state.editorOpen = false
    state.status = "✓ Kaydedildi"
    await refresh()
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
    render()
  }
})

document.addEventListener("click", async (e) => {
  const t = e.target instanceof Element ? e.target.closest("[data-act]") : null
  if (!t) return
  const act = t.getAttribute("data-act")
  const id = t.getAttribute("data-id") || ""

  if (act === "tpl-tab") {
    const tab = t.getAttribute("data-tab")
    if (tab === "post" || tab === "story") {
      state.uiTab = tab
      render()
    }
    return
  }
  if (act === "tpl-view-mode") {
    const mode = t.getAttribute("data-mode")
    if (mode === "grid" || mode === "list") {
      state.viewMode = mode
      render()
    }
    return
  }
  if (act === "tpl-open-create") {
    state.editingId = null
    state.draft = { title: "", prompt: "", imageUrls: [], outputSize: state.uiTab === "story" ? "story" : "post_4_5" }
    state.editorOpen = true
    render()
    return
  }
  if (act === "tpl-close-editor" || act === "tpl-close-editor-bg") {
    if (act === "tpl-close-editor-bg" && t.closest("[data-stop]")) return
    state.editingId = null
    state.draft = { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" }
    state.editorOpen = false
    render()
    return
  }
  if (act === "tpl-set-size") {
    const size = t.getAttribute("data-size")
    if (size === "square" || size === "post_4_5" || size === "story") {
      state.draft.outputSize = size
      const sel = document.getElementById("tpl-output-size")
      if (sel instanceof HTMLSelectElement) sel.value = size
      render()
    }
    return
  }
  if (act === "tpl-pick-layout") {
    document.getElementById("tpl-file-layout")?.click()
    return
  }
  if (act === "tpl-pick-logo") {
    document.getElementById("tpl-file-logo")?.click()
    return
  }
  if (act === "tpl-edit" && id) {
    const tpl = state.templates.find((x) => x.id === id)
    if (!tpl) return
    state.editingId = id
    state.draft = {
      title: tpl.title,
      prompt: tpl.prompt,
      imageUrls: [...tpl.imageUrls],
      outputSize: tpl.outputSize,
    }
    state.editorOpen = true
    render()
    return
  }
  if (act === "tpl-cancel") {
    state.editingId = null
    state.draft = { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" }
    state.editorOpen = false
    render()
    return
  }
  if (act === "tpl-del" && id) {
    if (!window.confirm(T("composerTemplatesDeleteConfirm") || "Silinsin mi?")) return
    try {
      const tpl = state.templates.find((x) => x.id === id)
      if (tpl?.imageUrls?.length) {
        await deleteStorageImages(tpl.imageUrls.filter((u) => isManagedUploadStorageUrl(u)))
      }
      await socialDelete(userCollection, id)
      await refresh()
    } catch (err) {
      state.status = err instanceof Error ? err.message : String(err)
      render()
    }
  }
})

document.addEventListener("change", async (e) => {
  const input = e.target instanceof HTMLInputElement ? e.target : null
  if (!input || input.type !== "file") return
  const files = input.files
  if (!files?.length) return
  state.uploading = true
  render()
  try {
    const uploaded = await uploadFiles(files)
    const first = String(uploaded[0] || "").trim()
    if (!first) throw new Error("upload")
    const cur = [...state.draft.imageUrls]
    if (input.id === "tpl-file-layout") {
      const logo = String(cur[1] || "").trim()
      state.draft.imageUrls = logo ? [first, logo] : [first]
    } else if (input.id === "tpl-file-logo") {
      const layout = String(cur[0] || "").trim()
      if (!layout) throw new Error("Önce layout yükleyin")
      state.draft.imageUrls = [layout, first]
    }
    state.status = "✓ Görsel yüklendi"
  } catch (err) {
    state.status = err instanceof Error ? err.message : String(err)
  } finally {
    state.uploading = false
    input.value = ""
    render()
  }
})

void refresh()
