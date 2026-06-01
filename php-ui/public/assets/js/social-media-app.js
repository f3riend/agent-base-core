import { primeHolidays, getHolidayLabelsSync } from "./social-media-holidays.js"
import {
  LABEL_COLORS,
  PENDING_POLL_MS,
  PENDING_TASKS_STORAGE_KEY,
} from "./social-media-constants.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import {
  clearDebugEvents,
  debugEvents,
  debugLog,
  debugStatus,
  flushDebugLogs,
  loadPendingTasks,
  readDebugMode,
  writeDebugMode,
} from "./social-media-runtime.js"
import {
  holidayWatchStorageKey,
  pendingPollTimer,
  rootEl,
  s,
  setPendingPollTimer,
  setRootEl,
} from "./social-media-state.js"
import { campaignLoadCatalog } from "./social-media-data.js"
import { registerCalendarDelegation } from "./social-media-calendar-events.js"
import {
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  CAMPAIGN_CREDENTIALS_HINT,
  campaignCatalogCredentialsReady,
  campaignMediaList,
  campaignTemplateCollections,
  findCampaignBySelection,
  localeTag,
  normalizeCampaignStudioState,
  normalizeStudioPanel,
  user,
} from "./social-media-selectors.js"
import { createBackgroundTasks } from "./social-media-background-tasks.js"
import { createComposerActions } from "./social-media-composer-actions.js"
import { createDelegatedClickHandler } from "./social-media-actions.js"
import { createDelegatedChangeHandler } from "./social-media-change-handler.js"
import { createPersistenceActions } from "./social-media-persistence.js"
import { createPostPreviewController } from "./social-media-post-preview.js"
import { createSocialMediaRenderer } from "./social-media-render.js"
import { createStudioHelpers } from "./social-media-studio-helpers.js"
import { createModalHelpers } from "./social-media-modal-helpers.js"
import { baseForRevisionUrl, formatDateKey } from "./social-media-post-utils.js"
import { T } from "./social-media-api.js"
import { draftsCollection } from "./social-media-campaign-utils.js"
import { socialDelete, socialPatchFields } from "./social-media-data.js"
import { CAMPAIGN_SCHEDULED_POSTS, SCHEDULED_POSTS } from "./social-media-constants.js"

const {
  hidePostPreview,
  registerPostPreviewHover,
} = createPostPreviewController()

let appendAiUrls
let applyGeneratedVideoUrl
let applyTemplateToReviseWithScope
let composerModalScrollAreaClass
let findReviseTemplate
let findRevisionBase
let loadDraftIntoStudio
let loadPostIntoStudio
let looksLikeVideoUrl
let openStudioManual
let publishCapsStudio
let removeRailAsset
let selectedReviseTemplate
let studioUploadFilesFromFileList
let syncAssetOrderFromCollections
let templateUploadFilesFromFileList
let uploadFilesToStorage

const {
  maybeFetchGraphPublishCards,
  paintModals,
  syncOpenModalsFromDom,
} = createModalHelpers({
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  composerModalScrollAreaClass: (...args) => composerModalScrollAreaClass(...args),
  ensureCampaignMediaBoundSync,
  findCampaignBySelection,
  findRevisionBase: (...args) => findRevisionBase(...args),
  graphPublishCardKey,
  looksLikeVideoUrl: (...args) => looksLikeVideoUrl(...args),
  normalizeCampaignStudioState,
  normalizeStudioPanel,
  publishCapsStudio: (...args) => publishCapsStudio(...args),
  studioRailHasVideo,
  syncAssetOrderFromCollections: (...args) => syncAssetOrderFromCollections(...args),
})

const {
  computeServerDataSig,
  dayCardCarouselKey,
  makeCalendarDragGhost,
  paint,
  paintCalendar,
  paintDayPanel,
  paintShell,
  paintStatusAndBanner,
  paintTaskBanner,
  setDayCardCarouselIndex,
  shiftDayCardCarousel,
  syncDayCardCarouselDom,
} = createSocialMediaRenderer({
  paintModals,
})

