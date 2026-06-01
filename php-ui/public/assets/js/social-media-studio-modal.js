import { esc, T } from "./social-media-api.js"
import {
  CAMPAIGN_MODE,
  formatCampaignCatalogProviderLine,
  formatCampaignOptionLabel,
  formatCampaignStatusLabel,
  isPublishableCampaignStoreId,
  isSepetlerCampaignApiBase,
} from "./social-media-campaign-utils.js"
import { usesStoreDiscountedProductCatalog } from "./social-media-data.js"
import { DEFAULT_CAMPAIGN_API_BASE_URL } from "./social-media-constants.js"
import { getLastCampaignCatalogErrorMsg } from "./social-media-data.js"
import { CAMPAIGN_CREDENTIALS_HINT, campaignCatalogCredentialsReady } from "./social-media-selectors.js"
import { s } from "./social-media-state.js"
import { loadingDotsHtml } from "./social-media-ui.js"

/** SocialMediaComposer `PublishChannelBadges` — React ile aynı sınıflar. */
function publishChannelBadgesHtml(acc) {
  if (!acc) return ""
  const ig = Boolean(String(acc.instagramAccessToken || "").trim())
  const fb = Boolean(ig && String(acc.facebookPageId || "").trim())
  return `<span class="mt-1 flex gap-1">
<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold ${ig ? "bg-emerald-100 text-emerald-800" : "bg-neutral-200 text-neutral-500"}">IG</span>
<span class="rounded px-1.5 py-0.5 text-[10px] font-semibold ${fb ? "bg-blue-100 text-blue-800" : "bg-neutral-200 text-neutral-500"}">FB</span>
</span>`
}

