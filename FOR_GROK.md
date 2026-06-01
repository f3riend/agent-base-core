# FOR_GROK.md — Agent Base Sistem Mimarisi (Tam Brief)

> **Kime:** Başka bir AI ajanına (Grok) verilen self-contained sistem
> haritası. Repo dışı kaynaklara bağımlı değil; her şey burada.
>
> **Konum:** `/home/bypasa10/Desktop/rule-based-engine/agent-base/`
> **Tarih:** 2026-05-27 (Tur 5 polish-2 + bug-fix sonrası)
> **Statü:** Çalışır (`uv run uvicorn app.main:app` + `make run`).
> **Backend:** Python 3.11 + FastAPI + LangGraph + SQLite + MySQL.
> **Frontend:** PHP 8 standalone (php-ui) + JS modules + premium CSS.
> **Deployment:** Tek Docker container (all-in-one), supervisord ile 7
> process.

---

## 0. Bu Doküman Niye Var

Sistem 5 turluk evrim geçirdi. Grok'un (veya başka bir Claude
session'ın) kolayca tam resmi alabilmesi için:

- **Klasör yapısı** — neyin nerede olduğu
- **Çalışma akışı** — event → rule → execution
- **Endpoint sözleşmeleri** — request/response shape'leri net
- **DB tabloları** — anahtar ilişkiler
- **Çözülen bug'lar** — hangi pattern hangi konumda
- **Açık iş** — Tur 6 öncesi yapılması gerekenler

Sadece bu dokümanı + repo'yu okuyarak değişiklik yapabilir.

---

## 1. Üst Düzey Mimari

```
┌──────────────────────────────────────────────────────────────────┐
│  agent-base-allinone (TEK Docker container)                       │
│                                                                   │
│  nginx :80                                                        │
│    ├──► /api/*    → uvicorn :8000  (FastAPI: app.main + orch.)    │
│    └──► /*        → php-fpm        (php-ui)                       │
│                                                                   │
│  supervisord process'leri (priority sırasıyla):                   │
│    api          (uvicorn)              p10                        │
│    worker       (celery)               p15                        │
│    rbe-listener (listener.py)          p16                        │
│    rbe-workflow (workflow_worker.py)   p17                        │
│    rbe-task     (task_executor.py)     p17                        │
│    php-fpm                             p18                        │
│    nginx                               p20                        │
│                                                                   │
│  Bağlı servisler (depends_on):                                    │
│    MySQL (8.0) — agent-base şeması (users, social_data, sm_*)     │
│    Redis (7)   — celery broker                                    │
└──────────────────────────────────────────────────────────────────┘
```

### Üç Katman

1. **Sense (algı)** — `fake_commerce_app` veya gerçek e-ticaret
   platformu `fake_ai_api.db.timeline` tablosuna event yazar
   (`store.created`, `stock.updated`, `order.shipped`,
   `review.negative`, ...).
2. **Reason (akıl)** — `listener.py` 2 saniyede bir timeline'ı
   polling eder ve **üç paralel yola** tetik gönderir:
   - **Structured Rules (LangGraph)** — Pydantic-validated kanonik
     kurallar.
   - **Critical/legacy rules** — `rule_engine.find_matching_rules`.
   - **Autonomous planner** — LLM-backed yaratıcı planlayıcı.
3. **Act (eylem)** — LangGraph node'ları + `tool_adapters/` (Instagram,
   Facebook, TikTok stub).

**Prensip:** *AI proposes, runtime executes.* LLM kanonik
`StructuredRule` JSON üretir; çalıştırmayı LangGraph
(deterministic StateGraph + SqliteSaver checkpoint) yapar.

---

## 2. Klasör Yapısı

