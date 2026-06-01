import { esc, T } from "./social-media-api.js"
import { rootEl, s } from "./social-media-state.js"

export function createPostPreviewController() {
  let postPreviewTimer = null
  let postPreviewEl = null
  let postPreviewActiveId = null
  let postPreviewActiveAnchor = null
  let postPreviewHoverAnchor = false
  let postPreviewHoverCard = false

  function hidePostPreview() {
    if (postPreviewTimer) {
      clearTimeout(postPreviewTimer)
      postPreviewTimer = null
    }
    const host = document.getElementById("sm-post-preview-root")
    if (host) {
      host.innerHTML = ""
      host.classList.add("pointer-events-none")
    }
    postPreviewEl = null
    postPreviewActiveId = null
    postPreviewActiveAnchor = null
    postPreviewHoverAnchor = false
    postPreviewHoverCard = false
  }

  function scheduleHidePostPreview() {
    if (postPreviewTimer) clearTimeout(postPreviewTimer)
    postPreviewTimer = window.setTimeout(() => {
      if (postPreviewHoverAnchor || postPreviewHoverCard) return
      hidePostPreview()
    }, 260)
  }

  function cancelPostPreviewClose() {
    if (postPreviewTimer) {
      clearTimeout(postPreviewTimer)
      postPreviewTimer = null
    }
  }

  function showPostPreview(postId, anchorEl) {
    if (s.draggingPostId) return    /** Drag sırasında popup açma — sürükle-bırak UX'ini bozmasın. */
    cancelPostPreviewClose()
    const post = s.posts.find((p) => p.id === postId)
    if (!post || !anchorEl) return
    if (postPreviewActiveId === postId && postPreviewActiveAnchor === anchorEl && postPreviewEl) return
    let host = document.getElementById("sm-post-preview-root")
    if (!host) {
      host = document.createElement("div")
      host.id = "sm-post-preview-root"
      host.className = "pointer-events-none fixed inset-0 z-[80]"
      document.body.appendChild(host)
    }
    const r = anchorEl.getBoundingClientRect()
    const viewportW = window.innerWidth
    const viewportH = window.innerHeight
    const margin = 10
    const gap = 10
    const panelW = Math.max(220, Math.min(260, viewportW - margin * 2))
    const panelH = Math.max(240, Math.min(320, viewportH - margin * 2))
    const canRight = r.right + gap + panelW <= viewportW - margin
    const canLeft = r.left - gap - panelW >= margin
    const left = canRight
      ? r.right + gap
      : canLeft
        ? r.left - panelW - gap
        : Math.max(margin, Math.min(viewportW - panelW - margin, r.left + (r.width - panelW) / 2))
    const preferTop = r.top
    const top =
      preferTop + panelH <= viewportH - margin
        ? preferTop
        : r.bottom - panelH >= margin
          ? r.bottom - panelH
          : Math.max(margin, viewportH - panelH - margin)
    const st =
      post.publishStatus === "published"
        ? T("planStatusPublished")
        : post.publishStatus === "failed"
          ? T("planStatusFailed")
          : post.publishStatus === "publishing"
            ? T("planStatusPublishing")
            : T("planStatusPending")
    const previewImage = (post.imageUrl || "").trim()
    const imgBlock = previewImage
      ? `<img src="${esc(previewImage)}" alt="" class="h-full w-full object-cover"/>`
      : `<div class="flex h-full items-center justify-center text-xs text-neutral-400">${esc(T("noVisual"))}</div>`
    host.classList.remove("pointer-events-none")
    host.innerHTML = `
<div id="sm-post-preview-card" class="sm-peek-card pointer-events-auto fixed overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-[0_12px_32px_rgba(15,23,42,0.16)]" style="left:${left}px;top:${top}px;width:${panelW}px;max-height:${panelH}px">
  <div class="h-40 overflow-hidden bg-neutral-100">${imgBlock}</div>
  <div class="space-y-1.5 p-2.5">
    <div class="flex items-center justify-between gap-2">
      <p class="truncate text-xs font-semibold text-neutral-900">${esc(post.accountName)}</p>
      <span class="shrink-0 rounded-full bg-neutral-100 px-1.5 py-0.5 text-[9px] font-semibold text-neutral-600">${esc(st)}</span>
    </div>
    <div class="flex items-center gap-1.5 text-[10px] text-neutral-500">
      <span>${esc(post.date || "")}</span>
      ${post.time ? `<span class="tabular-nums">${esc(post.time)}</span>` : ""}
    </div>
    <p class="overflow-hidden text-xs leading-relaxed text-neutral-700" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;">${esc(post.caption || "")}</p>
  </div>
</div>`
    postPreviewEl = host.querySelector("#sm-post-preview-card")
    postPreviewActiveId = postId
    postPreviewActiveAnchor = anchorEl
    if (postPreviewEl) {
      postPreviewEl.addEventListener("mouseenter", () => {
        postPreviewHoverCard = true
        cancelPostPreviewClose()
      })
      postPreviewEl.addEventListener("mouseleave", () => {
        postPreviewHoverCard = false
        scheduleHidePostPreview()
      })
    }
  }

  function registerPostPreviewHover() {
    if (!rootEl || rootEl.dataset.previewHoverBound) return
    rootEl.dataset.previewHoverBound = "1"
    rootEl.addEventListener("mousemove", (e) => {
      if (!(e.target instanceof Element)) return
      const inGrid = Boolean(e.target.closest("#sm-cal-grid"))
      const w = inGrid ? e.target.closest("[data-post-preview]") : null
      if (!w) {
        postPreviewHoverAnchor = false
        scheduleHidePostPreview()
        return
      }
      const id = w.getAttribute("data-post-preview")
      if (!id) return
      postPreviewHoverAnchor = true
      showPostPreview(id, w)
    })
    rootEl.addEventListener("mouseleave", () => {
      postPreviewHoverAnchor = false
      scheduleHidePostPreview()
    })
  }

  return {
    hidePostPreview,
    scheduleHidePostPreview,
    cancelPostPreviewClose,
    showPostPreview,
    registerPostPreviewHover,
  }
}
