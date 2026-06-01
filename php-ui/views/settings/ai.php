<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900">AI</h1>
    <p class="mt-3 text-gray-600">Control AI behavior for prompt professionalization.</p>

    <section class="mt-6 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
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
  </div>
</div>
