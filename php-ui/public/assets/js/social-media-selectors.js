import { cfg, T } from "./social-media-api.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import {
  CAMPAIGN_GLOBAL_TEMPLATES,
  CAMPAIGN_USER_TEMPLATES,
  DEFAULT_CAMPAIGN_API_BASE_URL,
  GLOBAL_TEMPLATES,
  USER_TEMPLATES,
} from "./social-media-constants.js"
import { formatDateKey, isPostApproved, isPostUnapproved, parseScheduledLocalDateTime } from "./social-media-post-utils.js"
import { s } from "./social-media-state.js"

export const CAMPAIGN_CREDENTIALS_HINT =
  "Kampanya kataloğu için seçili hesapta Campaign API Base URL (ör. https://mtlive.sepetler.com/api/ai/v1) ve Bearer API Key girin."

export function user() {
  return cfg().user || null
}

export function localeTag() {
  return cfg().uiLocale === "en" ? "en-US" : "tr-TR"
}

export function weekdayLabels() {
  return ["weekdayMon", "weekdayTue", "weekdayWed", "weekdayThu", "weekdayFri", "weekdaySat", "weekdaySun"].map(T)
}

export function visiblePosts() {
  if (!s.filterAccountId) return s.posts
  return s.posts.filter((p) => !p.accountId || p.accountId === s.filterAccountId)
}

function toMinute(time) {
  const raw = (time || "").trim()
  const m = raw.match(/^(\d{1,2}):(\d{2})$/)
  if (!m) return 24 * 60 + 1
  return Number(m[1]) * 60 + Number(m[2])
}

function dateFromKey(key) {
  const raw = String(key || "").trim()
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const y = Number(m[1])
  const mm = Number(m[2]) - 1
  const d = Number(m[3])
  const dt = new Date(y, mm, d, 0, 0, 0, 0)
  return Number.isNaN(dt.getTime()) ? null : dt
}

function isWithinSelectedWeek(dateKey, selectedKey) {
  const base = dateFromKey(selectedKey)
  const cur = dateFromKey(dateKey)
  if (!base || !cur) return false
  const dayOffset = (base.getDay() + 6) % 7
  const start = new Date(base)
  start.setDate(base.getDate() - dayOffset)
  const end = new Date(start)
  end.setDate(start.getDate() + 6)
  return cur >= start && cur <= end
}

export function selectedDayPosts() {
  const selectedKey = String(s.selectedDate || formatDateKey(new Date())).trim() || formatDateKey(new Date())
  const selectedDt = dateFromKey(selectedKey)
  const selectedPrefix = selectedDt
    ? `${selectedDt.getFullYear()}-${String(selectedDt.getMonth() + 1).padStart(2, "0")}-`
    : ""
  const loc = cfg().uiLocale === "en" ? "en" : "tr"
  return visiblePosts()
    .filter((post) => {
      const dk = String(post.date || "").trim()
      if (!dk) return false
      if (s.dayScope === "today") return dk === selectedKey
      if (s.dayScope === "week") return isWithinSelectedWeek(dk, selectedKey)
      return selectedPrefix ? dk.startsWith(selectedPrefix) : dk === selectedKey
    })
    .sort((a, b) => {
      const diff = toMinute(a.time) - toMinute(b.time)
      if (diff !== 0) return diff
      return a.accountName.localeCompare(b.accountName, loc)
    })
}

export function approvedPosts() {
  return selectedDayPosts().filter((p) => isPostApproved(p))
}

export function unapprovedPosts() {
  return selectedDayPosts().filter((p) => isPostUnapproved(p))
}

export function visibleDrafts() {
  const list = !s.filterAccountId ? s.drafts : s.drafts.filter((d) => d.accountId === s.filterAccountId)
  const selectedKey = String(s.selectedDate || formatDateKey(new Date())).trim() || formatDateKey(new Date())
  const selectedDt = dateFromKey(selectedKey)
  const selectedPrefix = selectedDt
    ? `${selectedDt.getFullYear()}-${String(selectedDt.getMonth() + 1).padStart(2, "0")}-`
    : ""
  if (s.dayScope === "today") return list.filter((d) => (d.date || "").trim() === selectedKey)
  if (s.dayScope === "week") return list.filter((d) => isWithinSelectedWeek(String(d.date || "").trim(), selectedKey))
  return list.filter((d) => {
    const dk = String(d.date || "").trim()
    return selectedPrefix ? dk.startsWith(selectedPrefix) : dk === selectedKey
  })
}

