import { cfg } from "./social-media-api.js"
import { GRACE_MS } from "./social-media-constants.js"

function looksLikeVideoUrl(u) {
  const t = String(u || "")
    .trim()
    .toLowerCase()
  return /\.(mp4|mov|webm|m4v)(\?|#|$)/.test(t) || t.includes("video/")
}

export function formatDateKey(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, "0")
  const d = String(date.getDate()).padStart(2, "0")
  return `${y}-${m}-${d}`
}

export function buildCalendarDays(baseDate) {
  const year = baseDate.getFullYear()
  const month = baseDate.getMonth()
  const firstDay = new Date(year, month, 1)
  const startOffset = (firstDay.getDay() + 6) % 7
  const startDate = new Date(year, month, 1 - startOffset)
  return Array.from({ length: 35 }, (_, index) => {
    const day = new Date(startDate)
    day.setDate(startDate.getDate() + index)
    return day
  })
}

export function parseScheduledLocalDateTime(dateStr, timeStr) {
  const d = (dateStr || "").trim()
  const rawT = (timeStr || "12:00").trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null
  const dm = d.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!dm) return null
  const yyyy = Number(dm[1])
  const monthIndex = Number(dm[2]) - 1
  const day = Number(dm[3])
  const tm = rawT.match(/^(\d{1,2}):(\d{2})$/)
  if (!tm) return null
  const hh = Number(tm[1])
  const mm = Number(tm[2])
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null
  if (monthIndex < 0 || monthIndex > 11 || day < 1 || day > 31) return null
  const dt = new Date(yyyy, monthIndex, day, hh, mm, 0, 0)
  return Number.isNaN(dt.getTime()) ? null : dt
}

export function scheduleSlotTiming(post, nowMs) {
  const dt = parseScheduledLocalDateTime(post.date, post.time)
  if (!dt) return "missed"
  const t = dt.getTime()
  if (nowMs < t) return "upcoming"
  if (nowMs > t + GRACE_MS) return "missed"
  return "due"
}

export function normalizeApprovalStatus(raw) {
  const a = String(raw?.approvalStatus ?? raw?.approvalState ?? raw?.approval_state ?? "")
    .trim()
    .toLowerCase()
  if (a === "waiting_approval") return "pending"
  if (a === "pending" || a === "approved" || a === "rejected") return a
  const pub = String(raw?.publishStatus ?? raw?.status ?? "")
    .trim()
    .toLowerCase()
  if (pub === "pending") return "pending"
  return ""
}

export function isPostApproved(post) {
  const a = normalizeApprovalStatus(post)
  if (a === "approved") return true
  if (a === "pending" || a === "rejected") return false
  return true
}

export function isPostUnapproved(post) {
  const a = normalizeApprovalStatus(post)
  return a === "pending" || a === "rejected"
}

export function isApprovedForAutoPublish(post) {
  if (isPostApproved(post)) return true
  const a = normalizeApprovalStatus(post)
  if (a === "pending" || a === "rejected") return false
  return true
}

export function mergePostImageListOrdered(primary, orderedUrls) {
  const seen = new Set()
  const out = []
  for (const u of orderedUrls || []) {
    const t = (u || "").trim()
    if (!t || seen.has(t)) continue
    seen.add(t)
    out.push(t)
  }
  const p = (primary || "").trim()
  if (p && !seen.has(p)) out.push(p)
  if (out.length === 0 && p) return [p]
  return out
}

function assetComparableUrl(url) {
  const t = String(url || "").trim()
  if (!t) return ""
  try {
    const u = new URL(t, window.location.origin)
    return `${u.pathname}${u.search}`.toLowerCase()
  } catch {
    return t.toLowerCase()
  }
}

export function assetUrlEquals(a, b) {
  const aa = String(a || "").trim()
  const bb = String(b || "").trim()
  if (!aa || !bb) return false
  if (aa === bb) return true
  const ca = assetComparableUrl(aa)
  const cb = assetComparableUrl(bb)
  return Boolean(ca && cb && ca === cb)
}

export function baseForRevisionUrl(url, revisionMap) {
  const t = String(url || "").trim()
  if (!t || !revisionMap || typeof revisionMap !== "object") return ""
  for (const [base, items] of Object.entries(revisionMap)) {
    const b = String(base || "").trim()
    const arr = Array.isArray(items) ? items.map((u) => String(u || "").trim()).filter(Boolean) : []
    if (arr.some((u) => assetUrlEquals(u, t)) && !assetUrlEquals(b, t)) return b
  }
  return ""
}

/** Non-base URLs in revision chains — each is one user-visible "revizyon" beyond the rail slot base. */
export function revisionChainVariantCount(revisionMap) {
  const revMap = revisionMap && typeof revisionMap === "object" ? revisionMap : {}
  let n = 0
  for (const [base, items] of Object.entries(revMap)) {
    const b = String(base || "").trim()
    const arr = Array.isArray(items) ? items.map((u) => String(u || "").trim()).filter(Boolean) : []
    for (const u of arr) {
      if (!u || assetUrlEquals(u, b)) continue
      n += 1
    }
  }
  return n
}