```
agent-base/                                  ← MONOREPO KÖKÜ
│
├─ docker-compose.yml, Dockerfile, Makefile, README.md
├─ FOR_GROK.md                                ← bu dosya
├─ ÖZET.md                                    ← Türkçe operasyonel özet
├─ SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md      ← Tur 1-5 evrim tarihçesi
│
├─ docker/
│   ├─ supervisord.conf                       (7 process tanımı)
│   ├─ nginx.conf, nginx.conf.template
│   └─ proxy/, layout-*.sh, write-*.sh
│
├─ agent-base-api/                            ← Python backend
│   │
│   ├─ pyproject.toml                         (langgraph + cryptography; CrewAI yok)
│   ├─ uv.lock
│   ├─ alembic/                               (versions/006_*, 007_*)
│   ├─ config/, data/, logs/, scripts/
│   │
│   ├─ app/main.py                            ← FastAPI birleşik entrypoint
│   │
│   ├─ app/                                   (mevcut agent-base submodule)
│   │   ├─ api/{auth,client_debug,social_data,social_media}.py
│   │   ├─ agents/manager/agent_factory.py    (LightAgent — CrewAI yok)
│   │   ├─ services/agent_runtime_service.py  (native OpenAI/Gemini)
│   │   ├─ core/{celery_app,config,database,settings,security}.py
│   │   ├─ integrations/{ai_client,instagram_client,r2_storage,smtp_client}.py
│   │   ├─ models/                            (SQLAlchemy ORM)
│   │   ├─ routers/social_media.py
│   │   ├─ runtime/                           (orchestrator, fsm, policy_engine, ...)
│   │   └─ services/{content,task_dispatcher,prompt_builder,...}.py
│   │
│   ├─ langgraph_engine/                      ← LangGraph runtime
│   │   ├─ state.py    (RuleExecutionState TypedDict + Pydantic alt-modeller)
│   │   ├─ nodes.py    (10 node: supervisor, wait, content_gen, risk, approval, ...)
│   │   └─ runtime.py  (build_graph + start_execution + resume_after_wait)
│   │
│   ├─ tool_adapters/                         ← Gerçek sosyal medya
│   │   ├─ instagram.py  (Graph API publish; SOCIAL_PUBLISH_LIVE gated)
│   │   ├─ facebook.py
│   │   └─ tiktok.py     (stub)
│   │
│   └─ 68 flat .py modülü                     (rule-based-engine'den taşındı)
│       db.py, orchestration_api.py, listener.py,
│       workflow_worker.py, task_executor.py,
│       structured_rule.py, structured_rule_engine.py,
│       nl_rule_parser.py, semantic_entity_resolver.py,
│       conversational_rule_edit.py, rule_templates.py,
│       rule_learning.py, conflict_resolver.py,
│       ai_planner.py, autonomous_planner.py,
│       business_chat.py, scheduling_service.py,
│       campaign_service.py, customer_interaction_service.py,
│       auth_service.py, social_credentials.py,
│       fake_commerce_app.py, tools.py, ...
│
└─ php-ui/                                    ← PHP frontend
    │
    ├─ includes/{bootstrap,auth,config,http,i18n}.php
    ├─ locale/, styles/
    │
    ├─ public/
    │   ├─ index.php                          (front controller; route'lar)
    │   ├─ router.php
    │   └─ assets/
    │       ├─ css/
    │       │   ├─ app.css, sm-premium-ui.css, sm-tags-ui.css
    │       │   └─ timeline-rules.css         ← Tur 5 polish-2 (678 satır)
    │       └─ js/
    │           ├─ app-shell.js
    │           ├─ social-media-app.js, social-media-*.js
    │           ├─ approvals-app.js, sm-tags-app.js, sm-templates-app.js
    │           ├─ settings-app.js
    │           ├─ timeline-store-automation.js (1182 satır — system_admin chat)
    │           └─ timeline-page-rules.js     ← Tur 5 polish-2 (~720 satır)
    │
    └─ views/
        ├─ layout.php                         (sidebar; Kurallar linki YOK)
        ├─ login.php, register.php, forgot_password.php, reset_*.php
        ├─ social_media.php                   (sosyal medya takvimi)
        ├─ approvals.php                      (onay bekleyenler)
        ├─ sm_tags.php, sm_templates.php      (etiket + post şablonları)
        ├─ system_admin.php                   (AI OPERATÖR MERKEZİ)
        ├─ page.php                           (generic + timeline rules toolbar)
        ├─ settings/{account,workspace,ai,api_keys,automation,security}.php
        └─ timeline/
            ├─ _rules_toolbar.php             ← Tur 5 contextual panel markup
            └─ store_page.php
```

---

## 3. Çalışma Akışı (Event → Rule → Execution)