function graphPublishCardKey(card) {
  if (!card || typeof card !== "object") return ""
  return card.kind === "facebook"
    ? `facebook:${String(card.page_id || "").trim()}`
    : `instagram:${String(card.ig_user_id || "").trim()}`
}

/** Seçili Graph kartına göre yayın gövdesine eklenecek kimlikler (React `publishIds`). */
function graphPublishIdsForPostBody() {
  const cards = s.graphPublishCards
  const key = (s.selectedGraphPublishKey || "").trim()
  if (!Array.isArray(cards) || !cards.length || !key) return {}
  const c = cards.find((x) => graphPublishCardKey(x) === key)
  if (!c) return {}
  if (c.kind === "instagram") {
    const ig = String(c.ig_user_id || "").trim()
    return ig ? { instagram_user_id: ig } : {}
  }
  if (c.kind === "facebook") {
    const fb = String(c.page_id || "").trim()
    return fb ? { facebook_page_id: fb } : {}
  }
  return {}
}

function studioRailHasVideo() {
  const urls = [...(s.uploadedImageUrls || []), ...(s.aiImageUrls || []), s.imageUrl]
    .map((x) => String(x || "").trim())
    .filter(Boolean)
  return urls.some((u) => looksLikeVideoUrl(u))
}

function getPromptProThreshold() {
  try {
    const raw = localStorage.getItem("app_settings_prompt_professionalization_threshold")
    if (!raw) return 300
    const n = Number(raw)
    return Number.isFinite(n) ? Math.max(0, Math.min(3000, Math.round(n))) : 300
  } catch {
    return 300
  }
}

/** `SocialMediaComposer` generateCaptionNow: rail görsellerini konuya ek bağlam olarak ekle */
function buildCaptionImageContextForKonu() {
  const order = s.assetOrder || []
  const urls = order
    .map((base) =>
      String((s.selectedRevisionByBase && s.selectedRevisionByBase[base]) || base || "").trim(),
    )
    .filter(Boolean)
  if (!urls.length) return ""
  const lines = urls.map((u, i) => `${i + 1}) ${u}`).join("\n")
  return `\n\nGorsel baglami (URL):\n${lines}\nBu gorselleri birlikte analiz edip tek bir caption uret.`
}

/** `SocialMediaComposer` handlePublishInstagram / handleSaveToCalendar: rail sırası + seçili revize + önizleme URL */
function composerOrderedDisplayUrls() {
  const bases = [...(s.assetOrder || [])].map((x) => String(x || "").trim()).filter(Boolean)
  const fromRail = bases.map((b) =>
    String((s.selectedRevisionByBase && s.selectedRevisionByBase[b]) || b || "").trim(),
  ).filter(Boolean)
  const parent = String(s.imageUrl || "").trim()
  const seen = new Set()
  const out = []
  for (const u of [...fromRail, parent]) {
    if (!u || seen.has(u)) continue
    seen.add(u)
    out.push(u)
  }
  return out
}

function reorderRailAssets(fromUrl, toUrl) {
  const from = String(fromUrl || "").trim()
  const to = String(toUrl || "").trim()
  if (!from || !to || from === to) return
  if (studioRailHasVideo()) return
  const arr = [...(s.assetOrder || [])].map((x) => String(x || "").trim()).filter(Boolean)
  const fi = arr.indexOf(from)
  const ti = arr.indexOf(to)
  if (fi < 0 || ti < 0) return
  const next = [...arr]
  const [moved] = next.splice(fi, 1)
  next.splice(ti, 0, moved)
  s.assetOrder = next
  const orderedDisplay = next
    .map((b) => String((s.selectedRevisionByBase && s.selectedRevisionByBase[b]) || b || "").trim())
    .filter(Boolean)
  if (orderedDisplay[0]) s.imageUrl = orderedDisplay[0]
  if (!s.editingPostId) saveComposerDraftQuiet()
}

function lsKeyOpenAi() {
  return String((s.appSettings && s.appSettings.openaiApiKey) || "").trim()
}

function lsKeyFal() {
  return String((s.appSettings && s.appSettings.falApiKey) || "").trim()
}

