<?php
declare(strict_types=1);
/** @var string $approvalsHeading */
/** @var string $approvalsIntro */
$approvalsHeading = $approvalsHeading ?? 'Onay Bekleyenler';
$approvalsIntro = $approvalsIntro ?? 'Onay bekleyen içerikleri görüntüleyin ve yönetin.';
$apiBase = htmlspecialchars(app_browser_api_base(), ENT_QUOTES, 'UTF-8');
$token = app_access_token();
$tokAttr = $token !== null ? htmlspecialchars($token, ENT_QUOTES, 'UTF-8') : '';
// orchestration_api `user_id` query param ile auth context kurar; auth gerektirmez.
// Aktif kullanıcı varsa onun id'sini kullan, yoksa DEFAULT_USER_ID=3 (db.py:11) fallback.
$cu = function_exists('app_current_user') ? app_current_user() : null;
$internalUserId = isset($cu['id']) && is_int($cu['id']) ? (string) $cu['id']
    : (isset($cu['user_id']) && is_int($cu['user_id']) ? (string) $cu['user_id'] : '3');
$internalUserIdAttr = htmlspecialchars($internalUserId, ENT_QUOTES, 'UTF-8');
?>
<div id="sm-app" class="sm-studio-embed-host" aria-hidden="true"></div>
<div class="sm-premium-page">
  <header class="sm-premium-page__header">
    <div>
      <h1 class="sm-premium-page__title"><?= htmlspecialchars($approvalsHeading, ENT_QUOTES, 'UTF-8') ?></h1>
      <p class="sm-premium-page__subtitle">
        <?= htmlspecialchars($approvalsIntro, ENT_QUOTES, 'UTF-8') ?>
        Toplam bekleyen: <strong data-approvals-total>0</strong>
      </p>
    </div>
    <button type="button" class="sm-premium-btn sm-premium-btn--primary" data-act="approvals-create">+ Yeni içerik oluştur</button>
  </header>
  <div id="approvals-root"></div>

  <!-- Sistem Onayları (LangGraph / orchestration_api) — dinamik sekme -->
  <section
    id="internal-approvals-root"
    class="ia-block"
    data-api-base="<?= $apiBase ?>"
    data-token="<?= $tokAttr ?>"
    data-user-id="<?= $internalUserIdAttr ?>"
    style="margin-top:2.5rem; padding:1.5rem; border:1px solid #e5e7eb; border-radius:1rem; background:#fafafa;"
  >
    <header style="margin-bottom:1rem;">
      <h2 style="margin:0; font-size:1.25rem;">Kural Tabanlı Onaylar</h2>
      <p style="margin:0.25rem 0 0; color:#6b7280; font-size:0.9rem;">
        LangGraph kurallarından üretilen onay istekleri. Sekmeler approval_type'a göre dinamik.
      </p>
    </header>
    <nav id="ia-tabs" style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-bottom:1rem;"></nav>
    <div id="ia-list" style="display:flex; flex-direction:column; gap:0.75rem;">
      <p style="color:#9ca3af;">Yükleniyor…</p>
    </div>
  </section>
</div>
<script type="module" src="<?= htmlspecialchars(app_url('/assets/js/internal-approvals.js'), ENT_QUOTES, 'UTF-8') ?>" defer></script>