```
┌──────────────────────┐
│ Operatör Türkçe NL    │   Örnek:
│                      │   "Yeni banner oluştuğunda bu banneri
│                      │    sosyal medya hesabında da paylaş"
└───────────┬──────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│ Composer (/page/timeline/<slug> veya /social-media/system-   │
│ admin chat)                                                  │
│                                                              │
│  POST /api/internal/structured-rules/parse                   │
│  body: { natural_language: <text>, name?, user_id? }         │
│  resp: { rule: {...}, explanation, parse_confidence,         │
│           missing_fields }                                   │
└───────────┬──────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│ Preview render (stepper):                                    │
│   ▶ başla → generate_content → risk_check → ⏸ approval →     │
│   📤 publish → ✓ bitir                                       │
└───────────┬──────────────────────────────────────────────────┘
            │
            ▼ Kuralı Etkinleştir
┌──────────────────────────────────────────────────────────────┐
│ POST /api/internal/structured-rules                          │
│ body: { natural_language, name?, user_id?, enabled: true }   │
│ resp: { data: rule }                                         │
│ INSERT INTO structured_rules                                 │
└──────────────────────────────────────────────────────────────┘

══════ ARKAPLAN ══════
┌──────────────────────────────────────────────────────────────┐
│ fake_commerce_app veya gerçek e-ticaret                      │
│ POST /commerce-platform/internal/create-*                    │
│ INSERT INTO fake_ai_api.db.timeline                          │
└───────────┬──────────────────────────────────────────────────┘
            │
            ▼ (2s polling)
┌──────────────────────────────────────────────────────────────┐
│ listener.py process_event(event)                             │
│ ├─ EventEnvelope.from_legacy                                 │
│ ├─ SKIP_SYNTHETIC kontrolü                                   │
│ ├─ resolve_user_id_from_event                                │
│ ├─ route_event → "critical" | "monitoring" | "creative"      │
│ └─ Üç paralel matching path:                                 │
│    ┌──────────────────────────────────────────────────────┐ │
│    │ 1) structured_rule_engine.trigger_rules_for_event    │ │
│    │    └─ runtime.start_execution(rule, event, user_id)  │ │
│    │       └─ build_graph(rule):                          │ │
│    │           supervisor → (wait ⏸) → content_gen →      │ │
│    │           risk → (approval ⏸) → publish → monitor →  │ │
│    │           finalize                                   │ │
│    │           interrupt_before=["approval"]              │ │
│    │           interrupt_after=["wait"]                   │ │
│    │           SqliteSaver checkpoint                     │ │
│    │                                                      │ │
│    │ 2) rule_engine.find_matching_rules                   │ │
│    │    └─ action_engine.execute_rule_actions             │ │
│    │                                                      │ │
│    │ 3) autonomous_planner (creative route)               │ │
│    │    └─ planner_runtime.handle_autonomous_event        │ │
│    └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘

Wait path:
   wait_node → scheduled_entries INSERT (payload.resume_after_wait=True)
   ↓
   workflow_worker.py (5s loop)
   ↓
   fire_due_schedules() → status='fired'
   ↓
   _handle_wait_resumes() → runtime.resume_after_wait(execution_id)
   ↓
   SqliteSaver checkpoint'inden devam → kalan node'lar çalışır

Approval path:
   approval_gate_node interrupt_before öncesi pause
   ↓
   approval_requests INSERT
   ↓
   php-ui /onay-bekleyenler veya /page/timeline/* > kart > onayla
   ↓
   POST /api/internal/approvals/{id}/approve
   ↓
   runtime.resume_execution(execution_id, approved=True)
   ↓
   publisher_node → tool_adapters.<channel>.publish()
   ↓
   SOCIAL_PUBLISH_LIVE=0 → FeatureDisabledError → mock log
   SOCIAL_PUBLISH_LIVE=1 → Graph API gerçek post → result kaydet

Sonuç:
   rule_executions.status='completed', finished_at=now
   graph_node_traces INSERT per-node
   rule_learning.record_outcome(rule_id, success)
   health_score güncellenir
```

---

## 4. Endpoint Sözleşmeleri (Tur 5 Doğrulanmış)

**Tüm endpoint'ler `/api/internal/*` mount path'inde.** Backend
`orchestration_api.py` router'ı. Response shape **çoğu yerde
`{"data": ...}` wrapper formatı** kullanır — JS tarafında auto-
unwrap için `pickList(resp)` helper'ı var.

### 4.1 Structured Rules

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | `/structured-rules/parse` | `{natural_language, name?, user_id?}` | `{rule, explanation, parse_confidence, missing_fields}` |
| POST | `/structured-rules` | `{natural_language, name?, user_id?, enabled?}` | `{data: rule}` |
| GET | `/structured-rules` | (query: user_id, enabled_only, limit) | `{data: [rules]}` |
| GET | `/structured-rules/{id}` | — | `{data: rule}` |
| PATCH | `/structured-rules/{id}/enabled` | **Query param `?enabled=true/false`** (BODY YOK!) | `{data: rule}` |
| DELETE | `/structured-rules/{id}` | — | `{success: true, rule_id}` |
| POST | `/structured-rules/test` | (RuleTestRequest) | dry_run_preview |
| GET | `/structured-rules/{id}/versions` | — | (list) |
| GET | `/structured-rules-conflicts` | — | `{data: [...]}` |
| GET | `/structured-rules-conflicts/suggestions` | — | `{data: [...]}` |
| POST | `/structured-rules-conflicts/resolve` | (ConflictResolveRequest) | `{data: result}` |

### 4.2 Rule Templates

| Method | Path | Response |
|--------|------|---------|
| GET | `/rule-templates` (query: category?) | `{data: [tpls], categories}` |
| POST | `/rule-templates/{slug}/materialize` (params) | `{template, rule, explanation, parse_confidence}` |

### 4.3 Rule Executions

| Method | Path | Response |
|--------|------|---------|
| GET | `/rule-executions` (rule_id?, status?, limit) | `{data: [...]}` |
| GET | `/rule-executions/{id}` | `{data: row, traces}` |
| POST | `/rule-executions/{id}/resume` | `{data: result}` |

### 4.4 Conversational + Diğer

| Method | Path | Açıklama |
|--------|------|---------|
| POST | `/chat-edit/preview` | conversational_rule_edit önizleme |
| POST | `/chat-edit/apply` | conversational_rule_edit uygulama |
| POST | `/semantic-resolve` | "Çanakkale hesabı" → store_id |
| POST | `/chat` | business_chat (multi-turn) |
| GET | `/learning-suggestions` | rule_learning öneri feed |
| GET | `/adapter-health` | tool_adapters durumu |
| GET | `/orchestration-health` | toplam sağlık |
| GET | `/dashboard-metrics` | dashboard sayaçlar |

