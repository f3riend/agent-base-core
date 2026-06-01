# Agent Base — Sistem Dokümantasyonu

> Tarih: 2026-05-28
> Yöntem: Kod taraması (orchestration_api.py, app/api/social_media.py 5212 satır, 37 JS dosyası, 6 SQLAlchemy modeli, 35 SQLite tablo, PHP rotaları)
> Kurallar: Tahmin yok. Görüldüğü yazıldı. Yedek `.bak` dosyaları bozulmadı.

---

## 1. Genel Bakış

Agent Base, **operatörün Türkçe doğal dilde** ("Yeni mağaza oluşunca @deneme hesabında /mers şablonu ile post at") otomasyon kuralı tanımladığı bir **rule-based + LangGraph** orchestration motoru. Mock e-ticaret platformundan gelen event'ler timeline'a yazılır, listener bunları 2sn polling ile yakalar, eşleşen `structured_rules`'i LangGraph'a verir; AI içerik + gerçek görsel (FAL/OpenAI) + insan onayı zincirinden geçirerek Instagram/Facebook Graph API'sine kadar gider.

### Teknoloji stack
| Katman | Teknoloji |
|---|---|
| Backend | Python 3.11, FastAPI ≥0.135, uvicorn |
| ORM | SQLAlchemy ≥2.0 → MySQL (pymysql) |
| Orchestration | LangGraph ≥1.2, SqliteSaver checkpointer |
| Job queue | Celery + Redis (USE_CELERY env) |
| AI | OpenAI gpt-4o-mini (caption + vision), OpenAI image, FAL (image/video) |
| Storage | Cloudflare R2 (boto3 S3) veya yerel `/data/media` |
| Auth | python-jose JWT, bcrypt, Fernet (cryptography) |
| Frontend | PHP 8.2 + Nginx, vanilla JS (37 dosya, ~14.700 satır), 4 CSS dosyası |

### Çalışan process'ler (`start_local.sh` veya `docker/supervisord.conf`)
```
uvicorn          .venv/bin/python -m uvicorn app.main:app --port 8000
listener         .venv/bin/python -u listener.py
workflow_worker  .venv/bin/python -u workflow_worker.py
task_executor    .venv/bin/python -u task_executor.py
php-fpm          (Docker'da; lokal PHP built-in dev server)
nginx            (Docker'da; /api/ → 8000 proxy)
worker           celery -A app.core.celery_app worker (USE_CELERY=true)
```

### Veri akışı (en üst seviye)
```
fake_commerce / harici platform
  → fake_ai_api.db.timeline (INSERT)
  → listener.py (2sn poll)
  → structured_rule_engine.trigger_rules_for_event
  → langgraph_engine.runtime.start_execution
  → supervisor → [wait] → condition_check → content_generator
  → risk_analyzer → approval_gate ⏸ (insan onayı bekler)
  → publish_post / publish_story / publish_banner (paralel olabilir)
  → MySQL social_documents (UI takvim kartı) + listener.db scheduled_entries
  → tool_adapters/instagram.py (SOCIAL_PUBLISH_LIVE=1 ise Graph API)
  → finalize
```

---

## 2. Kullanıcı Akışları

### 2.1 Giriş yapma
- `GET /login` → `views/login.php` (PHP UI'de form)
- Form `POST /auth/login` (FastAPI) → `{username, password}` → response `{access_token, user: {id, username, uid}}`
- PHP, JWT'i session cookie'sine yazar (`includes/auth.php`)
- `app_access_token()` her sayfada bu token'ı okur, `app_require_login()` token yoksa `/login`'e yönlendirir

### 2.2 Instagram hesabı ekleme
İki yol:
- **a)** `POST /social-media/instagram/linked-accounts` → user'ın Facebook access token'ından bağlı Instagram hesaplarını çek. UI'da hesap seçimi (sm-templates-app.js, sm-tags-app.js)
- **b)** Listener tarafı: `POST /api/internal/credentials` → `social_credentials` tablosuna Fernet-encrypted token. `tool_adapters/instagram.py:publish_post` bunu okur

### 2.3 Manuel içerik oluşturma (Sosyal Medya Takvimi)
- `/social-media` → `views/social_media.php` + `social-media-app.js`
- Takvimde "+" → `social-media-studio-modal.js` modal açar
- `social-media-composer-actions.js` ile:
  - `POST /social-media/caption/generate` (OpenAI caption)
  - `POST /social-media/flow/generate-images` (FAL/OpenAI image)
  - `POST /social-media/flow/revise-image` (image revize)
- "Yayınla" tıklanınca `POST /social-media/post` → Graph API
- Veya `social-data/collections/scheduled_posts` ile draft kaydedilir

### 2.4 Şablon kullanarak içerik oluşturma
- `/social-media/sablonlar` → `views/sm_templates.php` + `sm-templates-app.js`
- Şablon CRUD: `POST/PUT /social-data/collections/content_templates`
- Görsel upload: `POST /social-media/image/upload`
- Şablon payload: `{title, prompt, imageUrls: [...], outputSize: 'story'|'post', ...}`
- Composer açıldığında "şablondan başla" → şablon prompt'u + imageUrls referans olarak `flow/generate-from-reference`'a verilir

### 2.5 Resim üretme / revize
**Backend yolu (manuel composer):**
- `POST /social-media/flow/generate-images` → `_sync_generate_images_task` → `generate_images` (text-only) veya `generate_images_from_reference` (referans var)
- `POST /social-media/flow/revise-image` → `_sync_revise_image_task` → `revise_image_with_feedback`
- Provider: FAL_KEY + OPENAI_API_KEY env'den, output_size: feed/story/video

**Kural yolu (otomatik, LangGraph):**
- `langgraph_engine/nodes.py:_generate_image_via_pipeline` → `_sync_generate_images_task` çağırır
- Referans olarak şablonun `imageUrls[0]` geçer, prompt = AI-üretilmiş image_prompt
- Üretim başarısızsa graceful fallback: şablon görseli direkt kullanılır

