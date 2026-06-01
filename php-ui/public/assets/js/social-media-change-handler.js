import { apiBase, T } from "./social-media-api.js"
import { campaignLoadStoreDiscountedProducts, usesStoreDiscountedProductCatalog } from "./social-media-data.js"
import { debugLog } from "./social-media-runtime.js"
import { activeCampaign, activeCampaignStore, campaignMediaList, hasUnsavedCampaignBannerWork } from "./social-media-selectors.js"
import { rootEl, s } from "./social-media-state.js"

/** Kampanya değişiminden önce banner çalışması varsa kullanıcıdan onay alır; reddederse dropdown eski değere döner. */
function confirmCampaignSwitchOrRevert(selectEl, prevValue) {
  if (!hasUnsavedCampaignBannerWork()) return true
  const ok = window.confirm(
    "Bu oluşturduğunuz mevcut banner silinecektir. Başka kampanyaya geçmek istediğinize emin misiniz?",
  )
  if (!ok) {
    selectEl.value = prevValue
    return false
  }
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
  s.selectedTemplateId = null
  s.selectedTemplateScope = "user"
  return true
}

export function createDelegatedChangeHandler(deps = {}) {
  const {
    applyCampaignSelectionDetails,
    paintDayPanel,
    paintModals,
    recomputeUnifiedMediaRail,
    setStatus,
    studioUploadFilesFromFileList,
    syncCampaignSelectionToRail,
    templateUploadFilesFromFileList,
  } = deps

  return function onDelegatedChange(e) {
    const t = e.target
    if (t instanceof HTMLSelectElement && t.id === "sm-dayscope") {
      s.dayScope = t.value === "month" ? "month" : t.value === "week" ? "week" : "today"
      paintDayPanel()
      return
    }
    if (t instanceof HTMLSelectElement && t.id === "st-campaign-store") {
      const prevStore = String(s.campaignStoreId || "")
      if (!confirmCampaignSwitchOrRevert(t, prevStore)) return
      s.campaignStoreId = t.value || s.campaignStoreId
      const applyFirstProduct = (products) => {
        if (products.length) {
          const first = products[0] || {}
          s.campaignId = String(first.id || first.product || "")
          const firstCampaign = activeCampaign()
          applyCampaignSelectionDetails(firstCampaign)
          const directUrls = campaignMediaList(firstCampaign)
          s.campaignMediaUrls = [...directUrls]
          recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
          debugLog("campaign.select_store", {
            storeId: s.campaignStoreId,
            campaignId: s.campaignId,
            directCount: directUrls.length,
          })
          void syncCampaignSelectionToRail()
        } else {
          s.campaignId = ""
          s.campaignMediaUrls = []
          s.campaignMediaKey = `${String(s.campaignStoreId || "")}::`
          recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
          debugLog("campaign.select_store_empty", { storeId: s.campaignStoreId })
        }
        paintModals()
      }
      if (usesStoreDiscountedProductCatalog()) {
        s.campaignId = ""
        s.campaignStoreProductsLoading = true
        paintModals()
        void campaignLoadStoreDiscountedProducts(s.campaignStoreId)
          .then((products) => applyFirstProduct(products))
          .catch(() => applyFirstProduct([]))
        return
      }
      const store = activeCampaignStore()
      if (store && Array.isArray(store.campaigns) && store.campaigns.length) {
        applyFirstProduct(store.campaigns)
      } else {
        applyFirstProduct([])
      }
      return
    }
    if (t instanceof HTMLSelectElement && t.id === "st-campaign-id") {
      const prevCampaign = String(s.campaignId || "")
      if (!confirmCampaignSwitchOrRevert(t, prevCampaign)) return
      s.campaignId = t.value || s.campaignId
      const campaign = activeCampaign()
      applyCampaignSelectionDetails(campaign)
      const directUrls = campaignMediaList(campaign)
      s.campaignMediaUrls = [...directUrls]
      recomputeUnifiedMediaRail({ preferFirst: !s.studioOpen })
      debugLog("campaign.select_campaign", {
        storeId: s.campaignStoreId,
        campaignId: s.campaignId,
        directCount: directUrls.length,
      })
      void syncCampaignSelectionToRail()
      paintModals()
      return
    }
    if (!(t instanceof HTMLInputElement)) return
    if (t.classList.contains("linked-cb")) {
      const id = t.getAttribute("data-igid")
      if (!id) return
      if (t.checked) s.linkedSelected.add(id)
      else s.linkedSelected.delete(id)
      const addBtn = rootEl.querySelector("#linked-add-btn")
      if (addBtn) addBtn.disabled = s.linkedSelected.size === 0
    }
    if (t.id === "acc-logo-file" && t.files && t.files[0]) {
      void (async () => {
        s.logoUploading = true
        paintModals()
        try {
          const fd = new FormData()
          fd.append("file", t.files[0])
          const res = await fetch(apiBase() + "/social-media/image/upload", { method: "POST", body: fd })
          const data = await res.json().catch(() => ({}))
          if (!res.ok) throw new Error(data.error || "upload")
          s.accLogo = String(data.url || "")
        } catch {
          setStatus(T("msgUploadFailed"))
        } finally {
          s.logoUploading = false
          t.value = ""
          paintModals()
        }
      })()
    }
    if (t.id === "st-file" && t.files && t.files.length) {
      const files = Array.from(t.files || [])
      void studioUploadFilesFromFileList(files).finally(() => {
        t.value = ""
      })
    }
    if (t.id === "tpl-file-layout" && t.files && t.files.length) {
      const files = Array.from(t.files || [])
      void templateUploadFilesFromFileList(files, "layout").finally(() => {
        t.value = ""
      })
    }
    if (t.id === "tpl-file-logo" && t.files && t.files.length) {
      const files = Array.from(t.files || [])
      void templateUploadFilesFromFileList(files, "logo").finally(() => {
        t.value = ""
      })
    }
    const refCbAct = t.getAttribute("data-act")
    if (refCbAct === "st-ref-url-cb" && t instanceof HTMLInputElement) {
      const url = t.getAttribute("data-url")
      if (!url) return
      const arr = [...(s.referenceCheckedUrls || [])]
      const i = arr.indexOf(url)
      if (t.checked) {
        if (i < 0) arr.push(url)
      } else if (i >= 0) arr.splice(i, 1)
      s.referenceCheckedUrls = arr
    }
  }
}
