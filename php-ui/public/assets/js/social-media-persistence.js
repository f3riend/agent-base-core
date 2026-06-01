import { T } from "./social-media-api.js"
import {
  APP_SETTINGS_COLLECTION,
  APP_SETTINGS_DOC_ID,
} from "./social-media-constants.js"
import {
  accountsCollection,
  CAMPAIGN_MODE,
  draftsCollection,
  scheduledPostsCollection,
  scheduledPostsCollections,
} from "./social-media-campaign-utils.js"
import { debugLog } from "./social-media-runtime.js"
import {
  mergePostImageListOrdered,
  resolvePostLifecycle,
  withInferredRevisionState,
} from "./social-media-post-utils.js"
import { mapAccount, mapAutomationEvent, mapDraft, mapPost, mapWorkflow } from "./social-media-mappers.js"
import { campaignCatalogCredentialsReady } from "./social-media-selectors.js"
import {
  lastServerDataSig,
  s,
  setLastServerDataSig,
} from "./social-media-state.js"
import {
  campaignLoadCatalog,
  campaignLoadStoreDiscountedProducts,
  clearCampaignCatalogCache,
  usesStoreDiscountedProductCatalog,
  collectManagedUrlsForPost,
  deleteStorageImages,
  DEL,
  socialCreate,
  socialDelete,
  socialList,
  socialPatchFields,
  socialPut,
  TS,
  automationListEvents,
  automationListWorkflows,
} from "./social-media-data.js"

function splitDoc(entry) {
  const data = { ...entry }
  const id = String(data.id ?? "")
  delete data.id
  return { id, data }
}

