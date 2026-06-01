<?php
declare(strict_types=1);
/** @var string $title */
/** @var string|null $extraHead */
app_session_start();
$bp = app_base_path();
$api = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
$token = app_access_token();
$tokAttr = $token !== null ? htmlspecialchars($token, ENT_QUOTES, 'UTF-8') : '';
$loc = app_ui_locale();
$cu = app_current_user();
$userJson = json_encode(
    $cu !== null ? ['username' => $cu['username'], 'uid' => $cu['uid']] : null,
    JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
);
$stringsJson = json_encode(app_ui_strings_blob(), JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
$holidayCountry = htmlspecialchars(
    (string) (getenv('VITE_HOLIDAY_COUNTRY') ?: getenv('APP_HOLIDAY_COUNTRY') ?: 'TR'),
    ENT_QUOTES,
    'UTF-8',
);
$requestPath = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$requestPath = is_string($requestPath) ? $requestPath : '/';
if ($bp !== '' && str_starts_with($requestPath, $bp)) {
    $requestPath = substr($requestPath, strlen($bp)) ?: '/';
}
if ($requestPath === '' || $requestPath[0] !== '/') {
    $requestPath = '/' . $requestPath;
}
$isAuthed = app_access_token() !== null;
$isTimeline = str_starts_with($requestPath, '/page/timeline/');
$isTriggers = $requestPath === '/triggers';
$isStores = str_starts_with($requestPath, '/stores');
$isSocialMain = $requestPath === '/social-media';
$isSocialNav = str_starts_with($requestPath, '/social-media');
$socialNavSlug = '';
if ($isSocialNav && $requestPath !== '/social-media') {
    $socialNavSlug = trim(substr($requestPath, strlen('/social-media/')), '/');
}
$isSystemAdmin = $requestPath === '/social-media/system-admin';
$isCampaignManager = str_starts_with($requestPath, '/campaign-management');
$campaignNavSlug = '';
if ($isCampaignManager && $requestPath !== '/campaign-management') {
    $campaignNavSlug = trim(substr($requestPath, strlen('/campaign-management/')), '/');
}
$isSettings = str_starts_with($requestPath, '/settings');
$timelineItems = [
    'all' => 'Tümü',
    'orders' => 'Siparişler',
    'products' => 'Ürünler',
    'reviews' => 'Değerlendirmeler',
    'questions' => 'Sorular',
    'coupons' => 'Kuponlar',
    'campaigns' => 'Kampanyalar',
    'ads' => 'Reklamlar',
    'staff' => 'Çalışanlar',
    'messages' => 'Mesajlar',
    'stock' => 'Stok',
    'checkin-checkout' => 'Giriş/Çıkış',
    'store' => 'Mağaza Sayfası',
    'returns' => 'İadeler',
    'withdrawals' => 'Para Çekme',
    'discounts' => 'İndirimler',
    'plugins' => 'Eklentiler',
    'subscription' => 'Abonelik',
    'delivery' => 'Teslimat',
    'banners' => 'Bannerlar',
    'flash-sales' => 'Flash Satış',
    'components' => 'Bileşenler',
];
$timelineSection = null;
if ($isTimeline) {
    $timelineSection = substr($requestPath, strlen('/page/timeline/'));
    $timelineSection = is_string($timelineSection) ? trim($timelineSection, '/') : null;
}
$settingsSection = 'account';
if ($isSettings) {
    $parts = explode('/', trim($requestPath, '/'));
    if (isset($parts[1]) && $parts[1] !== '') {
        $settingsSection = $parts[1];
    }
}
?>
<!DOCTYPE html>
<html lang="<?= $loc === 'en' ? 'en' : 'tr' ?>">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title><?= htmlspecialchars($title, ENT_QUOTES, 'UTF-8') ?></title>
  <meta name="color-scheme" content="light dark">
  <?php if ($isAuthed): ?>
  <script>
    (function () {
      try {
        var raw = localStorage.getItem("app_shell_sidebar_collapsed_v1");
        var collapsed = raw === "1" || raw === "true";
        if (collapsed) document.documentElement.classList.add("app-shell-collapsed");
      } catch (e) {
        /* ignore */
      }
    })();
  </script>
  <?php endif; ?>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="<?= htmlspecialchars(app_url('/assets/css/app.css'), ENT_QUOTES, 'UTF-8') ?>">
  <?php if ($isAuthed): ?>
  <link rel="stylesheet" href="<?= htmlspecialchars(app_url('/assets/css/sm-premium-ui.css'), ENT_QUOTES, 'UTF-8') ?>">
  <?php endif; ?>
  <script>
    window.__AGENTBASE__ = {
      apiBase: "<?= $api ?>",
      basePath: "<?= htmlspecialchars($bp, ENT_QUOTES, 'UTF-8') ?>",
      accessToken: "<?= $tokAttr ?>",
      uiLocale: "<?= htmlspecialchars($loc, ENT_QUOTES, 'UTF-8') ?>",
      holidayCountry: "<?= $holidayCountry ?>",
      user: <?= $userJson ?>
    };
    window.__UI_STRINGS__ = <?= $stringsJson ?>;
  </script>
  <script defer src="https://unpkg.com/lucide@latest"></script>
  <?php if ($isAuthed): ?>
    <script defer src="<?= htmlspecialchars(app_url('/assets/js/app-shell.js'), ENT_QUOTES, 'UTF-8') ?>"></script>
  <?php endif; ?>
  <?= $extraHead ?? '' ?>
</head>
<body>
<?php if (!$isAuthed): ?>
<?= $content ?? '' ?>
<?php else: ?>
<div class="app-shell" data-app-shell>
  <aside class="app-sidebar" data-sidebar aria-label="Workspace Sidebar">
    <div class="app-sidebar__top">
      <div class="app-sidebar__brand">
        <div class="app-sidebar__brand-mark">AB</div>
        <div class="app-sidebar__brand-copy">
          <p class="app-sidebar__brand-title">Agent Base</p>
          <p class="app-sidebar__brand-sub"><?= htmlspecialchars($cu['username'] ?? '', ENT_QUOTES, 'UTF-8') ?></p>
        </div>
      </div>
      <button type="button" class="app-sidebar__toggle" data-sidebar-toggle aria-label="Toggle Sidebar">
        <i data-lucide="panel-left-close"></i>
      </button>
    </div>

    <nav class="app-sidebar__nav">
      <div class="app-nav-group<?= $isSocialNav ? ' is-open' : '' ?>" data-group data-group-id="social-media">
        <button type="button" class="app-nav-group__trigger<?= $isSocialNav ? ' is-active' : '' ?>" data-group-toggle title="<?= htmlspecialchars(t('socialMediaMenu'), ENT_QUOTES, 'UTF-8') ?>">
          <div class="app-nav-group__label">
            <i data-lucide="megaphone"></i>
            <span><?= htmlspecialchars(t('socialMediaMenu'), ENT_QUOTES, 'UTF-8') ?></span>
          </div>
          <i data-lucide="chevron-right" class="app-nav-group__chevron"></i>
        </button>
        <div class="app-nav-group__children">
          <a href="<?= htmlspecialchars(app_url('/social-media'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSocialMain ? ' is-active' : '' ?>"><i data-lucide="calendar-days"></i><span>Takvim</span></a>
          <a href="<?= htmlspecialchars(app_url('/social-media/etiketler'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $socialNavSlug === 'etiketler' ? ' is-active' : '' ?>"><i data-lucide="tags"></i><span>Etiketler</span></a>
          <a href="<?= htmlspecialchars(app_url('/social-media/sablonlar'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $socialNavSlug === 'sablonlar' ? ' is-active' : '' ?>"><i data-lucide="layout-template"></i><span>Şablonlar</span></a>
          <a href="<?= htmlspecialchars(app_url('/social-media/onay-bekleyenler'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $socialNavSlug === 'onay-bekleyenler' ? ' is-active' : '' ?>"><i data-lucide="check-square"></i><span>Onay Bekleyenler</span></a>
        </div>
      </div>

      <div class="app-nav-group<?= $isCampaignManager ? ' is-open' : '' ?>" data-group data-group-id="campaign-management">
        <button type="button" class="app-nav-group__trigger<?= $isCampaignManager ? ' is-active' : '' ?>" data-group-toggle title="Kampanya Yönetimi">
          <div class="app-nav-group__label">
            <i data-lucide="briefcase-business"></i>
            <span>Kampanya Yönetimi</span>
          </div>
          <i data-lucide="chevron-right" class="app-nav-group__chevron"></i>
        </button>
        <div class="app-nav-group__children">
          <a href="<?= htmlspecialchars(app_url('/campaign-management'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $requestPath === '/campaign-management' ? ' is-active' : '' ?>"><i data-lucide="calendar-days"></i><span>Takvim</span></a>
          <a href="<?= htmlspecialchars(app_url('/campaign-management/sablonlar'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $campaignNavSlug === 'sablonlar' ? ' is-active' : '' ?>"><i data-lucide="layout-template"></i><span>Şablonlar</span></a>
          <a href="<?= htmlspecialchars(app_url('/campaign-management/onay-bekleyenler'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $campaignNavSlug === 'onay-bekleyenler' ? ' is-active' : '' ?>"><i data-lucide="check-square"></i><span>Onay Bekleyenler</span></a>
        </div>
      </div>
      <a href="<?= htmlspecialchars(app_url('/social-media/system-admin'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item<?= $isSystemAdmin ? ' is-active' : '' ?>" title="Sistem Yöneticisi — AI Operatör Merkezi">
        <i data-lucide="shield"></i>
        <span>Sistem Yöneticisi</span>
      </a>

      <div class="app-nav-group<?= $isTimeline ? ' is-open' : '' ?>" data-group data-group-id="timeline">
        <button type="button" class="app-nav-group__trigger<?= $isTimeline ? ' is-active' : '' ?>" data-group-toggle title="Zaman Tüneli">
          <div class="app-nav-group__label">
            <i data-lucide="history"></i>
            <span>Zaman Tüneli</span>
          </div>
          <i data-lucide="chevron-right" class="app-nav-group__chevron"></i>
        </button>
        <div class="app-nav-group__children">
          <?php foreach ($timelineItems as $slug => $label): ?>
            <a
              href="<?= htmlspecialchars(app_url('/page/timeline/' . $slug), ENT_QUOTES, 'UTF-8') ?>"
              class="app-nav-item app-nav-item--child<?= $isTimeline && $timelineSection === $slug ? ' is-active' : '' ?>"
            >
              <i data-lucide="dot"></i>
              <span><?= htmlspecialchars($label, ENT_QUOTES, 'UTF-8') ?></span>
            </a>
          <?php endforeach; ?>
        </div>
      </div>

      <a href="<?= htmlspecialchars(app_url('/stores'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item<?= $isStores ? ' is-active' : '' ?>" title="Mağazalar — Mağaza & ürün yönetimi">
        <i data-lucide="store"></i>
        <span>Mağazalar</span>
      </a>

      <a href="<?= htmlspecialchars(app_url('/triggers'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item<?= $isTriggers ? ' is-active' : '' ?>" title="Tetikleyiciler — Otomatik kurallar">
        <i data-lucide="zap"></i>
        <span>Tetikleyiciler</span>
      </a>

      <div class="app-nav-group<?= $isSettings ? ' is-open' : '' ?>" data-group data-group-id="settings">
        <button type="button" class="app-nav-group__trigger<?= $isSettings ? ' is-active' : '' ?>" data-group-toggle title="<?= htmlspecialchars(t('settingsNavLabel'), ENT_QUOTES, 'UTF-8') ?>">
          <div class="app-nav-group__label">
            <i data-lucide="settings-2"></i>
            <span><?= htmlspecialchars(t('settingsNavLabel'), ENT_QUOTES, 'UTF-8') ?></span>
          </div>
          <i data-lucide="chevron-right" class="app-nav-group__chevron"></i>
        </button>
        <div class="app-nav-group__children">
          <a href="<?= htmlspecialchars(app_url('/settings/account'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'account' ? ' is-active' : '' ?>"><i data-lucide="user-circle-2"></i><span>Hesap</span></a>
          <a href="<?= htmlspecialchars(app_url('/settings/workspace'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'workspace' ? ' is-active' : '' ?>"><i data-lucide="folders"></i><span>Çalışma Alanı</span></a>
          <a href="<?= htmlspecialchars(app_url('/settings/ai'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'ai' ? ' is-active' : '' ?>"><i data-lucide="bot"></i><span>Yapay Zeka</span></a>
          <a href="<?= htmlspecialchars(app_url('/settings/api-keys'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'api-keys' ? ' is-active' : '' ?>"><i data-lucide="key-round"></i><span>Anahtarlar & Kullanım</span></a>
          <a href="<?= htmlspecialchars(app_url('/settings/automation'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'automation' ? ' is-active' : '' ?>"><i data-lucide="workflow"></i><span>Otomasyon</span></a>
          <a href="<?= htmlspecialchars(app_url('/settings/security'), ENT_QUOTES, 'UTF-8') ?>" class="app-nav-item app-nav-item--child<?= $isSettings && $settingsSection === 'security' ? ' is-active' : '' ?>"><i data-lucide="shield-check"></i><span>Güvenlik</span></a>
        </div>
      </div>
    </nav>

    <div class="app-sidebar__bottom">
      <form method="post" action="<?= htmlspecialchars(app_url('/logout'), ENT_QUOTES, 'UTF-8') ?>">
        <button type="submit" class="app-nav-item app-nav-item--ghost" title="<?= htmlspecialchars(t('signOut'), ENT_QUOTES, 'UTF-8') ?>">
          <i data-lucide="log-out"></i>
          <span><?= htmlspecialchars(t('signOut'), ENT_QUOTES, 'UTF-8') ?></span>
        </button>
      </form>
    </div>
  </aside>
  <button type="button" class="app-sidebar-handle" data-sidebar-handle aria-label="Toggle Sidebar" aria-expanded="true">
    <i data-lucide="chevron-right"></i>
  </button>
  <div class="app-sidebar-backdrop" data-sidebar-backdrop aria-hidden="true"></div>

  <main class="app-shell__content" data-app-content>
    <?= $content ?? '' ?>
  </main>
</div>
<?php endif; ?>
</body>
</html>
