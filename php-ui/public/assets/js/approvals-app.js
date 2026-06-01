import { appPath, authHeaders, apiRequest, esc } from "./social-media-api.js"
import { mapDraft, mapPost } from "./social-media-mappers.js"
import { mergePostImageListOrdered, revisionChainVariantCount } from "./social-media-post-utils.js"
import { carouselKey, createFeedCardCarousel } from "./sm-feed-card-carousel.js"

const SCHEDULED_POSTS = "scheduled_posts"
const STORY_SCHEDULED_POSTS = "story_scheduled_posts"
const CAMPAIGN_SCHEDULED_POSTS = "campaign_scheduled_posts"
const COMPOSER_DRAFTS = "composer_drafts"
const ACCOUNTS = "accounts"
const CAMPAIGN_ACCOUNTS = "campaign_accounts"

const LS_VIEW = "approvals.view"
const LS_TAB = "approvals.tab"
const PAGE = window.__APPROVALS_PAGE__ && typeof window.__APPROVALS_PAGE__ === "object" ? window.__APPROVALS_PAGE__ : {}
const pageMode = PAGE.mode === "campaign" ? "campaign" : "social"
const isCampaignPage = pageMode === "campaign"

const state = {
  view: localStorage.getItem(LS_VIEW) === "list" ? "list" : "grid",
  uiTab: localStorage.getItem(LS_TAB) === "story" ? "story" : "post",
  posts: [],
  drafts: [],
  loading: false,
  cardSlides: {},
}

const cardCarousel = createFeedCardCarousel(state.cardSlides)

function approvalsRoot() {
  return document.getElementById("approvals-root")
}

async function listCollection(name) {
  try {
    const data = await apiRequest("/social-data/collections/" + encodeURIComponent(name), {
      headers: authHeaders(false),
    })
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.items)) return data.items
    return []
  } catch {
    return []
  }
}

function isPendingApproval(status) {
  const a = String(status || "").toLowerCase()
  return a === "pending" || a === "waiting_approval"
}

function formatPricingLine(pricing) {
  if (!pricing || typeof pricing !== "object") return ""
  const oldP = String(pricing.old_price ?? pricing.oldPrice ?? "").trim()
  const newP = String(pricing.new_price ?? pricing.newPrice ?? "").trim()
  const disc = String(pricing.discount_percent ?? pricing.discountPercent ?? "").trim()
  if (oldP && newP) return `${oldP} → ${newP} TL`
  if (disc) return `%${disc.replace(/%/g, "")} indirim`
  if (newP) return `${newP} TL`
  return ""
}

function isStoryItem(item) {
  const pt = item.publishTargets
  if (pt) return Boolean(pt.instagramStory) && !Boolean(pt.instagramPost)
  return String(item.contentType || "").toLowerCase().includes("story")
}

function collectCardImages(item) {
  if (item.isDraft) {
    const snap = item.snapshot && typeof item.snapshot === "object" ? item.snapshot : {}
    const baseUrls = [
      ...new Set(
        [...(snap.uploadedImageUrls || []), ...(snap.aiImageUrls || [])]
          .map((u) => String(u || "").trim())
          .filter(Boolean),
      ),
    ]
    const revUrls = Object.values(snap.revisionMap || {})
      .flat()
      .map((u) => String(u || "").trim())
      .filter(Boolean)
    const sel = String(item.imageUrl || "").trim()
    return [...new Set([...(sel ? [sel] : []), ...baseUrls, ...revUrls])]
  }
  return mergePostImageListOrdered(item.imageUrl, item.imageUrls || [])
}

function postRevisionCount(post) {
  const raw = typeof post?.revisionSnapshotJson === "string" ? post.revisionSnapshotJson : ""
  if (!raw) return 0
  try {
    const parsed = JSON.parse(raw)
    const map = parsed && typeof parsed === "object" ? parsed.revisionMap : null
    if (!map || typeof map !== "object") return 0
    return revisionChainVariantCount(map)
  } catch {
    return 0
  }
}

function draftRevisionCount(draft) {
  const snap = draft.snapshot && typeof draft.snapshot === "object" ? draft.snapshot : {}
  return revisionChainVariantCount(snap.revisionMap || {})
}