### 2.6 Kural yazma ve tetikleme
- `/page/timeline/<slug>` → `views/page.php` + `views/timeline/_rules_toolbar.php` + `timeline-page-rules.js`
- Operatör Türkçe yazar → "Önizle" tıklanır → `POST /api/internal/structured-rules/parse` → `parse_rule()` (regex prefilter + LLM ince ayar)
- "Etkinleştir" → `POST /api/internal/structured-rules` → DB'ye kaydet, `structured_rules.enabled=1`

### 2.7 Onay akışı
- LangGraph `approval_gate` node'unda `interrupt_before` ile durur → `approval_requests` tablosuna kayıt
- `/social-media/onay-bekleyenler` → `views/approvals.php` + iki JS:
  - `approvals-app.js` — eski SocialDocument bazlı yol (composer_drafts vs scheduled_posts)
  - `internal-approvals.js` — yeni LangGraph approval'ları (`/api/internal/approvals/types` + dinamik sekme)
- Operatör onayla → `POST /api/internal/approvals/{id}/approve` → `runtime.resume_execution(decision="approved")` → graph kalan node'ları çalıştırır

### 2.8 Yayınlama
- Graph akışı: `publish_post_node` / `publish_story_node` / `publish_banner_node`
- Her node:
  1. `tool_adapters.get_adapter(channel).publish_post()` → Instagram Graph API (`SOCIAL_PUBLISH_LIVE=1` zorunlu)
  2. `_create_calendar_entry` → `scheduled_entries` (listener.db, takvim için)
  3. `_save_to_social_documents` → MySQL `scheduled_posts`/`campaign_scheduled_posts`/`story_scheduled_posts` (UI kart için)
- UI takvim `/social-data/collections/scheduled_posts` ile çekip gösterir

---

## 3. PHP UI — Sayfa Haritası

`php-ui/public/index.php` (586 satır) tek front controller. Tüm rotalar burada.

| URL | View | JS / Extra | Auth |
|-----|------|------------|------|
| `/` | (redirect) | → `/social-media` veya `/login` | — |
| `/login` | `login.php` | (form post → `/auth/login`) | hayır |
| `/register` | `register.php` | (form post → `/auth/register`) | hayır |
| `/forgot-password` | `forgot_password.php` | — | hayır |
| `/reset-password` | `reset_password.php` | — | hayır |
| `/reset-code` | `reset_code.php` | — | hayır |
| `/verify-email` | (redirect) | → `/social-media` | hayır |
| `/social-media` | `social_media.php` (2 satır şell) | `social-media-app.js` | ✓ |
| `/social-media/system-admin` | `system_admin.php` (160 satır) | `timeline-store-automation.js` (1245 satır) | ✓ |
| `/social-media/etiketler` | `sm_tags.php` | `sm-tags-app.js` + `sm-tags-ui.css` | ✓ |
| `/social-media/sablonlar` | `sm_templates.php` | `sm-templates-app.js` | ✓ |
| `/social-media/onay-bekleyenler` | `approvals.php` (52 satır) | `social-media-app.js` + `approvals-app.js` + `internal-approvals.js` (`mode=social`) | ✓ |
| `/onay-bekleyenler` | (redirect) | → `/social-media/onay-bekleyenler` | ✓ |
| `/campaign-management` | `social_media.php` | `social-media-app.js` | ✓ |
| `/campaign-management/sablonlar` | `sm_templates.php` | `sm-templates-app.js` | ✓ |
| `/campaign-management/onay-bekleyenler` | `approvals.php` | `social-media-app.js` + `approvals-app.js` (`mode=campaign`) | ✓ |
| `/settings`, `/settings/{section}` | `settings/{section}.php` (6 alt: account, workspace, ai, api-keys, automation, security) | `settings-app.js` | ✓ |
| `/kurallar`, `/rules` | (redirect) | → `/page/timeline/all` | ✓ |
| `/page/timeline/<slug>` (22 slug) | `page.php` veya `timeline/store_page.php` (slug=store ise) | `timeline-page-rules.js` + `timeline-rules.css` (sadece slug varsa) | ✓ |
| `/page/<other>` | `page.php` | — | ✓ |

### Sidebar (`views/layout.php`, 241 satır)
22 timeline slug'i (Tümü, Siparişler, Ürünler, Değerlendirmeler, Sorular, Kuponlar, Kampanyalar, Reklamlar, Çalışanlar, Mesajlar, Stok, Giriş/Çıkış, Mağaza Sayfası, İadeler, Para Çekme, İndirimler, Eklentiler, Abonelik, Teslimat, Bannerlar, Flash Satış, Bileşenler) + Sosyal Medya (4) + Kampanya Yönetimi (3) + Sistem Yöneticisi + Ayarlar (6).

---

## 4. JavaScript Dosyaları (37 dosya, ~14.700 satır)