function buildIntegration(account) {
  const o = {}
  const oai = lsKeyOpenAi()
  const fal = lsKeyFal()
  if (oai) o.openai_api_key = oai
  if (fal) o.fal_api_key = fal
  if (account) {
    const igTok = (account.instagramAccessToken || "").trim()
    const igUid = (account.instagramUserId || "").trim()
    const fb = (account.facebookPageId || "").trim()
    if (igTok) o.instagram_access_token = igTok
    if (igUid) o.instagram_user_id = igUid
    if (fb) o.facebook_page_id = fb
  }
  if (s.sessionId) o.session_id = s.sessionId
  return o
}

function getWatchlist() {
  try {
    const raw = localStorage.getItem(holidayWatchStorageKey())
    if (!raw) return []
    const p = JSON.parse(raw)
    return Array.isArray(p) ? p : []
  } catch {
    return []
  }
}

function findWatchEntry(month, day, holidayName) {
  return getWatchlist().find(
    (e) => e.month === month && e.day === day && String(e.holidayName || "") === String(holidayName || ""),
  )
}

function upsertWatchEntry(entry) {
  const list = getWatchlist()
  const k = `${entry.month}-${entry.day}-${entry.holidayName}`
  const idx = list.findIndex((e) => `${e.month}-${e.day}-${e.holidayName}` === k)
  if (idx >= 0) list[idx] = { ...list[idx], ...entry }
  else list.push({ ...entry })
  localStorage.setItem(holidayWatchStorageKey(), JSON.stringify(list))
  window.dispatchEvent(new Event("app-holiday-settings"))
}

function applyCampaignSelectionDetails(campaign) {
  if (!CAMPAIGN_MODE || !campaign) return
  if (!campaignCatalogCredentialsReady()) {
    setStatus(CAMPAIGN_CREDENTIALS_HINT)
    return
  }
  const dates = campaign.campaign_dates && typeof campaign.campaign_dates === "object" ? campaign.campaign_dates : {}
  const start = String(dates.start_date || dates.startDate || "").trim()
  const end = String(dates.end_date || dates.endDate || "").trim()
  if (start) {
    s.campaignStartDate = start
    s.selectedDate = start
  }
  if (end) s.campaignEndDate = end
  const desc = String(campaign.description || "").trim()
  s.caption =
    s.caption.trim() ||
    [String(campaign.product || campaign.id || "").trim(), desc].filter(Boolean).join(desc ? " — " : "")
  const urls = campaignMediaList(campaign)
  if (urls.length) {
    s.campaignMediaUrls = urls
    s.campaignMediaKey = `${String(s.campaignStoreId || "")}:${String(s.campaignId || "")}:${urls.join("|")}`
    recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
  }
}

function applyCampaignImagesToRail(campaign) {
  if (CAMPAIGN_MODE && !campaignCatalogCredentialsReady()) return
  const urls = campaignMediaList(campaign)
  if (!urls.length) return
  s.campaignMediaUrls = [...urls]
  recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
}

