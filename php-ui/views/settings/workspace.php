<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900">Workspace</h1>
    <p class="mt-3 text-gray-600">Workspace-wide behavior and yearly holiday rules.</p>

    <section class="mt-6 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-4">
        <div class="min-w-0 flex-1">
          <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Social media special days — yearly auto drafts' : 'Sosyal medya özel günleri — yıllık otomatik taslak', ENT_QUOTES, 'UTF-8') ?></h2>
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
      <p class="mt-2 text-xs font-medium uppercase tracking-wide text-gray-500"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Social media special-day rules' : 'Sosyal medya özel günü kuralları', ENT_QUOTES, 'UTF-8') ?></p>
      <div id="st-holiday-grid" class="mt-3 grid gap-2 sm:grid-cols-2"></div>
    </section>

    <section class="mt-6 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-4">
        <div class="min-w-0 flex-1">
          <h2 class="text-lg font-medium text-gray-900"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Campaign special days — yearly auto drafts' : 'Kampanya özel günleri — yıllık otomatik taslak', ENT_QUOTES, 'UTF-8') ?></h2>
          <p class="mt-2 text-sm leading-6 text-gray-600"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Keeps a separate yearly watchlist for Campaign Management special-day campaigns.' : 'Kampanya Yönetimi için özel gün kampanyalarını sosyal medya özel günlerinden ayrı bir yıllık listede tutar.', ENT_QUOTES, 'UTF-8') ?></p>
        </div>
        <button
          type="button"
          id="st-campaign-holiday-master"
          role="switch"
          aria-checked="false"
          aria-label="<?= htmlspecialchars(app_ui_locale() === 'en' ? 'Toggle campaign special-day yearly drafts' : 'Kampanya özel günü yıllık taslaklarını aç/kapat', ENT_QUOTES, 'UTF-8') ?>"
          class="relative mt-1 inline-flex h-8 w-14 shrink-0 cursor-pointer items-center rounded-full bg-gray-300 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-500"
        >
          <span aria-hidden class="inline-block h-7 w-7 translate-x-1 transform rounded-full bg-white shadow transition motion-reduce:transition-none"></span>
        </button>
      </div>
      <p class="mt-4 text-xs leading-relaxed text-gray-500"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'These rules are read only on the Campaign Management calendar. Social Media keeps using the list above.' : 'Bu kurallar sadece Kampanya Yönetimi takviminde okunur. Sosyal Medya yukarıdaki listeyi kullanmaya devam eder.', ENT_QUOTES, 'UTF-8') ?></p>
      <p class="mt-2 text-xs font-medium uppercase tracking-wide text-gray-500"><?= htmlspecialchars(app_ui_locale() === 'en' ? 'Campaign special-day rules' : 'Kampanya özel günü kuralları', ENT_QUOTES, 'UTF-8') ?></p>
      <div id="st-campaign-holiday-grid" class="mt-3 grid gap-2 sm:grid-cols-2"></div>
    </section>
  </div>
</div>

<div id="st-holiday-editor" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/40 p-4" role="presentation"></div>
