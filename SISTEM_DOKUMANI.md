# SISTEM DOKÜMANI — agent-base

> Bu doküman `agent-base/` klasörünün baştan sona analizine dayanır. Hem yeni clean-arch (`agent-base-api/app/`) hem de eski flat modüller (`agent-base-api/*.py`) kapsanmıştır. Frontend tarafında PHP UI (`php-ui/`) ve eski tek-sayfa dashboard HTML (`agent-base-api/index.html`) iki ayrı arayüz olarak ele alınmıştır.

---

## 0. Genel Mimari

```
agent-base/
├── agent-base-api/           ← FastAPI backend (tek process)
│   ├── app/                  ← Yeni clean-arch (Tur 4)
│   │   ├── api/              ← auth, social_media, social_data, client_debug, commerce
│   │   ├── core/             ← database (PG SessionLocal), env_settings
│   │   ├── models/           ← Store, Product, ProductImage, ProductReview,
│   │   │                       ProductFaq, ProductMetricsWeekly, ChatSession,
│   │   │                       ChatMessage, SocialDocument, ...
│   │   ├── routers/          ← legacy social_media router
│   │   ├── services/         ← content, prompt builder, dispatcher, usage,
│   │   │                       chat_session_service, local_media_storage
│   │   ├── agents/           ← pipeline + manager + social_media_agent
│   │   ├── integrations/     ← instagram_client (Graph API), r2_storage
│   │   ├── tasks/            ← image_tasks (Celery destekli)
│   │   ├── runtime/          ← semantic_operation_interpreter
│   │   └── main.py           ← FastAPI entrypoint
│   ├── *.py                  ← Eski rule-based-engine flat modülleri (60+ dosya)
│   ├── langgraph_engine/     ← StateGraph runtime + node implementasyonları
│   ├── tool_adapters/        ← Instagram/Facebook/TikTok adapter'lar
│   ├── orchestration_api.py  ← /api/internal/* — 80+ endpoint
│   ├── fake_commerce_app.py  ← /commerce-platform/* — mock e-ticaret
│   ├── listener.py           ← Timeline polling + rule dispatch
│   ├── listener.db           ← SQLite (operatör tarafı, 35 tablo, ~3MB)
│   ├── fake_ai_api.db        ← SQLite (legacy mock commerce, 9 tablo)
│   └── alembic/              ← PG migration disiplini (versions/008+)
├── php-ui/                   ← PHP frontend
│   ├── public/index.php      ← Tek giriş noktası (front controller)
│   ├── views/                ← 14+ sayfa şablonu
│   ├── public/assets/js/     ← 40+ JS modülü (ES6 + IIFE karışık)
│   └── includes/             ← bootstrap, auth, i18n, http
└── docker/, docker-compose.* ← Konteyner yapılandırması
```

