<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-md items-center">
    <div class="w-full rounded-[2rem] border border-gray-200 bg-white p-6 shadow-xl shadow-gray-200/70 sm:p-8">
      <h1 class="text-2xl font-bold text-gray-900">Sifremi unuttum</h1>
      <p class="mt-3 text-sm text-gray-600">
        Giris artik MySQL tabanli kullanici adi ve sifre ile yapiliyor. Sifre sifirlama e-postasi henuz devre
        disi. Sifreni unuttuysan veritabaninda sifre hashini yenilemek veya yeni kullanici olusturmak icin sunucu
        yoneticisiyle iletisime gecebilirsin; ileride API uzerinden sifre sifirlama eklenebilir.
      </p>
      <p class="mt-6 text-center text-sm text-gray-500">
        <a href="<?= htmlspecialchars(app_url('/login'), ENT_QUOTES, 'UTF-8') ?>" class="font-medium text-gray-900 underline decoration-gray-300 underline-offset-4">Girise don</a>
      </p>
    </div>
  </div>
</div>