function waitStudioApi(timeoutMs = 12000) {
  if (window.__SM_STUDIO_API__?.ready) return Promise.resolve(window.__SM_STUDIO_API__)
  return new Promise((resolve) => {
    const done = () => resolve(window.__SM_STUDIO_API__?.ready ? window.__SM_STUDIO_API__ : null)
    if (window.__SM_STUDIO_API__?.ready) {
      done()
      return
    }
    const onReady = () => {
      window.removeEventListener("sm-studio-ready", onReady)
      done()
    }
    window.addEventListener("sm-studio-ready", onReady)
    window.setTimeout(() => {
      window.removeEventListener("sm-studio-ready", onReady)
      done()
    }, timeoutMs)
  })
}

async function loadData() {
  const [smRows, storyRows, campRows, draftRows, smAccs, campAccs] = await Promise.all([
    listCollection(SCHEDULED_POSTS),
    isCampaignPage ? Promise.resolve([]) : listCollection(STORY_SCHEDULED_POSTS),
    listCollection(CAMPAIGN_SCHEDULED_POSTS),
    isCampaignPage ? Promise.resolve([]) : listCollection(COMPOSER_DRAFTS),
    listCollection(ACCOUNTS),
    listCollection(CAMPAIGN_ACCOUNTS),
  ])

  const nameById = new Map()
  for (const a of [...smAccs, ...campAccs]) {
    const d = a && a.data ? a.data : a
    const id = String(a?.id ?? d?.id ?? "")
    if (id) nameById.set(id, String(d?.name ?? d?.accountName ?? "Hesap"))
  }

  const enrich = (p) => ({
    ...p,
    accountName: p.accountName || nameById.get(p.accountId) || "(hesap belirsiz)",
  })

  let posts = []
  if (isCampaignPage) {
    const campById = new Map()
    for (const row of campRows) {
      const p = mapPost(row)
      if (!isPendingApproval(p.approvalStatus) || !p.id) continue
      campById.set(p.id, { ...enrich(p), collection: CAMPAIGN_SCHEDULED_POSTS })
    }
    for (const row of smRows) {
      const p = mapPost(row)
      if (!isPendingApproval(p.approvalStatus) || p.source !== "campaign_banner" || !p.id) continue
      if (!campById.has(p.id)) {
        campById.set(p.id, { ...enrich(p), collection: SCHEDULED_POSTS })
      }
    }
    posts = [...campById.values()]
  } else {
    const postCards = smRows
      .map((row) => mapPost(row))
      .filter((p) => isPendingApproval(p.approvalStatus) && p.source !== "campaign_banner" && p.id)
      .map((p) => ({ ...enrich(p), collection: SCHEDULED_POSTS }))
    const storyCards = storyRows
      .map((row) => mapPost(row))
      .filter((p) => isPendingApproval(p.approvalStatus) && p.id)
      .map((p) => ({ ...enrich(p), collection: STORY_SCHEDULED_POSTS }))
    posts = [...postCards, ...storyCards]
  }

  const drafts = isCampaignPage
    ? []
    : draftRows
        .map((row) => mapDraft(row))
        .filter((d) => d.id)
        .map((d) => ({ ...enrich(d), isDraft: true, collection: COMPOSER_DRAFTS }))

  return { posts, drafts }
}

function filteredPosts() {
  if (isCampaignPage) return state.posts
  if (state.uiTab === "story") return state.posts.filter((p) => isStoryItem(p))
  return state.posts.filter((p) => !isStoryItem(p))
}

function filteredDrafts() {
  if (isCampaignPage || state.uiTab === "story") return []
  return state.drafts
}

function findItem(pid, draftId) {
  if (draftId) return state.drafts.find((d) => d.id === draftId) || null
  if (pid) return state.posts.find((p) => p.id === pid) || null
  return null
}

