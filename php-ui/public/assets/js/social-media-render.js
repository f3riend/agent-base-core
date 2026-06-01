import { appPath, cfg, esc, T } from "./social-media-api.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { getHolidayLabelsSync, holidayTooltipTextSync } from "./social-media-holidays.js"
import {
  buildCalendarDays,
  formatDateKey,
  lifecycleBadge,
  mergePostImageListOrdered,
  parseScheduledLocalDateTime,
  isPostApproved,
  isPostUnapproved,
  resolvePostLifecycle,
  revisionChainVariantCount,
  scheduleRelativeText,
  sourceBadgeText,
} from "./social-media-post-utils.js"
import {
  clearVisualPendingHint,
  countComposerPendingTasks,
  countHolidayPendingTasks,
  loadPendingTasks,
  pendingTaskProgressSummary,
  readCaptionInFlightBanner,
  readImageHttpInFlightBanner,
  readVisualPendingHint,
} from "./social-media-runtime.js"
import {
  approvedPosts,
  eventTimelineForWorkflow,
  localeTag,
  selectedDayPosts,
  unapprovedPosts,
  visibleDrafts,
  visiblePosts,
  weekdayLabels,
  workflowForPost,
} from "./social-media-selectors.js"
import { rootEl, s } from "./social-media-state.js"
import { iconChevL, iconChevR, iconPlus, loadingDotsHtml } from "./social-media-ui.js"

function dateFromKey(key) {
  const raw = String(key || "").trim()
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0, 0)
  return Number.isNaN(dt.getTime()) ? null : dt
}