`php-ui/public/assets/js/`. Tablo (her dosya için: satır, rol, çağırdığı endpoint'ler):

| Dosya | Satır | Rol | Çağırdığı endpoint'ler |
|---|---:|---|---|
| `app-shell.js` | 122 | Sidebar collapse state, localStorage toggle | — (DOM-only) |
| `approvals-app.js` | 542 | Eski approval flow — SocialDocument bazlı (composer_drafts + scheduled_posts) | `/social-data/collections/*` |
| `internal-approvals.js` | 221 | **YENİ:** Kural Tabanlı Onaylar dinamik sekme (LangGraph approval'ları) | `/api/internal/approvals/types`, `/api/internal/approvals`, `/api/internal/approvals/{id}/approve`, `.../reject` |
| `settings-app.js` | 680 | Ayarlar — API keys, otomasyon event listesi | `/social-data/collections/*`, `/social-media/automation/events` |
| `settings.js` | 43 | localStorage API key (OpenAI/FAL) | — |
| `sm-feed-card-carousel.js` | 112 | Feed carousel index/timer | — |
| `sm-tags-app.js` | 685 | Etiket CRUD (workspace_labels) | `/social-data/collections/*` |
| `sm-templates-app.js` | 449 | İçerik şablon editörü (content_templates) | `/social-media/image/upload` + `/social-data/collections/*` |
| `social-media-actions.js` | 907 | Event delegasyon — click handler, form submit | (modüler, import-based) |
| `social-media-api.js` | 62 | HTTP helper (apiRequest, authHeaders, token) | — |
| `social-media-app.js` | 896 | **Ana SM app** — state init, modül composition | (import-only) |
| `social-media-auto-publish.js` | 98 | Auto-publish sweep — zamanı dolan draft'ları yayınla | — (helper) |
| `social-media-background-tasks.js` | 299 | Background task loop (caption/image/video/holiday) | `/social-media/holiday/generate`, `/social-media/tasks/*` |
| `social-media-calendar-events.js` | 228 | Takvim drag/drop + tarih seçimi | — (event delegasyon) |
| `social-media-campaign-utils.js` | 415 | Campaign mode yardımcıları (banner output size) | — |
| `social-media-change-handler.js` | 176 | Form input sync + image upload | `/social-media/image/upload` |
| `social-media-composer-actions.js` | 953 | **Composer modal** — caption + image üretimi (FAL pipeline) | `/social-media/caption/generate`, `.../caption/revize`, `/social-media/flow/generate-images`, `.../flow/generate-from-reference`, `.../flow/revise-image`, `/social-media/video/generate`, `/social-media/holiday/generate` |
| `social-media-constants.js` | 52 | Sabitler (koleksiyon adları, size preset) | — |
| `social-media-data.js` | 387 | Data layer — socialList/Create/Put/Patch, campaign catalog | `/social-data/collections/*`, `/social-media/automation/workflows`, `/social-media/campaign/catalog`, `.../campaign/store-products`, `.../campaign/publish`, `/social-media/post`, `.../image/delete`, `.../tasks/*`, `.../instagram/linked-accounts` |
| `social-media-holidays.js` | 94 | Tatil günü sabitleri (date-holidays) | — |
| `social-media-mappers.js` | 183 | Document → UI model normalizasyon | — |
| `social-media-modal-helpers.js` | 486 | Studio form sync + Graph publish kart, dropdown builder | `/social-media/instagram/graph-destinations` |
| `social-media-persistence.js` | 734 | State persistence — async fetch/sync, localStorage cache | (data-layer async) |
| `social-media-post-preview.js` | 151 | Hover preview card | — |
| `social-media-post-utils.js` | 234 | Tarih formatı, takvim builder, lifecycle badge | — |
| `social-media-render.js` | 768 | DOM render — post grid, takvim, timeline, modal | — |
| `social-media-runtime.js` | 349 | Runtime state, debug log, in-flight caption/image | `/client-debug/browser-logs` |
| `social-media-selectors.js` | 205 | State selector | — |
| `social-media-state.js` | 146 | Global `s` objesi | — |
| `social-media-studio-helpers.js` | 528 | Studio post/draft yükleyici, campaign asset sync | `/social-media/usage/cost` |
| `social-media-studio-modal.js` | 677 | Studio modal HTML render, hesap seçici | — |
| `social-media-ui.js` | 22 | UI ikonları (chevron, plus, loading dots) | — |
| `social.js` | 326 | Legacy app entry — task polling, caption/image/video | `/social-media/tasks/*`, `/social-data/collections/accounts`, `/social-media/caption/generate`, `.../flow/generate-images`, `.../image/upload`, `/social-media/post`, `.../manager/run` |
| `timeline-page-rules.js` | 730 | **NL kural composer** — toast, optimistic toggle, şablon grid | `/api/internal/structured-rules*`, `.../rule-templates`, `.../rule-executions`, `.../structured-rules-conflicts/suggestions` |
| `timeline-store-automation.js` | 1245 | **Sistem Yöneticisi chat UI** — ürün grid + insights + timeline + AI sohbet | `/api/internal/chat`, `.../items`, `.../humanized-timeline`, `/social-data/collections/*` |
| `usage-app.js` | 100 | Kullanım analitikleri (90 günlük cost) | `/social-media/usage/summary` |

**lib/** klasörü boş.

---

## 5. Backend API Endpoint'leri

### 5.1 `app/api/auth.py` (prefix `/auth`, 3 endpoint)
| Method | Path | Ne yapıyor |
|---|---|---|
| POST | `/auth/register` | Yeni user (MySQL `users`), workspace_uid auto, JWT döner |
| POST | `/auth/login` | username/password doğrula (bcrypt), JWT döner |
| GET | `/auth/me` | `Authorization: Bearer <JWT>` ile user bilgisi |

### 5.2 `app/api/client_debug.py` (1 endpoint)
| Method | Path | Ne yapıyor |
|---|---|---|
| POST | `/client-debug/browser-logs` | UI'den hata logları al |

### 5.3 `app/api/social_data.py` (prefix `/social-data`, 8 endpoint)
Generic SocialDocument CRUD — koleksiyon bazlı.
| Method | Path | Ne yapıyor |
|---|---|---|
| GET | `/social-data/collections/{collection}` | Workspace'in koleksiyonundaki tüm doc'ları döner |
| POST | `/social-data/collections/{collection}` | Yeni doc oluştur (UUID doc_id) |
| PUT | `/social-data/collections/{collection}/{doc_id}` | Full replace |
| PATCH | `/social-data/collections/{collection}/{doc_id}` | Partial update |
| DELETE | `/social-data/collections/{collection}/{doc_id}` | Sil (204) |
| POST | `/social-data/collections/scheduled_posts/{doc_id}/claim-publish` | Scheduled post'u yayın için claim |
| POST | `/social-data/admin/cleanup` | Admin temizlik |

### 5.4 `app/api/social_media.py` (prefix `/social-media`, 5212 satır, ~50 endpoint)

#### Mock / Fake commerce
`/mock/stores`, `/mock/products` (+id/reviews/orders/insights), `/mock/ai-operate`, `/mock/ai-operate-stream`, `/mock/operations/{id}`, `/mock/operations/{id}/stream`, `/mock/operation-history`, `/mock/approvals`

#### Automation
`/automation/events`, `/automation/chat-trigger`, `/automation/stores/fake-create`, `/automation/stores`, `/automation/stores/{id}/approve|reject`, `/automation/workflows`, `/automation/workflows/{id}/dispatch-publish`

#### Content generation
- `POST /caption/generate` → `_sync_caption_generate_task` → OpenAI
- `POST /caption/revize` → `_sync_caption_revize_task`
- `POST /holiday/generate` → `_sync_holiday_generate_task` (caption + image + video)
- `POST /video/generate` → FAL video chain

#### Flow (interactive image pipeline)
- `POST /flow/analyze` → ContentIntelligenceService (GPT-4o vision, reference image analizi)
- `POST /flow/generate-images` → `_sync_generate_images_task`
- `POST /flow/generate-from-reference` → `_sync_generate_from_reference_task`
- `POST /flow/revise-image` → `_sync_revise_image_task`
- `POST /flow/session/start|feedback`, `GET /flow/session/{id}`
- `GET /tasks/{task_id}` (Celery task status)

#### Instagram (Meta Graph)
- `POST /instagram/linked-accounts` — user token → bağlı IG hesapları
- `POST /instagram/graph-destinations` — feed/story/reel destinasyonları

#### Campaign (upstream "Sepetler AI" API)
- `GET /campaign/catalog` — store + campaign listesi (cached)
- `GET /campaign/store-products`
- `POST /campaign/publish` — banner upstream'a yolla

#### Publish
- `POST /post` — Instagram/Facebook Graph publish (feed photo, story, carousel)

#### Utilities
- `POST /image/upload`, `POST /image/delete`
- `GET /usage/summary`, `GET /usage/cost`

### 5.5 `app/routers/social_media.py` (legacy_router, 822 satır, 21 endpoint)
Eski stil router — `main.py:legacy_router = social_media.legacy_router` ile mount. İçeriği detaylı incelenmedi.

### 5.6 `orchestration_api.py` (prefix `/api/internal`, 96 endpoint)
LangGraph + structured rule + scheduling + customer + campaign + auth/orgs API'si. **Auth katmanı yok** — `user_id` query param ile default (db.DEFAULT_USER_ID=3) veya `X-API-Key` header.

Tam liste (satır numarasıyla):

```
GET    /dashboard                              137
GET    /rules                                  149
POST   /rules/preview                          159
POST   /rules/preview-autonomous               188
POST   /rules/apply                            196
DELETE /rules/{rule_id}                        229
PATCH  /rules/{rule_id}/enabled                237
POST   /rules/import-file                      244
POST   /rules/export-file                      250
GET    /workflows                              256
GET    /items                                  297
GET    /tasks                                  318
GET    /tool-executions                        350
GET    /automation-logs                        368
GET    /timeline                               384
GET    /proposals                              401
GET    /approvals                              422
POST   /approvals/{id}/approve                 441
POST   /approvals/{id}/reject                  447
POST   /approvals/{id}/edit                    452
POST   /approvals/{id}/retry                   457
POST   /approvals/{id}/feedback                462
GET    /business-insights                      467
GET    /business-state                         475
GET    /planner-memory                         480
GET    /tools/registry                         485
GET    /agents                                 490
GET    /cache-stats                            495
GET    /traces                                 500
GET    /traces/tags                            515
GET    /traces/by-event/{event_id}             522
GET    /humanized-timeline                     529
GET    /insight-cards                          539
GET    /memory-patterns                        546
GET    /operational-pressure                   552
GET    /chat/intents                           558
GET    /calendar                               588
GET    /schedules                              599
POST   /schedules                              612
PATCH  /schedules/{id}                         633
DELETE /schedules/{id}                         647
POST   /schedules/fire-due                     657
GET    /customer-threads                       688
GET    /customer-threads/{id}                  697
POST   /customer-threads                       706
POST   /customer-threads/{id}/messages         722
POST   /customer-threads/{id}/draft            733
POST   /customer-threads/{id}/escalate         742
POST   /customer-threads/{id}/resolve          751
GET    /credentials                            781
POST   /credentials                            791
DELETE /credentials/{id}                       817
GET    /credentials/encryption-status          832
GET    /campaigns                              860
GET    /campaigns/{id}                         872
POST   /campaigns                              882
PATCH  /campaigns/{id}/pause                   899
PATCH  /campaigns/{id}/archive                 914
GET    /campaigns/{id}/metrics                 924
POST   /orgs                                   968
GET    /orgs/me                                985
POST   /orgs/members                          1022
GET    /orgs/{id}/members                     1038
GET    /api-keys                              1048
POST   /api-keys                              1068
DELETE /api-keys/{id}                         1098
POST   /structured-rules/parse                1155
POST   /structured-rules                      1173
GET    /structured-rules                      1189
GET    /structured-rules/{id}                 1200
PATCH  /structured-rules/{id}/enabled         1209
DELETE /structured-rules/{id}                 1219
POST   /structured-rules/test                 1228
GET    /rule-executions                       1256
GET    /rule-executions/{id}                  1266
POST   /rule-executions/{id}/resume           1276
GET    /rule-templates                        1301
POST   /rule-templates/{id}/materialize       1310
GET    /structured-rules/{id}/versions        1331
GET    /structured-rules-conflicts            1337
POST   /semantic-resolve                      1351
GET    /adapter-health                        1361
GET    /structured-rules/{id}/learning        1377
GET    /learning-suggestions                  1386
GET    /structured-rules-conflicts/suggestions 1398
POST   /structured-rules-conflicts/resolve    1410
POST   /chat-edit/preview                     1436
POST   /chat-edit/apply                       1461
GET    /orchestration-health                  1479
GET    /users                                 1522
POST   /chat                                  1548
POST   /chat/new-session                      1556
GET    /trending-products                     1563
POST   /seed-data                             1578
POST   /products/import                       1584
GET    /dashboard-metrics                     1597
GET    /approvals/types                       (~1700, dinamik sekme için)
```

---

## 6. Resim Üretme Sistemi

### 6.1 Provider'lar (env)
- `FAL_KEY` — FAL.ai, çoğu üretim ve revize burada
- `OPENAI_API_KEY` — gpt-image-1 (text-to-image), GPT-4o vision (reference analizi), caption üretimi
- Provider yoksa: pipeline graceful skip, mock fallback

### 6.2 Düşük seviye fonksiyonlar (`app/services/content_service.py`)
- `generate_images(prompt, count, fal_api_key, platform, openai_api_key, use_gpt, output_size)` — text-only
- `generate_images_from_reference(reference_image_url, prompt, count, fal_api_key, openai_api_key, platform, reference_image_urls, mode, skip_professionalization, output_size)` — referans + prompt
- `revise_image_with_feedback(image_url, feedback, count, ..., revision_context)` — geri-revize

### 6.3 Senkron task wrapper'ları (`app/api/social_media.py`)
| Fonksiyon | Satır | Dönüş | Kullanım |
|---|---:|---|---|
| `_sync_generate_images_task` | 226 | `list[{url}]` | Reference varsa `from_reference`, yoksa text-only |
| `_sync_revise_image_task` | 262 | `list[{url}]` | Image revize, social vs campaign_banner context |
| `_sync_holiday_generate_task` | 291 | `dict` | Caption + image + video (seçilenlere göre) |
| `_sync_generate_from_reference_task` | 339 | `{images, session_id}` | Mode + skip_professionalization flag |
| `_runtime_tool_generate_image` | 1306 | `RuntimeToolResult` | Async, Orchestrator/runtime pattern, reference URL toplama |

### 6.4 Şablondan üretim akışı (kural tetiklendiğinde)
**`langgraph_engine/nodes.py:content_generator_node` içinde:**
1. `_fetch_template_from_mysql(template_name, channel)` — MySQL `content_templates`'tan şablon getir (story durumunda outputSize='story' önceliklendir)
2. `_ai_generate_caption(event_payload, rule_meta, template_data, channel)` — şablon `prompt` + event flat+nested bağlam + sistem mesajı → OpenAI caption (max 3 cümle, 5-7 hashtag)
3. `_build_image_prompt(event_payload, template_data, channel, template)` — şablonun `imagePrompt`/`prompt` öncelikli, yoksa türetilmiş
4. `_generate_image_via_pipeline(prompt, reference_image_url, reference_image_urls, channel, output_size)` — şablonun `imageUrls[0]` reference olarak `_sync_generate_images_task`'a verilir → FAL/OpenAI yeni varlık üretir
5. Üretim başarılı: `image_url = generated_url`, `extras.image_source = "pipeline_generated"`
6. Başarısız (404, key yok, vs.): `image_url = template_image_url`, `image_source = "template_reference"` (graceful fallback)

### 6.5 Görsel nereye kaydediliyor
- `MEDIA_STORAGE=local` → `/data/media/api_uploads/{hash}_{name}.png` → URL `MEDIA_PUBLIC_BASE_URL/media/...`
- `MEDIA_STORAGE=r2` → `app/integrations/r2_storage.py` (boto3 S3) → `R2_PUBLIC_BASE_URL/{key}` veya `R2_PUBLIC_R2_DEV_HOST`

---

## 7. Kural Sistemi (LangGraph)

### 7.1 NL Parser (`nl_rule_parser.py`, 1097 satır)
**Hibrit:** regex prefilter + LLM ince ayar.

Regex pattern'leri:
- Event tipleri (19 adet): store/product/order/stock/review/customer/campaign/banner/story/coupon/sales — Türkçe sinyallere göre
- Zaman: `X gün/saat/dakika sonra`, "anında/hemen"
- Kanal: instagram/facebook/banner/coupon/email/sms vs.
- Şablon: 14 hazır (anneler_gunu, kara_cuma, ...) — keyword tabanlı
- **Yeni sözdizimi**:
  - `@hesap` → target_accounts (multi)
  - `@magaza:urun` → target_store + target_item
  - `@magaza?kategori` → target_store + target_category
  - `/sablon_adi` → target_template
  - `%X üzeri/altı indirim` → Condition (discount_percent >= X)
  - `X kategorisinde` → Condition (category == X)
  - `post`, `story`, `banner` → publish_types
  - `senkron` veya 2+ tip → is_parallel_publish=True

LLM prompt (`_LLM_SYSTEM_PROMPT`): 19 event tipini Türkçe sinyallerle eşler, 5 zorunlu kural (hikaye→story.created, kupon→coupon.created, vs.) ve "ASLA store.created fallback yapma".

### 7.2 StructuredRule (`structured_rule.py`, 689 satır)
Pydantic modeller (`extra="forbid"`):
- `TriggerSpec` (event_type + filters)
- `TimingSpec` (delay_seconds, schedule_at, recurrence)
- `TargetSpec` (account_handle, entity_filters)
- `ContentSpec` (template, channel, headline_hint)
- `ActionStep` (kind, config) — eski/canonical
- `Condition` (field, operator, value) — yeni
- `NodeDefinition` (node_id, node_type, params, parallel_with, depends_on) — yeni
- `GraphDefinition` (nodes, entry/exit, interrupt_before, interrupt_after) — yeni
- `StructuredRule` (yukarıdakileri içerir) + `graph_definition: GraphDefinition | None` (opsiyonel, geri uyumlu)
- `_synthesize_graph_from_actions(rule)` — eski actions zincirinden GraphDefinition türetir

### 7.3 LangGraph Engine
**`langgraph_engine/state.py`** — `RuleExecutionState` TypedDict + reducers (append_traces, merge_dict, take_last). Field: execution_id, rule_id, user_id, thread_id, rule, event, content, risk, approval, publish, monitor, status, current_node, last_error, trace_events, metadata.

**`langgraph_engine/nodes.py`** — node fonksiyonları (16 NODE_TYPES):
- Canonical: `supervisor_node`, `wait_node`, `content_generator_node`, `risk_analyzer_node`, `approval_gate_node`, `publisher_node`, `monitor_node`, `notify_customer_node`, `create_coupon_node`, `finalize_node`
- Yeni (Bölüm 4.1): `condition_check_node`, `publish_post_node`, `publish_story_node`, `publish_banner_node`, `web_publish_node`, `kampanya_sync_node`
- Helper'lar: `_eval_condition` (7 operator: >=, <=, ==, !=, in, not_in, contains), `_resolve_accounts`, `_publish_via_adapter`, `_save_to_social_documents`, `_resolve_account_id`, `_create_calendar_entry`, `_fetch_template_from_mysql`, `_ai_generate_caption`, `_build_image_prompt`, `_generate_image_via_pipeline`, `_extract_hashtags`, `_is_story_rule`
- `NODE_FUNCTIONS` public registry (19 entry — yeni + alias)

**`langgraph_engine/runtime.py`** — graph build + execution lifecycle:
- `build_graph(rule)` dispatcher → `_build_dynamic_graph` (graph_definition varsa) veya `_build_canonical_graph` (eski yol, dokunulmadı)
- `_wire_edges(g, gd)` — parallel_with grupları tespit + fan-out/fan-in + depends_on açık edge
- `SqliteSaver(listener.db)` checkpointer
- `start_execution(rule, event, user_id)` — yeni execution + invoke
- `resume_after_wait(execution_id)` — waiting_timer'dan resume
- `resume_execution(execution_id, approval_decision)` — approval onay/red ile resume
- `dry_run_preview(rule, event)` — UI preview
- `get_execution_traces`, `list_executions`, `get_execution`

### 7.4 Onay akışı
- `approval_gate_node` `interrupt_before` ile compile → LangGraph bu node'a girmeden durur
- `_get_node_params(state)` ile `approval_type` okunur (post_approval/story_approval/banner_approval/campaign_approval/generic_approval)
- `approval_service.create_approval_request(user_id, proposal, event_id, approval_type)` → DB INSERT
- UI'dan POST `/api/internal/approvals/{id}/approve` → `runtime.resume_execution(decision="approved")` → state update + `graph.invoke(None)` → kalan node'lar çalışır
- Reject → state.status="cancelled", publisher atlar, finalize "cancelled" raporlar

### 7.5 Event → publish tam zinciri
```
1. fake_commerce_app.py veya gerçek platform: INSERT INTO fake_ai_api.db.timeline
2. listener.py:main() — 2sn polling
3. listener.py:process_event(event)
   a. SKIP_SYNTHETIC ise atla
   b. event_router.route_event() — critical/monitoring/autonomous karar
   c. subject_type → resource_service.fetch_X (store/item/order/review/campaign/banner/story/coupon)
   d. build_rule_context() — payload + nested + DB row merge
   e. structured_rule_engine.trigger_rules_for_event() — structured_rules tablosundan trigger_event eşleşmesi
   f. Her eşleşen rule için runtime.start_execution(rule, event)
   g. Paralel olarak legacy yol (rule_engine + autonomous_planner)
4. runtime.start_execution → graph.invoke → supervisor → ... → approval_gate ⏸
5. approval_requests row, rule_executions.status='waiting_human'
6. Operatör UI'dan onaylar → resume_execution
7. publisher node → tool_adapters.instagram/facebook.publish_post
8. _save_to_social_documents → MySQL kart
9. _create_calendar_entry → scheduled_entries (listener.db)
10. finalize → rule_executions.status='completed'
```

### 7.6 Wait/Schedule akışı
- `wait_node`: `interrupt_after=[wait]` ile durur, `scheduling_service.create_schedule(kind='workflow', resume_after_wait=True)` ile `scheduled_entries`'e yazar, status='waiting_timer'
- `workflow_worker.py` 2sn polling:
  - `scheduling_service.fire_due_schedules()` — zamanı gelmiş entry'leri `status='fired'`
  - `_handle_wait_resumes()` — fired entry'ler için `runtime.resume_after_wait()` çağırır (tüm user_id'leri tarar, eski hardcode user_id=1 bug fix edildi)

---

## 8. Kampanya Sistemi

### 8.1 Banner üretimi (kural tarafı)
- `publish_banner_node`:
  - Şablon `imageUrls` varsa pipeline ile yeni banner üret
  - `EXTERNAL_WEBHOOK_URL` env varsa POST (banner kullanımı için harici site)
  - Yoksa `automation_log_service.log_action(action_kind='banner_publish_skipped', detail={reason: 'no_webhook'})` ile draft kabul
- `_save_to_social_documents` `collection="campaign_scheduled_posts"` MySQL kart yaz
- `_create_calendar_entry` `kind="campaign"` ile listener.db `scheduled_entries`

### 8.2 Banner üretimi (UI tarafı, sosyal medya composer)
- `POST /social-media/campaign/publish` → upstream Campaign API (Sepetler AI v1):
  - `/banners` (upload), `/schedule`, `/publish` (try-fallback)
  - `_campaign_provider_settings(db, workspace_uid, campaign_account_id)` — account override veya workspace app_settings'tan `campaignApiBaseUrl` + `campaignApiKey`
- Response: `{attempted_paths: [...], publish_result: {...}}`

### 8.3 Takvimle ilişkisi
- `/api/internal/calendar?month=2026-05` → `listener.db.scheduled_entries` (kural-tetiklemeli)
- UI takvimi `/social-data/collections/scheduled_posts` (MySQL — manuel + kural birleşik)
- `/social-data/collections/campaign_scheduled_posts` (kampanya banner draft'ları)
- `/social-data/collections/story_scheduled_posts` (story draft'ları)
- İki dünya farklı tablolar — kural tetiklenince hem listener.db'ye hem MySQL'e yazılır

---

## 9. Veritabanı

### 9.1 MySQL (`agentbase` DB, SQLAlchemy, `app/models/`)
| Tablo | Model | İçerik |
|---|---|---|
| `users` | `User` | id, username (unique), password_hash, workspace_uid (unique), created_at |
| `accounts` | `Account` | id, user_id, name, instagram_access_token (Text), instagram_user_id, logo_url, instagram_token_expires_at, created_at |
| `workspace_labels` | `LabelRow` | id, user_id, name, color (default `#6b7280`), created_at — sosyal medya etiketleri |
| `password_reset_tokens` | `PasswordResetToken` | id, user_id, token, expires_at |
| `content_templates` | `ContentTemplate` | id, user_id (nullable), title, prompt, image_urls (JSON), is_global, created_at — **AMA** UI bunu kullanmıyor; SocialDocument.collection='content_templates' kullanıyor |
| `social_documents` | `SocialDocument` | id, workspace_uid, collection, doc_id, payload (JSON), created_at, updated_at. Unique(workspace_uid, collection, doc_id). **TÜM UI verisi burada — generic key-value store** |
| `usage_events` | `UsageEvent` | id, user_id, account_id, timestamp, kind, model, input_tokens, output_tokens, image_count, seconds — AI usage metering |

#### `social_documents` koleksiyonları (UI tarafından kullanılan)
- `accounts` — Instagram hesap profilleri (UI tarafı)
- `campaign_accounts` — Kampanya hesapları (campaignApiBaseUrl + campaignApiKey)
- `content_templates` — Şablonlar (title, prompt, imageUrls, outputSize)
- `content_templates_global` — Global şablonlar (admin)
- `composer_drafts` — Composer'da kaydedilen draft'lar
- `campaign_composer_drafts` — Kampanya composer draft'ları
- `scheduled_posts` — UI takvim post kartları (rule_engine kaynaklı dahil)
- `campaign_scheduled_posts` — Kampanya banner draft kartları
- `story_scheduled_posts` — Story draft kartları
- `app_settings` — Workspace ayarları (OpenAI/FAL key, campaign API URL)
- `product_reviews`, `product_faq`, `product_support_tickets`, `product_metrics_daily`, `product_assets` — Sistem Yöneticisi runtime context
- `agents` — Agent tanımları

#### Alembic migration'lar
`agent-base-api/alembic/versions/`: `006_create_composer_drafts.py`, `007_create_usage_events.py`.

### 9.2 SQLite — `listener.db` (orchestration + LangGraph, 35 tablo)
Önemli tablolar:

| Tablo | Kayıt | İçerik |
|---|---:|---|
| `users` | 2 | tenant tablosu (id, name, email) — auth değil, çoklu-tenant scope |
| `orgs` | 3 | organizasyonlar |
| `org_members` | — | org üyelikleri |
| `structured_rules` | 14 | kurallar — id, user_id, name, natural_language, rule_json (full Pydantic dump), trigger_event, enabled, fire_count, parse_confidence, version, parent_rule_id, is_current, health_score, success_count, failure_count, cancel_count |
| `rules` | 1 | legacy DSL kuralları (eski rule_engine) |
| `rule_executions` | 58 | LangGraph execution'lar — id, user_id, rule_id, event_id, thread_id (unique), status (running/waiting_human/waiting_timer/completed/cancelled/failed), current_node, approval_id, error |
| `graph_node_traces` | 404 | her node çalıştığında trace — execution_id, node_name, node_status, summary, details_json, duration_ms |
| `approval_requests` | 51 | id, user_id, proposal_id, workflow_name, proposal_json, status (pending/approved/rejected), risk_level, reason, feedback, proposal_hash, **approval_type** (post_approval/story_approval/banner_approval/campaign_approval/generic_approval) |
| `scheduled_entries` | 58 | id, user_id, kind, channel, title, scheduled_at, workflow_name, payload_json, status (pending/fired), fired_at, requires_approval |
| `social_credentials` | 2 | Fernet-encrypted token blob — user_id, provider, account_handle, encrypted_token_blob, scope, token_expires_at, status |
| `checkpoints` | 439 | LangGraph SqliteSaver state checkpoints |
| `workflow_instances` | 49 | legacy workflow yaşam döngüsü |
| `orchestration_traces` | 2230 | observability — tag bazlı event store |
| `chat_sessions` | 8 | Sistem Yöneticisi chat — id (str), user_id, opened_at, last_turn_at, active_entity_type/id/label, active_intent, active_rule_id/name |
| `chat_turns` | 17 | her chat mesajı — session_id, question, resolved_question, intent, primary_entity_type, answer, confidence |
| `ai_tasks` | 47 | background task store (payload, status, next_retry_at) |
| `tool_executions` | — | tool çağrı kayıtları |
| `planner_memory`, `planner_proposals`, `planner_outcomes`, `planner_learning_stats` | — | autonomous planner state |
| `customer_threads`, `customer_messages` | — | müşteri etkileşim akışı |
| `campaigns`, `campaign_metrics` | — | (rule-engine kaynaklı) |
| `automation_logs` | — | per-action structured log |
| `safety_counters`, `execution_cooldowns` | — | rate limit + circuit breaker |
| `stores`, `items`, `orders` | — | resource_service upsert tablosu (fake_ai_api'den synced) |
| `api_keys` | — | per-org API key + scope |
| `rule_history` | — | rule revision log |
| `writes`, `listener_state` | — | cursor + write log |

### 9.3 SQLite — `fake_ai_api.db` (mock e-ticaret, 12 tablo)
| Tablo | Şema önemli alanlar |
|---|---|
| `timeline` | id, ts, event, event_label, log_group, group_label, description, store_id, subject_type, subject_id, causer_type/id/name, changes (JSON), payload (JSON), meta (JSON) — **listener bu tabloyu polling eder** |
| `stores` | id, name, owner, instagram, created_at, updated_at |
| `items` | id, store_id, name, price, stock, sales, category, created_at, updated_at |
| `orders`, `reviews`, `questions`, `campaigns`, `banners` | klasik e-ticaret entity'leri |
| `automation_logs`, `scheduled_jobs`, `listener_state` | mock için |
| `sqlite_sequence` | system |

---

## 10. Eksik / Tamamlanmamış

### 10.1 Bilinen sorunlar
- **TikTok adapter:** `tool_adapters/tiktok.py` stub — her zaman `error: "henüz implement edilmedi"` döner. Phase D2 için ertelenmiş.
- **`crewai_worker.py`:** Deprecated shim (21 satır), hiçbir yerden çağrılmıyor — silinebilir.
- **`agent-base-api/index.html`:** Eski dashboard HTML, `app/main.py:158` `/dashboard` route'unda kullanılıyor — PHP UI tek frontend olduğu için istenmiyor.
- **`rule-based-engine/` kök dizini:** `agent-base/agent-base-api/`'nin md5 bit-bit kopyası (60+ flat .py). Geliştirme tek noktadan yapılmalı; arşivlenmesi öneriliyor.

### 10.2 Yarım kalan özellikler
- **`/api/internal/seed-data`** ve **`/api/internal/products/import`** endpoint'leri var ama UI'dan çağıran tarafı tespit edilmedi.
- **Story/Coupon DB tabloları:** fake_ai_api.db'de `stories` veya `coupons` tablosu yok — `resource_service.fetch_story/fetch_coupon` graceful None döner, context payload'dan kurulur. Tablo eklenmesi gerekli.
- **`workflow_worker.py` TODO'ları:** Worker concurrency + row-level locking + entity_type partitioning + async I/O.
- **`is_story` tespiti** şu an sadece `_is_story_rule` (graph_definition'da publish_story var mı) ile. NL parser yayın türünü NodeDefinition.params'a daha güçlü koymalı; aksi halde "Mağaza oluşunca hikaye oluştur" kuralı bazen `publish_post` üretiyor.
- **`accountId` resolve fallback:** Eğer rule target_accounts'ta handle yazılı ama `campaign_accounts` koleksiyonunda eşleşme yoksa `accountId=null`. UI bunu yorumlamıyor — kullanıcı manuel hesap seçmek zorunda.

### 10.3 Eski/legacy katmanlar
- Aynı event hem `structured_rule_engine` (LangGraph, structured_rules) hem legacy `rule_engine` (rules tablosu) tarafından paralel işlenir. Race değil ama 2 sistem yan yana.
- `crewai_worker.py` shim (silinmeli)
- `app/routers/social_media.py` (`legacy_router`, 822 satır) — `app/api/social_media.py` (5212 satır) ile paralel mount

### 10.4 UI eksikleri
- 22 timeline slug'undan 8'i (coupons, staff, checkin-checkout, withdrawals, plugins, subscription, components, all) `_rules_toolbar.php`'da boş event prefix'iyle açılır → kural panel yok bağlamı
- `timeline-store-automation.js`'in eski `/social-data/collections/*` çağrıları workspace'in dolu olması gerek; manuel collection seed gerekmiyor ama veri yoksa boş listeler

---

## 11. Önemli Dosya Referansları

| Konu | Dosya | Önemli noktalar |
|---|---|---|
| FastAPI entrypoint | `agent-base-api/app/main.py` | 180 satır, lifespan'de hem MySQL init_db hem listener.db init_db, sys.path manip, 5 router include + 2 mount |
| Tek FastAPI router (96 endpoint) | `agent-base-api/orchestration_api.py:42` | `prefix="/api/internal"` |
| Auth (FastAPI) | `agent-base-api/app/api/auth.py:14` | `prefix="/auth"`, 3 endpoint |
| Auth (orchestration_api) | `agent-base-api/orchestration_api.py:89` | `get_current_auth(request, user_id=Query(DEFAULT_USER_ID))` — JWT yok, query param veya X-API-Key |
| DB helper'ları | `agent-base-api/db.py` | ConnectionPool(8), `_ensure_column` idempotent migration, `DEFAULT_USER_ID=3` |
| Sosyal medya API | `agent-base-api/app/api/social_media.py` | 5212 satır, 50 endpoint, image üretim wrapper'ları (226-360), runtime tools (1306-4530) |
| LangGraph state | `agent-base-api/langgraph_engine/state.py` | `RuleExecutionState`, 17 field, reducer'lar |
| LangGraph nodes | `agent-base-api/langgraph_engine/nodes.py` | 16 node, NODE_FUNCTIONS registry (19 alias), 12 helper |
| LangGraph runtime | `agent-base-api/langgraph_engine/runtime.py` | dispatcher build_graph, dinamik + canonical yol, _wire_edges fan-out/fan-in |
| NL parser | `agent-base-api/nl_rule_parser.py` | 1097 satır, regex prefilter (19 event pattern) + LLM ince ayar |
| Pydantic şema | `agent-base-api/structured_rule.py` | StructuredRule, Condition, NodeDefinition, GraphDefinition |
| Trigger eşleme | `agent-base-api/structured_rule_engine.py` | `trigger_rules_for_event(event_name, event, user_id)` |
| Event listener | `agent-base-api/listener.py:48` | `process_event(event)` — synthetic skip + route + structured + legacy paralel |
| Workflow worker | `agent-base-api/workflow_worker.py:44` | `_handle_wait_resumes` — tüm user'ları tara (hardcode bug fix edildi) |
| Resource fetch | `agent-base-api/resource_service.py` | fetch_store/item/order/review/banner/campaign/story/coupon + build_rule_context |
| Approval lifecycle | `agent-base-api/approval_service.py:94` | `create_approval_request(user_id, proposal, event_id, approval_type)` — `assess_approval_need` external publish gate |
| Scheduling | `agent-base-api/scheduling_service.py:172` | `create_schedule(...)` ve `:656 fire_due_schedules` |
| Tool adapters | `agent-base-api/tool_adapters/__init__.py` | `get_adapter(provider)` → instagram/facebook/tiktok |
| Instagram publish | `agent-base-api/tool_adapters/instagram.py:64` | Graph API 2-aşamalı: `/media` container + `/media_publish`, SOCIAL_PUBLISH_LIVE flag |
| Internal event | `agent-base-api/event_emitter.py` | `emit_event(event_type, payload, source)` — fake_ai_api.db.timeline INSERT + webhook |
| PHP front controller | `php-ui/public/index.php` | 586 satır — tüm rotalar, auth guard, layout render |
| PHP layout | `php-ui/views/layout.php` | 241 satır, sidebar + timeline 22 slug + window.__AGENTBASE__ inject |
| PHP HTTP helper | `php-ui/includes/http.php` | `app_http_json($method, $url, $body, $bearer)` |
| PHP auth helper | `php-ui/includes/auth.php` | session + JWT cookie + app_require_login |
| PHP config | `php-ui/includes/config.php` | APP_BASE_PATH, APP_BROWSER_API_BASE (default `/api`), APP_INTERNAL_API_URL |
| PHP onay sayfası | `php-ui/views/approvals.php` | 52 satır, `data-api-base/data-token/data-user-id` attribute'lar (internal-approvals.js için) |
| PHP sistem yöneticisi | `php-ui/views/system_admin.php` | 160 satır — sol ürün listesi + chat + sağ panel |
| PHP timeline kural panel | `php-ui/views/timeline/_rules_toolbar.php` | 134 satır — slug→event prefix eşlemesi (22 slug) |
| Lokal start script | `agent-base-api/start_local.sh` | 4 process spawn — uvicorn + listener + workflow_worker + task_executor |
| Yedek | `agent-base-api/*.bak*`, `langgraph_engine/*.bak*` | 6+ yedek seti, geri dönüş için |

---

*Bu döküman kod taramasına dayanır; her referans gerçek satır numarası veya gerçek tablo adıdır. Eksik/yarım kalan bölümler "Eksik / Tamamlanmamış" başlığında işaretlenmiştir.*