function renderFeedCard(item, kind) {
  const isCampaign = kind === "campaign"
  const isDraft = Boolean(item.isDraft)
  const carouselKind = isDraft ? "draft" : "post"
  const allImg = collectCardImages(item)
  const revCount = isDraft ? draftRevisionCount(item) : postRevisionCount(item)
  const aspect = isCampaign ? "1600/704" : "4/4.5"
  const captionRaw = (item.caption || item.prompt || "").trim() || (isDraft ? "(bos taslak)" : "(içerik yok)")
  const caption = esc(captionRaw)

  const campaignPills = isCampaign
    ? `${item.campaignName ? `<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-900">${esc(item.campaignName)}</span>` : ""}
${formatPricingLine(item.campaignPricing) ? `<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-900">${esc(formatPricingLine(item.campaignPricing))}</span>` : ""}`
    : ""

  const pidAttr = isDraft ? "" : ` data-pid="${esc(item.id)}"`
  const draftAttr = isDraft ? ` data-draft-id="${esc(item.id)}"` : ""
  const collectionAttr = isDraft
    ? ""
    : ` data-collection="${esc(item.collection || (kind === "campaign" ? CAMPAIGN_SCHEDULED_POSTS : SCHEDULED_POSTS))}" data-kind="${esc(kind)}"`

  const mediaInner = cardCarousel.render(carouselKind, item.id, allImg)

  const statusPill = isDraft
    ? `<span class="rounded-full bg-neutral-200 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-700">Taslak</span>`
    : `<span class="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-900">Onay bekliyor</span>`

  return `<article class="sm-feed-card sm-premium-feed-card group overflow-hidden rounded-[22px] border border-neutral-200 bg-white shadow-[0_6px_18px_rgba(15,23,42,0.05)] transition hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(15,23,42,0.08)]"${pidAttr}${draftAttr}${collectionAttr}>
  <div class="relative overflow-hidden bg-neutral-100" style="aspect-ratio:${aspect};">
    ${mediaInner}
    <div class="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-1.5 bg-gradient-to-b from-black/55 via-black/20 to-transparent p-2">
      <div class="min-w-0 flex items-center gap-2">
        <span class="h-2 w-2 rounded-full bg-emerald-300" aria-hidden="true"></span>
        <p class="truncate text-sm font-semibold text-white">${esc(item.accountName || "Hesap secilmemis")}</p>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-1">
        <span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(item.date || "-")}</span>
        <span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(item.time || "12:00")}</span>
      </div>
    </div>
  </div>
  <div class="p-2.5">
    <div class="flex flex-wrap items-center gap-1">
      ${statusPill}
      ${allImg.length > 1 ? `<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-semibold text-neutral-700">${allImg.length} gorsel</span>` : ""}
      ${revCount ? `<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800">${revCount} revizyon</span>` : ""}
      ${campaignPills}
    </div>
    <p class="mt-1 overflow-hidden text-[12.5px] leading-relaxed text-neutral-700" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;">${caption}</p>
    <div class="mt-2 grid grid-cols-3 gap-1">
      <button type="button" data-act="approvals-edit" class="rounded-xl bg-neutral-900 px-2 py-1.5 text-xs font-semibold text-white hover:bg-neutral-800">Düzenle</button>
      <button type="button" data-act="approvals-delete" class="rounded-xl border border-red-200 bg-red-50 px-2 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100">Sil</button>
      <button type="button" data-act="approvals-approve" class="rounded-xl bg-emerald-600 px-2 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700">Onayla</button>
    </div>
  </div>
</article>`
}

function renderCreateCard() {
  const label = isCampaignPage ? "Yeni kampanya bannerı" : "Yeni içerik oluştur"
  const hint = isCampaignPage
    ? "Kampanya içerik oluşturma modali burada açılır"
    : "Gönderi veya hikaye oluşturma modali burada açılır"
  return `<button type="button" data-act="approvals-create" class="sm-premium-card sm-premium-card--create">
  <span class="sm-premium-card--create__icon">+</span>
  <span class="sm-premium-card--create__label">${esc(label)}</span>
  <span class="sm-premium-card--create__hint">${esc(hint)}</span>
</button>`
}

function renderTabs() {
  if (isCampaignPage) return ""
  return `<nav class="sm-premium-tabs" role="tablist">
  <button type="button" role="tab" data-act="approvals-tab" data-tab="post" class="sm-premium-tabs__btn${state.uiTab === "post" ? " is-active" : ""}">Post Onayları</button>
  <button type="button" role="tab" data-act="approvals-tab" data-tab="story" class="sm-premium-tabs__btn${state.uiTab === "story" ? " is-active" : ""}">Hikaye Onayları</button>