### 4.5 Diğer Önemli Endpoint'ler

- `/workflows`, `/tasks`, `/approvals/{id}/{approve,reject,edit,retry,feedback}`
- `/calendar`, `/schedules/*`
- `/customer-threads/*` (CRUD + draft/escalate/resolve)
- `/credentials/*` (Fernet)
- `/campaigns/*`
- `/orgs/*`, `/api-keys/*`
- `/traces`, `/business-insights`, `/business-state`

Toplam ~96 endpoint `orchestration_api.py` içinde.

---

## 5. Veritabanı Tabloları

### 5.1 `listener.db` (SQLite, orchestration tarafı)

| Tablo | Tur | Önemli Kolonlar | Amaç |
|------|-----|------------------|------|
| `users` | T0 | id, workspace_uid | Tenant kimliği |
| `listener_state` | T0 | cursor | Timeline polling konumu |
| `rules` | T0 | id, name, conditions JSON | **Legacy** klasik kurallar |
| `tool_executions` | T0 | tool_name, args, result | Tool çağrı loğu |
| `stores`, `items`, `orders` | T0 | name, city, qty, status | Cached entity snapshot |
| `ai_tasks` | T0 | task_type, payload, status | task_executor kuyruğu |
| `workflow_instances` | T0 | name, state, payload | workflow_worker scheduler |
| `automation_logs` | T0 | tool_name, status, latency | Otomasyon ses kaydı |
| `rule_history` | T0 | rule_id, action, timestamp | Legacy değişiklik tarihçesi |
| `planner_proposals` | T0 | event_id, plan_json | autonomous_planner çıktısı |
| `approval_requests` | T0 | id, payload, status | **LangGraph approval node burada yazar** |
| `planner_memory` | T0 | event_pattern, outcome | Semantic memory |
| **`structured_rules`** | T1 | id, user_id, name, natural_language, **trigger_json, timing_json, target_json, content_json, actions_json**, version, parent_rule_id, enabled, **health_score** | **LangGraph kanonik kurallar** |
| **`rule_executions`** | T1 | id, user_id, rule_id, event_id, event_type, **thread_id, status, current_node, idempotency_key**, started_at, finished_at | **LangGraph run state** |
| `graph_node_traces` | T1 | execution_id, node_name, duration_ms, status, summary | Per-node trace |
| `orgs`, `org_members`, `api_keys` | T1 | — | Multi-tenant |
| `campaigns`, `campaign_metrics` | T1 | — | Kampanya state machine |
| **`social_credentials`** | T1 | user_id, platform, handle, **encrypted_token (Fernet)** | Şifreli access token'lar |
| `orchestration_traces` | T1 | — | Genel trace |
| `scheduled_entries` | T2 | run_at, payload_json, status | **Schedule + LangGraph wait_resume** |
| `customer_threads`, `customer_messages` | T2 | — | Müşteri sohbet |

### 5.2 `fake_ai_api.db` (SQLite, demo commerce)

| Tablo | Amaç |
|------|------|
| `timeline` | Event log (group, event, subject_json, payload_json, ts, meta_json) — listener'ın polling kaynağı |
| `stores`, `items`, `orders`, `reviews` | Demo verileri |

### 5.3 MySQL (agent-base tarafı)

| Tablo | Amaç |
|------|------|
| `users` | Kullanıcılar (agent-base auth tarafı) |
| `social_documents` | SM post draftları |
| `content_templates` | SM post şablonları |
| `labels` | Etiketler |
| `usage_events` | LLM/medya kullanım metrikleri |
| `accounts` | Sosyal medya hesapları |
| `password_resets` | Şifre sıfırlama token'ları |

### 5.4 Anahtar İlişkiler

- `structured_rules.id` ↔ `rule_executions.rule_id`
- `rule_executions.id` ↔ `graph_node_traces.execution_id`
- `rule_executions.event_id` → `fake_ai_api.db.timeline.id` (cross-db, FK enforced değil)
- `scheduled_entries.payload.execution_id` → `rule_executions.id` (wait/resume linki)
- `users.id` ↔ tüm tabloların `user_id` kolonu (multi-tenant tabanı)
- `orgs.id` ↔ `org_members.org_id` ↔ `users.id` (M:N)
- `social_credentials` UNIQUE(user_id, platform, handle)

LangGraph SqliteSaver kendi tablolarını (`checkpoints`, `writes`,
`checkpoint_blobs`) `LANGGRAPH_CHECKPOINT_DB` (default `listener.db`)
içine yazar.

---

## 6. Sidebar Yapısı (Operatör Görüşü)

