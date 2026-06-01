# Agent Base (All-in-One Docker)

Bu repo frontend + `agent-base-api` backend'i **tek container** içinde calistirir.

- Frontend build: `node:22-bookworm-slim`
- API runtime: `python 3.11 (uv)` + `uvicorn`
- Reverse proxy/static serve: `nginx`
- Process manager: `supervisord`

## Sunucuda hizli kurulum (docker compose)

### 0) Gereksinim

Sunucuda Docker ve Docker Compose plugin kurulu olmali:

```bash
docker --version
docker compose version
```

### 1) Repoyu cek

```bash
git clone <REPO_URL> agent-base
cd agent-base
```

### 2) Ortam degiskenlerini duzenle

#### 2.1 Frontend build env (`./.env`)

`VITE_API_URL` mutlaka `/api` olmali (tek container proxy icin):

```env
VITE_API_URL=/api
VITE_HOLIDAY_COUNTRY=TR
VITE_STORAGE_PUBLIC_URL=https://pub-xxxxx.r2.dev
WEB_PORT=8080
```

`VITE_STORAGE_PUBLIC_URL`, arayüzün silinebilir medya URL’lerini tanıması için backend’de dönen **public kök** ile aynı olmalıdır (`R2_PUBLIC_BASE_URL` veya `https://{bucket}.r2.dev` veya **cloudflared** hostname).

#### 2.2 Backend runtime env (`./agent-base-api/.env`)

`agent-base-api/.env` dosyasini sunucuda doldur (OpenAI, Cloudflare R2, SMTP vb. anahtarlar). R2 için: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`. Public URL: **`R2_PUBLIC_BASE_URL=https://...`** (özel domain veya cloudflared), alternatif olarak boş bırakıp **`R2_PUBLIC_R2_DEV_HOST=pub-xxxxx.r2.dev`** (R2 konsoldaki Public Development URL hostu). Yerel disk için: `MEDIA_STORAGE=local`, `MEDIA_ROOT`, `MEDIA_PUBLIC_BASE_URL`.

Not: Bu dosyadaki secretlari GitHub'a koyma; sadece sunucuda tut.

#### 2.3 Arka plan isleri (Celery + Redis)

`docker compose` ile birlikte **Redis** ayaga kalkar; API container icinde **Celery worker** da calisir.

- `USE_CELERY=true` (varsayilan) iken agir isler kuyruga alinir: gorsel uret / referansli uret / revize / ozel gun / caption.
- Sonuc: Ayni anda birden fazla istek (farkli kullanicilar veya ayni kullanicinin paralel isleri) worker havuzunda siralanir; HTTP istegi cabuk doner (`task_id`), UI `GET /social-media/tasks/{id}` ile durumu okur.
- Paralel is sayisi: ortam degiskeni `CELERY_WORKER_CONCURRENCY` (varsayilan `4`). Gerekirse `.env` veya compose ile artir/azalt.
- **Özel gün (tatil) taslağı:** Arayüzde Ayarlar’dan OpenAI anahtarı yoksa bile istek gider; sunucu `agent-base-api/.env` icindeki `OPENAI_API_KEY` ile üretir (Docker’da tarayıcı key’i zorunlu degil).

### 3) Build + ayaga kaldir

```bash
docker compose up -d --build
```

Uygulama: `http://SERVER_IP:8080` (veya `WEB_PORT` ne verdiysen).

### 4) Durum ve log kontrolu

```bash
docker compose ps
docker compose logs -f agent-base-allinone
```

### 5) Guncelleme (yeni kod deploy)

```bash
git pull
docker compose up -d --build
```

### 6) Durdurma / yeniden baslatma

```bash
docker compose stop
docker compose restart
docker compose down
```

---

## Tur 4 — LangGraph Rule Engine (Monorepo birleşim)

Bu repo `rule-based-engine/` ile birleşti. Tek FastAPI process artık iki rolü
birden taşıyor: mevcut **agent-base API** (auth/social_data/social_media) +
**orchestration_api** (LangGraph + structured rules + scheduling + campaign +
auth/org + social credentials).

### Mimari (all-in-one)

```
agent-base-allinone (Docker)
├─ nginx          → /api → uvicorn :8000  /  /* → php-fpm (php-ui)
├─ php-fpm        → php-ui (frontend, Türkçe operasyon paneli)
├─ uvicorn        → app.main:app
│                    ├─ app.api.auth / social_data / social_media   (Tur 1-3)
│                    └─ orchestration_api.router  → /api/internal/* (Tur 4)
├─ celery worker  → image/medya/caption agir is kuyruğu
└─ supervisord ile yönetilen LangGraph worker'ları (Tur 4):
   ├─ rbe-listener        → listener.py (event polling + planner)
   ├─ rbe-workflow        → workflow_worker.py (workflow runner)
   └─ rbe-task            → task_executor.py (task runner)
```

