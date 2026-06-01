<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-md items-center">
    <div class="w-full rounded-[2rem] border border-gray-200 bg-white p-6 shadow-xl shadow-gray-200/70 sm:p-8">
      <h1 class="text-2xl font-bold text-gray-900">Sifre yenileme</h1>
      <p class="mt-3 text-sm text-gray-600">
        Bu baglanti turu (eski e-posta sifirlama linki) artik kullanilmiyor. Giris icin
        <a href="<?= htmlspecialchars(app_url('/login'), ENT_QUOTES, 'UTF-8') ?>" class="font-medium text-gray-900 underline">giris sayfasina</a>
        don; gerekirse yeni hesap ac.
      </p>
    </div>
  </div>
</div>