</nav>`
}

function renderToolbar() {
  return `<div class="sm-premium-toolbar">
  <select class="sm-premium-select" aria-label="Filtre" disabled>
    <option>Onay bekleyen</option>
  </select>
  <div class="flex flex-wrap items-center gap-2">
    <button type="button" data-act="approvals-refresh" class="sm-premium-btn sm-premium-btn--ghost">Yenile</button>
    <div class="sm-premium-view-toggle" role="group" aria-label="Görünüm">
      <button type="button" data-act="approvals-view-mode" data-mode="grid" class="sm-premium-view-toggle__btn${state.view === "grid" ? " is-active" : ""}" title="Kart">⊞</button>
      <button type="button" data-act="approvals-view-mode" data-mode="list" class="sm-premium-view-toggle__btn${state.view === "list" ? " is-active" : ""}" title="Liste">≡</button>
    </div>
  </div>
</div>`
}

function renderGrid() {
  const kind = isCampaignPage ? "campaign" : "social"
  const posts = filteredPosts()
  const drafts = filteredDrafts()
  const items = [...posts, ...drafts]
  const gridCls = state.view === "list" ? "sm-premium-grid sm-premium-grid--list" : "sm-premium-grid"
  const cards = items.map((item) => renderFeedCard(item, kind)).join("")
  const createCard = renderCreateCard()
  return `<div class="${gridCls}">${cards}${createCard}</div>`
}

function setPageCopy() {
  const createHeader = document.querySelector('header [data-act="approvals-create"]')
  if (createHeader) {
    createHeader.textContent = isCampaignPage ? "+ Yeni kampanya oluştur" : "+ Yeni içerik oluştur"
  }
}

function updateChrome() {
  setPageCopy()
  const total = state.posts.length + state.drafts.length
  const totalEl = document.querySelector("[data-approvals-total]")
  if (totalEl) totalEl.textContent = String(total)

  const tabPost = document.querySelector('[data-act="approvals-tab"][data-tab="post"]')
  const tabStory = document.querySelector('[data-act="approvals-tab"][data-tab="story"]')
  if (tabPost) {
    const n = state.posts.filter((p) => !isStoryItem(p)).length + state.drafts.length
    tabPost.textContent = `Post Onayları (${n})`
  }
  if (tabStory) {
    const n = state.posts.filter((p) => isStoryItem(p)).length
    tabStory.textContent = `Hikaye Onayları (${n})`
  }
}

function render() {
  const root = document.getElementById("approvals-root")
  if (!root) return
  updateChrome()
  root.innerHTML = `