export function buildRailOrderFromDoc(primary, imageUrls, snapAssetOrder, revisionMap) {
  const revMap = revisionMap && typeof revisionMap === "object" ? revisionMap : {}
  const snap = Array.isArray(snapAssetOrder) ? snapAssetOrder.map((u) => String(u || "").trim()).filter(Boolean) : []
  const imgs = Array.isArray(imageUrls) ? imageUrls.map((u) => String(u || "").trim()).filter(Boolean) : []
  /** Variant URL'leri base'ine çevir; aksi halde çoklu rail slotu (revize zinciri) reload sonrası tek base'e iniyor. */
  const baseFromImgs = imgs.map((u) => baseForRevisionUrl(u, revMap) || u).filter(Boolean)
  const p = String(primary || "").trim()
  let merged = [...new Set(baseFromImgs)]
  if (!merged.length && p) {
    const pb = baseForRevisionUrl(p, revMap)
    merged = pb ? [pb] : [p]
  }
  for (const b of Object.keys(revMap)) {
    const t = String(b || "").trim()
    if (t && !merged.includes(t)) merged.push(t)
  }
  if (snap.length) {
    if (!merged.length) return [...new Set(snap)]
    const mergedSet = new Set(merged)
    const out = snap.filter((u) => mergedSet.has(u))
    for (const u of merged) {
      if (!out.includes(u)) out.push(u)
    }
    return out
  }
  return merged
}

export function withInferredRevisionState(primary, imageUrls, revisionMap, selectedRevisionByBase) {
  /** Tarihsel: eski tek-revize akışı için primary listede yoksa otomatik bir `{ urls[0]: [urls[0], primary] }`
   *  chain'i üretiyordu. Yeni akışta `saveComposerDraftQuiet` `s.revisionMap`'i zaten snapshot'a yazıyor,
   *  ayrıca kampanya rail'inde birden çok bağımsız ürün görseli olduğunda bu infer "rail item'ı revize variantı"
   *  şeklinde yanlış yorum yaratıyor. Bu yüzden no-op'a alındı: state ne ise onu döner.
   */
  const revMapIn = revisionMap && typeof revisionMap === "object" ? revisionMap : {}
  const selIn = selectedRevisionByBase && typeof selectedRevisionByBase === "object" ? selectedRevisionByBase : {}
  return { revisionMap: revMapIn, selectedRevisionByBase: selIn }
}

export function resolvePostLifecycle(post) {
  const status = String(post?.status || "").toLowerCase()
  const publishStatus = String(post?.publishStatus || "").toLowerCase()
  if (status === "cancelled" || publishStatus === "cancelled") return "cancelled"
  if (publishStatus === "published" || status === "published") return "published"
  if (publishStatus === "publishing" || status === "publishing" || status === "queued") return "publishing"
  if (status === "scheduled") return "scheduled"
  if (publishStatus === "failed" && status !== "cancelled") return "failed"
  return "pending"
}

export function lifecycleBadge(lifecycle) {
  const en = cfg().uiLocale === "en"
  if (lifecycle === "published") return { label: en ? "published" : "Yayınlandı", cls: "bg-emerald-100 text-emerald-800" }
  if (lifecycle === "publishing") return { label: en ? "publishing" : "Yayınlanıyor", cls: "bg-amber-100 text-amber-900" }
  if (lifecycle === "scheduled") return { label: en ? "scheduled" : "Planlandı", cls: "bg-blue-100 text-blue-800" }
  if (lifecycle === "cancelled") return { label: en ? "cancelled" : "İptal", cls: "bg-red-100 text-red-800" }
  if (lifecycle === "failed") return { label: en ? "failed" : "Başarısız", cls: "bg-red-100 text-red-800" }
  return { label: en ? "pending" : "Beklemede", cls: "bg-neutral-200 text-neutral-600" }
}

export function sourceBadgeText(post) {
  const src = String(post?.source || post?.sourceRaw || "").toLowerCase()
  if (src === "store_workflow") return "AI Automation • Store Approval"
  if (src === "automation_rule") return "AI Automation"
  if (src === "holiday") return "Holiday Automation"
  if (src === "campaign_banner") return "Campaign Banner"
  if (src === "manual") return "Manual"
  return "Automation"
}

export function scheduleRelativeText(post) {
  const dt = parseScheduledLocalDateTime(post?.date || "", post?.time || "12:00")
  if (!dt) return ""
  const diffMs = dt.getTime() - Date.now()
  if (diffMs <= 0) return "Yayin zamani geldi"
  const dayMs = 24 * 60 * 60 * 1000
  const hourMs = 60 * 60 * 1000
  const d = Math.floor(diffMs / dayMs)
  if (d >= 1) return `${d} gun sonra yayinlanacak`
  const h = Math.max(1, Math.floor(diffMs / hourMs))
  return `${h} saat sonra yayinlanacak`
}
