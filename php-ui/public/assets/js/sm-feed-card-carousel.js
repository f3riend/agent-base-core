import { esc, T } from "./social-media-api.js"

export function carouselKey(kind, id) {
  return `${String(kind || "").trim()}:${String(id || "").trim()}`
}

export function createFeedCardCarousel(slidesState) {
  function getIndex(kind, id, total) {
    const n = Number(total || 0)
    const key = carouselKey(kind, id)
    const raw = Number(slidesState[key] ?? 0)
    if (!Number.isFinite(raw) || n <= 1) return 0
    const clamped = Math.min(n - 1, Math.max(0, Math.trunc(raw)))
    if (clamped !== raw) slidesState[key] = clamped
    return clamped
  }

  function setIndex(kind, id, total, nextIndex) {
    const n = Number(total || 0)
    const key = carouselKey(kind, id)
    if (!Number.isFinite(nextIndex) || n <= 1) {
      slidesState[key] = 0
      return 0
    }
    const clamped = Math.min(n - 1, Math.max(0, Math.trunc(nextIndex)))
    slidesState[key] = clamped
    return clamped
  }

  function findCard(root, kind, id) {
    if (!root) return null
    const key = carouselKey(kind, id)
    const cards = Array.from(root.querySelectorAll("[data-card-carousel]"))
    return cards.find((el) => el.getAttribute("data-card-carousel") === key) || null
  }

  function syncDom(root, kind, id) {
    const card = findCard(root, kind, id)
    if (!card) return
    const total = Number(card.getAttribute("data-carousel-total") || "0")
    if (!Number.isFinite(total) || total <= 0) return
    const idx = getIndex(kind, id, total)
    const track = card.querySelector("[data-carousel-track]")
    if (track) track.style.transform = `translate3d(-${idx * 100}%,0,0)`
    const badge = card.querySelector("[data-carousel-count-badge]")
    if (badge) badge.textContent = total > 1 ? `${idx + 1}/${total}` : "1/1"
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

  function shift(root, kind, id, delta) {
    const card = findCard(root, kind, id)
    if (!card) return
    const total = Number(card.getAttribute("data-carousel-total") || "0")
    if (!Number.isFinite(total) || total <= 1) return
    const current = getIndex(kind, id, total)
    let next = current + Number(delta || 0)
    if (next < 0) next = total - 1
    if (next >= total) next = 0
    setIndex(kind, id, total, next)
    syncDom(root, kind, id)
  }

  function render(kind, id, imageUrls) {
    const imgs = Array.isArray(imageUrls) ? imageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
    const total = imgs.length
    const key = carouselKey(kind, id)
    const active = getIndex(kind, id, total)
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
    const countBadge = `<span data-carousel-count-badge class="absolute right-3 top-3 z-20 rounded-full border border-white/50 bg-black/35 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm">${total > 1 ? `${active + 1}/${total}` : "1/1"}</span>`
    return `
<div class="relative h-full w-full overflow-hidden bg-neutral-200" data-card-carousel="${esc(key)}" data-carousel-total="${total}" data-carousel-swipe="1">
  <div class="flex h-full w-full transition-transform duration-300" data-carousel-track style="transform:translate3d(-${active * 100}%,0,0)">${slides}</div>
  ${arrows}
  ${countBadge}
  ${dots}
</div>`
  }

  return { render, syncDom, shift, setIndex, getIndex }
}
