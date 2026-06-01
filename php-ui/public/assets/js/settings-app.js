/**
 * Ayarlar — React `src/pages/Settings.tsx` + `HolidayYearlySlideToggle` ile ayni localStorage anahtarlari.
 */
const OPENAI_STORAGE_KEY = "app_settings_openai_api_key"
const FAL_STORAGE_KEY = "app_settings_fal_api_key"
const PROMPT_PRO_THRESHOLD_KEY = "app_settings_prompt_professionalization_threshold"
const HOLIDAY_YEARLY_AUTO_KEY = "app_settings_holiday_yearly_auto"
const HOLIDAY_YEARLY_WATCHLIST_KEY = "app_settings_holiday_yearly_watchlist"
const CAMPAIGN_HOLIDAY_YEARLY_AUTO_KEY = "app_settings_campaign_holiday_yearly_auto"
const CAMPAIGN_HOLIDAY_YEARLY_WATCHLIST_KEY = "app_settings_campaign_holiday_yearly_watchlist"
const APP_SETTINGS_COLLECTION = "app_settings"
const APP_SETTINGS_DOC_ID = "api_keys"
const AUTOMATION_RULES_COLLECTION = "automation_rules"

function T(key) {
  const loc = window.__AGENTBASE__?.uiLocale || "tr"
  const row = window.__UI_STRINGS__?.[loc]?.[key]
  return row != null ? String(row) : key
}

function lsGet(k) {
  try {
    return localStorage.getItem(k) || ""
  } catch {
    return ""
  }
}

function lsSet(k, v) {
  try {
    if (v) localStorage.setItem(k, v)
    else localStorage.removeItem(k)
  } catch {
    /* ignore */
  }
}

function authHeaders(withJson = false) {
  const h = {}
  if (withJson) h["Content-Type"] = "application/json"
  const tok = window.__AGENTBASE__?.accessToken || ""
  if (tok) h.Authorization = "Bearer " + tok
  return h
}

async function apiRequest(path, options = {}) {
  const base = (window.__AGENTBASE__?.apiBase || "").replace(/\/+$/, "")
  const url = base + path
  const res = await fetch(url, options)
  const txt = await res.text()
  let json = null
  try {
    json = txt ? JSON.parse(txt) : null
  } catch {
    json = null
  }
  if (!res.ok) {
    const msg = (json && (json.detail || json.error)) || txt || `HTTP ${res.status}`
    throw new Error(String(msg))
  }
  return json
}

function socialList(collection) {
  return apiRequest("/social-data/collections/" + encodeURIComponent(collection), { headers: authHeaders(false) })
}

function socialPut(collection, id, body, merge = false) {
  const qs = merge ? "?merge=true" : ""
  return apiRequest(`/social-data/collections/${encodeURIComponent(collection)}/${encodeURIComponent(id)}${qs}`, {
    method: "PUT",
    headers: authHeaders(true),
    body: JSON.stringify(body),
  })
}