/** `renderPublishAccountPicker()` ile birebir (148px kartlar, görev etiketleri, pembe seçim). */
function buildPublishAccountPickerHtml({ activeAccount, graphPublishCardKey }) {
  const accounts = s.accounts
  const active = activeAccount()
  const tok = (active?.instagramAccessToken || "").trim()
  let inner = ""
  if (accounts.length > 1) {
    inner = `<div class="flex gap-2 overflow-x-auto pb-1">${accounts
      .map((a) => {
        const sel = s.activeAccountId === a.id
        return `<button type="button" data-act="st-pick-acc" data-aid="${esc(a.id)}" class="shrink-0 max-w-[160px] rounded-xl border px-3 py-2 text-left transition ${sel ? "border-emerald-600 bg-emerald-50 ring-2 ring-emerald-200" : "border-neutral-200 bg-neutral-50 hover:border-neutral-300"}"><span class="block truncate text-xs font-medium text-neutral-900">${esc(a.name)}</span>${publishChannelBadgesHtml(a)}</button>`
      })
      .join("")}</div>`
  } else if (active) {
    inner = `<div class="flex items-start gap-2 rounded-lg bg-neutral-50 px-2 py-1.5"><div class="min-w-0"><span class="block truncate text-sm font-medium text-neutral-900">${esc(active.name)}</span>${publishChannelBadgesHtml(active)}</div></div>`
  } else {
    inner = `<p class="text-xs text-neutral-500">${esc(T("pickAccountHint"))}</p>`
  }
  let graph = ""
  if (tok) {
    const loading = s.graphPublishLoading
    const err = (s.graphPublishError || "").trim()
    const cards = s.graphPublishCards
    let body = ""
    if (loading) {
      body = `<div class="mt-3 flex items-center gap-2 text-xs text-neutral-600"><span>${esc(T("publishGraphLoading"))}</span>${loadingDotsHtml("text-neutral-500")}</div>`
    }
    if (err) body += `<p class="mt-2 text-[11px] text-red-600">${esc(err)}</p>`
    if (Array.isArray(cards) && cards.length === 0 && !loading) {
      body += `<p class="mt-2 text-[11px] text-neutral-500">${esc(T("publishGraphEmpty"))}</p>`
    }
    if (Array.isArray(cards) && cards.length > 0) {
      body += `<div class="mt-3 flex flex-wrap gap-3">${cards
        .map((c) => {
          const key = graphPublishCardKey(c)
          const sel = s.selectedGraphPublishKey === key
          const title =
            c.kind === "facebook"
              ? String(c.name || "").trim()
              : `@${String(c.username || "").trim() || c.ig_user_id}`
          const img = (c.picture_url || "").trim()
          const initials =
            c.kind === "facebook"
              ? (String(c.name || "").trim().slice(0, 2).toUpperCase() || "FB")
              : (String(c.username || "").trim().slice(0, 2).toUpperCase() || "IG")
          const kindLabel = c.kind === "facebook" ? "Facebook" : "Instagram"
          const tasks = Array.isArray(c.tasks) ? c.tasks.filter(Boolean) : []
          const taskSpans = tasks
            .slice(0, 16)
            .map(
              (task) =>
                `<span class="mb-1 mr-1 inline-block rounded bg-neutral-100 px-1 py-0.5 text-[9px] font-medium text-neutral-700">${esc(String(task))}</span>`,
            )
            .join("")
          const tasksBlock =
            tasks.length > 0
              ? `<div class="mt-2 w-full border-t border-neutral-200 pt-2 text-left"><p class="mb-1 text-[9px] font-semibold uppercase tracking-wide text-neutral-400">${esc(T("publishGraphTasksLabel"))}</p><div class="max-h-16 overflow-y-auto">${taskSpans}</div></div>`
              : ""
          const face = img
            ? `<img src="${esc(img)}" alt="" class="h-full w-full object-cover" referrerpolicy="no-referrer"/>`
            : `<div class="flex h-full w-full items-center justify-center bg-neutral-200 text-[11px] font-bold text-neutral-600">${esc(initials)}</div>`
          return `<button type="button" data-act="st-graph-card" data-key="${esc(key)}" class="flex w-[148px] flex-col rounded-xl border-2 bg-neutral-50 p-3 text-center transition ${sel ? "border-[#E1306C] bg-white shadow-sm ring-2 ring-pink-100" : "border-transparent hover:border-neutral-300"}"><div class="mx-auto h-16 w-16 shrink-0 overflow-hidden rounded-full bg-neutral-200">${face}</div><span class="mt-2 line-clamp-2 text-xs font-semibold text-neutral-900">${esc(title)}</span><span class="text-[10px] uppercase tracking-wide text-neutral-500">${kindLabel}</span>${tasksBlock}</button>`
        })
        .join("")}</div>`
    }
    graph = `<div class="border-t border-neutral-100 pt-3">
<p class="text-xs font-semibold text-neutral-700">${esc(T("publishGraphCardsSection"))}</p>
${body}</div>`
  }
  return `<div class="space-y-3 rounded-xl border border-neutral-200 bg-white p-3">
<p class="text-xs font-semibold text-neutral-700">${esc(T("publishPickAccountSection"))}</p>
${inner}${graph}
</div>`
}

export function buildStudioModalHtml(deps = {}) {
  const {
    activeAccount,
    activeCampaign,
    activeCampaignStore,
    composerModalScrollAreaClass,
    ensureCampaignMediaBoundSync,
    findCampaignBySelection,
    findRevisionBase,
    graphPublishCardKey,
    looksLikeVideoUrl,
    normalizeCampaignStudioState,
    normalizeStudioPanel,
    publishCapsStudio,
    studioRailHasVideo,
    syncAssetOrderFromCollections,
  } = deps
  ensureCampaignMediaBoundSync()
  syncAssetOrderFromCollections()
  normalizeCampaignStudioState()
  const caps = publishCapsStudio()
  if (!CAMPAIGN_MODE) {
    if (!caps.canIg) s.publishTargets = { ...s.publishTargets, instagramPost: false, instagramStory: false }
    if (!caps.canFb) s.publishTargets = { ...s.publishTargets, facebookPost: false }
  }
  const campaignStore = activeCampaignStore()
  const campaign = activeCampaign()
  const activeAcc = activeAccount()
  const sepetlerApi = isSepetlerCampaignApiBase(
    String(activeAcc?.campaignApiBaseUrl || "").trim() || DEFAULT_CAMPAIGN_API_BASE_URL,
  )
  const publishableStore = isPublishableCampaignStoreId(s.campaignStoreId)
  const storeProductMode = usesStoreDiscountedProductCatalog()
  let campaignCatalogBanner = ""
  if (CAMPAIGN_MODE) {
    if (!campaignCatalogCredentialsReady()) {
      campaignCatalogBanner = `<div class="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">${esc(CAMPAIGN_CREDENTIALS_HINT)}</div>`
    } else if (!(s.campaignStores || []).length && !(s.campaignList || []).length) {
      const err = String(getLastCampaignCatalogErrorMsg() || "").trim()
      const hint = err || "Kampanya katalogu yuklenemedi veya bos. Campaign API Base URL ve Bearer key degerlerini kontrol edin."
      campaignCatalogBanner = `<div class="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">${esc(hint)}</div>`
    } else {
      const providerLine = formatCampaignCatalogProviderLine(s.campaignCatalogProvider, s.campaignStores)
      if (providerLine) {
        campaignCatalogBanner = `<div class="mb-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-950">${esc(providerLine)}</div>`
      }
    }
  }
  const campaignMediaPreview = (() => {
    if (!campaign) return ""
    const urls = Array.isArray(campaign.media) ? campaign.media.map((u) => String(u || "").trim()).filter(Boolean) : []
    const thumb = urls[0] || ""
    const status = formatCampaignStatusLabel(campaign)
    const desc = String(campaign.description || "").trim()
    const dates = campaign.campaign_dates && typeof campaign.campaign_dates === "object" ? campaign.campaign_dates : {}
    const start = String(dates.start_date || dates.startDate || "").trim()
    const end = String(dates.end_date || dates.endDate || "").trim()
    const statusCls =
      campaign.published === true ? "bg-emerald-100 text-emerald-800" : "bg-neutral-200 text-neutral-700"
    return `<div class="rounded-xl border border-neutral-200 bg-white p-3"><div class="flex gap-3">
${thumb ? `<div class="h-20 w-32 shrink-0 overflow-hidden rounded-lg border border-neutral-200 bg-neutral-100"><img src="${esc(thumb)}" alt="" class="h-full w-full object-cover"/></div>` : `<div class="flex h-20 w-32 shrink-0 items-center justify-center rounded-lg border border-dashed border-neutral-300 bg-neutral-50 text-[10px] text-neutral-400">Gorsel yok</div>`}
<div class="min-w-0 flex-1"><div class="flex flex-wrap items-center gap-2">
<p class="truncate text-sm font-semibold text-neutral-900">${esc(String(campaign.product || campaign.id || "Kampanya"))}</p>
${status ? `<span class="rounded-full px-2 py-0.5 text-[10px] font-semibold ${statusCls}">${esc(status)}</span>` : ""}
</div>
${desc ? `<p class="mt-1 line-clamp-2 text-xs text-neutral-600">${esc(desc)}</p>` : ""}
${start || end ? `<p class="mt-1 text-[11px] text-neutral-500">API tarihleri: ${esc(start || "—")} – ${esc(end || "—")}</p>` : ""}
</div></div></div>`
  })()
  const storePublishWarning =
    sepetlerApi && s.campaignStoreId && !publishableStore
      ? `<p class="text-[11px] text-amber-800">Bu satir modul bazli gruplanmis; banner yayini icin sayisal ID'li bir magaza secin.</p>`
      : ""
  // Campaign rail is managed by ensureCampaignMediaBoundSync() called above;
  // do NOT overwrite s.campaignMediaUrls here — it would reset user-generated images.
  const igPost = s.publishTargets.instagramPost && caps.canIg
  const igStory = s.publishTargets.instagramStory && caps.canIg
  const fbPost = s.publishTargets.facebookPost && caps.canFb
  const graphLayerActive = Array.isArray(s.graphPublishCards) && s.graphPublishCards.length > 0
  const publishHint =
    !graphLayerActive && (!caps.canIg || !caps.canFb)
      ? `<p class="text-[11px] leading-snug text-neutral-500">${!caps.canIg ? esc(T("publishChannelInstagramUnavailable")) + " " : ""}${!caps.canFb ? esc(T("publishChannelFacebookUnavailable")) : ""}</p>`
      : ""
  const _studioModeStory = s.studioMode === "story"
  /** Studio modu post ise IG Post + FB; story ise sadece IG Story göster. Diğer kanallar gizlenir. */
  const socialPublishRow = _studioModeStory
    ? `
<div class="grid gap-2">
<label class="flex cursor-pointer items-center gap-2 text-xs ${caps.canIg ? "text-neutral-700" : "cursor-not-allowed text-neutral-400"}">
  <input type="checkbox" id="pt-st" class="h-4 w-4 rounded border-neutral-300 disabled:opacity-50" checked ${caps.canIg ? "" : "disabled"}/>
  Instagram Hikaye
</label>
<input type="hidden" id="pt-ig"/>
<input type="hidden" id="pt-fb"/>
</div>
${publishHint}`
    : `
<div class="grid gap-2 sm:grid-cols-2">
<label class="flex cursor-pointer items-center gap-2 text-xs ${caps.canIg ? "text-neutral-700" : "cursor-not-allowed text-neutral-400"}">
  <input type="checkbox" id="pt-ig" class="h-4 w-4 rounded border-neutral-300 disabled:opacity-50" ${igPost ? "checked" : ""} ${caps.canIg ? "" : "disabled"}/>
  Instagram Post
</label>
<label class="flex cursor-pointer items-center gap-2 text-xs ${caps.canFb ? "text-neutral-700" : "cursor-not-allowed text-neutral-400"}">
  <input type="checkbox" id="pt-fb" class="h-4 w-4 rounded border-neutral-300 disabled:opacity-50" ${fbPost ? "checked" : ""} ${caps.canFb ? "" : "disabled"}/>
  Facebook Post
</label>
<input type="hidden" id="pt-st"/>
</div>
${publishHint}`
  const campaignPublishRow = `
${campaignCatalogBanner}
<div class="space-y-3">
<div class="grid gap-3 sm:grid-cols-2">
<div>
<label class="mb-1 block text-xs text-neutral-500">Magaza (Sepetler /resources/stores)</label>
<select id="st-campaign-store" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm">
<option value="" ${!String(s.campaignStoreId || "").trim() ? "selected" : ""}>— Magaza secin —</option>
${(s.campaignStores || [])
  .map((row) => {
    const id = String(row.id || "").trim()
    return `<option value="${esc(id)}" ${String(s.campaignStoreId || "") === id ? "selected" : ""}>${esc(row.name || id || "Store")}</option>`
  })
  .join("")}
</select>
</div>
<div>
<label class="mb-1 block text-xs text-neutral-500">${
    storeProductMode ? "Indirimli urun (/resources/items)" : "Kampanya (/resources/campaigns)"
  }</label>
<select id="st-campaign-id" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm" ${
    s.campaignStoreProductsLoading ? "disabled" : ""
  }>
${
  s.campaignStoreProductsLoading
    ? `<option value="">Urunler yukleniyor...</option>`
    : Array.isArray(campaignStore?.campaigns) && campaignStore.campaigns.length
      ? campaignStore.campaigns
          .map((row) => {
            const value = String(row.id || row.product || "")
            const selected = findCampaignBySelection(campaignStore, s.campaignId) === row
            return `<option value="${esc(value)}" ${selected ? "selected" : ""}>${esc(formatCampaignOptionLabel(row))}</option>`
          })
          .join("")
      : `<option value="">${esc(s.campaignStoreProductsError || (storeProductMode ? "Once magaza secin" : "Kampanya yok"))}</option>`
}
</select>
</div>
</div>
${campaignMediaPreview}
${storePublishWarning}
<div class="grid gap-3 sm:grid-cols-2">
<div>
<label class="mb-1 block text-xs text-neutral-500">Baslangic Tarihi</label>
<input id="st-campaign-start" type="date" value="${esc(s.campaignStartDate || "")}" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm"/>
</div>
<div>
<label class="mb-1 block text-xs text-neutral-500">Bitis Tarihi</label>
<input id="st-campaign-end" type="date" value="${esc(s.campaignEndDate || "")}" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm"/>
</div>
</div>
<p class="text-[11px] leading-relaxed text-neutral-500">Banner ciktisi <b>1600×704</b>. Uretimde kampanya gorseli sol rail'e eklenir.${
    storeProductMode
      ? " Magaza secince indirimli urunler listelenir; fiyat/indirim banner'a aktarilir. Yayin: POST /banners."
      : sepetlerApi
        ? " Yayin: Sepetler POST /banners (secili magazaya site banner'i)."
        : " Yayin: Campaign API publish ucu."
  }</p>
</div>`
  const approvalBlock = CAMPAIGN_MODE
    ? `
<div class="space-y-3 rounded-xl border border-neutral-200 bg-neutral-50 p-3">
  <div class="flex items-center justify-between gap-3 text-sm font-medium text-neutral-800">
    <span>Onay durumu</span>
    <div class="flex items-center gap-2">
      <button type="button" data-act="st-toggle-appr" role="switch" class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
        s.composerApproved ? "bg-emerald-600" : "bg-neutral-300"
      }" aria-checked="${s.composerApproved ? "true" : "false"}">
        <span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${
          s.composerApproved ? "translate-x-5" : "translate-x-1"
        }"></span>
      </button>
      <span class="text-xs text-neutral-600">${s.composerApproved ? "Onaylandi" : "Onaylanmadi"}</span>
    </div>
  </div>
  ${campaignPublishRow}
</div>`
    : `
<div class="space-y-3 rounded-xl border border-neutral-200 bg-neutral-50 p-3">
  <div class="flex items-center justify-between gap-3 text-sm font-medium text-neutral-800">
    <span>Onay durumu</span>
    <div class="flex items-center gap-2">
      <button type="button" data-act="st-toggle-appr" role="switch" class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
        s.composerApproved ? "bg-emerald-600" : "bg-neutral-300"
      }" aria-checked="${s.composerApproved ? "true" : "false"}">
        <span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${
          s.composerApproved ? "translate-x-5" : "translate-x-1"
        }"></span>
      </button>
      <span class="text-xs text-neutral-600">${s.composerApproved ? "Onaylandi" : "Onaylanmadi"}</span>
    </div>
  </div>
  ${socialPublishRow}
</div>`
  const savePublishRow = CAMPAIGN_MODE
    ? `
<div class="flex flex-wrap gap-2">
  <button type="button" data-act="st-save" class="rounded-xl bg-neutral-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-neutral-800">${esc(T("saveCalendar"))}</button>
  <button type="button" data-act="st-publish-campaign" class="inline-flex min-h-[2.75rem] items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60" ${
    s.studioPublishBusy || s.composerBusy || !publishableStore ? "disabled" : ""
  } title="${!publishableStore ? "Banner yayini icin sayisal magaza ID secin" : ""}">${
    s.studioPublishBusy ? `Kampanya banner yayinlaniyor ${loadingDotsHtml("text-emerald-100")}` : "Kampanya Banner Olarak Yayinla"
  }</button>
</div>`
    : `
<div class="flex flex-wrap gap-2">
  <button type="button" data-act="st-save" class="rounded-xl bg-neutral-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-neutral-800">${esc(T("saveCalendar"))}</button>
  <button type="button" data-act="st-publish-ig" class="inline-flex min-h-[2.75rem] items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60" ${
    s.studioPublishBusy || s.composerBusy ? "disabled" : ""
  }>${
    s.studioPublishBusy
      ? `${esc(T("publishInstagramWorking"))} ${loadingDotsHtml("text-emerald-100")}`
      : esc(T("publishInstagram"))
  }</button>
</div>`
  const dialogShell =
    "max-h-[94vh] w-full max-w-6xl overflow-x-hidden overflow-y-auto overscroll-contain rounded-[28px] bg-white shadow-2xl [scrollbar-width:thin] [scrollbar-color:#a3a3a3_#f5f5f5] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-neutral-400 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-neutral-100"
  const composerRoot = "flex min-h-0 w-full flex-1 flex-col overflow-hidden"
  const scrollArea = composerModalScrollAreaClass()
  const fileAccept = s.visualOutputKind === "video" ? "image/*,video/*" : "image/*"
  const railVid = studioRailHasVideo()
  const vidDur = Math.min(15, Math.max(3, Math.round(Number(s.videoDurationSec) || 5)))
  const aa = activeAccount()
  const accountTabLine = CAMPAIGN_MODE
    ? (campaignStore ? esc(campaignStore.name || "Campaign Store") : "Kampanya modu")
    : (aa ? esc(aa.name) : esc(T("pickAccountHint")))
  const mp = normalizeStudioPanel(s.modalPanel || "caption")
  /** Kampanya modunda seçim olmadan Üret/Revize butonları kilitli. */
  const campaignSelectionMissing = CAMPAIGN_MODE && !String(s.campaignId || "").trim()
  const reviseDisabled = s.composerBusy || campaignSelectionMissing
  const reviseTitle = campaignSelectionMissing ? "Önce Yayınla sekmesinden bir kampanya seçin" : ""
  const activeRevisionBase = findRevisionBase(s.imageUrl)
  const activeRevisionList = activeRevisionBase
    ? ((s.revisionMap && s.revisionMap[activeRevisionBase]) || []).map((u) => String(u || "").trim()).filter(Boolean)
    : []
  const busyPreview = s.composerBusy
    ? `<div class="pointer-events-none absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-black/35 backdrop-blur-[0.5px]" aria-live="polite"><span class="max-w-[min(100%,20rem)] rounded-lg bg-white/95 px-3 py-2 text-center text-xs font-semibold text-amber-950 shadow-md">${esc(T("composerVisualGenerateWorking"))}</span>${loadingDotsHtml("text-white drop-shadow-md")}</div>`
    : ""
  const previewInner = (s.imageUrl || "").trim()
    ? looksLikeVideoUrl(s.imageUrl)
      ? `<video src="${esc(s.imageUrl)}" controls class="h-full w-full object-contain"></video>`
      : `<img src="${esc(s.imageUrl)}" alt="" class="h-full w-full object-contain"/>`
    : `<div class="text-sm text-neutral-400">${esc(T("noVisual"))}</div>`
  const storyAspectPreview =
    !CAMPAIGN_MODE && igStory && (s.imageUrl || "").trim() && !looksLikeVideoUrl(s.imageUrl)
  const previewBlock = storyAspectPreview
    ? `<div class="relative mx-auto h-full max-h-full w-auto max-w-full overflow-hidden rounded-xl border-2 border-neutral-800 bg-black shadow-md [aspect-ratio:9/16]">${previewInner}</div>`
    : previewInner
  const activeRevisionIdx = activeRevisionList.length > 1
    ? activeRevisionList.indexOf((s.imageUrl || "").trim())
    : -1
  const revisionPositionBadge = activeRevisionList.length > 1 && activeRevisionIdx >= 0
    ? `<span class="pointer-events-none absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded-full bg-white/90 px-2 py-0.5 text-[10px] font-semibold text-neutral-700 shadow-sm">${activeRevisionIdx + 1}/${activeRevisionList.length}</span>`
    : ""
  /** Aktif görsel base'in kendisi DEĞİL (yani revize variant'ı) ise preview'in sol altına çöp kutusu çıkar. */
  const activeIsRevisionVariant =
    activeRevisionList.length > 1 && activeRevisionBase && (s.imageUrl || "").trim() !== String(activeRevisionBase || "").trim()
  const revisionDeleteBtn = activeIsRevisionVariant
    ? `<button type="button" data-act="st-rev-remove" data-url="${esc((s.imageUrl || "").trim())}" class="absolute bottom-2 left-2 z-10 inline-flex h-7 w-7 items-center justify-center rounded-md bg-red-50 text-red-600 shadow-sm hover:bg-red-100" title="Bu revizyonu sil" aria-label="Bu revizyonu sil">🗑</button>`
    : ""
  const revisionSliderUi =
    activeRevisionList.length > 1
      ? `<button type="button" data-act="st-rev-prev" class="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded-full border border-neutral-300 bg-white/90 px-2 py-1 text-sm text-neutral-700 hover:bg-white" aria-label="Onceki revize">‹</button>
${revisionPositionBadge}
${revisionDeleteBtn}
<button type="button" data-act="st-rev-next" class="absolute right-2 top-1/2 z-10 -translate-y-1/2 rounded-full border border-neutral-300 bg-white/90 px-2 py-1 text-sm text-neutral-700 hover:bg-white" aria-label="Sonraki revize">›</button>`
      : ""

  const modalAssets = [...(s.assetOrder || [])].map((x) => String(x || "").trim()).filter(Boolean)
  const railItems =
    s.campaignMediaLoading
      ? `<div class="space-y-2">
<div class="h-20 w-full animate-pulse rounded-xl border border-neutral-200 bg-neutral-200"></div>
<div class="h-20 w-full animate-pulse rounded-xl border border-neutral-200 bg-neutral-200"></div>
</div>`
      : modalAssets.length === 0
        ? `<div class="rounded-xl border border-neutral-200 bg-white px-3 py-4 text-center text-xs text-neutral-400">${esc(T("noVisual"))}</div>`
      : modalAssets
          .map((asset) => {
            const isVideo = looksLikeVideoUrl(asset)
            const dimmed = railVid && !isVideo
            const canDragRail = !railVid && !dimmed
            const dragCls = canDragRail ? "cursor-grab active:cursor-grabbing" : ""
            const checked = (s.referenceCheckedUrls || []).includes(asset)
            const displayAsset = String((s.selectedRevisionByBase && s.selectedRevisionByBase[asset]) || asset || "").trim()
            const isSel = (s.imageUrl || "").trim() === displayAsset
            const dragHandle = canDragRail
              ? `<span class="absolute right-1.5 top-1.5 z-20 inline-flex h-6 w-6 cursor-grab items-center justify-center rounded-md border border-neutral-200 bg-white/95 text-[13px] font-semibold text-neutral-500 shadow-sm active:cursor-grabbing" title="Sırayı değiştirmek için sürükle" aria-hidden="true">↕</span>`
              : ""
            return `<div data-drag-rail="${esc(asset)}" draggable="${canDragRail}" title="${canDragRail ? "Sırayı değiştirmek için sürükle" : ""}" class="${dragCls} relative w-full rounded-xl border-2 bg-white ${
              isSel ? "border-neutral-900" : "border-neutral-200"
            } ${dimmed ? "pointer-events-none opacity-45 grayscale" : ""}">
<label class="absolute left-1.5 top-1.5 z-30 flex h-6 w-6 items-center justify-center rounded-md border border-neutral-300 bg-white/95 shadow-sm ${
              dimmed ? "pointer-events-none opacity-60" : "cursor-pointer"
            }">
<input type="checkbox" data-act="st-ref-url-cb" data-url="${esc(asset)}" class="h-4 w-4 rounded border-neutral-400 text-emerald-600" ${
              checked ? "checked" : ""
            } ${dimmed ? "disabled" : ""}/>
</label>
<button type="button" data-act="st-rail-select" data-url="${esc(asset)}" class="block w-full rounded-xl ${dimmed ? "cursor-not-allowed" : ""}" ${
              dimmed ? "disabled" : ""
            }>
${isVideo ? `<video src="${esc(displayAsset)}" muted playsInline class="h-24 w-full rounded-xl object-cover"></video>` : `<img src="${esc(displayAsset)}" alt="" draggable="false" class="h-24 w-full rounded-xl object-contain"/>`}
</button>
${dragHandle}
${
  isVideo || !dimmed
    ? `<button type="button" data-act="st-rail-remove" data-url="${esc(asset)}" class="absolute bottom-1.5 left-1.5 inline-flex h-6 w-6 items-center justify-center rounded-md bg-red-50 text-red-600 shadow-sm hover:bg-red-100" title="Sil" aria-label="Sil">🗑</button>`
    : ""
}
</div>`
          })
          .join("")

  const tabBtn = (id, label) =>
    `<button type="button" data-act="st-modal-panel" data-panel="${id}" class="rounded-lg px-3 py-1.5 text-xs font-semibold ${
      mp === id ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
    }">${esc(label)}</button>`

  const videoHolidayBlock =
    s.visualOutputKind === "video"
      ? `<div class="space-y-3 rounded-xl border border-neutral-200 bg-white p-3">
  <div class="flex flex-wrap gap-2">
    ${(
      [
        ["text", T("composerVideoModeText")],
        ["reference", T("composerVideoModeRef")],
        ["holiday", T("composerVideoModeHoliday")],
      ]
    )
      .map(
        ([id, lab]) =>
          `<button type="button" data-act="st-video-ai-mode" data-mode="${id}" class="rounded-lg px-3 py-1.5 text-xs font-semibold ${s.videoAiMode === id ? "bg-neutral-900 text-white" : "border border-neutral-200 bg-neutral-50 text-neutral-600"}">${esc(lab)}</button>`,
      )
      .join("")}
  </div>
  <div class="rounded-lg border border-neutral-100 bg-neutral-50 px-2 py-2">
    <div class="mb-1.5 flex items-center justify-between gap-2">
      <span class="text-xs font-medium text-neutral-700">${esc(T("composerVideoDurationLabel"))}</span>
      <span class="text-xs font-mono tabular-nums text-neutral-600">${vidDur}s</span>
    </div>
    <input id="st-video-dur" type="range" min="3" max="15" step="1" value="${vidDur}" ${railVid ? "disabled" : ""} class="mb-3 h-2 w-full cursor-pointer accent-emerald-600 disabled:opacity-40"/>
    <div class="flex items-center justify-between gap-2">
      <span class="text-xs font-medium text-neutral-700">${esc(T("composerVideoAudioLabel"))}</span>
      <button type="button" data-act="st-video-audio-toggle" role="switch" aria-checked="${s.videoGenerateAudio ? "true" : "false"}" ${railVid ? "disabled" : ""} class="rounded-full px-3 py-1 text-[11px] font-semibold disabled:opacity-40 ${s.videoGenerateAudio ? "bg-emerald-600 text-white" : "bg-neutral-200 text-neutral-700"}">${esc(s.videoGenerateAudio ? T("composerVideoAudioOn") : T("composerVideoAudioOff"))}</button>
    </div>
  </div>
  ${
    s.videoAiMode === "text"
      ? `<textarea id="st-direct-prompt" rows="4" class="w-full rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none focus:border-neutral-400" placeholder="${esc(T("visualPrompt"))}">${esc(s.directImagePrompt)}</textarea>
<button type="button" data-act="st-gen-video-text" class="w-full rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40" ${s.composerBusy || railVid || !s.directImagePrompt.trim() ? "disabled" : ""}>${esc(T("generate"))}</button>`
      : s.videoAiMode === "reference"
        ? `<p class="text-xs text-neutral-600">${esc(T("composerVideoNeedRef"))}</p>
<textarea id="st-direct-prompt" rows="3" class="w-full rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none focus:border-neutral-400" placeholder="${esc(T("composerVideoMotionHint"))}">${esc(s.directImagePrompt)}</textarea>
<button type="button" data-act="st-gen-video-ref" class="w-full rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40" ${s.composerBusy || railVid ? "disabled" : ""}>${esc(T("generate"))}</button>`
        : `<input id="st-holiday-video-name" type="text" value="${esc(s.holidayVideoName)}" class="w-full rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm" placeholder="${esc(T("composerVideoHolidayName"))}"/>
<div>
<label class="mb-1 block text-xs text-neutral-500">${esc(T("composerVideoHolidayDate"))}</label>
<input id="st-holiday-video-date" type="date" value="${esc(s.holidayVideoDate)}" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm"/>
</div>
<button type="button" data-act="st-gen-video-holiday" class="w-full rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-40" ${s.composerBusy || railVid ? "disabled" : ""}>${esc(T("generate"))}</button>`
  }
</div>`
      : ""

  const imageDirectBlock =
    s.visualOutputKind === "image"
      ? `<div class="space-y-3">
${CAMPAIGN_MODE ? "" : `<div class="flex gap-1 rounded-xl border border-neutral-200 bg-neutral-100 p-1">
<button type="button" data-act="st-gen-sub-manual" class="flex-1 rounded-lg py-1.5 text-xs font-medium transition ${s.generateSubTab === "manual" ? "bg-white text-neutral-900 shadow" : "text-neutral-500 hover:text-neutral-700"}">${esc(T("manual"))}</button>
<button type="button" data-act="st-gen-sub-ticket" class="flex-1 rounded-lg py-1.5 text-xs font-medium transition ${s.generateSubTab === "ticket" ? "bg-white text-neutral-900 shadow" : "text-neutral-500 hover:text-neutral-700"}">🏷 Etiket${s.tickets.length ? ` <span class="ml-1 rounded-full bg-neutral-200 px-1.5 text-[10px] font-bold">${s.tickets.length}</span>` : ""}</button>
</div>`}
${
  s.generateSubTab === "manual"
    ? `<textarea id="st-direct-prompt" rows="3" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-400" placeholder="${esc(T("visualPrompt"))}">${esc(s.directImagePrompt)}</textarea>
<div class="rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2">
<div class="flex items-center justify-between">
<span class="text-xs text-neutral-700">Seçilmiş ürünleri referans al</span>
<button type="button" data-act="st-toggle-use-ref" role="switch" aria-checked="${s.useSelectedAsReference ? "true" : "false"}" class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${s.useSelectedAsReference ? "bg-emerald-600" : "bg-neutral-300"}">
<span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${s.useSelectedAsReference ? "translate-x-5" : "translate-x-1"}"></span>
</button>
</div>
<p class="mt-1 text-[11px] text-neutral-500">Açık: solda işaretli görselleri çoklu referans olarak kullanır. Kapalı: yalnızca metinle serbest üretir.</p>
</div>
<button type="button" data-act="st-gen-direct" title="${esc(reviseTitle)}" class="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50" ${reviseDisabled ? "disabled" : ""}>${esc(T("generate"))}</button>`
    : s.tickets.length === 0
      ? `<p class="rounded-xl border border-dashed border-neutral-200 py-8 text-center text-xs text-neutral-400">Henüz etiket yok. Takvim üstündeki 🏷 Etiketler butonundan ekleyin.</p>`
      : `<div class="grid max-h-48 grid-cols-2 gap-2 overflow-y-auto pr-0.5 sm:grid-cols-3">
${s.tickets
  .map(
    (tk) =>
      `<button type="button" data-act="st-pick-ticket" data-tid="${esc(tk.id)}" class="w-full rounded-xl border px-3 py-2.5 text-left transition ${s.selectedTicketId === tk.id ? "border-neutral-900 bg-neutral-900 text-white" : "border-neutral-200 bg-white text-neutral-800 hover:border-neutral-300 hover:bg-neutral-50"}"><span class="line-clamp-1 text-sm font-medium">${esc(tk.name)}</span></button>`,
  )
  .join("")}
</div>
<div class="rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2">
<div class="flex items-center justify-between">
<span class="text-xs text-neutral-700">Seçilmiş ürünleri referans al</span>
<button type="button" data-act="st-toggle-use-ref" role="switch" aria-checked="${s.useSelectedAsReference ? "true" : "false"}" class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${s.useSelectedAsReference ? "bg-emerald-600" : "bg-neutral-300"}">
<span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${s.useSelectedAsReference ? "translate-x-5" : "translate-x-1"}"></span>
</button>
</div>
<p class="mt-1 text-[11px] text-neutral-500">Soldaki kutucuklarla hangi görsellerin referans olacağını seçin.</p>
</div>
<button type="button" data-act="st-gen-direct" title="${esc(reviseTitle)}" class="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50" ${reviseDisabled ? "disabled" : ""}>${esc(T("generate"))}</button>`
}
</div>`
      : ""

  const panelCaption = `<div class="space-y-3">
<textarea id="st-cap" rows="6" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-400" placeholder="${esc(T("contentPlaceholder"))}">${esc(s.caption)}</textarea>
<div class="grid gap-2 sm:grid-cols-2">
<input id="st-flow-topic" type="text" value="${esc(s.lastTopic)}" placeholder="${esc(T("topic"))}" class="rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-400"/>
<button type="button" data-act="st-gen-caption" class="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50" ${s.composerBusy ? "disabled" : ""}>${esc(T("generateContent"))}</button>
</div>
</div>`

  const reviseReady = Boolean((s.imageUrl || "").trim()) && !looksLikeVideoUrl((s.imageUrl || "").trim())
  /** Sosyal medya modunda Studio modu (post/story) ile şablonun outputSize'ı eşleşmeli. CAMPAIGN_MODE'da filtre yok. */
  const _tplFilter = CAMPAIGN_MODE
    ? () => true
    : s.studioMode === "story"
      ? (tpl) => String(tpl?.outputSize || "") === "story"
      : (tpl) => String(tpl?.outputSize || "post_4_5") !== "story"
  const reviseTemplateRows = [
    ...(s.globalTemplates || []).filter(_tplFilter).map((tpl) => ({ scope: "global", tpl })),
    ...(s.userTemplates || []).filter(_tplFilter).map((tpl) => ({ scope: "user", tpl })),
  ]
  const reviseTemplateCards = reviseTemplateRows.length
    ? `<div class="flex gap-2 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:thin]">${
      reviseTemplateRows
        .map((row) => {
          const tpl = row.tpl || {}
          const first = String((tpl.imageUrls || [])[0] || "").trim()
          const scope = String(row.scope || "user")
          const selected =
            String(s.selectedTemplateId || "") === String(tpl.id || "") &&
            String(s.selectedTemplateScope || "user") === scope
          return `<div class="w-[5.5rem] shrink-0">
<button type="button" data-act="st-apply-template" data-id="${esc(tpl.id)}" data-scope="${esc(scope)}" title="${esc(tpl.title || "")}" class="flex w-full flex-col overflow-hidden rounded-lg border-2 bg-white text-left shadow-sm transition ${selected ? "border-violet-600 ring-2 ring-violet-200" : "border-neutral-200 hover:border-violet-300"}">
${first ? `<img src="${esc(first)}" alt="" class="aspect-square w-full object-cover"/>` : `<div class="flex aspect-square w-full items-center justify-center bg-neutral-100 text-[10px] text-neutral-400">—</div>`}
<span class="line-clamp-2 px-1 py-0.5 text-[9px] font-medium text-neutral-800">${esc(tpl.title || "—")}</span>
</button>
${scope === "user" ? `<button type="button" data-act="st-edit-template" data-id="${esc(tpl.id)}" class="mt-1 w-full rounded-md border border-neutral-200 bg-white px-1 py-1 text-[10px] text-neutral-700 hover:bg-neutral-50">Duzenle</button>` : `<span class="mt-1 inline-flex w-full items-center justify-center rounded-md border border-neutral-100 bg-neutral-50 px-1 py-1 text-[10px] text-neutral-500">Global</span>`}
</div>`
        })
        .join("")
    }</div>`
    : `<div class="rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-4 text-center">
<p class="text-sm font-medium text-neutral-800">Henuz sablon olusturulmadi</p>
<p class="mt-1 text-xs text-neutral-500">${CAMPAIGN_MODE ? "Ilk kampanya uretim sablonunu olustur." : "Ilk AI revize sablonunu olustur."}</p>
<button type="button" data-act="open-templates" class="mt-2 rounded-lg border border-neutral-200 bg-white px-3 py-1.5 text-xs font-semibold text-neutral-700 hover:bg-neutral-100">Sablon Olustur</button>
</div>`
  const reviseActionLabel = CAMPAIGN_MODE ? "Kampanya Banner Revize Et" : "Görseli Revize Et"
  const reviseTemplateHint = CAMPAIGN_MODE
    ? "Secilen sablonun prompt ve gorselleri uretim istegine referans olarak eklenir."
    : "Secilen sablonun prompt ve gorselleri revize istegine referans olarak eklenir."
  const panelRevise = `<div class="space-y-3">
<div class="rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2">
<div class="flex items-center justify-between gap-2">
<span class="text-xs text-neutral-700">Seçilenleri referans al</span>
<button type="button" data-act="st-toggle-revise-refs" role="switch" aria-checked="${s.useSelectedRefsForRevise ? "true" : "false"}" class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${s.useSelectedRefsForRevise ? "bg-emerald-600" : "bg-neutral-300"}">
<span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${s.useSelectedRefsForRevise ? "translate-x-5" : "translate-x-1"}"></span>
</button>
</div>
<p class="mt-1 text-[11px] text-neutral-500">Açıkken solda işaretli görseller revizyonda ek ürün bağlamı olarak kullanılır.</p>
</div>
<textarea id="st-revise-fb" rows="4" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-400" placeholder="${esc(T("phReviseVisualFeedback"))}">${esc(s.reviseFeedback)}</textarea>
<div class="rounded-xl border border-neutral-200 bg-[#fcfcfe] p-3">
<div class="mb-2 flex items-center justify-between">
<p class="text-xs font-semibold tracking-wide text-neutral-600">Sablonlar</p>
<div class="flex items-center gap-1.5">
${s.selectedTemplateId ? `<button type="button" data-act="st-clear-template" class="rounded-lg border border-violet-200 bg-white px-2 py-1 text-[10px] font-medium text-violet-800 hover:bg-violet-50">Secimi temizle</button>` : ""}
<button type="button" data-act="open-templates" class="rounded-lg border border-neutral-200 bg-white px-2 py-1 text-[11px] font-semibold text-neutral-700 hover:bg-neutral-100">Yonet</button>
</div>
</div>
${reviseTemplateCards}
<p class="mt-2 text-[10px] leading-snug text-neutral-500">${reviseTemplateHint}</p>
</div>
<button type="button" data-act="st-revise-image" title="${esc(reviseTitle)}" class="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50" ${reviseDisabled ? "disabled" : ""}>${reviseActionLabel}</button>
</div>`

  const panelGenerate = `<div class="space-y-3">
<div class="flex gap-1 rounded-xl border border-neutral-200 bg-neutral-50 p-1">
<button type="button" data-act="st-vis-kind-img" class="flex-1 rounded-lg py-1.5 text-xs font-semibold transition ${s.visualOutputKind === "image" ? "bg-white shadow text-neutral-900" : "text-neutral-500 hover:text-neutral-700"}">${esc(T("composerMediaKindImage"))}</button>
<button type="button" data-act="st-vis-kind-vid" class="flex-1 rounded-lg py-1.5 text-xs font-semibold transition ${s.visualOutputKind === "video" ? "bg-white shadow text-neutral-900" : "text-neutral-500 hover:text-neutral-700"}">${esc(T("composerMediaKindVideo"))}</button>
</div>
${videoHolidayBlock}
${imageDirectBlock}
</div>`

  const postCostUsd = Number(s.editingPostCost || s.activeDraftCost || 0)
  const costBlock = postCostUsd > 0
    ? `<div class="rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">Bu içeriğin tahmini maliyeti: <b>${postCostUsd.toFixed(postCostUsd >= 1 ? 2 : 4)} USD</b></div>`
    : ""
  const panelPublish = `<div class="space-y-4">
${CAMPAIGN_MODE ? "" : buildPublishAccountPickerHtml({ activeAccount, graphPublishCardKey })}
<div class="grid gap-3 sm:grid-cols-2">
<div>
<label class="mb-1 block text-xs text-neutral-500">${esc(T("calendarDay"))}</label>
<input id="st-date" type="date" value="${esc(s.selectedDate)}" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm"/>
</div>
<div>
<label class="mb-1 block text-xs text-neutral-500">${esc(T("time"))}</label>
<input id="st-time" type="time" value="${esc(s.scheduledTime)}" class="w-full rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm"/>
</div>
</div>
${costBlock}
${approvalBlock}
${savePublishRow}
</div>`

  let panelBody = ""
  if (CAMPAIGN_MODE) panelBody = mp === "publish" ? panelPublish : panelRevise
  else if (mp === "caption") panelBody = panelCaption
  else if (mp === "generate") panelBody = panelGenerate
  else if (mp === "revise") panelBody = panelRevise
  else panelBody = panelPublish

  const studioTabs = CAMPAIGN_MODE
    ? `${tabBtn("revise", "Üret")}${tabBtn("publish", "Yayınla")}`
    : `${tabBtn("generate", "Üret")}${tabBtn("caption", "Açıklama")}${tabBtn("revise", "Revize")}${tabBtn("publish", "Yayınla")}`

  const statusBlock = (s.statusLine || "").trim()
    ? `<div class="mt-4 rounded-xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-700">${esc(s.statusLine)}</div>`
    : ""

  return `<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" data-act="close-studio-bg">
<div class="${dialogShell}" data-stop="1" role="dialog" aria-modal="true">
<div class="${composerRoot}">
<div class="shrink-0 border-b border-neutral-200 bg-[#fafaf8] px-4 py-3 md:px-6">
<div class="mx-auto flex max-w-6xl items-center justify-between gap-3">
<div class="min-w-0">
<p class="text-[11px] font-semibold uppercase tracking-[0.2em] text-neutral-400">${esc(CAMPAIGN_MODE ? "Kampanya Oluştur" : T("createHeader"))}</p>
</div>
<button type="button" data-act="close-studio" class="shrink-0 rounded-full border border-neutral-200 bg-white p-2 text-neutral-500 hover:bg-neutral-50" aria-label="${esc(T("close"))}"><span class="block text-lg leading-none">×</span></button>
</div>
</div>
<input id="st-file" type="file" accept="${esc(fileAccept)}" multiple class="hidden"/>
<div class="min-h-0 flex-1 bg-white">
<div class="mx-auto flex h-[min(76vh,820px)] min-h-[320px] w-full max-w-6xl overflow-hidden md:min-h-[400px]">
<div data-act="st-rail-drop-zone" class="flex min-h-0 w-52 shrink-0 flex-col border-r border-neutral-200 bg-neutral-50 p-3">
<button type="button" ${s.composerBusy || (s.visualOutputKind === "video" && railVid) ? "disabled" : ""} data-act="st-upload" class="mb-3 w-full shrink-0 rounded-xl border border-dashed border-neutral-300 bg-white px-3 py-3 text-xs font-medium text-neutral-600 hover:bg-neutral-100 disabled:opacity-50">${esc(T("pickFile"))}</button>
<p class="mb-2 shrink-0 text-[10px] text-neutral-500">Ctrl+V / Sürükle-bırak / Çoklu seçim</p>
<div class="min-h-0 flex-1 space-y-2 pr-1 ${scrollArea}">${railItems}${
    s.composerBusy
      ? `<div class="space-y-1.5"><p class="text-[10px] font-semibold leading-tight text-amber-900">${esc(T("composerVisualGenerateWorking"))}</p><div class="animate-pulse rounded-xl border-2 border-neutral-200 bg-white p-2"><div class="h-20 w-full rounded-lg bg-neutral-200"></div></div></div>`
      : ""
  }</div>
</div>
<div class="flex min-h-0 flex-1 flex-col">
<div class="relative border-b border-neutral-200 bg-neutral-100">
<div class="flex h-64 items-center justify-center bg-neutral-100">${previewBlock}</div>
${revisionSliderUi}
${busyPreview}
</div>
<div class="flex items-center justify-between border-b border-neutral-200 bg-white px-4 py-2.5">
<div class="flex flex-wrap gap-1">${studioTabs}</div>
<p class="text-xs text-neutral-500">${accountTabLine}</p>
</div>
<div class="min-h-0 flex-1 p-4 ${scrollArea}">${panelBody}${statusBlock}</div>
</div>
</div>
</div>
</div></div></div>`
}
