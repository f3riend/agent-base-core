<?php
declare(strict_types=1);
/**
 * Tur 5 (Polish-2): Contextual rule paneli.
 * CSS: public/assets/css/timeline-rules.css (index.php extraHead'inde yüklenir)
 * JS:  public/assets/js/timeline-page-rules.js
 *
 * Tüm dinamik Türkçe metinler PHP'de htmlspecialchars'tan geçer. JS
 * tarafında DOM API + template literal + escapeHtml disiplini var.
 *
 * @var string $timelineSlug
 */
$slug = htmlspecialchars($timelineSlug, ENT_QUOTES, 'UTF-8');
$apiBase = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
$token = app_access_token();
$tokAttr = $token !== null ? htmlspecialchars($token, ENT_QUOTES, 'UTF-8') : '';
$appBasePath = function_exists('app_base_path') ? app_base_path() : '';
$cu = function_exists('app_current_user') ? app_current_user() : null;
$userId = isset($cu['id']) && is_int($cu['id'])
    ? (string) $cu['id']
    : (isset($cu['user_id']) && is_int($cu['user_id']) ? (string) $cu['user_id'] : '3');
$userIdAttr = htmlspecialchars($userId, ENT_QUOTES, 'UTF-8');

/* Slug → trigger event_type prefix eşlemesi.
   "all" filter uygulamaz; diğerleri kendi olay namespace'ini gösterir. */
$slugEventMap = [
    'all'              => [],
    'store'            => ['store.'],
    'orders'           => ['order.'],
    'products'         => ['product.'],
    'stock'            => ['stock.'],
    'reviews'          => ['review.'],
    'questions'        => ['customer.question'],
    'coupons'          => [],
    'campaigns'        => ['campaign.'],
    'ads'              => ['banner.', 'sales.'],
    'banners'          => ['banner.'],
    'flash-sales'      => ['sales.'],
    'discounts'        => ['sales.'],
    'staff'            => [],
    'messages'         => ['customer.question'],
    'checkin-checkout' => [],
    'returns'          => ['order.cancelled'],
    'withdrawals'      => [],
    'plugins'          => [],
    'subscription'     => [],
    'delivery'         => ['shipping.', 'order.shipped'],
    'components'       => [],
];

$eventPrefixes = $slugEventMap[$timelineSlug] ?? [];
$eventPrefixesJson = htmlspecialchars(
    json_encode($eventPrefixes, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR),
    ENT_QUOTES,
    'UTF-8'
);

$slugLabels = [
    'all'         => ['title' => 'Tüm Kurallar',              'lead' => 'Tüm sekmeler için aktif olan kurallar — tek bakışta.',                              'placeholder' => 'Örnek: Yeni mağaza oluştuktan 3 gün sonra Çanakkale hesabında Anneler Günü şablonu kullanarak Instagram paylaşımı yap.'],
    'store'       => ['title' => 'Mağaza Kuralları',          'lead' => 'store.* olaylarında tetiklenir.',                                                    'placeholder' => 'Örnek: Yeni mağaza oluştuğunda hoşgeldin postu hazırla ve onay bekle.'],
    'orders'      => ['title' => 'Sipariş Kuralları',         'lead' => 'order.* olaylarında tetiklenir.',                                                    'placeholder' => 'Örnek: 500 TL üzeri sipariş geldiğinde teşekkür kuponu üret.'],
    'products'    => ['title' => 'Ürün Kuralları',            'lead' => 'product.* olaylarında tetiklenir.',                                                  'placeholder' => 'Örnek: Yeni ürün eklendiğinde Instagram lansman postu hazırla.'],
    'stock'       => ['title' => 'Stok Kuralları',            'lead' => 'stock.updated olayında tetiklenir.',                                                 'placeholder' => 'Örnek: Stok 5 altına düştüğünde Instagram indirim postu hazırla.'],
    'reviews'     => ['title' => 'Değerlendirme Kuralları',   'lead' => 'review.* olaylarında tetiklenir.',                                                   'placeholder' => 'Örnek: Negatif değerlendirme geldiğinde müşteri destek thread aç ve özür mesajı taslağı hazırla.'],
    'questions'   => ['title' => 'Müşteri Soru Kuralları',    'lead' => 'customer.question olayında tetiklenir.',                                             'placeholder' => 'Örnek: Müşteri sorduğunda taslak yanıt üret ve onay bekle.'],
    'campaigns'   => ['title' => 'Kampanya Kuralları',        'lead' => 'campaign.* olaylarında tetiklenir.',                                                 'placeholder' => 'Örnek: Yeni kampanya oluştuğunda banner üret ve onayla.'],
    'ads'         => ['title' => 'Reklam Kuralları',          'lead' => 'banner.* ve sales.* olaylarında tetiklenir.',                                        'placeholder' => 'Örnek: Satış güncellendiğinde flash sale banner üret.'],
    'banners'     => ['title' => 'Banner Kuralları',          'lead' => 'banner.updated olayında tetiklenir.',                                                'placeholder' => 'Örnek: Banner güncellendiğinde sosyal medya post hazırla.'],
    'flash-sales' => ['title' => 'Flash Satış Kuralları',     'lead' => 'sales.updated olayında tetiklenir.',                                                 'placeholder' => 'Örnek: Flash satış aktif olduğunda 1 saat sonra hatırlatma postu üret.'],
    'discounts'   => ['title' => 'İndirim Kuralları',         'lead' => 'sales.updated olayında tetiklenir.',                                                 'placeholder' => 'Örnek: İndirim oranı yüksek olduğunda Instagram post hazırla.'],
    'delivery'    => ['title' => 'Teslimat Kuralları',        'lead' => 'shipping.* ve order.shipped olaylarında tetiklenir.',                                'placeholder' => 'Örnek: Kargo gecikti olayı geldiğinde müşteriye özür mesajı gönder.'],
    'returns'     => ['title' => 'İade Kuralları',            'lead' => 'order.cancelled olayında tetiklenir.',                                               'placeholder' => 'Örnek: Sipariş iptal edildiğinde müşteriye geri ödeme bilgisi gönder.'],
    'messages'    => ['title' => 'Mesaj Kuralları',           'lead' => 'customer.question olayında tetiklenir.',                                             'placeholder' => 'Örnek: Yeni müşteri sorusu geldiğinde önceliklendir.'],
];
$labels = $slugLabels[$timelineSlug] ?? [
    'title'       => 'Sayfa Kuralları',
    'lead'        => 'Bu sekme için olay eşlemesi tanımlı değil — tüm kuralları gösterir.',
    'placeholder' => 'Doğal Türkçe ile niyetini yaz.',
];
$titleEsc = htmlspecialchars($labels['title'], ENT_QUOTES, 'UTF-8');
$leadEsc = htmlspecialchars($labels['lead'], ENT_QUOTES, 'UTF-8');
$placeholderEsc = htmlspecialchars($labels['placeholder'], ENT_QUOTES, 'UTF-8');