export function createSocialMediaRenderer({ paintModals } = {}) {
  function paintTaskBanner() {
    const el = rootEl && rootEl.querySelector("#sm-task-banner")
    if (!el) return
    const nVisual = countComposerPendingTasks()
    const nHoliday = countHolidayPendingTasks()
    const holidayBusy = Boolean(s.holidayBusyDateKey)
    const cap = readCaptionInFlightBanner()
    const imgHttpWait = nVisual === 0 && readImageHttpInFlightBanner()
    const visualHint = nVisual === 0 && imgHttpWait ? readVisualPendingHint() : null
    const progressInfo = pendingTaskProgressSummary()
    const progressPct = progressInfo ? progressInfo.progress : null
    if (nVisual === 0 && !imgHttpWait) clearVisualPendingHint()
    if (nVisual === 0 && nHoliday === 0 && !holidayBusy && !cap && !imgHttpWait && !visualHint) {
      el.classList.add("hidden")
      el.classList.remove("flex")
      el.innerHTML = ""
      return
    }
    let msg = ""
    if (holidayBusy) msg = T("holidayDraftsAiWorking")
    else if (cap) msg = T("composerCaptionResumeBanner")
    else if (imgHttpWait) msg = T("composerImageHttpBanner")
    else if (visualHint) {
      const vk = visualHint.kind
      if (vk === "revise") msg = T("composerVisualReviseWorking")
      else if (vk === "video") msg = T("composerVideoGenerateWorking")
      else if (vk === "generate" || vk === "reference") msg = T("composerVisualGenerateWorking")
      else msg = T("composerVisualBackgroundWorking")
    }
    else if (nHoliday > 0 && nVisual > 0)
      msg = T("taskQueueBannerMixed").replace("{h}", String(nHoliday)).replace("{v}", String(nVisual))
    else if (nHoliday > 0) msg = T("taskQueueBannerHolidays").replace("{n}", String(nHoliday))
    else msg = T("backgroundCeleryBanner")
    el.classList.remove("hidden")
    el.classList.add("flex")
    const progressBar =
      progressPct !== null
        ? `<span class="ml-auto flex min-w-[9rem] items-center gap-2">
  <span class="h-1.5 w-24 overflow-hidden rounded-full bg-amber-200" aria-hidden="true"><span class="block h-full rounded-full bg-amber-700 transition-all duration-300" style="width:${progressPct}%"></span></span>
  <span class="w-9 text-right font-mono text-[10px] tabular-nums">${progressPct}%</span>
</span>`
        : ""
    el.innerHTML = `${loadingDotsHtml("text-amber-800")}<span class="min-w-0 flex-1">${esc(msg)}</span>${progressBar}`
  }

  function makeCalendarDragGhost(post) {
    const el = document.createElement("div")
    el.style.cssText =
      "position:fixed;top:-9999px;left:-9999px;width:148px;padding:8px;border-radius:12px;background:#ffffff;box-shadow:0 10px 24px rgba(15,23,42,.18);border:1px solid #e5e7eb;display:flex;align-items:center;gap:8px;z-index:9999;pointer-events:none;"
    const media = (post && post.imageUrl ? String(post.imageUrl) : "").trim()
    const title = String((post && post.accountName) || "Post").trim() || "Post"
    el.innerHTML = `
<div style="width:36px;height:36px;border-radius:8px;overflow:hidden;background:#f3f4f6;flex-shrink:0;">
  ${media ? `<img src="${esc(media)}" alt="" style="width:100%;height:100%;object-fit:cover;" />` : ""}
</div>
<div style="min-width:0;">
  <p style="margin:0;font-size:11px;font-weight:600;color:#111827;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:92px;">${esc(title)}</p>
  <p style="margin:0;font-size:10px;color:#6b7280;">Tarihi taşı</p>
</div>`
    document.body.appendChild(el)
    return el
  }

  function paintShell() {
    if (!rootEl) return
    const calendarTitle = CAMPAIGN_MODE ? "Kampanya Takvimi" : T("calendarTitle")
    const calendarHint = CAMPAIGN_MODE ? "Kampanya bannerlerini planlayin ve yayinlayin." : T("calendarHint")
    const autoPublishNote = CAMPAIGN_MODE
      ? "Magaza listesi /resources/stores; indirimli urunler magaza secilince /resources/items ile gelir. Banner yayini POST /banners."
      : T("autoPublishNote")
    rootEl.innerHTML = `
<div class="flex min-h-screen min-w-0 flex-1 flex-col bg-[#fcfcfa]">
  <main class="flex min-h-0 min-w-0 flex-1 flex-col">
    <p id="sm-status" class="empty:hidden shrink-0 border-b border-neutral-200 bg-[#fcfcfa] px-4 py-2 text-center text-xs text-neutral-600 md:px-6"></p>
    <div id="sm-task-banner" class="hidden sticky top-0 z-30 shrink-0 items-center gap-2 border-b border-amber-200 bg-amber-50/98 px-4 py-2.5 text-xs font-semibold text-amber-950 backdrop-blur-sm"></div>
    <section class="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div class="shrink-0 border-b border-neutral-200 bg-[#fcfcfa] px-4 py-4 md:px-6">
        <div class="mx-auto w-full max-w-[1720px]">
          <h2 class="text-lg font-semibold text-neutral-900">${esc(calendarTitle)}</h2>
          <p class="mt-0.5 text-xs text-neutral-500">${esc(calendarHint)}</p>
          <p class="mt-1 text-[11px] leading-relaxed text-neutral-400">${esc(autoPublishNote)}</p>
          <div id="sm-accounts" class="mt-4 flex w-full gap-3 overflow-x-auto px-1 pb-2 pt-1 [scrollbar-width:thin] [scroll-behavior:smooth]"></div>
        </div>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto bg-[#f6f7f8] px-4 py-4 md:px-6">
        <div class="mx-auto max-w-[1720px]">
          <div class="mb-4"><span class="text-sm text-neutral-500">${esc(T("selectedDate"))}: <span id="sm-seldate"></span></span></div>
          <div class="sm-workspace">
            <div id="sm-calendar-wrap" class="sm-workspace-calendar"></div>
            <div id="sm-day-panel" class="sm-workspace-feed h-full"></div>
          </div>
        </div>
      </div>
    </section>
  </main>
</div>
<div id="sm-modals"></div>`
    const sd = rootEl.querySelector("#sm-seldate")
    if (sd) sd.textContent = s.selectedDate
    const st = rootEl.querySelector("#sm-status")
    if (st && s.statusLine) st.textContent = s.statusLine
  }

  function paintAccounts() {
    const box = rootEl && rootEl.querySelector("#sm-accounts")
    if (!box) return
    const allSel = !s.filterAccountId
    const allLabel = CAMPAIGN_MODE ? "Tüm Kampanya Hesapları" : T("all")
    const addAccountLabel = CAMPAIGN_MODE ? "Kampanya Hesabı Ekle" : T("addAccount")
    let h = `
<button type="button" data-act="filter-all" class="flex shrink-0 flex-col items-center gap-1.5">
  <div class="flex h-16 w-16 items-center justify-center rounded-full border-[3px] bg-white text-xs font-semibold text-neutral-700 ${
    allSel ? "border-neutral-900 ring-2 ring-neutral-900/20" : "border-neutral-200"
  }">${esc(CAMPAIGN_MODE ? "Tümü" : T("all"))}</div>
  <span class="max-w-[92px] truncate text-center text-[11px] text-neutral-600">${esc(allLabel)}</span>
</button>`
    for (const a of s.accounts) {
      const sel = s.filterAccountId === a.id
      const logo = a.logoUrl
        ? `<img src="${esc(a.logoUrl)}" alt="" class="h-full w-full object-cover"/>`
        : esc(a.name.slice(0, 2).toUpperCase())
      h += `
<button type="button" data-act="pick-account" data-aid="${esc(a.id)}" class="flex shrink-0 flex-col items-center gap-1.5">
  <div class="flex h-16 w-16 items-center justify-center overflow-hidden rounded-full border-[3px] bg-gradient-to-br from-violet-500 to-fuchsia-600 text-sm font-semibold text-white ${
    sel ? "border-neutral-900 ring-2 ring-neutral-900/25" : "border-transparent"
  }">${logo}</div>
  <span class="max-w-[72px] truncate text-center text-[11px] text-neutral-600">${esc(a.name)}</span>
</button>`
    }
    h += `
<button type="button" data-act="open-account-modal" class="flex shrink-0 flex-col items-center gap-1.5">
  <div class="flex h-16 w-16 items-center justify-center rounded-full border-2 border-dashed border-neutral-300 bg-white text-neutral-400 transition hover:border-neutral-400 hover:text-neutral-600">${iconPlus()}</div>
  <span class="max-w-[92px] truncate text-center text-[11px] text-neutral-600">${esc(addAccountLabel)}</span>
</button>`
    box.innerHTML = h
  }

  function paintCalendar() {
    const wrap = rootEl && rootEl.querySelector("#sm-calendar-wrap")
    if (!wrap) return
    const days = buildCalendarDays(s.monthDate)
    const wlabels = weekdayLabels()
    const tagsHref = appPath("/social-media/etiketler")
    const templatesHref = appPath(CAMPAIGN_MODE ? "/campaign-management/sablonlar" : "/social-media/sablonlar")
    const ticketsButton = CAMPAIGN_MODE
      ? ""
      : `<a href="${esc(tagsHref)}" class="flex items-center gap-1.5 rounded-full border border-neutral-200 bg-white px-3 py-1.5 text-xs font-semibold text-neutral-700 transition hover:bg-neutral-50">
      <span class="text-base leading-none">🎫</span> Etiketler
      ${s.tickets.length ? `<span class="rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10px] font-bold text-neutral-500">${s.tickets.length}</span>` : ""}
    </a>`
    let header = `
<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
  <div class="flex items-center gap-2">
    <button type="button" data-act="cal-prev" class="rounded-full border border-neutral-200 bg-white p-2 text-neutral-700 transition hover:border-neutral-300 hover:bg-neutral-50">${iconChevL()}</button>
    <h3 class="min-w-[10rem] text-center text-base font-semibold text-neutral-900">${esc(
      s.monthDate.toLocaleDateString(localeTag(), { month: "long", year: "numeric" }),
    )}</h3>
    <button type="button" data-act="cal-next" class="rounded-full border border-neutral-200 bg-white p-2 text-neutral-700 transition hover:border-neutral-300 hover:bg-neutral-50">${iconChevR()}</button>
  </div>
  <div class="flex flex-wrap items-center gap-2">
    ${ticketsButton}
    <a href="${esc(templatesHref)}" class="flex items-center gap-1.5 rounded-full border border-neutral-200 bg-white px-3 py-1.5 text-xs font-semibold text-neutral-700 transition hover:bg-neutral-50">
      <span class="text-base leading-none">📋</span> ${esc(T("calendarTemplatesBtn"))}
      ${s.userTemplates.length ? `<span class="rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700">${s.userTemplates.length}</span>` : ""}
    </a>
  </div>
</div>
<div class="mb-3 grid grid-cols-7 gap-3 px-1 text-center text-[11px] font-semibold uppercase tracking-[0.2em] text-neutral-400">
  ${wlabels.map((lb) => `<div>${esc(lb)}</div>`).join("")}
</div>
<div id="sm-cal-grid" class="grid grid-cols-7 gap-3"></div>`
    wrap.innerHTML = header
    const grid = wrap.querySelector("#sm-cal-grid")
    const vpRaw = visiblePosts()
    /** Sağ panel sekmesi takvim hücrelerine de uygulanır: Taslaklar → boş; Onaylı → approved; Onaylanmayan → pending. */
    const vp = s.dayListTab === "drafts"
      ? []
      : s.dayListTab === "unapproved"
        ? vpRaw.filter((p) => isPostUnapproved(p))
        : vpRaw.filter((p) => isPostApproved(p))
    const loc = cfg().uiLocale || "tr"
    const selectedPost = s.selectedPostId
      ? s.posts.find((p) => p.id === s.selectedPostId)
      : null
    const highlightRange = (() => {
      if (!selectedPost) return null
      const start = String(selectedPost.campaignStartDate || selectedPost.date || "").trim()
      const end = String(selectedPost.campaignEndDate || selectedPost.date || start).trim()
      if (!start) return null
      return { start, end: end || start }
    })()
    const inHighlightRange = (dateKey) => {
      if (!highlightRange) return false
      return dateKey >= highlightRange.start && dateKey <= highlightRange.end
    }
    for (const day of days) {
      const dateKey = formatDateKey(day)
      const isCur = day.getMonth() === s.monthDate.getMonth()
      const isSel = dateKey === s.selectedDate
      const posts = vp.filter((p) => p.date === dateKey)
      const lifecycleRows = posts.map((p) => resolvePostLifecycle(p))
      const hasCancelled = lifecycleRows.includes("cancelled")
      const hasPublishing = lifecycleRows.includes("publishing")
      const hasScheduled = lifecycleRows.includes("scheduled")
      const labels = getHolidayLabelsSync(day, loc)
      const hasApproved = posts.some((p) => p.approvalStatus === "approved")
      const title = labels.length ? holidayTooltipTextSync(day, loc) : ""
      const drop = s.draggingPostId && s.dragOverDateKey === dateKey
      const flash = s.dropFlashDateKey === dateKey
      let cellClass =
        "relative min-h-28 cursor-pointer rounded-[20px] border p-3 text-left shadow-[0_1px_0_rgba(255,255,255,0.9)_inset] transition duration-300 outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
      if (drop) {
        cellClass += isSel
          ? " border-neutral-900 bg-neutral-900 text-white ring-2 ring-white ring-inset"
          : " border-black bg-black/30 text-neutral-900 ring-2 ring-black"
      } else if (isSel)
        cellClass += " border-emerald-700 bg-emerald-700 text-white shadow-[0_10px_24px_rgba(6,95,70,0.28)]"
      else if (inHighlightRange(dateKey))
        cellClass += isCur
          ? " border-emerald-400 bg-emerald-100 text-emerald-900 shadow-[0_4px_12px_rgba(16,185,129,0.18)]"
          : " border-emerald-200 bg-emerald-50 text-emerald-700"
      else if (isCur) cellClass += " border-neutral-200 bg-white text-neutral-800 hover:-translate-y-0.5 hover:border-neutral-300 hover:bg-white hover:shadow-[0_8px_18px_rgba(15,23,42,0.08)]"
      else cellClass += " border-neutral-100 bg-neutral-50 text-neutral-300"
      if (flash && !isSel) cellClass += " ring-2 ring-emerald-200 bg-emerald-50/80"
      const busy = s.holidayBusyDateKey === dateKey
      const holidayQueuedThisDay = loadPendingTasks().some((tk) => tk.kind === "holiday" && tk.meta && tk.meta.dateKey === dateKey)
      const thumbs = posts
        .map(
          (post) => `
<div class="shrink-0" data-post-preview="${esc(post.id)}">
  <div draggable="true" data-drag-post="${esc(post.id)}" class="flex h-9 w-9 cursor-grab items-center justify-center overflow-hidden rounded-lg border bg-neutral-100/80 active:cursor-grabbing ${
    isSel ? "border-white/50" : "border-neutral-200"
  } ${resolvePostLifecycle(post) === "cancelled" ? "sm-thumb-cancelled" : ""}">
    ${
      post.imageUrl.trim()
        ? `<img src="${esc(post.imageUrl)}" alt="" class="max-h-full max-w-full object-contain"/>`
        : `<div class="flex h-full w-full items-center justify-center text-[10px] font-bold ${
            isSel ? "bg-white/20 text-white" : "bg-neutral-200 text-neutral-500"
          }">${esc(post.accountName.slice(0, 1).toUpperCase())}</div>`
    }
  </div>
</div>`,
        )
        .join("")
      const star = labels.length
        ? `<span class="max-w-[4.5rem] truncate rounded px-1 py-0.5 text-[8px] font-bold uppercase leading-none ${
            hasApproved ? "bg-emerald-500/90 text-emerald-950" : "bg-amber-400/90 text-amber-950"
          }" title="${esc(title)}">★</span>`
        : ""
      const el = document.createElement("div")
      el.className = cellClass
      el.setAttribute("role", "button")
      el.tabIndex = 0
      el.dataset.dateKey = dateKey
      if (title) el.title = title
      el.innerHTML = `
<div class="flex h-full flex-col">
  <div class="flex items-start justify-between gap-1">
    <span class="text-sm font-semibold">${day.getDate()}</span>${star}
  </div>
  <div class="mt-1 flex flex-wrap gap-1">
    ${hasScheduled ? `<span class="rounded bg-blue-100 px-1 py-0.5 text-[8px] font-semibold text-blue-800">${cfg().uiLocale === "en" ? "scheduled" : "Planlandı"}</span>` : ""}
    ${hasPublishing ? `<span class="rounded bg-amber-100 px-1 py-0.5 text-[8px] font-semibold text-amber-900">${cfg().uiLocale === "en" ? "publishing" : "Yayınlanıyor"}</span>` : ""}
    ${hasCancelled ? `<span class="rounded bg-red-100 px-1 py-0.5 text-[8px] font-semibold text-red-800">${cfg().uiLocale === "en" ? "cancelled" : "İptal"}</span>` : ""}
  </div>
  <div class="mt-1.5 flex max-h-[5.5rem] flex-wrap content-start gap-1 overflow-y-auto pr-0.5">${thumbs}</div>
</div>
${
  busy || holidayQueuedThisDay
    ? `<div class="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-1 rounded-[22px] bg-amber-50/93 backdrop-blur-[2px]">
  <span class="px-1.5 text-center text-[10px] font-semibold leading-tight text-amber-950">${
    busy ? esc(T("holidayDraftsAiWorking")) : esc(T("holidayDraftQueuedShort"))
  }</span>
  ${loadingDotsHtml("text-amber-800")}
</div>`
    : ""
}`
      if (busy || holidayQueuedThisDay) el.classList.add("relative")
      grid.appendChild(el)
    }
  }

  function paintDayPanel() {
    const wrap = rootEl && rootEl.querySelector("#sm-day-panel")
    if (!wrap) return
    const sp = selectedDayPosts()
    const pub = sp.filter((p) => p.publishStatus === "published").length
    const pend = sp.filter((p) => isPostUnapproved(p)).length
    const fail = sp.filter((p) => p.publishStatus === "failed").length
    const drafts = visibleDrafts()
    const ap = approvedPosts()
    const up = unapprovedPosts()
    const tabOrder = drafts.length > 0 ? ["drafts", "approved", "unapproved"] : ["approved", "unapproved"]
    if (drafts.length === 0 && s.dayListTab === "drafts") s.dayListTab = "approved"
    const tabLabel = (tab) => {
      if (tab === "approved") return `${T("tabApproved")} (${ap.length})`
      if (tab === "unapproved") return `${T("tabUnapproved")} (${up.length})`
      return `Taslaklar (${drafts.length})`
    }
    let listHtml = ""
    if (s.dayListTab !== "drafts") {
      const list = s.dayListTab === "approved" ? ap : up
      if (list.length === 0) {
        listHtml = `<div class="rounded-2xl bg-neutral-50 px-4 py-4 text-sm text-neutral-500">${
          s.dayListTab === "approved" ? esc(T("noPlansApprovedTab")) : esc(T("noPlansUnapprovedTab"))
        }</div>`
      } else {
        listHtml = list
          .map(
            (post) => {
              const images = mergePostImageListOrdered(post.imageUrl, post.imageUrls || [])
              const revCount = postRevisionCount(post)
              const lifecycle = resolvePostLifecycle(post)
              const lb = lifecycleBadge(lifecycle)
              const workflow = workflowForPost(post)
              const relative = scheduleRelativeText(post)
              const templateTitle = String(post.templateSnapshot?.name || post.templateId || workflow?.templateId || "").trim()
              return `
<article draggable="true" data-drag-post="${esc(post.id)}" data-act="select-post-highlight" data-pid="${esc(post.id)}" class="sm-feed-card sm-premium-feed-card group cursor-pointer overflow-hidden rounded-[22px] border border-neutral-200 bg-white shadow-[0_6px_18px_rgba(15,23,42,0.05)] transition hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(15,23,42,0.08)] ${s.selectedPostId === post.id ? "ring-2 ring-emerald-500 ring-offset-2" : ""} ${lifecycle === "cancelled" ? "sm-post-cancelled" : ""}">
  <div class="relative overflow-hidden bg-neutral-100" style="aspect-ratio:4/4.5;">
    ${renderCardCarousel("post", post.id, images)}
    <div class="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-1.5 bg-gradient-to-b from-black/55 via-black/20 to-transparent p-2">
      <div class="min-w-0 flex items-center gap-2">
        <span draggable="true" data-drag-post="${esc(post.id)}" class="pointer-events-auto inline-flex h-6 w-6 cursor-grab items-center justify-center rounded-full border border-white/50 bg-black/35 text-[11px] text-white active:cursor-grabbing" title="Surukle birak ile gunu degistir">↕</span>
        <p class="truncate text-sm font-semibold text-white">${esc(post.accountName || "Hesap secilmemis")}</p>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-1">
        <span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(post.date)}</span>
        ${post.time ? `<span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(post.time)}</span>` : ""}
      </div>
    </div>
  </div>
  <div class="p-2.5">
    <div class="flex flex-wrap items-center gap-1">
      <span class="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${lb.cls}">${esc(lb.label)}</span>
      ${
        post.approvalStatus === "pending"
          ? `<span class="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-900">${esc(T("approvalPending"))}</span>`
          : ""
      }
      ${
        post.approvalStatus === "rejected"
          ? `<span class="rounded-full bg-neutral-200 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-700">${esc(T("approvalRejected"))}</span>`
          : ""
      }
      ${
        post.source === "holiday" && post.holidayName
          ? `<span class="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-900">${esc(post.holidayName)}</span>`
          : ""
      }
      <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-semibold text-neutral-700">${esc(sourceBadgeText(post))}</span>
      ${relative ? `<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800">${esc(relative)}</span>` : ""}
      ${templateTitle ? `<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-900">${esc(templateTitle)}</span>` : ""}
      ${images.length > 1 ? `<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-semibold text-neutral-700">${images.length} gorsel</span>` : ""}
      ${revCount > 0 ? `<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800">${revCount} revizyon</span>` : ""}
    </div>
    ${workflow ? `<button type="button" data-act="workflow-select" data-wid="${esc(workflow.id)}" class="mt-1 text-xs font-medium text-blue-700 hover:underline">Workflow detayı</button>` : ""}
    ${post.publishStatus === "failed" && post.lastPublishError ? `<p class="mt-1 text-xs text-red-600">${esc(post.lastPublishError)}</p>` : ""}
    <p class="mt-1 overflow-hidden text-[12.5px] leading-relaxed text-neutral-700" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;">${esc(post.caption || "(bos icerik)")}</p>
    ${
      s.dayListTab === "unapproved" && isPostUnapproved(post)
        ? `<div class="mt-2 grid grid-cols-2 gap-1">
      <button type="button" data-act="approve-post" data-pid="${esc(post.id)}" class="rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700">Onayla</button>
      <button type="button" data-act="reject-post" data-pid="${esc(post.id)}" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100">Reddet</button>
    </div>`
        : ""
    }
    <div class="mt-2 grid grid-cols-2 gap-1">
      <button type="button" data-act="edit-post" data-pid="${esc(post.id)}" class="rounded-xl bg-neutral-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-neutral-800">${esc(T("editPlan"))}</button>
      <button type="button" data-act="del-post" data-pid="${esc(post.id)}" class="rounded-xl border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100">${esc(T("deletePlan"))}</button>
    </div>
  </div>
</article>`
            },
          )
          .join("")
      }
    } else if (drafts.length === 0) {
      listHtml = `<div class="rounded-2xl bg-neutral-50 px-4 py-4 text-sm text-neutral-500">Bu filtrede taslak yok.</div>`
    } else {
      listHtml = drafts
        .map((d) => {
          const snap = d.snapshot
          const baseUrls = [...new Set([...(snap?.uploadedImageUrls || []), ...(snap?.aiImageUrls || [])])]
          const revUrls = Object.values(snap?.revisionMap || {}).flat()
          const revVariantCount = revisionChainVariantCount(snap?.revisionMap || {})
          const sel = (d.imageUrl || "").trim()
          const allImg = [...new Set([...(sel ? [sel] : []), ...baseUrls, ...revUrls])]
          return `
<article class="sm-feed-card sm-premium-feed-card group overflow-hidden rounded-[22px] border border-neutral-200 bg-white shadow-[0_6px_18px_rgba(15,23,42,0.05)] transition hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
  <div class="relative overflow-hidden bg-neutral-100" style="aspect-ratio:4/4.5;">
    ${renderCardCarousel("draft", d.id, allImg)}
    <div class="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-1.5 bg-gradient-to-b from-black/55 via-black/20 to-transparent p-2">
      <div class="min-w-0 flex items-center gap-2">
        <span class="h-2 w-2 rounded-full bg-emerald-300" aria-hidden="true"></span>
        <p class="truncate text-sm font-semibold text-white">${esc(d.accountName || "Hesap secilmemis")}</p>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-1">
        <span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(d.date || "-")}</span>
        <span class="rounded-full border border-white/60 bg-black/35 px-2 py-0.5 text-[10px] font-medium text-white">${esc(d.time || "12:00")}</span>
      </div>
    </div>
  </div>
  <div class="p-2.5">
    <div class="flex flex-wrap items-center gap-1">
      <span class="rounded-full bg-neutral-200 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-700">Taslak</span>
      ${allImg.length > 1 ? `<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-semibold text-neutral-700">${allImg.length} gorsel</span>` : ""}
      ${revVariantCount ? `<span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800">${revVariantCount} revizyon</span>` : ""}
    </div>
    <p class="mt-1 overflow-hidden text-[12.5px] leading-relaxed text-neutral-700" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;">${esc((d.caption || "").trim() || (d.prompt || "").trim() || "(bos taslak)")}</p>
    <div class="mt-2 flex gap-1">
      <button type="button" data-act="resume-draft" data-did="${esc(d.id)}" class="flex-1 rounded-xl bg-neutral-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-neutral-800">Devam Et</button>
      <button type="button" data-act="del-draft" data-did="${esc(d.id)}" class="rounded-xl border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100">Sil</button>
    </div>
  </div>
</article>`
        })
        .join("")
    }
    const selectedKey = String(s.selectedDate || formatDateKey(new Date())).trim() || formatDateKey(new Date())
    const selectedDt = dateFromKey(selectedKey)
    const hlToday =
      s.dayScope === "today"
        ? (() => {
            const d = parseScheduledLocalDateTime(selectedKey, "12:00")
            if (!d) return ""
            const hl = getHolidayLabelsSync(d, cfg().uiLocale || "tr")
            return hl.length ? `<p class="mt-1.5 text-xs font-medium text-amber-900">${esc(hl.join(" · "))}</p>` : ""
          })()
        : ""
    const scopeTitle =
      s.dayScope === "today"
        ? "Bugün"
        : s.dayScope === "week"
          ? "Bu Hafta"
          : selectedDt
            ? `${selectedDt.getFullYear()}-${String(selectedDt.getMonth() + 1).padStart(2, "0")}`
            : `${s.monthDate.getFullYear()}-${String(s.monthDate.getMonth() + 1).padStart(2, "0")}`
    const dayWorkflowPosts = sp.filter((p) => p.automationWorkflowId || p.source === "store_workflow")
    const workflowList = dayWorkflowPosts
      .map((p) => workflowForPost(p))
      .filter(Boolean)
      .filter((w, i, arr) => arr.findIndex((x) => x.id === w.id) === i)
    if (!s.selectedWorkflowId && workflowList[0]) s.selectedWorkflowId = workflowList[0].id
    const selectedWorkflow = workflowList.find((w) => w.id === s.selectedWorkflowId) || workflowList[0] || null
    const selectedWorkflowPost = selectedWorkflow
      ? (sp.find((p) => String(p.automationWorkflowId || "") === selectedWorkflow.id)
        || sp.find((p) => String(p.id || "") === String(selectedWorkflow.scheduledPostId || "")))
      : null
    const wfTimeline = selectedWorkflow ? eventTimelineForWorkflow(selectedWorkflow, selectedWorkflowPost) : []
    const lifecycle = resolvePostLifecycle(selectedWorkflowPost)
    const wfBadge = lifecycleBadge(lifecycle)
    const workflowPanelHtml = selectedWorkflow
      ? `
<div class="mt-3 rounded-2xl border border-neutral-200 bg-white p-3">
  <div class="flex flex-wrap items-center gap-1.5">
    <span class="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${wfBadge.cls}">${esc(wfBadge.label)}</span>
    <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-semibold text-neutral-700">${esc(selectedWorkflow.workflowType || "store workflow")}</span>
    <span class="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800">${esc(selectedWorkflow.templateId || selectedWorkflowPost?.templateId || "-")}</span>
  </div>
  <p class="mt-1 text-xs text-neutral-600">Scheduled for: ${esc(selectedWorkflow.scheduledFor || selectedWorkflowPost?.scheduledAt || "-")}</p>
  <p class="mt-1 text-xs text-neutral-600">Cancellation policy: ${esc(selectedWorkflow.cancellationPolicy || "-")}</p>
  ${selectedWorkflowPost?.caption ? `<p class="mt-1 text-xs text-neutral-700">${esc(selectedWorkflowPost.caption)}</p>` : ""}
  ${selectedWorkflowPost?.imageUrl ? `<img src="${esc(selectedWorkflowPost.imageUrl)}" alt="" class="mt-2 h-24 w-full rounded-xl object-cover"/>` : ""}
  <div class="mt-2 flex flex-col gap-1">
    ${wfTimeline
      .map(
        (row) => `<div class="sm-workflow-line"><span>${esc(row.icon)}</span><strong>${esc(row.text)}</strong><small>${esc(String(row.at || "").slice(0, 16).replace("T", " "))}</small></div>`,
      )
      .join("") || `<div class="text-xs text-neutral-500">Workflow event bekleniyor</div>`}
  </div>
</div>`
      : ""
    const workflowPickerHtml = workflowList.length
      ? `<div class="mt-2 flex flex-wrap gap-1">${workflowList
        .map((wf) => `<button type="button" data-act="workflow-select" data-wid="${esc(wf.id)}" class="rounded-full px-2.5 py-1 text-[10px] font-semibold ${wf.id === (selectedWorkflow?.id || "") ? "bg-neutral-900 text-white" : "bg-neutral-100 text-neutral-700"}">${esc(wf.templateId || wf.id.slice(0, 8))}</button>`)
        .join("")}</div>`
      : ""
    wrap.innerHTML = `
<div class="flex h-full min-h-0 flex-col">
  <div class="shrink-0 border-b border-neutral-100 px-3 py-3">
    <p class="text-[11px] font-semibold uppercase tracking-[0.2em] text-neutral-400">${esc(T("selectedDay"))}</p>
    <div class="mt-1 flex flex-wrap items-center gap-2">
      <p class="text-lg font-semibold text-neutral-900">${scopeTitle}</p>
      <select id="sm-dayscope" class="rounded-lg border border-neutral-200 bg-white px-2.5 py-1 text-xs font-medium text-neutral-700">
        <option value="today" ${s.dayScope === "today" ? "selected" : ""}>Bugün</option>
        <option value="week" ${s.dayScope === "week" ? "selected" : ""}>Bu Hafta</option>
        <option value="month" ${s.dayScope === "month" ? "selected" : ""}>Bu Ay</option>
      </select>
    </div>
    ${hlToday}
    <div class="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
      <span class="inline-flex items-center gap-1 rounded-full border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-neutral-600"><span class="h-1.5 w-1.5 rounded-full bg-neutral-500"></span>${esc(T("plans"))}: <b class="font-semibold text-neutral-900">${sp.length}</b></span>
      <span class="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-emerald-700"><span class="h-1.5 w-1.5 rounded-full bg-emerald-600"></span>${esc(T("planStatusPublished"))}: <b class="font-semibold text-emerald-900">${pub}</b></span>
      <span class="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-sky-700"><span class="h-1.5 w-1.5 rounded-full bg-sky-700"></span>${esc(T("approvalPending"))}: <b class="font-semibold text-sky-900">${pend}</b></span>
      <span class="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-red-700"><span class="h-1.5 w-1.5 rounded-full bg-red-600"></span>${esc(T("planStatusFailed"))}: <b class="font-semibold text-red-900">${fail}</b></span>
    </div>
    ${workflowPickerHtml}
    ${workflowPanelHtml}
  </div>
  <div class="shrink-0 px-3 pt-2.5">
    <div class="flex flex-wrap items-center gap-1.5 border-b border-neutral-100 pb-2.5">
  ${tabOrder
    .map(
      (tab) => `
<button type="button" data-act="day-tab" data-tab="${tab}" class="rounded-full px-3 py-1.5 text-[11px] font-semibold transition ${
        s.dayListTab === tab ? "bg-neutral-900 text-white shadow-sm" : "bg-neutral-100 text-neutral-600 hover:bg-neutral-200/80"
      }">${esc(tabLabel(tab))}</button>`,
    )
    .join("")}
</div>
  </div>
  <div class="sm-feed-scroll-wrapper min-h-0 flex-1 px-3 pb-3 pt-3">
    <div class="sm-feed-stream">${listHtml}</div>
  </div>
  <div class="shrink-0 border-t border-neutral-100 px-3 py-2.5">
    ${CAMPAIGN_MODE
      ? `<button type="button" data-act="ctx-manual" data-dk="${esc(s.selectedDate)}" class="w-full rounded-xl border border-neutral-200 bg-[#f8faf8] px-4 py-2 text-sm font-semibold text-[#14532d] transition hover:border-[#14532d]/30 hover:bg-[#eef6ef]">+ Bu güne kampanya ekle</button>`
      : `<div class="grid grid-cols-2 gap-2">
          <button type="button" data-act="ctx-ig-post" data-dk="${esc(s.selectedDate)}" class="rounded-xl border border-neutral-200 bg-[#f8faf8] px-3 py-2 text-xs font-semibold text-[#14532d] transition hover:border-[#14532d]/30 hover:bg-[#eef6ef]">+ Post</button>
          <button type="button" data-act="ctx-ig-story" data-dk="${esc(s.selectedDate)}" class="rounded-xl border border-neutral-200 bg-[#f8faf8] px-3 py-2 text-xs font-semibold text-[#14532d] transition hover:border-[#14532d]/30 hover:bg-[#eef6ef]">+ Hikaye</button>
        </div>`}
  </div>
</div>`
  }

  function dayCardCarouselKey(kind, id) {
    return `${String(kind || "").trim()}:${String(id || "").trim()}`
  }

  function getDayCardCarouselIndex(kind, id, total) {
    const n = Number(total || 0)
    const key = dayCardCarouselKey(kind, id)
    const raw = Number(s.dayCardSlides[key] ?? 0)
    if (!Number.isFinite(raw) || n <= 1) return 0
    const clamped = Math.min(n - 1, Math.max(0, Math.trunc(raw)))
    if (clamped !== raw) s.dayCardSlides[key] = clamped
    return clamped
  }

  function setDayCardCarouselIndex(kind, id, total, nextIndex) {
    const n = Number(total || 0)
    const key = dayCardCarouselKey(kind, id)
    if (!Number.isFinite(nextIndex) || n <= 1) {
      s.dayCardSlides[key] = 0
      return 0
    }
    const clamped = Math.min(n - 1, Math.max(0, Math.trunc(nextIndex)))
    s.dayCardSlides[key] = clamped
    return clamped
  }

  function syncDayCardCarouselDom(kind, id) {
    if (!rootEl) return
    const key = dayCardCarouselKey(kind, id)
    const cards = Array.from(rootEl.querySelectorAll("[data-card-carousel]"))
    const card = cards.find((el) => el.getAttribute("data-card-carousel") === key)
    if (!card) return
    const total = Number(card.getAttribute("data-carousel-total") || "0")
    if (!Number.isFinite(total) || total <= 0) return
    const idx = getDayCardCarouselIndex(kind, id, total)
    const track = card.querySelector("[data-carousel-track]")
    if (track) track.style.transform = `translate3d(-${idx * 100}%,0,0)`
    const dots = Array.from(card.querySelectorAll('[data-act="day-card-dot"]'))
    dots.forEach((dot) => {
      const di = Number(dot.getAttribute("data-index") || "0")
      dot.setAttribute("aria-current", di === idx ? "true" : "false")
      dot.classList.toggle("bg-white", di === idx)
      dot.classList.toggle("w-4", di === idx)
      dot.classList.toggle("bg-white/20", di !== idx)
      dot.classList.toggle("w-2", di !== idx)
    })
  }

  function shiftDayCardCarousel(kind, id, delta) {
    if (!rootEl) return
    const key = dayCardCarouselKey(kind, id)
    const cards = Array.from(rootEl.querySelectorAll("[data-card-carousel]"))
    const card = cards.find((el) => el.getAttribute("data-card-carousel") === key)
    if (!card) return
    const total = Number(card.getAttribute("data-carousel-total") || "0")
    if (!Number.isFinite(total) || total <= 1) return
    const current = getDayCardCarouselIndex(kind, id, total)
    let next = current + Number(delta || 0)
    if (next < 0) next = total - 1
    if (next >= total) next = 0
    setDayCardCarouselIndex(kind, id, total, next)
    syncDayCardCarouselDom(kind, id)
  }

  function renderCardCarousel(kind, id, imageUrls) {
    const imgs = Array.isArray(imageUrls) ? imageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
    const total = imgs.length
    const key = dayCardCarouselKey(kind, id)
    const active = getDayCardCarouselIndex(kind, id, total)
    if (!total) {
      return `<div class="flex min-h-[220px] items-center justify-center bg-neutral-100 text-sm text-neutral-400">${esc(T("noVisual"))}</div>`
    }
    const slides = imgs
      .map(
        (url) =>
          `<div class="h-full w-full shrink-0"><img src="${esc(url)}" alt="" class="h-full w-full object-cover"/></div>`,
      )
      .join("")
    const dots =
      total > 1
        ? `<div class="absolute bottom-3 left-1/2 z-20 flex -translate-y-0 gap-1.5 -translate-x-1/2">
${imgs
  .map(
    (_, i) =>
      `<button type="button" data-act="day-card-dot" data-kind="${esc(kind)}" data-id="${esc(id)}" data-index="${i}" class="h-2 rounded-full transition-all ${i === active ? "w-4 bg-white" : "w-2 bg-white/20"}" aria-current="${i === active ? "true" : "false"}" aria-label="${i + 1}. gorsel"></button>`,
  )
  .join("")}
</div>`
        : ""
    const arrows =
      total > 1
        ? `<button type="button" data-act="day-card-prev" data-kind="${esc(kind)}" data-id="${esc(id)}" class="absolute left-2.5 top-1/2 z-20 -translate-y-1/2 rounded-full border border-white/45 bg-black/30 px-2 py-0.5 text-sm text-white backdrop-blur-[1px] transition hover:bg-black/45" aria-label="Onceki gorsel">‹</button>
<button type="button" data-act="day-card-next" data-kind="${esc(kind)}" data-id="${esc(id)}" class="absolute right-2.5 top-1/2 z-20 -translate-y-1/2 rounded-full border border-white/45 bg-black/30 px-2 py-0.5 text-sm text-white backdrop-blur-[1px] transition hover:bg-black/45" aria-label="Sonraki gorsel">›</button>`
        : ""
    const countBadge =
      total > 1
        ? `<span class="absolute right-3 top-3 z-20 rounded-full border border-white/50 bg-black/35 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm">${active + 1}/${total}</span>`
        : `<span class="absolute right-3 top-3 z-20 rounded-full border border-white/50 bg-black/35 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm">1/1</span>`
    return `
<div class="relative h-full w-full overflow-hidden bg-neutral-200" data-card-carousel="${esc(key)}" data-carousel-total="${total}" data-carousel-swipe="1">
  <div class="flex h-full w-full transition-transform duration-300" data-carousel-track style="transform:translate3d(-${active * 100}%,0,0)">${slides}</div>
  ${arrows}
  ${countBadge}
  ${dots}
</div>`
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

  function paint() {
    paintAccounts()
    /** Stüdyo açıkken takvim/gün panelini yeniden boyama: arka plandaki `refreshData` (2.5s) DOM’u silip sayfa/modal kaydırmasını ve odağı bozmasın. */
    if (!s.studioOpen) {
      paintCalendar()
      paintDayPanel()
    }
    const otherModalOpen =
      Boolean(s.contextAccountId) ||
      s.dayMenu != null ||
      s.holidaySettings != null ||
      s.accountModal ||
      s.ticketModal ||
      s.templateModal
    /** Stüdyo açıkken periyodik `paint()` iç içerik modalını yeniden yazmasın; yazı kaybolmasın (React’te modal React state ile kalır). */
    if (!s.studioOpen || otherModalOpen) paintModals()
    paintStatusAndBanner()
  }

  function paintStatusAndBanner() {
    paintTaskBanner()
    const sd = rootEl && rootEl.querySelector("#sm-seldate")
    if (sd) sd.textContent = s.selectedDate
    const st = rootEl && rootEl.querySelector("#sm-status")
    if (st && s.statusLine) st.textContent = s.statusLine
  }

  function computeServerDataSig() {
    const posts = s.posts
      .map(
        (p) =>
          `${p.id}:${p.date}:${p.time || ""}:${p.publishStatus || ""}:${p.approvalStatus || ""}:${(p.caption || "").slice(0, 48)}:${(p.imageUrl || "").slice(0, 64)}`,
      )
      .join("|")
    const drafts = s.drafts
      .map(
        (d) =>
          `${d.id}:${d.date || ""}:${(d.caption || "").slice(0, 48)}:${(d.prompt || "").slice(0, 48)}:${(d.imageUrl || "").slice(0, 64)}`,
      )
      .join("|")
    const ac = s.accounts.map((a) => `${a.id}:${(a.name || "").slice(0, 40)}:${(a.logoUrl || "").slice(0, 80)}`).join("|")
    const tk = s.tickets.map((t) => `${t.id}:${(t.name || "").slice(0, 40)}`).join("|")
    const ut = s.userTemplates.map((u) => `${u.id}:${(u.title || "").slice(0, 40)}`).join("|")
    const gt = s.globalTemplates.map((u) => `${u.id}:${(u.title || "").slice(0, 40)}`).join("|")
    const wf = s.workflows
      .map((w) => `${w.id}:${w.status || ""}:${w.scheduledFor || ""}:${w.scheduledPostId || ""}`)
      .join("|")
    const ev = s.automationEvents
      .slice(0, 40)
      .map((e) => `${e.id}:${e.eventType || ""}:${e.triggeredAt || ""}`)
      .join("|")
    return `${posts}#${drafts}#${ac}#${tk}#${ut}#${gt}#${wf}#${ev}`
  }

  return {
    computeServerDataSig,
    dayCardCarouselKey,
    getDayCardCarouselIndex,
    makeCalendarDragGhost,
    paint,
    paintAccounts,
    paintCalendar,
    paintDayPanel,
    paintShell,
    paintStatusAndBanner,
    paintTaskBanner,
    postRevisionCount,
    renderCardCarousel,
    setDayCardCarouselIndex,
    shiftDayCardCarousel,
    syncDayCardCarouselDom,
  }
}
