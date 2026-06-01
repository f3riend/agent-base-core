import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { DEFAULT_CAMPAIGN_API_BASE_URL } from "./social-media-constants.js"
import { rootEl, s } from "./social-media-state.js"

export function registerCalendarDelegation({
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
}) {
  if (!rootEl || rootEl.dataset.calBound) return
  rootEl.dataset.calBound = "1"
  rootEl.addEventListener("click", (e) => {
    const cell = e.target.closest("#sm-cal-grid [data-date-key]")
    if (!cell) return
    if (e.target.closest("[data-drag-post]")) return
    s.selectedDate = cell.getAttribute("data-date-key") || s.selectedDate
    s.dayListTab = "approved"
    paint()
  })
  rootEl.addEventListener("keydown", (e) => {
    const cell = e.target.closest("#sm-cal-grid [data-date-key]")
    if (!cell) return
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      s.selectedDate = cell.getAttribute("data-date-key") || s.selectedDate
      s.dayListTab = "approved"
      paint()
    }
  })
  rootEl.addEventListener("contextmenu", (e) => {
    const accBtn = e.target.closest("#sm-accounts [data-aid]")
    if (accBtn) {
      e.preventDefault()
      const id = accBtn.getAttribute("data-aid")
      if (!id) return
      s.contextAccountId = id
      s.contextX = e.clientX
      s.contextY = e.clientY
      paintModals()
      return
    }
    const cell = e.target.closest("#sm-cal-grid [data-date-key]")
    if (!cell) return
    e.preventDefault()
    s.selectedDate = cell.getAttribute("data-date-key") || s.selectedDate
    s.dayMenu = { x: e.clientX, y: e.clientY, dateKey: s.selectedDate }
    paintModals()
  })
  rootEl.addEventListener("dragstart", (e) => {
    const railCard = e.target.closest("[data-drag-rail]")
    if (railCard && s.studioOpen && !studioRailHasVideo()) {
      const u = railCard.getAttribute("data-drag-rail")
      const url = u ? String(u).trim() : ""
      if (!url) return
      s.draggingRailAssetUrl = url
      try {
        e.dataTransfer.setData("application/x-sm-rail-asset", url)
      } catch {
        /* */
      }
      e.dataTransfer.effectAllowed = "move"
      return
    }
    const h = e.target.closest("[data-drag-post]")
    if (!h) return
    s.draggingPostId = h.getAttribute("data-drag-post")
    if (typeof hidePostPreview === "function") hidePostPreview()    /** Önizleme popup'ı drag'i kesintiye uğratmasın. */
    e.dataTransfer.setData("text/plain", s.draggingPostId || "")
    e.dataTransfer.effectAllowed = "move"
    const post = s.posts.find((p) => p.id === s.draggingPostId)
    if (post) {
      const ghost = makeCalendarDragGhost(post)
      try {
        e.dataTransfer.setDragImage(ghost, 16, 16)
      } catch {
        /* */
      }
      window.setTimeout(() => {
        if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost)
      }, 0)
    }
  })
  rootEl.addEventListener("dragover", (e) => {
    const railItem = e.target.closest("[data-drag-rail]")
    if (
      railItem &&
      s.studioOpen &&
      (s.draggingRailAssetUrl ||
        (e.dataTransfer.types && Array.from(e.dataTransfer.types).includes("application/x-sm-rail-asset")))
    ) {
      e.preventDefault()
      e.dataTransfer.dropEffect = "move"
      return
    }
    const rail = e.target.closest('[data-act="st-rail-drop-zone"]')
    if (rail && s.studioOpen) {
      e.preventDefault()
      return
    }
    const tplLayout = e.target.closest('[data-act="tpl-drop-layout"]')
    const tplLogo = e.target.closest('[data-act="tpl-drop-logo"]')
    const tplZone = tplLayout || tplLogo
    if (tplZone && s.templateModal) {
      e.preventDefault()
      return
    }
    const cell = e.target.closest("#sm-cal-grid [data-date-key]")
    if (!cell || !s.draggingPostId) return
    e.preventDefault()
    const dk = cell.getAttribute("data-date-key")
    if (dk !== s.dragOverDateKey) {
      s.dragOverDateKey = dk
      paintCalendar()
    }
  })
  rootEl.addEventListener("dragleave", (e) => {
    if (!e.target.closest("#sm-cal-grid")) return
    s.dragOverDateKey = null
    paintCalendar()
  })
  rootEl.addEventListener("drop", (e) => {
    const railDropItem = e.target.closest("[data-drag-rail]")
    if (railDropItem && s.studioOpen && !studioRailHasVideo()) {
      let fromUrl = ""
      try {
        fromUrl = e.dataTransfer.getData("application/x-sm-rail-asset").trim()
      } catch {
        /* */
      }
      if (!fromUrl) fromUrl = (s.draggingRailAssetUrl || "").trim()
      const toUrl = (railDropItem.getAttribute("data-drag-rail") || "").trim()
      if (fromUrl && toUrl && fromUrl !== toUrl) {
        e.preventDefault()
        e.stopPropagation()
        reorderRailAssets(fromUrl, toUrl)
        s.draggingRailAssetUrl = null
        paintModals()
        return
      }
    }
    const rail = e.target.closest('[data-act="st-rail-drop-zone"]')
    if (rail && s.studioOpen) {
      if (s.visualOutputKind === "video" && studioRailHasVideo()) return
      e.preventDefault()
      const files = Array.from(e.dataTransfer.files || [])
      if (files.length) void studioUploadFilesFromFileList(files)
      return
    }
    const tplLayout = e.target.closest('[data-act="tpl-drop-layout"]')
    const tplLogo = e.target.closest('[data-act="tpl-drop-logo"]')
    if (tplLayout && s.templateModal) {
      e.preventDefault()
      const files = Array.from(e.dataTransfer.files || [])
      if (files.length) void templateUploadFilesFromFileList(files, "layout")
      return
    }
    if (tplLogo && s.templateModal) {
      e.preventDefault()
      const files = Array.from(e.dataTransfer.files || [])
      if (files.length) void templateUploadFilesFromFileList(files, "logo")
      return
    }
    const cell = e.target.closest("#sm-cal-grid [data-date-key]")
    const id = e.dataTransfer.getData("text/plain")
    if (!id) return
    e.preventDefault()
    const dk = cell && cell.getAttribute("data-date-key")
    s.draggingPostId = null
    s.dragOverDateKey = null
    if (dk) {
      s.dropFlashDateKey = dk
      window.setTimeout(() => {
        if (s.dropFlashDateKey === dk) {
          s.dropFlashDateKey = null
          paintCalendar()
        }
      }, 520)
    }
    paint()
    if (id && dk) void movePost(id, dk)
  })
  window.addEventListener("dragend", () => {
    s.draggingRailAssetUrl = null
    if (s.draggingPostId || s.dragOverDateKey) {
      s.draggingPostId = null
      s.dragOverDateKey = null
      paintCalendar()
    }
  })
  rootEl.addEventListener("dblclick", (e) => {
    const postHandle = e.target.closest("#sm-cal-grid [data-post-preview], #sm-cal-grid [data-drag-post]")
    if (postHandle) {
      const id = postHandle.getAttribute("data-post-preview") || postHandle.getAttribute("data-drag-post")
      const post = s.posts.find((p) => p.id === id)
      if (post) {
        e.preventDefault()
        loadPostIntoStudio(post)
        return
      }
    }
    const btn = e.target.closest("#sm-accounts [data-aid]")
    if (!btn) return
    const id = btn.getAttribute("data-aid")
    const acc = s.accounts.find((a) => a.id === id)
    if (!acc) return
    s.accountModal = true
    s.editingAccountId = acc.id
    s.accName = acc.name
    s.accToken = CAMPAIGN_MODE ? acc.campaignApiKey || "" : acc.instagramAccessToken
    s.accCampaignBaseUrl = CAMPAIGN_MODE
      ? String(acc.campaignApiBaseUrl || "").trim() || DEFAULT_CAMPAIGN_API_BASE_URL
      : ""
    s.accCampaignKind = acc.campaignAccountKind === "restaurant" ? "restaurant" : "store"
    s.accLogo = acc.logoUrl || ""
    s.linkedRows = []
    s.linkedSelected = new Set()
    s.linkedErr = ""
    paintModals()
  })
}