`listener.py`, `workflow_worker.py`, `task_executor.py` flat Python
modülleri `/opt/api/` altına kopyalandı (`app.main`'in `sys.path.insert`
çağrısı sayesinde `import db`, `from langgraph_engine import runtime`
gibi flat import'lar çalışıyor).

### Yeni env değişkenleri (`.env` veya compose `environment:`)

| Değişken                      | Varsayılan                      | Açıklama                                              |
|-------------------------------|---------------------------------|-------------------------------------------------------|
| `INTERNAL_SERVICE_IN_PROCESS` | `1` (compose'ta zorunlu set)    | orchestration_api'nin self-HTTP loop guard'ı için    |
| `LANGGRAPH_CHECKPOINT_DB`     | `/opt/api/listener.db`          | SqliteSaver checkpoint dosyası                        |
| `SOCIAL_PUBLISH_LIVE`         | `0`                             | `1` yapılana kadar gerçek social post atılmaz        |
| `CHAT_USE_LLM`                | `1`                             | business_chat LLM aracılığı                           |
| `NL_PARSER_USE_LLM`           | `1`                             | nl_rule_parser LLM augmentation                       |
| `FERNET_KEY`                  | (boş → otomatik üret)           | social_credentials şifreleme anahtarı                 |

`SOCIAL_PUBLISH_LIVE=0` korunuyor — Instagram/Facebook adapter'leri "mock"
modda çalışır. `1`'e çekmek için credentials'ı `/api/internal/credentials`
endpoint'inden Fernet ile kaydet ve ardından env'i set et.

### Yeni endpoint'ler (`orchestration_api` router'ından mount edilen)

`/api/internal/*` altında 80+ endpoint. Önemlileri:

- **Structured rules**: `POST /api/internal/structured-rules/parse-preview`,
  `POST /api/internal/structured-rules`, `GET /api/internal/structured-rules`,
  `POST /api/internal/structured-rules/{id}/test-run`,
  `GET /api/internal/structured-rules-conflicts/suggestions`.
- **Templates**: `GET /api/internal/rule-templates`,
  `POST /api/internal/rule-templates/{slug}/materialize`.
- **Executions**: `GET /api/internal/rule-executions`,
  `GET /api/internal/rule-executions/{id}`,
  `POST /api/internal/rule-executions/{id}/resume`.
- **Learning**: `GET /api/internal/learning-suggestions`.
- **Campaigns**: `/api/internal/campaigns*`,
  `GET /api/internal/scheduled-entries`.
- **Auth (multi-tenant)**: `/api/internal/orgs*`, `/api/internal/api-keys*`.
- **Credentials**: `/api/internal/credentials*` (Fernet).
- **Dashboard**: `GET /dashboard` (legacy SPA), `GET /api/internal/dashboard/*`.
- **Fake commerce (test/demo)**: `/commerce-platform/internal/create-*`
  (eski `rule-based-engine/main.py` `fake_commerce_app.py`'a taşındı,
  sub-app olarak mount; listener.py polling akışı bozulmasın diye korundu).

### php-ui — yeni sayfa

`/kurallar` (alias `/rules`) — LangGraph rules management 5-tab Türkçe
operasyon paneli:

1. **Yeni Kural** — NL composer + parse-preview stepper.
2. **Aktif Kurallar** — toggle / sil / health badge.
3. **Şablonlar** — `rule_templates.py` ile materialize.
4. **Yürütmeler** — execution timeline + duration bar + resume button
   (wait/interrupt akışı için).
5. **AI Önerileri** — `rule_learning.py` + `conflict_resolver.py`
   feed'leri.

Sol menüye `git-branch` ikonlu **Kurallar** linki eklendi.

### CrewAI tamamen kaldırıldı

Tur 2'de başlayan removal Tur 4'te tamamlandı:

- `app/agents/manager/agent_factory.py` → `LightAgent` dataclass.
- `app/services/agent_runtime_service.py` → native OpenAI/Gemini chat
  completion. Public API (`run_agent`, `manager_run`) korunuyor.
- `crewai_worker.py` → LangGraph node'larına bağlanmış mock runner; yeni
  CrewAI dependency'si yok.
- `pyproject.toml`'da `crewai[tools]` yerine `langgraph`,
  `langgraph-checkpoint-sqlite`, `cryptography`, `pydantic>=2.0`.

### Hızlı doğrulama

```bash
# API health
curl -s http://localhost:8080/api/health | jq

# Orchestration health (LangGraph mount)
curl -s http://localhost:8080/api/internal/health | jq

# Rule create (preview)
curl -s -X POST http://localhost:8080/api/internal/structured-rules/parse-preview \
  -H "Content-Type: application/json" \
  -d '{"text": "Stok 5 altına düştüğünde Instagram'\''e indirim post hazırla"}'
```

`SOCIAL_PUBLISH_LIVE=0` modunda gerçek paylaşım yapılmaz; tüm zincir
listener.db'ye yazılır, dashboard ve php-ui'dan izlenebilir.