$adminLink = htmlspecialchars(app_url('/social-media/system-admin'), ENT_QUOTES, 'UTF-8');
?>
<script>window.__APP_BASE_PATH__ = <?= json_encode($appBasePath, JSON_UNESCAPED_UNICODE) ?>;</script>

<div
  id="timeline-rules-mount"
  class="tr-shell"
  data-timeline-slug="<?= $slug ?>"
  data-event-prefixes="<?= $eventPrefixesJson ?>"
  data-api-base="<?= $apiBase ?>"
  data-token="<?= $tokAttr ?>"
  data-user-id="<?= $userIdAttr ?>"
>
  <div class="tr-head">
    <div class="tr-head-text">
      <h2><?= $titleEsc ?></h2>
      <div class="tr-lead"><?= $leadEsc ?></div>
    </div>
    <div class="tr-head-side">
      <span id="tr-count" class="tr-chip">—</span>
      <a class="tr-ai-link" href="<?= $adminLink ?>" title="AI Operatör Merkezi — sohbet ile yönet">
        <span>AI ile yönet</span>
      </a>
    </div>
  </div>

  <div id="tr-conflicts"></div>

  <div class="tr-composer">
    <div class="tr-composer-label">Yeni kural — doğal Türkçe</div>
    <textarea
      id="tr-nl-input"
      placeholder="<?= $placeholderEsc ?>"
    ></textarea>
    <div class="tr-hint">İpucu: <kbd>⌘</kbd>/<kbd>Ctrl</kbd> + <kbd>Enter</kbd> ile hızlı önizle.</div>
    <div class="tr-actions">
      <button type="button" class="tr-btn tr-btn-ghost" data-tr-act="preview">Önizle</button>
      <button type="button" id="tr-save-btn" class="tr-btn tr-btn-accent" data-tr-act="save" disabled>Kuralı Etkinleştir</button>
      <button type="button" class="tr-btn tr-btn-ghost" data-tr-act="clear">Temizle</button>
      <button type="button" class="tr-templates-toggle" data-tr-act="toggle-templates">Şablonlardan seç</button>
    </div>
    <div id="tr-preview" class="tr-preview" style="display:none;"></div>
    <div id="tr-templates-section" class="tr-templates-section" style="display:none;">
      <div class="tr-templates-head">Hazır şablonlar — bu sekme ile uyumlular öne çıkar. Tıkla doldur.</div>
      <div id="tr-templates-grid" class="tr-templates">
        <div class="tr-skeleton"></div>
        <div class="tr-skeleton"></div>
        <div class="tr-skeleton"></div>
      </div>
    </div>
  </div>

  <div id="tr-list" class="tr-list">
    <div class="tr-skeleton"></div>
    <div class="tr-skeleton"></div>
  </div>
</div>