```
Sosyal Medya ▾
  ├ Takvim                       /social-media
  ├ Etiketler                    /social-media/etiketler
  ├ Şablonlar (SM post)          /social-media/sablonlar
  └ Onay Bekleyenler             /social-media/onay-bekleyenler

Kampanya Yönetimi ▾
  ├ Takvim                       /campaign-management
  ├ Şablonlar (banner)           /campaign-management/sablonlar
  └ Onay Bekleyenler             /campaign-management/onay-bekleyenler

Sistem Yöneticisi ⛨              /social-media/system-admin
  └─ AI OPERATÖR MERKEZİ
     (1182 satır chat + 4 AI modu: Analiz/Operasyon/Strateji/İçerik
      + multi-turn cognition + conversational_rule_edit
      + business analytics)

Zaman Tüneli ▾                   /page/timeline/<slug>
  ├ Tümü (all)             ─ KURALLAR HER SEKMENİN İÇİNDE ─
  ├ Siparişler (orders)        filter prefix: order.*
  ├ Ürünler (products)         filter prefix: product.*
  ├ Stok (stock)               filter prefix: stock.*
  ├ Değerlendirmeler (reviews) filter prefix: review.*
  ├ Sorular (questions)        filter: customer.question
  ├ Mesajlar (messages)        filter: customer.question
  ├ Mağaza Sayfası (store)     filter prefix: store.*
  ├ Kampanyalar (campaigns)    filter prefix: campaign.*
  ├ Reklamlar (ads)            filter: banner.* + sales.*
  ├ Bannerlar (banners)        filter prefix: banner.*
  ├ Flash Satış (flash-sales)  filter prefix: sales.*
  ├ İndirimler (discounts)     filter prefix: sales.*
  ├ Teslimat (delivery)        filter: shipping.* + order.shipped
  ├ İadeler (returns)          filter: order.cancelled
  └ (diğerleri: Kuponlar, Çalışanlar, Giriş/Çıkış, Mağaza Sayfası,
     Para Çekme, Eklentiler, Abonelik, Bileşenler — filter yok)

Ayarlar ▾                        /settings/{section}
  └ Hesap, Çalışma Alanı, Yapay Zeka, Anahtarlar, Otomasyon, Güvenlik

[Çıkış]                          POST /logout
```

**Kurallar artık ayrı sekme DEĞİL**; her timeline sayfasında o sekmeye
özel contextual panel olarak render edilir (`views/page.php` →
`include views/timeline/_rules_toolbar.php`). `index.php` timeline
sayfaları için `extraHead`'e `timeline-rules.css` + `timeline-page-
rules.js` yükler.

---

## 7. Tur Tur Birikim (1-5)

### Tur 1 — LangGraph Altyapısı (Mayıs öncesi)
- `structured_rule.py` Pydantic schema (18 trigger event, 10 channel,
  14 template, 9 action_kind)
- `nl_rule_parser.py` Türkçe NL → StructuredRule (prefilter + LLM)
- `langgraph_engine/{state, nodes, runtime}.py`
- `structured_rule_engine.py` + listener entegrasyonu
- `orchestration_api.py` 80+ endpoint
- DB tablolar: structured_rules, rule_executions, graph_node_traces,
  orgs/org_members/api_keys, campaigns, social_credentials
- Dashboard 5-tab Türkçe rewrite

### Tur 2 — Sistem Olgunlaşması
- `tools.py` kendi protocol BaseTool'una geçti (CrewAI yok)
- Wait/resume gerçek pipeline (workflow_worker + interrupt_after)
- `semantic_entity_resolver.py` ("Çanakkale hesabı" → store_id)
- `rule_templates.py` (9 hazır şablon) + UI
- Rule versioning + conflict detection
- Dashboard execution graph görselleştirme
- `tool_adapters/` stub iskeleti

### Tur 3 — Öğrenme + Multi-tenant + Conversational Edit
- `rule_learning.py` (health_score öneri motoru)
- `conversational_rule_edit.py` (sohbette kural düzenleme)
- `conflict_resolver.py` + öneriler
- Real social adapter HTTP (Instagram + Facebook)
- business_chat conversational_rule_edit entegrasyonu

### Tur 4 — Monorepo Birleşim
- `rule-based-engine/` + `agent-base/` tek monorepo
- `agent-base-api/app/main.py` birleşik FastAPI entrypoint
- `orchestration_api` router mount edildi
- `fake_commerce_app` `/commerce-platform` sub-app
- CrewAI tamamen kaldırıldı (LightAgent dataclass + native
  OpenAI/Gemini)
- pyproject.toml + uv.lock güncellendi
- supervisord 7 process
- php-ui'a `/kurallar` sayfası eklendi (Tur 5'te silinecek)
- docker-compose + README + SON_DEGISIKLIKLER tam yazıldı

### Tur 5 — Contextual Rules + AI Operatör Merkezi + Premium UI
- **SyntaxError `Unexpected identifier 'nin'` çözüldü** (rules.php:592
  silindi + JS güvenli string disiplini)
- Tek `/kurallar` sekmesi kaldırıldı → her timeline alt sekmesi
  contextual rule paneli içeriyor (22 slug → event_type prefix map)
- Sistem Yöneticisi sayfası AI Operatör Merkezi olarak vurgulandı
- Şablon hiyerarşisi netleştirildi (rule_templates / sm_templates /
  campaign templates üç ayrı katman)
- Polish-2: premium CSS, toast notification, optimistic UI, skeleton
  loading, renkli stepper, conflict banner, kbd hint, hover lift,
  responsive
- **Bug fix:** orchestrator.py `detected_risks` NameError + JS parse
  endpoint field name + response shape + PATCH query param

---

## 8. Çözülmüş Bug'lar ve Pattern'ler

### 8.1 SyntaxError: Unexpected identifier 'nin'

**Konum:** `php-ui/views/rules.php:592` (silindi)
```js
root.innerHTML = '<div class="muted">AI'nin henüz öneri ürettiği bir kural yok.</div>'
//                                   ^ tek tırnak burada string'i kapatıyor
//                                    ^^^ JS `nin` → identifier → SyntaxError
```

**Kalıcı disiplin:**
1. Türkçe metni DOM API ile yerleştir (`textContent`, `createElement`)
2. Template literal mecbursa (`` ` ``); asla raw single-quote
3. HTML inject ederken `escapeHtml()`
4. PHP → JS veri: `htmlspecialchars(json_encode($x, JSON_UNESCAPED_UNICODE), ENT_QUOTES, 'UTF-8')` + `JSON.parse(dataset.x)`