export function createPersistenceActions(deps) {
  const {
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
  } = deps

  /** Post yazma yolu — story_scheduled_posts'tan yüklenen kartlar için
   *  delete/move/patch'i doğru koleksiyona yönlendir. Bilinmiyorsa default
   *  scheduledPostsCollection(). */
  function postWriteCollection(postOrId) {
    const id = typeof postOrId === "string" || typeof postOrId === "number"
      ? String(postOrId)
      : String(postOrId?.id || "")
    if (id) {
      const p = (s.posts || []).find((x) => String(x.id) === id)
      if (p && p._collection) return p._collection
    }
    return scheduledPostsCollection()
  }

  function closeStudio(saveDraft) {
    syncOpenModalsFromDom()
    if (saveDraft && s.studioOpen && !s.editingPostId) saveComposerDraftQuiet()
    s.studioOpen = false
    s.editingPostId = null
    s.composerBusy = false
    s.studioPublishBusy = false
    paint()
    if (window.__SM_EMBED_MODE__ === "approvals") {
      window.dispatchEvent(new CustomEvent("sm-studio-closed"))
    }
  }

  function saveComposerDraftQuiet(force = false) {
    if ((!s.studioOpen && !force) || s.editingPostId) return
    const has =
      s.caption.trim() ||
      s.prompt.trim() ||
      s.directImagePrompt.trim() ||
      s.imageUrl.trim() ||
      s.lastTopic.trim()
    if (!has) return
    const account = activeAccount()
    const draftId = s.activeDraftId || `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const existingDraft = s.drafts.find((x) => x.id === draftId)
    const existingSnap = existingDraft && existingDraft.snapshot && typeof existingDraft.snapshot === "object" ? existingDraft.snapshot : {}
    const normalizeUrls = (arr) =>
      (Array.isArray(arr) ? arr : []).map((u) => String(u || "").trim()).filter(Boolean)
    const orderedForSave = normalizeUrls(composerOrderedDisplayUrls())
    const imagePrimaryForSave = orderedForSave[0] || String(s.imageUrl || "").trim()
    const imageUrlsForSave = mergePostImageListOrdered(imagePrimaryForSave, orderedForSave)
    const currentRevMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
    const currentSelRev = s.selectedRevisionByBase && typeof s.selectedRevisionByBase === "object" ? s.selectedRevisionByBase : {}
    const existingRevMap = existingSnap.revisionMap && typeof existingSnap.revisionMap === "object" ? existingSnap.revisionMap : {}
    const snapRevMap = { ...existingRevMap }
    for (const [base, items] of Object.entries(currentRevMap)) {
      const b = String(base || "").trim()
      if (!b) continue
      const prevItems = Array.isArray(snapRevMap[b]) ? snapRevMap[b].map((u) => String(u || "").trim()).filter(Boolean) : []
      const curItems = Array.isArray(items) ? items.map((u) => String(u || "").trim()).filter(Boolean) : []
      const mergedItems = [...new Set([...prevItems, ...curItems])].filter(Boolean)
      if (mergedItems.length > 0) snapRevMap[b] = mergedItems
    }
    if (Object.keys(snapRevMap).length === 0 && imageUrlsForSave.length > 0) {
      const inferred = withInferredRevisionState(imagePrimaryForSave, imageUrlsForSave, {}, {})
      if (inferred.revisionMap && Object.keys(inferred.revisionMap).length) {
        Object.assign(snapRevMap, inferred.revisionMap)
      }
    }
    const snapSelRev =
      {
        ...(existingSnap.selectedRevisionByBase && typeof existingSnap.selectedRevisionByBase === "object"
          ? existingSnap.selectedRevisionByBase
          : {}),
        ...currentSelRev,
      }
    const snapAssetOrderCurrent = normalizeUrls(s.assetOrder)
    const snapAssetOrder = snapAssetOrderCurrent.length
      ? [...new Set(snapAssetOrderCurrent)]
      : imageUrlsForSave
    const snapUploadedCurrent = normalizeUrls(s.uploadedImageUrls)
    const snapAiCurrent = normalizeUrls(s.aiImageUrls)
    const body = {
      accountId: s.activeAccountId,
      accountName: account?.name || "",
      date: s.selectedDate,
      time: s.scheduledTime,
      prompt: s.prompt,
      caption: s.caption,
      imageUrl: (() => {
        const first = imageUrlsForSave[0]
        return first || imagePrimaryForSave
      })(),
      imageUrls: imageUrlsForSave,
      chatInput: "",
      chatMessages: [],
      composerApproved: s.composerApproved,
      composerPublishTargets: s.publishTargets,
      snapshotJson: JSON.stringify({
        studioTab: s.studioTab,
        step: s.composerStep,
        captionMode: s.captionMode,
        mediaMode: s.mediaMode,
        visualOutputKind: s.visualOutputKind,
        generateSubTab: s.generateSubTab,
        videoAiMode: s.videoAiMode,
        directImagePrompt: s.directImagePrompt,
        lastTopic: s.lastTopic,
        selectedTicketId: s.selectedTicketId,
        useSelectedAsReference: s.useSelectedAsReference,
        useSelectedRefsForRevise: s.useSelectedRefsForRevise,
        holidayVideoName: s.holidayVideoName,
        holidayVideoDate: s.holidayVideoDate,
        videoDurationSec: s.videoDurationSec,
        videoGenerateAudio: s.videoGenerateAudio,
        revisionMap: snapRevMap,
        selectedRevisionByBase: snapSelRev,
        assetOrder: snapAssetOrder,
        uploadedImageUrls: snapUploadedCurrent,
        aiImageUrls: snapAiCurrent,
        selectedTemplateId: s.selectedTemplateId != null ? s.selectedTemplateId : null,
        selectedTemplateScope: String(s.selectedTemplateScope || "user").trim() || "user",
        referenceCheckedUrls: normalizeUrls(s.referenceCheckedUrls),
      }),
      updatedAt: new Date().toISOString(),
    }
    const optimisticDraft = {
      id: draftId,
      accountId: String(body.accountId || ""),
      accountName: String(body.accountName || ""),
      campaignAccountId: String(body.accountId || ""),
      campaignStoreId: String(s.campaignStoreId || ""),
      campaignId: String(s.campaignId || ""),
      campaignStartDate: String(s.campaignStartDate || ""),
      campaignEndDate: String(s.campaignEndDate || ""),
      date: String(body.date || ""),
      time: String(body.time || "12:00"),
      prompt: String(body.prompt || ""),
      caption: String(body.caption || ""),
      imageUrl: String(body.imageUrl || ""),
      imageUrls: Array.isArray(body.imageUrls) ? body.imageUrls : [],
      snapshot: JSON.parse(body.snapshotJson || "{}"),
    }
    const dIdx = s.drafts.findIndex((x) => x.id === draftId)
    if (dIdx >= 0) s.drafts[dIdx] = optimisticDraft
    else s.drafts.unshift(optimisticDraft)
    paintDayPanel()
    void socialPut(draftsCollection(), draftId, body, true).catch(() => {})
    if (!s.activeDraftId) s.activeDraftId = draftId
  }

  function currentComposerPendingMeta() {
    if (s.editingPostId) {
      const postId = String(s.editingPostId || "").trim()
      return postId ? { postId } : null
    }
    if (!s.activeDraftId) saveComposerDraftQuiet(true)
    const draftId = String(s.activeDraftId || "").trim()
    return draftId ? { draftId } : null
  }

  function persistActiveDraftQuiet() {
    if (s.editingPostId) return
    if (!s.activeDraftId) return
    saveComposerDraftQuiet(true)
  }

  async function persistPendingVisualToDraft(draftId, payload) {
    const id = String(draftId || "").trim()
    if (!id) return
    debugLog("draft.persist.start", { draftId: id, kind: payload && payload.kind })
    const d = s.drafts.find((x) => x.id === id)
    const prevUrls = d
      ? Array.isArray(d.imageUrls)
        ? d.imageUrls.map((u) => String(u || "").trim()).filter(Boolean)
        : d.imageUrl
          ? [String(d.imageUrl || "").trim()]
          : []
      : []
    let nextUrls = prevUrls
    const prevSnap = d && d.snapshot && typeof d.snapshot === "object" ? d.snapshot : {}
    let nextSnap = { ...prevSnap }
    if (payload.kind === "revise" && Array.isArray(payload.urls) && payload.urls.length) {
      const base = String(payload.baseUrl || d?.imageUrl || prevUrls[0] || "").trim()
      if (base) {
        const prevRevMap = prevSnap.revisionMap && typeof prevSnap.revisionMap === "object" ? prevSnap.revisionMap : {}
        const prevSel = prevSnap.selectedRevisionByBase && typeof prevSnap.selectedRevisionByBase === "object"
          ? prevSnap.selectedRevisionByBase
          : {}
        const prevList = Array.isArray(prevRevMap[base]) ? prevRevMap[base].map((u) => String(u || "").trim()).filter(Boolean) : [base]
        const add = payload.urls.map((u) => String(u || "").trim()).filter(Boolean)
        const merged = [...new Set([...prevList, ...add])].filter(Boolean)
        const primaryRev = add[0] || String(d?.imageUrl || "").trim()
        nextSnap = {
          ...prevSnap,
          revisionMap: { ...prevRevMap, [base]: merged },
          selectedRevisionByBase: { ...prevSel, [base]: primaryRev },
        }
        const nextDraftUrls = [...new Set([...prevUrls, ...add])].filter(Boolean)
        await socialPatchFields(draftsCollection(), id, {
          imageUrl: primaryRev,
          imageUrls: nextDraftUrls.length ? nextDraftUrls : prevUrls,
          ...(payload.caption ? { caption: payload.caption } : {}),
          snapshotJson: JSON.stringify(nextSnap),
          updatedAt: TS,
        })
        debugLog("draft.persist.done", { draftId: id, imageUrl: primaryRev, n: nextDraftUrls.length, kind: "revise" })
        return
      }
    } else if (Array.isArray(payload.urls) && payload.urls.length) {
      const add = payload.urls.map((u) => String(u || "").trim()).filter(Boolean)
      if (payload.kind === "video") {
        const keep = prevUrls.filter((u) => !looksLikeVideoUrl(u))
        nextUrls = mergePostImageListOrdered(add[0] || "", [...keep, ...add])
      } else {
        nextUrls = mergePostImageListOrdered(add[0] || prevUrls[0] || "", [...prevUrls, ...add])
      }
    } else if (payload.kind === "video" && payload.url) {
      const v = String(payload.url || "").trim()
      if (v) {
        const keep = prevUrls.filter((u) => !looksLikeVideoUrl(u))
        nextUrls = mergePostImageListOrdered(v, [...keep, v])
      }
    }
    const primary = (nextUrls[0] || payload.url || "").trim()
    nextSnap = {
      ...nextSnap,
      assetOrder: nextUrls,
    }
    await socialPatchFields(draftsCollection(), id, {
      imageUrl: primary,
      imageUrls: nextUrls,
      ...(payload.caption ? { caption: payload.caption } : {}),
      snapshotJson: JSON.stringify(nextSnap),
      updatedAt: TS,
    })
    debugLog("draft.persist.done", { draftId: id, imageUrl: primary, n: nextUrls.length })
  }

  async function persistPendingVisualToPost(postId, payload) {
    const id = String(postId || "").trim()
    if (!id) return
    debugLog("post.persist.start", { postId: id, kind: payload && payload.kind })
    const p = s.posts.find((x) => x.id === id)
    const prevUrls = p
      ? Array.isArray(p.imageUrls)
        ? p.imageUrls.map((u) => String(u || "").trim()).filter(Boolean)
        : p.imageUrl
          ? [String(p.imageUrl || "").trim()]
          : []
      : []
    let nextUrls = prevUrls
    let prevRevSnap = {}
    if (p && typeof p.revisionSnapshotJson === "string" && p.revisionSnapshotJson.trim()) {
      try {
        prevRevSnap = JSON.parse(p.revisionSnapshotJson)
      } catch {
        prevRevSnap = {}
      }
    }
    let nextRevSnap = { ...prevRevSnap }
    if (payload.kind === "revise" && Array.isArray(payload.urls) && payload.urls.length) {
      const base = String(payload.baseUrl || p?.imageUrl || prevUrls[0] || "").trim()
      if (base) {
        const prevRevMap = prevRevSnap.revisionMap && typeof prevRevSnap.revisionMap === "object" ? prevRevSnap.revisionMap : {}
        const prevSel = prevRevSnap.selectedRevisionByBase && typeof prevRevSnap.selectedRevisionByBase === "object"
          ? prevRevSnap.selectedRevisionByBase
          : {}
        const prevList = Array.isArray(prevRevMap[base]) ? prevRevMap[base].map((u) => String(u || "").trim()).filter(Boolean) : [base]
        const add = payload.urls.map((u) => String(u || "").trim()).filter(Boolean)
        const merged = [...new Set([...prevList, ...add])].filter(Boolean)
        const primaryRev = add[0] || String(p?.imageUrl || "").trim()
        nextRevSnap = {
          ...prevRevSnap,
          revisionMap: { ...prevRevMap, [base]: merged },
          selectedRevisionByBase: { ...prevSel, [base]: primaryRev },
        }
        const nextImageUrls = [...new Set([...prevUrls, ...add])].filter(Boolean)
        await socialPatchFields(postWriteCollection(id), id, {
          imageUrl: primaryRev,
          imageUrls: nextImageUrls.length ? nextImageUrls : prevUrls,
          ...(payload.caption ? { caption: payload.caption } : {}),
          revisionSnapshotJson: JSON.stringify(nextRevSnap),
          updatedAt: TS,
        })
        debugLog("post.persist.done", { postId: id, imageUrl: primaryRev, n: nextImageUrls.length, kind: "revise" })
        return
      }
    } else if (Array.isArray(payload.urls) && payload.urls.length) {
      const add = payload.urls.map((u) => String(u || "").trim()).filter(Boolean)
      if (payload.kind === "video") {
        const keep = prevUrls.filter((u) => !looksLikeVideoUrl(u))
        nextUrls = mergePostImageListOrdered(add[0] || "", [...keep, ...add])
      } else {
        nextUrls = mergePostImageListOrdered(add[0] || prevUrls[0] || "", [...prevUrls, ...add])
      }
    } else if (payload.kind === "video" && payload.url) {
      const v = String(payload.url || "").trim()
      if (v) {
        const keep = prevUrls.filter((u) => !looksLikeVideoUrl(u))
        nextUrls = mergePostImageListOrdered(v, [...keep, v])
      }
    }
    const primary = (nextUrls[0] || payload.url || "").trim()
    nextRevSnap = {
      ...nextRevSnap,
      assetOrder: nextUrls,
    }
    await socialPatchFields(postWriteCollection(id), id, {
      imageUrl: primary,
      imageUrls: nextUrls,
      ...(payload.caption ? { caption: payload.caption } : {}),
      revisionSnapshotJson: JSON.stringify(nextRevSnap),
      updatedAt: TS,
    })
    debugLog("post.persist.done", { postId: id, imageUrl: primary, n: nextUrls.length })
  }

  async function refreshData() {
    try {
      const tplCollections = campaignTemplateCollections()
      const settingsPromise = socialList(APP_SETTINGS_COLLECTION).catch((err) => {
        const msg = err instanceof Error ? err.message : String(err || "")
        if (msg) debugLog("app_settings.fetch_failed", { message: msg })
        return []
      })
      const campaignCatalogPromise = CAMPAIGN_MODE
        ? campaignLoadCatalog().catch((err) => {
          const msg = err instanceof Error ? err.message : String(err || "")
          if (msg) debugLog("campaign.catalog.fetch_failed", { message: msg })
          return { stores: [], provider: {} }
        })
        : Promise.resolve({ stores: [], provider: {} })
      const postsPromise = Promise.all(
        scheduledPostsCollections().map((col) =>
          socialList(col).catch(() => []).then((rows) => ({ col, rows: Array.isArray(rows) ? rows : [] })),
        ),
      ).then((groups) => groups.flatMap(({ col, rows }) => rows.map((r) => ({ __col: col, row: r }))))
      const [dRows, tRows, utRows, gtRows, aRows, pRows, wfRows, evRows, settingsRows, campaignCatalog] = await Promise.all([
        socialList(draftsCollection()),
        socialList("tickets"),
        socialList(tplCollections.user),
        socialList(tplCollections.global).catch(() => []),
        socialList(accountsCollection()),
        postsPromise,
        automationListWorkflows().catch(() => []),
        automationListEvents().catch(() => []),
        settingsPromise,
        campaignCatalogPromise,
      ])
      s.drafts = (Array.isArray(dRows) ? dRows : []).map(mapDraft)
      s.tickets = (Array.isArray(tRows) ? tRows : []).map((row) => {
        const { id, data } = splitDoc(row)
        return { id, name: String(data.name ?? ""), description: String(data.description ?? "") }
      })
      const _readOutputSize = (data) => {
        const raw = String(data?.outputSize ?? data?.output_size ?? "").trim()
        return raw || "post_4_5"   /** Eski şablonlarda alan yoksa default Instagram feed (4:5). */
      }
      s.userTemplates = (Array.isArray(utRows) ? utRows : []).map((row) => {
        const { id, data } = splitDoc(row)
        const urls = Array.isArray(data.imageUrls) ? data.imageUrls.map((u) => String(u ?? "").trim()).filter(Boolean) : []
        const title = String(data.title ?? data.name ?? "").trim()
        const prompt = String(data.prompt ?? data.description ?? "").trim()
        return { id, title, prompt, imageUrls: urls, outputSize: _readOutputSize(data) }
      })
      s.globalTemplates = (Array.isArray(gtRows) ? gtRows : []).map((row) => {
        const { id, data } = splitDoc(row)
        const urls = Array.isArray(data.imageUrls) ? data.imageUrls.map((u) => String(u ?? "").trim()).filter(Boolean) : []
        const title = String(data.title ?? data.name ?? "").trim()
        const prompt = String(data.prompt ?? data.description ?? "").trim()
        return { id, title, prompt, imageUrls: urls, outputSize: _readOutputSize(data) }
      })
      s.accounts = (Array.isArray(aRows) ? aRows : []).map(mapAccount)
      if (!s.accounts.some((a) => a.id === s.activeAccountId)) {
        s.activeAccountId = s.accounts[0]?.id || ""
      }
      const prevIds = new Set((Array.isArray(s.posts) ? s.posts : []).map((p) => String(p.id || "")))
      s.posts = (Array.isArray(pRows) ? pRows : []).map((entry) => {
        const post = mapPost(entry.row)
        post._collection = entry.__col
        return post
      })
      s.workflows = (Array.isArray(wfRows) ? wfRows : []).map(mapWorkflow)
      s.automationEvents = (Array.isArray(evRows) ? evRows : []).map(mapAutomationEvent)
      const added = s.posts.filter((p) => p.id && !prevIds.has(String(p.id)))
      if (added.length) {
        const dk = String(added[0].date || "").trim()
        if (dk) {
          s.dropFlashDateKey = dk
          setTimeout(() => {
            if (s.dropFlashDateKey === dk) {
              s.dropFlashDateKey = null
              paintCalendar()
            }
          }, 2200)
        }
      }
      const appSettingsList = Array.isArray(settingsRows) ? settingsRows : []
      const appSettingsDoc =
        appSettingsList.find((row) => String((row && row.id) || "").trim() === APP_SETTINGS_DOC_ID) || appSettingsList[0] || {}
      s.appSettings = {
        openaiApiKey: String((appSettingsDoc && appSettingsDoc.openaiApiKey) || "").trim(),
        falApiKey: String((appSettingsDoc && appSettingsDoc.falApiKey) || "").trim(),
      }
      if (CAMPAIGN_MODE) {
        if (!campaignCatalogCredentialsReady()) {
          clearCampaignCatalogCache()
          s.campaignStores = []
          s.campaignStoreId = ""
          s.campaignId = ""
          s.campaignMediaUrls = []
          s.campaignMediaKey = "::"
          recomputeUnifiedMediaRail({ preferFirst: false })
        } else {
          let effectiveCampaignCatalog = campaignCatalog
          if ((!Array.isArray(effectiveCampaignCatalog?.stores) || !effectiveCampaignCatalog.stores.length) && s.activeAccountId) {
            effectiveCampaignCatalog = await campaignLoadCatalog({ force: true }).catch((err) => {
              const msg = err instanceof Error ? err.message : String(err || "")
              if (msg) debugLog("campaign.catalog.fetch_failed_after_account", { message: msg, accountId: s.activeAccountId })
              return { stores: [], provider: {} }
            })
          }
          const fetchedStores = Array.isArray(effectiveCampaignCatalog?.stores) ? effectiveCampaignCatalog.stores : []
          if (fetchedStores.length || !(Array.isArray(s.campaignStores) && s.campaignStores.length)) {
            s.campaignStores = fetchedStores
          }
          if (!s.campaignStoreId && s.campaignStores[0]) s.campaignStoreId = String(s.campaignStores[0].id || "")
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
            const activeStore = activeCampaignStore()
            if (activeStore && Array.isArray(activeStore.campaigns)) {
              if (!s.campaignId && activeStore.campaigns[0]) {
                const first = activeStore.campaigns[0]
                s.campaignId = String(first.id || first.product || "")
              }
            } else {
              s.campaignId = ""
            }
          }
          const c = activeCampaign()
          if (c) applyCampaignSelectionDetails(c)
          if (c && (!s.assetOrder || s.assetOrder.length === 0) && (!s.uploadedImageUrls || s.uploadedImageUrls.length === 0)) {
            await syncCampaignSelectionToRail()
          }
        }
      }
      if (s.selectedWorkflowId && !s.workflows.some((w) => w.id === s.selectedWorkflowId)) s.selectedWorkflowId = ""
      const sig = computeServerDataSig()
      const unchanged = lastServerDataSig !== undefined && lastServerDataSig === sig
      setLastServerDataSig(sig)
      if (unchanged) paintStatusAndBanner()
      else paint()
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }

  async function movePost(postId, newDate) {
    try {
      await socialPatchFields(postWriteCollection(postId), postId, {
        date: newDate,
        publishStatus: "pending",
        publishedAt: DEL,
        publishStartedAt: DEL,
        lastPublishError: DEL,
      })
      setStatus(T("planMoved"))
      await refreshData()
    } catch {
      setStatus(T("planMoveFailed"))
    }
  }

  async function deletePostById(postId) {
    const post = s.posts.find((p) => p.id === postId)
    if (!post || !window.confirm(T("deletePlanConfirm"))) return
    try {
      await deleteStorageImages(collectManagedUrlsForPost(post))
      await socialDelete(postWriteCollection(post), postId)
      s.posts = s.posts.filter((p) => p.id !== postId)
      if (s.selectedPostId === postId) s.selectedPostId = ""
      paint()
      if (s.editingPostId === postId) closeStudio(false)
      setStatus(T("planDeletedOk"))
      await refreshData()
    } catch {
      setStatus(T("planDeleteFailed"))
    }
  }

  function _calendarNormalizeUrls(arr) {
    return (Array.isArray(arr) ? arr : []).map((u) => String(u || "").trim()).filter(Boolean)
  }

  /** Persist studio revision + template + ref-check state with scheduled posts (campaign + social). */
  function buildRevisionSnapshotJsonForCalendarEntry() {
    const revMap = s.revisionMap && typeof s.revisionMap === "object" ? { ...s.revisionMap } : {}
    const selRev =
      s.selectedRevisionByBase && typeof s.selectedRevisionByBase === "object" ? { ...s.selectedRevisionByBase } : {}
    const assetOrder = _calendarNormalizeUrls(s.assetOrder)
    const refChecked = _calendarNormalizeUrls(s.referenceCheckedUrls)
    const aiSnap = _calendarNormalizeUrls(s.aiImageUrls)
    const payload = {
      revisionMap: revMap,
      selectedRevisionByBase: selRev,
      ...(assetOrder.length ? { assetOrder } : {}),
      ...(aiSnap.length ? { aiImageUrls: aiSnap } : {}),
      ...(s.selectedTemplateId != null && String(s.selectedTemplateId).trim()
        ? {
            selectedTemplateId: s.selectedTemplateId,
            selectedTemplateScope: String(s.selectedTemplateScope || "user").trim() || "user",
          }
        : {}),
      ...(refChecked.length ? { referenceCheckedUrls: refChecked } : {}),
    }
    return JSON.stringify(payload)
  }

  /** All distinct image URLs for the post (primary first) so re-open restores revision rail / slider. */
  function collectExpandedCalendarImageUrls(primary) {
    const p = String(primary || "").trim()
    const seen = new Set()
    const out = []
    const push = (u) => {
      const v = String(u || "").trim()
      if (!v || looksLikeVideoUrl(v) || seen.has(v)) return
      seen.add(v)
      out.push(v)
    }
    if (p) push(p)
    for (const u of _calendarNormalizeUrls(composerOrderedDisplayUrls())) push(u)
    for (const u of _calendarNormalizeUrls(s.assetOrder)) push(u)
    for (const u of _calendarNormalizeUrls(s.aiImageUrls)) push(u)
    const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
    for (const arr of Object.values(revMap)) {
      if (!Array.isArray(arr)) continue
      for (const u of arr) push(u)
    }
    return out.length ? out : p ? [p] : []
  }

  async function saveCalendarEntry() {
    const account = activeAccount()
    const store = activeCampaignStore()
    const campaign = activeCampaign()
    if (!CAMPAIGN_MODE && !account) {
      setStatus(T("msgPostNeedAccount"))
      return
    }
    if (CAMPAIGN_MODE && (!store || !campaign)) {
      setStatus("Magaza ve kampanya secimi zorunlu.")
      return
    }
    if (CAMPAIGN_MODE && !account) {
      setStatus("Kampanya hesabi secimi zorunlu.")
      return
    }
    if (!CAMPAIGN_MODE && !s.caption.trim()) {
      setStatus(T("msgNeedCaptionForPlan"))
      return
    }
    const urls = composerOrderedDisplayUrls()
    const primary = urls[0] || (s.imageUrl || "").trim()
    /** Kampanya postu DB'ye sadece kullanıcının seçtiği tek banner ile yazılır; auto-publish'te de campaign API'ye 1 URL gider. */
    const publishUrls = CAMPAIGN_MODE ? (primary ? [primary] : []) : urls
    const revisionSnapshotJson = buildRevisionSnapshotJsonForCalendarEntry()
    if (!primary) {
      setStatus(T("msgPostNeedImageCaption"))
      return
    }
    const campaignCaption = s.caption.trim() || String(campaign?.product || campaign?.id || "Kampanya banner").trim()
    const basePayload = CAMPAIGN_MODE
      ? {
        accountId: String(account?.id || ""),
        accountName: String(account?.name || "Campaign Account"),
        campaignAccountId: String(account?.id || ""),
        campaignAccountName: String(account?.name || ""),
        campaignStoreId: String(store?.id || ""),
        campaignStoreName: String(store?.name || ""),
        campaignId: String(campaign?.id || ""),
        campaignName: String(campaign?.product || campaign?.id || ""),
        campaignStartDate: String(s.campaignStartDate || ""),
        campaignEndDate: String(s.campaignEndDate || ""),
        campaignRedirectUrl: String(campaign?.redirect_url || ""),
        campaignPricing: campaign?.pricing || {},
        bannerSize: "1600x704",
        publishTargets: { instagramPost: false, instagramStory: false, facebookPost: false },
        source: "campaign_banner",
        approvalStatus: s.composerApproved ? "approved" : "pending",
        status: s.composerApproved ? "scheduled" : "pending",
      }
      : {
        accountId: account.id,
        accountName: account.name,
        publishTargets: s.publishTargets,
        source: "manual",
        approvalStatus: s.composerApproved ? "approved" : "pending",
        holidayName: DEL,
      }
    try {
      if (s.editingPostId) {
        await socialPatchFields(postWriteCollection(s.editingPostId), s.editingPostId, {
          ...basePayload,
          date: s.selectedDate,
          time: (s.scheduledTime || "12:00").trim() || "12:00",
          prompt: s.prompt.trim(),
          caption: CAMPAIGN_MODE ? campaignCaption : s.caption.trim(),
          imageUrl: primary,
          imageUrls: publishUrls,
          revisionSnapshotJson,
          updatedAt: TS,
          publishStatus: "pending",
          publishedAt: DEL,
          publishStartedAt: DEL,
          lastPublishError: DEL,
        })
        setStatus(T("planUpdated"))
      } else {
        await socialCreate(scheduledPostsCollection(), {
          ...basePayload,
          date: s.selectedDate,
          time: (s.scheduledTime || "12:00").trim() || "12:00",
          prompt: s.prompt.trim(),
          caption: CAMPAIGN_MODE ? campaignCaption : s.caption.trim(),
          imageUrl: primary,
          imageUrls: publishUrls,
          revisionSnapshotJson,
          publishStatus: "pending",
          createdAt: new Date().toISOString(),
        })
        setStatus(T("msgCalendarEntryAdded"))
      }
      /** Post takvime kaydedildiğinde aynı içerik draft olarak kalmasın — tek noktada yaşasın. */
      const draftIdToCleanup = String(s.activeDraftId || "").trim()
      if (draftIdToCleanup) {
        try {
          await socialDelete(draftsCollection(), draftIdToCleanup)
        } catch {
          /* draft cleanup sessiz; post zaten kaydedildi */
        }
        s.drafts = s.drafts.filter((d) => d.id !== draftIdToCleanup)
        s.activeDraftId = null
      }
      if (!s.composerApproved) s.dayListTab = "unapproved"
      closeStudio(false)
      await refreshData()
      paint()
      if (window.__SM_EMBED_MODE__ === "approvals") {
        window.dispatchEvent(new CustomEvent("sm-studio-closed"))
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : T("planMoveFailed"))
    }
  }

  /** Edit modunda rail değişikliklerini (asset silme/yeniden sıralama) DB'ye yaz; aksi halde modal yeniden açılınca eski state geri geliyor. */
  function persistEditingPostMediaState() {
    const id = String(s.editingPostId || "").trim()
    if (!id) return
    const orderedNow = _calendarNormalizeUrls(composerOrderedDisplayUrls())
    const primary = orderedNow[0] || String(s.imageUrl || "").trim()
    if (!primary) return
    void socialPatchFields(postWriteCollection(id), id, {
      imageUrl: primary,
      imageUrls: orderedNow,
      revisionSnapshotJson: buildRevisionSnapshotJsonForCalendarEntry(),
      updatedAt: TS,
    }).catch(() => {})
  }

  return {
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
  }
}
