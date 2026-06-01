/**
 * Tur 5 (Polish-2): Contextual rule paneli.
 * - Backend: /api/internal/structured-rules* + /rule-templates +
 *   /rule-executions + /structured-rules-conflicts/suggestions
 * - CSS: public/assets/css/timeline-rules.css
 *
 * SyntaxError-safe disiplin:
 *   1) Hiçbir string literal'da apostrof + Türkçe ek YOKTUR.
 *   2) Türkçe metinler DOM API ile basılır (textContent / createElement).
 *   3) Template literal mecbursa çift tırnak içine alınmaz; backtick güvenli.
 *   4) PHP -> JS veri: data-* + JSON.parse(dataset.x).
 *
 * UX:
 *   - Toast notification (alert yerine).
 *   - Optimistic toggle (anında değişir, hata olursa geri alır).
 *   - Skeleton loading (sayfa açılışında).
 *   - Empty state ikon + güzel metin.
 *   - Cmd/Ctrl+Enter ile hızlı önizle.
 */
;(function () {
  "use strict"

  const mount = document.getElementById("timeline-rules-mount")
  if (!mount) return

  const slug = String(mount.dataset.timelineSlug || "").trim()
  if (!slug) return

  const apiBase = String(mount.dataset.apiBase || "").replace(/\/+$/, "")
  const token = String(mount.dataset.token || "").trim()
  const userId = String(mount.dataset.userId || "").trim()
  const userIdNum = userId ? Number(userId) : null
  const userIdQs = userId ? "?user_id=" + encodeURIComponent(userId) : ""
  let eventPrefixes = []
  try {
    const raw = mount.dataset.eventPrefixes || "[]"
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) eventPrefixes = parsed.map((p) => String(p))
  } catch (e) {
    eventPrefixes = []
  }

  // ---------------------------------------------------------------------
  // Toast — alert yerine sade bildirim
  // ---------------------------------------------------------------------
  function ensureToastHost() {
    let host = document.getElementById("tr-toast-host")
    if (host) return host
    host = document.createElement("div")
    host.id = "tr-toast-host"
    host.className = "tr-toast-host"
    document.body.appendChild(host)
    return host
  }

  function toast(message, kind) {
    const host = ensureToastHost()
    const el = document.createElement("div")
    el.className = "tr-toast"
    if (kind === "success") el.classList.add("tr-toast-success")
    else if (kind === "error") el.classList.add("tr-toast-error")
    const span = document.createElement("span")
    span.textContent = String(message)
    el.appendChild(span)
    host.appendChild(el)
    const ttl = kind === "error" ? 4500 : 2800
    setTimeout(() => {
      el.classList.add("tr-toast-out")
      setTimeout(() => el.remove(), 220)
    }, ttl)
  }

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
  }

  async function api(method, path, body) {
    const url = `${apiBase}/api/internal${path}`
    const init = {
      method,
      headers: { "Accept": "application/json" },
    }
    if (token) init.headers["Authorization"] = `Bearer ${token}`
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json"
      init.body = JSON.stringify(body)
    }
    const res = await fetch(url, init)
    const text = await res.text()
    let json = null
    try {
      json = text ? JSON.parse(text) : null
    } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const msg =
        (json && (json.detail || json.error || json.message)) ||
        text ||
        `HTTP ${res.status}`
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg))
    }
    return json
  }

  // Backend tüm liste endpoint'leri {data: [...]} formatı döndürüyor.
  // Bu helper response shape'ini bir array'e indirger.
  function pickList(resp) {
    if (Array.isArray(resp)) return resp
    if (resp && Array.isArray(resp.data)) return resp.data
    return []
  }

  function matchSlug(rule) {
    if (!eventPrefixes.length) return true
    const evt = String(rule?.trigger?.event_type || "").toLowerCase()
    return eventPrefixes.some((p) => evt.startsWith(String(p).toLowerCase()))
  }

  function healthChipClass(score) {
    const s = Number(score)
    if (!Number.isFinite(s) || s >= 0.75) return "tr-chip-good"
    if (s >= 0.45) return "tr-chip-warn"
    return "tr-chip-bad"
  }

  function fmtPercent(score) {
    const s = Number(score)
    if (!Number.isFinite(s)) return "—"
    return `${Math.round(s * 100)}%`
  }

  function fmtRelativeTime(iso) {
    if (!iso) return ""
    const t = Date.parse(iso)
    if (!Number.isFinite(t)) return ""
    const diff = Math.max(0, Date.now() - t)
    const sec = Math.floor(diff / 1000)
    if (sec < 60) return `${sec} sn önce`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min} dk önce`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr} sa önce`
    const day = Math.floor(hr / 24)
    return `${day} gün önce`
  }

  function humanizeSeconds(s) {
    const n = Number(s)
    if (!Number.isFinite(n) || n <= 0) return "anında"
    if (n < 60) return `${n} sn`
    if (n < 3600) return `${Math.round(n / 60)} dk`
    if (n < 86400) return `${Math.round(n / 3600)} sa`
    return `${Math.round(n / 86400)} gün`
  }

  function statusBadgeClass(status) {
    const s = String(status || "").toLowerCase()
    if (s === "completed" || s === "success") return "tr-chip-good"
    if (s === "failed" || s === "error") return "tr-chip-bad"
    if (s === "waiting" || s === "waiting_approval") return "tr-chip-warn"
    if (s === "running") return "tr-chip-on"
    return "tr-chip-off"
  }

  function statusLabel(status) {
    const s = String(status || "").toLowerCase()
    const map = {
      "completed": "Tamamlandı",
      "success": "Tamamlandı",
      "failed": "Başarısız",
      "error": "Başarısız",
      "waiting": "Bekliyor",
      "waiting_approval": "Onay bekliyor",
      "running": "Çalışıyor",
    }
    return map[s] || (status ? String(status) : "—")
  }

  function setStatus(el, text) {
    el.textContent = String(text == null ? "" : text)
  }

  function appendChip(parent, cls, text) {
    parent.appendChild(document.createTextNode(" "))
    const span = document.createElement("span")
    span.className = cls
    span.textContent = String(text)
    parent.appendChild(span)
  }

  // ---------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------
  const elNlInput = document.getElementById("tr-nl-input")
  const elPreview = document.getElementById("tr-preview")
  const elSaveBtn = document.getElementById("tr-save-btn")
  const elList = document.getElementById("tr-list")
  const elCount = document.getElementById("tr-count")
  const elTemplatesSection = document.getElementById("tr-templates-section")
  const elTemplatesGrid = document.getElementById("tr-templates-grid")
  const elConflicts = document.getElementById("tr-conflicts")

  let lastParsed = null
  let lastNlText = ""
  let conflictsByRule = new Map()

  // ---------------------------------------------------------------------
  // Liste — kart render
  // ---------------------------------------------------------------------
  function renderList(rules) {
    elList.replaceChildren()
    elCount.textContent = String(rules.length)

    if (!rules.length) {
      const empty = document.createElement("div")
      empty.className = "tr-empty"
      const icon = document.createElement("span")
      icon.className = "tr-empty-icon"
      icon.textContent = "✨"
      const txt = document.createElement("div")
      txt.textContent =
        "Bu sekme için henüz kural yok. Yukarıdan doğal Türkçe ile bir tane oluştur veya şablonlardan birini seç."
      empty.appendChild(icon)
      empty.appendChild(txt)
      elList.appendChild(empty)
      return
    }

    for (const rule of rules) {
      elList.appendChild(buildRuleItem(rule))
    }
  }

  function buildRuleItem(rule) {
    const item = document.createElement("div")
    item.className = "tr-item"
    item.setAttribute("data-rule-id", String(rule.id))

    const main = document.createElement("div")
    main.className = "tr-item-main"

    const titleRow = document.createElement("div")
    titleRow.className = "tr-item-title"
    titleRow.textContent = String(
      rule.name || rule.natural_language || `Kural #${rule.id}`,
    )

    const subRow = document.createElement("div")
    subRow.className = "tr-item-sub"

    appendChip(subRow, "tr-chip", `tetik: ${String(rule?.trigger?.event_type || "—")}`)
    appendChip(subRow, "tr-chip", `kanal: ${String(rule?.content?.channel || "—")}`)
    appendChip(subRow, "tr-chip", `şablon: ${String(rule?.content?.template || "generic")}`)
    appendChip(
      subRow,
      `tr-chip ${rule.enabled ? "tr-chip-on" : "tr-chip-off"}`,
      rule.enabled ? "AKTİF" : "PASİF",
    )

    if (rule.health_score != null) {
      appendChip(
        subRow,
        `tr-chip ${healthChipClass(rule.health_score)}`,
        `sağlık: ${fmtPercent(rule.health_score)}`,
      )
    }

    const execChip = document.createElement("span")
    execChip.className = "tr-chip tr-chip-off"
    execChip.setAttribute("data-tr-exec-chip", String(rule.id))
    execChip.textContent = "son: yükleniyor…"
    subRow.appendChild(document.createTextNode(" "))
    subRow.appendChild(execChip)

    main.appendChild(titleRow)
    main.appendChild(subRow)

    const conflicts = conflictsByRule.get(Number(rule.id)) || []
    if (conflicts.length) {
      const warn = document.createElement("div")
      warn.className = "tr-warn"
      const icon = document.createElement("span")
      icon.className = "tr-warn-icon"
      icon.textContent = "⚠"
      const txt = document.createElement("span")
      txt.textContent = ` ${conflicts.length} çakışma önerisi var. `
      warn.appendChild(icon)
      warn.appendChild(txt)
      const link = document.createElement("a")
      link.href =
        (window.__APP_BASE_PATH__ || "") + "/social-media/system-admin"
      link.textContent = "AI Operatör ile çöz →"
      warn.appendChild(link)
      main.appendChild(warn)
    }

    const actions = document.createElement("div")
    actions.className = "tr-item-actions"

    const toggleBtn = document.createElement("button")
    toggleBtn.type = "button"
    toggleBtn.className = `tr-btn ${rule.enabled ? "tr-btn-ghost" : "tr-btn-accent"}`
    toggleBtn.textContent = rule.enabled ? "Pasifleştir" : "Etkinleştir"
    toggleBtn.addEventListener("click", () => onToggle(rule, !rule.enabled, toggleBtn, item))

    const delBtn = document.createElement("button")
    delBtn.type = "button"
    delBtn.className = "tr-btn tr-btn-danger"
    delBtn.textContent = "Sil"
    delBtn.addEventListener("click", () => onDelete(rule))

    actions.appendChild(toggleBtn)
    actions.appendChild(delBtn)

    item.appendChild(main)
    item.appendChild(actions)

    loadLastExecution(rule.id)

    return item
  }

  // ---------------------------------------------------------------------
  // Last execution chip (per rule, background fetch)
  // ---------------------------------------------------------------------
  async function loadLastExecution(ruleId) {
    const chip = elList.querySelector(`[data-tr-exec-chip="${ruleId}"]`)
    try {
      const resp = await api(
        "GET",
        `/rule-executions?rule_id=${encodeURIComponent(ruleId)}&limit=1`,
      )
      const list = pickList(resp)
      const exec = list[0] || null
      if (!chip) return
      if (!exec) {
        chip.className = "tr-chip tr-chip-off"
        chip.textContent = "henüz yürütülmedi"
        return
      }
      const when = fmtRelativeTime(
        exec.finished_at || exec.started_at || exec.created_at,
      )
      chip.className = `tr-chip ${statusBadgeClass(exec.status)}`
      chip.textContent = `son: ${statusLabel(exec.status)}${when ? ` · ${when}` : ""}`
    } catch (e) {
      if (chip) {
        chip.className = "tr-chip tr-chip-off"
        chip.textContent = "son: çekilemedi"
      }
    }
  }

  // ---------------------------------------------------------------------
  // Conflicts feed (mount banner)
  // ---------------------------------------------------------------------
  async function loadConflicts() {
    conflictsByRule = new Map()
    if (!elConflicts) return
    elConflicts.replaceChildren()
    try {
      const resp = await api("GET", "/structured-rules-conflicts/suggestions" + userIdQs)
      const list = pickList(resp)
      for (const s of list) {
        const ruleId = Number(s?.rule_id || s?.id || 0)
        if (!ruleId) continue
        const arr = conflictsByRule.get(ruleId) || []
        arr.push(s)
        conflictsByRule.set(ruleId, arr)
      }
      if (!list.length) return
      const banner = document.createElement("div")
      banner.className = "tr-conflicts-banner"
      const body = document.createElement("div")
      body.className = "tr-conflicts-body"
      const title = document.createElement("div")
      title.className = "tr-conflicts-title"
      title.textContent = `AI ${list.length} kural çakışması öneriyor`
      const desc = document.createElement("div")
      desc.className = "tr-conflicts-desc"
      desc.textContent =
        "Sistem Yöneticisi AI Operatör ile doğal Türkçe konuşarak çözebilirsin."
      body.appendChild(title)
      body.appendChild(desc)
      const linkRow = document.createElement("div")
      linkRow.style.marginTop = ".55rem"
      const link = document.createElement("a")
      link.className = "tr-btn tr-btn-accent"
      link.style.textDecoration = "none"
      link.style.fontSize = ".82rem"
      link.style.padding = ".4rem .85rem"
      link.href = (window.__APP_BASE_PATH__ || "") + "/social-media/system-admin"
      link.textContent = "AI Operatör ile çöz →"
      linkRow.appendChild(link)
      body.appendChild(linkRow)
      banner.appendChild(body)
      elConflicts.appendChild(banner)
    } catch (e) { /* sessizce sayfayı bozma */ }
  }

  // ---------------------------------------------------------------------
  // Data flow
  // ---------------------------------------------------------------------
  async function refresh() {
    try {
      await loadConflicts()
      const resp = await api("GET", "/structured-rules" + userIdQs)
      const list = pickList(resp)
      const filtered = list.filter(matchSlug)
      filtered.sort((a, b) => {
        if (Boolean(b.enabled) !== Boolean(a.enabled)) {
          return Boolean(b.enabled) ? 1 : -1
        }
        return Number(b.id || 0) - Number(a.id || 0)
      })
      renderList(filtered)
    } catch (e) {
      elList.replaceChildren()
      const err = document.createElement("div")
      err.className = "tr-empty"
      err.style.color = "var(--tr-rose-700, #b91c1c)"
      const icon = document.createElement("span")
      icon.className = "tr-empty-icon"
      icon.textContent = "⚠"
      const txt = document.createElement("div")
      txt.textContent = `Kurallar yüklenemedi: ${e.message}`
      err.appendChild(icon)
      err.appendChild(txt)
      elList.appendChild(err)
    }
  }

  async function loadTemplatesIfNeeded() {
    if (elTemplatesGrid.dataset.loaded === "1") return
    try {
      const resp = await api("GET", "/rule-templates")
      const tpls = pickList(resp)
      elTemplatesGrid.replaceChildren()
      if (!tpls.length) {
        const empty = document.createElement("div")
        empty.className = "tr-empty"
        empty.style.gridColumn = "1/-1"
        empty.textContent = "Şablon bulunamadı."
        elTemplatesGrid.appendChild(empty)
      } else {
        tpls.sort((a, b) => {
          const aMatch = templateMatchesSlug(a) ? 0 : 1
          const bMatch = templateMatchesSlug(b) ? 0 : 1
          if (aMatch !== bMatch) return aMatch - bMatch
          return String(a.name || a.slug || "").localeCompare(
            String(b.name || b.slug || ""),
            "tr",
          )
        })
        for (const t of tpls) {
          const card = document.createElement("button")
          card.type = "button"
          card.className = "tr-tpl"
          if (templateMatchesSlug(t)) card.classList.add("tr-tpl-relevant")
          const strong = document.createElement("strong")
          strong.textContent = String(t.name || t.slug || "Şablon")
          card.appendChild(strong)
          const desc = document.createTextNode(
            String(t.description || t.summary || ""),
          )
          card.appendChild(desc)
          card.addEventListener("click", () => onTemplatePick(t))
          elTemplatesGrid.appendChild(card)
        }
      }
      elTemplatesGrid.dataset.loaded = "1"
    } catch (e) {
      elTemplatesGrid.replaceChildren()
      const err = document.createElement("div")
      err.className = "tr-empty"
      err.style.gridColumn = "1/-1"
      err.style.color = "var(--tr-rose-700, #b91c1c)"
      err.textContent = `Şablonlar yüklenemedi: ${e.message}`
      elTemplatesGrid.appendChild(err)
    }
  }

  function templateMatchesSlug(t) {
    if (!eventPrefixes.length) return true
    const evt = String(t?.trigger_event_type || t?.event_type || "").toLowerCase()
    return eventPrefixes.some((p) => evt.startsWith(String(p).toLowerCase()))
  }

  function onTemplatePick(t) {
    const sample = String(
      t.natural_language_template ||
        t.placeholder ||
        t.summary ||
        t.description ||
        t.name ||
        "",
    )
    elNlInput.value = sample
    elTemplatesSection.style.display = "none"
    elNlInput.focus()
    onPreview()
  }

  // ---------------------------------------------------------------------
  // Composer actions
  // ---------------------------------------------------------------------
  async function onPreview() {
    const text = String(elNlInput.value || "").trim()
    if (!text) {
      elPreview.style.display = "block"
      setStatus(elPreview, "Boş metin. Bir şeyler yaz veya şablon seç.")
      return
    }
    elPreview.style.display = "block"
    elPreview.style.color = ""
    setStatus(elPreview, "Analiz ediliyor…")
    elSaveBtn.disabled = true
    lastParsed = null
    lastNlText = text
    try {
      const resp = await api("POST", "/structured-rules/parse", {
        natural_language: text,
      })
      // Backend cevabı: {rule, explanation, parse_confidence, missing_fields}
      const rule = resp && resp.rule ? resp.rule : resp
      // explanation + missing_fields tepe seviyede; rule içine de basıyoruz
      rule.explanation = resp && resp.explanation ? resp.explanation : rule.explanation
      rule.missing = resp && resp.missing_fields ? resp.missing_fields : (rule.missing || [])
      lastParsed = rule
      renderPreview(rule)
      elSaveBtn.disabled = false
    } catch (e) {
      lastParsed = null
      elSaveBtn.disabled = true
      elPreview.style.color = "var(--tr-rose-700, #b91c1c)"
      setStatus(elPreview, `Hata: ${e.message}`)
    }
  }

  function renderPreview(r) {
    elPreview.replaceChildren()
    elPreview.style.color = ""

    const head = document.createElement("div")
    head.className = "tr-preview-head"
    head.textContent = String(r.name || r.natural_language || "Kural taslağı")
    elPreview.appendChild(head)

    const meta = document.createElement("div")
    meta.className = "tr-preview-meta"
    appendChip(meta, "tr-chip", `Tetik: ${String(r?.trigger?.event_type || "—")}`)
    const delay = Number(r?.timing?.delay_seconds || 0)
    appendChip(meta, "tr-chip", `Bekleme: ${delay > 0 ? humanizeSeconds(delay) : "anında"}`)
    appendChip(meta, "tr-chip", `Kanal: ${String(r?.content?.channel || "—")}`)
    appendChip(meta, "tr-chip", `Şablon: ${String(r?.content?.template || "generic")}`)
    elPreview.appendChild(meta)

    const actions = Array.isArray(r?.actions) ? r.actions : []
    if (actions.length) {
      const stepper = document.createElement("div")
      stepper.className = "tr-stepper"
      const startPill = document.createElement("span")
      startPill.className = "tr-step tr-step-start"
      startPill.textContent = "başla"
      stepper.appendChild(startPill)
      for (const a of actions) {
        const arrow = document.createElement("span")
        arrow.className = "tr-step-arrow"
        arrow.textContent = "→"
        stepper.appendChild(arrow)
        const pill = document.createElement("span")
        const kind = String(a?.kind || "—").toLowerCase()
        let cls = "tr-step"
        if (kind === "approval") cls += " tr-step-pause"
        else if (kind === "wait") cls += " tr-step-wait"
        else if (kind === "publish") cls += " tr-step-publish"
        pill.className = cls
        pill.textContent = kind
        stepper.appendChild(pill)
      }
      const arrowEnd = document.createElement("span")
      arrowEnd.className = "tr-step-arrow"
      arrowEnd.textContent = "→"
      stepper.appendChild(arrowEnd)
      const endPill = document.createElement("span")
      endPill.className = "tr-step tr-step-end"
      endPill.textContent = "bitir"
      stepper.appendChild(endPill)
      elPreview.appendChild(stepper)
    }

    if (r.explanation) {
      const exp = document.createElement("div")
      exp.className = "tr-preview-exp"
      exp.textContent = String(r.explanation)
      elPreview.appendChild(exp)
    }

    const missing = Array.isArray(r?.missing) ? r.missing : []
    if (missing.length) {
      const miss = document.createElement("div")
      miss.className = "tr-preview-miss"
      miss.textContent = `Eksik: ${missing.join(", ")} — varsayılan kullanılacak`
      elPreview.appendChild(miss)
    }
  }

  async function onSave() {
    if (!lastParsed) {
      toast("Önce Önizle ile kuralı analiz et.", "error")
      return
    }
    elSaveBtn.disabled = true
    elSaveBtn.textContent = "Kaydediliyor…"
    try {
      // Backend RuleCreateRequest: {natural_language, name?, user_id?, enabled?}
      const payload = {
        natural_language: lastNlText,
        name: (lastParsed && lastParsed.name) || null,
        enabled: true,
      }
      if (userIdNum && Number.isFinite(userIdNum)) payload.user_id = userIdNum
      await api("POST", "/structured-rules", payload)
      toast("Kural etkinleştirildi.", "success")
      elNlInput.value = ""
      elPreview.style.display = "none"
      lastParsed = null
      elSaveBtn.textContent = "Kuralı Etkinleştir"
      await refresh()
    } catch (e) {
      elSaveBtn.disabled = false
      elSaveBtn.textContent = "Kuralı Etkinleştir"
      toast(`Kural kaydedilemedi: ${e.message}`, "error")
    }
  }

  function onClear() {
    elNlInput.value = ""
    elPreview.style.display = "none"
    elSaveBtn.disabled = true
    lastParsed = null
  }

  // Optimistic toggle — UI önce, sonra API. Hata olursa geri al.
  async function onToggle(rule, nextState, btn, item) {
    const prevLabel = btn.textContent
    const prevClass = btn.className
    btn.disabled = true
    btn.textContent = nextState ? "Etkinleştiriliyor…" : "Pasifleştiriliyor…"

    // Optimistic chip update
    const stateChip = item.querySelector(".tr-chip.tr-chip-on, .tr-chip.tr-chip-off")
    let prevChipClass = null
    let prevChipText = null
    if (stateChip) {
      prevChipClass = stateChip.className
      prevChipText = stateChip.textContent
      stateChip.className = `tr-chip ${nextState ? "tr-chip-on" : "tr-chip-off"}`
      stateChip.textContent = nextState ? "AKTİF" : "PASİF"
    }

    try {
      // Backend: enabled bir Query parametresi (body değil)
      await api(
        "PATCH",
        `/structured-rules/${rule.id}/enabled?enabled=${Boolean(nextState)}`,
      )
      toast(nextState ? "Kural etkin." : "Kural pasif.", "success")
      await refresh()
    } catch (e) {
      // Geri al
      btn.textContent = prevLabel
      btn.className = prevClass
      btn.disabled = false
      if (stateChip && prevChipClass != null) {
        stateChip.className = prevChipClass
        stateChip.textContent = prevChipText
      }
      toast(`Durum güncellenemedi: ${e.message}`, "error")
    }
  }

  async function onDelete(rule) {
    const ok = window.confirm(
      `Bu kuralı silmek istediğine emin misin?\n${String(rule.name || rule.id)}`,
    )
    if (!ok) return
    try {
      await api("DELETE", `/structured-rules/${rule.id}`)
      toast("Kural silindi.", "success")
      await refresh()
    } catch (e) {
      toast(`Silinemedi: ${e.message}`, "error")
    }
  }

  function toggleTemplates() {
    const open = elTemplatesSection.style.display !== "none"
    elTemplatesSection.style.display = open ? "none" : "block"
    if (!open) loadTemplatesIfNeeded()
  }

  // ---------------------------------------------------------------------
  // Wire up
  // ---------------------------------------------------------------------
  mount.addEventListener("click", (ev) => {
    const target = ev.target
    if (!(target instanceof HTMLElement)) return
    const act = target.getAttribute("data-tr-act")
    if (!act) return
    if (act === "preview") onPreview()
    else if (act === "save") onSave()
    else if (act === "clear") onClear()
    else if (act === "toggle-templates") toggleTemplates()
  })

  if (elNlInput) {
    elNlInput.addEventListener("keydown", (ev) => {
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
        ev.preventDefault()
        onPreview()
      }
    })
  }

  refresh()
})()