**CI lint regex:**
```regex
'[A-Za-zçğıöşüÇĞIÖŞÜ]'[a-zçğıöşü]
```
JSDoc yorumları hariç bu pattern bulunduğunda build fail edilmeli.

### 8.2 NameError: 'detected_risks' is not defined

**Konum:** `app/runtime/orchestrator.py:1112`

**Çözüm:** `done_payload` sözlüğü oluşturulduktan sonra local
değişkene atama eklendi (satır 1103):
```python
done_payload = {
    ...
    "detected_risks": list(final_narrative.get("detected_risks") or []),
    ...
}
detected_risks = done_payload["detected_risks"]  # local ref
```

### 8.3 422 Unprocessable Entity (Parse Endpoint)

**Hata:**
```
[{"type":"missing","loc":["body","natural_language"],"msg":"Field required",
  "input":{"text":"..."}}]
```

**Kök neden:** JS `{text}` gönderiyordu, backend `{natural_language}` bekliyor (RuleParseRequest schema).

**Çözüm:** `timeline-page-rules.js`:
```js
// ÖNCE:
const r = await api("POST", "/structured-rules/parse", { text })

// SONRA:
const resp = await api("POST", "/structured-rules/parse", {
  natural_language: text,
})
const rule = resp && resp.rule ? resp.rule : resp
rule.explanation = resp?.explanation || rule.explanation
rule.missing = resp?.missing_fields || rule.missing || []
lastParsed = rule
renderPreview(rule)
```

### 8.4 Response Shape Mismatch

**Sorun:** Backend tüm liste endpoint'leri `{"data": [...]}` döndürüyor; JS ham array bekliyordu → sessizce boş liste.

**Çözüm:** JS'e `pickList(resp)` helper'ı eklendi ve 4 callsite (`refresh`, `loadLastExecution`, `loadConflicts`, `loadTemplatesIfNeeded`) güncellendi:
```js
function pickList(resp) {
  if (Array.isArray(resp)) return resp
  if (resp && Array.isArray(resp.data)) return resp.data
  return []
}
```

### 8.5 PATCH /enabled Body vs Query

**Sorun:** Backend `enabled: bool = Query(...)` — yani URL query parameter, body değil. JS body gönderiyordu → 422.

**Çözüm:**
```js
// ÖNCE:
await api("PATCH", `/structured-rules/${id}/enabled`, { enabled: nextState })

// SONRA:
await api("PATCH", `/structured-rules/${id}/enabled?enabled=${Boolean(nextState)}`)
```

---

## 9. Güvenlik Katmanları

| Katman | Mekanizma |
|--------|-----------|
| `SOCIAL_PUBLISH_LIVE=0` (default) | Tool_adapters import time'da okur; 0 iken `FeatureDisabledError` raise → mock log |
| `INTERNAL_SERVICE_IN_PROCESS=1` | orchestration_api self-HTTP loop guard |
| `interrupt_before=["approval"]` | LangGraph onay öncesi pause |
| `interrupt_after=["wait"]` | LangGraph delay sonrası pause (delay_seconds > 0) |
| `cryptography.fernet.Fernet` | `social_credentials.py` token şifreleme |
| `auth_service.get_current_auth` | Her sorguya `user_id + org_id` filter |
| `rule_executions.idempotency_key` | Aynı `f"{rule_id}:{event_id}"` çift tetiklenemez |
| Pydantic fail-fast | NL parse hata → 422 → kural oluşmaz |
| `CRITICAL_FALLBACK=0` | Critical event'te rule yoksa autonomous'a düşmez |
| JS XSS-safe | DOM API + escapeHtml + JSON_UNESCAPED_UNICODE |

---

## 10. Premium UI Patternleri (Tur 5 Polish-2)

### Renk Paleti
- **Slate**: 50, 100, 200, 500, 700, 900 (zemin + metin)
- **Indigo**: 50, 100, 200, 500, 600, 700 (primary)
- **Emerald**: 50, 100, 200, 700 (good)
- **Amber**: 50, 100, 200, 700, 800, 900 (warn)
- **Rose**: 50, 100, 700 (bad)
- **Sky**: 50, 100, 700 (info)

