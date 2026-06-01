<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900">Account</h1>
    <p class="mt-3 text-gray-600"><?= htmlspecialchars(t('settingsIntro'), ENT_QUOTES, 'UTF-8') ?></p>

    <?php if (isset($settingsFlash) && is_array($settingsFlash)): ?>
      <?php
      $flashType = (string) ($settingsFlash['type'] ?? 'info');
      $isSuccess = $flashType === 'success';
      ?>
      <div class="mt-6 rounded-2xl border px-4 py-3 text-sm <?= $isSuccess ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-700' ?>">
        <?= htmlspecialchars((string) ($settingsFlash['message'] ?? ''), ENT_QUOTES, 'UTF-8') ?>
      </div>
    <?php endif; ?>

    <div class="mt-8 grid gap-4 sm:grid-cols-2">
      <section class="rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5 sm:col-span-2">
        <h2 class="text-lg font-medium text-gray-900">Hesap Bilgilerini Guncelle</h2>
        <p class="mt-2 text-sm leading-6 text-gray-600">Guvenlik icin mevcut sifreni girerek kullanici adini ve sifreni degistirebilirsin.</p>
        <form method="post" action="" class="mt-4 space-y-3">
          <input type="hidden" name="settings_action" value="update_credentials">
          <div>
            <label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Yeni kullanici adi (opsiyonel)</label>
            <input
              type="text"
              name="new_username"
              minlength="3"
              maxlength="64"
              autocomplete="username"
              class="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400"
              placeholder="ornek_kullanici"
            >
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Yeni sifre (opsiyonel)</label>
              <input
                type="password"
                name="new_password"
                minlength="6"
                maxlength="128"
                autocomplete="new-password"
                class="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400"
                placeholder="En az 6 karakter"
              >
            </div>
            <div>
              <label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Yeni sifre (tekrar)</label>
              <input
                type="password"
                name="new_password_confirm"
                minlength="6"
                maxlength="128"
                autocomplete="new-password"
                class="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400"
                placeholder="Yeni sifreyi tekrar yaz"
              >
            </div>
          </div>
          <div>
            <label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Mevcut sifre (zorunlu)</label>
            <input
              type="password"
              name="current_password"
              required
              minlength="1"
              maxlength="128"
              autocomplete="current-password"
              class="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400"
              placeholder="Guncelleme icin mevcut sifren"
            >
          </div>
          <button type="submit" class="inline-flex items-center rounded-xl bg-gray-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-gray-800">
            Hesabi guncelle
          </button>
        </form>
      </section>

      <section class="rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
        <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('security'), ENT_QUOTES, 'UTF-8') ?></h2>
        <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('securityDesc'), ENT_QUOTES, 'UTF-8') ?></p>
      </section>
      <section class="rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
        <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('preferences'), ENT_QUOTES, 'UTF-8') ?></h2>
        <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('preferencesDesc'), ENT_QUOTES, 'UTF-8') ?></p>
        <form method="post" action="" class="mt-4">
          <label class="block text-xs font-medium uppercase tracking-wide text-gray-500"><?= htmlspecialchars(t('settingsLanguage'), ENT_QUOTES, 'UTF-8') ?></label>
          <select name="ui_locale" class="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" onchange="this.form.submit()">
            <option value="tr" <?= app_ui_locale() === 'tr' ? 'selected' : '' ?>><?= htmlspecialchars(t('languageTr'), ENT_QUOTES, 'UTF-8') ?></option>
            <option value="en" <?= app_ui_locale() === 'en' ? 'selected' : '' ?>><?= htmlspecialchars(t('languageEn'), ENT_QUOTES, 'UTF-8') ?></option>
          </select>
        </form>
        <p class="mt-3 text-xs leading-relaxed text-gray-600"><?= htmlspecialchars(t('settingsLanguageHelp'), ENT_QUOTES, 'UTF-8') ?></p>
        <p class="mt-2 text-xs leading-relaxed text-gray-500"><?= htmlspecialchars(t('settingsContentLanguageNote'), ENT_QUOTES, 'UTF-8') ?></p>
      </section>
    </div>
  </div>
</div>
