<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-md items-center">
    <div class="w-full rounded-[2rem] border border-gray-200 bg-white p-6 shadow-xl shadow-gray-200/70 sm:p-8">
      <h1 class="text-2xl font-bold text-gray-900">E-postani kontrol et</h1>
      <p class="mt-3 text-sm text-gray-600">
        Sifre yenileme artik e-postana gelen <strong>link</strong> ile yapiliyor. Gelen kutunu (ve spam klasorunu)
        kontrol et, linke tikla; yeni sifreni belirleme sayfasi acilacak.
      </p>
      <div class="mt-6 flex flex-col gap-3">
        <a href="<?= htmlspecialchars(app_url('/forgot-password'), ENT_QUOTES, 'UTF-8') ?>" class="w-full rounded-2xl bg-gray-900 py-3 text-center font-medium text-white transition hover:bg-gray-800">Linki tekrar gonder</a>
        <a href="<?= htmlspecialchars(app_url('/login'), ENT_QUOTES, 'UTF-8') ?>" class="w-full rounded-2xl border border-gray-200 bg-white py-3 text-center font-medium text-gray-900 transition hover:bg-gray-50">Giris sayfasina don</a>
      </div>
    </div>
  </div>
</div>