### Tipografi & Spacing
- Font: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto
- Border-radius: 10px (buton), 12px (input/skeleton), 14px (kart),
  16-18px (panel)
- Shadow scale: sm (1px-2px), md (4px-12px), lg (8px-24px), ring
  (0 0 0 3px alpha)
- Transition: 150ms cubic-bezier(.4, 0, .2, 1)

### Etkileşim
- Buton hover: translateY(-1px) + shadow-md
- Buton active: scale(.98)
- Card hover: border-indigo-200 + shadow-md
- Toast: slide-in 250ms + fade-out 200ms, sağ alt köşe
- Skeleton: 200% bg shimmer, 1.4s linear infinite
- Optimistic UI: chip anında değiş + rollback hata durumunda

### Chip Sistemi
Her chip başında `●` durum noktası renkli:
- `tr-chip-good` → emerald (sağlık ≥ 0.75, status completed)
- `tr-chip-warn` → amber (sağlık 0.45-0.75, status waiting)
- `tr-chip-bad` → rose (sağlık < 0.45, status failed)
- `tr-chip-on` → indigo (AKTİF, running)
- `tr-chip-off` → slate (PASİF, none)

### Stepper Pill'leri
- `▶ başla` (sky)
- `⏱ wait` (mavi)
- `⏸ approval` (amber)
- `📤 publish` (indigo)
- `✓ bitir` (emerald)

---

## 11. Çevre Değişkenleri

