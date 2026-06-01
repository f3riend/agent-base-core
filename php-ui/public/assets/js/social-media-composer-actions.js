import { apiRequest, authHeaders, cfg, T } from "./social-media-api.js"
import {
  appendCampaignReviseLayoutMismatchHint,
  buildCampaignBannerPrompt,
  buildCampaignBannerReferenceImageList,
  buildCampaignTemplateRefsExcludingMain,
  CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE,
  CAMPAIGN_MODE,
  isPublishableCampaignStoreId,
  scheduledPostsCollection,
  withCampaignBannerConstraint,
} from "./social-media-campaign-utils.js"
import { resolveOutputSize } from "./social-media-constants.js"
import {
  campaignPublish,
  DEL,
  postInstagram,
  resolveQueued,
  socialCreate,
  socialPatchFields,
  TS,
} from "./social-media-data.js"
import {
  clearCaptionInFlight,
  clearImageHttpInFlight,
  clearVisualPendingHint,
  queuePendingComposerTask,
  writeCaptionInFlight,
  writeImageHttpInFlight,
  writeVisualPendingHint,
} from "./social-media-runtime.js"
import { s } from "./social-media-state.js"
import { assetUrlEquals } from "./social-media-post-utils.js"

function hasPostApiResults(data) {
  const r = data && data.results
  return r && typeof r === "object" && !Array.isArray(r) && Object.keys(r).length > 0
}

