<?php
declare(strict_types=1);
$timelineSlug = 'store';
?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl">
    <?php include __DIR__ . '/_rules_toolbar.php'; ?>
    <div class="rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
      <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">Zaman Tuneli</p>
      <h1 class="mt-2 text-3xl font-semibold text-gray-900">Magaza Sayfasi</h1>
      <p class="mt-3 text-gray-600">Bu sayfa icerik icin ayrildi. Magaza otomasyonu icin <a class="font-medium text-emerald-700 underline" href="<?= htmlspecialchars(app_url('/social-media/system-admin'), ENT_QUOTES, 'UTF-8') ?>">Sistem Yoneticisi</a> sayfasini kullanin.</p>
    </div>
  </div>
</div>
