import { apiRequest, authHeaders, cfg, esc, T } from "./social-media-api.js"
import { CAMPAIGN_MODE } from "./social-media-campaign-utils.js"
import { DEFAULT_CAMPAIGN_API_BASE_URL } from "./social-media-constants.js"
import { getHolidayLabelsSync } from "./social-media-holidays.js"
import { parseScheduledLocalDateTime } from "./social-media-post-utils.js"
import { rootEl, s } from "./social-media-state.js"
import { buildStudioModalHtml } from "./social-media-studio-modal.js"

export function createModalHelpers({
  activeAccount,
  activeCampaign,
  activeCampaignStore,
  composerModalScrollAreaClass,
  ensureCampaignMediaBoundSync,
  findCampaignBySelection,
  findRevisionBase,
  graphPublishCardKey,
  looksLikeVideoUrl,
  normalizeCampaignStudioState,
  normalizeStudioPanel,
  publishCapsStudio,
  studioRailHasVideo,
  syncAssetOrderFromCollections,
} = {}) {
  /** `paintModals` innerHTML öncesi: açık formlardaki yazıları `s` içine al (periyodik boyama silmesin). */
  function syncOpenModalsFromDom() {
    const host = rootEl && rootEl.querySelector("#sm-modals")
    if (!host) return
    if (s.studioOpen) {
      const cap = host.querySelector("#st-cap")
      const flowIn = host.querySelector("#st-flow-topic")
      const topic = host.querySelector("#st-topic")
      const pr = host.querySelector("#st-prompt")
      const dirPr = host.querySelector("#st-direct-prompt")
      const img = host.querySelector("#st-img")
      const dEl = host.querySelector("#st-date")
      const tEl = host.querySelector("#st-time")
      const cStore = host.querySelector("#st-campaign-store")
      const cId = host.querySelector("#st-campaign-id")
      const cStart = host.querySelector("#st-campaign-start")
      const cEnd = host.querySelector("#st-campaign-end")
      const capRev = host.querySelector("#st-cap-rev")
      const revFb = host.querySelector("#st-revise-fb")
      const vcnt = host.querySelector("#st-var-count")
      const hvn = host.querySelector("#st-holiday-video-name")
      const hvd = host.querySelector("#st-holiday-video-date")
      const vdur = host.querySelector("#st-video-dur")
      if (cap instanceof HTMLTextAreaElement) s.caption = cap.value
      if (flowIn instanceof HTMLInputElement) s.lastTopic = flowIn.value
      else if (topic instanceof HTMLTextAreaElement) s.lastTopic = topic.value
      if (pr instanceof HTMLTextAreaElement) s.prompt = pr.value
      if (dirPr instanceof HTMLTextAreaElement) {
        s.directImagePrompt = dirPr.value
        s.prompt = dirPr.value
      }
      if (img instanceof HTMLInputElement) s.imageUrl = img.value
      if (dEl instanceof HTMLInputElement) s.selectedDate = dEl.value || s.selectedDate
      if (tEl instanceof HTMLInputElement) s.scheduledTime = tEl.value || s.scheduledTime
      if (cStore instanceof HTMLSelectElement) s.campaignStoreId = cStore.value || s.campaignStoreId
      if (cId instanceof HTMLSelectElement) s.campaignId = cId.value || s.campaignId
      if (cStart instanceof HTMLInputElement) s.campaignStartDate = cStart.value || s.campaignStartDate
      if (cEnd instanceof HTMLInputElement) s.campaignEndDate = cEnd.value || s.campaignEndDate
      if (capRev instanceof HTMLTextAreaElement) s.captionReviseFeedback = capRev.value
      if (revFb instanceof HTMLTextAreaElement) s.reviseFeedback = revFb.value
      if (hvn instanceof HTMLInputElement) s.holidayVideoName = hvn.value
      if (hvd instanceof HTMLInputElement) s.holidayVideoDate = hvd.value || s.holidayVideoDate
      if (vdur instanceof HTMLInputElement) {
        const n = Number(vdur.value)
        if (Number.isFinite(n)) s.videoDurationSec = Math.min(15, Math.max(3, n))
      }
      if (vcnt instanceof HTMLSelectElement) {
        const n = Number(vcnt.value)
        s.imageVariantCount = Number.isFinite(n) ? Math.min(4, Math.max(1, n)) : 1
      }
      const ptIg = host.querySelector("#pt-ig")
      const ptSt = host.querySelector("#pt-st")
      const ptFb = host.querySelector("#pt-fb")
      if (ptIg instanceof HTMLInputElement && ptSt instanceof HTMLInputElement && ptFb instanceof HTMLInputElement) {
        s.publishTargets = {
          instagramPost: ptIg.checked,
          instagramStory: ptSt.checked,
          facebookPost: ptFb.checked,
        }
      }
    }
    if (s.holidaySettings) {
      const ta = host.querySelector("#hs-ta")
      if (ta instanceof HTMLTextAreaElement && s.holidaySettings) s.holidaySettings.instructions = ta.value
    }
    if (s.accountModal) {
      const n = host.querySelector("#acc-name")
      const tok = host.querySelector("#acc-token")
      const kind = host.querySelector("#acc-campaign-kind")
      if (n instanceof HTMLInputElement) s.accName = n.value
      if (tok instanceof HTMLInputElement) s.accToken = tok.value
      const base = rootEl.querySelector("#acc-campaign-base")
      if (base instanceof HTMLInputElement) s.accCampaignBaseUrl = base.value
      if (kind instanceof HTMLSelectElement) s.accCampaignKind = kind.value === "restaurant" ? "restaurant" : "store"
      host.querySelectorAll(".linked-label").forEach((el) => {
        if (!(el instanceof HTMLInputElement)) return
        const igid = el.getAttribute("data-igid")
        const row = s.linkedRows.find((r) => r.instagramUserId === igid)
        if (row) row.draftLabel = el.value
      })
    }
    if (s.ticketModal) {
      const n = host.querySelector("#tk-name")
      const d = host.querySelector("#tk-desc")
      if (n instanceof HTMLInputElement) s.ticketDraft.name = n.value
      if (d instanceof HTMLTextAreaElement) s.ticketDraft.description = d.value
    }
    if (s.templateModal) {
      const title = host.querySelector("#tpl-title")
      const prompt = host.querySelector("#tpl-prompt")
      if (title instanceof HTMLInputElement) s.templateDraft.title = title.value
      if (prompt instanceof HTMLTextAreaElement) s.templateDraft.prompt = prompt.value
    }
  }

  async function maybeFetchGraphPublishCards() {
    if (CAMPAIGN_MODE) {
      s.graphPublishCards = null
      s.selectedGraphPublishKey = null
      s.graphPublishError = ""
      return
    }
    if (!s.studioOpen) return
    const acc = activeAccount()
    const tok = (acc?.instagramAccessToken || "").trim()
    if (!tok) {
      s.graphPublishCards = null
      s.selectedGraphPublishKey = null
      s.graphPublishError = ""
      return
    }
    s.graphPublishLoading = true
    s.graphPublishError = ""
    paintModals()
    try {
      const data = await apiRequest("/social-media/instagram/graph-destinations", {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({ access_token: tok }),
      })
      const raw = data.cards
      const cards = []
      if (Array.isArray(raw)) {
        for (const row of raw) {
          if (!row || typeof row !== "object") continue
          const r = row
          const kind = r.kind
          const tasks = Array.isArray(r.tasks) ? r.tasks.map((x) => String(x ?? "").trim()).filter(Boolean) : undefined
          const pic = typeof r.picture_url === "string" ? r.picture_url.trim() : ""
          if (kind === "facebook") {
            const page_id = String(r.page_id ?? "").trim()
            const name = String(r.name ?? "").trim()
            if (!page_id) continue
            cards.push({
              kind: "facebook",
              page_id,
              name: name || page_id,
              ...(pic ? { picture_url: pic } : {}),
              ...(tasks?.length ? { tasks } : {}),
            })
          } else if (kind === "instagram") {
            const ig_user_id = String(r.ig_user_id ?? "").trim()
            const username = String(r.username ?? "").trim()
            const page_id = String(r.page_id ?? "").trim()
            if (!ig_user_id) continue
            cards.push({
              kind: "instagram",
              ig_user_id,
              username,
              page_id,
              ...(pic ? { picture_url: pic } : {}),
              ...(tasks?.length ? { tasks } : {}),
            })
          }
        }
      }
      s.graphPublishCards = cards
      if (cards.length && !s.selectedGraphPublishKey) s.selectedGraphPublishKey = graphPublishCardKey(cards[0])
      if (!cards.length) s.selectedGraphPublishKey = null
    } catch (e) {
      s.graphPublishCards = null
      s.selectedGraphPublishKey = null
      s.graphPublishError = e instanceof Error ? e.message : "graph"
    } finally {
      s.graphPublishLoading = false
      paintModals()
    }
  }

  /** @param {boolean} [skipDomSync] API sonrası gibi durumlarda: önce `syncOpenModalsFromDom` + state, sonra `paintModals(true)` — aksi halde eski textarea DOM'dan tekrar `s`'e yazılır. */
  function paintModals(skipDomSync = false) {
    const host = rootEl && rootEl.querySelector("#sm-modals")
    if (!host) return
    if (!skipDomSync) syncOpenModalsFromDom()
    let html = ""
    if (s.contextAccountId) {
      html += `<div class="fixed z-50 w-44 rounded-2xl border border-neutral-200 bg-white p-2 shadow-xl" style="left:${s.contextX}px;top:${s.contextY}px" data-stop="1">
<button type="button" data-act="ctx-del-acc" class="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-red-600 transition hover:bg-red-50">${esc(T("deleteAccount"))}</button>
</div>`
    }
    if (s.dayMenu) {
      const dk = s.dayMenu.dateKey
      const dt = parseScheduledLocalDateTime(dk, "12:00")
      const isHol = dt ? getHolidayLabelsSync(dt, cfg().uiLocale || "tr").length > 0 : false
      html += `<div class="fixed z-50 w-56 rounded-2xl border border-neutral-200 bg-white p-2 shadow-xl" style="left:${s.dayMenu.x}px;top:${s.dayMenu.y}px" data-stop="1">
${CAMPAIGN_MODE
  ? `<button type="button" data-act="ctx-manual" data-dk="${esc(dk)}" class="flex w-full rounded-xl px-3 py-2 text-left text-sm text-neutral-800 transition hover:bg-neutral-50">Kampanya oluştur</button>`
  : `<button type="button" data-act="ctx-ig-post" data-dk="${esc(dk)}" class="flex w-full rounded-xl px-3 py-2 text-left text-sm text-neutral-800 transition hover:bg-neutral-50">📷 Instagram Post Oluştur</button>
<button type="button" data-act="ctx-ig-story" data-dk="${esc(dk)}" class="mt-1 flex w-full rounded-xl px-3 py-2 text-left text-sm text-neutral-800 transition hover:bg-neutral-50">🟪 Instagram Hikaye Oluştur</button>`}
${
  isHol
    ? `<button type="button" data-act="ctx-holiday-draft" data-dk="${esc(dk)}" class="mt-1 flex w-full rounded-xl px-3 py-2 text-left text-sm text-amber-900 transition hover:bg-amber-50">${esc(CAMPAIGN_MODE ? "Özel gün kampanyası oluştur" : T("contextHolidayDraft"))}</button>
<button type="button" data-act="ctx-holiday-settings" data-dk="${esc(dk)}" class="mt-1 flex w-full rounded-xl px-3 py-2 text-left text-sm text-violet-900 transition hover:bg-violet-50">${esc(T("contextHolidaySettings"))}</button>`
    : ""
}
</div>`
    }
    if (s.holidaySettings) {
      const hs = s.holidaySettings
      html += `<div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" data-act="close-hol-set-bg">
<div class="w-full max-w-lg rounded-2xl border border-neutral-200 bg-white p-5 shadow-xl" data-stop="1" role="dialog">
<div class="flex flex-wrap items-center justify-between gap-3">
<span class="text-sm font-medium text-neutral-900">${esc(T("holidaySettingsRenewToggle"))}</span>
<button type="button" data-act="hs-renew" class="relative inline-flex h-8 w-14 shrink-0 cursor-pointer items-center rounded-full transition ${hs.renewYearly ? "bg-violet-600" : "bg-neutral-300"}">
<span class="inline-block h-7 w-7 ${hs.renewYearly ? "translate-x-7" : "translate-x-1"} transform rounded-full bg-white shadow"></span>
</button></div>
<h2 class="mt-5 text-lg font-semibold text-neutral-900">${esc(hs.holidayName)}</h2>
<p class="mt-1 text-xs text-neutral-500">${esc(hs.dateLine)}</p>
<label class="mt-6 block text-sm font-medium text-neutral-800">${esc(T("holidaySettingsInstructionsLabel"))}</label>
<p class="mt-1 text-xs text-neutral-500">${esc(T("holidaySettingsInstructionsHint"))}</p>
<textarea id="hs-ta" rows="5" class="mt-2 max-h-[40vh] w-full resize-y rounded-xl border border-neutral-200 px-3 py-2 text-sm text-neutral-900">${esc(hs.instructions)}</textarea>
<div class="mt-6 flex flex-wrap justify-end gap-2 border-t border-neutral-100 pt-4">
<button type="button" data-act="hs-cancel" class="rounded-xl border border-neutral-200 px-4 py-2 text-sm">${esc(T("holidaySettingsCancel"))}</button>
<button type="button" data-act="hs-save" class="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white">${esc(T("holidaySettingsSave"))}</button>
</div></div></div>`
    }
    if (s.accountModal) {
      const accountHeader = CAMPAIGN_MODE ? "Kampanya Hesabı" : esc(T("accountManagementHeader"))
      const accountTitle = CAMPAIGN_MODE
        ? (s.editingAccountId ? "Kampanya Hesabını Düzenle" : "Kampanya Hesabı Ekle")
        : esc(s.editingAccountId ? T("accountModalEditTitle") : T("accountModalNewTitle"))
      const campaignAccountFields = CAMPAIGN_MODE
        ? `<select id="acc-campaign-kind" class="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm">
<option value="store" ${s.accCampaignKind === "restaurant" ? "" : "selected"}>Mağaza</option>
<option value="restaurant" ${s.accCampaignKind === "restaurant" ? "selected" : ""}>Restoran</option>
</select>
<label class="block text-[11px] font-medium text-neutral-500">Campaign API Base URL</label>
<input id="acc-campaign-base" value="${esc(s.accCampaignBaseUrl || DEFAULT_CAMPAIGN_API_BASE_URL)}" class="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm font-mono text-[13px]" placeholder="${esc(DEFAULT_CAMPAIGN_API_BASE_URL)}"/>
<label class="mt-1 block text-[11px] font-medium text-neutral-500">Campaign API Key (Bearer)</label>
<input id="acc-token" value="${esc(s.accToken)}" class="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm font-mono text-[13px]" placeholder="aio_…"/>
<p class="text-[11px] leading-relaxed text-neutral-500">Canlı Sepetler API: <span class="font-mono">/api/ai/v1</span> + Bearer token. Katalog <span class="font-mono">/resources/stores</span> ve <span class="font-mono">/resources/campaigns</span> uçlarını kullanır; banner yayını <span class="font-mono">POST /banners</span> ile yapılır.</p>`
        : `<input id="acc-token" value="${esc(s.accToken)}" class="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm" placeholder="${esc(T("phInstagramAccessToken"))}"/>
<p class="text-[11px] text-neutral-500">${esc(T("instagramTokenMetaHelp"))}</p>`
      const linkedAccountBlock =
        !CAMPAIGN_MODE && !s.editingAccountId
          ? `<div class="rounded-2xl border border-neutral-200 bg-neutral-50/80 p-4">
<p class="text-xs font-medium">${esc(T("linkedIgSectionTitle"))}</p>
<p class="mt-1 text-[11px] text-neutral-500">${esc(T("linkedIgSectionHelp"))}</p>
<button type="button" data-act="linked-load" class="mt-3 rounded-xl border bg-white px-3 py-2 text-xs" ${s.linkedLoading ? "disabled" : ""}>${s.linkedLoading ? "…" : esc(T("linkedIgListButton"))}</button>
${s.linkedErr ? `<p class="mt-2 text-xs text-red-600">${esc(s.linkedErr)}</p>` : ""}
<div id="linked-rows" class="mt-3 max-h-52 space-y-2 overflow-y-auto"></div>
<button type="button" data-act="linked-add" id="linked-add-btn" class="mt-3 w-full rounded-xl bg-violet-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">${esc(T("linkedIgAddSelected"))}</button>
</div>`
          : ""
      const logoBlock = CAMPAIGN_MODE
        ? ""
        : `<div class="rounded-2xl border border-neutral-200 bg-neutral-50 p-4">
<p class="text-xs font-medium">${esc(T("accountLogoLabel"))}</p>
<div class="mt-3 flex flex-wrap items-center gap-3">
<div class="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-full border-2 border-neutral-200 bg-white">
${s.accLogo.trim() ? `<img src="${esc(s.accLogo)}" alt="" class="h-full w-full object-cover"/>` : "—"}</div>
<div><input id="acc-logo-file" type="file" accept="image/*" class="hidden"/>
<button type="button" data-act="acc-logo-pick" class="rounded-xl border bg-white px-3 py-2 text-xs">${esc(T("accountLogoPick"))}</button>
${s.accLogo.trim() ? `<button type="button" data-act="acc-logo-clear" class="ml-2 text-xs text-red-600">${esc(T("accountLogoRemove"))}</button>` : ""}
</div></div></div>`
      html += `<div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/30 p-4" data-act="close-acc-bg">
<div class="w-full max-w-xl rounded-[32px] bg-white p-6 shadow-2xl" data-stop="1">
<div class="flex items-start justify-between gap-4">
<div><p class="text-[11px] font-semibold uppercase tracking-[0.24em] text-neutral-400">${accountHeader}</p>
<h3 class="mt-2 text-2xl font-semibold">${accountTitle}</h3></div>
<button type="button" data-act="close-acc" class="rounded-full border border-neutral-200 p-2 text-neutral-500">✕</button>
</div>
<div class="mt-6 grid gap-4">
<input id="acc-name" value="${esc(s.accName)}" class="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm" placeholder="${esc(T("phAccountName"))}"/>
${campaignAccountFields}
${linkedAccountBlock}
${logoBlock}
<div class="mt-6 flex justify-end gap-3">
<button type="button" data-act="close-acc" class="rounded-2xl border px-4 py-3 text-sm">${esc(T("genericCancel"))}</button>
<button type="button" data-act="acc-save" class="rounded-2xl bg-neutral-900 px-4 py-3 text-sm font-medium text-white">${esc(T("genericSave"))}</button>
</div></div></div>`
    }
    if (s.studioOpen) {
      html += buildStudioModalHtml({
        activeAccount,
        activeCampaign,
        activeCampaignStore,
        composerModalScrollAreaClass,
        ensureCampaignMediaBoundSync,
        findCampaignBySelection,
        findRevisionBase,
        graphPublishCardKey,
        looksLikeVideoUrl,
        normalizeCampaignStudioState,
        normalizeStudioPanel,
        publishCapsStudio,
        studioRailHasVideo,
        syncAssetOrderFromCollections,
      })
    }
    if (s.ticketModal) {
      html += `<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" data-act="close-ticket-bg">
<div class="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl" data-stop="1">
<div class="mb-4 flex justify-between"><h2 class="font-semibold">🏷 Etiketler</h2><button type="button" data-act="close-ticket">✕</button></div>
<input id="tk-name" class="mb-3 w-full rounded-xl border px-3 py-2 text-sm" value="${esc(s.ticketDraft.name)}" placeholder="Ad"/>
<textarea id="tk-desc" rows="5" class="mb-3 w-full rounded-xl border px-3 py-2 text-sm">${esc(s.ticketDraft.description)}</textarea>
<button type="button" data-act="tk-save" class="w-full rounded-xl bg-neutral-900 py-2 text-sm text-white">Kaydet</button>
<div id="tk-list" class="mt-4 grid max-h-64 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3"></div>
</div></div>`
    }
    if (s.templateModal) {
      const layoutU = String((s.templateDraft.imageUrls || [])[0] || "").trim()
      const logoU = String((s.templateDraft.imageUrls || [])[1] || "").trim()
      const busy = s.templateUploading ? "opacity-50" : ""
      const dis = s.templateUploading ? "disabled" : ""
      const templateLayoutLogoBlock = `
<div class="mb-3 space-y-3">
<div class="rounded-2xl border border-neutral-200 bg-neutral-50/80 p-3 shadow-sm">
<p class="text-xs font-semibold text-neutral-900">${esc(T("tplCampaignLayoutTitle"))}</p>
<input id="tpl-file-layout" type="file" accept="image/*" class="hidden"/>
<div data-act="tpl-drop-layout" class="sm-premium-upload-drop ${busy}">
${layoutU ? `<div class="group relative"><img src="${esc(layoutU)}" alt="" class="mx-auto max-h-32 w-full rounded-xl border border-slate-200 object-contain"/>
<button type="button" data-act="tpl-remove-layout" class="absolute right-1.5 top-1.5 rounded-lg bg-black/65 px-2 py-0.5 text-[10px] font-semibold text-white opacity-0 transition group-hover:opacity-100">${esc(T("tplCampaignRemoveLayout"))}</button></div>` : `<div class="sm-premium-upload-drop__icon">↑</div><p class="sm-premium-upload-drop__label">${esc(T("tplCampaignPickLayout"))}</p><p class="sm-premium-upload-drop__hint">${esc(T("tplCampaignLayoutHelp"))}</p>`}
${layoutU ? `<button type="button" data-act="tpl-pick-layout" class="sm-premium-btn sm-premium-btn--ghost mt-2 w-full" style="padding:0.45rem;font-size:0.75rem" ${dis}>${esc(T("tplCampaignPickLayout"))}</button>` : ""}
</div>
</div>
<div>
<p class="text-[11px] font-semibold text-slate-700 mb-1">${esc(T("tplCampaignLogoTitle"))}</p>
<input id="tpl-file-logo" type="file" accept="image/*" class="hidden"/>
<div data-act="tpl-drop-logo" class="sm-premium-upload-drop ${busy}" style="background:linear-gradient(180deg,#faf5ff 0%,#fff 100%)">
${logoU ? `<div class="group relative mx-auto max-w-[140px]"><img src="${esc(logoU)}" alt="" class="mx-auto max-h-24 w-auto rounded-xl border border-violet-100 object-contain"/>
<button type="button" data-act="tpl-remove-logo" class="absolute right-1 top-1 rounded-lg bg-black/65 px-2 py-0.5 text-[10px] font-semibold text-white opacity-0 transition group-hover:opacity-100">${esc(T("tplCampaignRemoveLogo"))}</button></div>` : `<div class="sm-premium-upload-drop__icon">◇</div><p class="sm-premium-upload-drop__label">${esc(T("tplCampaignPickLogo"))}</p><p class="sm-premium-upload-drop__hint">${esc(T("tplCampaignLogoHelp"))}</p>`}
${!logoU ? `<button type="button" data-act="tpl-pick-logo" class="sm-premium-btn sm-premium-btn--ghost mt-2 w-full" style="padding:0.45rem;font-size:0.75rem" ${dis} ${layoutU ? "" : "disabled"}>${esc(T("tplCampaignPickLogo"))}</button>` : `<button type="button" data-act="tpl-pick-logo" class="sm-premium-btn sm-premium-btn--ghost mt-2 w-full" style="padding:0.45rem;font-size:0.75rem" ${dis}>Değiştir</button>`}
</div>
</div>
</div>`
      const extraRefs = (s.templateDraft.imageUrls || []).slice(2).map((u) => String(u || "").trim()).filter(Boolean)
      const extraRefsBlock =
        extraRefs.length > 0
          ? `<div class="mb-3 rounded-2xl border border-neutral-200 bg-white p-3 shadow-sm">
<p class="text-xs font-semibold text-neutral-800">Ek sablon katmanlari</p>
<p class="mt-0.5 text-[11px] text-neutral-500">imageUrls[2] ve sonrasi — revize referans sirasinda kullanilir.</p>
<div class="mt-2 grid grid-cols-2 gap-2">${extraRefs
              .map(
                (u, j) => `<div class="group relative overflow-hidden rounded-xl border border-neutral-200 bg-white">
<img src="${esc(u)}" alt="" class="h-20 w-full object-cover"/>
<button type="button" data-act="tpl-remove-img" data-index="${j + 2}" class="absolute right-1.5 top-1.5 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold text-white opacity-0 transition group-hover:opacity-100">Sil</button>
</div>`,
              )
              .join("")}</div>
</div>`
          : ""
      const visualSection = `${templateLayoutLogoBlock}${extraRefsBlock}`
      const tplOutSize = s.templateDraft.outputSize || "post_4_5"
      const tplSizePicker = CAMPAIGN_MODE
        ? ""
        : `<div class="sm-premium-field">
<label>Çıktı boyutu</label>
<select id="tpl-output-size" class="sm-premium-hidden-select" aria-hidden="true" tabindex="-1">
<option value="square" ${tplOutSize === "square" ? "selected" : ""}>square</option>
<option value="post_4_5" ${tplOutSize === "post_4_5" ? "selected" : ""}>post_4_5</option>
<option value="story" ${tplOutSize === "story" ? "selected" : ""}>story</option>
</select>
<div class="sm-premium-size-grid">
<button type="button" data-act="tpl-set-size" data-size="post_4_5" class="sm-premium-size-card${tplOutSize === "post_4_5" ? " is-active" : ""}"><div class="sm-premium-size-card__icon">▭</div><div class="sm-premium-size-card__label">Gönderi (4:5)</div><div class="sm-premium-size-card__dim">1088×1360</div></button>
<button type="button" data-act="tpl-set-size" data-size="square" class="sm-premium-size-card${tplOutSize === "square" ? " is-active" : ""}"><div class="sm-premium-size-card__icon">□</div><div class="sm-premium-size-card__label">Kare (1:1)</div><div class="sm-premium-size-card__dim">1024×1024</div></button>
<button type="button" data-act="tpl-set-size" data-size="story" class="sm-premium-size-card${tplOutSize === "story" ? " is-active" : ""}"><div class="sm-premium-size-card__icon">▯</div><div class="sm-premium-size-card__label">Hikaye (9:16)</div><div class="sm-premium-size-card__dim">1088×1920</div></button>
</div>
</div>`
      html += `<div class="sm-premium-modal-root" data-act="close-tpl-bg">
<div class="sm-premium-modal sm-premium-modal--editor" data-stop="1" role="dialog" aria-modal="true">
<header class="sm-premium-modal__header">
  <div><h2 class="sm-premium-modal__title">${esc(T("templatesManageTitle"))}</h2><p class="sm-premium-modal__desc">${esc(T("templatesManageIntro"))}</p></div>
  <button type="button" class="sm-premium-modal__close" data-act="close-tpl" aria-label="Kapat">✕</button>
</header>
<div class="sm-premium-modal__body">
<div class="sm-premium-modal__split sm-premium-modal__split--balanced">
<aside class="sm-premium-modal__preview-col">
<span class="sm-premium-kicker">Şablon önizlemesi</span>
${visualSection}
<ul id="tpl-list" class="sm-premium-modal__tpl-list"></ul>
</aside>
<div class="sm-premium-modal__form-col">
<div class="sm-premium-field"><label for="tpl-title">Şablon adı</label><input id="tpl-title" class="sm-premium-input" value="${esc(s.templateDraft.title)}"/></div>
<div class="sm-premium-field"><label for="tpl-prompt">AI talimatı / revize metni</label>
<div class="sm-premium-editor">
<div class="sm-premium-editor__toolbar">
<div class="sm-premium-editor__tools"><span>B</span><span>I</span><span>U</span><span>≡</span><span>•</span><span>🔗</span></div>
<span class="sm-premium-editor__ai">✦ AI</span>
</div>
<textarea id="tpl-prompt" class="sm-premium-editor__area" rows="7">${esc(s.templateDraft.prompt)}</textarea>
</div>
</div>
${tplSizePicker}
</div></div></div>
<footer class="sm-premium-modal__footer"><button type="button" class="sm-premium-btn sm-premium-btn--ghost" data-act="close-tpl">${esc(T("genericCancel"))}</button><button type="button" data-act="tpl-save" class="sm-premium-btn sm-premium-btn--primary">${esc(T("genericSave"))}</button></footer>
</div></div>`
    }
    host.innerHTML = html
    if (s.accountModal && !s.editingAccountId && s.linkedRows.length) {
      const lr = host.querySelector("#linked-rows")
      const addBtn = host.querySelector("#linked-add-btn")
      if (lr) {
        lr.innerHTML = s.linkedRows
          .map(
            (row) => `
<label class="flex cursor-pointer gap-2 rounded-xl border bg-white p-2 text-xs">
<input type="checkbox" class="linked-cb mt-0.5" data-igid="${esc(row.instagramUserId)}" ${s.linkedSelected.has(row.instagramUserId) ? "checked" : ""}/>
<span class="min-w-0 flex-1"><span class="font-medium">${esc(row.facebookPageName)}</span>
<input type="text" class="linked-label mt-1 w-full rounded border px-2 py-1 text-[11px]" data-igid="${esc(row.instagramUserId)}" value="${esc(row.draftLabel)}"/>
</span></label>`,
          )
          .join("")
      }
      if (addBtn) addBtn.disabled = s.linkedSelected.size === 0
    }
    if (s.ticketModal) {
      const list = host.querySelector("#tk-list")
      if (list) {
        list.innerHTML =
          s.tickets.length === 0
            ? `<div class="col-span-full rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-6 text-center">
<p class="text-sm font-medium text-neutral-800">Henuz etiket yok</p>
<p class="mt-1 text-xs text-neutral-500">Ilk etiketini olustur ve revize akisini hizlandir.</p>
</div>`
            : s.tickets
                .map(
                  (tk) => `<div class="rounded-xl border border-neutral-200 bg-white px-2 py-2 text-sm flex justify-between gap-1 transition hover:-translate-y-0.5 hover:shadow-sm">
<span class="truncate">${esc(tk.name)}</span>
<span><button type="button" data-act="tk-edit" data-tid="${esc(tk.id)}" class="text-xs">✎</button>
<button type="button" data-act="tk-del" data-tid="${esc(tk.id)}" class="text-xs text-red-600">🗑</button></span></div>`,
                )
                .join("")
      }
    }
    if (s.templateModal) {
      const list = host.querySelector("#tpl-list")
      if (list) {
        const renderRow = (tpl) => `<li class="flex items-center justify-between gap-2 rounded-xl border border-neutral-200 bg-white px-3 py-2 transition hover:-translate-y-0.5 hover:shadow-sm">
<span class="truncate text-sm text-neutral-800">${esc(tpl.title)}</span>
<span><button type="button" data-act="tpl-edit" data-id="${esc(tpl.id)}" class="text-xs text-violet-700">Düzenle</button>
<button type="button" data-act="tpl-del" data-id="${esc(tpl.id)}" class="text-xs text-red-600">Sil</button></span></li>`
        if (CAMPAIGN_MODE) {
          /** Kampanya modu: tüm şablonlar tek listede (banner şablonu, post/story ayrımı yok). */
          list.innerHTML = s.userTemplates.length
            ? s.userTemplates.map(renderRow).join("")
            : `<li class="rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-5 text-center">
<p class="text-sm font-medium text-neutral-800">Henüz şablon oluşturulmadı</p>
<p class="mt-1 text-xs text-neutral-500">İlk AI revize şablonunu oluştur.</p>
</li>`
        } else {
          const postTpls = s.userTemplates.filter((t) => String(t.outputSize || "post_4_5") !== "story")
          const storyTpls = s.userTemplates.filter((t) => String(t.outputSize || "") === "story")
          const emptyPost = `<li class="rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-3 text-center text-xs text-neutral-500">Henüz post şablonu yok.</li>`
          const emptyStory = `<li class="rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-4 py-3 text-center text-xs text-neutral-500">Henüz hikaye şablonu yok.</li>`
          list.innerHTML = `
<li class="mb-1 mt-1 text-[11px] font-bold uppercase tracking-wider text-neutral-500">📷 Post Şablonları (1:1 / 4:5)</li>
${postTpls.length ? postTpls.map(renderRow).join("") : emptyPost}
<li class="mb-1 mt-4 text-[11px] font-bold uppercase tracking-wider text-neutral-500">🟪 Hikaye Şablonları (9:16)</li>
${storyTpls.length ? storyTpls.map(renderRow).join("") : emptyStory}
`
        }
      }
    }
  }

  return {
    maybeFetchGraphPublishCards,
    paintModals,
    syncOpenModalsFromDom,
  }
}
