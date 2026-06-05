<?php declare(strict_types=1); ?>
<?php
/**
 * Sistem Yöneticisi — AI Operatör Merkezi (Faz 4).
 *
 * Sol panel: chat geçmişi (yeni MySQL/PG chat_sessions tablosundan).
 * Orta panel: chat akışı (tab'lar + mod butonları + presence + log + form).
 * Sağ panel kaldırıldı.
 *
 * Backend (orchestration_api.py):
 *   GET    /api/internal/chat/sessions?user_id=...        — session listesi
 *   GET    /api/internal/chat/sessions/{id}?user_id=...   — mesajlar dahil detay
 *   POST   /api/internal/chat/new-session?user_id=...     — boş session aç
 *   DELETE /api/internal/chat/sessions/{id}?user_id=...
 *   POST   /api/internal/chat                             — soru-cevap (session_id ile)
 */
$apiBase = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
// Mevcut /api/internal/chat auth'suz; user_id query parametresinden gelir.
// app_current_user() username + uid döner (numeric id yok). Geçici olarak
// data-user-id'yi 3 default — backend DEFAULT_USER_ID=3 ile uyumlu.
$cu = function_exists('app_current_user') ? app_current_user() : null;
$userId = '3';
if (is_array($cu)) {
    if (isset($cu['id']) && is_int($cu['id'])) {
        $userId = (string) $cu['id'];
    } elseif (isset($cu['user_id']) && is_int($cu['user_id'])) {
        $userId = (string) $cu['user_id'];
    }
}
$userIdAttr = htmlspecialchars($userId, ENT_QUOTES, 'UTF-8');
?>
<div class="tsop-page" id="tsop-system-admin"
     data-api-base="<?= $apiBase ?>"
     data-user-id="<?= $userIdAttr ?>">
  <div class="tsop-topbar">
    <div>
      <h1>Sistem Yöneticisi <span style="font-size:.55em;font-weight:500;color:#4338ca;background:#eef2ff;border:1px solid #c7d2fe;padding:.18em .55em;border-radius:999px;vertical-align:middle;margin-left:.4em;">AI Operatör Merkezi</span></h1>
      <p>Doğal Türkçe ile tüm kuralları, kampanyaları, ürünleri ve mağaza operasyonlarını yönet. Sohbet conversational rule edit + conflict resolution + business analytics ile bağlı; "şu kuralı pasifleştir", "stok düşüşünde ne yapıyoruz" gibi her şeyi sor.</p>
    </div>
    <div class="tsop-top-actions">
      <input type="text" id="tsop-date-range" value="15.05.2026 - 15.05.2026" />
      <button id="tsop-filter-btn" type="button"><i data-lucide="filter"></i>Filtrele</button>
    </div>
  </div>

  <div class="tsop-shell" style="grid-template-columns: 280px minmax(0, 1fr);">
    <!-- SOL PANEL — Chat geçmişi -->
    <aside class="tsop-left" style="display:flex; flex-direction:column; gap:0.5rem; min-height:0;">
      <button type="button" id="tsop-new-chat"
              style="display:flex; align-items:center; justify-content:center; gap:0.4rem; width:100%; padding:0.6rem 0.8rem; background:#111827; color:#fff; border:0; border-radius:0.55rem; cursor:pointer; font-weight:600; font-size:0.9rem;">
        <span style="font-size:1.05rem; line-height:1;">+</span><span>Yeni Sohbet</span>
      </button>
      <div id="tsop-chat-history-list"
           style="flex:1 1 auto; overflow-y:auto; display:flex; flex-direction:column; gap:0.3rem; padding-right:0.15rem;">
        <p style="color:#9ca3af; padding:0.5rem; font-size:0.85rem;">Yükleniyor…</p>
      </div>
    </aside>

    <!-- ORTA PANEL — Chat akışı (mevcut yapı korunur) -->
    <main class="tsop-center">
      <div class="tsop-chat-tabs">
        <button class="is-active" data-chat-tab="chat" type="button">Sohbet</button>
        <button data-chat-tab="operations" type="button">Operasyonlar</button>
        <button data-chat-tab="history" type="button">Gecmis</button>
      </div>
      <div class="tsop-ai-modes">
        <button class="is-active" data-ai-mode="analiz" type="button">Analiz</button>
        <button data-ai-mode="operasyon" type="button">Operasyon</button>
        <button data-ai-mode="strateji" type="button">Strateji</button>
        <button data-ai-mode="icerik" type="button">Icerik</button>
      </div>
      <div class="tsop-ai-presence" id="tsop-ai-presence" data-presence="idle" aria-live="polite">
        <span class="tsop-ai-presence-dot" aria-hidden="true"></span>
        <span id="tsop-ai-presence-text">AI hazir</span>
      </div>

      <section id="tsop-chat-panel" class="tsop-chat-panel">
        <div class="tsws-v2-chat">
          <div class="tsws-v2-chat-log-wrapper">
            <div id="tsws-chat-log" class="tsop-chat-log"></div>
          </div>
          <form id="tsws-chat-form" class="tsop-chat-form">
            <div class="tsop-chat-input-wrap">
              <textarea id="tsws-chat-input" rows="3" placeholder="Bu urunun satislari neden dustu?"></textarea>
              <div class="tsop-chat-tools">
                <button type="button" aria-label="Ek">Ek</button>
                <button type="button" aria-label="Araclar">Arac</button>
                <button type="button" aria-label="Komut">Komut</button>
                <button type="button" aria-label="Ses">Ses</button>
              </div>
            </div>
            <button class="tsop-send" type="submit">Gonder</button>
          </form>
        </div>
      </section>
    </main>
  </div>
</div>
