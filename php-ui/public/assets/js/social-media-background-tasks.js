import { apiRequest, authHeaders, cfg, T } from "./social-media-api.js"
import { CAMPAIGN_MODE, scheduledPostsCollection } from "./social-media-campaign-utils.js"
import { COMPOSER_PENDING_KINDS } from "./social-media-constants.js"
import { getTaskStatus, socialCreate } from "./social-media-data.js"
import {
  clearImageHttpInFlight,
  clearVisualPendingHint,
  countComposerPendingTasks,
  debugLog,
  hasPendingHolidayDraft,
  loadPendingTasks,
  queuePendingHolidayTask,
  readImageHttpInFlightBanner,
  removePendingTask,
  setPendingTaskProgress,
} from "./social-media-runtime.js"
import { parseScheduledLocalDateTime } from "./social-media-post-utils.js"
import { s } from "./social-media-state.js"

export function createBackgroundTasks(deps) {
  const {
    activeAccount,
    applyGeneratedVideoUrl,
    appendAiUrls,
    buildIntegration,
    findRevisionBase,
    findWatchEntry,
    getHolidayLabelsSync,
    lsKeyOpenAi,
    paint,
    paintCalendar,
    paintModals,
    paintTaskBanner,
    persistActiveDraftQuiet,
    persistPendingVisualToDraft,
    persistPendingVisualToPost,
    refreshData,
    setStatus,
    syncAssetOrderFromCollections,
    syncOpenModalsFromDom,
    user,
  } = deps

  async function createHolidayDraftForDateKey(dateKey) {
    if (!user()) {
      setStatus(T("holidayDraftsNeedUser"))
      return
    }
    const account = activeAccount()
    if (!account) {
      setStatus(T("holidayDraftsNeedAccount"))
      return
    }
    if (!(account.instagramAccessToken || "").trim()) {
      setStatus(T("holidayDraftsNeedAccount"))
      return
    }
    if (!lsKeyOpenAi()) {
      setStatus(T("holidayDraftsNeedOpenAI"))
      return
    }
    const dt = parseScheduledLocalDateTime(dateKey, "12:00")
    if (!dt) return
    const labels = getHolidayLabelsSync(dt, cfg().uiLocale || "tr")
    if (!labels.length) {
      setStatus(T("holidayDraftDayNotHoliday"))
      return
    }
    const name = labels.join(" · ")
    const mc = dt.getMonth() + 1
    const dc = dt.getDate()
    const watch = findWatchEntry(mc, dc, name) || findWatchEntry(mc, dc, labels[0])
    const extraInstructions = ((watch && watch.gptExtraInstructions) || "").trim()
    const exists = s.posts.some(
      (p) => p.date === dateKey && p.source === "holiday" && String(p.holidayName || "") === name,
    )
    if (exists) {
      setStatus(T("holidayDraftExists"))
      return
    }
    if (hasPendingHolidayDraft(dateKey, name)) {
      setStatus(T("holidayDraftAlreadyQueued"))
      return
    }
    const timeStr = (s.scheduledTime || "12:00").trim() || "12:00"
    const captionHint = T("holidayDraftCaptionHint")
    s.holidayBusyDateKey = dateKey
    setStatus(T("holidayDraftsAiProgress").replace("{i}", "1").replace("{n}", "1"))
    paint()
    try {
      const raw = await apiRequest("/social-media/holiday/generate", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          holiday_name: name,
          date_key: dateKey,
          locale: cfg().uiLocale || "tr",
          generate_image: true,
          generate_video: false,
          ...(extraInstructions ? { extra_instructions: extraInstructions } : {}),
          ...buildIntegration(account),
        }),
      })
      if (raw && raw.queued === true && typeof raw.task_id === "string" && raw.task_id.trim()) {
        queuePendingHolidayTask(raw.task_id.trim(), {
          dateKey,
          timeStr,
          holidayName: name,
          accountId: account.id,
          accountName: account.name,
          captionHint,
          ...(extraInstructions ? { extraInstructions } : {}),
        })
        setStatus(T("holidayDraftQueued"))
        return
      }
      const caption = String(raw.caption ?? `${name}\n\n${captionHint}`)
      const imageUrl = String(raw.image_url ?? "")
      const prompt = String(raw.image_prompt ?? name)
      await socialCreate(scheduledPostsCollection(), {
        accountId: account.id,
        accountName: account.name,
        date: dateKey,
        time: timeStr,
        prompt,
        caption,
        imageUrl,
        publishStatus: "pending",
        approvalStatus: "pending",
        source: "holiday",
        holidayName: name,
        createdAt: new Date().toISOString(),
      })
      setStatus(T("holidayDraftSingleCreated"))
    } catch (err) {
      setStatus(err instanceof Error ? err.message : T("holidayDraftsAiFailed"))
    } finally {
      s.holidayBusyDateKey = null
      paintTaskBanner()
      paintCalendar()
      await refreshData()
    }
  }

  async function pollPendingTasksOnce() {
    const tasks = loadPendingTasks()
    if (!tasks.length) return
    debugLog("poll.start", { pending: tasks.length })
    let needRefresh = false
    for (const task of tasks) {
      let status
      try {
        status = await getTaskStatus(task.taskId)
        debugLog("poll.status", { taskId: task.taskId, kind: task.kind, status: status && status.status })
        if (status && typeof status.progress === "number") setPendingTaskProgress(task.taskId, task.kind, status.progress)
      } catch (err) {
        console.warn("[social-media] getTaskStatus", task.taskId, err)
        debugLog("poll.error", { taskId: task.taskId, kind: task.kind, error: err instanceof Error ? err.message : String(err || "") })
        setStatus(T("backgroundPollUnreachable"))
        continue
      }
      if (task.kind === "holiday") {
        const meta = task.meta
        if (status.status === "success") {
          if (!user() || !meta) {
            removePendingTask(task.taskId)
            needRefresh = true
            continue
          }
          const r = status.result || {}
          const hint = (meta && meta.captionHint) || T("holidayDraftCaptionHint")
          const fallbackCaption = `${meta.holidayName}\n\n${hint}`
          const caption = String(r.caption ?? fallbackCaption)
          const imageUrlRaw = String(r.image_url ?? "").trim()
          const videoUrlRaw = String(r.video_url ?? "").trim()
          const imageUrl = imageUrlRaw || videoUrlRaw
          const prompt = String(r.image_prompt ?? r.video_prompt ?? meta.holidayName)
          try {
            await socialCreate(scheduledPostsCollection(), {
              accountId: meta.accountId,
              accountName: meta.accountName,
              date: meta.dateKey,
              time: meta.timeStr,
              prompt,
              caption,
              imageUrl,
              publishStatus: "pending",
              approvalStatus: "pending",
              source: "holiday",
              holidayName: meta.holidayName,
              createdAt: new Date().toISOString(),
            })
            removePendingTask(task.taskId)
            setStatus(T("holidayDraftSingleCreated"))
            needRefresh = true
          } catch (e) {
            setStatus(e instanceof Error ? e.message : T("holidayDraftsAiFailed"))
            removePendingTask(task.taskId)
            needRefresh = true
          }
        } else if (status.status === "failure") {
          setStatus(status.error || T("holidayDraftsAiFailed"))
          removePendingTask(task.taskId)
          needRefresh = true
        } else {
          const progress = typeof status.progress === "number" ? `${status.progress}%` : ""
          if (progress) setStatus(`${T("backgroundJobProgress")} ${progress}`.trim())
        }
        continue
      }
      if (COMPOSER_PENDING_KINDS.has(task.kind)) {
        if (status.status === "success") {
          const r = status.result || {}
          if (task.kind === "video") {
            const v = String(r.video_url ?? r.url ?? "").trim()
            const cap = String(r.caption ?? "").trim()
            if (cap) s.caption = cap
            const taskDraftId = task.meta && typeof task.meta.draftId === "string" ? task.meta.draftId : ""
            const taskPostId = task.meta && typeof task.meta.postId === "string" ? task.meta.postId : ""
            if (taskDraftId) await persistPendingVisualToDraft(taskDraftId, { kind: "video", url: v, caption: cap })
            if (taskPostId) await persistPendingVisualToPost(taskPostId, { kind: "video", url: v, caption: cap })
            if (v && (s.studioOpen || s.activeDraftId)) {
              if (s.studioOpen) syncOpenModalsFromDom()
              applyGeneratedVideoUrl(v)
              persistActiveDraftQuiet()
              if (s.studioOpen) paintModals(true)
            }
            removePendingTask(task.taskId)
            clearVisualPendingHint()
            setStatus(v ? T("composerVideoReady") : T("composerImagesIssue"))
            needRefresh = true
          } else {
            clearVisualPendingHint()
            const urls = Array.isArray(r.images)
              ? r.images.map((x) => (typeof x === "string" ? x : x && x.url)).filter(Boolean)
              : []
            const taskDraftId = task.meta && typeof task.meta.draftId === "string" ? task.meta.draftId : ""
            const taskPostId = task.meta && typeof task.meta.postId === "string" ? task.meta.postId : ""
            const taskBaseUrl = task.meta && typeof task.meta.baseUrl === "string" ? task.meta.baseUrl : ""
            if (taskDraftId && urls.length) await persistPendingVisualToDraft(taskDraftId, { kind: task.kind, urls, baseUrl: taskBaseUrl })
            if (taskPostId && urls.length) await persistPendingVisualToPost(taskPostId, { kind: task.kind, urls, baseUrl: taskBaseUrl })
            if (task.kind === "revise" && urls.length) {
              const current = (s.imageUrl || "").trim()
              const base = taskBaseUrl || findRevisionBase(current) || current || (s.assetOrder[0] || "").trim()
              if (base) {
                const existing = Array.isArray(s.revisionMap[base]) ? s.revisionMap[base] : [base]
                const merged = [...new Set([...existing, ...urls])].filter(Boolean)
                s.revisionMap = { ...(s.revisionMap || {}), [base]: merged }
                s.selectedRevisionByBase = {
                  ...(s.selectedRevisionByBase || {}),
                  [base]: urls[0],
                }
              }
            }
            if (urls[0] && (s.studioOpen || s.activeDraftId)) {
              if (s.studioOpen) syncOpenModalsFromDom()
              s.imageUrl = urls[0]
              if (task.kind !== "revise" || CAMPAIGN_MODE) appendAiUrls(urls)
              syncAssetOrderFromCollections()
              persistActiveDraftQuiet()
              if (s.studioOpen) paintModals(true)
            }
            removePendingTask(task.taskId)
            const sid = String(r.session_id ?? "").trim()
            if (sid && task.kind !== "revise") s.sessionId = sid
            if (task.kind === "revise") {
              if (urls.length) setStatus(T("composerReviseOk").replace("{n}", String(urls.length)))
              else setStatus(T("composerReviseFail"))
            } else if (urls.length) {
              setStatus(
                task.kind === "reference"
                  ? T("composerRefVariantsOk").replace("{n}", String(urls.length))
                  : T("composerImagesReady").replace("{n}", String(urls.length)),
              )
            } else {
              setStatus(T("composerImagesIssue"))
            }
            needRefresh = true
          }
        } else if (status.status === "failure") {
          removePendingTask(task.taskId)
          clearVisualPendingHint()
          setStatus(status.error || T("backgroundJobFailed"))
          needRefresh = true
        } else {
          const progress = typeof status.progress === "number" ? `${status.progress}%` : ""
          if (progress) setStatus(`${T("backgroundJobProgress")} ${progress}`.trim())
        }
      }
    }
    const nComposer = countComposerPendingTasks()
    if (nComposer > 0) clearImageHttpInFlight()
    if (nComposer === 0 && !readImageHttpInFlightBanner()) clearVisualPendingHint()
    if (needRefresh) await refreshData()
    else paintTaskBanner()
  }

  return { createHolidayDraftForDateKey, pollPendingTasksOnce }
}
