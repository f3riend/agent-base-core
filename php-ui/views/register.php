<?php
declare(strict_types=1);
/** @var string|null $err */
?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-md rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/70">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500">Hesap olustur</p>
    <h1 class="mt-2 text-3xl font-bold text-gray-900 mb-2">Kayit Ol</h1>
    <p class="mb-6 text-sm text-gray-500">Kullanici adi ve sifre ile hesap ac.</p>

    <?php if (!empty($err)): ?>
      <div class="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><?= htmlspecialchars($err, ENT_QUOTES, 'UTF-8') ?></div>
    <?php endif; ?>

    <form method="post" action="<?= htmlspecialchars(app_url('/register'), ENT_QUOTES, 'UTF-8') ?>" class="space-y-4">
      <div>
        <label class="mb-2 block text-sm font-medium text-gray-700">Kullanici adi</label>
        <input
          type="text"
          name="username"
          value="<?= htmlspecialchars((string) ($_POST['username'] ?? ''), ENT_QUOTES, 'UTF-8') ?>"
          required
          minlength="3"
          maxlength="64"
          autocomplete="username"
          placeholder="ornek_kullanici"
          class="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-900 outline-none transition focus:border-gray-400 focus:bg-white focus:ring-4 focus:ring-gray-200"
        />
        <p class="mt-1.5 text-xs text-gray-500">3-64 karakter: harf, rakam, alt cizgi (_)</p>
      </div>
      <div>
        <label class="mb-2 block text-sm font-medium text-gray-700">Sifre</label>
        <input
          type="password"
          name="password"
          required
          minlength="6"
          autocomplete="new-password"
          class="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-900 outline-none transition focus:border-gray-400 focus:bg-white focus:ring-4 focus:ring-gray-200"
        />
      </div>
      <div>
        <label class="mb-2 block text-sm font-medium text-gray-700">Sifre Tekrar</label>
        <input
          type="password"
          name="confirm"
          required
          minlength="6"
          autocomplete="new-password"
          class="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-gray-900 outline-none transition focus:border-gray-400 focus:bg-white focus:ring-4 focus:ring-gray-200"
        />
      </div>

      <button type="submit" class="w-full rounded-2xl bg-gray-900 py-3 text-white font-medium transition hover:bg-gray-800">Kayit Ol</button>
    </form>

    <p class="mt-6 text-center text-sm text-gray-500">
      Zaten hesabin var mi?
      <a href="<?= htmlspecialchars(app_url('/login'), ENT_QUOTES, 'UTF-8') ?>" class="font-medium text-gray-900 underline decoration-gray-300 underline-offset-4">Giris Yap</a>
    </p>
  </div>
</div>
