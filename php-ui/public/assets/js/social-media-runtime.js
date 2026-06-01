import { apiRequest, authHeaders, token } from "./social-media-api.js"
import {
  COMPOSER_PENDING_KINDS,
  MAX_VISUAL_PENDING_HINT_MS,
  PENDING_TASKS_STORAGE_KEY,
  SM_CAPTION_IN_FLIGHT_KEY,
  SM_DEBUG_BATCH_MAX,
  SM_DEBUG_EVENTS_KEY,
  SM_DEBUG_FLUSH_MS,
  SM_DEBUG_MAX_EVENTS,
  SM_DEBUG_MODE_KEY,
  SM_IMAGE_HTTP_IN_FLIGHT_KEY,
  SM_VISUAL_PENDING_HINT_KEY,
} from "./social-media-constants.js"

let smDebugEvents = readStoredDebugEvents()
let smDebugSessionEvents = []
let smDebugFlushTimer = 0
let smDebugFlushInFlight = false
let smDebugNextFlushAt = 0
let getPendingState = () => ({})

export function configurePendingState(getter) {
  getPendingState = typeof getter === "function" ? getter : () => ({})
}

export function readDebugMode() {
  try {
    const qs = new URLSearchParams(window.location.search)
    if (qs.get("sm_debug") === "1") return true
    const raw = localStorage.getItem(SM_DEBUG_MODE_KEY)
    return raw === "1" || raw === "true"
  } catch {
    return false
  }
}

export function writeDebugMode(v) {
  try {
    localStorage.setItem(SM_DEBUG_MODE_KEY, v ? "1" : "0")
  } catch {
    /* */
  }
}