| Variable | Default | Açıklama |
|----------|---------|---------|
| `INTERNAL_SERVICE_IN_PROCESS` | `1` (compose'ta) | orchestration_api self-HTTP guard |
| `LANGGRAPH_CHECKPOINT_DB` | `/opt/api/listener.db` | SqliteSaver dosyası |
| `SOCIAL_PUBLISH_LIVE` | `0` | Real publish gate |
| `CHAT_USE_LLM` | `1` | business_chat LLM |
| `NL_PARSER_USE_LLM` | `1` | nl_rule_parser LLM |
| `FERNET_KEY` | (boş → otomatik) | social_credentials şifreleme |
| `OPENAI_API_KEY` | (operatör verir) | OpenAI |
| `GEMINI_API_KEY` | (operatör verir) | Gemini fallback |
| `R2_*` | (config) | Cloudflare R2 medya storage |
| `DATABASE_URL` | `mysql+pymysql://...` | MySQL bağlantısı (agent-base tarafı) |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `AUTONOMOUS_PLANNER_ENABLED` | `1` | listener autonomous path |
| `CRITICAL_FALLBACK` | `0` | Critical event rule yoksa autonomous'a düşme |
| `SKIP_SYNTHETIC` | `1` | listener fake event'leri atla |
| `LISTENER_POLL_INTERVAL` | `2` (kod içi) | listener polling |
| `WORKFLOW_POLL_INTERVAL` | `5` (kod içi) | workflow_worker polling |

---

## 12. Hızlı Test Komutları

```bash
# 1. Yerel dev (uvicorn)
cd agent-base/agent-base-api
make run
# veya: uv run uvicorn app.main:app --reload

# 2. Docker (production-like)
cd agent-base
docker compose up -d --build
docker compose exec agent-base-allinone supervisorctl status
# Beklenen: api, worker, rbe-listener, rbe-workflow, rbe-task,
#           php-fpm, nginx → tümü RUNNING

# 3. Health endpoint'leri
curl -s http://localhost:8000/health | jq                    # uvicorn doğrudan
curl -s http://localhost:8080/api/health | jq                # nginx üzerinden
curl -s http://localhost:8080/api/internal/orchestration-health | jq

# 4. Kural CRUD smoke (JS düzeltmesi sonrası — natural_language field!)
curl -s -X POST http://localhost:8080/api/internal/structured-rules/parse \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{"natural_language":"Yeni banner oluştuğunda bu banneri sosyal medya hesabında da paylaş"}'

curl -s http://localhost:8080/api/internal/structured-rules \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq '.data | length'

curl -s -X PATCH "http://localhost:8080/api/internal/structured-rules/1/enabled?enabled=false" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq

# 5. Şablonlar
curl -s http://localhost:8080/api/internal/rule-templates \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq '.data[].slug'

# 6. Adapter sağlık
curl -s http://localhost:8080/api/internal/adapter-health \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq

# 7. Syntax doğrulama (CI)
node -c agent-base/php-ui/public/assets/js/timeline-page-rules.js
php -l agent-base/php-ui/views/timeline/_rules_toolbar.php
python3 -c "import ast; ast.parse(open('agent-base/agent-base-api/app/runtime/orchestrator.py').read())"

# 8. UI smoke (manuel)
# - http://localhost:8080/login → giriş
# - /page/timeline/store → "Mağaza Kuralları" panel
# - DevTools Console: SyntaxError YOK olmalı
# - Composer'a yaz + Cmd/Ctrl+Enter → preview
# - Kuralı Etkinleştir → success toast → liste yenilenir
# - Pasifleştir → chip anında değişir (optimistic)
# - /page/timeline/products → mağaza kuralı GÖRÜNMEMELİ (filter ✓)
# - /social-media/system-admin → AI Operatör chat
```

---

## 13. Açık İş — Tur 6 Adayları

1. **AI Operatör chat tool-binding** — Sistem Yöneticisi chat'te "şu
   kuralı pasifleştir" derken doğrudan `/structured-rules/{id}/enabled`
   API çağrısı. `conversational_rule_edit` endpoint'i hazır;
   prompt'a explicit tool-binding eklenecek.
2. **Vector retrieval rule similarity** — `embedding_placeholder`
   kolonu Tur 1'de eklenmişti; pgvector/sqlite-vss ile gerçek NN.
3. **Multi-account dispatch** — `store_handle IN (...)` pattern.
4. **TikTok content API real publish** (şu an stub).
5. **Trendyol Q&A + Shopify webhook** real adapter.
6. **Visual rule builder** — drag-drop "trigger → actions" UI.
7. **Org/role-aware rule visibility** — org_id widening UI.
8. **Bulk import/export** — kural JSON migrasyonu.
9. **Operatör özel rule_templates kaydet** — "mers şablonu" gibi.
10. **Per-channel SOCIAL_PUBLISH_LIVE** — Instagram canlı + TikTok mock.
11. **Time-travel viewer** — LangGraph checkpoint history UI.
12. **Cost/quota tracking** — LLM cost ölç, org quota gate.
13. **Per-slug execution mini grafik** — son N yürütme trend bar.
14. **Mobile-first sticky composer**.
15. **Dark mode** — CSS variables hazır, sadece `prefers-color-scheme`.
16. **Eski `rule-based-engine/` dizinini sil** — Tur 4-5 stabilse.

---

## 14. Önemli Dosyalar (Hızlı Erişim)

| Dosya | İş |
|------|------|
| `agent-base-api/app/main.py` | FastAPI entrypoint (Tur 4 birleşik) |
| `agent-base-api/orchestration_api.py` | 96 endpoint router |
| `agent-base-api/listener.py` | Timeline polling worker |
| `agent-base-api/workflow_worker.py` | Wait/resume + schedule |
| `agent-base-api/task_executor.py` | Tool dispatcher worker |
| `agent-base-api/structured_rule.py` | Pydantic schema + taksonomi |
| `agent-base-api/nl_rule_parser.py` | NL → StructuredRule |
| `agent-base-api/langgraph_engine/runtime.py` | build_graph + start/resume |
| `agent-base-api/langgraph_engine/nodes.py` | 10 node implementasyonu |
| `agent-base-api/tool_adapters/instagram.py` | Real Instagram Graph API |
| `agent-base-api/db.py` | SQLite şema (30+ tablo) |
| `agent-base-api/app/runtime/orchestrator.py` | Multi-turn AI orchestrator (line 1100 civarı `detected_risks`) |
| `php-ui/public/index.php` | PHP front controller (route'lar) |
| `php-ui/views/layout.php` | Sidebar template |
| `php-ui/views/system_admin.php` | AI Operatör Merkezi |
| `php-ui/views/timeline/_rules_toolbar.php` | Contextual rule panel markup |
| `php-ui/public/assets/css/timeline-rules.css` | Premium CSS (678 satır) |
| `php-ui/public/assets/js/timeline-page-rules.js` | Backend-bağlı CRUD JS (~720 satır) |
| `php-ui/public/assets/js/timeline-store-automation.js` | system_admin chat UI (1182 satır) |

---

## 15. Geliştirici İletişimi — Grok için Öneri

Grok, bu dosya seninle paylaşıldıysa muhtemelen kullanıcı:

- **Yeni bir özellik istiyor** → Tur 6 adaylarından birine bak veya
  custom; her zaman backend endpoint'i kontrol et (`orchestration_api.py`
  içinde grep), JS'i `timeline-page-rules.js` paterni ile yaz.
- **Bug bildiriyor** → DevTools Console + `docker compose logs`
  + `make run` çıktısı iste. SyntaxError ise §8.1 disiplinini hatırlat.
  422 ise §8.3/§8.4'e bak.
- **Mimari sorusu** → §1 (üst düzey) + §3 (akış) + §4 (endpoint) +
  §5 (DB).
- **UI/UX** → §10 (premium pattern) referans al.

**Her zaman:**
1. Önce repo'da grep et — uydurma yapma.
2. Backend Python'a dokunmadan UI sorunu çözebiliyorsan dokunma.
3. JS yazıyorsan §8.1 disiplinine uy (DOM API + escapeHtml + JSON.parse).
4. PHP yazıyorsan `htmlspecialchars(..., ENT_QUOTES, 'UTF-8')` kullan.
5. Test komutu (§12) ile doğrula.

---

*Doküman sonu. Daha fazla ayrıntı için `ÖZET.md` (Türkçe operasyonel
özet) ve `SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md` (Tur 1-5 evrim
tarihçesi) dosyalarına bakın.*
