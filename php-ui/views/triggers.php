<?php
declare(strict_types=1);
/** Tetikleyiciler — Aktif structured_rules listesi.
 * orchestration_api `/api/internal/structured-rules*` endpoint'lerini kullanır.
 */
$apiBase = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
$token = app_access_token();
$tokAttr = $token !== null ? htmlspecialchars($token, ENT_QUOTES, 'UTF-8') : '';
$cu = function_exists('app_current_user') ? app_current_user() : null;
$userId = isset($cu['id']) && is_int($cu['id'])
    ? (string) $cu['id']
    : (isset($cu['user_id']) && is_int($cu['user_id']) ? (string) $cu['user_id'] : '3');
$userIdAttr = htmlspecialchars($userId, ENT_QUOTES, 'UTF-8');
?>
<div class="sm-premium-page">
  <header class="sm-premium-page__header">
    <div>
      <h1 class="sm-premium-page__title">Tetikleyiciler</h1>
      <p class="sm-premium-page__subtitle">Otomatik kurallar ve tetikleyici olaylar.</p>
    </div>
    <a
      href="<?= htmlspecialchars(app_url('/page/timeline/all'), ENT_QUOTES, 'UTF-8') ?>"
      class="sm-premium-btn sm-premium-btn--primary"
    >+ Yeni Kural (Zaman Tüneli)</a>
  </header>

  <section
    id="triggers-root"
    data-api-base="<?= $apiBase ?>"
    data-token="<?= $tokAttr ?>"
    data-user-id="<?= $userIdAttr ?>"
    style="display:flex; flex-direction:column; gap:0.75rem;"
  >
    <p style="color:#9ca3af; padding:1rem;">Yükleniyor…</p>
  </section>
</div>
<script type="module" src="<?= htmlspecialchars(app_url('/assets/js/triggers-app.js'), ENT_QUOTES, 'UTF-8') ?>" defer></script>
