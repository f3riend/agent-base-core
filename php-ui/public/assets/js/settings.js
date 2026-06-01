(function () {
  'use strict';

  const OPENAI_STORAGE_KEY = 'app_settings_openai_api_key';
  const FAL_STORAGE_KEY = 'app_settings_fal_api_key';
  const PROMPT_PRO_THRESHOLD_KEY = 'app_settings_prompt_professionalization_threshold';

  function lsGet(k) {
    try {
      return localStorage.getItem(k) || '';
    } catch {
      return '';
    }
  }

  function lsSet(k, v) {
    try {
      if (v) localStorage.setItem(k, v);
      else localStorage.removeItem(k);
    } catch {
      /* ignore */
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const o = document.getElementById('st-openai');
    const f = document.getElementById('st-fal');
    const th = document.getElementById('st-threshold');
    const msg = document.getElementById('st-msg');
    o.value = lsGet(OPENAI_STORAGE_KEY);
    f.value = lsGet(FAL_STORAGE_KEY);
    const t0 = lsGet(PROMPT_PRO_THRESHOLD_KEY);
    th.value = t0 ? t0 : '300';

    document.getElementById('st-save').addEventListener('click', () => {
      lsSet(OPENAI_STORAGE_KEY, o.value.trim());
      lsSet(FAL_STORAGE_KEY, f.value.trim());
      const n = Math.max(0, Math.min(3000, Math.round(Number(th.value) || 300)));
      lsSet(PROMPT_PRO_THRESHOLD_KEY, String(n));
      msg.textContent = 'Kaydedildi (bu tarayici).';
    });
  });
})();
