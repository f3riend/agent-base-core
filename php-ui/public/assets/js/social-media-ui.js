import { esc } from "./social-media-api.js"

export function loadingDotsHtml(cls) {
  const c = cls || "text-amber-800"
  return `<span class="inline-flex items-center gap-0.5 align-middle ${esc(c)}" aria-hidden="true">
<span class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current" style="animation-duration:0.65s;animation-delay:-0.2s"></span>
<span class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current" style="animation-duration:0.65s;animation-delay:-0.1s"></span>
<span class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current" style="animation-duration:0.65s"></span>
</span>`
}

export function iconChevL() {
  return `<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="m15 6-6 6 6 6"/></svg>`
}

export function iconChevR() {
  return `<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="m9 6 6 6-6 6"/></svg>`
}

export function iconPlus() {
  return `<svg class="h-6 w-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>`
}