${state.loading ? `<p class="mb-4 text-sm text-neutral-500">Yükleniyor…</p>` : ""}
${renderTabs()}
${renderToolbar()}
${renderGrid()}
<p class="mt-8 text-xs text-neutral-500"><a href="${esc(appPath(isCampaignPage ? "/campaign-management" : "/social-media"))}" class="font-medium text-emerald-800 hover:underline">← Takvime dön</a></p>`
}

async function refresh() {
  state.loading = true
  render()
  try {
    const { posts, drafts } = await loadData()
    state.posts = posts
    state.drafts = drafts
  } catch (err) {
    const root = document.getElementById("approvals-root")
    if (root) {
      root.innerHTML = `<p class="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">${esc(err instanceof Error ? err.message : String(err))}</p>`
    }
    return
  } finally {
    state.loading = false
  }
  render()
}

async function openCreateModal() {
  const api = await waitStudioApi()
  if (!api) {
    alert("İçerik oluşturma modülü yüklenemedi. Sayfayı yenileyin.")
    return
  }
  api.openCreate({ story: !isCampaignPage && state.uiTab === "story" })
}

async function handleCardAction(btn) {
  const card = btn.closest("article[data-pid], article[data-draft-id]")
  if (!card) return
  const pid = card.getAttribute("data-pid")
  const draftId = card.getAttribute("data-draft-id")
  const act = btn.getAttribute("data-act")
  if (!act) return

  const item = findItem(pid, draftId)
  if (!item && act !== "approvals-create") return

  const api = await waitStudioApi()
  if (!api) {
    alert("İçerik modülü yüklenemedi. Sayfayı yenileyin.")
    return
  }

  btn.setAttribute("disabled", "true")
  try {
    if (act === "approvals-edit") {
      if (draftId) api.openDraft(item)
      else api.openPost(item)
      return
    }
    if (act === "approvals-delete") {
      if (draftId) await api.deleteDraft(draftId)
      else await api.deletePost(pid)
      await refresh()
      return
    }
    if (act === "approvals-approve") {
      if (draftId) {
        api.openDraft(item, { approveFocus: true })
        return
      }
      const collection = card.getAttribute("data-collection") || SCHEDULED_POSTS
      await api.approvePost(pid, collection)
      await refresh()
    }
  } catch (err) {
    console.error("approvals action failed", err)
    alert("İşlem başarısız: " + (err instanceof Error ? err.message : String(err)))
  } finally {
    btn.removeAttribute("disabled")
  }
}

function handleCarouselClick(target) {
  const root = approvalsRoot()
  if (!root) return false
  const prev = target.closest('[data-act="day-card-prev"]')
  const next = target.closest('[data-act="day-card-next"]')
  const dot = target.closest('[data-act="day-card-dot"]')
  if (prev) {
    const kind = prev.getAttribute("data-kind") || ""
    const id = prev.getAttribute("data-id") || ""
    if (kind && id) cardCarousel.shift(root, kind, id, -1)
    return true
  }
  if (next) {
    const kind = next.getAttribute("data-kind") || ""
    const id = next.getAttribute("data-id") || ""
    if (kind && id) cardCarousel.shift(root, kind, id, 1)
    return true
  }
  if (dot) {
    const kind = dot.getAttribute("data-kind") || ""
    const id = dot.getAttribute("data-id") || ""
    const idx = Number(dot.getAttribute("data-index") || "0")
    const card = root.querySelector(`[data-card-carousel="${carouselKey(kind, id)}"]`)
    const total = Number(card?.getAttribute("data-carousel-total") || "0")
    if (kind && id && Number.isFinite(idx)) {
      cardCarousel.setIndex(kind, id, total, idx)
      cardCarousel.syncDom(root, kind, id)
    }
    return true
  }
  return false
}

document.addEventListener("click", async (e) => {
  const target = e.target instanceof Element ? e.target : null
  if (!target) return

  if (handleCarouselClick(target)) {
    e.preventDefault()
    e.stopPropagation()
    return
  }

  const createBtn = target.closest('[data-act="approvals-create"]')
  if (createBtn) {
    e.preventDefault()
    await openCreateModal()
    return
  }

  const refreshBtn = target.closest('[data-act="approvals-refresh"]')
  if (refreshBtn) {
    void refresh()
    return
  }

  const tabBtn = target.closest('[data-act="approvals-tab"]')
  if (tabBtn) {
    const tab = tabBtn.getAttribute("data-tab")
    if (tab === "post" || tab === "story") {
      state.uiTab = tab
      localStorage.setItem(LS_TAB, tab)
      render()
    }
    return
  }

  const viewBtn = target.closest('[data-act="approvals-view-mode"]')
  if (viewBtn) {
    const mode = viewBtn.getAttribute("data-mode")
    if (mode === "grid" || mode === "list") {
      state.view = mode
      localStorage.setItem(LS_VIEW, mode)
      render()
    }
    return
  }

  const actionBtn = target.closest(
    '[data-act="approvals-edit"], [data-act="approvals-delete"], [data-act="approvals-approve"]',
  )
  if (actionBtn) {
    e.preventDefault()
    await handleCardAction(actionBtn)
  }
})

window.addEventListener("sm-studio-closed", () => {
  void refresh()
})
window.addEventListener("sm-scheduled-post-created", () => {
  void refresh()
})

function bindCarouselSwipe() {
  const root = approvalsRoot()
  if (!root || root.dataset.carouselSwipeBound === "1") return
  root.dataset.carouselSwipeBound = "1"
  let touchStart = null
  root.addEventListener(
    "touchstart",
    (e) => {
      const node = e.target instanceof Element ? e.target.closest("[data-carousel-swipe]") : null
      if (!node) return
      const touch = e.changedTouches?.[0]
      if (!touch) return
      const key = String(node.getAttribute("data-card-carousel") || "").trim()
      if (!key) return
      touchStart = { key, x: touch.clientX, y: touch.clientY }
    },
    { passive: true },
  )
  root.addEventListener("touchend", (e) => {
    const start = touchStart
    touchStart = null
    if (!start) return
    const touch = e.changedTouches?.[0]
    if (!touch) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < 28 || Math.abs(dx) <= Math.abs(dy)) return
    const [kind, ...rest] = start.key.split(":")
    const id = rest.join(":")
    if (!kind || !id) return
    cardCarousel.shift(root, kind, id, dx > 0 ? -1 : 1)
  })
}

bindCarouselSwipe()
void refresh()
