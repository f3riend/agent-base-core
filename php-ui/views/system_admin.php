<?php declare(strict_types=1); ?>
<div class="tsop-page">
  <div class="tsop-topbar">
    <div>
      <h1>Sistem Yöneticisi <span style="font-size:.55em;font-weight:500;color:#4338ca;background:#eef2ff;border:1px solid #c7d2fe;padding:.18em .55em;border-radius:999px;vertical-align:middle;margin-left:.4em;">AI Operatör Merkezi</span></h1>
      <p>Doğal Türkçe ile tüm kuralları, kampanyaları, ürünleri ve mağaza operasyonlarını yönet. Aşağıdaki sohbet conversational rule edit + conflict resolution + business analytics ile bağlı; "şu kuralı pasifleştir", "stok düşüşünde ne yapıyoruz" gibi her şeyi sor.</p>
    </div>
    <div class="tsop-top-actions">
      <input type="text" id="tsop-date-range" value="15.05.2026 - 15.05.2026" />
      <button id="tsop-filter-btn" type="button"><i data-lucide="filter"></i>Filtrele</button>
    </div>
  </div>

  <div class="tsop-shell">
    <aside class="tsop-left">
      <div class="tsop-left-toolbar">
        <input id="tsws-search" type="search" placeholder="Urun ara..." />
        <select id="tsws-store-filter"></select>
        <button id="tsop-add-product" type="button">+ Urun Ekle</button>
      </div>
      <div class="tsop-multi-bar">
        <span id="tsop-multi-count">0 secili</span>
        <div>
          <button data-bulk-action="analyze_reviews" type="button">Toplu Analiz</button>
          <button data-bulk-action="create_campaign" type="button">Toplu Kampanya</button>
          <button data-bulk-action="generate_banner" type="button">Toplu Banner</button>
        </div>
      </div>
      <div id="tsws-products-grid" class="tsop-product-list"></div>
    </aside>

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

    <aside class="tsop-right">
      <div class="tsop-product-head">
        <div class="tsop-product-thumb" id="tsop-selected-thumb"></div>
        <div>
          <h3 id="tsws-selected-product-name">Urun sec</h3>
          <p id="tsws-selected-product-kicker">Kategori</p>
        </div>
        <span class="tsop-status-pill">Stokta</span>
      </div>

      <section id="tsop-panel-overview" class="tsop-pane tsop-pane-active tsop-pane-minimal">
        <div id="tsws-tab-content" class="tsop-tab-content"></div>
      </section>

      <section id="tsop-panel-reviews" class="tsop-pane tsop-pane-active tsop-pane-minimal">
        <h4>Yorumlar</h4>
        <div id="tsop-reviews-list" class="tsop-list"></div>
        <form id="tsop-review-form" class="tsop-inline-form">
          <input id="tsop-review-author" placeholder="Yazar" />
          <input id="tsop-review-rating" type="number" min="1" max="5" step="1" placeholder="Puan (1-5)" />
          <input id="tsop-review-comment" placeholder="Yorum" required />
          <button type="submit">Yorum Ekle</button>
        </form>
      </section>

      <section id="tsop-panel-tickets" class="tsop-pane tsop-pane-active tsop-pane-minimal">
        <h4>Aktif Destek Kayitlari</h4>
        <div id="tsop-ticket-list" class="tsop-list"></div>
        <form id="tsop-ticket-form" class="tsop-inline-form">
          <input id="tsop-ticket-title" placeholder="Baslik" required />
          <input id="tsop-ticket-issue" placeholder="Issue Type (delivery/return/payment...)" required />
          <input id="tsop-ticket-detail" placeholder="Detay" />
          <button type="submit">Kayit Ekle</button>
        </form>
      </section>

      <details class="tsop-right-drawer">
        <summary>Ek baglam (AI icgorusu ve operasyon detayi)</summary>
        <section id="tsop-panel-insights" class="tsop-pane tsop-pane-active">
          <h4>AI Icgoruleri</h4>
          <div id="tsws-insight-list" class="tsop-list"></div>
        </section>
        <section id="tsop-panel-faq" class="tsop-pane tsop-pane-active">
          <h4>Sik Sorulan Sorular</h4>
          <div id="tsop-faq-list" class="tsop-list"></div>
          <form id="tsop-faq-form" class="tsop-inline-form">
            <input id="tsop-faq-question" placeholder="Soru" required />
            <input id="tsop-faq-answer" placeholder="Cevap" required />
            <button type="submit">SSS Ekle</button>
          </form>
        </section>
        <section id="tsop-panel-operations" class="tsop-pane tsop-pane-active">
          <h4>Bekleyen Adimlar <span id="tsws-pending-count">0</span></h4>
          <div id="tsws-pending-list" class="tsop-list"></div>
          <h4>Canli Akis <span id="tsws-event-count">0</span></h4>
          <div id="tsws-event-feed" class="tsop-list"></div>
          <h4>Operasyon Akisi <span id="tsws-timeline-count">0</span></h4>
          <div id="tsws-operation-timeline" class="tsop-list"></div>
        </section>
        <section id="tsop-panel-history" class="tsop-pane tsop-pane-active">
          <h4>Gecmis Operasyonlar</h4>
          <div id="tsop-history-list" class="tsop-list"></div>
        </section>
      </details>
    </aside>
  </div>
</div>

<div id="tsop-product-modal" class="tsop-modal" hidden>
  <form id="tsop-product-form" class="tsop-modal-card">
    <h3>Urun Ekle</h3>
    <input id="tsop-product-name" placeholder="Urun adi" required />
    <input id="tsop-product-category" placeholder="Kategori" />
    <input id="tsop-product-price" type="number" min="0" step="0.01" placeholder="Fiyat" />
    <input id="tsop-product-stock" type="number" min="0" step="1" placeholder="Stok" />
    <input id="tsop-product-images" placeholder="Gorsel URL (virgulle ayir)" />
    <textarea id="tsop-product-description" rows="3" placeholder="Urun aciklamasi"></textarea>
    <div class="tsop-modal-actions">
      <button type="button" data-close-modal="product">Iptal</button>
      <button type="submit">Kaydet</button>
    </div>
  </form>
</div>

<div id="tsws-context-menu" class="tsws-context-menu" hidden>
  <button type="button" data-context-action="analyze_reviews">Urunu Analiz Et</button>
  <button type="button" data-context-action="generate_banner">Banner Olustur</button>
  <button type="button" data-context-action="create_campaign">Kampanya Olustur</button>
  <button type="button" data-context-action="view_timeline">Zaman Akisini Ac</button>
  <button type="button" data-context-action="open_chat">AI Sohbeti Ac</button>
</div>
