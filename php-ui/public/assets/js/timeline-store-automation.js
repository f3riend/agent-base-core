// Sistem Yöneticisi — AI Operatör Merkezi (Faz 4 rewrite).
//
// Sol panel: chat geçmişi (yeni PG chat_sessions tablosu).
// Orta panel: chat akışı (mevcut UI korunur — tab'lar, mod butonları,
// presence indicator, log, form).
//
// Backend endpoint'leri:
//   GET    /api/internal/chat/sessions?user_id=...
//   GET    /api/internal/chat/sessions/{id}?user_id=...
//   POST   /api/internal/chat/new-session?user_id=...
//   DELETE /api/internal/chat/sessions/{id}?user_id=...
//   POST   /api/internal/chat                   (body: question, user_id, session_id)

(function () {
  "use strict"

  const root = document.getElementById("tsop-system-admin")
  if (!root) return

  const apiBase = (window.__AGENTBASE__?.apiBase || root.dataset.apiBase || "").replace(/\/+$/, "")
  const userId = (root.dataset.userId || "3").trim() || "3"

  // ---------- State ----------
  const state = {
    sessions: [],          // chat_sessions[] (özet)
    activeSessionId: null, // UUID string
    messages: [],          // active session mesajları
    sending: false,
  }

  const LS_ACTIVE_KEY = "tsop_active_chat_session"

  // ---------- DOM ----------
  const els = {
    historyList: document.getElementById("tsop-chat-history-list"),
    newChatBtn: document.getElementById("tsop-new-chat"),
    chatLog: document.getElementById("tsws-chat-log"),
    chatScroll: document.querySelector(".tsws-v2-chat-log-wrapper"),
    chatForm: document.getElementById("tsws-chat-form"),
    chatInput: document.getElementById("tsws-chat-input"),
    chatTabs: Array.from(document.querySelectorAll(".tsop-chat-tabs button")),
    aiModes: Array.from(document.querySelectorAll(".tsop-ai-modes button")),
    presence: document.getElementById("tsop-ai-presence"),
    presenceText: document.getElementById("tsop-ai-presence-text"),
  }

  if (!els.historyList || !els.chatLog || !els.chatForm || !els.chatInput) {
    console.warn("[system-admin] gerekli DOM elementleri bulunamadı, init iptal.")
    return
  }

  // ---------- Utilities ----------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;")
  }

  function formatRelTime(iso) {
    if (!iso) return ""
    const t = new Date(iso)
    if (!Number.isFinite(t.getTime())) return ""
    const now = new Date()
    const diffSec = Math.floor((now - t) / 1000)
    if (diffSec < 60) return "şimdi"
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)} dk önce`
    const sameDay = t.toDateString() === now.toDateString()
    if (sameDay) return t.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
    const y = new Date(now); y.setDate(y.getDate() - 1)
    if (t.toDateString() === y.toDateString()) {
      return "Dün " + t.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })
    }
    return t.toLocaleDateString("tr-TR", { day: "numeric", month: "short" })
  }

  function nowIso() { return new Date().toISOString() }

  function setPresence(stateName, text = "", ttlMs = 0) {
    if (els.presence instanceof HTMLElement) els.presence.dataset.presence = String(stateName || "idle")
    if (els.presenceText instanceof HTMLElement && text) els.presenceText.textContent = text
    if (ttlMs > 0) {
      setTimeout(() => {
        if (els.presence instanceof HTMLElement) els.presence.dataset.presence = "idle"
        if (els.presenceText instanceof HTMLElement) els.presenceText.textContent = "AI hazir"
      }, ttlMs)
    }
  }

  // ---------- API ----------
  async function _request(method, path, body) {
    const init = { method, headers: { "Accept": "application/json" } }
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json"
      init.body = JSON.stringify(body)
    }
    const res = await fetch(apiBase + path, init)
    if (res.status === 204) return null
    let parsed = null
    try { parsed = await res.json() } catch { parsed = null }
    if (!res.ok) {
      const detail = parsed?.detail || parsed?.error || ("HTTP " + res.status)
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail))
    }
    return parsed
  }
  const apiGet    = (path)       => _request("GET", path)
  const apiPost   = (path, body) => _request("POST", path, body ?? {})
  const apiDelete = (path)       => _request("DELETE", path)

  function qsUser() { return `user_id=${encodeURIComponent(userId)}` }

  // ---------- Session listesi ----------
  async function loadSessions() {
    try {
      const resp = await apiGet(`/api/internal/chat/sessions?${qsUser()}`)
      state.sessions = Array.isArray(resp?.data) ? resp.data : []
    } catch (e) {
      console.warn("[system-admin] sessions load failed:", e)
      state.sessions = []
    }
    renderSessionList()
  }

  function renderSessionList() {
    if (!state.sessions.length) {
      els.historyList.innerHTML = `
        <p style="color:#9ca3af; padding:0.5rem; font-size:0.85rem;">
          Henüz sohbet yok. <br><br>Yukarıdan + Yeni Sohbet ile başla.
        </p>`
      return
    }
    els.historyList.innerHTML = state.sessions.map(sessionCard).join("")
    els.historyList.querySelectorAll("[data-session-id]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-act='delete-session']")) return
        const sid = String(el.dataset.sessionId || "")
        if (sid) void selectSession(sid)
      })
    })
    els.historyList.querySelectorAll("[data-act='delete-session']").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation()
        const sid = String(btn.dataset.sessionId || "")
        if (!sid) return
        if (!confirm("Bu sohbeti silmek istediğine emin misin?")) return
        try {
          await apiDelete(`/api/internal/chat/sessions/${encodeURIComponent(sid)}?${qsUser()}`)
          if (state.activeSessionId === sid) {
            state.activeSessionId = null
            state.messages = []
            els.chatLog.innerHTML = ""
            try { localStorage.removeItem(LS_ACTIVE_KEY) } catch {}
          }
          await loadSessions()
        } catch (err) {
          alert("Silinemedi: " + (err.message || String(err)))
        }
      })
    })
  }

  function sessionCard(s) {
    const active = s.id === state.activeSessionId
    const title = (s.title || "").trim() || "(Başlıksız sohbet)"
    const when = formatRelTime(s.last_message_at || s.created_at)
    const baseStyle = [
      "display:flex", "flex-direction:column", "gap:0.15rem",
      "padding:0.55rem 0.65rem",
      "background:" + (active ? "#f3f4f6" : "#fff"),
      "border:1px solid " + (active ? "#d1d5db" : "#e5e7eb"),
      "border-left:3px solid " + (active ? "#111827" : "transparent"),
      "border-radius:0.5rem",
      "cursor:pointer",
      "transition:background .12s",
    ].join(";")
    return [
      `<div data-session-id="${esc(s.id)}" style="${baseStyle}"`,
      `     onmouseover="if(this.dataset.active!=='1')this.style.background='#f9fafb'"`,
      `     onmouseout="if(this.dataset.active!=='1')this.style.background='#fff'"`,
      `     ${active ? 'data-active="1"' : ""}>`,
      `  <div style="display:flex; justify-content:space-between; align-items:center; gap:0.4rem;">`,
      `    <strong style="font-size:0.85rem; color:#111827; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">${esc(title)}</strong>`,
      `    <button type="button" data-act="delete-session" data-session-id="${esc(s.id)}" title="Sil" aria-label="Sohbeti sil"`,
      `            style="background:transparent; border:0; color:#9ca3af; cursor:pointer; padding:0.15rem 0.3rem; font-size:0.95rem; line-height:1;">×</button>`,
      `  </div>`,
      when ? `<small style="color:#6b7280; font-size:0.7rem;">${esc(when)}</small>` : "",
      `</div>`,
    ].filter(Boolean).join("")
  }

  // ---------- Session seç + mesajları yükle ----------
  async function selectSession(sessionId) {
    state.activeSessionId = String(sessionId || "")
    try { localStorage.setItem(LS_ACTIVE_KEY, state.activeSessionId) } catch {}
    renderSessionList()
    els.chatLog.innerHTML = ""
    state.messages = []
    if (!state.activeSessionId) return
    try {
      const resp = await apiGet(
        `/api/internal/chat/sessions/${encodeURIComponent(state.activeSessionId)}?${qsUser()}`
      )
      const sess = resp?.data
      state.messages = Array.isArray(sess?.messages) ? sess.messages : []
    } catch (e) {
      console.warn("[system-admin] session load failed:", e)
      state.messages = []
    }
    renderMessages()
  }

  function renderMessages() {
    els.chatLog.innerHTML = ""
    for (const m of state.messages) {
      appendChat(m.role === "assistant" ? "assistant" : "user", m.content, formatRelTime(m.created_at), false)
    }
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
  }

  // ---------- Yeni session ----------
  async function newSession() {
    try {
      const resp = await apiPost(`/api/internal/chat/new-session?${qsUser()}`, {})
      const sess = resp?.data
      const sid = sess?.id ? String(sess.id) : null
      if (!sid) throw new Error("Session ID alınamadı.")
      await loadSessions()
      await selectSession(sid)
      els.chatInput.focus()
    } catch (e) {
      alert("Yeni sohbet açılamadı: " + (e.message || String(e)))
    }
  }

  // ---------- Chat balonu ----------
  function appendChat(kind, text, meta = "", scroll = true) {
    const row = document.createElement("div")
    row.className = `tsop-chat-msg tsop-chat-${kind}`
    const safe = esc(text).replace(/\n/g, "<br>")
    row.innerHTML = `<p>${safe}</p>${meta ? `<small>${esc(meta)}</small>` : ""}`
    els.chatLog.appendChild(row)
    if (scroll && els.chatScroll instanceof HTMLElement) {
      els.chatScroll.scrollTop = els.chatScroll.scrollHeight
    }
  }

  let thinkingNode = null
  function showThinking(text = "Düşünüyor…") {
    if (thinkingNode instanceof HTMLElement) return
    const row = document.createElement("div")
    row.className = "tsop-chat-msg tsop-chat-assistant is-thinking tsop-chat-thinking-live"
    row.innerHTML = `<p>${esc(text)}</p><small>${esc("Sessiz analiz suruyor...")}</small>`
    els.chatLog.appendChild(row)
    thinkingNode = row
    if (els.chatScroll instanceof HTMLElement) els.chatScroll.scrollTop = els.chatScroll.scrollHeight
    setPresence("thinking", "Sessiz analiz suruyor...")
  }
  function clearThinking() {
    if (thinkingNode instanceof HTMLElement) { thinkingNode.remove(); thinkingNode = null }
    setPresence("watching", "Baglam izleniyor", 2200)
  }

  // ---------- Mesaj gönder ----------
  async function sendMessage(message) {
    const text = String(message || "").trim()
    if (!text || state.sending) return
    state.sending = true

    appendChat("user", text, formatRelTime(nowIso()))
    showThinking()

    try {
      const resp = await apiPost(`/api/internal/chat`, {
        question: text,
        user_id: Number(userId) || 3,
        session_id: state.activeSessionId,
      })
      const answer = String(resp?.answer || resp?.response || "Cevap alınamadı.")
      const newSid = resp?.session_id ? String(resp.session_id) : null

      clearThinking()
      appendChat("assistant", answer, formatRelTime(nowIso()))

      // Cevap yeni session yarattıysa state'i ve sidebar'ı güncelle
      if (newSid && newSid !== state.activeSessionId) {
        state.activeSessionId = newSid
        try { localStorage.setItem(LS_ACTIVE_KEY, newSid) } catch {}
      }
      // Sidebar listesi tazelensin (yeni session veya last_message_at değişikliği)
      await loadSessions()

      // Backend recommendations[] dönerse hızlı özet
      const recs = Array.isArray(resp?.recommendations) ? resp.recommendations.slice(0, 3) : []
      if (recs.length) {
        const rText = recs.map((r) => "• " + (r.suggestion || r.intent || "")).filter(Boolean).join("\n")
        if (rText) appendChat("assistant", "Öneriler:\n" + rText, "", true)
      }

      setPresence("idle", "AI hazir", 1500)
    } catch (err) {
      clearThinking()
      appendChat("assistant", "Hata: " + (err.message || String(err)), formatRelTime(nowIso()))
      setPresence("alert", "AI baglantisinda gecici sorun var", 3500)
    } finally {
      state.sending = false
    }
  }

  // ---------- Form / butonlar ----------
  els.chatForm.addEventListener("submit", (e) => {
    e.preventDefault()
    const text = els.chatInput.value
    if (!text.trim()) return
    els.chatInput.value = ""
    void sendMessage(text)
  })
  els.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      els.chatForm.dispatchEvent(new Event("submit", { cancelable: true }))
    }
  })

  els.newChatBtn?.addEventListener("click", () => { void newSession() })

  // Tab'lar ve mod butonları — UI state cosmetic (backend etkilemez)
  els.chatTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      els.chatTabs.forEach((x) => x.classList.remove("is-active"))
      btn.classList.add("is-active")
    })
  })
  els.aiModes.forEach((btn) => {
    btn.addEventListener("click", () => {
      els.aiModes.forEach((x) => x.classList.remove("is-active"))
      btn.classList.add("is-active")
    })
  })

  // ---------- Init ----------
  async function init() {
    await loadSessions()
    let active = null
    try { active = localStorage.getItem(LS_ACTIVE_KEY) } catch {}
    if (active && state.sessions.some((s) => s.id === active)) {
      await selectSession(active)
    } else if (state.sessions.length) {
      // Son aktif yoksa otomatik en son session'ı aç
      await selectSession(state.sessions[0].id)
    }
    setPresence("idle", "AI hazir")
  }
  void init()
})()
