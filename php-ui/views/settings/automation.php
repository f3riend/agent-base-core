<?php declare(strict_types=1); ?>
<div class="min-h-screen bg-gradient-to-br from-white via-gray-50 to-gray-100 px-4 py-10">
  <div class="mx-auto w-full max-w-4xl rounded-[2rem] border border-gray-200 bg-white p-8 shadow-xl shadow-gray-200/60">
    <p class="text-sm font-medium uppercase tracking-[0.2em] text-gray-500"><?= htmlspecialchars(t('settingsTitle'), ENT_QUOTES, 'UTF-8') ?></p>
    <h1 class="mt-2 text-3xl font-semibold text-gray-900">Automation</h1>
    <p class="mt-3 text-gray-600">Event geldiginde CrewAI ile icerik uretip takvime planli post ekle.</p>

    <section class="mt-6 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900">Rule Builder</h2>
      <p class="mt-2 text-sm leading-6 text-gray-600">Ornek: <span class="font-medium">store_created</span> etkinliginde 10 gun sonra paylasilacak icerik uret.</p>
      <form id="st-automation-rule-form" class="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <input type="hidden" id="st-auto-rule-id" value="" />
        <label class="block text-sm text-gray-700">
          Event Type
          <input id="st-auto-event-type" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" value="store_created" />
        </label>
        <label class="block text-sm text-gray-700">
          Delay (days)
          <input id="st-auto-delay-days" type="number" min="0" max="365" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" value="10" />
        </label>
        <label class="block text-sm text-gray-700">
          Publish Time
          <input id="st-auto-publish-time" type="time" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" value="12:00" />
        </label>
        <label class="block text-sm text-gray-700">
          Caption Tone
          <input id="st-auto-caption-tone" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" value="profesyonel" />
        </label>
        <label class="block text-sm text-gray-700">
          Account ID
          <input id="st-auto-account-id" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" placeholder="acc_123" />
        </label>
        <label class="block text-sm text-gray-700">
          Account Name
          <input id="st-auto-account-name" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" placeholder="My Brand" />
        </label>
        <label class="block text-sm text-gray-700 md:col-span-2">
          Template Prompt
          <textarea id="st-auto-template-prompt" rows="3" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" placeholder="Yeni sube acilisi icin premium sosyal medya duyurusu"></textarea>
        </label>
        <label class="block text-sm text-gray-700 md:col-span-2">
          Required Includes (comma separated)
          <input id="st-auto-required-includes" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" placeholder="marka adi, kampanya, lokasyon" />
        </label>
        <div class="rounded-xl border border-gray-200 bg-white px-3 py-3 text-sm text-gray-700">
          <p class="mb-2 font-medium text-gray-900">Publish Targets</p>
          <label class="mr-3 inline-flex items-center gap-2"><input id="st-auto-target-ig-post" type="checkbox" checked /> Instagram Post</label>
          <label class="mr-3 inline-flex items-center gap-2"><input id="st-auto-target-ig-story" type="checkbox" /> Instagram Story</label>
          <label class="inline-flex items-center gap-2"><input id="st-auto-target-fb-post" type="checkbox" /> Facebook Post</label>
        </div>
        <div class="rounded-xl border border-gray-200 bg-white px-3 py-3 text-sm text-gray-700">
          <p class="mb-2 font-medium text-gray-900">Workflow</p>
          <label class="mr-3 inline-flex items-center gap-2"><input id="st-auto-active" type="checkbox" checked /> Active</label>
          <label class="inline-flex items-center gap-2"><input id="st-auto-approve" type="checkbox" /> Auto approve</label>
        </div>
        <div class="md:col-span-2 flex items-center gap-3">
          <button id="st-auto-save-rule" type="submit" class="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800">Save Rule</button>
          <button id="st-auto-reset-rule" type="button" class="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-100">Reset</button>
          <span id="st-auto-rule-status" class="text-sm text-gray-500"></span>
        </div>
      </form>
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-medium text-gray-900">Saved Rules</h2>
        <button id="st-auto-refresh-rules" type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-700 hover:bg-white">Refresh</button>
      </div>
      <div id="st-auto-rules-list" class="mt-3 space-y-2 text-sm text-gray-700"></div>
    </section>

    <section class="mt-4 rounded-[1.5rem] border border-gray-200 bg-gray-50 p-5">
      <h2 class="text-lg font-medium text-gray-900">Event Test</h2>
      <p class="mt-2 text-sm leading-6 text-gray-600">Buradan event gonderince backend otomasyonu calisir ve planli post takvime kaydolur.</p>
      <form id="st-automation-test-form" class="mt-4 grid grid-cols-1 gap-3">
        <label class="block text-sm text-gray-700">
          Rule
          <select id="st-auto-test-rule-id" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400"></select>
        </label>
        <label class="block text-sm text-gray-700">
          Event Type
          <input id="st-auto-test-event-type" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" value="store_created" />
        </label>
        <label class="block text-sm text-gray-700">
          Override Delay Days (optional)
          <input id="st-auto-test-delay" type="number" min="0" max="365" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-gray-400" placeholder="10" />
        </label>
        <label class="block text-sm text-gray-700">
          Event Payload (JSON)
          <textarea id="st-auto-test-payload" rows="5" class="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 font-mono text-xs text-gray-900 outline-none focus:border-gray-400">{
  "storeId": "store_001",
  "storeName": "Kadikoy Subesi",
  "city": "Istanbul"
}</textarea>
        </label>
        <div class="flex items-center gap-2 text-sm text-gray-700">
          <input id="st-auto-test-dry-run" type="checkbox" />
          <label for="st-auto-test-dry-run">Dry run (scheduled post olusturma)</label>
        </div>
        <div class="flex items-center gap-3">
          <button id="st-auto-run-test" type="submit" class="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800">Run Event Test</button>
          <span id="st-auto-test-status" class="text-sm text-gray-500"></span>
        </div>
      </form>
      <pre id="st-auto-test-result" class="mt-3 max-h-72 overflow-auto rounded-xl border border-gray-200 bg-white p-3 text-xs text-gray-700"></pre>
    </section>
  </div>
</div>
