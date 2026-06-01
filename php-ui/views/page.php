<?php
declare(strict_types=1);
/** @var string $pageId */
/** @var string|null $timelineSlug */
$timelineSlug = isset($timelineSlug) ? (string) $timelineSlug : '';
?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl">
    <?php if ($timelineSlug !== ''): ?>
      <?php include __DIR__ . '/timeline/_rules_toolbar.php'; ?>
    <?php endif; ?>
    <div class="rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
      <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">Dinamik sayfa</p>
      <h1 class="mt-2 text-3xl font-semibold text-gray-900">Sayfa <?= htmlspecialchars($pageId, ENT_QUOTES, 'UTF-8') ?></h1>
      <p class="mt-3 text-gray-600">Bu alan secilen kayit veya icerik detayi icin hazirlandi.</p>
    </div>
  </div>
</div>