export function workflowForPost(post) {
  const wid = String(post?.automationWorkflowId || "").trim()
  if (wid) {
    const direct = s.workflows.find((w) => w.id === wid)
    if (direct) return direct
  }
  return s.workflows.find((w) => String(w.scheduledPostId || "") === String(post?.id || "")) || null
}

export function eventTimelineForWorkflow(workflow, post) {
  if (!workflow) return []
  const sid = String(workflow.storeId || "")
  const wid = String(workflow.id || "")
  const pid = String(post?.id || workflow.scheduledPostId || "")
  const rows = s.automationEvents.filter((ev) => {
    const p = ev.payload || {}
    return String(p.workflow_id || "") === wid || String(p.store_id || "") === sid || String(p.scheduled_post_id || "") === pid
  })
  const ordered = rows.slice().sort((a, b) => String(a.triggeredAt || "").localeCompare(String(b.triggeredAt || "")))
  const iconFor = (eventType) => {
    if (eventType === "workflow_cancelled") return "✕"
    return "✓"
  }
  return ordered.map((ev) => ({
    icon: iconFor(ev.eventType),
    text: String(ev.eventType || "").replaceAll("_", " "),
    at: ev.triggeredAt,
  }))
}

export function activeAccount() {
  return s.accounts.find((a) => a.id === s.activeAccountId) || null
}

/** Kampanya upstream / katalog: seçili hesapta API key ve base URL zorunlu (localhost varsayılanı sayılmaz). */
export function campaignCatalogCredentialsReady() {
  if (!CAMPAIGN_MODE) return true
  const acc = activeAccount()
  if (!acc) return false
  const key = String(acc.campaignApiKey || "").trim()
  const base = String(acc.campaignApiBaseUrl || "").trim() || DEFAULT_CAMPAIGN_API_BASE_URL
  return Boolean(key && base)
}

export function campaignTemplateCollections() {
  return CAMPAIGN_MODE
    ? { user: CAMPAIGN_USER_TEMPLATES, global: CAMPAIGN_GLOBAL_TEMPLATES }
    : { user: USER_TEMPLATES, global: GLOBAL_TEMPLATES }
}

export function normalizeStudioPanel(panel) {
  const p = String(panel || "").trim()
  if (CAMPAIGN_MODE) return p === "publish" ? "publish" : "revise"
  return p === "generate" || p === "caption" || p === "revise" || p === "publish" ? p : "caption"
}

export function normalizeCampaignStudioState() {
  if (!CAMPAIGN_MODE) return
  s.modalPanel = normalizeStudioPanel(s.modalPanel)
  if (s.generateSubTab === "ticket") s.generateSubTab = "manual"
  s.selectedTicketId = null
}

export function activeCampaignStore() {
  return s.campaignStores.find((x) => String(x.id || "") === String(s.campaignStoreId || "")) || null
}

export function findCampaignBySelection(store, selectionValue) {
  if (!store || !Array.isArray(store.campaigns)) return null
  const sel = String(selectionValue || "").trim()
  if (!sel) return null
  const byId = store.campaigns.find((row) => String(row?.id || "").trim() === sel)
  if (byId) return byId
  const selLc = sel.toLowerCase()
  return (
    store.campaigns.find((row) => String(row?.product || "").trim().toLowerCase() === selLc) || null
  )
}

export function activeCampaign() {
  const store = activeCampaignStore()
  return findCampaignBySelection(store, s.campaignId)
}

export function campaignMediaList(campaign) {
  return Array.isArray(campaign?.media) ? campaign.media.map((u) => String(u || "").trim()).filter(Boolean) : []
}

/** Kampanya modunda kullanıcı bir banner üzerinde çalıştı mı? Mağaza/kampanya değişiminden önce uyarı için. */
export function hasUnsavedCampaignBannerWork() {
  if (!CAMPAIGN_MODE) return false
  if (String(s.imageUrl || "").trim()) return true
  const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
  for (const arr of Object.values(revMap)) {
    if (Array.isArray(arr) && arr.length > 0) return true
  }
  return false
}
