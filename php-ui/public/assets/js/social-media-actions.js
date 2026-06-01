import { cfg, T } from "./social-media-api.js"
import { CAMPAIGN_MODE, accountsCollection, draftsCollection, scheduledPostsCollection } from "./social-media-campaign-utils.js"
import { DEFAULT_CAMPAIGN_API_BASE_URL } from "./social-media-constants.js"
import {
  clearCampaignCatalogCache,
  DEL,
  deleteStorageImages,
  enrichAccountGraphIds,
  fetchLinkedIg,
  isManagedUploadStorageUrl,
  socialCreate,
  socialDelete,
  socialPatchFields,
  socialPut,
  TS,
} from "./social-media-data.js"
import { getHolidayLabelsSync } from "./social-media-holidays.js"
import { parseScheduledLocalDateTime } from "./social-media-post-utils.js"
import { debugLog } from "./social-media-runtime.js"
import { campaignTemplateCollections, localeTag, normalizeStudioPanel } from "./social-media-selectors.js"
import { rootEl, s } from "./social-media-state.js"

export function createDelegatedClickHandler(deps = {}) {
  const {
    applyTemplateToReviseWithScope,
    closeStudio,
    composePublishCampaign,
    composePublishInstagram,
    composerGenerateCampaignBanner,
    composerGenerateCaption,
    composerGenerateImages,
    composerHolidayVideo,
    composerReviseCaption,
    composerReviseImage,
    composerVideoFromReference,
    composerVideoFromText,
    createHolidayDraftForDateKey,
    dayCardCarouselKey,
    deletePostById,
    findRevisionBase,
    findWatchEntry,
    loadDraftIntoStudio,
    loadPostIntoStudio,
    maybeFetchGraphPublishCards,
    openStudioManual,
    paint,
    paintCalendar,
    paintDayPanel,
    paintModals,
    persistActiveDraftQuiet,
    persistEditingPostMediaState,
    refreshData,
    removeRailAsset,
    saveCalendarEntry,
    selectedReviseTemplate,
    setDayCardCarouselIndex,
    setStatus,
    shiftDayCardCarousel,
    syncDayCardCarouselDom,
    syncOpenModalsFromDom,
    upsertWatchEntry,
  } = deps

  return async function onDelegatedClick(e) {
    const el = e.target instanceof Element ? e.target : null
    if (!el) return
    const t = el.closest("[data-act]")
    const act = t && t.getAttribute("data-act")
    if (!act) return
    if (act === "filter-all") {
      s.filterAccountId = null
      paint()
    }
    if (act === "pick-account") {
      const id = t.getAttribute("data-aid")
      if (id) {
        s.filterAccountId = id
        s.activeAccountId = id
        if (CAMPAIGN_MODE) {
          s.campaignStores = []
          s.campaignStoreId = ""
          s.campaignId = ""
          s.campaignMediaUrls = []
          s.campaignMediaKey = ""
          clearCampaignCatalogCache()
          void refreshData()
        }
        paint()
      }
    }
    if (act === "open-account-modal") {
      s.accountModal = true
      s.editingAccountId = null
      s.accName = ""
      s.accToken = ""
      s.accCampaignBaseUrl = DEFAULT_CAMPAIGN_API_BASE_URL
      s.accCampaignKind = "store"
      s.accLogo = ""
      s.linkedRows = []
      s.linkedSelected = new Set()
      s.linkedErr = ""
      paintModals()
    }
    if (act === "cal-prev") {
      s.monthDate = new Date(s.monthDate.getFullYear(), s.monthDate.getMonth() - 1, 1)
      paint()
    }
    if (act === "cal-next") {
      s.monthDate = new Date(s.monthDate.getFullYear(), s.monthDate.getMonth() + 1, 1)
      paint()
    }
    if (act === "open-tickets") {
      if (CAMPAIGN_MODE) return
      s.ticketModal = true
      s.editingTicket = null
      s.ticketDraft = { name: "", description: "" }
      paintModals()
    }
    if (act === "open-templates") {
      s.templateModal = true
      s.editingTemplate = null
      s.templateDraft = { title: "", prompt: "", imageUrls: [], outputSize: "post_4_5" }
      paintModals()
    }
    if (act === "close-ticket" || act === "close-ticket-bg") {
      if (act === "close-ticket-bg" && el.closest("[data-stop]")) return
      s.ticketModal = false
      paintModals()
    }
    if (act === "close-tpl" || act === "close-tpl-bg") {
      if (act === "close-tpl-bg" && el.closest("[data-stop]")) return
      s.templateModal = false
      paintModals()
    }
    if (act === "tk-save") {
      const name = rootEl.querySelector("#tk-name")?.value?.trim() || ""
      const description = rootEl.querySelector("#tk-desc")?.value?.trim() || ""
      if (!name) return
      try {
        if (s.editingTicket) await socialPut("tickets", s.editingTicket.id, { name, description }, true)
        else await socialCreate("tickets", { name, description, createdAt: new Date().toISOString() })
        s.ticketModal = false
        await refreshData()
      } catch (err) {
        setStatus(err instanceof Error ? err.message : "err")
      }
    }
    if (act === "tk-edit") {
      const id = t.getAttribute("data-tid")
      const tk = s.tickets.find((x) => x.id === id)
      if (tk) {
        s.editingTicket = tk
        s.ticketDraft = { name: tk.name, description: tk.description }
        paintModals()
        const n = rootEl.querySelector("#tk-name")
        const d = rootEl.querySelector("#tk-desc")
        if (n) n.value = tk.name
        if (d) d.value = tk.description
      }
    }
    if (act === "tk-del") {
      const id = t.getAttribute("data-tid")
      if (id) {
        await socialDelete("tickets", id)
        await refreshData()
      }
    }
    if (act === "st-apply-template") {
      const id = t.getAttribute("data-id")
      const scope = t.getAttribute("data-scope") || "user"
      if (id) applyTemplateToReviseWithScope(id, scope)
    }
    if (act === "st-edit-template") {
      const id = t.getAttribute("data-id")
      const tpl = s.userTemplates.find((x) => x.id === id)
      if (tpl) {
        s.templateModal = true
        s.editingTemplate = tpl
        s.templateDraft = { title: tpl.title, prompt: tpl.prompt, imageUrls: [...(tpl.imageUrls || [])], outputSize: tpl.outputSize || "post_4_5" }
        debugLog("template.edit.open_from_studio", {
          id,
          hasTitle: Boolean(tpl.title),
          hasPrompt: Boolean(tpl.prompt),
          imageCount: (tpl.imageUrls || []).length,
        })
        paintModals(true)
      }
    }
    if (act === "st-clear-template") {
      const selectedTpl = selectedReviseTemplate()
      const p = String(selectedTpl?.prompt || "").trim()
      s.selectedTemplateId = null
      s.selectedTemplateScope = "user"
      if (String(s.reviseFeedback || "").trim() === p) s.reviseFeedback = ""
      paintModals()
    }
    if (act === "tpl-pick-layout") {
      rootEl.querySelector("#tpl-file-layout")?.click()
    }
    if (act === "tpl-pick-logo") {
      rootEl.querySelector("#tpl-file-logo")?.click()
    }
    if (act === "tpl-remove-layout") {
      const cur = [...(s.templateDraft.imageUrls || [])]
      const logo = String(cur[1] || "").trim()
      s.templateDraft.imageUrls = logo ? [logo] : []
      paintModals()
    }
    if (act === "tpl-remove-logo") {
      const cur = [...(s.templateDraft.imageUrls || [])]
      const layout = String(cur[0] || "").trim()
      s.templateDraft.imageUrls = layout ? [layout] : []
      paintModals()
    }
    if (act === "tpl-remove-img") {
      const idx = Number(t.getAttribute("data-index") || "-1")
      if (Number.isFinite(idx) && idx >= 0) {
        const next = [...(s.templateDraft.imageUrls || [])]
        next.splice(idx, 1)
        s.templateDraft.imageUrls = next
        paintModals()
      }
    }
    if (act === "tpl-set-size") {
      const size = t.getAttribute("data-size")
      const sel = rootEl.querySelector("#tpl-output-size")
      if (size && sel instanceof HTMLSelectElement) {
        sel.value = size
        s.templateDraft.outputSize = size
        paintModals(true)
      }
    }
    if (act === "tpl-save") {
      const title = rootEl.querySelector("#tpl-title")?.value?.trim() || ""
      const prompt = rootEl.querySelector("#tpl-prompt")?.value?.trim() || ""
      const outputSizeEl = rootEl.querySelector("#tpl-output-size")
      const outputSize = outputSizeEl instanceof HTMLSelectElement
        ? String(outputSizeEl.value || "post_4_5")
        : String(s.templateDraft.outputSize || "post_4_5")
      const layout = String((s.templateDraft.imageUrls || [])[0] || "").trim()
      if (!layout) {
        setStatus(T("tplCampaignNeedLayoutSave"))
        return
      }
      const logo = String((s.templateDraft.imageUrls || [])[1] || "").trim()
      const tail = (s.templateDraft.imageUrls || []).slice(2).map((x) => String(x || "").trim()).filter(Boolean)
      const imageUrls = [layout, ...(logo ? [logo] : []), ...tail]
      if (!title || !prompt) return
      try {
        const tplCollections = campaignTemplateCollections()
        const base = { title, prompt, imageUrls, outputSize, updatedAt: TS }
        if (s.editingTemplate) await socialPatchFields(tplCollections.user, s.editingTemplate.id, base)
        else await socialCreate(tplCollections.user, { title, prompt, imageUrls, outputSize, updatedAt: new Date().toISOString(), createdAt: new Date().toISOString() })
        s.templateModal = false
        setStatus(T("composerTemplatesSaved"))
        await refreshData()
      } catch {
        setStatus(T("composerTemplatesSaveFailed"))
      }
    }
    if (act === "tpl-edit") {
      const id = t.getAttribute("data-id")
      const tpl = s.userTemplates.find((x) => x.id === id)
      if (tpl) {
        s.editingTemplate = tpl
        s.templateDraft = { title: tpl.title, prompt: tpl.prompt, imageUrls: [...(tpl.imageUrls || [])], outputSize: tpl.outputSize || "post_4_5" }
        debugLog("template.edit.open_from_list", {
          id,
          hasTitle: Boolean(tpl.title),
          hasPrompt: Boolean(tpl.prompt),
          imageCount: (tpl.imageUrls || []).length,
        })
        paintModals(true)
      }
    }
    if (act === "tpl-del") {
      const id = t.getAttribute("data-id")
      if (!id || !window.confirm(T("composerTemplatesDeleteConfirm"))) return
      try {
        const tpl = s.userTemplates.find((x) => x.id === id)
        if (tpl) await deleteStorageImages(tpl.imageUrls)
        const tplCollections = campaignTemplateCollections()
        await socialDelete(tplCollections.user, id)
        await refreshData()
      } catch {
        setStatus(T("composerTemplatesDeleteFailed"))
      }
    }
    if (act === "day-tab") {
      const tab = t.getAttribute("data-tab")
      if (tab) {
        s.dayListTab = tab
        s.selectedPostId = ""
        paintCalendar()
        paintDayPanel()
      }
    }
    if (act === "select-post-highlight") {
      const pid = t.getAttribute("data-pid") || ""
      s.selectedPostId = s.selectedPostId === pid ? "" : pid
      paintCalendar()
      paintDayPanel()
    }
    if (act === "workflow-select") {
      const wid = String(t.getAttribute("data-wid") || "").trim()
      if (wid) {
        s.selectedWorkflowId = wid
        paintDayPanel()
      }
    }
    if (act === "day-card-prev" || act === "day-card-next") {
      const kind = t.getAttribute("data-kind") || ""
      const id = t.getAttribute("data-id") || ""
      if (kind && id) shiftDayCardCarousel(kind, id, act === "day-card-prev" ? -1 : 1)
    }
    if (act === "day-card-dot") {
      const kind = t.getAttribute("data-kind") || ""
      const id = t.getAttribute("data-id") || ""
      const idx = Number(t.getAttribute("data-index") || "0")
      if (kind && id && Number.isFinite(idx)) {
        const key = dayCardCarouselKey(kind, id)
        const cards = Array.from(rootEl.querySelectorAll("[data-card-carousel]"))
        const card = cards.find((elx) => elx.getAttribute("data-card-carousel") === key)
        const total = Number(card?.getAttribute("data-carousel-total") || "0")
        setDayCardCarouselIndex(kind, id, total, idx)
        syncDayCardCarouselDom(kind, id)
      }
    }
    if (act === "edit-post") {
      const id = t.getAttribute("data-pid")
      const post = s.posts.find((p) => p.id === id)
      if (post) loadPostIntoStudio(post)
    }
    if (act === "del-post") {
      const id = t.getAttribute("data-pid")
      if (id) void deletePostById(id)
    }
    if (act === "approve-post" || act === "reject-post") {
      const id = t.getAttribute("data-pid")
      const post = id ? s.posts.find((p) => p.id === id) : null
      if (!post) return
      e.preventDefault()
      e.stopPropagation()
      const approved = act === "approve-post"
      void (async () => {
        try {
          await socialPatchFields(
            post._collection || scheduledPostsCollection(),
            post.id,
            approved
              ? { approvalStatus: "approved", status: "scheduled", publishStatus: "pending" }
              : { approvalStatus: "rejected" },
          )
          await refreshData()
          paint()
        } catch (err) {
          setStatus(err instanceof Error ? err.message : T("planMoveFailed"))
        }
      })()
    }
    if (act === "resume-draft") {
      const id = t.getAttribute("data-did")
      const d = s.drafts.find((x) => x.id === id)
      if (d) loadDraftIntoStudio(d)
    }
    if (act === "del-draft") {
      const id = t.getAttribute("data-did")
      const d = s.drafts.find((x) => x.id === id)
      if (d && window.confirm(T("deletePlanConfirm"))) {
        void (async () => {
          try {
            await deleteStorageImages([d.imageUrl, ...(d.imageUrls || [])].filter((u) => isManagedUploadStorageUrl(u)))
            await socialDelete(draftsCollection(), id)
            s.drafts = s.drafts.filter((x) => x.id !== id)
            if (s.activeDraftId === id) s.activeDraftId = ""
            paint()
            await refreshData()
          } catch {
            /* */
          }
        })()
      }
    }
    if (act === "close-acc" || act === "close-acc-bg") {
      if (act === "close-acc-bg" && el.closest("[data-stop]")) return
      s.accountModal = false
      paintModals()
    }
    if (act === "acc-logo-pick") rootEl.querySelector("#acc-logo-file")?.click()
    if (act === "acc-logo-clear") {
      s.accLogo = ""
      paintModals()
    }
    if (act === "linked-load") {
      const tok = (rootEl.querySelector("#acc-token")?.value || s.accToken).trim()
      if (tok.length < 10) {
        s.linkedErr = T("linkedIgNeedToken")
        paintModals()
        return
      }
      s.linkedLoading = true
      s.linkedErr = ""
      paintModals()
      try {
        const { accounts } = await fetchLinkedIg(tok)
        s.linkedRows = (accounts || []).map((a) => ({
          instagramUserId: String(a.instagram_user_id ?? "").trim(),
          facebookPageId: String(a.facebook_page_id ?? "").trim(),
          facebookPageName: String(a.facebook_page_name ?? "").trim(),
          instagramUsername: String(a.instagram_username ?? "").trim(),
          draftLabel: (() => {
            const u = String(a.instagram_username ?? "").trim()
            if (u) return "@" + u
            return String(a.facebook_page_name ?? "").trim() || String(a.instagram_user_id ?? "").trim()
          })(),
        }))
        s.linkedSelected = new Set(s.linkedRows.map((r) => r.instagramUserId))
      } catch (err) {
        s.linkedRows = []
        s.linkedSelected = new Set()
        s.linkedErr = err instanceof Error ? err.message : T("linkedIgListFailed")
      } finally {
        s.linkedLoading = false
        paintModals()
      }
    }
    if (act === "linked-add") {
      const tok = (rootEl.querySelector("#acc-token")?.value || s.accToken).trim()
      const selected = s.linkedRows.filter((r) => s.linkedSelected.has(r.instagramUserId))
      if (!tok || !selected.length) return
      const existing = new Set(s.accounts.map((a) => (a.instagramUserId || "").trim()).filter(Boolean))
      let added = 0
      let skipped = 0
      for (const r of selected) {
        if (existing.has(r.instagramUserId)) {
          skipped++
          continue
        }
        const name = (r.draftLabel || "Instagram").trim()
        await socialCreate(accountsCollection(), {
          name,
          instagramAccessToken: tok,
          instagramUserId: r.instagramUserId,
          facebookPageId: r.facebookPageId || undefined,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        })
        added++
        existing.add(r.instagramUserId)
      }
      s.accountModal = false
      setStatus(T("linkedIgAddedSummary").replace("{added}", String(added)).replace("{skipped}", String(skipped)))
      await refreshData()
    }
    if (act === "acc-save") {
      s.accName = rootEl.querySelector("#acc-name")?.value?.trim() || ""
      s.accToken = rootEl.querySelector("#acc-token")?.value?.trim() || ""
      s.accCampaignBaseUrl = rootEl.querySelector("#acc-campaign-base")?.value?.trim() || ""
      s.accCampaignKind = rootEl.querySelector("#acc-campaign-kind")?.value === "restaurant" ? "restaurant" : "store"
      if (!s.accName || !s.accToken) {
        setStatus(T("msgAccountRequiredFields"))
        return
      }
      if (CAMPAIGN_MODE && !s.accCampaignBaseUrl) {
        setStatus("Campaign API Base URL zorunlu.")
        return
      }
      void (async () => {
        try {
          if (CAMPAIGN_MODE) {
            const payload = {
              name: s.accName,
              campaignAccountKind: s.accCampaignKind,
              campaignApiKey: s.accToken,
              campaignApiBaseUrl: s.accCampaignBaseUrl,
              updatedAt: TS,
            }
            if (s.editingAccountId) {
              await socialPatchFields(accountsCollection(), s.editingAccountId, payload)
              s.activeAccountId = s.editingAccountId
            } else {
              const created = await socialCreate(accountsCollection(), {
                ...payload,
                updatedAt: new Date().toISOString(),
                createdAt: new Date().toISOString(),
              })
              s.activeAccountId = created.id
            }
            s.filterAccountId = s.activeAccountId
            s.accountModal = false
            clearCampaignCatalogCache()
            setStatus("Kampanya hesabi kaydedildi.")
            await refreshData()
            return
          }
          const logoPayload = s.accLogo.trim() ? { logoUrl: s.accLogo.trim() } : { logoUrl: DEL }
          if (s.editingAccountId) {
            const prev = s.accounts.find((a) => a.id === s.editingAccountId)
            const tokenChanged = prev && prev.instagramAccessToken.trim() !== s.accToken
            await socialPatchFields(accountsCollection(), s.editingAccountId, {
              name: s.accName,
              instagramAccessToken: s.accToken,
              ...logoPayload,
              ...(tokenChanged
                ? {
                    instagramTokenExpiresAt: DEL,
                    instagramTokenExpiresInSeconds: DEL,
                    instagramUserId: DEL,
                    facebookPageId: DEL,
                  }
                : {
                    ...(prev?.instagramUserId?.trim() ? { instagramUserId: prev.instagramUserId.trim() } : {}),
                    ...(prev?.facebookPageId?.trim() ? { facebookPageId: prev.facebookPageId.trim() } : {}),
                  }),
              updatedAt: TS,
            })
            if (tokenChanged) {
              const filled = await enrichAccountGraphIds(s.editingAccountId, s.accToken)
              setStatus(filled ? T("msgAccountUpdatedIdsFromToken") : T("msgAccountKeysUpdated"))
            } else setStatus(T("msgAccountKeysUpdated"))
          } else {
            const created = await socialCreate(accountsCollection(), {
              name: s.accName,
              instagramAccessToken: s.accToken,
              ...(s.accLogo.trim() ? { logoUrl: s.accLogo.trim() } : {}),
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            })
            s.activeAccountId = created.id
            const filled = await enrichAccountGraphIds(created.id, s.accToken)
            setStatus(filled ? T("msgAccountAddedIdsFromToken") : T("msgAccountAdded"))
          }
          s.accountModal = false
          await refreshData()
        } catch (err) {
          setStatus(err instanceof Error ? err.message : "err")
        }
      })()
    }
    if (act === "ctx-del-acc") {
      const id = s.contextAccountId
      s.contextAccountId = null
      paintModals()
      if (id) {
        await socialDelete(accountsCollection(), id)
        if (s.activeAccountId === id) s.activeAccountId = ""
        if (s.filterAccountId === id) s.filterAccountId = null
        setStatus(T("msgAccountRemoved"))
        await refreshData()
      }
    }
    if (act === "ctx-manual") {
      const dk = t.getAttribute("data-dk")
      s.dayMenu = null
      paintModals()
      if (dk) s.selectedDate = dk
      s.studioDefaultTab = "manual"
      paint()
      openStudioManual()
    }
    if (act === "ctx-ig-post" || act === "ctx-ig-story") {
      const dk = t.getAttribute("data-dk")
      s.dayMenu = null
      paintModals()
      if (dk) s.selectedDate = dk
      s.studioDefaultTab = "manual"
      if (act === "ctx-ig-story") {
        s.studioMode = "story"
        s.publishTargets = { instagramPost: false, instagramStory: true, facebookPost: false }
      } else {
        s.studioMode = "post"
        s.publishTargets = { instagramPost: true, instagramStory: false, facebookPost: true }
      }
      paint()
      openStudioManual()
    }
    if (act === "ctx-holiday-draft") {
      const dk = t.getAttribute("data-dk")
      s.dayMenu = null
      paintModals()
      if (dk) void createHolidayDraftForDateKey(dk)
    }
    if (act === "ctx-holiday-settings") {
      const dk = t.getAttribute("data-dk")
      s.dayMenu = null
      paintModals()
      if (!dk) return
      const dt = parseScheduledLocalDateTime(dk, "12:00")
      if (!dt) return
      const labels = getHolidayLabelsSync(dt, cfg().uiLocale || "tr")
      if (!labels.length) {
        setStatus(T("holidayDraftDayNotHoliday"))
        return
      }
      const holidayName = labels.join(" · ")
      const mc = dt.getMonth() + 1
      const dc = dt.getDate()
      const ex = findWatchEntry(mc, dc, holidayName) || findWatchEntry(mc, dc, labels[0])
      const dateLine = dt.toLocaleDateString(localeTag(), {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
      })
      s.holidaySettings = {
        dateKey: dk,
        holidayName,
        month: mc,
        day: dc,
        renewYearly: ex ? ex.renewYearly !== false : true,
        instructions: (ex && ex.gptExtraInstructions) || "",
        dateLine,
      }
      paintModals()
    }
    if (act === "hs-renew") {
      if (s.holidaySettings) {
        s.holidaySettings.renewYearly = !s.holidaySettings.renewYearly
        paintModals()
      }
    }
    if (act === "close-hol-set-bg") {
      if (el.closest("[data-stop]")) return
      s.holidaySettings = null
      paintModals()
    }
    if (act === "hs-cancel") {
      s.holidaySettings = null
      paintModals()
    }
    if (act === "hs-save") {
      const hs = s.holidaySettings
      if (!hs) return
      const instructions = rootEl.querySelector("#hs-ta")?.value?.trim() || ""
      upsertWatchEntry({
        month: hs.month,
        day: hs.day,
        holidayName: hs.holidayName,
        renewYearly: hs.renewYearly,
        gptExtraInstructions: instructions,
      })
      s.holidaySettings = null
      setStatus(T("holidaySettingsSaved"))
      paintModals()
    }
    if (act === "close-studio" || act === "close-studio-bg") {
      if (act === "close-studio-bg" && el.closest("[data-stop]")) return
      closeStudio(true)
    }
    if (act === "st-save") {
      syncOpenModalsFromDom()
      s.selectedDate = rootEl.querySelector("#st-date")?.value || s.selectedDate
      s.scheduledTime = rootEl.querySelector("#st-time")?.value || "12:00"
      const stImg = rootEl.querySelector("#st-img")
      if (stImg instanceof HTMLInputElement) s.imageUrl = stImg.value.trim() || s.imageUrl
      const pr = rootEl.querySelector("#st-prompt")
      if (pr instanceof HTMLTextAreaElement) s.prompt = pr.value
      const flowTop = rootEl.querySelector("#st-flow-topic")
      if (flowTop instanceof HTMLInputElement) s.lastTopic = flowTop.value
      const top = rootEl.querySelector("#st-topic")
      if (top instanceof HTMLTextAreaElement) s.lastTopic = top.value
      const capEl = rootEl.querySelector("#st-cap")
      s.caption = capEl instanceof HTMLTextAreaElement ? capEl.value : s.caption
      s.publishTargets = {
        instagramPost: Boolean(rootEl.querySelector("#pt-ig")?.checked),
        instagramStory: Boolean(rootEl.querySelector("#pt-st")?.checked),
        facebookPost: Boolean(rootEl.querySelector("#pt-fb")?.checked),
      }
      void saveCalendarEntry()
    }
    if (act === "st-upload") rootEl.querySelector("#st-file")?.click()
    if (act === "st-tab-manual") {
      s.studioTab = "manual"
      paintModals()
    }
    if (act === "st-tab-ai") {
      s.studioTab = "ai"
      s.composerStep = 0
      paintModals()
      void maybeFetchGraphPublishCards()
    }
    if (act === "st-step") {
      const step = Number(t.getAttribute("data-step"))
      if (step >= 0 && step <= 2) s.composerStep = step
      paintModals()
    }
    if (act === "st-pick-acc") {
      if (CAMPAIGN_MODE) return
      const id = t.getAttribute("data-aid")
      if (id) {
        s.activeAccountId = id
        paintModals()
      }
    }
    if (act === "st-toggle-appr") {
      s.composerApproved = !s.composerApproved
      paintModals()
    }
    if (act === "st-gen-caption") void composerGenerateCaption()
    if (act === "st-revise-caption") void composerReviseCaption()
    if (act === "st-gen-images") void composerGenerateImages()
    if (act === "st-caption-mode-manual") {
      s.captionMode = "manual"
      paintModals()
    }
    if (act === "st-caption-mode-ai") {
      s.captionMode = "ai"
      paintModals()
    }
    if (act === "st-next-visual") {
      syncOpenModalsFromDom()
      if (!s.caption.trim()) return
      s.composerStep = 1
      paintModals()
    }
    if (act === "st-back-caption") {
      s.composerStep = 0
      paintModals()
    }
    if (act === "st-next-publish") {
      syncOpenModalsFromDom()
      if (!(s.imageUrl || "").trim()) return
      s.composerStep = 2
      paintModals()
      void maybeFetchGraphPublishCards()
    }
    if (act === "st-back-media") {
      s.composerStep = 1
      paintModals()
    }
    if (act === "st-vis-kind-img") {
      s.visualOutputKind = "image"
      if (s.mediaMode === "ai_revise") s.mediaMode = "ai_direct"
      paintModals()
    }
    if (act === "st-vis-kind-vid") {
      s.visualOutputKind = "video"
      if (s.mediaMode === "ai_revise") s.mediaMode = "ai_direct"
      paintModals()
    }
    if (act === "st-media-mode") {
      const m = t.getAttribute("data-mode")
      if (m === "manual" || m === "ai_direct" || m === "ai_revise") {
        if (s.visualOutputKind === "video" && m === "ai_revise") return
        s.mediaMode = m
        paintModals()
      }
    }
    if (act === "st-gen-sub-manual") {
      s.generateSubTab = "manual"
      paintModals()
    }
    if (act === "st-gen-sub-ticket") {
      if (CAMPAIGN_MODE) return
      s.generateSubTab = "ticket"
      paintModals()
    }
    if (act === "st-toggle-use-ref") {
      s.useSelectedAsReference = !s.useSelectedAsReference
      paintModals()
    }
    if (act === "st-toggle-revise-refs") {
      s.useSelectedRefsForRevise = !s.useSelectedRefsForRevise
      paintModals()
    }
    if (act === "st-pick-ticket") {
      const id = t.getAttribute("data-tid")
      if (!id) return
      s.selectedTicketId = id
      const tk = s.tickets.find((x) => x.id === id)
      if (tk) s.directImagePrompt = String(tk.description || tk.name || "")
      paintModals()
    }
    if (act === "st-pick-ai-img") {
      const u = t.getAttribute("data-url")
      if (u) {
        s.imageUrl = u
        paintModals()
      }
    }
    if (act === "st-gen-direct") {
      if (CAMPAIGN_MODE && !String(s.campaignId || "").trim()) {
        setStatus("Önce bir kampanya seçin.")
      } else {
        void composerGenerateImages()
      }
    }
    if (act === "st-generate-campaign-banner") {
      if (!String(s.campaignId || "").trim()) {
        setStatus("Önce bir kampanya seçin.")
      } else {
        void composerGenerateCampaignBanner()
      }
    }
    if (act === "st-revise-image") {
      if (CAMPAIGN_MODE && !String(s.campaignId || "").trim()) {
        setStatus("Önce bir kampanya seçin.")
      } else {
        void composerReviseImage()
      }
    }
    if (act === "st-video-ai-mode") {
      const m = t.getAttribute("data-mode")
      if (m === "text" || m === "reference" || m === "holiday") {
        s.videoAiMode = m
        paintModals()
      }
    }
    if (act === "st-video-audio-toggle") {
      s.videoGenerateAudio = !s.videoGenerateAudio
      paintModals()
    }
    if (act === "st-gen-video-text") void composerVideoFromText()
    if (act === "st-gen-video-ref") void composerVideoFromReference()
    if (act === "st-gen-video-holiday") void composerHolidayVideo()
    if (act === "st-graph-card") {
      const k = t.getAttribute("data-key")
      if (k) {
        s.selectedGraphPublishKey = k
        paintModals()
      }
    }
    if (act === "st-publish-ig") void composePublishInstagram()
    if (act === "st-publish-campaign") void composePublishCampaign()
    if (act === "st-modal-panel") {
      const panel = t.getAttribute("data-panel")
      if (panel === "generate" || panel === "caption" || panel === "revise" || panel === "publish") {
        if (CAMPAIGN_MODE && panel !== "revise" && panel !== "publish") return
        s.modalPanel = normalizeStudioPanel(panel)
        if (s.modalPanel === "publish") void maybeFetchGraphPublishCards()
        paintModals()
      }
    }
    if (act === "st-rail-select") {
      const u = t.getAttribute("data-url")
      if (u) {
        s.imageUrl = String((s.selectedRevisionByBase && s.selectedRevisionByBase[u]) || u || "").trim()
        s.selectedMediaId = s.imageUrl
        paintModals()
      }
    }
    if (act === "st-rev-prev" || act === "st-rev-next") {
      const base = findRevisionBase(s.imageUrl)
      if (base) {
        const list = ((s.revisionMap && s.revisionMap[base]) || []).map((u) => String(u || "").trim()).filter(Boolean)
        if (list.length > 1) {
          const cur = list.indexOf((s.imageUrl || "").trim())
          const idx = cur >= 0 ? cur : 0
          const nextIdx = act === "st-rev-prev" ? Math.max(0, idx - 1) : Math.min(list.length - 1, idx + 1)
          if (nextIdx !== idx) {
            s.imageUrl = list[nextIdx]
            s.selectedRevisionByBase = {
              ...(s.selectedRevisionByBase || {}),
              [base]: String(s.imageUrl || "").trim(),
            }
            paintModals()
          }
        }
      }
    }
    if (act === "st-rail-remove") {
      const u = t.getAttribute("data-url")
      if (u) {
        removeRailAsset(u)
        if (s.editingPostId) persistEditingPostMediaState()
        else persistActiveDraftQuiet()
        paintModals()
        paintDayPanel()
      }
    }
    if (act === "st-rev-remove") {
      const u = String(t.getAttribute("data-url") || "").trim()
      if (u) {
        /** Revize slider'dan tek bir variant'ı sil; base ve diğer revize variantları kalsın. */
        const base = findRevisionBase(u)
        if (base) {
          const revMap = s.revisionMap && typeof s.revisionMap === "object" ? s.revisionMap : {}
          const list = (Array.isArray(revMap[base]) ? revMap[base] : []).map((x) => String(x || "").trim()).filter(Boolean)
          const next = list.filter((x) => x !== u)
          const nextMap = { ...revMap }
          if (next.length <= 1) {
            /** Sadece base kaldı (veya hiç) — chain'i tamamen kaldır. */
            delete nextMap[base]
          } else {
            nextMap[base] = next
          }
          s.revisionMap = nextMap
          const sel = { ...(s.selectedRevisionByBase || {}) }
          if (String(sel[base] || "") === u) {
            sel[base] = next.find((x) => x !== base) || base
          }
          if (!nextMap[base]) delete sel[base]
          s.selectedRevisionByBase = sel
          /** Aktif görsel siliniyorsa kalan ilk variant'a (yoksa base'e) geç. */
          if ((s.imageUrl || "").trim() === u) {
            s.imageUrl = String(sel[base] || base || "").trim()
          }
          if (s.editingPostId) persistEditingPostMediaState()
          else persistActiveDraftQuiet()
          paintModals()
          paintDayPanel()
        }
      }
    }
  }

}