function recomputeUnifiedMediaRail({ preferFirst = false } = {}) {
  const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
  const isRevisionVariantSlot = (u) => Boolean(baseForRevisionUrl(u, revMap))
  const campaign = (s.campaignMediaUrls || [])
    .map((u) => String(u || "").trim())
    .filter(Boolean)
    .filter((u) => !isRevisionVariantSlot(u))
  const manual = (s.uploadedImageUrls || [])
    .map((u) => String(u || "").trim())
    .filter(Boolean)
    .filter((u) => !isRevisionVariantSlot(u))
  const ai = (s.aiImageUrls || [])
    .map((u) => String(u || "").trim())
    .filter(Boolean)
    .filter((u) => !isRevisionVariantSlot(u))
  const merged = [...new Set([...campaign, ...manual, ...ai])].filter(Boolean)
  const mergedSet = new Set(merged)
  const currentOrder = Array.isArray(s.assetOrder)
    ? s.assetOrder.map((u) => String(u || "").trim()).filter((u) => u && mergedSet.has(u))
    : []
  const ordered = preferFirst ? [] : [...currentOrder]
  for (const u of merged) {
    if (!ordered.includes(u)) ordered.push(u)
  }
  const nextOrder = ordered.length ? ordered : merged
  s.assetOrder = nextOrder
  s.mediaItems = nextOrder.map((url) => ({
    id: url,
    url,
    type: looksLikeVideoUrl(url) ? "video" : "image",
    source: campaign.includes(url) ? "campaign" : "upload",
    campaignId: campaign.includes(url) ? String(s.campaignId || "") : undefined,
  }))
  const currentImage = String(s.imageUrl || "").trim()
  const currentBase = findRevisionBase(currentImage)
  const currentInRail = Boolean(currentImage && (nextOrder.includes(currentImage) || (currentBase && nextOrder.includes(currentBase))))
  if (preferFirst || !currentInRail) {
    s.imageUrl = nextOrder[0] || ""
    s.selectedMediaId = s.imageUrl || ""
  } else {
    s.selectedMediaId = currentBase || currentImage
  }
  s.referenceCheckedUrls = (s.referenceCheckedUrls || []).filter((u) => nextOrder.includes(String(u || "").trim()))
}

function ensureCampaignMediaBoundSync() {
  return ensureCampaignMediaBoundSyncWith(false)
}

