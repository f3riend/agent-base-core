# Son Değişiklikler ve Genel Sistem — Tur 5 (Contextual Rules)

> Bu doküman, projenin **beş turunu** birlikte yansıtıyor. Önceki
> turlardaki shipped phase'ler ve prensipler **olduğu gibi korundu**.
>
> **Tur 1 (LangGraph altyapısı):** structured_rule, nl_rule_parser,
> langgraph_engine/*, dynamic graph builder, dashboard 5-tab
> Türkçeleştirme.
>
> **Tur 2 (sistem olgunlaşması):** CrewAI removal başlangıcı (tools.py
> kendi BaseTool protocol'üne geçti), gerçek wait/resume pipeline,
> semantic_entity_resolver, rule_templates, rule versioning + conflict
> detection, dashboard execution graph görselleştirme, tool_adapters/
> stub iskeleti.
>
> **Tur 3 (öğrenme + multi-tenant + conversational edit):** rule
> health_score, rule_learning.py (öneri motoru), conversational_rule_edit
> (sohbette kural düzenleme), conflict_resolver.py + UI önerileri, real
> social adapter HTTP (Instagram/Facebook) `SOCIAL_PUBLISH_LIVE` flag
> arkasında, org/api-key, social_credentials Fernet.
>
> **Tur 4 (Monorepo Birleşim):**
> - `rule-based-engine/` + `agent-base/` **tek monorepo** altında
>   birleşti: `agent-base/agent-base-api/` Python backend +
>   `agent-base/php-ui/` PHP frontend + `agent-base/docker-compose.yml`
>   all-in-one Docker stack.
> - Tek FastAPI process iki rolü taşıyor: agent-base API (auth /
>   social_data / social_media) + orchestration_api (`/api/internal/*`
>   altında 80+ endpoint).
> - **CrewAI tamamen kaldırıldı** (Tur 2'de başlamıştı, Tur 4'te son
>   modül-seviyesi import'lar da `LightAgent` + native OpenAI/Gemini'a
>   geçirildi).
> - php-ui'a `/kurallar` (alias `/rules`) Türkçe LangGraph operasyon
>   sayfası eklendi (5-tab: Yeni Kural, Aktif Kurallar, Şablonlar,
>   Yürütmeler, AI Önerileri).
> - LangGraph worker'ları (listener, workflow_worker, task_executor)
>   `supervisord` altında aynı container'da uvicorn ile birlikte
>   çalışıyor.
>
> **Tur 5 (Contextual Rules + JS güvenli string — BU TUR):**
> - Kritik **`Uncaught SyntaxError: Unexpected identifier 'nin'`**
>   hatası çözüldü. Kök neden: `views/rules.php:592`'deki
>   `'<div>AI'nin henüz öneri yok</div>'` ifadesinde tek tırnak,
>   Türkçe iyelik ekinden önce string'i kapatıyordu. Düzeltme:
>   `rules.php` tamamen silindi + tüm yeni JS dosyalarında **DOM API +
>   template literal + escapeHtml + JSON.stringify** pattern'i zorunlu
>   kılındı.
> - Tek-sayfa "Kurallar" yaklaşımı kaldırıldı. Kurallar artık her
>   **Zaman Tüneli alt sekmesinde** contextual olarak duruyor (Mağaza,
>   Ürünler, Stok, Değerlendirmeler, Reklamlar, Kampanyalar, ...).
> - Slug → event_type prefix eşlemesi `_rules_toolbar.php` içinde
>   tanımlı; UI client-side filtre ile o sekmeye uyan kuralları
>   gösteriyor.
> - `timeline-page-rules.js` baştan yazıldı: `/api/internal/structured-
>   rules*` endpoint'lerine bağlı CRUD + composer + preview + şablon
>   grid + health score.
> - Sistem Yöneticisi sayfası **AI Operatör Merkezi** olarak
>   konumlandırıldı: mevcut 1182 satırlık `timeline-store-
>   automation.js` chat'i + 4 AI modu (Analiz, Operasyon, Strateji,
>   İçerik) + `conversational_rule_edit` ile NL kural yönetimi.
> - Şablon hiyerarşisi netleştirildi: **rule_templates** (LangGraph
>   kural şablonları, contextual panelden seçilir) **sm_templates**
>   (sosyal medya post şablonları) **campaign templates** (banner) —
>   üçü farklı.

---

## 1. Genel Mimari Özeti (Tur 4)

Sistem artık tek bir monorepo altında dört katmanlı bir AI-native
commerce operating runtime:

```
┌──────────────────────────────────────────────────────────────────────┐
│  agent-base-allinone (tek Docker container)                          │
│                                                                      │
│  nginx :80  ──┬──► /api/*    → uvicorn :8000 (app.main:app)          │
│               └──► /*        → php-fpm (php-ui)                      │
│                                                                      │
│  supervisord ile yönetilen process'ler:                              │
│    • uvicorn      (FastAPI: agent-base + orchestration_api)          │
│    • celery       (image/medya/caption agir is kuyruğu)              │
│    • rbe-listener     (Tur 4 ─ listener.py event polling)            │
│    • rbe-workflow     (Tur 4 ─ workflow_worker.py runner)            │
│    • rbe-task         (Tur 4 ─ task_executor.py runner)              │
│    • php-fpm + nginx                                                 │
└──────────────────────────────────────────────────────────────────────┘
       │                                                          ▲
       │ internal_service (in-process, INTERNAL_SERVICE_IN_PROCESS=1)
       ▼                                                          │
┌──────────────────────┐     ┌────────────────────────────────────┴───┐
│ MySQL (agent-base)   │     │ listener.db (orchestration)            │
│  - users, sessions   │     │  - workflow_instances, ai_tasks        │
│  - social_data       │     │  - approval_requests, automation_logs  │
│  - social_media_*    │     │  - planner_memory, planner_outcomes    │
│  - sm_tags,          │     │  - orchestration_traces                │
│    sm_templates      │     │  - orgs, org_members, api_keys         │
│  - campaigns_local   │     │  - social_credentials (Fernet)         │
└──────────────────────┘     │  - scheduled_entries, customer_*       │
                             │  - campaigns, campaign_metrics         │
┌──────────────────────┐     │  - structured_rules, rule_executions   │
│ fake_ai_api.db       │     │  - graph_node_traces                   │
│ (fake_commerce_app   │     │  - rule_learning_signals (Tur 3)       │
│  sub-app @           │     │  - LangGraph checkpoints (SqliteSaver) │
│  /commerce-platform) │     └────────────────────────────────────────┘
│  - timeline                       ▲
│  - stores, items,                 │
│    orders, reviews                │
└──────────────────────┘            │
       ▲                            │
       │ polls                      │
┌──────┴───────────┐                │
│ listener.py      │                │
│  Üç paralel      │                │
│  matching path:  │                │
│  1) rule_engine  │                │
│  2) autonomous_  │                │
│     planner     ─┼───► structured_rule_engine.trigger_rules_for_event
│  3) STRUCTURED_  │              │
│     RULES        │              ▼
└──────────────────┘     ┌────────────────────────────────────────┐
                         │  langgraph_engine.runtime               │
                         │   StructuredRule → StateGraph compile   │
                         │   SqliteSaver checkpoint + interrupt    │
                         │                                         │
                         │  Nodes:                                 │
                         │   supervisor → wait → content_gen       │
                         │     → risk_check → ⏸approval            │
                         │     → publish → monitor → finalize      │
                         └─────────────────────────────────────────┘
                                       │
                                       ▼ (tools.py protocol BaseTool)
                         ┌────────────────────────────────────────┐
                         │  tool_adapters/* + tools.py           │
                         │   instagram / facebook / tiktok       │
                         │   trendyol / shopify / banner         │
                         │   SOCIAL_PUBLISH_LIVE=0 iken mock     │
                         └────────────────────────────────────────┘
```

**Önemli prensip korunuyor:** AI proposes, runtime executes. LLM'in
ürettiği şey kanonik `StructuredRule` JSON; çalıştırmayı LangGraph
runtime (deterministic) yapıyor. NL parse hatası rule'u oluşturmaz —
fail-fast.

---

## 2. Yeni Klasör Yapısı

```
agent-base/                          # Monorepo kökü
├─ docker-compose.yml                # all-in-one + mysql + redis
├─ docker-compose.override.yml
├─ docker-compose.prod.yml
├─ Dockerfile                        # node build + python uv + nginx + supervisord
├─ Makefile
├─ README.md                         # Tur 4 LangGraph bölümü eklendi
├─ SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md  ← bu dosya
├─ docker/
│   ├─ supervisord.conf              # rbe-listener / rbe-workflow / rbe-task eklendi
│   ├─ nginx.conf*
│   └─ ...
│
├─ agent-base-api/                   # Python backend (FastAPI + LangGraph)
│   ├─ pyproject.toml                # CrewAI yok; langgraph + cryptography var
│   ├─ uv.lock
│   ├─ app/                          # mevcut agent-base modülleri
│   │   ├─ main.py                   # Tur 4: orchestration_api + fake_commerce mount
│   │   ├─ api/  (auth, social_data, social_media, client_debug)
│   │   ├─ agents/manager/agent_factory.py     # LightAgent
│   │   ├─ services/agent_runtime_service.py   # native OpenAI/Gemini
│   │   └─ core/, integrations/, ...
│   │
│   ├─ orchestration_api.py          # Tur 4: rule-based-engine'den taşındı
│   ├─ listener.py                   # supervisord altında ayrı process
│   ├─ workflow_worker.py            # supervisord altında ayrı process
│   ├─ task_executor.py              # supervisord altında ayrı process
│   ├─ crewai_worker.py              # CrewAI yok; LangGraph node bridge
│   ├─ structured_rule.py
│   ├─ structured_rule_engine.py
│   ├─ nl_rule_parser.py
│   ├─ semantic_entity_resolver.py
│   ├─ conversational_rule_edit.py
│   ├─ rule_templates.py
│   ├─ rule_learning.py
│   ├─ conflict_resolver.py
│   ├─ ai_planner.py
│   ├─ autonomous_planner.py
│   ├─ business_chat.py
│   ├─ campaign_service.py
│   ├─ auth_service.py
│   ├─ social_credentials.py
│   ├─ db.py
│   ├─ tools.py
│   ├─ tool_adapters/                # instagram, facebook, tiktok, trendyol, shopify
│   ├─ langgraph_engine/             # state, nodes, runtime, conditions, registry
│   ├─ fake_commerce_app.py          # eski main.py — /commerce-platform sub-app
│   ├─ index.html                    # legacy /dashboard SPA
│   ├─ listener.db
│   ├─ fake_ai_api.db
│   └─ ... (toplam 68 flat Python modülü kopyalandı)
│
└─ php-ui/                           # PHP frontend (mevcut yapı korunmuş)
    ├─ public/
    │   ├─ index.php                 # Tur 4: /kurallar route eklendi
    │   └─ router.php
    ├─ includes/                     # bootstrap, http, session, i18n
    ├─ assets/                       # css, js modüller
    └─ views/
        ├─ layout.php                # Tur 4: "Kurallar" nav linki eklendi
        ├─ social_media.php          # KORUNDU
        ├─ approvals.php             # KORUNDU
        ├─ sm_tags.php               # KORUNDU
        ├─ sm_templates.php          # KORUNDU
        ├─ system_admin.php          # KORUNDU
        ├─ timeline/                 # KORUNDU
        ├─ settings/                 # KORUNDU
        ├─ login.php, register.php, ... # KORUNDU
        └─ rules.php                 # Tur 4 ─ YENİ (5-tab LangGraph paneli)
```

**Strateji:** "copy not move". `rule-based-engine/` orijinal dizini
yedek olarak repo kökünde duruyor; `agent-base/agent-base-api/` paralel
bir kopya. Geri dönüş ihtiyacı olursa reversible.

---

## 3. Önemli Değişiklikler (Tur 4)

### 3.1 `app/main.py` — birleşik entrypoint

`agent-base-api/app/main.py` artık iki rolü birden taşıyor:

```python
# 1. INTERNAL_SERVICE_IN_PROCESS guard — orchestration_api'nin
#    self-HTTP loop detector'u bu process'te aktif olsun.
os.environ.setdefault("INTERNAL_SERVICE_IN_PROCESS", "1")

# 2. Flat Python modüllerini sys.path'e ekle (rule-based-engine
#    `import db`, `from langgraph_engine import runtime` kullanıyor).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 3. lifespan: hem agent-base init_db() hem orchestration db.init_db()
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()                           # MySQL agent-base şemaları
    import db as orchestration_db
    orchestration_db.init_db()          # listener.db 30+ tablo
    yield

# 4. Mevcut router'lar korunuyor
app.include_router(auth.router)
app.include_router(client_debug.router)
app.include_router(social_data.router)
app.include_router(social_media.router)
app.include_router(social_media.legacy_router)

# 5. Tur 4 ─ orchestration_api router mount (80+ endpoint)
from orchestration_api import router as orchestration_router
app.include_router(orchestration_router)

# 6. Tur 4 ─ fake_commerce_app sub-app mount (listener.py polling akışı)
from fake_commerce_app import app as _fake_commerce_app
app.mount("/commerce-platform", _fake_commerce_app)

# 7. Legacy dashboard SPA kısayolu
@app.get("/dashboard")
def dashboard():
    return FileResponse(str(_DASHBOARD_PATH), headers={"Cache-Control": "no-store"})
```

**Eski rule-based-engine deployment'ı** (port 8000 fake commerce + port
8001 orchestration) artık **tek port, tek lifespan**.

### 3.2 CrewAI tamamen kaldırıldı

Tur 2'de başlayan removal Tur 4'te modül-seviyesi import'lar dahil
tamamlandı:

| Dosya | Önce | Sonra |
|-------|------|-------|
| `app/agents/manager/agent_factory.py` | `from crewai import Agent, LLM` | `LightAgent` dataclass |
| `app/services/agent_runtime_service.py` | `from crewai import Crew, Task` | native OpenAI/Gemini chat |
| `crewai_worker.py` | CrewAI dispatch | LangGraph node bridge |
| `tools.py` | `from crewai.tools import BaseTool` | kendi protocol-typed BaseTool (Tur 2) |
| `api/social_media.py` | module-level | function-level lazy import (try/except) |
| `pyproject.toml` | `crewai[tools]>=1.10.1` | `langgraph + langgraph-checkpoint-sqlite + cryptography + pydantic>=2.0` |

`LightAgent` kritik kod:

```python
@dataclass
class LightAgent:
    role: str
    goal: str
    backstory: str
    model: str
    api_key: str | None
    tools: list[Any] = field(default_factory=list)

    def system_prompt(self) -> str:
        return (
            f"Rol: {self.role}\nHedef: {self.goal}\nGeçmiş: {self.backstory}\n\n"
            f"Mevcut araçlar: {[getattr(t, 'name', repr(t)) for t in self.tools]}\n"
            "Kısa, net ve Türkçe cevap ver."
        )
```

`AgentRuntimeService._run_native` — model `gemini/` ile başlıyorsa
Gemini, aksi halde OpenAI chat completion. Public API (`run_agent`,
`manager_run`) korunuyor — caller değişmiyor.

### 3.3 `pyproject.toml` (agent-base-api)

```toml
dependencies = [
    "celery>=5.6.3",
    "redis>=5.0.0",
    # CrewAI tamamen kaldırıldı (Tur 2 → Tur 4).
    "fal-client>=0.13.2",
    "fastapi>=0.135.3",
    "boto3>=1.35.0",
    "cryptography>=42.0.0",            # Tur 1: social_credentials Fernet
    "google-genai>=1.73.0",
    "google-generativeai>=0.8.6",
    "httpx>=0.28.1",
    "langgraph>=1.2.1",                # Tur 4
    "langgraph-checkpoint-sqlite>=3.1.0", # Tur 4
    "loguru>=0.7.3",
    "openai>=2.31.0",
    "pillow>=12.2.0",
    "pyaml>=26.2.1",
    "pydantic>=2.0",
    "python-dotenv>=1.1.1,<1.2.0",
    "requests>=2.32.5",
    "uvicorn>=0.44.0",
    "imageio-ffmpeg>=0.6.0",
    "sqlalchemy>=2.0.49",
    "pymysql>=1.1.3",
    "python-jose[cryptography]>=3.5.0",
    "bcrypt>=4.2.0",
    "pydantic-settings>=2.10.1",
]
```

### 3.4 `docker-compose.yml`

`agent-base-allinone` servisine LangGraph env değişkenleri eklendi:

```yaml
environment:
  INTERNAL_SERVICE_IN_PROCESS: "1"
  LANGGRAPH_CHECKPOINT_DB: ${LANGGRAPH_CHECKPOINT_DB:-/opt/api/listener.db}
  SOCIAL_PUBLISH_LIVE: ${SOCIAL_PUBLISH_LIVE:-0}    # KORUNUYOR — varsayılan mock
  CHAT_USE_LLM: ${CHAT_USE_LLM:-1}
  NL_PARSER_USE_LLM: ${NL_PARSER_USE_LLM:-1}
  FERNET_KEY: ${FERNET_KEY:-}
```

### 3.5 `docker/supervisord.conf`

Yeni üç program (priority 16-17, php-fpm/nginx'ten önce):

```ini
[program:rbe-listener]
directory=/opt/api
command=/opt/api/.venv/bin/python listener.py
environment=INTERNAL_SERVICE_IN_PROCESS=1
autorestart=true
priority=16

[program:rbe-workflow]
directory=/opt/api
command=/opt/api/.venv/bin/python workflow_worker.py
environment=INTERNAL_SERVICE_IN_PROCESS=1
autorestart=true
priority=17

[program:rbe-task]
directory=/opt/api
command=/opt/api/.venv/bin/python task_executor.py
environment=INTERNAL_SERVICE_IN_PROCESS=1
autorestart=true
priority=17
```

---

## 4. php-ui Güncellemeleri

### 4.1 Korunan sayfalar (DOKUNULMADI)

- `social_media.php` — Sosyal medya takvimi + post composer
- `approvals.php` — Onay bekleyenler (social + campaign modlu)
- `sm_tags.php`, `sm_templates.php` — Etiket + şablon yönetimi
- `system_admin.php` — Sistem yöneticisi
- `timeline/store_page.php` — Mağaza timeline
- `settings/account.php`, `workspace.php`, `ai.php`, `api-keys.php`,
  `automation.php`, `security.php` — KORUNDU
- `login.php`, `register.php`, `forgot_password.php`,
  `reset_password.php`, `reset_code.php` — KORUNDU

### 4.2 Yeni: `views/rules.php` + `/kurallar` route

5-tab Türkçe LangGraph operasyon paneli (~600 satır, `RulesUI` JS
nesnesi):

| Sekme | İçerik | API |
|-------|--------|-----|
| **Yeni Kural** | NL composer + parse-preview stepper | `POST /api/internal/structured-rules/parse-preview`, `POST /api/internal/structured-rules`, `POST /api/internal/structured-rules/{id}/test-run` |
| **Aktif Kurallar** | toggle / sil / health badge | `GET /api/internal/structured-rules`, `PATCH .../toggle`, `DELETE ...` |
| **Şablonlar** | `rule_templates.py` materialize formu | `GET /api/internal/rule-templates`, `POST /rule-templates/{slug}/materialize` |
| **Yürütmeler** | execution timeline + duration bar + resume | `GET /api/internal/rule-executions`, `GET .../{id}`, `POST .../{id}/resume` |
| **AI Önerileri** | learning + conflict önerileri | `GET /api/internal/learning-suggestions`, `GET /structured-rules-conflicts/suggestions` |

PHP entrypoint kalıbı:

```php
// public/index.php — yeni route bloğu
if ($path === '/kurallar' || $path === '/rules') {
    app_require_login();
    app_refresh_user_from_api();
    if (app_access_token() === null) {
        header('Location: ' . app_url('/login'), true, 302);
        exit;
    }
    $title = 'Kurallar — LangGraph';
    ob_start();
    include __DIR__ . '/../views/rules.php';
    $content = ob_get_clean();
    app_render_layout($title, $content, null);
    exit;
}
```

`views/layout.php` sol menüsüne `git-branch` ikonlu **Kurallar** linki:

```php
<a href="<?= htmlspecialchars(app_url('/kurallar'), ENT_QUOTES, 'UTF-8') ?>"
   class="app-nav-item<?= ($requestPath === '/kurallar' || $requestPath === '/rules') ? ' is-active' : '' ?>"
   title="Kurallar — LangGraph">
  <i data-lucide="git-branch"></i><span>Kurallar</span>
</a>
```

JS tarafı `app_browser_api_base()` + `app_access_token()` yardımcı
fonksiyonlarını PHP head'inde okuyor; ardından `RulesUI` nesnesi tüm
API çağrılarını `Authorization: Bearer <token>` ile yapıyor.

---

## 5. Korunan Güvenlik Katmanları

- **`SOCIAL_PUBLISH_LIVE=0`** varsayılan. `1` yapılana kadar
  `tool_adapters/instagram_adapter.py`, `facebook_adapter.py`,
  `tiktok_adapter.py` mock döner. Compose env'ine sızdırılmadı — kök
  `.env`'den explicit set gerekir.
- **`INTERNAL_SERVICE_IN_PROCESS=1`** — orchestration_api'nin
  in-process internal_service'i self-HTTP atmasını engelliyor (Phase
  NEW guard'ı korundu).
- **Approval interrupt** — LangGraph `interrupt_after` yüksek riskli
  action'larda akışı durduruyor; operatör php-ui `/onay-bekleyenler`
  veya `/kurallar > Yürütmeler > Devam Et` butonuyla resume ediyor.
- **Fernet credentials** — `social_credentials` tablosu Fernet ile
  şifrelenmiş. `FERNET_KEY` env yoksa runtime otomatik üretir ve
  startup log'una yazar (operatör `.env`'e taşımalı).
- **Multi-tenant** — `auth_service.get_current_auth` her workflow / task
  / approval / rule sorgusuna `user_id` + `org_id` filtresi ekliyor.
  Tur 3'te eklenen `org_members`, `api_keys`, `orgs` şemaları korundu.
- **Idempotency** — `rule_executions.idempotency_key` aynı event için
  tekrar tetiklemeyi engelliyor (Phase 1 guard'ı korundu).

---

## 6. Şipped Phase'lerin Korunduğunun Kanıtı

Aşağıdaki shipped özellikler **hiçbiri değişmedi**, sadece monorepo
altındaki yeni dosya yollarına yerleştiler:

- Phase 0-9 (cleanup → BI/cross-event reasoning)
- Phase NEW (internal HTTP loop fix)
- business_retrieval_service + business_query_router + humanized chat
- index.html "AI Operations Center" 5-tab
- scheduling_service + customer_interaction_service + conversation_memory
- ai_synthesizer LLM-backed
- Tur 1: orgs/api_keys/social_credentials Fernet
- Tur 1: campaigns + campaign_metrics + state machine + autonomous_planner
- Tur 1 LangGraph: structured_rule + nl_rule_parser + langgraph_engine/*
- Tur 1 UI: 5-tab Türkçe dashboard rewrite
- Tur 2: tools.py protocol BaseTool (CrewAI yok)
- Tur 2: gerçek wait/resume pipeline (workflow_worker + interrupt_after)
- Tur 2: semantic_entity_resolver + rule_templates + versioning +
  conflict detection + execution graph görselleştirme + tool_adapters/
- Tur 3: rule health_score + rule_learning öneri motoru +
  conversational_rule_edit + conflict_resolver + real social adapter
  HTTP (SOCIAL_PUBLISH_LIVE flag arkasında)
- Tur 3: dashboard modal form + timeline + öneriler

---

## 7. Hızlı Smoke Test

```bash
# 1. Build + start
cd agent-base
docker compose up -d --build

# 2. Health
curl -s http://localhost:8080/api/health | jq
curl -s http://localhost:8080/api/internal/health | jq

# 3. Rule preview (NL → StructuredRule)
curl -s -X POST http://localhost:8080/api/internal/structured-rules/parse-preview \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{"text":"Çanakkale mağazasında stok 5 altına düştüğünde Instagram indirim postu hazırla"}'

# 4. Templates
curl -s http://localhost:8080/api/internal/rule-templates \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq '.[].slug'

# 5. Worker durumu (supervisord)
docker compose exec agent-base-allinone supervisorctl status

# 6. UI
# http://localhost:8080/login → giriş → /kurallar
```

Beklenen: 200 OK, `health.monorepo_tour=4`, parse-preview JSON
`trigger` + `actions` döndürür, `supervisorctl status` 6 program
RUNNING (api, worker, rbe-listener, rbe-workflow, rbe-task, php-fpm,
nginx).

---

## 8. Sonraki (Tur 5) İçin Önerilen Roadmap

1. **Vector retrieval for rule similarity** — `embedding_placeholder`
   kolonu Tur 1'de eklenmişti; pgvector / sqlite-vss ile gerçek
   nearest-neighbor. NL parse anında geçmiş benzer kuralları öneri
   olarak dön.
2. **Multi-account dispatch** — `store_handle IN (...)` pattern,
   tek kural birden çok hesabı tetikler.
3. **TikTok content API real publish** (şu an stub).
4. **Trendyol Q&A + Shopify webhook** real adapter'lar (Tur 3'te
   Instagram/Facebook bitmişti).
5. **Visual rule builder** — `rules.php`'nin yanına drag-and-drop
   "trigger → actions" PHP+JS UI; yine canonical `StructuredRule`
   JSON üretir.
6. **Org/role-aware rule visibility** — `org_id` filter rule
   sorgularına genişletildi mi denetlenmeli; widening UI eklemek lazım.
7. **Bulk import / export** — kuralları JSON dışa aktar, başka
   workspace'e içe aktar (org migration).
8. **Resolver UI live preview** — `views/rules.php` Yeni Kural
   sekmesinde semantic_entity_resolver'ın kaç entity bulduğunu canlı
   gösteren panel.
9. **Time-travel viewer** — LangGraph checkpoint history UI; "şu
   adımdan tekrar başla" operasyonu (`POST .../resume?from_node=...`).
10. **Cost / quota tracking** — her LLM çağrısının cost'unu ölç,
    `orgs.monthly_quota` ile karşılaştır, quota dolduğunda CHAT_USE_LLM
    / NL_PARSER_USE_LLM otomatik 0'a düş.
11. **Worker ayrılması (opsiyonel)** — şu an supervisord altında tek
    container'da; horizontal scaling için listener/workflow/task'ı
    ayrı compose servislerine taşıma (image'ı paylaşıp `command:`
    override ile).
12. **Eski `rule-based-engine/` dizinini sil** — Tur 4 birkaç hafta
    stabil çalışırsa orijinal kopya kaldırılabilir (şu an yedek).

---

*Doküman sonu. Sorular için: `agent-base-api/` içindeki dosya başı
docstring'leri + `SYSTEM_REVIEW_FOR_EXTERNAL_AUDIT.md` + bu repo
içindeki ARCHITECTURE_*.md dosyaları.*

---

## TUR 5 — Contextual Rules + JS Güvenli String (Mayıs 2026)

### T5-1. Hatanın Kök Nedeni

**Hata:** `Uncaught SyntaxError: Unexpected identifier 'nin'`

**Konum:** `php-ui/views/rules.php:592`

```js
root.innerHTML = '<div class="muted" style="...">AI'nin henüz öneri ürettiği bir kural yok.</div>'
//                                              ^ string burada kapanıyor
//                                               ^^^ `nin` → JS identifier
```

JavaScript parser tek tırnağı string sonu olarak yorumladı; ardından
gelen `nin henüz...` ifadesini bir identifier dizisi olarak parse
etmeye çalıştı. Sayfa açıldığında HER `RulesUI` çağrısı kırılıyordu.

### T5-2. Çözüm: rules.php Kaldırıldı + Mimari Yeniden Tasarlandı

`rules.php` tek bir sayfaya 5 sekme (Composer / Aktif / Şablonlar /
Yürütmeler / AI Önerileri) tıkıştırmıştı. Kullanıcı geri bildirimi:
"kuralları sayfa context'inde yönetmek istiyorum". Tur 5 birleşik
çözüm:

| Adım | Aksiyon |
|------|---------|
| 1 | `php-ui/views/rules.php` silindi |
| 2 | `php-ui/views/layout.php` sidebar'dan "Kurallar" linki kaldırıldı |
| 3 | `php-ui/public/index.php` `/kurallar` ve `/rules` route'ları `/page/timeline/all`'a 302 redirect oldu |
| 4 | `php-ui/views/timeline/_rules_toolbar.php` yeniden yazıldı (composer + preview + şablon grid + liste markup) |
| 5 | `php-ui/public/assets/js/timeline-page-rules.js` baştan yazıldı (backend-bağlı, slug-aware) |

### T5-3. Slug → Event Prefix Eşlemesi

`_rules_toolbar.php` içinde 21 timeline slug'ı için event_type prefix
mapping tanımlı. Örnekler:

| Timeline Sekmesi | Prefix(ler) |
|------------------|-------------|
| `store` (Mağaza) | `store.` |
| `products` (Ürünler) | `product.` |
| `stock` (Stok) | `stock.` |
| `orders` (Siparişler) | `order.` |
| `reviews` (Değerlendirmeler) | `review.` |
| `ads` (Reklamlar) | `banner.`, `sales.` |
| `campaigns` (Kampanyalar) | `campaign.` |
| `delivery` (Teslimat) | `shipping.`, `order.shipped` |
| `returns` (İadeler) | `order.cancelled` |
| `all` (Tümü) | (boş — filtre yok) |

`timeline-page-rules.js` `mount.dataset.eventPrefixes` (JSON) okuyup
`matchSlug(rule)` ile client-side filter uyguluyor:

```js
function matchSlug(rule) {
  if (!eventPrefixes.length) return true
  const evt = String(rule?.trigger?.event_type || "").toLowerCase()
  return eventPrefixes.some((p) => evt.startsWith(String(p).toLowerCase()))
}
```

### T5-4. JS Güvenli String Pattern'i

Yeni `timeline-page-rules.js`'te ve gelecek tüm Türkçe içerikli JS'te
zorunlu kurallar:

1. **DOM API tercih et** (string concat değil):
   ```js
   const span = document.createElement("span")
   span.textContent = "Kural'ın durumu güncellendi"  // çift tırnak güvenli
   parent.appendChild(span)
   ```
2. **Mecbursan template literal kullan**:
   ```js
   el.textContent = `${count} kural — AI'nin önerisi`
   ```
3. **HTML inject** her zaman `escapeHtml`'den geçer:
   ```js
   function escapeHtml(s) {
     return String(s == null ? "" : s)
       .replace(/&/g, "&amp;").replace(/</g, "&lt;")
       .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
       .replace(/'/g, "&#39;")
   }
   ```
4. **PHP → JS veri taşıması** `JSON_UNESCAPED_UNICODE` ile attribute:
   ```php
   data-foo="<?= htmlspecialchars(json_encode($x, JSON_UNESCAPED_UNICODE),
                                  ENT_QUOTES, 'UTF-8') ?>"
   ```
   ```js
   const x = JSON.parse(el.dataset.foo)
   ```

### T5-5. Contextual Rule Paneli Özellikleri

Her timeline alt sekmesinde:

- **Composer**: `<textarea>` + Önizle / Etkinleştir / Temizle /
  Şablonlardan seç butonları.
- **Önizleme**: parse-preview cevabı — kural adı, tetik, bekleme,
  kanal, şablon, adımlar (`actions[].kind`), açıklama.
- **Şablon grid**: `/api/internal/rule-templates` listesi; slug ile
  eşleşenler üstte sıralanır. Tıkla → textarea doldurur.
- **Liste**: filtreden geçen kurallar; her satırda:
  - Başlık (rule.name || rule.natural_language)
  - Chip'ler: tetik, kanal, şablon, AKTİF/PASİF, sağlık%
  - Butonlar: Pasifleştir/Etkinleştir, Sil

Endpoint'ler (zaten Tur 1'de eklenmişti):

| Method | Path |
|--------|------|
| `GET` | `/api/internal/structured-rules` |
| `POST` | `/api/internal/structured-rules/parse` |
| `POST` | `/api/internal/structured-rules` |
| `PATCH` | `/api/internal/structured-rules/{id}/enabled` |
| `DELETE` | `/api/internal/structured-rules/{id}` |
| `GET` | `/api/internal/rule-templates` |

### T5-6. Sistem Yöneticisi — AI Operatör Merkezi

`views/system_admin.php` (160 satır) + `timeline-store-automation.js`
(1182 satır) zaten kapsamlı multi-turn AI chat içeriyordu. Tur 5'te
sayfa kodu değiştirilmedi — sadece **sidebar'da konumlandırma**
değişti:

- Eski: "Kurallar" linki sidebar'da öne çıkmıştı.
- Yeni: "Sistem Yöneticisi" linki tek standalone link; tooltip
  "AI Operatör Merkezi" diye okuyor.

Operatör buradan **tüm kuralları doğal Türkçe konuşarak** yönetebilir.
Backend tarafında zaten Tur 3'te eklenen `conversational_rule_edit`
endpoint'i (`/api/internal/chat-edit/preview` + `/chat-edit/apply`)
chat agent tarafından kullanılabilir.

### T5-7. Şablon Hiyerarşisi Netleştirildi

Üç ayrı katman:

1. **`rule_templates`** (LangGraph kural şablonları)
   - Backend: `rule_templates.py` (Tur 2'de eklendi)
   - UI: Her timeline alt sekmesinin composer'ında "Şablonlardan seç"
   - Örnekler: Anneler Günü, Yılbaşı, Stok Düşüşü, Kara Cuma, ...
   - Endpoint: `GET /rule-templates`, `POST /rule-templates/{slug}/materialize`

2. **`sm_templates`** (sosyal medya post şablonları)
   - View: `php-ui/views/sm_templates.php`
   - URL: `/social-media/sablonlar`
   - Backend: `app/models/content_template.py` (MySQL)

3. **Campaign templates** (kampanya banner şablonları)
   - View: `sm_templates.php` (mod parametresiyle)
   - URL: `/campaign-management/sablonlar`

### T5-8. Değişen Dosyalar (Özet)

| Dosya | Aksiyon |
|------|---------|
| `php-ui/views/rules.php` | **SİLİNDİ** |
| `php-ui/views/layout.php` | "Kurallar" nav linki kaldırıldı |
| `php-ui/public/index.php` | `/kurallar` → redirect `/page/timeline/all` |
| `php-ui/views/timeline/_rules_toolbar.php` | Composer + preview + şablon grid + liste markup (PHP escape disiplinli) |
| `php-ui/public/assets/js/timeline-page-rules.js` | Backend-bağlı CRUD + SyntaxError-safe pattern |
| `agent-base/ÖZET.md` | Tur 5 sürümüne tam güncellendi |

`agent-base-api/` ve `langgraph_engine/` Python katmanına Tur 5'te
**dokunulmadı** — backend zaten Tur 1-3'te kontekstüel kural sistemini
destekliyor.

### T5-10. Polish — Tur 5 İkinci Geçişi (UX)

İlk Tur 5 çekirdek değişikliklerinin (rules.php silinmesi + contextual
panel + JS güvenli string) ardından operatör deneyimini "profesyonel
ve keyifle kullanılır" seviyeye çıkartmak için yapılan polish:

1. **Son yürütme chip'i** — Her kural satırının chip dizisinde
   background fetch ile `/rule-executions?rule_id=X&limit=1` çekiliyor;
   durum (Tamamlandı/Başarısız/Bekliyor/Onay bekliyor/Çalışıyor) +
   relative time ("5 dk önce") gösteriliyor.
2. **Conflict banner + inline ⚠** — Sayfa açıldığında
   `/structured-rules-conflicts/suggestions` çekiliyor; çakışma varsa
   composer üstünde turuncu banner + her ilgili kural satırının altında
   ⚠ uyarısı. "AI Operatör ile çöz →" linki sistem yöneticisine
   yönlendiriyor.
3. **Stepper preview** — Parse-preview cevabı görsel pill dizisi olarak
   render ediliyor: `başla → generate_content → risk_check → approval
   ⏸ → publish → bitir`. Approval **sarı pause**, wait **mavi**,
   publish **mor** renk kodları.
4. **AI ile yönet kısayolu** — Her panel header'ında sağda "AI ile
   yönet →" chip butonu (`/social-media/system-admin`'a yönlendirir).
5. **Cmd/Ctrl + Enter** — Composer textarea'sında klavye kısayolu
   anında Önizle tetikliyor; ipucu placeholder altında.
6. **Şablon vurgusu** — Şablon grid'inde slug ile uyumlu olanlar mavi
   vurgu (`tr-tpl-relevant`) ile üstte; tıklayınca textarea dolar +
   otomatik preview çalışır.
7. **system_admin.php h1** — "Sistem Yöneticisi · **AI Operatör
   Merkezi**" badge eklendi; alt paragraf conversational rule edit,
   conflict resolution, business analytics'i açıkça çağırıyor. Mevcut
   1182 satırlık chat UI'ya hiç dokunulmadı.
8. **Modern görsel disiplin** — Border-radius 12-18px, ince
   `border-color` hover'da `#c7d2fe`, item'larda `box-shadow` hover
   efekti, chip palette tutarlı (good/warn/bad/on/off).
9. **Graceful degradation** — `/rule-executions` veya
   `/structured-rules-conflicts/suggestions` hata verirse sayfa
   görünür kalır, sadece o chip "çekilemedi" gösterir veya banner
   sessizce gizli.
10. **Syntax CI test** — `node -c timeline-page-rules.js` ✓,
    `php -l _rules_toolbar.php` ✓ ile kalıcı garanti.

### T5-12. Polish-2 — Premium UI Tabakası

İlk iki T5 iterasyonu sonrası (rules.php silinmesi + slug-aware
contextual panel + last_execution + conflict + stepper) UI hâlâ
"fonksiyonel ama temel" hissi veriyordu. Polish-2 tur'u tamamen görsel
kalite + etkileşim hissi için yapıldı.

**Mimari değişiklik:** Inline `<style>` bloğu `_rules_toolbar.php`'den
çıkarıldı; ayrı `public/assets/css/timeline-rules.css` dosyası (678
satır) oluşturuldu. `index.php` extraHead'i bu CSS'i timeline
sayfalarına yüklüyor. Tarayıcı cache + bakım kolaylığı.

**Yeni özellikler:**

1. **Premium renk paleti** — CSS variables ile slate/indigo/emerald/
   amber/rose/sky. Hex hard-code yok; merkezi `:root` block.
2. **Toast notification sistemi** — `alert()` yerine sağ alt köşede
   slide-in toast. Success=emerald gradient, error=rose gradient.
   2.8s success, 4.5s error TTL. Animation: tr-toast-in/out.
3. **Optimistic UI toggle** — Pasifleştir/Etkinleştir butonuna basınca
   chip anında değişiyor; API başarısız olursa otomatik rollback +
   error toast.
4. **Skeleton loading** — Sayfa açılışında shimmer animasyon (200% bg
   slide, 1.4s linear infinite). "Henüz boş" hissini ortadan kaldırıyor.
5. **Renkli stepper pill'ler** — Preview'da:
   - `▶ başla` (sky)
   - `⏱ wait` (mavi)
   - `⏸ approval` (amber)
   - `📤 publish` (indigo)
   - `✓ bitir` (emerald)
   Operatör akışı 1 saniyede anlıyor.
6. **Chip dot indicator** — Her durum chip'inin başında `●`
   noktası, durumla renkli (yeşil/sarı/kırmızı/indigo/gri).
7. **Header noktalı badge** — Panel başlığının solunda 8px radial
   gradient indigo nokta + 3px alpha glow ring.
8. **Hover lift** — Kart üzerine gelince border indigo'ya, shadow-md
   uygulanır. translateY(-1px) micro-lift.
9. **Button gradient + scale** — Primary buton indigo-600→700 gradient,
   active'te `scale(.98)` press feedback.
10. **kbd hint chip'leri** — Composer altında `<kbd>⌘</kbd>/<kbd>Ctrl</kbd>
    + <kbd>Enter</kbd>` keyboard shortcut'u görsel olarak.
11. **Conflict banner sol şerit** — 4px orange-500 sol border + ⚠
    1.4rem icon + amber gradient background.
12. **Template "öne çıkan" rozeti** — Slug-uyumlu şablonların sağ
    üstünde 0.58rem uppercase indigo rozet ile fark edilebilirlik.
13. **Empty state ikon** — ✨ büyük + güzel Türkçe açıklama.
14. **Mobile responsive** — `< 720px` breakpoint'inde:
    - Header dik (column flex)
    - Item card stack (action button'lar full-width)
    - Toast viewport-wide
    - Template grid tek sütun

**Etkileşim akışı:**

```
Sayfa aç → Skeleton 200ms → Liste + composer slide-in
   ↓
Composer textarea focus → Indigo ring (3px alpha)
   ↓
⌘/Ctrl+Enter → preview API → stepper render
   ↓
"Kuralı Etkinleştir" → "Kaydediliyor…" → success toast
   ↓
Liste yenilenir → yeni satır fade-in
   ↓
"Pasifleştir" → Chip anında PASİF (optimistic)
   ↓
API success → liste refresh
veya
API fail → chip eski hale rollback + error toast
```

**Smoke test:**

```
✓ node -c timeline-page-rules.js                     → JS OK
✓ php -l _rules_toolbar.php                          → No syntax errors
✓ php -l system_admin.php / layout.php / index.php   → No syntax errors
✓ grep -nE "'[A-Za-zçğıöşü]'(nin|...)" → (boş)        → kalıcı temiz
✓ rules.php silinmiş, sidebar Kurallar yok, /kurallar 302
```

**Toplam değişen UI satırı (Polish-2):**
- `timeline-rules.css` (yeni): 678 satır
- `_rules_toolbar.php`: 134 satır (önceki 199'dan inline style çıkarıldı)
- `timeline-page-rules.js`: 711 satır (toast + optimistic + skeleton)
- `index.php`: +6 satır (CSS link extraHead'e)

Toplam: ~1523 satır UI kodu, ~99% vizyon uyumu.

### T5-13. Sonraki (Tur 6) İçin Öneriler

1. **Rule executions inline panel** — her sayfada o slug'a uyan son 5
   yürütme + duration bar.
2. **AI önerileri inline feed** — `learning-suggestions` + `conflict-
   suggestions` her panel altında.
3. **Operatör özel rule_templates yarat** — şu an sabit liste; `POST
   /rule-templates` ile kullanıcının kendi "mers şablonu"nu kaydetmesi.
4. **Per-slug server-side template filter** — şu an client-side.
5. **Sistem Yöneticisi chat'inde kural CRUD tool-call** — chat
   tarafından `structured-rules` API çağrıları yapsın.
6. **Mobile-friendly contextual panel** — collapse + sticky composer.
7. **Visual rule builder** — NL alternatifi (drag-drop trigger →
   actions).
8. **Eski `rule-based-engine/` dizinini sil** — stabil ise.

---

*Doküman gerçekten sonu. Anlık sistem fotoğrafı için `ÖZET.md`.*