function readStoredDebugEvents() {
  try {
    const raw = localStorage.getItem(SM_DEBUG_EVENTS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((row) => row && typeof row === "object" && typeof row.event === "string")
      .slice(-SM_DEBUG_MAX_EVENTS)
  } catch {
    return []
  }
}

function writeStoredDebugEvents(rows) {
  try {
    localStorage.setItem(SM_DEBUG_EVENTS_KEY, JSON.stringify(rows.slice(-SM_DEBUG_MAX_EVENTS)))
  } catch {
    /* */
  }
}

function appendStoredDebugEvent(row) {
  smDebugEvents.push(row)
  if (smDebugEvents.length > SM_DEBUG_MAX_EVENTS) {
    smDebugEvents = smDebugEvents.slice(smDebugEvents.length - SM_DEBUG_MAX_EVENTS)
  }
  writeStoredDebugEvents(smDebugEvents)
}

function sanitizeDebugPayload(value, depth = 0) {
  if (value == null) return value
  if (depth > 3) return "[depth-limit]"
  if (typeof value === "string") return value.length > 1000 ? value.slice(0, 1000) + "..." : value
  if (typeof value === "number" || typeof value === "boolean") return value
  if (Array.isArray(value)) return value.slice(0, 30).map((item) => sanitizeDebugPayload(item, depth + 1))
  if (typeof value === "object") {
    const out = {}
    Object.entries(value)
      .slice(0, 40)
      .forEach(([key, item]) => {
        out[key] = sanitizeDebugPayload(item, depth + 1)
      })
    return out
  }
  return String(value)
}

function scheduleDebugFlush() {
  if (smDebugFlushTimer || smDebugFlushInFlight || !token()) return
  const delay = Math.max(SM_DEBUG_FLUSH_MS, smDebugNextFlushAt - Date.now())
  smDebugFlushTimer = window.setTimeout(() => {
    smDebugFlushTimer = 0
    void flushDebugLogs()
  }, delay)
}

export async function flushDebugLogs({ all = false } = {}) {
  if (smDebugFlushInFlight || !token()) return false
  const batch = (all ? smDebugEvents : smDebugSessionEvents).slice(0, SM_DEBUG_BATCH_MAX)
  if (!batch.length) return true
  smDebugFlushInFlight = true
  try {
    await apiRequest("/client-debug/browser-logs", {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify({
        page: String(window.location.pathname || ""),
        debugEnabled: readDebugMode(),
        entries: batch,
      }),
    })
    if (!all) smDebugSessionEvents = smDebugSessionEvents.slice(batch.length)
    return true
  } catch {
    smDebugNextFlushAt = Date.now() + 10000
    return false
  } finally {
    smDebugFlushInFlight = false
    if (!all && smDebugSessionEvents.length) scheduleDebugFlush()
  }
}

export function debugLog(event, payload) {
  const row = {
    ts: new Date().toISOString(),
    event: String(event || ""),
    payload: sanitizeDebugPayload(payload ?? null),
  }
  appendStoredDebugEvent(row)
  smDebugSessionEvents.push(row)
  if (readDebugMode()) {
    console.debug("[sm-debug]", row.event, row.payload)
  }
  scheduleDebugFlush()
}

export function debugStatus() {
  return { enabled: readDebugMode(), events: smDebugEvents.length, pendingSend: smDebugSessionEvents.length }
}

export function debugEvents() {
  smDebugEvents = readStoredDebugEvents()
  return [...smDebugEvents]
}

export function clearDebugEvents() {
  smDebugEvents = []
  smDebugSessionEvents = []
  writeStoredDebugEvents([])
  return true
}

export function loadPendingTasks() {
  try {
    const raw = localStorage.getItem(PENDING_TASKS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((x) => x && typeof x.taskId === "string" && x.taskId.trim() && typeof x.kind === "string")
  } catch {
    return []
  }
}

export function savePendingTasks(tasks) {
  try {
    localStorage.setItem(PENDING_TASKS_STORAGE_KEY, JSON.stringify(tasks))
    debugLog("pending.save", { count: Array.isArray(tasks) ? tasks.length : 0 })
    window.dispatchEvent(new Event("sm-pending-tasks"))
  } catch {
    /* */
  }
}

export function removePendingTask(taskId) {
  clearPendingTaskProgress(taskId)
  savePendingTasks(loadPendingTasks().filter((t) => t.taskId !== taskId))
}

export function countComposerPendingTasks() {
  return loadPendingTasks().filter((t) => COMPOSER_PENDING_KINDS.has(t.kind)).length
}

export function countHolidayPendingTasks() {
  return loadPendingTasks().filter((t) => t.kind === "holiday").length
}

function clampProgressValue(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return Math.max(0, Math.min(100, Math.round(n)))
}

export function setPendingTaskProgress(taskId, kind, value) {
  const state = getPendingState()
  const id = String(taskId || "").trim()
  const progress = clampProgressValue(value)
  if (!id || progress === null) return
  state.pendingTaskProgress = {
    ...(state.pendingTaskProgress || {}),
    [id]: {
      taskId: id,
      kind: String(kind || "").trim(),
      progress,
      updatedAt: Date.now(),
    },
  }
}

export function clearPendingTaskProgress(taskId) {
  const state = getPendingState()
  const id = String(taskId || "").trim()
  if (!id || !state.pendingTaskProgress || typeof state.pendingTaskProgress !== "object") return
  if (!Object.prototype.hasOwnProperty.call(state.pendingTaskProgress, id)) return
  const next = { ...state.pendingTaskProgress }
  delete next[id]
  state.pendingTaskProgress = next
}

export function pendingTaskProgressSummary() {
  const state = getPendingState()
  const tasks = loadPendingTasks().filter((t) => COMPOSER_PENDING_KINDS.has(t.kind) || t.kind === "holiday")
  if (!tasks.length || !state.pendingTaskProgress || typeof state.pendingTaskProgress !== "object") return null
  const ids = new Set(tasks.map((t) => String(t.taskId || "").trim()).filter(Boolean))
  const entries = Object.values(state.pendingTaskProgress)
    .filter((p) => p && ids.has(String(p.taskId || "").trim()))
    .map((p) => clampProgressValue(p.progress))
    .filter((p) => p !== null)
  if (!entries.length) return null
  const progress = Math.round(entries.reduce((sum, n) => sum + n, 0) / entries.length)
  return { progress, count: entries.length }
}

export function hasPendingHolidayDraft(dateKey, holidayName) {
  return loadPendingTasks().some(
    (t) => t.kind === "holiday" && t.meta && t.meta.dateKey === dateKey && t.meta.holidayName === holidayName,
  )
}

export function queuePendingHolidayTask(taskId, meta) {
  const next = loadPendingTasks().filter((t) => t.taskId !== taskId)
  next.push({ taskId, kind: "holiday", createdAt: Date.now(), meta })
  savePendingTasks(next)
}

export function queuePendingComposerTask(taskId, kind, meta) {
  const next = loadPendingTasks().filter((t) => t.taskId !== taskId)
  next.push({ taskId, kind, createdAt: Date.now(), ...(meta && typeof meta === "object" ? { meta } : {}) })
  debugLog("pending.queue.composer", { taskId, kind, meta: meta || null })
  savePendingTasks(next)
}

export function readCaptionInFlightBanner() {
  try {
    const raw = sessionStorage.getItem(SM_CAPTION_IN_FLIGHT_KEY)
    if (!raw) return false
    const p = JSON.parse(raw)
    if (!p || typeof p.t !== "number") return false
    return Date.now() - p.t < 10 * 60 * 1000
  } catch {
    return false
  }
}

export function writeCaptionInFlight(payload) {
  try {
    sessionStorage.setItem(SM_CAPTION_IN_FLIGHT_KEY, JSON.stringify({ ...payload, t: Date.now() }))
  } catch {
    /* */
  }
}

export function clearCaptionInFlight() {
  try {
    sessionStorage.removeItem(SM_CAPTION_IN_FLIGHT_KEY)
  } catch {
    /* */
  }
}

export function readImageHttpInFlightBanner() {
  try {
    const raw = sessionStorage.getItem(SM_IMAGE_HTTP_IN_FLIGHT_KEY)
    if (!raw) return false
    const p = JSON.parse(raw)
    if (!p || typeof p.t !== "number") return false
    if (Date.now() - p.t < 3 * 60 * 1000) return true
    clearImageHttpInFlight()
    return false
  } catch {
    return false
  }
}

export function writeImageHttpInFlight() {
  try {
    sessionStorage.setItem(SM_IMAGE_HTTP_IN_FLIGHT_KEY, JSON.stringify({ t: Date.now() }))
  } catch {
    /* */
  }
}

export function clearImageHttpInFlight() {
  try {
    sessionStorage.removeItem(SM_IMAGE_HTTP_IN_FLIGHT_KEY)
  } catch {
    /* */
  }
}

export function writeVisualPendingHint(kind) {
  try {
    sessionStorage.setItem(SM_VISUAL_PENDING_HINT_KEY, JSON.stringify({ t: Date.now(), kind }))
  } catch {
    /* */
  }
}

export function clearVisualPendingHint() {
  try {
    sessionStorage.removeItem(SM_VISUAL_PENDING_HINT_KEY)
  } catch {
    /* */
  }
}

export function readVisualPendingHint() {
  try {
    const raw = sessionStorage.getItem(SM_VISUAL_PENDING_HINT_KEY)
    if (!raw) return null
    const p = JSON.parse(raw)
    if (!p || typeof p.t !== "number") return null
    if (Date.now() - p.t > MAX_VISUAL_PENDING_HINT_MS) {
      clearVisualPendingHint()
      return null
    }
    const k = p.kind
    if (k !== "generate" && k !== "reference" && k !== "revise" && k !== "video") return null
    return p
  } catch {
    return null
  }
}
