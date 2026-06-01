<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></h1>
    <p class="mt-3 text-gray-600"><?= htmlspecialchars(t('settingsIntro'), ENT_QUOTES, 'UTF-8') ?></p>

    <div class="mt-8 grid gap-4 sm:grid-cols-2">
      <section class="rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
        <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('security'), ENT_QUOTES, 'UTF-8') ?></h2>
        <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('securityDesc'), ENT_QUOTES, 'UTF-8') ?></p>
      </section>
      <section class="rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
        <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('preferences'), ENT_QUOTES, 'UTF-8') ?></h2>
        <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('preferencesDesc'), ENT_QUOTES, 'UTF-8') ?></p>
        <form method="post" action="<?= htmlspecialchars(app_url('/settings'), ENT_QUOTES, 'UTF-8') ?>" class="mt-4">
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

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-4">
        <div class="min-w-0 flex-1">
          <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsHolidayYearlyTitle'), ENT_QUOTES, 'UTF-8') ?></h2>
          <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('settingsHolidayYearlyIntro'), ENT_QUOTES, 'UTF-8') ?></p>
        </div>
        <button
          type="button"
          id="st-holiday-master"
          role="switch"
          aria-checked="false"
          aria-label="<?= htmlspecialchars(t('settingsHolidayYearlyMasterToggleAria'), ENT_QUOTES, 'UTF-8') ?>"
          class="relative mt-1 inline-flex h-8 w-14 shrink-0 cursor-pointer items-center rounded-full bg-gray-300 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-500"
        >
          <span aria-hidden class="inline-block h-7 w-7 translate-x-1 transform rounded-full bg-white shadow transition motion-reduce:transition-none"></span>
        </button>
      </div>
      <p class="mt-4 text-xs leading-relaxed text-gray-500"><?= htmlspecialchars(t('settingsHolidayYearlyHelp'), ENT_QUOTES, 'UTF-8') ?></p>
      <p class="mt-2 text-xs font-medium uppercase tracking-wide text-gray-500"><?= htmlspecialchars(t('settingsHolidayYearlyGridTitle'), ENT_QUOTES, 'UTF-8') ?></p>
      <div id="st-holiday-grid" class="mt-3 grid gap-2 sm:grid-cols-2"></div>
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsPromptProThresholdTitle'), ENT_QUOTES, 'UTF-8') ?></h2>
      <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('settingsPromptProThresholdHelp'), ENT_QUOTES, 'UTF-8') ?></p>
      <div class="mt-4 rounded-xl border border-gray-200 bg-white px-3 py-3">
        <div class="flex items-center justify-between text-xs text-gray-600">
          <span>0</span>
          <span id="st-threshold-label" class="font-semibold text-gray-900">300</span>
          <span>3000</span>
        </div>
        <input id="st-threshold" type="range" min="0" max="3000" step="10" value="300" class="mt-2 w-full" />
      </div>
      <p id="st-threshold-hint" class="mt-2 text-xs text-gray-500"></p>
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsOpenAiKey'), ENT_QUOTES, 'UTF-8') ?></h2>
      <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('settingsOpenAiHelp'), ENT_QUOTES, 'UTF-8') ?></p>
      <input id="st-openai" type="password" autocomplete="off" placeholder="sk-…" class="mt-4 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" />
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(t('settingsFalKey'), ENT_QUOTES, 'UTF-8') ?></h2>
      <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(t('settingsFalHelp'), ENT_QUOTES, 'UTF-8') ?></p>
      <input id="st-fal" type="password" autocomplete="off" placeholder="fal_…" class="mt-4 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" />
    </section>
  </div>
</div>

<div id="st-holiday-editor" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/40 p-4" role="presentation"></div>