export function createComposerActions(deps) {
  const {
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
  } = deps

  /** Rail slot = katalog / yuklenen URL; sablon layout URL'si zincir anahtari olmasin (sol rail sisirmasin). */
  function resolveCampaignBannerRevisionBase(layoutUrl) {
    const lay = String(layoutUrl || "").trim()
    const img = String(s.imageUrl || "").trim()
    const campaign = activeCampaign()
    const cm = campaign ? campaignMediaList(campaign).map((u) => String(u || "").trim()).filter(Boolean) : []
    const cm0 = cm[0] || ""
    const fromMap = img ? findRevisionBase(img) : null
    let base = String(fromMap || "").trim() || img
    if (!base && cm0) base = cm0
    if (CAMPAIGN_MODE && lay && cm0 && (assetUrlEquals(base, lay) || !base)) base = cm0
    return base || lay
  }

  function mergeCampaignRevisionMapEntry(currentBase, layoutUrl, newUrls) {
    const base = String(currentBase || "").trim()
    const lay = String(layoutUrl || "").trim()
    const next = { ...(s.revisionMap || {}) }
    let existing = Array.isArray(next[base]) ? [...next[base]] : base ? [base] : []
    if (CAMPAIGN_MODE && lay && base && !assetUrlEquals(base, lay) && Array.isArray(next[lay])) {
      const leg = next[lay].map((u) => String(u || "").trim()).filter(Boolean)
      existing = [...new Set([...existing, ...leg])].filter(Boolean)
      delete next[lay]
    }
    const merged = [...new Set([...existing, ...newUrls])].filter(Boolean)
    s.revisionMap = { ...next, [base]: merged }
  }

  async function composerGenerateCaption() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account) {
      setStatus(T("composerNeedAccount"))
      return
    }
    const topicBase = (s.lastTopic || s.prompt || "").trim()
    if (!topicBase) {
      setStatus(T("composerTopicRequired"))
      return
    }
    const imageCtx = buildCaptionImageContextForKonu()
    const konu = `${topicBase}${imageCtx}`
    s.prompt = topicBase
    s.lastTopic = topicBase
    s.composerBusy = true
    writeCaptionInFlight({ kind: "generate", konu })
    paintModals()
    let generated = ""
    try {
      const raw = await apiRequest("/social-media/caption/generate", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          konu,
          tone: "profesyonel",
          platform: "feed",
          ...buildIntegration(account),
        }),
      })
      const data = await resolveQueued(raw, 1500, 300000)
      generated = String(data.caption ?? "")
      const sid = data.session_id != null ? String(data.session_id).trim() : ""
      if (sid) s.sessionId = sid
      setStatus(generated.trim() ? T("composerCaptionOk") : T("composerCaptionFail"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerCaptionFail"))
    } finally {
      clearCaptionInFlight()
      syncOpenModalsFromDom()
      if (generated.trim()) s.caption = generated
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerReviseCaption() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account) {
      setStatus(T("composerNeedAccount"))
      return
    }
    if (!s.caption.trim()) {
      setStatus(T("composerRevizeNeedCaption"))
      return
    }
    if (!s.captionReviseFeedback.trim()) {
      setStatus(T("composerRevizePromptRequired"))
      return
    }
    const mevcut = s.caption.trim()
    const talep = s.captionReviseFeedback.trim()
    writeCaptionInFlight({ kind: "revise", mevcutCaption: mevcut, revizeTalebi: talep })
    s.composerBusy = true
    paintModals()
    let updated = ""
    try {
      const raw = await apiRequest("/social-media/caption/revize", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          mevcut_caption: mevcut,
          revize_talebi: talep,
          ...buildIntegration(account),
        }),
      })
      const data = await resolveQueued(raw, 1500, 300000)
      updated = String(data.caption ?? "")
      const sid = data.session_id != null ? String(data.session_id).trim() : ""
      if (sid) s.sessionId = sid
      setStatus(updated.trim() ? T("composerRevizeOk") : T("composerRevizeFail"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerRevizeFail"))
    } finally {
      clearCaptionInFlight()
      syncOpenModalsFromDom()
      if (updated.trim()) s.caption = updated
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerGenerateImages(options = {}) {
    syncOpenModalsFromDom()
    const opts = options && typeof options === "object" ? options : {}
    const account = activeAccount()
    if (!account && !CAMPAIGN_MODE) {
      setStatus(T("composerNeedAccount"))
      return
    }
    if (s.visualOutputKind === "video") return
    const selectedTpl = selectedReviseTemplate()
    const tplPrompt = CAMPAIGN_MODE && !opts.omitTemplatePrompt ? String(selectedTpl?.prompt || "").trim() : ""
    const fromDirect = (s.directImagePrompt || "").trim()
    const fromLegacy = (s.prompt || "").trim()
    const promptOverride = String(opts.promptOverride || "").trim()
    const promptSeed = [tplPrompt, (promptOverride || fromDirect || fromLegacy).trim()].filter(Boolean).join("\n\n")
    const prompt = withCampaignBannerConstraint(promptSeed)
    if (prompt.length < 2) {
      setStatus(T("composerVisualPromptRequired"))
      return
    }
    const threshold = getPromptProThreshold()
    const skipProfessionalization = prompt.length > threshold
    const count = s.studioOpen ? 1 : s.imageVariantCount
    const tplRefSeed = CAMPAIGN_MODE && Array.isArray(selectedTpl?.imageUrls)
      ? selectedTpl.imageUrls.map((u) => String(u || "").trim()).filter(Boolean)
      : []
    const forcedRefs = Array.isArray(opts.referenceUrls)
      ? opts.referenceUrls.map((u) => String(u || "").trim()).filter(Boolean)
      : []
    const requestedOutputSize = String(opts.outputSize || "").trim()
    const campaignOutputSize =
      CAMPAIGN_MODE && (requestedOutputSize || tplRefSeed.length)
        ? requestedOutputSize || CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE
        : ""
    /** Sosyal medya tarafında: önce şablonun outputSize'ı, yoksa publishTargets'tan otomatik. */
    let socialOutputSize = ""
    if (!CAMPAIGN_MODE) {
      if (selectedTpl?.outputSize) {
        socialOutputSize = resolveOutputSize(selectedTpl.outputSize)
      } else {
        const pt = s.publishTargets || {}
        if (pt.instagramStory && !pt.instagramPost) socialOutputSize = resolveOutputSize("story")
        else if (pt.instagramPost) socialOutputSize = resolveOutputSize("post_4_5")
        else socialOutputSize = resolveOutputSize("square")
      }
    }
    const finalOutputSize = campaignOutputSize || socialOutputSize
    const useRef = Boolean(opts.forceReference || (s.useSelectedAsReference && s.imageUrl.trim()) || tplRefSeed.length > 0 || forcedRefs.length > 0)
    const jobKind = useRef ? "reference" : "generate"
    let celeryQueued = false
    let sidFromVisual = ""
    s.composerBusy = true
    writeVisualPendingHint(jobKind)
    writeImageHttpInFlight()
    setStatus(T("composerVisualGenerateWorking"))
    paintModals()
    let resultImgs = []
    try {
      const path = useRef ? "/social-media/flow/generate-from-reference" : "/social-media/flow/generate-images"
      const refUrlPrimary = (s.imageUrl || "").trim()
      const order = s.assetOrder || []
      const checked = (s.referenceCheckedUrls || []).filter((b) => order.includes(b))
      const refSet =
        useRef && checked.length > 0
          ? [...new Set(checked.map((u) => String(u || "").trim()).filter(Boolean))]
          : useRef && refUrlPrimary
            ? [refUrlPrimary]
            : []
      const mergedRefSet = [...new Set([...forcedRefs, ...tplRefSeed, ...refSet])].filter(Boolean)
      const refUrl = mergedRefSet[0] || refUrlPrimary
      const extraRefs = mergedRefSet.length > 1 ? mergedRefSet.slice(1).filter((u) => u && u !== refUrl) : []
      const refExtras = useRef && extraRefs.length ? { reference_image_urls: extraRefs } : {}
      const body = useRef && refUrl
        ? {
            reference_image_url: refUrl,
            ...refExtras,
            prompt,
            count,
            mode: "background",
            skip_professionalization: skipProfessionalization,
            ...(finalOutputSize ? { output_size: finalOutputSize, banner_size: finalOutputSize } : {}),
            ...buildIntegration(account),
          }
        : {
            prompt,
            count,
            platform: "feed",
            use_gpt: skipProfessionalization,
            ...(finalOutputSize ? { output_size: finalOutputSize, banner_size: finalOutputSize } : {}),
            ...buildIntegration(account),
          }
      const raw = await apiRequest(path, {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify(body),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), jobKind, currentComposerPendingMeta())
        setStatus(cfg().uiLocale === "en" ? "Queued; task continues in the background." : "Arka plana alındı; görev arka planda sürüyor.")
        return
      }
      const data = await resolveQueued(raw, 2000, 600000)
      sidFromVisual = String(data.session_id ?? "").trim()
      resultImgs = Array.isArray(data.images)
        ? data.images.map((x) => (typeof x === "string" ? x : x && x.url)).filter(Boolean)
        : []
      const readyMsg =
        resultImgs.length && jobKind === "reference"
          ? T("composerRefVariantsOk").replace("{n}", String(resultImgs.length))
          : resultImgs.length
            ? T("composerImagesReady").replace("{n}", String(resultImgs.length))
            : T("composerImagesIssue")
      setStatus(readyMsg)
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerVisualGenFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (sidFromVisual) s.sessionId = sidFromVisual
      if (resultImgs.length) {
        appendAiUrls(resultImgs)
        s.imageUrl = resultImgs[0]
        syncAssetOrderFromCollections()
      }
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerGenerateCampaignBanner() {
    const campaignRaw = activeCampaign()
    let campaign = campaignRaw
    if (CAMPAIGN_MODE && campaignRaw && typeof campaignRaw === "object") {
      const cdIn =
        campaignRaw.campaign_dates && typeof campaignRaw.campaign_dates === "object"
          ? { ...campaignRaw.campaign_dates }
          : {}
      const sStart = String(s.campaignStartDate || "").trim()
      const sEnd = String(s.campaignEndDate || "").trim()
      const hasStart = String(cdIn.start_date || cdIn.startDate || "").trim()
      const hasEnd = String(cdIn.end_date || cdIn.endDate || "").trim()
      const cd = { ...cdIn }
      if (!hasStart && sStart) cd.start_date = sStart
      if (!hasEnd && sEnd) cd.end_date = sEnd
      campaign = { ...campaignRaw, campaign_dates: cd }
    }
    const selectedTpl = selectedReviseTemplate()
    const layoutUrl = String(selectedTpl?.imageUrls?.[0] || "").trim()
    if (!layoutUrl) {
      setStatus(T("tplCampaignBannerNeedTemplate"))
      return
    }
    const account = activeAccount()
    let prompt = withCampaignBannerConstraint(
      buildCampaignBannerPrompt(campaign, {
        templatePrompt: String(selectedTpl?.prompt || "").trim(),
        reviseFeedback: s.reviseFeedback,
        directImagePrompt: s.directImagePrompt,
      }),
    )
    if (!String(prompt || "").trim()) {
      setStatus(T("composerReviseFeedbackRequired"))
      return
    }
    /** Backend `feedback` alanı 10000 char max — savunma fallback. */
    if (prompt.length > 10000) prompt = prompt.slice(0, 9990) + "…"
    const orderRefs = (s.assetOrder || [])
      .map((base) => String((s.selectedRevisionByBase && s.selectedRevisionByBase[base]) || base || "").trim())
      .filter(Boolean)
    const campaignRefs = campaignMediaList(campaign)
    const checkedRefs = (s.referenceCheckedUrls || [])
      .map((base) => String((s.selectedRevisionByBase && s.selectedRevisionByBase[base]) || base || "").trim())
      .filter(Boolean)
    const imgRef = String(s.imageUrl || "").trim()
    const orderRefsNoDupImg = orderRefs.filter((u) => u !== imgRef)
    const tail = [...campaignRefs, ...checkedRefs, ...orderRefsNoDupImg, imgRef].filter(Boolean)
    const refMerged = buildCampaignBannerReferenceImageList(layoutUrl, selectedTpl, tail)
    const refUrls = refMerged.length ? refMerged : undefined
    const currentBase = resolveCampaignBannerRevisionBase(layoutUrl)
    let celeryQueued = false
    let sidFromRevise = ""
    s.composerBusy = true
    writeVisualPendingHint("revise")
    writeImageHttpInFlight()
    setStatus(T("composerVisualReviseWorking"))
    paintModals()
    let reviseImgs = []
    try {
      const raw = await apiRequest("/social-media/flow/revise-image", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          image_url: layoutUrl,
          feedback: prompt,
          count: 1,
          revision_context: "campaign_banner",
          ...(refUrls && refUrls.length ? { reference_image_urls: refUrls } : {}),
          output_size: CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE,
          banner_size: CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE,
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), "revise", { ...(currentComposerPendingMeta() || {}), baseUrl: currentBase })
        setStatus(cfg().uiLocale === "en" ? "Revise queued in background." : "Revize arka plana alındı.")
        return
      }
      const data = await resolveQueued(raw, 2000, 600000)
      sidFromRevise = String(data.session_id ?? "").trim()
      reviseImgs = Array.isArray(data.images)
        ? data.images.map((x) => (typeof x === "string" ? x : x && x.url)).filter(Boolean)
        : []
      if (reviseImgs.length) {
        mergeCampaignRevisionMapEntry(currentBase, layoutUrl, reviseImgs)
        s.selectedRevisionByBase = {
          ...(s.selectedRevisionByBase || {}),
          [currentBase]: reviseImgs[0],
        }
      }
      setStatus(reviseImgs.length ? T("composerReviseOk").replace("{n}", String(reviseImgs.length)) : T("composerReviseFail"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerReviseFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (sidFromRevise) s.sessionId = sidFromRevise
      if (reviseImgs.length) {
        appendAiUrls(reviseImgs)
        s.imageUrl = reviseImgs[0]
        syncAssetOrderFromCollections()
      }
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerReviseImage() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account && !CAMPAIGN_MODE) {
      setStatus(T("composerNeedAccount"))
      return
    }
    const img = (s.imageUrl || "").trim()
    const tplForPrompt = selectedReviseTemplate()
    const layoutUrl = String(tplForPrompt?.imageUrls?.[0] || "").trim()
    /** Campaign: seed OpenAI edit from studio main image so copy/catalog changes are visible; layout stays in refs. */
    let primaryReviseUrl = img
    if (CAMPAIGN_MODE && layoutUrl) {
      primaryReviseUrl = img && !looksLikeVideoUrl(img) ? img : layoutUrl
    }
    if (!primaryReviseUrl || looksLikeVideoUrl(primaryReviseUrl)) {
      setStatus(T("composerReviseVideoNotSupported"))
      return
    }
    let fb = withCampaignBannerConstraint((s.reviseFeedback || "").trim() || String(tplForPrompt?.prompt || "").trim())
    if (CAMPAIGN_MODE) {
      /** Kampanya banner revizesinde de CAMPAIGN_DATA (fiyat/tarih) JSON'unu otomatik enjekte et. */
      const campaign = activeCampaign()
      if (campaign) {
        fb = withCampaignBannerConstraint(
          buildCampaignBannerPrompt(campaign, {
            templatePrompt: String(tplForPrompt?.prompt || "").trim(),
            reviseFeedback: fb,
            directImagePrompt: "",
          }),
        )
      }
      fb = appendCampaignReviseLayoutMismatchHint(fb, img, layoutUrl)
    }
    if (!fb) {
      setStatus(T("composerReviseFeedbackRequired"))
      return
    }
    /** Backend `feedback` alanı 10000 char max — savunma fallback: prompt parçaları beklenmedik biçimde uzun ise kırp. */
    if (fb.length > 10000) fb = fb.slice(0, 9990) + "…"
    const order = s.assetOrder || []
    const displayUrlForBase = (base) => String((s.selectedRevisionByBase && s.selectedRevisionByBase[base]) || base || "").trim()
    const railExtras =
      s.useSelectedRefsForRevise && Array.isArray(s.referenceCheckedUrls)
        ? [
            ...new Set(
              s.referenceCheckedUrls
                .filter((b) => order.includes(b))
                .map((u) => displayUrlForBase(String(u || "").trim()))
                .filter((u) => u && u !== img),
            ),
          ]
        : []
    const selectedTpl = selectedReviseTemplate()
    const tplExtras = CAMPAIGN_MODE
      ? buildCampaignTemplateRefsExcludingMain(selectedTpl, primaryReviseUrl)
      : Array.isArray(selectedTpl?.imageUrls)
        ? selectedTpl.imageUrls.map((u) => String(u || "").trim()).filter((u) => u && u !== img)
        : []
    const refMerged = [...new Set([...tplExtras, ...railExtras])].filter(Boolean)
    const refUrls = refMerged.length ? refMerged : undefined
    const currentBase = CAMPAIGN_MODE
      ? resolveCampaignBannerRevisionBase(layoutUrl)
      : String(findRevisionBase(img) || "").trim() || img
    let celeryQueued = false
    let sidFromRevise = ""
    s.composerBusy = true
    writeVisualPendingHint("revise")
    writeImageHttpInFlight()
    setStatus(T("composerVisualReviseWorking"))
    paintModals()
    let reviseImgs = []
    try {
      const raw = await apiRequest("/social-media/flow/revise-image", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          image_url: primaryReviseUrl,
          feedback: fb,
          count: 1,
          ...(refUrls && refUrls.length ? { reference_image_urls: refUrls } : {}),
          ...(CAMPAIGN_MODE
            ? {
                revision_context: "campaign_banner",
                output_size: CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE,
                banner_size: CAMPAIGN_BANNER_REVISE_OUTPUT_SIZE,
              }
            : (() => {
                /** Sosyal medya revize: şablonun outputSize'ı varsa onu, yoksa publishTargets'tan otomatik. */
                let size = ""
                if (selectedTpl?.outputSize) {
                  size = resolveOutputSize(selectedTpl.outputSize)
                } else {
                  const pt = s.publishTargets || {}
                  if (pt.instagramStory && !pt.instagramPost) size = resolveOutputSize("story")
                  else if (pt.instagramPost) size = resolveOutputSize("post_4_5")
                  else size = resolveOutputSize("square")
                }
                return size ? { output_size: size, banner_size: size } : {}
              })()),
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), "revise", { ...(currentComposerPendingMeta() || {}), baseUrl: currentBase })
        setStatus(cfg().uiLocale === "en" ? "Revise queued in background." : "Revize arka plana alındı.")
        return
      }
      const data = await resolveQueued(raw, 2000, 600000)
      sidFromRevise = String(data.session_id ?? "").trim()
      reviseImgs = Array.isArray(data.images)
        ? data.images.map((x) => (typeof x === "string" ? x : x && x.url)).filter(Boolean)
        : []
      if (reviseImgs.length) {
        mergeCampaignRevisionMapEntry(currentBase, layoutUrl, reviseImgs)
        s.selectedRevisionByBase = {
          ...(s.selectedRevisionByBase || {}),
          [currentBase]: reviseImgs[0],
        }
      }
      setStatus(reviseImgs.length ? T("composerReviseOk").replace("{n}", String(reviseImgs.length)) : T("composerReviseFail"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerReviseFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (sidFromRevise) s.sessionId = sidFromRevise
      if (reviseImgs.length) {
        if (CAMPAIGN_MODE) appendAiUrls(reviseImgs)
        s.imageUrl = reviseImgs[0]
        syncAssetOrderFromCollections()
      }
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerVideoFromText() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account) {
      setStatus(T("composerNeedAccount"))
      return
    }
    if (!lsKeyFal()) {
      setStatus(T("composerVideoNeedFal"))
      return
    }
    if (studioRailHasVideo()) {
      setStatus(T("composerVideoRailFull"))
      return
    }
    const p = (s.directImagePrompt || "").trim()
    if (!p) {
      setStatus(T("composerVisualPromptRequired"))
      return
    }
    let celeryQueued = false
    s.composerBusy = true
    writeVisualPendingHint("video")
    writeImageHttpInFlight()
    setStatus(T("composerVideoGenerateWorking"))
    paintModals()
    let videoUrlOut = ""
    try {
      const raw = await apiRequest("/social-media/video/generate", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          prompt: p,
          duration_sec: Math.min(15, Math.max(3, Math.round(Number(s.videoDurationSec) || 5))),
          generate_audio: s.videoGenerateAudio !== false,
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), "video", currentComposerPendingMeta())
        setStatus(cfg().uiLocale === "en" ? "Video queued in background." : "Video arka plana alındı.")
        return
      }
      const data = await resolveQueued(raw, 2000, 900000)
      videoUrlOut = String(data.video_url ?? data.url ?? "").trim()
      if (videoUrlOut) setStatus(T("composerVideoReady"))
      else setStatus(T("composerImagesIssue"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerVisualGenFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (videoUrlOut) applyGeneratedVideoUrl(videoUrlOut)
      if (videoUrlOut) persistActiveDraftQuiet()
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerVideoFromReference() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account) {
      setStatus(T("composerNeedAccount"))
      return
    }
    if (!lsKeyFal()) {
      setStatus(T("composerVideoNeedFal"))
      return
    }
    if (studioRailHasVideo()) {
      setStatus(T("composerVideoRailFull"))
      return
    }
    const ref = (s.imageUrl || "").trim()
    if (!ref || looksLikeVideoUrl(ref)) {
      setStatus(T("composerVideoNeedRef"))
      return
    }
    const p =
      (s.directImagePrompt || "").trim() ||
      (cfg().uiLocale === "en" ? "Subtle parallax, warm light, product-focused motion." : "Hafif paralaks, sıcak ışık, ürün odaklı hareket.")
    let celeryQueued = false
    s.composerBusy = true
    writeVisualPendingHint("video")
    writeImageHttpInFlight()
    setStatus(T("composerVideoGenerateWorking"))
    paintModals()
    let refVideoUrl = ""
    try {
      const raw = await apiRequest("/social-media/video/generate", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          prompt: p,
          image_url: ref,
          duration_sec: Math.min(15, Math.max(3, Math.round(Number(s.videoDurationSec) || 5))),
          generate_audio: s.videoGenerateAudio !== false,
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), "video", currentComposerPendingMeta())
        setStatus(cfg().uiLocale === "en" ? "Video queued in background." : "Video arka plana alındı.")
        return
      }
      const data = await resolveQueued(raw, 2000, 900000)
      refVideoUrl = String(data.video_url ?? data.url ?? "").trim()
      if (refVideoUrl) setStatus(T("composerVideoReady"))
      else setStatus(T("composerImagesIssue"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerVisualGenFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (refVideoUrl) applyGeneratedVideoUrl(refVideoUrl)
      if (refVideoUrl) persistActiveDraftQuiet()
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composerHolidayVideo() {
    syncOpenModalsFromDom()
    const account = activeAccount()
    if (!account) {
      setStatus(T("composerNeedAccount"))
      return
    }
    const name = (s.holidayVideoName || "").trim()
    const dk = (s.holidayVideoDate || "").trim()
    if (!name || !/^\d{4}-\d{2}-\d{2}$/.test(dk)) {
      setStatus(T("composerVideoHolidayMissing"))
      return
    }
    if (!lsKeyOpenAi()) {
      setStatus(T("holidayDraftsNeedOpenAI"))
      return
    }
    if (!lsKeyFal()) {
      setStatus(T("composerVideoNeedFal"))
      return
    }
    if (studioRailHasVideo()) {
      setStatus(T("composerVideoRailFull"))
      return
    }
    let celeryQueued = false
    s.composerBusy = true
    writeVisualPendingHint("video")
    writeImageHttpInFlight()
    setStatus(T("composerVideoGenerateWorking"))
    paintModals()
    let holVideo = ""
    let holCaption = ""
    try {
      const raw = await apiRequest("/social-media/holiday/generate", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          holiday_name: name,
          date_key: dk,
          locale: cfg().uiLocale || "tr",
          generate_image: false,
          generate_video: true,
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        celeryQueued = true
        queuePendingComposerTask(raw.task_id.trim(), "video", currentComposerPendingMeta())
        setStatus(cfg().uiLocale === "en" ? "Holiday video queued." : "Tatil videosu kuyruğa alındı.")
        return
      }
      const data = await resolveQueued(raw, 2000, 900000)
      holVideo = String(data.video_url ?? "").trim()
      holCaption = String(data.caption ?? "").trim()
      if (holVideo) setStatus(T("composerVideoReady"))
      else setStatus(T("composerImagesIssue"))
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("composerVisualGenFail"))
    } finally {
      clearImageHttpInFlight()
      if (!celeryQueued) clearVisualPendingHint()
      syncOpenModalsFromDom()
      if (holCaption) s.caption = holCaption
      if (holVideo) applyGeneratedVideoUrl(holVideo)
      if (holVideo || holCaption) persistActiveDraftQuiet()
      s.composerBusy = false
      paintModals(true)
      paintTaskBanner()
    }
  }

  async function composePublishInstagram() {
    if (CAMPAIGN_MODE) {
      await composePublishCampaign()
      return
    }
    syncOpenModalsFromDom()
    if (!s.publishTargets.instagramPost && !s.publishTargets.instagramStory && !s.publishTargets.facebookPost) {
      setStatus(
        cfg().uiLocale === "en"
          ? "Select Instagram Post, Story, or Facebook before publishing."
          : "Instagram Post, Story veya Facebook seçilmeden yayın yapılamaz.",
      )
      return
    }
    const imgs = composerOrderedDisplayUrls()
    const img = imgs[0] || (s.imageUrl || "").trim()
    if (!img) {
      setStatus(T("msgPostNeedImageCaption"))
      return
    }
    if (s.publishTargets.instagramPost && !s.caption.trim()) {
      setStatus(T("msgPostNeedImageCaption"))
      return
    }
    const account = activeAccount()
    if (!account) {
      setStatus(T("msgPostNeedAccount"))
      return
    }
    s.studioPublishBusy = true
    paintModals()
    setStatus(T("msgPublishingInstagram"))
    try {
      const { ok, status, data, rawText, jsonError } = await postInstagram({
        image_url: imgs[0] ?? img,
        image_urls: imgs,
        caption: s.caption.trim(),
        publish_targets: {
          instagram_post: s.publishTargets.instagramPost,
          instagram_story: s.publishTargets.instagramStory,
          facebook_post: s.publishTargets.facebookPost,
        },
        ...graphPublishIdsForPostBody(),
        ...buildIntegration(account),
      })
      if (jsonError) {
        setStatus(
          T("msgCalendarJsonStoredError")
            .replace("{status}", String(status))
            .replace("{snippet}", rawText.slice(0, 200)),
        )
      } else if (ok || (!jsonError && status >= 200 && status < 400 && hasPostApiResults(data))) {
        setStatus(T("msgInstagramPublished"))
      } else {
        const err = String((data && (data.detail || data.error)) || "publish").slice(0, 500)
        setStatus(err || T("msgPostPartialUnknown"))
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("planDeleteFailed"))
    } finally {
      s.studioPublishBusy = false
      paintModals()
    }
  }

  async function composePublishCampaign() {
    syncOpenModalsFromDom()
    const campaignAccount = activeAccount()
    const store = activeCampaignStore()
    const campaign = activeCampaign()
    if (!campaignAccount) {
      setStatus("Kampanya hesabi secimi zorunlu.")
      return
    }
    if (!store || !campaign) {
      setStatus("Magaza ve kampanya secimi zorunlu.")
      return
    }
    if (!isPublishableCampaignStoreId(store.id)) {
      setStatus("Banner yayini icin listeden sayisal ID'li bir magaza secin (modul satirlari kullanilamaz).")
      return
    }
    if (!s.campaignStartDate || !s.campaignEndDate) {
      setStatus("Kampanya tarih araligi zorunlu.")
      return
    }
    const imgs = composerOrderedDisplayUrls()
    const img = imgs[0] || (s.imageUrl || "").trim()
    if (!img) {
      setStatus(T("msgPostNeedImageCaption"))
      return
    }
    const campaignCaption = s.caption.trim() || String(campaign.product || campaign.id || "Kampanya banner").trim()
    s.studioPublishBusy = true
    paintModals()
    setStatus("Kampanya banner yayinlaniyor...")
    try {
      await campaignPublish({
        campaign_account_id: campaignAccount.id,
        store_id: store.id,
        campaign_id: campaign.id,
        image_url: img,
        image_urls: [img],
        caption: campaignCaption,
        start_date: s.campaignStartDate,
        end_date: s.campaignEndDate,
        banner_size: "1600x704",
        campaign_name: String(campaign.product || campaign.id || ""),
        redirect_url: String(campaign.redirect_url || ""),
        pricing: campaign.pricing || {},
      })
      const basePayload = {
        accountId: String(campaignAccount.id || ""),
        accountName: String(campaignAccount.name || "Campaign Account"),
        campaignAccountId: String(campaignAccount.id || ""),
        campaignAccountName: String(campaignAccount.name || ""),
        campaignStoreId: String(store.id || ""),
        campaignStoreName: String(store.name || ""),
        campaignId: String(campaign.id || ""),
        campaignName: String(campaign.product || campaign.id || ""),
        campaignStartDate: String(s.campaignStartDate || ""),
        campaignEndDate: String(s.campaignEndDate || ""),
        campaignRedirectUrl: String(campaign.redirect_url || ""),
        campaignPricing: campaign.pricing || {},
        bannerSize: "1600x704",
        date: s.selectedDate,
        time: (s.scheduledTime || "12:00").trim() || "12:00",
        prompt: s.prompt.trim(),
        caption: campaignCaption,
        imageUrl: img,
        imageUrls: [img],
        publishStatus: "published",
        status: "published",
        approvalStatus: "approved",
        publishTargets: { instagramPost: false, instagramStory: false, facebookPost: false },
        source: "campaign_banner",
        publishedAt: TS,
        publishStartedAt: DEL,
        lastPublishError: DEL,
        updatedAt: TS,
      }
      if (s.editingPostId) {
        await socialPatchFields(scheduledPostsCollection(), s.editingPostId, basePayload)
      } else {
        const createPayload = {
          ...basePayload,
          publishedAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          createdAt: new Date().toISOString(),
        }
        delete createPayload.publishStartedAt
        delete createPayload.lastPublishError
        await socialCreate(scheduledPostsCollection(), createPayload)
      }
      setStatus("Kampanya banner yayinlandi.")
      closeStudio(false)
      await refreshData()
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Kampanya yayini basarisiz.")
    } finally {
      s.studioPublishBusy = false
      paintModals()
    }
  }

  return {
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
  }
}
