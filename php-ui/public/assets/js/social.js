(function () {
  'use strict';

  const cfg = window.__AGENTBASE__ || {};
  const API = (cfg.apiBase || '/api').replace(/\/$/, '');
  const token = () => (cfg.accessToken || '').trim();

  function authHeaders(json) {
    const h = new Headers();
    if (json) h.set('Content-Type', 'application/json');
    const t = token();
    if (t) h.set('Authorization', 'Bearer ' + t);
    return h;
  }

  async function apiRequest(path, init) {
    const url = API + (path.startsWith('/') ? path : '/' + path);
    const res = await fetch(url, init);
    const raw = await res.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + raw.slice(0, 200));
      return {};
    }
    if (!res.ok) {
      const d = data.detail;
      let msg =
        (typeof data.error === 'string' && data.error) ||
        (typeof d === 'string' && d) ||
        (Array.isArray(d) && d.map((x) => (x && x.msg) || '').filter(Boolean).join(' ')) ||
        'HTTP ' + res.status;
      throw new Error(msg);
    }
    return data;
  }

  async function postJson(path, body) {
    return apiRequest(path, { method: 'POST', headers: authHeaders(true), body: JSON.stringify(body) });
  }

  async function getTaskStatus(taskId) {
    return apiRequest('/social-media/tasks/' + encodeURIComponent(taskId), { headers: authHeaders(false) });
  }

  async function resolveQueued(data, opts) {
    const intervalMs = (opts && opts.intervalMs) || 2000;
    const maxWaitMs = (opts && opts.maxWaitMs) || 900000;
    if (data && data.queued === true && typeof data.task_id === 'string' && data.task_id.trim()) {
      const taskId = data.task_id.trim();
      const deadline = Date.now() + maxWaitMs;
      while (Date.now() < deadline) {
        const st = await getTaskStatus(taskId);
        if (st.status === 'success') return st.result || {};
        if (st.status === 'failure') throw new Error(st.error || 'Task failed');
        await new Promise((r) => setTimeout(r, intervalMs));
      }
      throw new Error('Task timeout');
    }
    return data;
  }

  function lsGet(k) {
    try {
      return (localStorage.getItem(k) || '').trim();
    } catch {
      return '';
    }
  }

  function pick(obj, a, b) {
    if (!obj) return '';
    const v = obj[a] != null ? obj[a] : obj[b];
    return v != null ? String(v).trim() : '';
  }

  function buildIntegration(account) {
    const o = {};
    const oai = lsGet('app_settings_openai_api_key');
    const fal = lsGet('app_settings_fal_api_key');
    if (oai) o.openai_api_key = oai;
    if (fal) o.fal_api_key = fal;
    if (account) {
      const igTok = pick(account, 'instagramAccessToken', 'instagram_access_token');
      const igUid = pick(account, 'instagramApiKey', 'instagram_user_id') || pick(account, 'instagramUserId', '');
      const fb = pick(account, 'facebookPageId', 'facebook_page_id');
      if (igTok) o.instagram_access_token = igTok;
      if (igUid) o.instagram_user_id = igUid;
      if (fb) o.facebook_page_id = fb;
    }
    return o;
  }

  function setStatus(el, text) {
    if (el) el.textContent = text;
  }

  function normalizeAccounts(rows) {
    return (rows || []).map((r) => {
      const id = String(r.id != null ? r.id : r.doc_id != null ? r.doc_id : '').trim();
      const name =
        pick(r, 'name', 'name') ||
        pick(r, 'accountName', 'account_name') ||
        (id ? 'Hesap ' + id : 'Hesap');
      return { raw: r, id, name };
    });
  }

  let accounts = [];
  let publishBusy = false;

  async function loadAccounts(sel, hint) {
    const rows = await apiRequest('/social-data/collections/accounts', { headers: authHeaders(false) });
    accounts = normalizeAccounts(Array.isArray(rows) ? rows : []);
    sel.innerHTML = '';
    if (!accounts.length) {
      hint.textContent = 'Hesap yok; Ayarlardan veya veri girisinden ekleyin.';
      return;
    }
    accounts.forEach((a, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = a.name;
      sel.appendChild(opt);
    });
    hint.textContent = accounts.length + ' hesap.';
  }

  function activeAccount() {
    const sel = document.getElementById('ab-account');
    const i = parseInt(sel && sel.value, 10);
    if (!Number.isFinite(i) || !accounts[i]) return null;
    return accounts[i].raw;
  }

  function renderStrip(container, urls, onPick) {
    container.innerHTML = '';
    urls.forEach((u) => {
      if (!u) return;
      const img = document.createElement('img');
      img.className = 'ab-thumb';
      img.src = u;
      img.alt = '';
      img.addEventListener('click', () => onPick(u, img));
      container.appendChild(img);
    });
  }

  function selectThumb(url) {
    const input = document.getElementById('ab-image-url');
    input.value = url;
    document.querySelectorAll('.ab-thumb').forEach((el) => {
      el.classList.toggle('ab-sel', el.src === url);
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const accSel = document.getElementById('ab-account');
    const accHint = document.getElementById('ab-account-hint');
    const topic = document.getElementById('ab-topic');
    const caption = document.getElementById('ab-caption');
    const imageUrl = document.getElementById('ab-image-url');
    const strip = document.getElementById('ab-image-strip');
    const statusEl = document.getElementById('ab-status');
    const mgrOut = document.getElementById('ab-manager-out');

    if (!token()) {
      setStatus(statusEl, 'Oturum token yok; yeniden giris yapin.');
      return;
    }

    try {
      await loadAccounts(accSel, accHint);
    } catch (e) {
      setStatus(statusEl, 'Hesaplar yuklenemedi: ' + (e && e.message));
    }

    document.getElementById('ab-btn-caption').addEventListener('click', async () => {
      setStatus(statusEl, 'Caption uretiliyor...');
      try {
        const acc = activeAccount();
        const data = await postJson('/social-media/caption/generate', {
          konu: (topic.value || '').trim(),
          tone: 'profesyonel',
          ...buildIntegration(acc),
        });
        const out = await resolveQueued(data);
        caption.value = (out.caption || out.konu || '').trim() || caption.value;
        setStatus(statusEl, 'Caption hazir.');
      } catch (e) {
        setStatus(statusEl, 'Hata: ' + (e && e.message));
      }
    });

    document.getElementById('ab-btn-images').addEventListener('click', async () => {
      setStatus(statusEl, 'Gorseller uretiliyor (kuyruk)...');
      try {
        const acc = activeAccount();
        const prompt = (topic.value || '').trim();
        if (!prompt) {
          setStatus(statusEl, 'Konu / prompt bos.');
          return;
        }
        const data = await postJson('/social-media/flow/generate-images', {
          prompt: prompt.slice(0, 3000),
          count: 4,
          use_gpt: false,
          ...buildIntegration(acc),
        });
        const out = await resolveQueued(data);
        const imgs = (out.images || []).map((x) => x.url).filter(Boolean);
        if (!imgs.length) {
          setStatus(statusEl, 'Gorsel URL donmedi.');
          return;
        }
        renderStrip(strip, imgs, (u, el) => selectThumb(u));
        selectThumb(imgs[0]);
        setStatus(statusEl, imgs.length + ' gorsel.');
      } catch (e) {
        setStatus(statusEl, 'Hata: ' + (e && e.message));
      }
    });

    document.getElementById('ab-file').addEventListener('change', async (ev) => {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      setStatus(statusEl, 'Yukleniyor...');
      try {
        const fd = new FormData();
        fd.append('file', f);
        const res = await fetch(API + '/social-media/image/upload', {
          method: 'POST',
          headers: authHeaders(false),
          body: fd,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Upload ' + res.status);
        const u = (data.url || '').trim();
        if (!u) throw new Error('URL yok');
        imageUrl.value = u;
        renderStrip(strip, [u], () => selectThumb(u));
        selectThumb(u);
        setStatus(statusEl, 'Yuklendi.');
      } catch (e) {
        setStatus(statusEl, 'Yukleme: ' + (e && e.message));
      }
      ev.target.value = '';
    });

    document.getElementById('ab-btn-publish').addEventListener('click', async () => {
      if (publishBusy) {
        setStatus(statusEl, 'Yayin zaten calisiyor.');
        return;
      }
      const ig = document.getElementById('ab-t-ig').checked;
      const st = document.getElementById('ab-t-story').checked;
      const fb = document.getElementById('ab-t-fb').checked;
      if (!ig && !st && !fb) {
        setStatus(statusEl, 'En az bir hedef secin.');
        return;
      }
      const urls = [];
      const primary = (imageUrl.value || '').trim();
      if (primary) urls.push(primary);
      publishBusy = true;
      setStatus(statusEl, 'Yayinlaniyor...');
      try {
        const acc = activeAccount();
        const body = {
          image_url: primary,
          image_urls: urls,
          caption: (caption.value || '').trim(),
          publish_targets: {
            instagram_post: ig,
            instagram_story: st,
            facebook_post: fb,
          },
          ...buildIntegration(acc),
        };
        const res = await fetch(API + '/social-media/post', {
          method: 'POST',
          headers: authHeaders(true),
          body: JSON.stringify(body),
        });
        const raw = await res.text();
        let data = {};
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch {
          setStatus(statusEl, 'JSON degil: ' + raw.slice(0, 400));
          return;
        }
        const hasResults =
          data &&
          (data.post_id ||
            data.story_id ||
            data.story_ids ||
            data.results ||
            (data.errors && Object.keys(data.errors).length));
        const ok = res.ok && (data.success === true || (data.success !== false && hasResults));
        setStatus(statusEl, JSON.stringify({ http: res.status, ok, data }, null, 2));
      } catch (e) {
        setStatus(statusEl, 'Hata: ' + (e && e.message));
      } finally {
        publishBusy = false;
      }
    });

    document.getElementById('ab-btn-manager').addEventListener('click', async () => {
      const msg = (document.getElementById('ab-manager-msg').value || '').trim();
      if (!msg) return;
      setStatus(mgrOut, 'Gonderiliyor...');
      try {
        const acc = activeAccount();
        const data = await postJson('/social-media/manager/run', {
          message: msg,
          ...buildIntegration(acc),
        });
        mgrOut.textContent = JSON.stringify(data, null, 2);
      } catch (e) {
        mgrOut.textContent = 'Hata: ' + (e && e.message);
      }
    });
  });
})();
