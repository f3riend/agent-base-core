<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-5xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900">Anahtarlar &amp; Kullanım</h1>
    <p class="mt-3 text-gray-600"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Manage model provider credentials and review production cost.' : 'Sağlayıcı kimlik bilgilerinizi yönetin ve üretim maliyetinizi inceleyin.', ENT_QUOTES, 'UTF-8') ?></p>

    <section class="mt-6 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsOpenAiKey'), ENT_QUOTES, 'UTF-8') ?></h2>
      <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Used for image/caption generation. Stored in your workspace database (MySQL), not in browser localStorage.' : 'Görsel/açıklama üretiminde kullanılır. Tarayıcı localStorage yerine çalışma alanı veritabanında (MySQL) saklanır.', ENT_QUOTES, 'UTF-8') ?></p>
      <input id="st-openai" type="password" autocomplete="off" placeholder="sk-…" class="mt-4 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" />
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsFalKey'), ENT_QUOTES, 'UTF-8') ?></h2>
      <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Used for Kling and similar video generation. Stored in your workspace database (MySQL), independent from OpenAI key.' : 'Kling vb. video üretiminde kullanılır. OpenAI anahtarından bağımsızdır ve çalışma alanı veritabanında (MySQL) saklanır.', ENT_QUOTES, 'UTF-8') ?></p>
      <input id="st-fal" type="password" autocomplete="off" placeholder="fal_…" class="mt-4 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" />
    </section>

    <section class="mt-8 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900">Kullanım Bilgileri</h2>
      <p class="mt-2 text-sm leading-6 text-gray-600">OpenAI ve FAL üzerinden yaptığınız görsel, video ve metin üretimlerinin maliyeti.</p>
      <div id="usage-root" class="mt-4">
        <p class="text-sm text-gray-500">Yükleniyor…</p>
      </div>
    </section>

  </div>
</div>
<script type="module" src="<?= htmlspecialchars(app_url('/assets/js/usage-app.js'), ENT_QUOTES, 'UTF-8') ?>"></script>
