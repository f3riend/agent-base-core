<?php
declare(strict_types=1);
/** @var string|null $err */
?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-md items-center">
    <div class="w-full rounded-[2rem] border border-gray-200 bg-white p-6 shadow-xl shadow-gray-200/70 sm:p-8">
      <div class="mb-8">
        <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">Oturum ac</p>
        <h2 class="mt-2 text-3xl font-bold text-gray-900">Giris Yap</h2>
        <p class="mt-2 text-sm text-gray-500">Kullanici adi ve sifre ile gir.</p>
      </div>

      <form method="post" action="<?= htmlspecialchars(app_url('/login'), ENT_QUOTES, 'UTF-8') ?>" class="space-y-5">
        <div>
          <label class="mb-2 block text-sm font-medium text-gray-700">Kullanici adi</label>
          <input
            type="text"
            name="username"
            value="<?= htmlspecialchars((string) ($_POST['username'] ?? ''), ENT_QUOTES, 'UTF-8') ?>"
            placeholder="ornek_kullanici"
            autocomplete="username"
            class="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-900 outline-none transition focus:border-gray-400 focus:bg-white focus:ring-4 focus:ring-gray-200"
            required
            minlength="3"
            maxlength="64"
          />
          <p class="mt-1.5 text-xs text-gray-500">3-64 karakter: harf, rakam, alt cizgi (_)</p>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between gap-3">
            <label class="block text-sm font-medium text-gray-700">Sifre</label>
            <a href="<?= htmlspecialchars(app_url('/forgot-password'), ENT_QUOTES, 'UTF-8') ?>" class="text-sm font-medium text-gray-600 transition hover:text-gray-900">Sifremi unuttum</a>
          </div>
          <input
            type="password"
            name="password"
            placeholder="••••••••"
            autocomplete="current-password"
            class="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-900 outline-none transition focus:border-gray-400 focus:bg-white focus:ring-4 focus:ring-gray-200"
            required
            minlength="6"
          />
        </div>

        <?php if (!empty($err)): ?>
          <p class="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><?= htmlspecialchars($err, ENT_QUOTES, 'UTF-8') ?></p>
        <?php endif; ?>

        <button type="submit" class="w-full rounded-2xl bg-gray-900 py-3 font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50">Giris Yap</button>
      </form>

      <p class="mt-6 text-center text-sm text-gray-500">
        Hesabin yok mu?
        <a href="<?= htmlspecialchars(app_url('/register'), ENT_QUOTES, 'UTF-8') ?>" class="font-medium text-gray-900 underline decoration-gray-300 underline-offset-4">Kayit Ol</a>
      </p>
    </div>
  </div>
</div>