function ensureCampaignMediaBoundSyncWith(force) {
  if (!CAMPAIGN_MODE) return
  if (!campaignCatalogCredentialsReady()) {
    s.campaignMediaUrls = []
    s.campaignMediaKey = `${String(s.campaignStoreId || "").trim()}:${String(s.campaignId || "").trim()}:`
    recomputeUnifiedMediaRail({ preferFirst: false })
    return
  }
  const campaign = activeCampaign()
  const urls = Array.isArray(campaign?.media) ? campaign.media.map((u) => String(u || "").trim()).filter(Boolean) : []
  const key = `${String(s.campaignStoreId || "")}:${String(s.campaignId || "")}:${urls.join("|")}`
  if (!force && key === String(s.campaignMediaKey || "")) return
  const current = (s.campaignMediaUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
  if (!force && urls.length === current.length && urls.every((u, i) => u === current[i])) return
  if (!force && !urls.length && current.length) {
    debugLog("campaign.rail.preserve_existing", {
      storeId: s.campaignStoreId,
      campaignId: s.campaignId,
      currentCount: current.length,
      key,
    })
    s.campaignMediaKey = key
    return
  }
  s.campaignMediaUrls = urls
  s.campaignMediaKey = key
  recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
  debugLog("campaign.rail.bound_sync", {
    storeId: s.campaignStoreId,
    campaignId: s.campaignId,
    count: urls.length,
    force,
  })
}

async function fetchCampaignMediaForSelection(storeId, campaignId) {
  if (CAMPAIGN_MODE && !campaignCatalogCredentialsReady()) return []
  const sid = String(storeId || "").trim()
  const cid = String(campaignId || "").trim()
  if (!sid || !cid) return []
  const localStore = (s.campaignStores || []).find((row) => String(row?.id || "").trim() === sid)
  const localCampaign = findCampaignBySelection(localStore, cid)
  const localMedia = Array.isArray(localCampaign?.media)
    ? localCampaign.media.map((u) => String(u || "").trim()).filter(Boolean)
    : []
  if (localMedia.length) return localMedia
  const catalog = await campaignLoadCatalog()
  const stores = Array.isArray(catalog?.stores) ? catalog.stores : []
  if (stores.length) s.campaignStores = stores
  const freshStore = stores.find((row) => String(row?.id || "").trim() === sid)
  const freshCampaign = findCampaignBySelection(freshStore, cid)
  return Array.isArray(freshCampaign?.media)
    ? freshCampaign.media.map((u) => String(u || "").trim()).filter(Boolean)
    : []
}

async function syncCampaignSelectionToRail() {
  if (!CAMPAIGN_MODE) return
  if (!campaignCatalogCredentialsReady()) {
    s.campaignMediaUrls = []
    s.campaignMediaKey = `${String(s.campaignStoreId || "").trim()}:${String(s.campaignId || "").trim()}:`
    recomputeUnifiedMediaRail({ preferFirst: false })
    s.campaignMediaLoading = false
    return
  }
  const sid = String(s.campaignStoreId || "").trim()
  const selected = activeCampaign()
  const cid = String(selected?.id || s.campaignId || "").trim()
  const beforeUrls = (s.campaignMediaUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
  if (!beforeUrls.length) ensureCampaignMediaBoundSyncWith(true)
  if (!sid || !cid) {
    s.campaignMediaUrls = []
    s.campaignMediaKey = `${sid}:${cid}:`
    recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
    debugLog("campaign.rail.clear_missing_selection", { storeId: sid, campaignId: cid })
    return
  }
  s.campaignMediaLoading = true
  debugLog("campaign.rail.sync_start", { storeId: sid, campaignId: cid, existingCount: beforeUrls.length })
  paintModals()
  try {
    const prevUrls = (s.campaignMediaUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
    const urls = await fetchCampaignMediaForSelection(sid, cid)
    if (urls.length) {
      s.campaignMediaUrls = urls
      s.campaignMediaKey = `${sid}:${cid}:${urls.join("|")}`
      recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
      debugLog("campaign.rail.sync_done", { storeId: sid, campaignId: cid, count: urls.length })
    } else if (!prevUrls.length) {
      s.campaignMediaUrls = []
      s.campaignMediaKey = `${sid}:${cid}:`
      recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
      debugLog("campaign.rail.sync_empty", { storeId: sid, campaignId: cid })
    } else {
      debugLog("campaign.rail.sync_preserved", { storeId: sid, campaignId: cid, count: prevUrls.length })
    }
  } catch (err) {
    // Glitch fix: geçici provider/fetch hatasında mevcut rail'i silme.
    debugLog("campaign.rail.sync_error", { storeId: sid, campaignId: cid, message: err instanceof Error ? err.message : String(err || "") })
  } finally {
    s.campaignMediaLoading = false
    paintModals()
  }
}

function setStatus(msg) {
  s.statusLine = msg
  const el = rootEl && rootEl.querySelector("#sm-status")
  if (el) el.textContent = msg
}

({
  appendAiUrls,
  applyGeneratedVideoUrl,
  applyTemplateToReviseWithScope,
  composerModalScrollAreaClass,
  findReviseTemplate,
  findRevisionBase,
  loadDraftIntoStudio,
  loadPostIntoStudio,
  looksLikeVideoUrl,
  openStudioManual,
  publishCapsStudio,
  removeRailAsset,
  selectedReviseTemplate,
  studioUploadFilesFromFileList,
  syncAssetOrderFromCollections,
  templateUploadFilesFromFileList,
  uploadFilesToStorage,
} = createStudioHelpers({
  applyCampaignSelectionDetails,
  maybeFetchGraphPublishCards,
  paintModals,
  recomputeUnifiedMediaRail,
  setStatus,
  syncCampaignSelectionToRail,
}))

const {
  closeStudio,
  saveComposerDraftQuiet,
  currentComposerPendingMeta,
  persistActiveDraftQuiet,
  persistEditingPostMediaState,
  persistPendingVisualToDraft,
  persistPendingVisualToPost,
  refreshData,
  movePost,
  deletePostById,
  saveCalendarEntry,
} = createPersistenceActions({
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  applyCampaignSelectionDetails,
  campaignTemplateCollections,
  composerOrderedDisplayUrls,
  computeServerDataSig,
  localeTag,
  looksLikeVideoUrl,
  paint,
  paintCalendar,
  paintDayPanel,
  paintStatusAndBanner,
  recomputeUnifiedMediaRail,
  setStatus,
  syncCampaignSelectionToRail,
  syncOpenModalsFromDom,
})

const {
  createHolidayDraftForDateKey,
  pollPendingTasksOnce,
} = createBackgroundTasks({
  activeAccount,
  applyGeneratedVideoUrl,
  appendAiUrls,
  buildIntegration,
  findRevisionBase,
  findWatchEntry,
  getHolidayLabelsSync,
  lsKeyOpenAi,
  paint,
  paintCalendar,
  paintModals,
  paintTaskBanner,
  persistActiveDraftQuiet,
  persistPendingVisualToDraft,
  persistPendingVisualToPost,
  refreshData,
  setStatus,
  syncAssetOrderFromCollections,
  syncOpenModalsFromDom,
  user,
})

const {
  composePublishCampaign,
  composePublishInstagram,
  composerGenerateCampaignBanner,
  composerGenerateCaption,
  composerGenerateImages,
  composerHolidayVideo,
  composerReviseCaption,
  composerReviseImage,
  composerVideoFromReference,
  composerVideoFromText,
} = createComposerActions({
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  appendAiUrls,
  applyGeneratedVideoUrl,
  buildCaptionImageContextForKonu,
  buildIntegration,
  campaignMediaList,
  closeStudio,
  composerOrderedDisplayUrls,
  currentComposerPendingMeta,
  findRevisionBase,
  getPromptProThreshold,
  graphPublishIdsForPostBody,
  looksLikeVideoUrl,
  lsKeyFal,
  lsKeyOpenAi,
  paintModals,
  paintTaskBanner,
  persistActiveDraftQuiet,
  refreshData,
  selectedReviseTemplate,
  setStatus,
  studioRailHasVideo,
  syncAssetOrderFromCollections,
  syncOpenModalsFromDom,
})

const onDelegatedClick = createDelegatedClickHandler({
  applyTemplateToReviseWithScope,
  closeStudio,
  composePublishCampaign,
  composePublishInstagram,
  composerGenerateCampaignBanner,
  composerGenerateCaption,
  composerGenerateImages,
  composerHolidayVideo,
  composerReviseCaption,
  composerReviseImage,
  composerVideoFromReference,
  composerVideoFromText,
  createHolidayDraftForDateKey,
  dayCardCarouselKey,
  deletePostById,
  findRevisionBase,
  findWatchEntry,
  loadDraftIntoStudio,
  loadPostIntoStudio,
  maybeFetchGraphPublishCards,
  openStudioManual,
  paint,
  paintCalendar,
  paintDayPanel,
  paintModals,
  persistActiveDraftQuiet,
  persistEditingPostMediaState,
  refreshData,
  removeRailAsset,
  saveCalendarEntry,
  selectedReviseTemplate,
  setDayCardCarouselIndex,
  setStatus,
  shiftDayCardCarousel,
  syncDayCardCarouselDom,
  syncOpenModalsFromDom,
  upsertWatchEntry,
})

const onDelegatedChange = createDelegatedChangeHandler({
  applyCampaignSelectionDetails,
  paintDayPanel,
  paintModals,
  recomputeUnifiedMediaRail,
  setStatus,
  studioUploadFilesFromFileList,
  syncCampaignSelectionToRail,
  templateUploadFilesFromFileList,
})

function publishStudioApi() {
  window.__SM_STUDIO_API__ = {
    ready: true,
    openCreate({ date, story = false } = {}) {
      const dk = String(date || formatDateKey(new Date())).trim() || formatDateKey(new Date())
      s.selectedDate = dk
      if (!CAMPAIGN_MODE) {
        s.publishTargets = story
          ? { instagramPost: false, instagramStory: true, facebookPost: false }
          : { instagramPost: true, instagramStory: false, facebookPost: true }
        s.studioMode = story ? "story" : "post"
      }
      openStudioManual()
    },
    openPost(post) {
      if (!post || !post.id) return
      loadPostIntoStudio(post)
    },
    openDraft(draft, opts = {}) {
      if (!draft || !draft.id) return
      loadDraftIntoStudio(draft)
      if (opts.approveFocus) {
        s.composerApproved = true
        paintModals()
      }
    },
    async deletePost(id) {
      if (!id) return
      if (!window.confirm(T("deletePlanConfirm"))) return
      await deletePostById(id)
      await refreshData()
      window.dispatchEvent(new CustomEvent("sm-studio-closed"))
    },
    async deleteDraft(id) {
      if (!id) return
      if (!window.confirm(T("deletePlanConfirm"))) return
      await socialDelete(draftsCollection(), id)
      if (s.activeDraftId === id) s.activeDraftId = ""
      s.drafts = s.drafts.filter((d) => d.id !== id)
      await refreshData()
      window.dispatchEvent(new CustomEvent("sm-studio-closed"))
    },
    async approvePost(id, collection) {
      if (!id || !collection) return
      const col = collection === CAMPAIGN_SCHEDULED_POSTS ? CAMPAIGN_SCHEDULED_POSTS : SCHEDULED_POSTS
      await socialPatchFields(col, id, {
        approvalStatus: "approved",
        status: "scheduled",
        publishStatus: "pending",
      })
      await refreshData()
      window.dispatchEvent(new CustomEvent("sm-studio-closed"))
    },
    refreshData,
  }
  window.dispatchEvent(new CustomEvent("sm-studio-ready"))
}

function init() {
  const isApprovalsEmbed = window.__SM_EMBED_MODE__ === "approvals"
  setRootEl(document.getElementById("sm-app"))
  if (!rootEl) return
  window.__SM_DEBUG__ = {
    enable() {
      writeDebugMode(true)
      debugLog("debug.enabled", { by: "api" })
      return true
    },
    disable() {
      debugLog("debug.disabled", { by: "api" })
      writeDebugMode(false)
      return false
    },
    status() {
      return debugStatus()
    },
    events() {
      return debugEvents()
    },
    flush() {
      return flushDebugLogs({ all: true })
    },
    clear() {
      return clearDebugEvents()
    },
    state() {
      return {
        studioOpen: s.studioOpen,
        activeDraftId: s.activeDraftId,
        editingPostId: s.editingPostId,
        composerBusy: s.composerBusy,
        imageUrl: s.imageUrl,
        assetOrder: [...(s.assetOrder || [])],
        pendingTasks: loadPendingTasks(),
      }
    },
  }
  debugLog("init", { debugEnabled: readDebugMode() })
  if (isApprovalsEmbed) {
    rootEl.innerHTML = '<div id="sm-modals"></div>'
    rootEl.classList.add("sm-studio-embed-host")
  } else {
    paintShell()
    void primeHolidays().then(() => {
      // Ilk paint, date-holidays importu tamamlanmadan calisabiliyor; yuklenince takvimi tazele.
      if (!rootEl || s.studioOpen) return
      paintCalendar()
      paintDayPanel()
    })
  }
  rootEl.addEventListener("click", (e) => {
    if (e.target instanceof Element && e.target.closest("[data-stop]")) e.stopPropagation()
    void onDelegatedClick(e)
  })
  rootEl.addEventListener("change", onDelegatedChange)
  rootEl.addEventListener("input", (e) => {
    const t = e.target
    if (!(t instanceof HTMLInputElement)) return
    if (t.id === "st-video-dur" && t.type === "range") {
      const n = Number(t.value)
      if (Number.isFinite(n)) s.videoDurationSec = Math.min(15, Math.max(3, n))
    }
  })
  rootEl.addEventListener(
    "touchstart",
    (e) => {
      const node = e.target instanceof Element ? e.target.closest("[data-carousel-swipe]") : null
      if (!node) return
      const touch = e.changedTouches && e.changedTouches[0]
      if (!touch) return
      const key = String(node.getAttribute("data-card-carousel") || "").trim()
      if (!key) return
      s.dayCardTouch = { key, x: touch.clientX, y: touch.clientY }
    },
    { passive: true },
  )
  rootEl.addEventListener("touchend", (e) => {
    const start = s.dayCardTouch
    s.dayCardTouch = null
    if (!start) return
    const touch = e.changedTouches && e.changedTouches[0]
    if (!touch) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < 28 || Math.abs(dx) <= Math.abs(dy)) return
    const [kind, ...rest] = String(start.key).split(":")
    const id = rest.join(":")
    if (!kind || !id) return
    shiftDayCardCarousel(kind, id, dx > 0 ? -1 : 1)
  })
  window.addEventListener("click", () => {
    hidePostPreview()
    s.contextAccountId = null
    s.dayMenu = null
    paintModals()
  })
  window.addEventListener("keydown", (e) => {
    if (e.shiftKey && e.ctrlKey && (e.key === "D" || e.key === "d")) {
      const next = !readDebugMode()
      writeDebugMode(next)
      debugLog("debug.toggle.hotkey", { enabled: next })
      setStatus(next ? "Debug mode: ON" : "Debug mode: OFF")
      return
    }
    if (e.key === "Escape") {
      hidePostPreview()
      s.dayMenu = null
      s.holidaySettings = null
      s.contextAccountId = null
      if (s.studioOpen) closeStudio(true)
      s.accountModal = false
      s.ticketModal = false
      s.templateModal = false
      paintModals()
    }
  })
  if (!window.__smComposerPasteBound) {
    window.__smComposerPasteBound = true
    window.addEventListener("paste", (ev) => {
      const dt = ev.clipboardData
      if (!dt) return
      if (s.templateModal) {
        const tplFiles = []
        for (const item of Array.from(dt.items || [])) {
          if (item.type.startsWith("image/")) {
            const f = item.getAsFile()
            if (f) tplFiles.push(f)
          }
        }
        if (!tplFiles.length) return
        ev.preventDefault()
        const cur = s.templateDraft.imageUrls || []
        const hasLayout = Boolean(String(cur[0] || "").trim())
        const hasLogo = Boolean(String(cur[1] || "").trim())
        const slot = !hasLayout ? "layout" : !hasLogo ? "logo" : undefined
        if (slot) void templateUploadFilesFromFileList(tplFiles.slice(0, 1), slot)
        else void templateUploadFilesFromFileList(tplFiles)
        return
      }
      if (!s.studioOpen) return
      if (s.visualOutputKind === "video" && studioRailHasVideo()) return
      const files = []
      for (const item of Array.from(dt.items || [])) {
        if (item.type.startsWith("image/") || (s.visualOutputKind === "video" && item.type.startsWith("video/"))) {
          const f = item.getAsFile()
          if (f) files.push(f)
        }
      }
      if (!files.length) return
      ev.preventDefault()
      void studioUploadFilesFromFileList(files)
    })
  }
  registerCalendarDelegation({
    hidePostPreview,
    loadPostIntoStudio,
    makeCalendarDragGhost,
    movePost,
    paint,
    paintCalendar,
    paintModals,
    reorderRailAssets,
    studioRailHasVideo,
    studioUploadFilesFromFileList,
    templateUploadFilesFromFileList,
  })
  registerPostPreviewHover()
  window.addEventListener("sm-pending-tasks", () => {
    paintTaskBanner()
    paintCalendar()
  })
  window.addEventListener("storage", (e) => {
    if (e.key === PENDING_TASKS_STORAGE_KEY) {
      paintTaskBanner()
      paintCalendar()
    }
  })
  window.addEventListener("sm-scheduled-post-created", () => {
    void refreshData()
  })
  if (isApprovalsEmbed) {
    void refreshData().then(() => publishStudioApi())
  } else {
    void refreshData().then(() => {
      /** CAMPAIGN_MODE'da hesap seçimi/credentials hazırsa katalog otomatik fetch. Yoksa kullanıcı "Bearer token girin" görür. */
      if (CAMPAIGN_MODE) {
        if (!String(s.activeAccountId || "").trim() && Array.isArray(s.accounts) && s.accounts.length) {
          s.activeAccountId = String(s.accounts[0].id || "")
        }
        if (campaignCatalogCredentialsReady()) {
          void campaignLoadCatalog({ force: false }).then(() => paint()).catch(() => {})
        }
      }
    })
    void pollPendingTasksOnce()
    if (!pendingPollTimer) {
      setPendingPollTimer(window.setInterval(() => void pollPendingTasksOnce(), PENDING_POLL_MS))
    }
  }
}

document.addEventListener("DOMContentLoaded", init)

