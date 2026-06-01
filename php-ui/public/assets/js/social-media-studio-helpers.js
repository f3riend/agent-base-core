import { apiBase, apiRequest, authHeaders, T } from "./social-media-api.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { campaignLoadStoreDiscountedProducts, usesStoreDiscountedProductCatalog } from "./social-media-data.js"
import {
  assetUrlEquals,
  baseForRevisionUrl,
  buildRailOrderFromDoc,
  formatDateKey,
  withInferredRevisionState,
} from "./social-media-post-utils.js"
import { s } from "./social-media-state.js"
import {
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  CAMPAIGN_CREDENTIALS_HINT,
  campaignCatalogCredentialsReady,
  normalizeStudioPanel,
} from "./social-media-selectors.js"

export function createStudioHelpers({
  applyCampaignSelectionDetails,
  maybeFetchGraphPublishCards,
  paintModals,
  recomputeUnifiedMediaRail,
  setStatus,
  syncCampaignSelectionToRail,
} = {}) {
  function loadPostIntoStudio(post) {
    s.studioOpen = true
    s.studioTab = "manual"
    s.composerStep = 0
    s.modalPanel = normalizeStudioPanel("caption")
    s.editingPostId = post.id
    s.caption = post.caption || ""
    s.prompt = post.prompt || ""
    s.lastTopic = (post.prompt || s.lastTopic || "").trim()
    let postSnap = {}
    if (typeof post.revisionSnapshotJson === "string" && post.revisionSnapshotJson.trim()) {
      try {
        postSnap = JSON.parse(post.revisionSnapshotJson)
      } catch {
        postSnap = {}
      }
    }
    let postRevMap = postSnap && typeof postSnap.revisionMap === "object" ? { ...postSnap.revisionMap } : {}
    let postSelRev =
      postSnap && typeof postSnap.selectedRevisionByBase === "object" ? { ...postSnap.selectedRevisionByBase } : {}
    let postRevisionState = withInferredRevisionState(post.imageUrl, post.imageUrls || [], postRevMap, postSelRev)
    if (Object.keys(postRevisionState.revisionMap).length === 0) {
      const imgs = (post.imageUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
      const ip = String(post.imageUrl || "").trim()
      const uniq = [...new Set([...imgs, ip].filter(Boolean))]
      if (uniq.length >= 2) {
        const base = uniq[0]
        postRevisionState = {
          revisionMap: { [base]: uniq },
          selectedRevisionByBase: {
            ...postSelRev,
            [base]: ip && uniq.some((u) => assetUrlEquals(u, ip)) ? ip : uniq[uniq.length - 1],
          },
        }
      }
    }
    s.revisionMap = postRevisionState.revisionMap || {}
    s.selectedRevisionByBase = postRevisionState.selectedRevisionByBase || {}
    s.assetOrder = buildRailOrderFromDoc(post.imageUrl, post.imageUrls || [], postSnap.assetOrder, s.revisionMap)
    /** Edit modunda rail her zaman kaydın kendi görsellerinden gelsin. */
    s.uploadedImageUrls = [...s.assetOrder]
    const snapAiPost = Array.isArray(postSnap.aiImageUrls)
      ? postSnap.aiImageUrls.map((u) => String(u || "").trim()).filter(Boolean)
      : []
    s.aiImageUrls = CAMPAIGN_MODE && snapAiPost.length ? [...new Set(snapAiPost)] : []
    const refSnapPost = Array.isArray(postSnap.referenceCheckedUrls)
      ? postSnap.referenceCheckedUrls.map((u) => String(u || "").trim()).filter(Boolean)
      : []
    const orderSetPost = new Set((s.assetOrder || []).map((x) => String(x || "").trim()))
    s.referenceCheckedUrls = refSnapPost.filter((u) => orderSetPost.has(u))
    if (postSnap.selectedTemplateId != null && String(postSnap.selectedTemplateId).trim()) {
      s.selectedTemplateId = postSnap.selectedTemplateId
      s.selectedTemplateScope = postSnap.selectedTemplateScope === "global" ? "global" : "user"
    } else {
      s.selectedTemplateId = null
      s.selectedTemplateScope = "user"
    }
    const storedPrimary = String(post.imageUrl || "").trim()
    s.imageUrl = storedPrimary
    const baseGuess = baseForRevisionUrl(storedPrimary, s.revisionMap)
    if (baseGuess) {
      const pick = String((s.selectedRevisionByBase && s.selectedRevisionByBase[baseGuess]) || storedPrimary).trim()
      if (pick) s.imageUrl = pick
    }
    s.scheduledTime = post.time || "12:00"
    s.composerApproved = post.approvalStatus !== "pending"
    s.publishTargets = post.publishTargets || { instagramPost: true, instagramStory: true, facebookPost: true }
    /** Studio modu post/story: publishTargets'tan türetilir — Story-only post'lar story modunda açılır. */
    s.studioMode = (s.publishTargets.instagramStory && !s.publishTargets.instagramPost) ? "story" : "post"
    s.activeAccountId = post.accountId
    s.campaignStoreId = String(post.campaignStoreId || post.accountId || s.campaignStoreId || "")
    s.campaignId = String(post.campaignId || s.campaignId || "")
    s.campaignStartDate = String(post.campaignStartDate || s.campaignStartDate || formatDateKey(new Date()))
    s.campaignEndDate = String(post.campaignEndDate || s.campaignEndDate || formatDateKey(new Date()))
    if (CAMPAIGN_MODE && campaignCatalogCredentialsReady()) {
      void syncCampaignSelectionToRail()
      recomputeUnifiedMediaRail({ preferFirst: false })
    }
    /** Bu post'un biriken üretim maliyetini Yayınla sekmesinde gösterebilmek için arka planda çek. */
    s.editingPostCost = 0
    s.activeDraftCost = 0
    void apiRequest("/social-media/usage/cost?post_id=" + encodeURIComponent(String(post.id || "")), {
      headers: authHeaders(false),
    })
      .then((res) => {
        s.editingPostCost = Number(res?.cost_usd || 0)
        paintModals()
      })
      .catch(() => {})
    paintModals()
  }

  function loadDraftIntoStudio(d) {
    const snap = d.snapshot && typeof d.snapshot === "object" ? d.snapshot : {}
    s.studioOpen = true
    s.studioTab = snap.studioTab === "ai" ? "ai" : "manual"
    s.modalPanel = normalizeStudioPanel(s.studioTab === "ai" ? "generate" : "caption")
    s.composerStep = typeof snap.step === "number" ? snap.step : 0
    s.editingPostId = null
    s.activeDraftId = d.id
    s.caption = d.caption || ""
    s.prompt = d.prompt || ""
    s.lastTopic = String(snap.lastTopic || d.prompt || s.lastTopic || "").trim()
    s.imageUrl = d.imageUrl || ""
    s.scheduledTime = d.time || "12:00"
    s.activeAccountId = d.accountId || s.activeAccountId
    s.campaignStoreId = String(d.campaignStoreId || d.accountId || s.campaignStoreId || "")
    s.campaignId = String(d.campaignId || s.campaignId || "")
    s.campaignStartDate = String(d.campaignStartDate || s.campaignStartDate || formatDateKey(new Date()))
    s.campaignEndDate = String(d.campaignEndDate || s.campaignEndDate || formatDateKey(new Date()))
    s.captionMode = snap.captionMode === "ai" ? "ai" : "manual"
    s.mediaMode = snap.mediaMode === "manual" || snap.mediaMode === "ai_direct" || snap.mediaMode === "ai_revise" ? snap.mediaMode : "manual"
    s.visualOutputKind = snap.visualOutputKind === "video" ? "video" : "image"
    s.generateSubTab = CAMPAIGN_MODE ? "manual" : snap.generateSubTab === "ticket" ? "ticket" : "manual"
    s.videoAiMode = snap.videoAiMode === "reference" || snap.videoAiMode === "holiday" ? snap.videoAiMode : "text"
    s.directImagePrompt = String(snap.directImagePrompt || s.prompt || "").trim()
    s.selectedTicketId = CAMPAIGN_MODE ? null : typeof snap.selectedTicketId === "string" ? snap.selectedTicketId : null
    s.useSelectedAsReference = Boolean(snap.useSelectedAsReference)
    s.useSelectedRefsForRevise = Boolean(snap.useSelectedRefsForRevise)
    s.holidayVideoName = String(snap.holidayVideoName || "")
    s.holidayVideoDate = String(snap.holidayVideoDate || s.holidayVideoDate || formatDateKey(new Date()))
    s.videoDurationSec =
      typeof snap.videoDurationSec === "number" && Number.isFinite(snap.videoDurationSec)
        ? Math.min(15, Math.max(3, Math.round(snap.videoDurationSec)))
        : 5
    s.videoGenerateAudio = snap.videoGenerateAudio !== false
    s.revisionMap = snap.revisionMap && typeof snap.revisionMap === "object" ? snap.revisionMap : {}
    s.selectedRevisionByBase =
      snap.selectedRevisionByBase && typeof snap.selectedRevisionByBase === "object" ? snap.selectedRevisionByBase : {}
    const draftRevisionState = withInferredRevisionState(d.imageUrl, d.imageUrls || [], s.revisionMap, s.selectedRevisionByBase)
    s.revisionMap = draftRevisionState.revisionMap
    s.selectedRevisionByBase = draftRevisionState.selectedRevisionByBase
    if (Object.keys(s.revisionMap || {}).length === 0) {
      const imgs = (d.imageUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
      const ip = String(d.imageUrl || "").trim()
      const uniq = [...new Set([...imgs, ip].filter(Boolean))]
      if (uniq.length >= 2) {
        const base = uniq[0]
        s.revisionMap = { [base]: uniq }
        s.selectedRevisionByBase = {
          ...s.selectedRevisionByBase,
          [base]: ip && uniq.some((u) => assetUrlEquals(u, ip)) ? ip : uniq[uniq.length - 1],
        }
      }
    }
    s.assetOrder = buildRailOrderFromDoc(d.imageUrl, d.imageUrls || [], snap.assetOrder, s.revisionMap)
    if (!s.assetOrder.length) {
      const fromSnapUploaded = Array.isArray(snap.uploadedImageUrls)
        ? snap.uploadedImageUrls.map((u) => String(u || "").trim()).filter(Boolean)
        : []
      const fromSnapAi = Array.isArray(snap.aiImageUrls) ? snap.aiImageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
      const fromSnapOrder = Array.isArray(snap.assetOrder) ? snap.assetOrder.map((u) => String(u || "").trim()).filter(Boolean) : []
      const fromDocUrls = Array.isArray(d.imageUrls) ? d.imageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
      const rawFallback = [...fromSnapOrder, ...fromDocUrls, ...fromSnapUploaded, ...fromSnapAi, String(d.imageUrl || "").trim()].filter(Boolean)
      const baseFallback = rawFallback.map((u) => baseForRevisionUrl(u, s.revisionMap) || u).filter(Boolean)
      s.assetOrder = [...new Set(baseFallback)]
    }
    if (s.revisionMap && typeof s.revisionMap === "object") {
      const nextSel = { ...(s.selectedRevisionByBase || {}) }
      for (const base of Object.keys(s.revisionMap)) {
        const b = String(base || "").trim()
        if (!b) continue
        if (!nextSel[b]) nextSel[b] = b
      }
      s.selectedRevisionByBase = nextSel
    }
    if (!s.imageUrl && s.assetOrder.length) {
      const firstBase = s.assetOrder[0]
      s.imageUrl = String((s.selectedRevisionByBase && s.selectedRevisionByBase[firstBase]) || firstBase || "").trim()
    }
    if (snap.selectedTemplateId != null && String(snap.selectedTemplateId).trim()) {
      s.selectedTemplateId = snap.selectedTemplateId
      s.selectedTemplateScope = snap.selectedTemplateScope === "global" ? "global" : "user"
    } else {
      s.selectedTemplateId = null
      s.selectedTemplateScope = "user"
    }
    const refSnapDraft = Array.isArray(snap.referenceCheckedUrls)
      ? snap.referenceCheckedUrls.map((u) => String(u || "").trim()).filter(Boolean)
      : []
    const orderSetDraft = new Set((s.assetOrder || []).map((x) => String(x || "").trim()))
    s.referenceCheckedUrls = refSnapDraft.filter((u) => orderSetDraft.has(u))
    const snapAiDraft = Array.isArray(snap.aiImageUrls) ? snap.aiImageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
    /** Draft devamında önceki oturum state'i değil, draftın kendi rail'i esas alınır. */
    s.uploadedImageUrls = [...s.assetOrder]
    s.aiImageUrls = CAMPAIGN_MODE && snapAiDraft.length ? [...new Set(snapAiDraft)] : []
    if (CAMPAIGN_MODE && !campaignCatalogCredentialsReady()) {
      s.campaignMediaUrls = []
      const sid = String(s.campaignStoreId || "").trim()
      const cid = String(s.campaignId || "").trim()
      s.campaignMediaKey = `${sid}:${cid}:`
      recomputeUnifiedMediaRail({ preferFirst: false })
      setStatus(CAMPAIGN_CREDENTIALS_HINT)
    } else if (CAMPAIGN_MODE && campaignCatalogCredentialsReady()) {
      void syncCampaignSelectionToRail()
      recomputeUnifiedMediaRail({ preferFirst: false })
    }
    /** Studio modu: draft state'indeki publishTargets'tan türet (story-only ise story). */
    const _pt = s.publishTargets || {}
    s.studioMode = (_pt.instagramStory && !_pt.instagramPost) ? "story" : "post"
    paintModals()
    if (s.studioTab === "ai") void maybeFetchGraphPublishCards()
    s.editingPostCost = 0
    s.activeDraftCost = 0
    void apiRequest("/social-media/usage/cost?draft_id=" + encodeURIComponent(String(d.id || "")), {
      headers: authHeaders(false),
    })
      .then((res) => {
        s.activeDraftCost = Number(res?.cost_usd || 0)
        paintModals()
      })
      .catch(() => {})
  }

  function openStudioManual() {
    s.studioOpen = true
    s.editingPostId = null
    s.activeDraftId = null
    s.studioTab = s.studioDefaultTab === "ai" ? "ai" : "manual"
    s.modalPanel = normalizeStudioPanel(s.studioDefaultTab === "ai" ? "generate" : "caption")
    if (s.studioTab === "ai") s.mediaMode = "ai_direct"
    s.composerStep = 0
    s.caption = ""
    s.prompt = ""
    s.lastTopic = ""
    s.captionReviseFeedback = ""
    s.directImagePrompt = ""
    s.reviseFeedback = ""
    s.imageUrl = ""
    s.aiImageUrls = []
    s.uploadedImageUrls = []
    s.referenceCheckedUrls = []
    s.assetOrder = []
    s.revisionMap = {}
    s.selectedRevisionByBase = {}
    s.selectedTicketId = null
    s.useSelectedAsReference = false
    s.useSelectedRefsForRevise = false
    s.selectedTemplateId = null
    s.selectedTemplateScope = "user"
    s.visualOutputKind = "image"
    s.videoAiMode = "text"
    s.videoDurationSec = 5
    s.videoGenerateAudio = true
    s.holidayVideoName = ""
    if (!s.holidayVideoDate) s.holidayVideoDate = formatDateKey(new Date())
    s.scheduledTime = s.scheduledTime || "12:00"
    if (CAMPAIGN_MODE) {
      if (!campaignCatalogCredentialsReady()) {
        s.campaignStoreId = ""
        s.campaignId = ""
        s.campaignMediaUrls = []
        s.campaignMediaKey = "::"
        setStatus(CAMPAIGN_CREDENTIALS_HINT)
      } else {
        if (!s.campaignStoreId && s.campaignStores[0]) s.campaignStoreId = String(s.campaignStores[0].id || "")
        const loadProducts = async () => {
          if (usesStoreDiscountedProductCatalog() && s.campaignStoreId) {
            try {
              const products = await campaignLoadStoreDiscountedProducts(s.campaignStoreId)
              if (!s.campaignId && products[0]) {
                s.campaignId = String(products[0].id || products[0].product || "")
              }
            } catch {
              s.campaignId = ""
            }
          } else {
            const store = activeCampaignStore()
            if (store && !s.campaignId && Array.isArray(store.campaigns) && store.campaigns[0]) {
              s.campaignId = String(store.campaigns[0].id || "")
            }
          }
          s.campaignStartDate = formatDateKey(new Date())
          s.campaignEndDate = formatDateKey(new Date(Date.now() + 7 * 24 * 60 * 60 * 1000))
          applyCampaignSelectionDetails(activeCampaign())
          void syncCampaignSelectionToRail()
          paintModals()
        }
        void loadProducts()
      }
    }
    paintModals()
    if (!CAMPAIGN_MODE) void maybeFetchGraphPublishCards()
  }

  function looksLikeVideoUrl(u) {
    const t = String(u || "")
      .trim()
      .toLowerCase()
    return t.endsWith(".mp4") || t.endsWith(".webm") || t.endsWith(".mov") || t.includes("/video") || t.includes("kling")
  }

  /** `SocialMediaComposer.tsx` MODAL_SCROLL_AREA ile aynı */
  function composerModalScrollAreaClass() {
    return "overflow-y-auto overscroll-contain [scrollbar-width:thin] [scrollbar-color:#a3a3a3_#f5f5f5] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-neutral-400 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-neutral-100"
  }

  function syncAssetOrderFromCollections() {
    recomputeUnifiedMediaRail()
  }

  /** Tek video kuralı: railde en fazla 1 video tut, mevcut görseller kalsın (disabled görünür). */
  function applyGeneratedVideoUrl(videoUrl) {
    const v = String(videoUrl || "").trim()
    if (!v) return
    const nonVideoUploaded = (s.uploadedImageUrls || []).map((x) => String(x || "").trim()).filter((u) => u && !looksLikeVideoUrl(u))
    const nonVideoAi = (s.aiImageUrls || []).map((x) => String(x || "").trim()).filter((u) => u && !looksLikeVideoUrl(u))
    s.uploadedImageUrls = [...new Set(nonVideoUploaded)]
    s.aiImageUrls = [...new Set([...nonVideoAi, v])]
    s.referenceCheckedUrls = (s.referenceCheckedUrls || []).map((x) => String(x || "").trim()).filter((u) => u && !looksLikeVideoUrl(u))
    const keepOrder = (s.assetOrder || []).map((x) => String(x || "").trim()).filter((u) => u && !looksLikeVideoUrl(u))
    s.assetOrder = [...new Set([...keepOrder, v])]
    s.imageUrl = v
  }

  function appendAiUrls(urls) {
    const next = (Array.isArray(urls) ? urls : []).map((u) => String(u || "").trim()).filter(Boolean)
    if (!next.length) return
    const prev = (s.aiImageUrls || []).map((u) => String(u || "").trim()).filter(Boolean)
    s.aiImageUrls = [...new Set([...prev, ...next])]
  }

  function findRevisionBase(url) {
    const target = String(url || "").trim()
    if (!target) return null
    const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
    for (const [base, items] of Object.entries(revMap)) {
      const b = String(base || "").trim()
      const arr = Array.isArray(items) ? items.map((u) => String(u || "").trim()).filter(Boolean) : []
      if (assetUrlEquals(b, target) || arr.some((u) => assetUrlEquals(u, target))) return b
    }
    return null
  }

  function removeRailAsset(url) {
    const u = String(url || "").trim()
    if (!u) return
    s.assetOrder = (s.assetOrder || []).filter((x) => x !== u)
    s.campaignMediaUrls = (s.campaignMediaUrls || []).filter((x) => x !== u)
    s.campaignMediaKey = `${String(s.campaignStoreId || "")}:${String(s.campaignId || "")}:${(s.campaignMediaUrls || []).join("|")}`
    s.uploadedImageUrls = (s.uploadedImageUrls || []).filter((x) => x !== u)
    s.aiImageUrls = (s.aiImageUrls || []).filter((x) => x !== u)
    s.referenceCheckedUrls = (s.referenceCheckedUrls || []).filter((x) => x !== u)
    const nextRevMap = {}
    const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
    for (const [base, items] of Object.entries(revMap)) {
      if (base === u) continue
      const filtered = (Array.isArray(items) ? items : []).map((x) => String(x || "").trim()).filter((x) => x && x !== u)
      if (filtered.length > 1) nextRevMap[base] = filtered
    }
    s.revisionMap = nextRevMap
    const nextSelRev = { ...(s.selectedRevisionByBase || {}) }
    delete nextSelRev[u]
    s.selectedRevisionByBase = nextSelRev
    if ((s.imageUrl || "").trim() === u) s.imageUrl = (s.assetOrder[0] || "").trim()
  }

  async function studioUploadFilesFromFileList(files) {
    const list = Array.isArray(files) ? files : Array.from(files || [])
    if (!list.length) return
    try {
      const newUrls = await uploadFilesToStorage(list)
      if (newUrls.length) {
        s.uploadedImageUrls = Array.from(new Set([...(s.uploadedImageUrls || []), ...newUrls]))
        syncAssetOrderFromCollections()
        if (!s.imageUrl.trim()) s.imageUrl = newUrls[0]
        else if (s.studioOpen && s.modalPanel === "generate") s.imageUrl = newUrls[newUrls.length - 1]
        setStatus(T("composerUploadOk"))
      } else setStatus(T("msgUploadFailed"))
    } catch {
      setStatus(T("msgUploadFailed"))
    } finally {
      paintModals()
    }
  }

  async function uploadFilesToStorage(files) {
    const list = Array.isArray(files) ? files : Array.from(files || [])
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

  async function templateUploadFilesFromFileList(files, slot) {
    const list = Array.isArray(files) ? files : Array.from(files || [])
    if (!list.length) return
    const useSlot = slot === "layout" || slot === "logo"
    s.templateUploading = true
    paintModals()
    try {
      const batch = useSlot ? list.slice(0, 1) : list
      const uploaded = await uploadFilesToStorage(batch)
      const first = String(uploaded[0] || "").trim()
      if (!first) {
        setStatus(T("msgUploadFailed"))
        return
      }
      if (useSlot) {
        const cur = [...(s.templateDraft.imageUrls || [])]
        const layout0 = String(cur[0] || "").trim()
        const logo1 = String(cur[1] || "").trim()
        if (slot === "layout") {
          s.templateDraft.imageUrls = logo1 ? [first, logo1] : [first]
        } else {
          if (!layout0) {
            setStatus(T("tplCampaignLogoNeedLayout"))
            return
          }
          s.templateDraft.imageUrls = [layout0, first]
        }
        setStatus(T("composerUploadOk"))
      } else if (uploaded.length) {
        const cur = [...(s.templateDraft.imageUrls || [])]
        const layout0 = String(cur[0] || "").trim()
        if (!layout0) {
          s.templateDraft.imageUrls = [...uploaded]
        } else {
          s.templateDraft.imageUrls = [...new Set([...cur, ...uploaded])]
        }
        setStatus(T("composerUploadOk"))
      } else {
        setStatus(T("msgUploadFailed"))
      }
    } catch {
      setStatus(T("msgUploadFailed"))
    } finally {
      s.templateUploading = false
      paintModals()
    }
  }

  function findReviseTemplate(scope, templateId) {
    const src = scope === "global" ? s.globalTemplates : s.userTemplates
    return src.find((x) => String(x.id || "") === String(templateId || "")) || null
  }

  function selectedReviseTemplate() {
    const sid = String(s.selectedTemplateId || "").trim()
    if (!sid) return null
    return findReviseTemplate(String(s.selectedTemplateScope || "user"), sid)
  }

  function applyTemplateToReviseWithScope(templateId, scope = "user") {
    const tpl = findReviseTemplate(String(scope || "user"), templateId)
    if (!tpl) return
    const same =
      String(s.selectedTemplateId || "") === String(tpl.id || "") &&
      String(s.selectedTemplateScope || "user") === String(scope || "user")
    const prompt = String(tpl.prompt || "").trim()
    if (same) {
      s.selectedTemplateId = null
      s.selectedTemplateScope = "user"
      if (String(s.reviseFeedback || "").trim() === prompt) s.reviseFeedback = ""
      setStatus("Sablon secimi kaldirildi")
      paintModals()
      return
    }
    s.selectedTemplateId = tpl.id
    s.selectedTemplateScope = String(scope || "user")
    s.reviseFeedback = String(tpl.prompt || "")
    setStatus(CAMPAIGN_MODE ? "Kampanya sablonu secildi. Uretimde referans olarak kullanilacak." : "Sablon secildi. Revizede referans olarak kullanilacak.")
    paintModals()
  }

  function publishCapsStudio() {
    const account = activeAccount()
    if (!account) return { canIg: false, canFb: false }
    const tok = Boolean((account.instagramAccessToken || "").trim())
    const fb = Boolean(tok && (account.facebookPageId || "").trim())
    return { canIg: tok, canFb: fb }
  }

  return {
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
  }
}