function socialDelete(collection, id) {
  return apiRequest(`/social-data/collections/${encodeURIComponent(collection)}/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(false),
  })
}

async function loadApiSettings() {
  const rows = await socialList(APP_SETTINGS_COLLECTION)
  const list = Array.isArray(rows) ? rows : []
  const row = list.find((x) => x && String(x.id || "") === APP_SETTINGS_DOC_ID) || list[0] || null
  return {
    openaiApiKey: row && typeof row.openaiApiKey === "string" ? row.openaiApiKey : "",
    falApiKey: row && typeof row.falApiKey === "string" ? row.falApiKey : "",
  }
}

async function saveApiSettings(openaiApiKey, falApiKey) {
  await socialPut(
    APP_SETTINGS_COLLECTION,
    APP_SETTINGS_DOC_ID,
    {
      openaiApiKey: String(openaiApiKey || "").trim(),
      falApiKey: String(falApiKey || "").trim(),
      updatedAt: new Date().toISOString(),
    },
    true,
  )
}

function holidayStorageKeys(scope = "social") {
  return scope === "campaign"
    ? { auto: CAMPAIGN_HOLIDAY_YEARLY_AUTO_KEY, watchlist: CAMPAIGN_HOLIDAY_YEARLY_WATCHLIST_KEY }
    : { auto: HOLIDAY_YEARLY_AUTO_KEY, watchlist: HOLIDAY_YEARLY_WATCHLIST_KEY }
}

function getHolidayYearlyAutoEnabled(scope = "social") {
  return lsGet(holidayStorageKeys(scope).auto) === "1"
}

function setHolidayYearlyAutoEnabled(v, scope = "social") {
  lsSet(holidayStorageKeys(scope).auto, v ? "1" : "")
}

function getWatchlist(scope = "social") {
  try {
    const raw = lsGet(holidayStorageKeys(scope).watchlist)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(Boolean)
  } catch {
    return []
  }
}

function setWatchlist(list, scope = "social") {
  lsSet(holidayStorageKeys(scope).watchlist, JSON.stringify(list))
}

function entryKey(e) {
  return `${e.month}-${e.day}-${e.holidayName}`
}

function upsertWatchEntry(entry, scope = "social") {
  const list = getWatchlist(scope)
  const k = entryKey(entry)
  const idx = list.findIndex((e) => entryKey(e) === k)
  if (idx >= 0) list[idx] = { ...list[idx], ...entry }
  else list.push({ ...entry })
  setWatchlist(list, scope)
}

function removeWatchEntry(entry, scope = "social") {
  const k = entryKey(entry)
  setWatchlist(getWatchlist(scope).filter((e) => entryKey(e) !== k), scope)
}

function syncMasterToggle(btn, on) {
  btn.setAttribute("aria-checked", on ? "true" : "false")
  btn.classList.toggle("bg-emerald-600", on)
  btn.classList.toggle("bg-gray-300", !on)
  const knob = btn.querySelector("span")
  if (knob) {
    knob.classList.toggle("translate-x-7", on)
    knob.classList.toggle("translate-x-1", !on)
  }
}

function renderHolidayGrid(container, scope = "social") {
  const list = getWatchlist(scope)
  container.innerHTML = ""
  if (list.length === 0) {
    const p = document.createElement("p")
    p.className =
      "rounded-xl border border-dashed border-gray-200 bg-white px-4 py-6 text-center text-sm text-gray-400 sm:col-span-2"
    p.textContent = T("settingsHolidayYearlyEmpty")
    container.appendChild(p)
    return
  }
  for (const entry of list) {
    const wrap = document.createElement("div")
    wrap.className =
      "flex items-start justify-between gap-2 rounded-xl border border-gray-200 bg-white px-3 py-3 text-sm text-gray-800"
    const left = document.createElement("div")
    left.className = "min-w-0"
    left.innerHTML = `<p class="font-medium text-gray-900"></p><p class="mt-1 text-xs text-gray-500"></p>`
    left.querySelector("p").textContent = entry.holidayName
    const sub = T("settingsHolidayYearlyCardSub")
      .replace("{d}", String(entry.day))
      .replace("{m}", String(entry.month))
    left.querySelector("p + p").textContent = sub
    const gpt = String(entry.gptExtraInstructions ?? "").trim()
    if (gpt) {
      const pi = document.createElement("p")
      pi.className = "mt-1 truncate text-[11px] text-gray-600"
      pi.title = gpt
      pi.textContent = gpt
      left.appendChild(pi)
    }
    if (entry.renewYearly === false) {
      const pau = document.createElement("p")
      pau.className = "mt-1 text-[11px] text-amber-800"
      pau.textContent = T("settingsHolidayYearlyRenewPaused")
      left.appendChild(pau)
    }
    const right = document.createElement("div")
    right.className = "flex shrink-0 flex-col items-end gap-1"
    right.innerHTML = `<button type="button" class="rounded-lg px-2 py-1 text-xs font-medium text-violet-700 hover:bg-violet-50">${T("settingsHolidayYearlyEdit")}</button>
      <button type="button" class="rounded-lg px-2 py-1 text-xs text-red-600 hover:bg-red-50">${T("settingsHolidayYearlyRemove")}</button>`
    right.querySelector("button").addEventListener("click", () => openEditor(entry, scope))
    right.querySelector("button:last-child").addEventListener("click", () => {
      removeWatchEntry(entry, scope)
      renderHolidayGrid(container, scope)
      window.dispatchEvent(new Event("app-holiday-settings"))
    })
    wrap.appendChild(left)
    wrap.appendChild(right)
    container.appendChild(wrap)
  }
}

function openEditor(entry, scope = "social") {
  const root = document.getElementById("st-holiday-editor")
  if (!root) return
  root.classList.remove("hidden")
  root.classList.add("flex")
  const y = new Date().getFullYear()
  const dt = new Date(y, entry.month - 1, entry.day)
  const loc = window.__AGENTBASE__?.uiLocale === "en" ? "en-US" : "tr-TR"
  const dateStr = Number.isNaN(dt.getTime())
    ? T("settingsHolidayYearlyCardSub").replace("{d}", String(entry.day)).replace("{m}", String(entry.month))
    : dt.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long", year: "numeric" })
  root.innerHTML = `
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation">
      <div role="dialog" aria-modal="true" class="w-full max-w-lg rounded-2xl border border-neutral-200 bg-white p-5 shadow-xl" data-stop="1">
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-4">
          <span class="max-w-[min(100%,16rem)] text-sm font-medium leading-snug text-neutral-900 sm:max-w-none">${T("holidaySettingsRenewToggle")}</span>
          <button type="button" id="ed-renew" role="switch" aria-checked="${entry.renewYearly !== false}" class="relative inline-flex h-8 w-14 shrink-0 cursor-pointer items-center rounded-full transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500 ${entry.renewYearly !== false ? "bg-violet-600" : "bg-neutral-300"}">
            <span class="inline-block h-7 w-7 ${entry.renewYearly !== false ? "translate-x-7" : "translate-x-1"} transform rounded-full bg-white shadow transition motion-reduce:transition-none"></span>
          </button>
        </div>
        <h2 class="mt-4 text-lg font-semibold leading-snug text-neutral-900">${escapeHtml(entry.holidayName)}</h2>
        <p class="mt-1 text-xs text-neutral-500">${escapeHtml(dateStr)}</p>
        <label class="mt-6 block text-sm font-medium text-neutral-800">${T("holidaySettingsInstructionsLabel")}</label>
        <p class="mt-1 text-xs text-neutral-500">${T("holidaySettingsInstructionsHint")}</p>
        <textarea id="ed-inst" rows="5" class="mt-2 max-h-[40vh] w-full resize-y rounded-xl border border-neutral-200 px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500" placeholder="${escapeAttr(T("holidaySettingsInstructionsPlaceholder"))}">${escapeHtml(String(entry.gptExtraInstructions || ""))}</textarea>
        <div class="mt-6 flex flex-wrap justify-end gap-2 border-t border-gray-100 pt-4">
          <button type="button" id="ed-cancel" class="rounded-xl border border-neutral-200 px-4 py-2 text-sm text-neutral-800 transition hover:bg-neutral-50">${T("holidaySettingsCancel")}</button>
          <button type="button" id="ed-save" class="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-700">${T("holidaySettingsSave")}</button>
        </div>
      </div>
    </div>`
  const dlg = root.querySelector("[data-stop]")
  root.firstElementChild.addEventListener("click", () => closeEditor())
  dlg.addEventListener("click", (e) => e.stopPropagation())
  let renew = entry.renewYearly !== false
  const renewBtn = root.querySelector("#ed-renew")
  renewBtn.addEventListener("click", () => {
    renew = !renew
    renewBtn.setAttribute("aria-checked", renew ? "true" : "false")
    renewBtn.classList.toggle("bg-violet-600", renew)
    renewBtn.classList.toggle("bg-neutral-300", !renew)
    const k = renewBtn.querySelector("span")
    k.classList.toggle("translate-x-7", renew)
    k.classList.toggle("translate-x-1", !renew)
  })
  root.querySelector("#ed-cancel").addEventListener("click", () => closeEditor())
  root.querySelector("#ed-save").addEventListener("click", () => {
    upsertWatchEntry({
      month: entry.month,
      day: entry.day,
      holidayName: entry.holidayName,
      renewYearly: renew,
      gptExtraInstructions: root.querySelector("#ed-inst").value.trim(),
    }, scope)
    window.dispatchEvent(new Event("app-holiday-settings"))
    closeEditor()
  })
}

function closeEditor() {
  const root = document.getElementById("st-holiday-editor")
  if (!root) return
  root.classList.add("hidden")
  root.classList.remove("flex")
  root.innerHTML = ""
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/'/g, "&#39;")
}

function makeId(prefix) {
  try {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return `${prefix}_${window.crypto.randomUUID().replace(/-/g, "")}`
    }
  } catch {
    /* ignore */
  }
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
}

function splitCommaList(input) {
  return String(input || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
}

async function initAutomationSettings() {
  const form = document.getElementById("st-automation-rule-form")
  if (!(form instanceof HTMLFormElement)) return

  const ruleIdEl = document.getElementById("st-auto-rule-id")
  const eventTypeEl = document.getElementById("st-auto-event-type")
  const delayEl = document.getElementById("st-auto-delay-days")
  const publishTimeEl = document.getElementById("st-auto-publish-time")
  const toneEl = document.getElementById("st-auto-caption-tone")
  const accountIdEl = document.getElementById("st-auto-account-id")
  const accountNameEl = document.getElementById("st-auto-account-name")
  const templateEl = document.getElementById("st-auto-template-prompt")
  const includesEl = document.getElementById("st-auto-required-includes")
  const activeEl = document.getElementById("st-auto-active")
  const approveEl = document.getElementById("st-auto-approve")
  const igPostEl = document.getElementById("st-auto-target-ig-post")
  const igStoryEl = document.getElementById("st-auto-target-ig-story")
  const fbPostEl = document.getElementById("st-auto-target-fb-post")
  const ruleStatusEl = document.getElementById("st-auto-rule-status")
  const resetBtn = document.getElementById("st-auto-reset-rule")
  const refreshBtn = document.getElementById("st-auto-refresh-rules")
  const listEl = document.getElementById("st-auto-rules-list")

  const testForm = document.getElementById("st-automation-test-form")
  const testRuleSelect = document.getElementById("st-auto-test-rule-id")
  const testEventTypeEl = document.getElementById("st-auto-test-event-type")
  const testDelayEl = document.getElementById("st-auto-test-delay")
  const testPayloadEl = document.getElementById("st-auto-test-payload")
  const testDryRunEl = document.getElementById("st-auto-test-dry-run")
  const testStatusEl = document.getElementById("st-auto-test-status")
  const testResultEl = document.getElementById("st-auto-test-result")

  if (
    !(ruleIdEl instanceof HTMLInputElement) ||
    !(eventTypeEl instanceof HTMLInputElement) ||
    !(delayEl instanceof HTMLInputElement) ||
    !(publishTimeEl instanceof HTMLInputElement) ||
    !(toneEl instanceof HTMLInputElement) ||
    !(accountIdEl instanceof HTMLInputElement) ||
    !(accountNameEl instanceof HTMLInputElement) ||
    !(templateEl instanceof HTMLTextAreaElement) ||
    !(includesEl instanceof HTMLInputElement) ||
    !(activeEl instanceof HTMLInputElement) ||
    !(approveEl instanceof HTMLInputElement) ||
    !(igPostEl instanceof HTMLInputElement) ||
    !(igStoryEl instanceof HTMLInputElement) ||
    !(fbPostEl instanceof HTMLInputElement) ||
    !(listEl instanceof HTMLElement)
  ) {
    return
  }

  let rules = []

  const resetForm = () => {
    ruleIdEl.value = ""
    eventTypeEl.value = "store_created"
    delayEl.value = "10"
    publishTimeEl.value = "12:00"
    toneEl.value = "profesyonel"
    accountIdEl.value = ""
    accountNameEl.value = ""
    templateEl.value = ""
    includesEl.value = ""
    activeEl.checked = true
    approveEl.checked = false
    igPostEl.checked = true
    igStoryEl.checked = false
    fbPostEl.checked = false
  }

  const setRuleStatus = (text, isErr = false) => {
    if (!(ruleStatusEl instanceof HTMLElement)) return
    ruleStatusEl.textContent = String(text || "")
    ruleStatusEl.classList.toggle("text-red-600", !!isErr)
    ruleStatusEl.classList.toggle("text-emerald-700", !isErr && !!text)
    ruleStatusEl.classList.toggle("text-gray-500", !text)
  }

  const buildRulePayload = () => ({
    eventType: eventTypeEl.value.trim() || "store_created",
    delayDays: Math.max(0, Math.min(365, parseInt(delayEl.value || "10", 10) || 10)),
    publishTime: publishTimeEl.value || "12:00",
    captionTone: toneEl.value.trim() || "profesyonel",
    templatePrompt: templateEl.value.trim(),
    requiredIncludes: splitCommaList(includesEl.value),
    accountId: accountIdEl.value.trim(),
    accountName: accountNameEl.value.trim(),
    autoApprove: !!approveEl.checked,
    isActive: !!activeEl.checked,
    publishTargets: {
      instagram_post: !!igPostEl.checked,
      instagram_story: !!igStoryEl.checked,
      facebook_post: !!fbPostEl.checked,
    },
    updatedAt: new Date().toISOString(),
  })

  const fillFormFromRule = (rule) => {
    const payload = rule && typeof rule === "object" ? rule : {}
    const targets = payload.publishTargets && typeof payload.publishTargets === "object" ? payload.publishTargets : {}
    ruleIdEl.value = String(payload.id || "")
    eventTypeEl.value = String(payload.eventType || "store_created")
    delayEl.value = String(payload.delayDays ?? 10)
    publishTimeEl.value = String(payload.publishTime || "12:00")
    toneEl.value = String(payload.captionTone || "profesyonel")
    accountIdEl.value = String(payload.accountId || "")
    accountNameEl.value = String(payload.accountName || "")
    templateEl.value = String(payload.templatePrompt || "")
    includesEl.value = Array.isArray(payload.requiredIncludes) ? payload.requiredIncludes.join(", ") : ""
    activeEl.checked = payload.isActive !== false
    approveEl.checked = !!payload.autoApprove
    igPostEl.checked = targets.instagram_post !== false
    igStoryEl.checked = !!targets.instagram_story
    fbPostEl.checked = !!targets.facebook_post
  }

  const renderRuleList = () => {
    listEl.innerHTML = ""
    if (!rules.length) {
      listEl.innerHTML =
        '<div class="rounded-xl border border-dashed border-gray-200 bg-white px-4 py-5 text-sm text-gray-500">Kayitli otomasyon kurali yok.</div>'
    } else {
      for (const rule of rules) {
        const item = document.createElement("div")
        item.className = "rounded-xl border border-gray-200 bg-white px-4 py-3"
        const stateText = rule.isActive === false ? "Pasif" : "Aktif"
        const includes = Array.isArray(rule.requiredIncludes) ? rule.requiredIncludes.join(", ") : ""
        item.innerHTML = `
          <div class="flex items-center justify-between gap-2">
            <p class="font-medium text-gray-900">${escapeHtml(String(rule.eventType || "event"))}</p>
            <span class="rounded-full border px-2 py-0.5 text-[11px] ${rule.isActive === false ? "border-gray-300 text-gray-500" : "border-emerald-200 text-emerald-700"}">${stateText}</span>
          </div>
          <p class="mt-1 text-xs text-gray-600">+${escapeHtml(String(rule.delayDays ?? 10))} gun • ${escapeHtml(String(rule.publishTime || "12:00"))}</p>
          <p class="mt-1 text-xs text-gray-600">${escapeHtml(String(rule.templatePrompt || ""))}</p>
          <p class="mt-1 text-[11px] text-gray-500">${escapeHtml(includes)}</p>
          <div class="mt-2 flex gap-2">
            <button type="button" data-act="edit" data-id="${escapeAttr(String(rule.id || ""))}" class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-100">Edit</button>
            <button type="button" data-act="delete" data-id="${escapeAttr(String(rule.id || ""))}" class="rounded-lg border border-red-200 px-2.5 py-1 text-xs text-red-700 hover:bg-red-50">Delete</button>
          </div>
        `
        listEl.appendChild(item)
      }
    }

    if (testRuleSelect instanceof HTMLSelectElement) {
      testRuleSelect.innerHTML = ""
      const optAll = document.createElement("option")
      optAll.value = ""
      optAll.textContent = "Auto match by event_type"
      testRuleSelect.appendChild(optAll)
      for (const rule of rules) {
        const opt = document.createElement("option")
        opt.value = String(rule.id || "")
        opt.textContent = `${String(rule.eventType || "event")} (+${String(rule.delayDays ?? 0)} gun)`
        testRuleSelect.appendChild(opt)
      }
    }
  }

  const loadRules = async () => {
    const rows = await socialList(AUTOMATION_RULES_COLLECTION)
    rules = Array.isArray(rows) ? rows : []
    renderRuleList()
    return rules
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target instanceof HTMLElement ? e.target.closest("button[data-act]") : null
    if (!(btn instanceof HTMLButtonElement)) return
    const id = String(btn.getAttribute("data-id") || "")
    if (!id) return
    const act = String(btn.getAttribute("data-act") || "")
    if (act === "edit") {
      const row = rules.find((x) => String(x.id || "") === id)
      if (row) fillFormFromRule(row)
      setRuleStatus("Rule edit moduna alindi.", false)
      return
    }
    if (act === "delete") {
      try {
        await socialDelete(AUTOMATION_RULES_COLLECTION, id)
        await loadRules()
        if (ruleIdEl.value === id) resetForm()
        setRuleStatus("Rule silindi.", false)
      } catch (err) {
        setRuleStatus(err instanceof Error ? err.message : "Rule silinemedi.", true)
      }
    }
  })

  form.addEventListener("submit", async (e) => {
    e.preventDefault()
    const docId = ruleIdEl.value.trim() || makeId("rule")
    const body = buildRulePayload()
    try {
      await socialPut(AUTOMATION_RULES_COLLECTION, docId, body, false)
      await loadRules()
      const created = rules.find((x) => String(x.id || "") === docId)
      if (created) fillFormFromRule(created)
      setRuleStatus("Rule kaydedildi.", false)
    } catch (err) {
      setRuleStatus(err instanceof Error ? err.message : "Rule kaydedilemedi.", true)
    }
  })

  if (resetBtn instanceof HTMLButtonElement) {
    resetBtn.addEventListener("click", () => {
      resetForm()
      setRuleStatus("", false)
    })
  }
  if (refreshBtn instanceof HTMLButtonElement) {
    refreshBtn.addEventListener("click", () => {
      void loadRules().catch((err) => setRuleStatus(err instanceof Error ? err.message : "Rule listesi yuklenemedi.", true))
    })
  }

  if (
    testForm instanceof HTMLFormElement &&
    testEventTypeEl instanceof HTMLInputElement &&
    testPayloadEl instanceof HTMLTextAreaElement &&
    testDryRunEl instanceof HTMLInputElement
  ) {
    const setTestStatus = (text, isErr = false) => {
      if (!(testStatusEl instanceof HTMLElement)) return
      testStatusEl.textContent = String(text || "")
      testStatusEl.classList.toggle("text-red-600", !!isErr)
      testStatusEl.classList.toggle("text-emerald-700", !isErr && !!text)
      testStatusEl.classList.toggle("text-gray-500", !text)
    }
    testForm.addEventListener("submit", async (e) => {
      e.preventDefault()
      let eventPayload = {}
      try {
        eventPayload = JSON.parse(testPayloadEl.value || "{}")
      } catch {
        setTestStatus("Event payload gecerli JSON olmali.", true)
        return
      }
      const ruleId = testRuleSelect instanceof HTMLSelectElement ? testRuleSelect.value.trim() : ""
      const delayVal = testDelayEl instanceof HTMLInputElement ? testDelayEl.value.trim() : ""
      const body = {
        event_type: testEventTypeEl.value.trim() || "store_created",
        event_payload: eventPayload,
        ...(ruleId ? { rule_id: ruleId } : {}),
        ...(delayVal ? { override_delay_days: Math.max(0, parseInt(delayVal, 10) || 0) } : {}),
        dry_run: !!testDryRunEl.checked,
      }
      setTestStatus("Event gonderiliyor...")
      try {
        const result = await apiRequest("/social-media/automation/events", {
          method: "POST",
          headers: authHeaders(true),
          body: JSON.stringify(body),
        })
        if (testResultEl instanceof HTMLElement) {
          testResultEl.textContent = JSON.stringify(result, null, 2)
        }
        const whenText = `${result?.scheduled_date || "-"} ${result?.scheduled_time || "-"}`
        setTestStatus(`Basarili. Planlanan zaman: ${whenText}. Takvimde Social Media altinda gorebilirsin.`)
      } catch (err) {
        if (testResultEl instanceof HTMLElement) {
          testResultEl.textContent = String(err instanceof Error ? err.message : err || "Request failed")
        }
        setTestStatus(err instanceof Error ? err.message : "Event test basarisiz.", true)
      }
    })
  }

  resetForm()
  await loadRules()
}

document.addEventListener("DOMContentLoaded", () => {
  const o = document.getElementById("st-openai")
  const f = document.getElementById("st-fal")
  const th = document.getElementById("st-threshold")
  const thLabel = document.getElementById("st-threshold-label")
  const thHint = document.getElementById("st-threshold-hint")
  const grid = document.getElementById("st-holiday-grid")
  const master = document.getElementById("st-holiday-master")
  const campaignGrid = document.getElementById("st-campaign-holiday-grid")
  const campaignMaster = document.getElementById("st-campaign-holiday-master")

  if (
    o instanceof HTMLInputElement &&
    f instanceof HTMLInputElement
  ) {
    void loadApiSettings()
      .then((cfg) => {
        o.value = cfg.openaiApiKey || ""
        f.value = cfg.falApiKey || ""
      })
      .catch(() => {
        o.value = ""
        f.value = ""
      })
  }
  if (th instanceof HTMLInputElement) {
    const t0 = parseInt(lsGet(PROMPT_PRO_THRESHOLD_KEY) || "300", 10)
    th.value = String(Number.isFinite(t0) ? Math.max(0, Math.min(3000, t0)) : 300)
  }

  const persist = () => {
    const n = Math.max(0, Math.min(3000, Math.round(Number(th.value) || 300)))
    lsSet(PROMPT_PRO_THRESHOLD_KEY, String(n))
  }
  const persistApiKeys = async () => {
    if (
      !(o instanceof HTMLInputElement) ||
      !(f instanceof HTMLInputElement)
    ) return
    try {
      await saveApiSettings(o.value.trim(), f.value.trim())
    } catch {
      /* ignore */
    }
  }

  ;[o, f].forEach((el) => {
    if (el instanceof HTMLInputElement) el.addEventListener("change", () => void persistApiKeys())
  })
  if (th instanceof HTMLInputElement) th.addEventListener("input", () => {
    const n = Math.max(0, Math.min(3000, Math.round(Number(th.value) || 300)))
    if (thLabel) thLabel.textContent = String(n)
    if (thHint) thHint.textContent = T("settingsPromptProThresholdHintCurrent").replace("{n}", String(n))
    persist()
  })
  if (th instanceof HTMLInputElement) th.dispatchEvent(new Event("input"))

  if (master instanceof HTMLElement) {
    syncMasterToggle(master, getHolidayYearlyAutoEnabled("social"))
    master.addEventListener("click", () => {
      const next = !getHolidayYearlyAutoEnabled("social")
      setHolidayYearlyAutoEnabled(next, "social")
      syncMasterToggle(master, next)
      window.dispatchEvent(new Event("app-holiday-settings"))
    })
  }

  if (grid instanceof HTMLElement) {
    const syncGrid = () => renderHolidayGrid(grid, "social")
    syncGrid()
    window.addEventListener("app-holiday-settings", syncGrid)
  }

  if (campaignMaster instanceof HTMLElement) {
    syncMasterToggle(campaignMaster, getHolidayYearlyAutoEnabled("campaign"))
    campaignMaster.addEventListener("click", () => {
      const next = !getHolidayYearlyAutoEnabled("campaign")
      setHolidayYearlyAutoEnabled(next, "campaign")
      syncMasterToggle(campaignMaster, next)
      window.dispatchEvent(new Event("app-holiday-settings"))
    })
  }

  if (campaignGrid instanceof HTMLElement) {
    const syncCampaignGrid = () => renderHolidayGrid(campaignGrid, "campaign")
    syncCampaignGrid()
    window.addEventListener("app-holiday-settings", syncCampaignGrid)
  }

  void initAutomationSettings()
})