**Veri depolama özet:**
- **PostgreSQL** — yeni commerce gerçeği: `stores`, `products`, `product_images`,
  `product_reviews`, `product_faqs`, `product_metrics_weekly`, `chat_sessions`,
  `chat_messages`, `social_documents` (content + campaign template'ler).
- **listener.db** (SQLite) — operatör tarafı: `structured_rules`, `rule_executions`,
  `approval_requests`, `scheduled_entries`, `workflow_instances`, `ai_tasks`, chat
  legacy memory (`chat_sessions/chat_turns` — business_chat'in eski yan-yazma yolu).
- **fake_ai_api.db** (SQLite) — yalnız mock commerce + timeline; yeni commerce verisi
  PG'ye geçti.
- **R2 / local media** — `MEDIA_STORAGE=local|r2`; üretilen görseller `data/media/`
  altına veya Cloudflare R2'ye yüklenir.

**Tek FastAPI process** üç yetkiyi taşır (app/main.py:72-152):
1. Yeni agent-base API → `app.include_router(auth/social_data/social_media)`
2. Eski orchestration → `app.include_router(orchestration_api.router)` (prefix `/api/internal`)
3. Mock commerce → `app.mount("/commerce-platform", fake_commerce_app)`

PHP UI ise apache/php-fpm üzerinde tamamen ayrı bir servis — fetch ile bu FastAPI'ye konuşur (`window.__AGENTBASE__.apiBase`).

---

## 1. Tüm Sayfalar (PHP Views)

### Korumasız (auth yok)
| Dosya | URL | Amaç |
|---|---|---|
| `views/login.php` | `/login` | Kullanıcı girişi (username + password) |
| `views/register.php` | `/register` | Kayıt formu |
| `views/forgot_password.php` | `/forgot-password` | Şifre sıfırlama bilgi sayfası |
| `views/reset_password.php` / `reset_code.php` / `reset_stub.php` | `/reset-*` | Sıfırlama akışı (deprecated email flow) |

### Korumalı (login gerekli — `app_require_login()`)
| Dosya | URL | Amaç | Yüklenen JS | Çağrılan endpoint'ler |
|---|---|---|---|---|
| `views/social_media.php` | `/social-media` | **Ana panel**: takvim, post composer, draft listesi | `social-media-app.js` (ES6 entry) | `/api/internal/accounts/*`, `/api/internal/posts/*`, `/api/internal/drafts/*` |
| `views/system_admin.php` | `/social-media/system-admin` | **AI Operatör Merkezi**: chat + ürün listesi + sağ context paneli | `timeline-store-automation.js` | `/api/internal/chat`, `/api/internal/humanized-timeline`, `/api/social-media/mock/*` |
| `views/stores.php` | `/stores` | **Mağazalar**: halkalar + ürün grid + modaller | `stores-app.js` (IIFE) | `/commerce-platform/stores`, `/commerce-platform/stores/{id}/items`, `/commerce-platform/internal/create-*` |
| `views/approvals.php` | `/social-media/onay-bekleyenler` ve `/campaign-management/onay-bekleyenler` | Onay bekleyenler (sosyal + kampanya) | `approvals-app.js` + `internal-approvals.js` | `/api/internal/approvals`, `/api/internal/approvals/types`, `/api/internal/approvals/{id}/approve` |
| `views/triggers.php` | `/triggers` | Aktif structured rule listesi | `triggers-app.js` | `/api/internal/structured-rules*` |
| `views/sm_tags.php` | `/social-media/etiketler` | Sosyal medya etiket yönetimi | `sm-tags-app.js` | `/api/internal/tags/*` (social_data collections altında) |
| `views/sm_templates.php` | `/social-media/sablonlar` ve `/campaign-management/sablonlar` | İçerik şablonları | `sm-templates-app.js` | `/api/internal/templates/*` |
| `views/page.php` | `/page/{id}` ve `/page/timeline/{slug}` | Dinamik içerik sayfası + timeline-context kural panosu | `timeline-page-rules.js` (timeline ise) | `/api/internal/structured-rules*`, `/api/internal/rule-templates`, `/api/internal/rule-executions` |
| `views/timeline/store_page.php` | `/page/timeline/store` | Mağaza timeline özelleşmesi | `timeline-page-rules.js` | aynı |
| `views/layout.php` | (master) | HTML iskelet, sidebar, `window.__AGENTBASE__` global config | `app-shell.js` | — |

### Settings Alt Sayfaları (`/settings/{section}`)
| Dosya | URL | Amaç |
|---|---|---|
| `views/settings/account.php` | `/settings/account` | Kullanıcı adı / şifre güncelleme |
| `views/settings/ai.php` | `/settings/ai` | AI davranış eşikleri (slider) |
| `views/settings/api_keys.php` | `/settings/api-keys` | **OpenAI ve FAL API key** girişi + usage dashboard (`usage-app.js`) |
| `views/settings/automation.php` | `/settings/automation` | Otomasyon (LangGraph rule) konfigürasyonu |
| `views/settings/security.php` | `/settings/security` | Güvenlik politikaları, session kontrolü |
| `views/settings/workspace.php` | `/settings/workspace` | Yıllık tatil kuralları + ülke seçimi (localStorage) |

### Routing & Layout
- **Tek giriş noktası**: `public/index.php` — manuel front controller. POST/GET path'leri elle eşleşir; `app_require_login()` korumalı bölgeleri kapsar.
- **Master layout**: `views/layout.php` tüm korumalı sayfalara sidebar + Lucide ikonları + `window.__AGENTBASE__ {apiBase, ...}` global config'i enjekte eder. Auth değilse sidebar render etmez.
- **Bootstrap**: `includes/bootstrap.php` → session başlatır, auth helper'ları (`app_access_token()`, `app_http_json()`), i18n (`t()`, `app_ui_locale()`), config (`app_url()`, API base).

**Eksikler:**
- Routing manuel `if/elseif` ile yapılıyor — mod_rewrite ve PSR-7 yok. Sayfa eklemek index.php düzenlemesi gerektiriyor.
- `reset_*` sayfaları deprecated; akış yarım kalmış görünüyor.
- `forgot_password.php` MySQL'e bağlı ama email gönderim mekanizması yok.

**Geliştirme notları:**
- Symfony/Slim mikro-router veya FastRoute geçişi tek yerde route tablosu çıkarır.
- Settings sayfalarında "Sepetler API" / "Instagram bağlama" sekmeleri eksik (sadece OpenAI + FAL var).

---

## 2. JavaScript Modülleri (40+ dosya, gruplu)

### Çekirdek
- **`app-shell.js`** — sidebar nav, collapsed state persistence, layout iskeleti.

### Sosyal Medya Modülü (sosyal medya panelinin ana motoru)
- `social-media-app.js` — entry; tüm SM modüllerini import eder.
- `social-media-api.js` — fetch wrapper (auth header'lı).
- `social-media-state.js` — merkezi state store.
- `social-media-data.js` — başlangıç veri yükleme (accounts, posts).
- `social-media-runtime.js` — debug mod, pending tasks polling.
- `social-media-constants.js` — renk paleti, polling aralıkları, storage key'leri.
- `social-media-mappers.js` — API → internal model.
- `social-media-change-handler.js` — reactive update.
- `social-media-render.js` — takvim grid + post card render.
- `social-media-ui.js` — modal, button, form bileşenleri.
- `social-media-post-preview.js` — post preview modal.
- `social-media-post-utils.js` — image merge, revision chain.
- `sm-feed-card-carousel.js` — feed card carousel.
- `social-media-composer-actions.js` — draft yarat/düzenle/yayınla.
- `social-media-actions.js` — post aksiyonları (sil, reschedule, retry).
- `social-media-auto-publish.js` — zamanlanmış yayın polling.
- `social-media-calendar-events.js` — takvim event'leri + tatil entegrasyonu.
- `social-media-holidays.js` — ülke kodu → tatil verisi.
- `social-media-campaign-utils.js` — kampanya modu vs sosyal mod ayrımı.
- `social-media-background-tasks.js` — arka plan görev kuyruğu.
- `social-media-persistence.js` — localStorage sync (drafts, pending, **chat history**).
- `social-media-selectors.js` — DOM query helper.
- `social-media-studio-helpers.js` — rich editor yardımcıları.
- `social-media-studio-modal.js` — studio modal lifecycle.
- `social-media-modal-helpers.js` — generic modal utility.

### Onaylar & Kurallar
- `approvals-app.js` — onay grid + carousel.
- `internal-approvals.js` — LangGraph rule approval sekmesi, `approval_type` tabları.
- `timeline-page-rules.js` — sayfa-bağlamlı kural paneli (load/save).
- `triggers-app.js` — structured rule listesi.

### Mağazalar
- `stores-app.js` — halkalar carousel + ürün grid + context menü + create/edit modallar. **Chat içermez.**

### Sistem Yöneticisi
- `timeline-store-automation.js` — **AI Operatör Merkezi'nin tüm JS'i**: chat fetch + stream render + presence indicator + sağ panel + ürün listesi. POST `/api/internal/chat?user_id=1`.

### Ayarlar
- `settings-app.js` — generic form handler.
- `usage-app.js` — OpenAI + FAL kullanım dashboard'u.

### Sosyal Medya — Diğer
- `social.js` — eski helper (muhtemelen legacy).
- `social-media-auto-publish.js`, `social-media-modal-helpers.js` — yardımcılar.

**Eksikler:**
- Modül sayısı sosyal medya tarafında patlamış (25+ dosya), bundling yok (her biri ayrı `<script>`).
- TypeScript/lint config yok.

---

## 3. Tüm API Endpoint'leri (~287 endpoint, kategoriye göre)

> Backend tek FastAPI process'inde; tüm yollar tek port altında.

### 3.1 Auth (`/auth/*`)
- `POST /auth/register` — yeni kullanıcı kaydı
- `POST /auth/login` — token döner
- `GET /auth/me` — mevcut kullanıcı profili

### 3.2 Kurallar / Structured Rules
- `GET /api/internal/rules` — tüm kurallar + cache stats
- `POST /api/internal/rules/preview` — NL'den preview (persist etmeden)
- `POST /api/internal/rules/preview-autonomous` — autonomous plan preview
- `POST /api/internal/rules/apply` — parse + persist
- `DELETE /api/internal/rules/{rule_id}`
- `PATCH /api/internal/rules/{rule_id}/enabled` — toggle
- `POST /api/internal/rules/import-file` / `export-file`
- `POST /api/internal/structured-rules/parse` — NL → structured
- `POST /api/internal/structured-rules` — oluştur
- `GET /api/internal/structured-rules` / `{rule_id}`
- `PATCH /api/internal/structured-rules/{rule_id}/enabled`
- `DELETE /api/internal/structured-rules/{rule_id}`
- `POST /api/internal/structured-rules/test` — synthetic event ile dry-run
- `GET /api/internal/structured-rules/{rule_id}/versions`
- `GET /api/internal/structured-rules-conflicts`
- `POST /api/internal/structured-rules-conflicts/resolve`
- `GET /api/internal/structured-rules-conflicts/suggestions`
- `GET /api/internal/structured-rules/{rule_id}/learning`
- `GET /api/internal/rule-templates`
- `POST /api/internal/rule-templates/{template_id}/materialize`

### 3.3 Rule Executions & Scheduling
- `GET /api/internal/rule-executions` / `{execution_id}`
- `POST /api/internal/rule-executions/{execution_id}/resume`
- `GET /api/internal/calendar` — operatör takvimi
- `GET /api/internal/schedules`
- `POST /api/internal/schedules` / `PATCH /api/internal/schedules/{id}` / `DELETE`
- `POST /api/internal/schedules/fire-due`

### 3.4 Chat & Conversational Edit
- `POST /api/internal/chat` — **operatör chat ana endpoint'i**
- `POST /api/internal/chat/new-session`
- `GET /api/internal/chat/intents`
- `POST /api/internal/chat-edit/preview`
- `POST /api/internal/chat-edit/apply`

### 3.5 Approvals
- `GET /api/internal/approvals` (+ `/types`)
- `POST /api/internal/approvals/{id}/approve` / `reject` / `edit` / `retry` / `feedback`

### 3.6 Campaigns
- `GET /api/internal/campaigns` / `{id}` / `{id}/metrics`
- `POST /api/internal/campaigns`
- `PATCH /api/internal/campaigns/{id}/pause` / `archive`

### 3.7 Customer Interaction
- `GET /api/internal/customer-threads` / `{thread_id}`
- `POST /api/internal/customer-threads` / `{id}/messages` / `{id}/draft` / `{id}/escalate` / `{id}/resolve`

### 3.8 Social Credentials & Orgs
- `GET /api/internal/credentials`, `POST`, `DELETE /{id}`
- `GET /api/internal/credentials/encryption-status`
- `POST /api/internal/orgs`, `GET /api/internal/orgs/me`, `POST /api/internal/orgs/members`, `GET /api/internal/orgs/{id}/members`
- `GET /api/internal/api-keys`, `POST`, `DELETE /{id}`

### 3.9 Insights & Intelligence
- `GET /api/internal/business-insights`
- `GET /api/internal/business-state`
- `GET /api/internal/planner-memory`
- `GET /api/internal/trending-products`
- `GET /api/internal/humanized-timeline`
- `GET /api/internal/insight-cards`
- `GET /api/internal/memory-patterns`
- `GET /api/internal/operational-pressure`
- `GET /api/internal/dashboard-metrics`

### 3.10 Timeline / Observability
- `GET /api/internal/timeline`
- `GET /api/internal/traces` / `/tags` / `/by-event/{event_id}`
- `GET /api/internal/automation-logs`
- `GET /api/internal/tool-executions`

### 3.11 Tools / Agents / Health
- `GET /api/internal/tools/registry`
- `GET /api/internal/agents`
- `GET /api/internal/cache-stats`
- `POST /api/internal/semantic-resolve`
- `GET /api/internal/adapter-health`
- `GET /api/internal/orchestration-health`

### 3.12 Workflows / Tasks / Items
- `GET /api/internal/workflows`
- `GET /api/internal/tasks`
- `GET /api/internal/items`
- `GET /api/internal/proposals`
- `GET /api/internal/learning-suggestions`
- `POST /api/internal/products/import`
- `POST /api/internal/seed-data`
- `GET /api/internal/dashboard` — legacy HTML
- `GET /api/internal/users` (debug, flag gerekli)

### 3.13 Sosyal Medya — Publish & Content (`/social-media/*`)
- `POST /social-media/post` — **Instagram/Facebook yayın**
- `POST /social-media/instagram/linked-accounts`
- `POST /social-media/instagram/graph-destinations`
- `POST /social-media/caption/generate` / `revize`
- `POST /social-media/holiday/generate`
- `POST /social-media/video/generate`
- `POST /social-media/flow/analyze` / `generate-images` / `generate-from-reference` / `revise-image`
- `POST /social-media/flow/session/start` / `feedback` ; `GET /social-media/flow/session/{id}`
- `POST /social-media/image/upload` / `delete`
- `GET /social-media/tasks/{task_id}` — Celery durum polling
- `POST /social-media/agents`, `GET`, `GET /{id}`, `PATCH`, `POST /{id}/run`
- `POST /social-media/manager/run`

### 3.14 Sosyal Medya — Automation & Mock
- `POST /social-media/automation/events`
- `POST /social-media/automation/chat-trigger` — **ikinci chat sistemi** (NL→JSON, CrewAI)
- `POST /social-media/automation/stores/fake-create`, `GET /social-media/automation/stores`, `POST /{id}/approve` / `reject`
- `GET /social-media/automation/workflows`, `POST /{id}/dispatch-publish`
- `GET /social-media/mock/stores`, `POST`, `GET /products`, `POST`, `GET /{id}`, `/reviews`, `/orders`, `/insights`
- `POST /social-media/mock/ai-operate` / `ai-operate-stream`
- `GET /social-media/mock/operations/{task_id}/stream` / `{task_id}` / `operation-history`
- `GET /social-media/mock/approvals`

### 3.15 Sosyal Medya — Campaign & Usage
- `GET /social-media/campaign/catalog`
- `GET /social-media/campaign/store-products`
- `POST /social-media/campaign/publish` — **Sepetler API üzerinden kampanya yayını**
- `GET /social-media/usage/summary` / `/cost`

### 3.16 Social Data (Document Store)
- `GET /social-data/collections/{collection}` — koleksiyon listesi
- `POST /social-data/collections/{collection}`
- `PUT /social-data/collections/{collection}/{doc_id}`
- `PATCH /social-data/collections/{collection}/{doc_id}`
- `DELETE /social-data/collections/{collection}/{doc_id}`
- `POST /social-data/collections/scheduled_posts/{doc_id}/claim-publish`
- `POST /social-data/admin/cleanup`

### 3.17 Commerce Platform (`/commerce-platform/*` — fake_commerce_app mount)
**Public AI API (prefix `/api/ai/v1`):**
- `GET /api/ai/v1/health`
- `GET /api/ai/v1/timeline` / `timeline/stream` / `replay`
- `GET /api/ai/v1/subjects/{type}/{id}/timeline`
- `GET /api/ai/v1/resources` (+ `/stores`, `/items`, `/orders`, `/{id}`)
- `GET /api/ai/v1/banners`, `POST`
- `GET /api/ai/v1/insights/products/top` / `inventory`

**Internal mutators (timeline'a event basar):**
- `POST /internal/create-store` / `create-product` / `create-order` / `update-stock` / `update-product` / `update-discount` / `create-review` / `create-question` / `shipping-delay` / `update-banner-performance` / `update-sales` / `create-campaign`

**Stores erişimi:**
- `GET /stores`, `GET /stores/{store_id}/items`

### 3.18 Client Debug
- `POST /client-debug/browser-logs` — browser log ingest

### 3.19 Legacy Shim'ler (`/api/*`)
- `POST /api/caption/generate` / `revize`
- `POST /api/flow/*`
- `POST /api/post`, `/api/image/upload`

**TOPLAM: ~287 endpoint.**

**Eksikler:**
- `/api/internal/*` tamamı **auth'suz** (orchestration_api.py:914 yorumu: "current /api/internal/* surface is unauthenticated (test mode)").
- `api_keys` tablosu var ama bunu doğrulayan middleware yok.
- 287 endpoint için OpenAPI tag düzeni dağınık; çok sayıda mock + legacy ölü endpoint.

**Geliştirme notları:**
- `Depends(verify_api_key)` middleware'i ile `api_keys.key_hash` kontrolü yapılmalı.
- Mock endpoint'leri (`/social-media/mock/*`) prod build'inden çıkartılmalı (env flag).
- Legacy shim'ler (kullananı yoksa) silinmeli.

---

## 4. Chat Sistemi

Sistemde **iki ayrı chat** bulunmakta — ama operatör chat'i son güncellemeyle hem
veri sorgusu hem one-shot workflow tetikleyici hâle geldi.

### 4.1 Operatör Chat — `/api/internal/chat`

**Amaç**: AI Operatör Merkezi'ndeki analitik chat + chat-içi one-shot workflow.

**Pipeline** (`business_chat.answer_question`):
```
1. open/load session (conversation_memory.open_session)
   ↓
2. _classify_action_or_query(question)        ← ucuz LLM sınıflandırıcı
   ├─ OpenAI gpt-4o-mini, max_tokens=10, temperature=0
   ├─ "Bu mesaj action mı, query mi?" → 'action' veya 'query'
   ├─ OPENAI_API_KEY yok / CHAT_USE_LLM=0  → 'query' (güvenli taraf)
   ↓
3a. 'action' → _one_shot_run(question, user_id)
    ├─ PG'den ilgili ürünü ara (inline ilike + Türkçe synonym sözlüğü)
    ├─ Ürün + Store + ProductImage'leri yükle (selectinload)
    ├─ rating>=4 son 5 olumlu yorumu çek
    ├─ event_payload kur:
    │     {name, price, brand, category, description (yorumlarla zenginleştirilmiş),
    │      reviews_positive[], primary_image_url, image_url, image_urls[],
    │      store_name, store_logo_url, logo_url, banner_url, store_banner_url,
    │      item: {...}, store: {...}, source: "business_chat_one_shot"}
    ├─ nl_rule_parser.parse_rule(question) → StructuredRule
    │     ├─ missing_fields'da trigger.event_type varsa → "story.created" default
    ├─ rule.enabled = True; structured_rule_engine.save_rule(rule)
    ├─ langgraph_engine.runtime.start_execution(rule=saved, event, user_id)
    │     ├─ wait → generate_content → risk_check → approval_gate → publish_*
    │     ├─ approval_gate'te interrupt: approval_requests'e kayıt, waiting_human
    ├─ set_enabled(rule.id, False)  — one-shot garantisi (event-driven tekrar tetiklemez)
    └─ Türkçe özet:
       - waiting_human: "✓ <ürün> için <kanal> paylaşımı planlandı. Onay bekleyenler..."
       - waiting_timer: "✓ <ürün> için <gün> sonrasına planlandı. Zamanı gelince..."
       - completed:    "✓ <ürün> için <kanal> paylaşımı tamamlandı."
       - failed:       "⚠ <ürün> için akış başlatıldı ama hata..."
   ↓
3b. 'query' → mevcut retrieval+synthesizer yolu:
    ├─ memory.resolve_follow_up() — "peki neden?" → "Satışlar neden bu durumda?"
    ├─ business_query_router.route() — artık LLM tabanlı saf veri çekici:
    │     PG'den TÜM mağaza + ürün + yorum + SSS snapshot'ı tek payload
    │     ('full_context'), keyword/intent matching YOK
    ├─ ai_synthesizer.synthesize() — OpenAI gpt-4o-mini
    │     ├─ format_pg_context: "=== MAĞAZALAR === MAĞAZA SAYISI: N",
    │     │   "=== ÜRÜNLER === ÜRÜN SAYISI: M", ÖZET satırı
    │     ├─ Tek sistem prompt'u (5 kural):
    │     │   (1) veri tek kaynak, varsayım yasak
    │     │   (2) liste uzunluğu = sayı, başka rakam üretme
    │     │   (3) genel sorularda önceki ürüne yapışma
    │     │   (4) kullanıcı yorumlarında yazım hatasını düzelt
    │     │   (5) jargon/kalıp yok, samimi Türkçe
    │     ├─ history bloğu + FORBIDDEN_PHRASES + session anti-tekrar
   ↓
4. record_turn() → listener.db.chat_turns + PG chat_messages yan-yazma
```

**Sınıflandırma örnekleri:**
| Mesaj | Sınıf | Yol |
|---|---|---|
| "Kaç mağazamız var?" | query | retrieval → synthesizer |
| "Yorumlar nasıl?" | query | retrieval → synthesizer |
| "Razer mouse için Instagram hikayesi paylaş /mers şablonuyla" | action | nl_rule_parser → LangGraph |
| "Bu ürüne %15 indirim kampanyası başlat 5 gün sonrasına" | action | nl_rule_parser → LangGraph |

**Hafıza** (`conversation_memory.py`):
- `chat_sessions` (listener.db) — session lifecycle, active_entity_type/id/label
- `chat_turns` (listener.db) — Q&A log + answer_signature + phrase_signatures
- Yan-yazma `chat_sessions` / `chat_messages` (PG) — UI sidebar için
- Anti-tekrar son 4 turdan toplanır, LLM prompt'una "BU CÜMLELERLE BAŞLAMA" olarak verilir

**Veri kaynağı:** PG (stores/products/reviews/faqs). `business_query_router` artık
keyword/intent classifier DEĞİL, saf veri çekici. Tüm snapshot LLM'e gider; LLM
hangi parçayı kullanacağını kendi seçer.

**KALDIRILAN:** `conversational_rule_edit.py` early-dispatch bloğu. Substring-tabanlı
detector ("aç" → "kaç" eşleşmesi gibi false positive'lerle) listener.db.structured_rules
tablosunu sessizce mutate ediyordu. Yerine action/query LLM sınıflandırıcısı geldi;
chat-içi rule edit'i artık operatöre `/chat-edit/preview` → `/chat-edit/apply` iki
adımlı endpoint'leriyle yapılır (operatöre özel UI).

**UI**: `views/system_admin.php` markup + `timeline-store-automation.js` driver.
POST: `base + "/api/internal/chat?user_id=1"`.

### 4.2 Automation Chat-Trigger — `/social-media/automation/chat-trigger`

**Amaç**: Sosyal medya composer'ın doğal Türkçe komutu sosyal medya zamanlama
JSON'una çevirme yolu (composer'dan).

**Pipeline** (app/api/social_media.py:2212):
- CrewAI agent + `gpt-4o-mini`
- Çıktı: `{event_type, delay_days, publish_time, caption_topic, image_prompt,
  account_name, account_id, instagram_post, instagram_story, facebook_post,
  approval_required}`
- Fallback: `_fallback_chat_interpretation` — regex ile sadece delay_days çıkarır.

**UI bağlantısı**: Sosyal medya panelinden tetikleniyor; operatör chat'i artık
**bu yolu kullanmıyor** — kendi one-shot path'i var (4.1).

### Eksikler
- Operatör chat'inde rule-edit komutları (örn. "kuralı kapat") doğrudan
  uygulanmıyor — Triggers paneli kullanılmalı. (Önceki conversational_rule_edit
  yolu false-positive sebebiyle kaldırıldı.)
- Streaming yok (`timeline-store-automation.js:961` "SSE stream artık kullanılmıyor").
- Token/cost kaydı yok — `chat_turns`'a model/prompt_tokens/completion_tokens yazılmıyor.

### Geliştirme notları
- Chat'ten rule-edit (toggle/disable) için LLM sınıflandırıcıya `rule_edit_command`
  sınıfı ekleyip iki turlu confirm pattern'i (pending state in session memory).
- `chat_turns` şemasına `model TEXT, prompt_tokens INT, completion_tokens INT,
  latency_ms INT` ekle.
- SSE veya WebSocket ile stages stream'i.

---

## 5. Veritabanları

Sistemde **4 farklı veritabanı** var (eskiden 3'tü — PG eklendi):

### 5.1 `listener.db` — SQLite (operatör tarafı)

Lokasyon: `agent-base-api/listener.db` (~3MB, dolu). 35 tablo:

**Kurallar & yürütme**
- `rules`, `structured_rules`, `rule_history` — kural şeması ve versiyonlama
- `rule_executions` — LangGraph state execution kayıtları (thread_id, current_node, status, approval_id)
- `graph_node_traces` — node-bazlı trace (operatör UI için humanized)
- `execution_cooldowns`, `safety_counters` — rate limit / circuit breaker
- `checkpoints` — LangGraph SqliteSaver state'leri

**Onaylar & planner**
- `approval_requests` — pending/approved/rejected proposal'lar
- `planner_proposals`, `planner_outcomes`, `planner_memory`, `planner_learning_stats`
- `ai_tasks` — task queue (pending/retrying/dead_letter)

**Chat & müşteri**
- `chat_sessions`, `chat_turns` — operatör chat hafızası
- `customer_threads`, `customer_messages` — müşteri DM thread'leri

**Commerce mirror**
- `items`, `stores`, `orders` — listener'ın commerce'tan kopyaladığı snapshot
- `campaigns`, `campaign_metrics`

**Operasyonel**
- `workflow_instances`, `tool_executions`, `automation_logs`, `orchestration_traces`
- `scheduled_entries` — operatör takvimi
- `social_credentials` — provider başına encrypt edilmiş token (instagram, facebook, trendyol, shopify)
- `api_keys` — org-scoped (key_hash, scope, status)
- `org_members`, `orgs`, `users`
- `listener_state` — son polling watermark
- `writes` — write log (audit)

### 5.2 `fake_ai_api.db` — SQLite (mock commerce)

Lokasyon: `agent-base-api/fake_ai_api.db`. 9 tablo: `stores, items, orders, banners, reviews, questions, campaigns, scheduled_jobs, listener_state, timeline, automation_logs`.

`timeline` tablosu **mevcut event türleri** (`log_group | event`):
- `banner|executed`
- `campaign|executed`
- `customer|executed`
- `product|created`
- `store|created`

Sadece 5 distinct kompozit anahtar. Listener bu tabloyu polling eder, olayları `listener.db`'ye taşır.

### 5.3 PHP UI MySQL (workspace DB)

PHP tarafının ayrı MySQL veritabanı. İçinde:
- Kullanıcı kimlikleri (login/register)
- API key storage (`settings/api_keys.php` — "stored in your workspace database (MySQL)")
- Holiday/locale ayarları

### 5.4 PostgreSQL — Gerçek commerce + chat session deposu (YENİ)

`app/core/database.py` üzerinden tek `SessionLocal` ile `env_settings.DATABASE_URL`
target alır. SQLAlchemy 2.x DeclarativeBase, Alembic ile migration disiplini
(`alembic/versions/008_create_commerce_and_chat.py` ve sonrası).

**Modeller (`app/models/`):**

| Tablo | Sorumluluk |
|---|---|
| `stores` | Operatörün gerçek mağazaları (`name, rating, logo_url, banner_url, status, user_id`) |
| `products` | Ürünler (`name, brand, category, price, discount, stock, rating, rating_count, weekly_sales, description, status, store_id`) |
| `product_images` | Ürün görselleri (`url, sort_order, product_id`) — `Product.images` relationship |
| `product_reviews` | Müşteri yorumları (`rating, content, review_date, product_id`) |
| `product_faqs` | Sık sorulan sorular (`question, answer, product_id`) |
| `product_metrics_weekly` | Haftalık satış/görüntülenme istatistikleri |
| `chat_sessions` (PG) | Operatör chat session header'ı (UI sidebar için) |
| `chat_messages` (PG) | Q&A çiftleri — business_chat yan-yazma; sidebar bunları okur |
| `chat_memory` | Genişletilebilir chat memory katmanı |
| `social_documents` | Workspace + collection + doc_id + JSON payload (`scheduled_posts`, `story_scheduled_posts`, `campaign_scheduled_posts`, `content_templates`, `campaign_templates`, `composer_drafts`, ...) |
| `system_snapshot` | Periyodik sistem durum snapshot'ı |
| `accounts` | Sosyal medya hesap bağlantıları |
| `usage_event` | OpenAI/FAL kullanım sayaçları |

**Yerine geçtikleri:**
- Eski `fake_ai_api.db.stores / items / reviews / questions` → PG `stores / products
  / product_reviews / product_faqs`. Mock commerce'in legacy bağımlılığı `fake_commerce_app`
  içinde kalmaya devam ediyor; canlı operatör verisi PG'de.
- Chat memory'nin SQLite tarafı (`listener.db.chat_sessions/chat_turns`) HÂLA
  business_chat'in anti-tekrar mekanizması için aktif. PG tarafı UI sidebar için
  paralel yazılır (chat_session_service yan-yazma).

**Sosyal şablonlar:**
`social_documents` tablosu MySQL DEĞİL PG'de. `_fetch_template_from_mysql` (langgraph_engine/nodes.py)
isim tarihsel; SQL son güncellemede MySQL `JSON_UNQUOTE(JSON_EXTRACT(...))` syntax'ından
PG `payload->>'field'` operatörlerine çevrildi.

**Eksikler:**
- 4 DB arası senkronizasyon manuel. PHP MySQL'deki OpenAI key, FastAPI'nin okuduğu
  `OPENAI_API_KEY` env'i ile çakışabilir.
- Üst dizinde sıfır byte boş `listener.db` ve `fake_ai_api.db` dosyaları var
  (`agent-base/`). Karışıklık riski.
- Foreign key constraint yalnız PG tarafında tutarlı; SQLite tarafları default
  enforcement'sız.
- `chat_attachments` (UI'daki "Ek" butonu için) tablosu yok.
- Chat memory iki ayrı yerde (SQLite + PG yan-yazma) — tek source-of-truth gerekli.

**Geliştirme notları:**
- Alembic disiplinini eski SQLite tabloları için de genişlet (`init_*_tables()`
  patternini kaldır).
- Chat memory'yi tamamen PG'ye taşımak; SQLite chat_turns artık sadece anti-tekrar
  signature'ı için.
- `social_documents.payload` üzerinde GIN index — şablon arama hızlanır.

---

## 6. Sistem Yöneticisi Sayfası (`/social-media/system-admin`)

### Yapı

```
┌─ Topbar: başlık + tarih aralığı + filtre butonu
├─ Sol panel (tsop-left):
│   ├─ Arama input
│   ├─ Mağaza filtresi select
│   ├─ "+ Ürün Ekle" butonu
│   ├─ Toplu aksiyon barı (Toplu Analiz / Toplu Kampanya / Toplu Banner)
│   └─ Ürün grid
├─ Orta panel (tsop-center):
│   ├─ Tab'lar: Sohbet / Operasyonlar / Geçmiş
│   ├─ AI mod butonları: Analiz / Operasyon / Strateji / İçerik
│   ├─ AI presence indicator
│   └─ Chat alanı (textarea + Gönder)
└─ Sağ panel (tsop-right):
    ├─ Seçili ürün thumbnail + isim + kategori + durum pill
    ├─ Overview (sales/revenue/rating/return rate)
    ├─ Yorumlar listesi + yorum ekleme formu
    ├─ Aktif destek kayıtları + ekleme formu
    └─ Drawer (ek bağlam): AI içgörüleri, SSS, bekleyen adımlar, canlı akış, operasyon timeline, geçmiş
```

### Çalışma

- **JS driver**: `timeline-store-automation.js`. Chat input gönderildiğinde:
  ```
  runPipeline(message) →
    context = { product_id, store_id, mode, product_ids }
    history = chatHistory.slice(-20)
    POST /api/internal/chat { question, session_id }
    → cevap parse: answer + recommendations[]
  ```
- **Ürün listesi** mock commerce verisinden geliyor (`/social-media/mock/products`).
- **AI modları** (Analiz / Operasyon / Strateji / İçerik) sadece UI cosmetic — backend bunu kullanmıyor.
- **Toplu aksiyonlar** (analyze_reviews, create_campaign, generate_banner) — context menüyle de tetikleniyor.
- **Context menü**: ürün üzerinde sağ-tık → "Urunu Analiz Et / Banner Olustur / Kampanya Olustur / Zaman Akisini Ac / AI Sohbeti Ac".

### Eksikler
- `context.product_id` chat backend'ine gönderiliyor **ama backend onu kullanmıyor** — retrieval intent'leri entity-aware değil.
- AI modları cosmetic (Operasyon/Strateji/İçerik fark yaratmıyor).
- "AI Sohbeti Ac" butonu chat'e ürün referansı enjekte etmiyor — sadece tab değiştiriyor.
- "Toplu Analiz / Toplu Kampanya / Toplu Banner" butonlarının backend bağlantısı UI'da seçili ürünlere uygulanmıyor — sadece mock akış var.

### Geliştirme notları
- Chat retrieval intent'lerine `product_id`, `store_id` parametrelerini eklemek (`business_query_router` zaten kwargs taşıyor).
- AI mod'unu LLM prompt'una system instruction olarak yedirmek (`Sen şu an Strateji modundasın — uzun vadeli...`).
- Sağ panel'de gösterilen `customer_messages` (DM thread'leri) chat'e açma intent'i ("Müşteri X'e cevap taslağı").

---

## 7. Mağazalar Sayfası (`/stores`)

### Yapı & Veri

JS: `stores-app.js` (IIFE). Kullanılan endpoint'ler:
- `GET /commerce-platform/stores?user_id=...` — mağaza listesi
- `GET /commerce-platform/stores/{id}/items` — ürünler
- `POST /commerce-platform/internal/create-store`
- `POST /commerce-platform/internal/create-product`

Render:
- **Halkalar** (Instagram-vari) üstte — her mağaza için logo veya initial'lar.
- **Ürün grid** — kart: görsel + isim + fiyat + stok + indirim.
- **Sağ panel** — seçili ürünün fiyat, indirim, stok, kategori, ID.
- **3 modal**: "Yeni Mağaza", "Ürün Ekle", "Ürünleri Gör".
- **Context menü**: mağaza halkasına sağ-tık → "Ürün Ekle / Ürünleri Gör".

Veriler **`fake_ai_api.db`'den** geliyor (`/commerce-platform` mount'u), `listener.db`'den değil.

### Eksikler
- **Chat yok** — sayfa AI ile bağlı değil.
- Yorum (`reviews`), soru (`questions`), sipariş (`orders`) detayı ürün seçildiğinde gösterilmiyor — bu tablolar `fake_ai_api.db`'de mevcut ama UI tüketmiyor.
- `/api/ai/v1/insights/products/top` ve `/insights/inventory` endpoint'leri çağrılmıyor.
- Timeline (`fake_ai_api.db.timeline`) ürün-bazlı filtrelenmiyor.
- Çoklu seçim / toplu aksiyon yok.
- Mağaza silme / düzenleme endpoint'i yok (sadece create var).

### Geliştirme notları
- `system_admin.php`'deki chat markup'ını mağazalar sayfasının orta paneline gömüp `context.product_id/store_id` gönderme.
- Ürün kartına "AI Analiz", "Banner Üret", "Kampanya Başlat" butonları (mevcut context menüye benzer).
- Sağ panele review/order/insight section'ları eklemek (mock commerce zaten dönüyor).
- Update/delete endpoint'leri (`/commerce-platform/internal/update-product`, vb. var ama UI yok).

---

## 8. Sosyal Medya Akışı (Post Üret → Onay → Yayınla)

### 8.1 Tetikleme

Üç farklı tetik yolu:
1. **UI**: `social_media.php` composer → `POST /social-media/post` (caption + image URLs + platform targets).
2. **Kural motoru**: LangGraph publish_node `tool_instagram_post` aracını çağırır.
3. **Automation chat-trigger**: NL → JSON → scheduled_entries → publish_node.

### 8.2 İçerik Üretimi

**Caption**: `generate_caption()` → OpenAI `gpt-4o-mini` (env `OPENAI_CAPTION_MODEL`). Dosya: `app/services/content_service.py:76`.

**Görsel**:
1. `ContentIntelligenceService.analyze()` — kullanıcı input + reference image → ContentContext (intent, refined prompts, physics hints).
2. `PromptBuilder` ile prompt enrichment.
3. `openai_ad_pipeline.generate_ad()` → OpenAI `gpt-image-2` (görsel).
4. fal.ai opsiyonel video generation.
5. `task_dispatcher.dispatch("generate_images", fn, ...)` — Celery varsa async, yoksa sync.

### 8.3 Onay Akışı (`approval_service.py`)

**`approval_requests` şeması:**
```
id, user_id, proposal_id, event_id, workflow_name,
proposal_json, proposal_hash, status (pending|approved|rejected),
risk_level, reason, approval_type
   ∈ {post_approval, story_approval, banner_approval, campaign_approval},
feedback, edited_proposal_json, approved_by, created_at, updated_at
```

**Onay gerekliliği** (`assess_approval_need`):
- `approval_type` post/story/banner/campaign approval, **veya**
- `tools` `instagram_campaign_tool` içeriyor, **veya**
- `workflow_name` "instagram"/"social_publish"/"public_campaign" içeriyor.

Dahili işlemler (FAQ, insight, newsletter) onay gerektirmez.

**Dedup**: `proposal_hash` ile aynı pending proposal tekrar oluşturulamaz.

**Approve** → `proposal["approved"]=True` + `planner_memory.record_feedback()` → `apply_approved_proposal()` ile akış devam eder.

**LangGraph entegrasyonu**: `approval_gate` node'u `interrupt_before` olarak compile edilir; karar gelene kadar graph suspend.

### 8.4 Yayınlama (`content_service.py`)

`GRAPH_URL = "https://graph.facebook.com/v22.0"` (app/integrations/instagram_client.py:8).

**Varyantlar:**
| Format | Fonksiyon | Container tipi |
|---|---|---|
| Feed (tek görsel) | `post_to_instagram` | IMAGE |
| Carousel | `post_carousel_to_instagram` | CAROUSEL + children |
| Story | `post_story_to_instagram` | STORIES |
| Story batch | `post_story_batch_to_instagram` | birden çok STORIES |
| Reel/Video | `create_reel_container` + publish | REELS |

**HTTP akışı:**
```
1. POST {GRAPH_URL}/{user_id}/media (container oluştur)
   ├─ _post_with_auto_refresh: OAuthException 190'da token refresh
2. wait_for_media_container_ready (poll)
3. POST {GRAPH_URL}/{user_id}/media_publish {"creation_id": container_id}
```

### 8.5 Görsel Normalizasyonu

Sabitler (`content_service.py:1579`):
- Instagram feed: 1080×1350 (4:5)
- Instagram story: 1080×1920 (9:16)
- Facebook page: 1200×630

Pipeline: download → PIL `ImageOps.contain()` → beyaz canvas padding → JPEG q=92 → upload (R2 veya local). Skip flag: `INSTAGRAM_SKIP_IMAGE_ASPECT_NORMALIZE=1`.

### 8.6 Async Task Queue (`image_tasks.py`)

Celery destekli (`USE_CELERY=true`); değilse `_sync_generate_images_task()` fallback. Görev'ler:
- `generate_images_task` (max_retries=2)
- `revise_image_task` (max_retries=2)
- `caption_generate_task` (max_retries=2)
- `video_generate_task` (max_retries=1)

Progress states: 10 (analiz), 30 (prompt), 60 (model), 100 (depolama).

### 8.7 Banner (Kampanya) — Ayrı Yol

`POST /social-media/campaign/publish` → `_sepetler_publish_campaign_banner()` → Sepetler AI API `/banners` endpoint. **Instagram Graph kullanılmaz**, CIS'ten geçmez.

### 8.8 Chat-Tetikli One-Shot Görsel Pipeline (YENİ)

Operatör chat'inden gelen aksiyon komutu LangGraph'i tetikleyince
`content_generator_node` (`langgraph_engine/nodes.py`) şu sırayla çalışır:

```
content_generator_node(state)
   ├─ Şablon seçimi (4-katmanlı öncelik, "generic" string'i SHORT-CIRCUIT etmiyor)
   │     params.template != "generic"  → kullan
   │   ‖ content.template != "generic" → kullan
   │   ‖ rule.target_template          → kullan (/mers gibi custom adlar)
   │   ‖ "generic"                     → MySQL fetch atlanır
   │
   ├─ _fetch_template_from_mysql(template, channel, module)
   │     PG social_documents (eskiden MySQL — SQL şimdi payload->>'title' formunda)
   │     Story için outputSize='story' önceliklendirme
   │     Bulunan payload'taki imageUrls _check_url_alive ile süzülür
   │
   ├─ AI caption (OpenAI gpt-4o-mini)
   │     event_payload.name/price/brand/category + nested item/store + reviews_positive
   │
   ├─ Görsel boyutu seçimi
   │     template_data.outputSize ‖ params.output_size ‖ channel-tabanlı fallback:
   │         story/instagram_story → "story"      (1088×1920)
   │         post/instagram/facebook → "post"     (1088×1360)
   │         banner/campaign_banner  → "campaign_banner" (1600×704)
   │     Banner kanalı her zaman campaign_banner ile override
   │
   ├─ Referans görselleri topla
   │     Öncelik: ürün → mağaza logosu → şablon görseli (alive filtreli)
   │
   ├─ Şablon + Ürün birleştirme dispatch:
   │
   │   Eğer HEM şablon HEM ürün görseli varsa → REVISE yolu:
   │     _sync_revise_image_task(
   │         image_url=template_primary,        # zemin
   │         feedback=prompt,                    # şablon prompt + ürün bilgileri
   │         reference_image_urls=revise_refs,   # template_primary HARİÇ
   │                                             # (UI'nin seen.add(layoutUrl) semantiği)
   │         revision_context="social"|"campaign_banner",
   │     )
   │   → app/services/content_service.revise_image_with_feedback
   │     OpenAI multi-file edit: primary.png + contextN.png[]
   │
   │   Aksi halde → GENERATE yolu:
   │     _sync_generate_images_task(reference_image_url=primary_ref, ...)
   │   → generate_images_from_reference (img2img)
   │   → veya pure generate_images (text→image, son çare)
   │
   └─ Üretilen URL'i state.content.image_url'e yaz
```

**`_check_url_alive` (kritik):**
- Lokal `/media/` URL'leri için **filesystem-direct** kontrol — `MEDIA_ROOT` altında
  dosya gerçekten var mı (`Path.is_file()`). HTTP self-loopback YAPMA.
- Sebep: tek-worker uvicorn altında chat handler worker'ı kilitlendiği için
  127.0.0.1:8000'e sync HEAD self-deadlock'a giriyor → 5s timeout → tüm local
  URL'ler "ölü" sayılır → şablon yolu çöker. Filesystem check bu sorunu çözer
  ve dead-file detection'ı korur.
- Harici URL'ler için HEAD → GET fallback (mevcut davranış).

**Önemli işbirliği davranışları:**
- `business_chat._one_shot_run` event_payload'a hem flat alanlar (`primary_image_url`,
  `image_url`, `image_urls`, `store_logo_url`, `logo_url`, `banner_url`,
  `store_banner_url`) hem nested objeler (`item`, `store`) hem de outer seviyede
  `item` / `store` koyar. content_generator_node bunların hangisinden okursa
  okusun referans görseli bulur.
- `caption` üretimi açıklamayı son 5 olumlu yorumla zenginleştirir (`description +
  "\n\nMüşteri yorumları:\n- ..."`).
- Custom şablon adı (örn. `/mers`) PG'de eşleşme bulursa kullanılır; yoksa "generic"
  fallback'e graceful düşüş.

### Eksikler
- **Token encryption** yok — `social_credentials.encrypted_token_blob` ismi geçiyor ama gerçek encrypt/decrypt logic'i belirsiz; plain-text flow var.
- Rate-limit error #2207051 retry logic tek-seferli.
- Container ready timeout (120s feed, 600s video) — uzun network kesintilerinde fail.
- Approval edge case'leri (partial edit, multi-edit) test edilmemiş.
- CIS prompt builders ve campaign banner builder duplicate kod.

### Geliştirme notları
- `social_credentials.encrypted_token_blob` için Fernet/age tabanlı encrypt katmanı.
- Retry policy: exponential backoff + jitter.
- Approval type'ları için tek bir state machine (`pending → reviewing → approved/rejected → published`).
- Banner ve post pipeline'ı tek prompt builder altında birleştirme.

---

## 9. Kural Motoru

### 9.1 Kural Şeması (`structured_rule.py`)

`StructuredRule` Pydantic modeli:
- **TriggerSpec** — event_type (store.created, product.updated...) + condition expression
- **TimingSpec** — `delay_seconds` veya `schedule_at` (absolute time)
- **TargetSpec** — hedef hesap, entity tip + filtreler
- **ContentSpec** — şablon (`anneler_gunu`, `yilbasi`, ...) + kanal (`instagram`/`facebook`/`banner`/`email`)
- **ActionStep[]** — wait, generate_content, risk_check, approval, publish, notify_customer, monitor
- **GraphDefinition** — opsiyonel; LangGraph compiler için node listesi + dependencies + interrupt noktaları

### 9.2 Event → Rule Dispatch

```
Timeline event (fake_ai_api.db.timeline)
   ↓
listener.process_event()
   ├─ EventEnvelope.from_legacy() — typed envelope
   ├─ event_router.route_event() — critical/creative/hybrid/analytical/monitoring
   ├─ resolve_user_id_from_event()
   ├─ resource_service.fetch_*() — entity cache
   ↓
structured_rule_engine.find_matching_rules()
   ├─ enabled=1 AND trigger.event_type match
   ↓
   ├─── Rules matched? YES ───→ langgraph_engine.invoke(rule)
   │        StateGraph: wait → generate_content → risk_check → approval_gate → publish → monitor
   │        ↓
   │    rule_executions + graph_node_traces yazılır
   │
   └─── Rules matched? NO ───→ event_router.should_use_autonomous()?
            ├─ critical event? → SKIP
            ├─ creative/analytical? → autonomous_planner.propose_action()
            ↓
        plan {decision, workflow_name, tools, business_intent, requires_approval, confidence}
            ├─ confidence ≥ CONFIDENCE_AUTO_APPLY?
            │    ├─ YES + requires_approval → approval_requests INSERT
            │    └─ YES + !requires_approval → workflow_service.create_workflow()
            └─ NO → noop
        ↓
    planner_memory.record_plan() — audit trail
```

### 9.3 Tanınmış Event Türleri (`event_envelope.TRIGGER_EVENT_TYPES`)

~34 tip:
- **Commerce**: order.created, order.shipped, stock.updated, shipping.delayed
- **Marketing**: banner.created, campaign.created, story.created, coupon.created
- **Support**: review.created, review.negative, customer.question
- **Platform**: store.created, store.updated, store.rejected, store.deleted, product.created, product.updated

Ancak `fake_ai_api.db.timeline`'da gerçekte **sadece 5 distinct kompozit anahtar** var (`banner|executed`, `campaign|executed`, `customer|executed`, `product|created`, `store|created`). Yani şema 34 tip destekliyor ama mock üretici çoğunu üretmiyor.

### 9.4 Dosya Sorumluluk Matrisi

| Dosya | Sorumluluk |
|---|---|
| `structured_rule.py` | Kural Pydantic şeması |
| `structured_rule_engine.py` | CRUD + rule matching + graph kickoff |
| `rule_engine.py` | Legacy DSL parsing |
| `rule_manager.py` | Versiyonlama + conflict detect |
| `rule_service.py` | DB fetch + in-memory cache |
| `nl_rule_parser.py` | Türkçe NL → StructuredRule (regex prefilter + gpt-4o-mini) |
| `event_envelope.py` | Legacy timeline → typed envelope |
| `event_router.py` | Event classification |
| `listener.py` | Timeline polling + orchestration |
| `langgraph_engine/runtime.py` | StructuredRule → StateGraph compiler |
| `langgraph_engine/state.py` | RuleExecutionState + reducers |
| `langgraph_engine/nodes.py` | Node logic (supervisor, wait, content_generator, risk_analyzer, approval_gate, publisher, monitor, notify_customer, create_coupon, finalize) |
| `approval_service.py` | Approval gate + human-in-the-loop |
| `autonomous_planner.py` | Business intent inference (LLM destekli) |
| `ai_planner.py` | Heuristic fallback (hard-coded event tipleri) |
| `planner_runtime.py` | Plan execution via workflow_service |
| `planner_memory.py` | Proposal + outcome learning |
| `conversational_rule_edit.py` | Chat'ten kural editi (toggle, delay, channel, delete) |
| `rule_templates.py` | Hazır template'ler |
| `rule_learning.py` | Outcome → öneri (boost/suppress) |

### 9.5 LangGraph Engine

`langgraph_engine/`:
- **`runtime.py`**: StructuredRule → StateGraph compile. Canonical sıra: `wait → generate_content → create_coupon → risk_check → approval → publish → notify_customer → monitor`. SqliteSaver checkpointer ile state persist. `approval_gate` `interrupt_before`, `wait` `interrupt_after`.
- **`state.py`**: TypedDict `RuleExecutionState` — sub-model'lar (EventContext, GeneratedContent, RiskAssessment, ApprovalDecision, PublishResult, MonitorResult), `trace_events` append-only.
- **`nodes.py`**: Her node trace start → işi yap (LLM/tool) → trace end → partial state. Hata raise etmez; `state.status="failed"` + `last_error` yazar.

### 9.6 Natural Language Rule Parser

`nl_rule_parser.py` (43KB) hibrit yaklaşım:
1. **Regex prefilter** — Türkçe event kalıpları ("yeni mağaza"→store.created), zaman ("3 gün sonra"→259200s), kanal, şablon, action verb.
2. **LLM** (gpt-4o-mini) — prefilter bulgularını + ham metni gönderip Pydantic schema'sına uygun JSON ürettirir.
3. **Validation** — StructuredRule(**resp). Başarısızsa `parse_confidence` düşer, `enabled=False`.

LLM offline da çalışır (sadece prefilter).

### Eksikler
- Event türü taksonomisi geniş tanımlı ama mock'ta dar; `review|created`, `order|placed` gibi alt tipler timeline'da yok.
- LangGraph node setleri sabit — yeni node tipi eklemek runtime ve nodes.py'ı değiştirmek gerekiyor.
- `rule_engine.py` (legacy DSL) ve `structured_rule_engine.py` ikisi de aktif — duplikasyon.
- Rule cache invalidation manuel.

### Geliştirme notları
- `fake_commerce_app.py` mutator'larını zenginleştir: `review|created`, `question|asked`, `order|placed`, `order|cancelled`, `shipping|delayed`, `banner|approved/rejected` üretsin.
- Legacy `rule_engine.py` deprecate edilebilir.
- Cache invalidation event-driven (`rule_service.invalidate_cache(user_id)`).

---

## 10. Instagram Entegrasyonu

### 10.1 Bileşenler

| Dosya | Sorumluluk |
|---|---|
| `app/integrations/instagram_client.py` | Graph API v22.0 client — account list, user_id resolve, image URL validate |
| `app/services/content_service.py` | `post_to_instagram`, `post_carousel_to_instagram`, `post_story_to_instagram`, `post_story_batch_to_instagram`, reel container, görsel normalize |
| `app/api/social_media.py:525` | `/social-media/instagram/linked-accounts` endpoint |
| `app/agents/tools/social_media_tools.py:58` | `tool_instagram_post` — CrewAI/LangGraph aracı |
| `app/tasks/image_tasks.py:323` | `_publish_instagram_post`, `_publish_instagram_story` — async publish |
| `tool_adapters/facebook.py` | **Legacy** v18.0 Graph adapter (parallel) |

### 10.2 Token Yönetimi

- `social_credentials` tablosunda `(provider, account_handle, encrypted_token_blob, scope, token_expires_at)`.
- `_resolve_credentials(access_token, instagram_user_id)` — workspace settings veya request body'den okur.
- OAuthException 190 → otomatik refresh (`_post_with_auto_refresh`).
- `validate_instagram_image_url()` — Instagram'a gönderilmeden önce URL erişilebilirlik check.

### 10.3 Ontology Bağlantısı

`ontology.py:56,62,68` — kural ontolojisinde `instagram_campaign_tool` default tool olarak listeli.

`structured_rule.py:224` — kuralların default platform'u "instagram".

### Eksikler
- **İki versiyon paralel**: yeni `app/integrations/instagram_client.py` v22.0, legacy `tool_adapters/facebook.py` v18.0. Tutarsız.
- Encrypt katmanı belirsiz — `encrypted_token_blob` ismi var ama gerçek encrypt fonksiyonu görünmüyor (plain text saklanıyor olabilir).
- Instagram DM webhook'u yok — `customer_threads` channel='instagram_dm' şeması hazır ama veri girişi yok.
- Instagram metric retrieval yok — "son 10 post engagement'ı" chat intent'i yok.

### Geliştirme notları
- v18.0 legacy adapter sil, çağrıları yeni client'a yönlendir.
- `social_credentials.encrypted_token_blob` için Fernet (`cryptography` kütüphanesi) ile gerçek encrypt.
- Meta Graph webhook receiver → `customer_interaction_service.ingest_message(channel="instagram_dm")`.
- Yeni retrieval intent'leri: `instagram_post_performance`, `instagram_engagement_overview`.

---

## 11. Sepetler API

### 11.1 Bağlantı

`DEFAULT_CAMPAIGN_API_BASE_URL = "https://mtlive.sepetler.com/api/ai/v1"` (`app/api/social_media.py:134`).

`fake_commerce_app.py:36` — mock app'in başlığı **"Fake Sepetler AI API"** — yani lokal mock Sepetler API'sini taklit ediyor; aynı `/api/ai/v1` prefix'i.

### 11.2 Akış

- `_campaign_api_is_sepetler_ai_v1(base_url)` — host'a göre branch.
- IP whitelist çağrısı (`app/api/social_media.py:513`): "Sepetler API: kaynak IP'yi whitelist'e ekler. Bu çağrı yapılmadan…".
- `_sepetler_fetch_resource_list` (:608) — campaign verisi çek.
- `_normalize_sepetler_campaign_item` (:639) — Sepetler item'ını internal şema'ya map.
- 401/403 → whitelist tekrar çağırılır (:583).

### 11.3 Kampanya Yayını

`POST /social-media/campaign/publish` → `_sepetler_publish_campaign_banner()` → `POST {sepetler_base}/banners`. Instagram Graph kullanılmaz.

### Eksikler
- **Settings UI'da Sepetler sekmesi yok** — base URL hardcoded, multi-tenant deploy mümkün değil.
- Sepetler için credential bağlama akışı (OAuth-like) yok; token nasıl `social_credentials`'a giriyor belirsiz (büyük olasılıkla manuel).
- Trendyol da benzer şekilde provider listesinde (`structured_rule.py:65`, `social_credentials.py:57`) ama UI ve OAuth akışı yok.

### Geliştirme notları
- `views/settings/` altına `sepetler.php` ekle: `api_base_url`, `api_key`, "Test Bağlantı" butonu.
- `POST /api/internal/credentials` ile Sepetler için token kaydı (provider='sepetler').
- Sepetler tenant config'i `org` bazlı (`orgs.sepetler_base_url` kolonu).

---

## 12. Genel Eksikler ve Öncelik Haritası

### En kritik 4 boşluk

1. **Mağazalar sayfasında chat yok** — operatör chat'i ve commerce verisi farklı UI'larda. Chat widget'ı buraya gömülmeli.
2. **İki commerce DB tek chat'ten görünmüyor** — operatör chat sadece `listener.db` okuyor. `fake_ai_api.db` (reviews/questions/orders/timeline) chat'ten erişilemiyor.
3. **İki chat sistemi izole** — `business_chat` (analitik+edit) ve `automation/chat-trigger` (NL→JSON) konuşmuyor. Tek pipeline gerekli.
4. **API auth yok + key yönetim UI yok** — `/api/internal/*` test mode, `api_keys` tablosu kullanılmıyor, Sepetler URL hardcoded.

### Hızlı kazanımlar (1-2 gün)

- Ölü SSE kodunu sil (`runPipeline` içindeki streamRequest, splitSse, getStreamNode artıkları).
- `humanized-timeline`'a `product_id` / `store_id` filter parametresi.
- `chat_turns` şemasına `model, prompt_tokens, completion_tokens, latency_ms` kolonları (synth zaten bu değerleri tutuyor, kaydedilmiyor).
- Legacy `tool_adapters/facebook.py` v18.0 silmek.
- Üst dizindeki boş `listener.db` / `fake_ai_api.db` dosyalarını silmek (karışıklık riski).

### Orta vade (1-2 hafta)

- `stores.php` içine chat widget'ı + entity-aware retrieval (`primary_entity_id` mekanizması zaten var).
- Sepetler / Trendyol / Instagram için settings UI sekmeleri + OAuth-like credential bağlama.
- Customer thread'leri chat retrieval intent'i olarak açma ("müşteri X'e cevap taslağı").
- API key middleware (`Depends(verify_api_key)` ile `api_keys.key_hash` kontrolü).
- `fake_commerce_app.py` mutator'larını zenginleştir → event taksonomisi gerçek hale gelsin.
- `social_credentials.encrypted_token_blob` için gerçek Fernet encrypt.

### Uzun vade

- LangGraph state'ini WebSocket ile UI'a stream (stages + token-by-token cevap).
- Üç DB'yi tek source-of-truth'a konsolide (Alembic migration disiplini).
- Mock endpoint'leri (`/social-media/mock/*`) prod build'inden çıkar.
- Bundling (`vite` veya `esbuild`) — 40+ JS dosyası tek bundle olmalı.
- 287 endpoint'in OpenAPI tag düzeni + auth/rate-limit politikası.

---

## Ek — Modül Bağımlılık Haritası (önemli akışlar)

```
PHP UI (php-ui/)
   │
   ├── /social-media          → social-media-app.js          → /api/internal/posts*
   ├── /social-media/system-admin → timeline-store-automation.js → /api/internal/chat
   ├── /stores                → stores-app.js                → /commerce-platform/*
   ├── /triggers              → triggers-app.js              → /api/internal/structured-rules
   ├── /social-media/onay-bekleyenler → approvals-app.js + internal-approvals.js → /api/internal/approvals
   └── /settings/api-keys     → usage-app.js                 → /api/internal/usage

FastAPI (agent-base-api/)
   │
   ├── app/main.py
   │    ├── auth + social_data + social_media (yeni)
   │    ├── orchestration_api router (legacy, /api/internal/*)
   │    └── fake_commerce_app mount (/commerce-platform)
   │
   ├── /api/internal/chat → business_chat.py
   │    ├── _classify_action_or_query → gpt-4o-mini (max_tokens=10)
   │    │     ├── 'action' → _one_shot_run:
   │    │     │      nl_rule_parser → save_rule → start_execution → disable
   │    │     │      (LangGraph: wait → generate_content → approval_gate → publish_*)
   │    │     └── 'query' → retrieval + synthesizer (mevcut yol)
   │    ├── conversation_memory.py (chat_sessions, chat_turns)
   │    ├── business_query_router.py (PG full snapshot — keyword YOK)
   │    └── ai_synthesizer.py (gpt-4o-mini, format_pg_context, 5-kural sistem prompt)
   │
   ├── listener.py
   │    ├── EventEnvelope (event_envelope.py)
   │    ├── event_router.py (classify)
   │    ├── structured_rule_engine.py (match)
   │    │    └── langgraph_engine/runtime.py → nodes.py
   │    │         └── approval_service.py (interrupt)
   │    └── autonomous_planner.py (no rule match fallback)
   │         └── ai_planner.py (heuristic fallback)
   │
   └── /social-media/post → app/services/content_service.py
        ├── ContentIntelligenceService (analyze)
        ├── PromptBuilder + openai_ad_pipeline (gpt-image-2)
        ├── task_dispatcher.py (Celery / sync)
        └── app/integrations/instagram_client.py (Graph API v22.0)
```

---

## 13. Son Düzeltmeler ve Bilinen Sorunlar (Changelog)

### 2026-06-04 — Chat one-shot + PG migrasyonu + görsel pipeline

**Chat 1 (business_chat) — yeni one-shot workflow tetikleyici:**
- `conversational_rule_edit` early-dispatch bloğu **KALDIRILDI** (`business_chat.py:319-388`).
  Substring-tabanlı detector ("aç" trigger'ı "kaç" içinde geçince) normal soruları
  rule mutasyonuna dönüştürüyordu (örn. "Kaç ürün var?" → "Yeni ürün hikayesi
  kuralını tekrar etkinleştirdim").
- Yerine **LLM tabanlı action/query sınıflandırıcı** geldi (`_classify_action_or_query`).
  gpt-4o-mini, max_tokens=10, temperature=0 — ucuz prefilter.
- `_one_shot_run` action komutlarını alır: nl_rule_parser ile parse → save_rule
  (enabled=True) → start_execution → set_enabled(False) (one-shot garantisi).
- Türkçe özet UI'a döner; waiting_human ise operatöre "Onay bekleyenler sayfasına
  düştü" yönlendirmesi.

**Veri katmanı — PG'ye migrasyon:**
- `app/core/database.py` tek `SessionLocal`, env DATABASE_URL üzerinden PG.
- Yeni modeller: `Store`, `Product`, `ProductImage`, `ProductReview`, `ProductFaq`,
  `ProductMetricsWeekly`, `ChatSession`, `ChatMessage`, `SocialDocument`.
- `business_query_router` **tamamen yeniden yazıldı** — saf veri çekici:
  PG'den tüm stores + products (yorumlar + SSS gömülü) tek `full_context` payload'ı
  döner. Keyword listesi, Türkçe stem matching, intent detection — **HEPSİ
  KALDIRILDI**. Kullanıcı ne yazarsa yazsın aynı snapshot LLM'e gider.
- `ai_synthesizer._SYSTEM_PROMPT` 5 kuralla yeniden yazıldı: (1) veri tek kaynak,
  (2) liste uzunluğu = sayı, (3) bağlam mirası yasak, (4) yorum yazımı düzelt,
  (5) jargon yok.
- `pg_context_formatter._format_full_context` mağaza/ürün sayılarını net etiketlerle
  ayırıyor (`MAĞAZA SAYISI: N`, `ÜRÜN SAYISI: M`, "Bu iki sayıyı asla
  birbirinin yerine kullanma" özeti).

**Görsel üretim pipeline (langgraph_engine/nodes.py):**
- `content_generator_node` artık şablon+ürün birleştirmesi yapıyor.
  HEM şablon HEM ürün varsa `_sync_revise_image_task` (UI'nın "Görseli Revize Et"
  semantiği); aksi halde `_sync_generate_images_task` (img2img veya text-to-image).
- Şablon seçimi 4-katmanlı (`params → content.template → target_template → generic`),
  "generic" string'inin short-circuit etmesi engellendi.
- `_fetch_template_from_mysql` (isim tarihsel) SQL'i PG operatörlerine çevrildi —
  `JSON_UNQUOTE(JSON_EXTRACT(payload,'$.field'))` → `payload->>'field'`.
  social_documents tablosu PG'de (`SocialDocument(Base)`).
- `output_size` channel-tabanlı fallback eklendi (`story` 1088×1920, `post`
  1088×1360, `banner` 1600×704).
- `_check_url_alive` lokal URL'ler için **filesystem-direct** kontrol —
  `MEDIA_ROOT` altında `Path.is_file()`. HTTP self-loopback YAPMA: tek-worker
  uvicorn altında chat handler ile aynı worker'a sync HEAD self-deadlock'a
  giriyordu (5s timeout → tüm local URL'ler "ölü" → şablon path'ı çöküyordu).
- Revise dispatch'inde `template_primary` reference_image_urls'ten çıkarılıyor
  (UI'nın `seen.add(layoutUrl)` semantiği) — OpenAI multi-file edit'i aynı
  görseli hem primary hem context olarak almıyor.
- `_one_shot_find_product` `Product.images` relationship'iyle ürün görsellerini
  çekiyor + bağlı `Store`'un logo/banner'ını da alıyor. event_payload'a hem
  flat (`primary_image_url`, `image_url`, `image_urls`, `store_logo_url`,
  `banner_url`) hem nested (`item`, `store`) hem outer-event (`event.item`,
  `event.store`) seviyede koyuluyor — content_generator_node hangi yoldan
  okursa okusun bulur.

**Bilinen sorunlar:**
- **Lokal media URL'ler dışarıdan erişilemez**: `MEDIA_PUBLIC_BASE_URL=http://127.0.0.1:8000`
  ile çalışırken UI veya Instagram publish hedefi farklı host'taysa URL'leri
  fetch edemez. Üretim: ya R2 (`MEDIA_STORAGE=r2` + R2 keys) ya da sunucunun
  gerçek public hostname'i `MEDIA_PUBLIC_BASE_URL`'e.
- **Reject sonrası graph donar**: `approval_service.reject` listener.db'ye
  status='rejected' yazar AMA `resume_execution` çağrılmaz — `rule_executions`
  waiting_human'da kalır. Fix: reject akışına da resume eklemek.
- **`SOCIAL_PUBLISH_LIVE=0` default**: gerçek Instagram/Facebook HTTP çağrısı
  yok, sadece draft kayıtları üretiliyor.
- **One-shot NL → "şimdi tek sefer çalıştır"** için temiz API yolu yok. Şu an
  sentetik event ile graph'i tetikleyip rule'u sonradan disable etme yöntemi
  kullanılıyor. Recurrence semantiği `structured_rule_engine` tarafında
  zayıf — kuralı disable etmek tek garantördür.
- **Custom şablon adları yarım yaşıyor** (`/mers` gibi): `content.template`
  whitelist sebebiyle "generic"te kalır; sadece graph node param'ında ve
  PG fuzzy lookup'ında korunur. Whitelist'i dinamik (PG'den) kılmak gerek.

---

Bu doküman 2026-06-04 itibariyle mevcut kod tabanını yansıtır. Yeni özellik
eklendikçe ilgili bölüm güncellenmelidir.
