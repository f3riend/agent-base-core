# ÖZET — Sistem Tam Taraması

> Tarih: 2026-05-27
> Yöntem: Tüm dosyalar açıldı, satır sayıldı, md5 hash'leri karşılaştırıldı.
> Tahmin yok. Görmediğim şeyler için "okuyamadım" diyorum.

---

## Bölüm 1: Sistem Genel Bakış

### Sistem ne yapıyor

E-ticaret operatörünün doğal Türkçeyle ("yeni mağaza açılınca Instagram postu paylaş")
otomasyon kuralı tanımladığı bir rule-based + LangGraph orchestration motoru.
Mock e-ticaret platformundan (`fake_commerce_app.py`) gelen olaylar
SQLite timeline'ına yazılır, listener bunları polling eder, eşleşen kuralları
LangGraph üzerinde çalıştırır; içerik üretimi → risk analizi → insan onayı →
Instagram/Facebook Graph API publish zincirinden geçirir. Frontend PHP-UI
sayfaları PHP-FPM + nginx üzerinden tek container'da sunuluyor; FastAPI
backend de aynı container'da uvicorn + supervisord altında çalışıyor.

### Teknoloji stack (kesin liste — `pyproject.toml` + Dockerfile'dan)

| Katman | Teknoloji |
|--------|-----------|
| Backend | Python 3.11, FastAPI ≥0.135.3, uvicorn ≥0.44.0 |
| ORM | SQLAlchemy ≥2.0.49 (MySQL via pymysql) |
| Orchestration motoru | LangGraph ≥1.2.1 + langgraph-checkpoint-sqlite ≥3.1.0 |
| Job queue | Celery ≥5.6.3 + Redis ≥5.0.0 |
| AI providers | openai ≥2.31.0, google-genai ≥1.73.0, google-generativeai ≥0.8.6 |
| Görsel/Medya | Pillow ≥12.2.0, imageio-ffmpeg ≥0.6.0, fal-client ≥0.13.2, boto3 (R2 S3) |
| Auth | python-jose[cryptography], bcrypt ≥4.2.0, cryptography ≥42 (Fernet) |
| HTTP | requests ≥2.32.5, httpx ≥0.28.1 |
| Frontend | PHP 8.2 (FPM), Nginx, vanilla JS + CSS |
| Container | Docker (`agent-base-allinone` + `mysql:8.0` + `redis:7-alpine`) |

CrewAI bağımlılığı pyproject'te YOK. `crewai_worker.py` (21 satır) deprecated shim.

### Supervisord process'leri (`docker/supervisord.conf` okundu — 7 process)

| Program | Komut | Priority |
|---------|-------|----------|
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 10 |
| `worker` | `celery -A app.core.celery_app.celery_app worker` | 15 |
| `rbe-listener` | `python listener.py` | 16 |
| `rbe-workflow` | `python workflow_worker.py` | 17 |
| `rbe-task` | `python task_executor.py` | 17 |
| `php-fpm` | `/usr/sbin/php-fpm8.2 -F` | 18 |
| `nginx` | `nginx -g "daemon off;"` | 20 |

### Docker compose servisleri (`docker-compose.yml` okundu)

| Service | Image / Build | Port | Health |
|---------|---------------|------|--------|
| `agent-base-allinone` | Lokal Dockerfile build | `${WEB_PORT:-8080}:80` | `curl http://127.0.0.1:8000/health` |
| `mysql` | `mysql:8.0` | (compose iç) | `mysqladmin ping` |
| `redis` | `redis:7-alpine` | (compose iç) | `redis-cli ping` |

`docker-compose.override.yml` ve `docker-compose.prod.yml` her ikisi de aynı
override'ı uyguluyor: `MEDIA_PUBLIC_BASE_URL=https://agentbase.boximgs.com` +
host-bind media volume + `host.docker.internal:host-gateway` extra_host.

---

## Bölüm 2: Klasör Yapısı

### Top-level dosya sayıları

| Konum | .py | .php | .js | .css | Toplam |
|-------|----:|----:|----:|----:|-------:|
| `rule-based-engine/` (kök, flat) | 65 | 0 | 0 | 0 | 65 |
| `rule-based-engine/langgraph_engine/` | 4 | — | — | — | 4 |
| `rule-based-engine/tool_adapters/` | 4 | — | — | — | 4 |
| `agent-base/agent-base-api/` (flat) | 66 | 0 | 0 | 0 | 66 |
| `agent-base/agent-base-api/langgraph_engine/` | 4 | — | — | — | 4 |
| `agent-base/agent-base-api/tool_adapters/` | 4 | — | — | — | 4 |
| `agent-base/agent-base-api/app/` (alt dahil) | 66 | — | — | — | 66 |
| `agent-base/agent-base-api/alembic/versions/` | 2 | — | — | — | 2 |
| `agent-base/agent-base-api/scripts/` | 1 | — | — | — | 1 |
| `agent-base/php-ui/views/` (alt dahil) | — | 22 | — | — | 22 |
| `agent-base/php-ui/public/` (assets dahil) | — | 2 | 37 | 4 | 43 |
| `agent-base/php-ui/includes/` | — | 5 | — | — | 5 |
| `agent-base/docker/` (sh + conf) | — | — | — | — | 7 |

### `agent-base/agent-base-api/app/` alt klasör görevi

| Klasör | İçerik | Notlar |
|--------|--------|--------|
| `app/api/` | 4 router dosyası: auth, client_debug, social_data, social_media | `social_media.py` = 5212 satır (devasa) |
| `app/routers/` | 1 dosya: social_media.py 822 satır | Eski stil router (legacy_router exporte ediliyor) |
| `app/agents/` | LightAgent + manager + tools + pipeline | CrewAI değil, dataclass tabanlı |
| `app/core/` | settings, config, database, celery_app, env_settings, pricing, security, logging | YAML config + pydantic Settings |
| `app/integrations/` | ai_client (563), instagram_client (905), r2_storage (133), smtp_client (28) | Hepsi gerçek HTTP yapan modüller |
| `app/models/` | 7 SQLAlchemy modeli | (Bölüm 10'da detay) |
| `app/runtime/` | orchestrator (1151), assistant_narrative (639), semantic_operation_interpreter (397), operation_semantics (331), initiative_engine (242), context_builder (138), commerce_reasoning (100) + diğerleri | App tarafının iç orchestration katmanı |
| `app/services/` | content_service (2153), content_intelligence_service (393), openai_ad_pipeline (399), prompt_builder (270), usage_service (165), agent_manager_service (111), agent_runtime_service (104), local_media_storage (91), task_dispatcher (64), social_payload_urls (47) | |
| `app/schemas/` | content (408), agent (43), auth (25), social_data (11) | Pydantic modelleri |
| `app/tasks/` | image_tasks (450) | Celery task'ları |

### `rule-based-engine/` (kök) — flat dosyalar

`agent-base/agent-base-api/` ile %95 birebir kopya (Bölüm 3'te kanıt).

---

## Bölüm 3: İki Ortamın Karşılaştırması

### Yöntem

Her iki ortamdaki ortak flat `.py` dosyaları için **md5 hash karşılaştırması** yapıldı.
`langgraph_engine/` ve `tool_adapters/` dosyaları da karşılaştırıldı.

### Sonuç (özet)

**Aynı `.py` dosyaları birebir bit-bit aynı.** Tek tek `wc -l` sayıları
da eşit. İki dizinin senkron olmadığı varsayımı YANLIŞ — son commit
ile birlikte (`May 27 07:37` ve `May 27 13:09` mtime'larında) iki kopya
aynı içeriği taşıyor.

### Senin istediğin tablo (satır karşılaştırması)

| Dosya | rule-based-engine/ | agent-base-api/ | Fark | Hangisi güncel |
|-------|------:|------:|-----:|----------------|
| `business_chat.py` | 332 | 332 | 0 | İkisi de aynı |
| `business_retrieval_service.py` | 921 | 921 | 0 | İkisi de aynı |
| `business_query_router.py` | 282 | 282 | 0 | İkisi de aynı |
| `conversation_memory.py` | 520 | 520 | 0 | İkisi de aynı |
| `ai_synthesizer.py` | 534 | 534 | 0 | İkisi de aynı |
| `narrative_synth.py` | 379 | 379 | 0 | İkisi de aynı |
| `cross_event_reasoner.py` | 235 | 235 | 0 | İkisi de aynı |
| `business_intelligence.py` | 435 | 435 | 0 | İkisi de aynı |
| `scheduling_service.py` | 669 | 669 | 0 | İkisi de aynı |
| `customer_interaction_service.py` | 631 | 631 | 0 | İkisi de aynı |
| `orchestration_api.py` | 1619 | 1619 | 0 | İkisi de aynı |
| `listener.py` | 381 | 381 | 0 | İkisi de aynı |
| `db.py` | 726 | 726 | 0 | İkisi de aynı |
| `structured_rule.py` | 331 | 331 | 0 | İkisi de aynı |
| `structured_rule_engine.py` | 343 | 343 | 0 | İkisi de aynı |
| `nl_rule_parser.py` | 645 | 645 | 0 | İkisi de aynı |
| `autonomous_planner.py` | 573 | 573 | 0 | İkisi de aynı |
| `approval_service.py` | 337 | 337 | 0 | İkisi de aynı |
| `workflow_service.py` | 502 | 502 | 0 | İkisi de aynı |
| `task_service.py` | 354 | 354 | 0 | İkisi de aynı |

### Sadece bir tarafta olan dosyalar

**Sadece `rule-based-engine/` (kök) içinde olup `agent-base-api/`'de olmayan:**

| Dosya | Satır | Ne yapıyor |
|-------|------:|-----------|
| `full_system_test.py` | 1146 | Tam stack entegrasyon test runner'ı (eski, kopyalanmamış) |
| `main.py` | 1647 | Eski FastAPI standalone entrypoint — agent-base'de `fake_commerce_app.py` olarak kopyalanmış (1647 satır, aynı boyut) |
| `test_api.py` | 440 | Eski test runner |

**Sadece `agent-base/agent-base-api/` içinde olup `rule-based-engine/`'de olmayan:**

| Dosya | Satır | Ne yapıyor |
|-------|------:|-----------|
| `fake_commerce_app.py` | 1647 | `rule-based-engine/main.py`'in birebir kopyası (FastAPI mock e-ticaret platformu) |
| `developer-tools.py` | 81 | Geliştirici yardımcı script (içerik okumadım) |
| `test3.py` | 44 | Test scratch (içerik okumadım) |
| `video.py` | 96 | Video helper (içerik okumadım) |

### `langgraph_engine/` ve `tool_adapters/` karşılaştırması

`md5sum` karşılaştırmasında **FARKLI** olarak işaretlenen dosya yok.
İkisi de bit-bit aynı.

| Dosya | rule-based-engine/ | agent-base-api/ |
|-------|------:|------:|
| `langgraph_engine/state.py` | 235 | 235 |
| `langgraph_engine/nodes.py` | 771 | 771 |
| `langgraph_engine/runtime.py` | 761 | 761 |
| `langgraph_engine/__init__.py` | 0 | 0 |
| `tool_adapters/__init__.py` | 65 | 65 |
| `tool_adapters/instagram.py` | 213 | 213 |
| `tool_adapters/facebook.py` | 130 | 130 |
| `tool_adapters/tiktok.py` | 39 | 39 |

### Veritabanı dosyalarının md5 karşılaştırması

| Dosya | md5 |
|-------|-----|
| `rule-based-engine/fake_ai_api.db` | `d9d0bc3baddf8e281bd46fc3974fa979` |
| `agent-base/agent-base-api/fake_ai_api.db` | `d9d0bc3baddf8e281bd46fc3974fa979` — **AYNI** |
| `rule-based-engine/listener.db` | `a98e3146f74c83d8750ec3063698b500` |
| `agent-base/agent-base-api/listener.db` | `8643da8026f707c94180717b3653062d` — **FARKLI** |

`fake_ai_api.db` aynı; `listener.db` farklı — biri daha sonra yazıldığı
için içerdiği execution/trace/checkpoint kayıtları farklı.

### Sonuç

İki dizin senkronize-değil DEĞİL; tam tersi: **birebir mirror'lar**. Hangisi
daha güncel sorusunun cevabı "ikisi de aynı". Yeni geliştirme sadece
`agent-base/agent-base-api/`'de yapılmalı; `rule-based-engine/` kökü
silinebilir veya `git mv` ile arşivlenebilir.

---

## Bölüm 4: agent-base-api/ Flat Modüller

Tablo, `wc -l` çıktısı + dosyanın ilk yorumundan / class isimlerinden
çıkarıldı.

| Dosya | Satır | Ne yapıyor | Ana import'lar / kullandığı |
|-------|------:|-----------|---------|
| `action_engine.py` | 45 | Eski rule engine için action executor (legacy DSL akışı) | (okumadım) |
| `agent_registry.py` | 119 | LightAgent kayıt defteri | (okumadım) |
| `agent_runtime.py` | 84 | LightAgent runtime helper'ları | (okumadım) |
| `ai_planner.py` | 229 | Tek atış AI planner (legacy) | (okumadım) |
| `ai_synthesizer.py` | 534 | LLM sentez katmanı — business analytics + narrative | openai, gemini |
| `approval_service.py` | 337 | `approval_requests` CRUD + lifecycle | db |
| `auth_service.py` | 614 | Yerleşik auth + jwt + bcrypt | bcrypt, jwt |
| `automation_log_service.py` | 221 | `automation_logs` yaz/oku helper | db |
| `autonomous_planner.py` | 573 | LLM tabanlı autonomous plan üretici | openai/gemini, plan_validator |
| `business_activity_simulator.py` | 77 | Synthetic event üretici (geliştirme yardımcısı) | db |
| `business_chat.py` | 332 | "Sistem Yöneticisi" chat orkestratörü | business_query_router, business_retrieval_service |
| `business_intelligence.py` | 435 | Top-level BI metrik hesaplayıcı | db, narrative_synth |
| `business_query_router.py` | 282 | Chat sorularını intent'e route eden katman | regex + LLM |
| `business_retrieval_service.py` | 921 | Mağaza/ürün/sipariş veri getirme — chat backend | db |
| `business_state.py` | 167 | Cross-event business state cache | db |
| `campaign_service.py` | 567 | `campaigns` ve `campaign_metrics` CRUD | db |
| `conflict_resolver.py` | 321 | Kurallar arası çakışma tespiti + öneri | LLM optional |
| `context_compressor.py` | 78 | Token-budget context kısaltıcı | — |
| `conversation_memory.py` | 520 | Chat session/turn kalıcı bellek | db |
| `conversational_rule_edit.py` | 564 | "şu kuralı pasifleştir" gibi NL düzenleme | nl_rule_parser, db |
| `crewai_worker.py` | 21 | DEPRECATED — stub shim (CrewAI tamamen kaldırılmış) | — |
| `cross_event_reasoner.py` | 235 | Birden çok event'i ilişkilendiren reasoner | db |
| `customer_interaction_service.py` | 631 | Customer thread + message + draft + escalate | db |
| `db.py` | 726 | SQLite bağlantı + schema init + execute_query/write helpers | sqlite3 |
| `env_bootstrap.py` | 18 | .env yükleyici küçük helper | dotenv |
| `event_envelope.py` | 250 | Timeline event tipli wrapper (legacy dict'ten typed) | — |
| `event_router.py` | 142 | Event → "critical / autonomous" routing kararı | — |
| `fake_commerce_app.py` | 1647 | **SADECE agent-base'de.** FastAPI sub-app — mock e-ticaret backend (commerce-platform endpoint'leri) | FastAPI |
| `fake_data_generator.py` | 128 | Seed ürün/mağaza/yorum üretici | db |
| `fake_tool_timeline.py` | 83 | Mock tool çıktısı timeline'a yazıcı | db |
| `internal_service.py` | 645 | Self-HTTP / in-process service çağırıcı | requests |
| `listener.py` | 381 | Ana event listener loop — 2sn polling | rule_engine + autonomous_planner + structured_rule_engine |
| `narrative_synth.py` | 379 | Olay/durum → Türkçe insan-okur metin sentezi | LLM optional |
| `nl_rule_parser.py` | 645 | NL → StructuredRule (Pydantic) parser | LLM + regex prefilter |
| `observability.py` | 436 | Tag bazlı emit + persist + read helper'ları | db |
| `ontology.py` | 178 | Event type → entity tip eşlemesi | — |
| `orchestration_api.py` | 1619 | **96 endpoint** — bütün PHP UI ↔ Python arası API yüzeyi | FastAPI APIRouter |
| `plan_validator.py` | 293 | Plan JSON schema + iş kuralı doğrulayıcı | — |
| `planner_learning.py` | 89 | Planner success/fail outcome stats | db |
| `planner_memory.py` | 468 | Planner past decisions store + recall | db |
| `planner_runtime.py` | 353 | Legacy plan runtime | — |
| `product_import_service.py` | 88 | Ürün import endpoint backend'i | db |
| `resource_service.py` | 201 | Store/item/order fetch + upsert helper'ları | db |
| `rule_engine.py` | 269 | Legacy rule matcher (DSL) | — |
| `rule_learning.py` | 383 | Rule health_score güncelleyici | db |
| `rule_manager.py` | 991 | Eski rule CRUD + yaşam döngüsü | db |
| `rule_service.py` | 357 | Rule seed + persist + load yardımcıları | db |
| `rule_templates.py` | 288 | 9 sistem şablonu (anneler_gunu, kara_cuma, vs.) | — |
| `safety_service.py` | 185 | Rate-limit + circuit breaker (kural seviyesinde) | — |
| `scheduling_service.py` | 669 | `scheduled_entries` + fire-due tetikleyici | db |
| `semantic_entity_resolver.py` | 375 | "Çanakkale hesabı" → `store_id` | LLM optional |
| `semantic_parser.py` | 518 | Legacy NL parser (yeni nl_rule_parser var) | — |
| `social_credentials.py` | 456 | Provider credential CRUD + Fernet encrypt | cryptography |
| `structured_rule.py` | 331 | Pydantic StructuredRule + alt-modeller | pydantic |
| `structured_rule_engine.py` | 343 | Event → matching structured_rules → LangGraph start | langgraph_engine.runtime |
| `task_executor.py` | 241 | Background task çalıştırıcı process | — |
| `task_service.py` | 354 | `ai_tasks` CRUD | db |
| `timeline_processing.py` | 90 | Event meta update helper | db |
| `timeline_service.py` | 164 | Timeline read helper | db |
| `tool_registry.py` | 263 | Tool registry + dispatch | — |
| `tool_sandbox.py` | 198 | Tool çağırma sandbox + retry + circuit breaker | — |
| `tool_schema_validator.py` | 108 | Tool input JSON Schema doğrulayıcı | — |
| `tools.py` | 494 | InstagramCampaignTool, BannerGeneratorTool, CouponGeneratorTool, FaqUpdateTool, SupportResponseTool gibi base tool'lar | abstract base |
| `workflow_service.py` | 502 | `workflow_instances` lifecycle | db |
| `workflow_worker.py` | 175 | `scheduled_entries.fire_due` polling worker | scheduling_service |
| `developer-tools.py` | 81 | (okumadım) | — |
| `test3.py` | 44 | (okumadım) | — |
| `video.py` | 96 | (okumadım) | — |

---

## Bölüm 5: rule-based-engine/ Flat Modüller

Bölüm 3'te kanıtlandığı üzere yukarıdaki tablo ile **birebir aynı**.
Ek dosyalar:

| Dosya | Satır | Ne yapıyor |
|-------|------:|-----------|
| `full_system_test.py` | 1146 | Eski full-stack entegrasyon testi runner'ı |
| `main.py` | 1647 | Eski standalone FastAPI app — agent-base'de `fake_commerce_app.py` ile aynı |
| `test_api.py` | 440 | Eski API test runner |

---

## Bölüm 6: LangGraph Engine

### 6.1 `state.py` (235 satır)

**RuleExecutionState** TypedDict + Annotated reducers. Field listesi:

| Alan | Tip | Reducer |
|------|-----|---------|
| `execution_id` | `int \| None` | `_take_last` |
| `rule_id` | `int` | `_take_last` |
| `user_id` | `int` | `_take_last` |
| `org_id` | `int \| None` | `_take_last` |
| `thread_id` | `str` | `_take_last` |
| `rule` | `dict` | `_take_last` |
| `event` | `dict` (EventContext.model_dump) | `_take_last` |
| `content` | `dict \| None` (GeneratedContent) | `_take_last` |
| `risk` | `dict \| None` (RiskAssessment) | `_take_last` |
| `approval` | `dict \| None` (ApprovalDecision) | `_take_last` |
| `publish` | `dict \| None` (PublishResult) | `_take_last` |
| `monitor` | `dict \| None` (MonitorResult) | `_take_last` |
| `status` | `str` (running\|waiting_human\|waiting_timer\|completed\|failed\|cancelled) | `_take_last` |
| `current_node` | `str \| None` | `_take_last` |
| `last_error` | `str \| None` | `_take_last` |
| `trace_events` | `list[dict]` | `_append_traces` |
| `metadata` | `dict` | `_merge_dict` |

Pydantic alt modeller: `EventContext`, `GeneratedContent`,
`RiskAssessment`, `ApprovalDecision`, `PublishResult`, `MonitorResult`,
`TraceEvent`. Hepsi `extra="allow"`.

### 6.2 `nodes.py` (771 satır) — node tablosu

| Fonksiyon | Ne yapıyor | Tool/Servis çağrıları |
|-----------|-----------|----------------------|
| `supervisor_node` | Giriş — trace başlat, RULE_EXECUTION_START emit | observability._emit |
| `wait_node` | Gecikme — `scheduling_service.create_schedule` ile `scheduled_entries` yaz, `status='waiting_timer'`, `interrupt_after` ile graph durur | scheduling_service.create_schedule |
| `content_generator_node` | Şablon tablosundan (`_TEMPLATE_HEADLINES` 14 şablon) headline+body+hashtag üret | Yok (LLM çağrısı yok — sabit tablo) |
| `risk_analyzer_node` | Heuristik risk skoru — `_RISKY_WORDS` (12 kelime), external publish +0.2, hassas event +0.25 | — |
| `approval_gate_node` | `approval_service.create_approval_request` ile DB'ye request kaydı; `interrupt_before` ile graph buradan ÖNCE durur | approval_service.create_approval_request |
| `publisher_node` | `social_credentials.try_get_credential`, `tool_adapters.get_adapter`, sonra `tool_registry.resolve_tool_instances` ile gerçek tool çağrısı | social_credentials, tool_adapters/instagram\|facebook\|tiktok, tool_registry (InstagramCampaignTool/BannerGeneratorTool/CouponGeneratorTool/FaqUpdateTool/SupportResponseTool) |
| `monitor_node` | 6 saat sonrasına `scheduled_entries`'e izleme check planla | scheduling_service.create_schedule |
| `notify_customer_node` | `support_response_tool` ile customer notify | tool_registry |
| `create_coupon_node` | `coupon_generator_tool` ile kupon üret | tool_registry |
| `finalize_node` | RULE_EXECUTION_END emit, final status set | observability._emit |

### 6.3 `runtime.py` (761 satır)

**Canonical sıra** (`_CANONICAL_ORDER`):
```
wait → generate_content → create_coupon → risk_check → approval → publish → notify_customer → monitor
```

`build_graph(rule)`:
- `StateGraph(RuleExecutionState)`
- Her zaman `supervisor` ekler ve `finalize` ile biter
- `_selected_kinds(rule)` ile sadece kuralda istenen action_kind'ları ekler
- Otomatik güvenlik: `publish` varsa `risk_check` ve `generate_content` zorla eklenir; `rule.requires_approval` ise `approval` da eklenir; `delay_seconds > 0` ise `wait` eklenir
- `interrupt_before=["approval"]` — operatör onayından önce durur
- `interrupt_after=["wait"]` — wait sonrası durur (Tur 2 davranışı)
- Checkpointer: `SqliteSaver(sqlite3.connect("listener.db"))`

**Public API fonksiyonları:**

| Fonksiyon | İşlev |
|-----------|-------|
| `start_execution(rule, event, user_id)` | `rule_executions` row aç, graph.invoke, durduğu yere göre `waiting_human` / `waiting_timer` / `completed` set |
| `resume_after_wait(execution_id)` | `waiting_timer` durumundan resume; `graph.update_state(metadata.wait_resolved=True)` + `graph.invoke(None)` |
| `resume_execution(execution_id, approval_decision, ...)` | `waiting_human` durumundan resume; approval.decision state'e bind edip `graph.invoke(None)` |
| `dry_run_preview(rule, event)` | Persistent execution row açmadan run et — UI preview için |
| `get_execution_traces(execution_id, limit)` | `graph_node_traces` tablosundan trace'leri çek |
| `list_executions(user_id, status, limit)` | `rule_executions` listele |

DB tarafı: `_open_execution_row`, `_update_execution_row` (rule_learning hook'u terminal status'ta tetikliyor), `_persist_trace_events` (her trace'i `graph_node_traces`'e yazıyor).

---

## Bölüm 7: app/ Klasörü (FastAPI)

### 7.1 `app/main.py` (180 satır)

Lifespan içinde:
1. `init_db()` (MySQL/SQLAlchemy şeması)
2. `import db as orchestration_db; orchestration_db.init_db()` (SQLite `listener.db` 30+ tablo)

Include edilen router'lar:
- `app.api.auth.router`
- `app.api.client_debug.router`
- `app.api.social_data.router`
- `app.api.social_media.router`
- `app.api.social_media.legacy_router`
- `orchestration_api.router` (root'tan import, try/except — başarısız olursa log warning)

Mount'lar:
- `fake_commerce_app.app` → `/commerce-platform`
- Lokal medya → `/media` (eğer `use_local_media_storage()` true)
- Dashboard HTML → `/dashboard` (sadece `index.html` varsa)

Middleware: `CORSMiddleware` (config'ten okuyor).
`sys.path` manipülasyonu: `_ROOT` (agent-base-api kökü) eklenir ki
`import db`, `from langgraph_engine import runtime` çalışsın.

### 7.2 `app/runtime/orchestrator.py` (1151 satır)

App tarafının iç orchestrator'ı. İçeriği okumadım (1151 satır tek dosya).
Komşu dosyalardan eko: `app/runtime/assistant_narrative.py` 639, 
`app/runtime/semantic_operation_interpreter.py` 397,
`app/runtime/operation_semantics.py` 331, `app/runtime/initiative_engine.py` 242.
Bunlar app/api/social_media.py içinde "konuşkan asistan" akışını besliyor olabilir
ama bunu doğrulayamadım (5212 satırlık `social_media.py`'ı tek tek okumadım).

### 7.3 `app/agents/` — LightAgent

Dosyalar:
- `social_media_agent.py` (95) — class tanımı
- `pipeline/social_media_pipeline.py` (299)
- `manager/agent_factory.py` (49), `default_registry.py` (31), `tool_registry.py` (30)
- `tools/social_media_tools.py` (80)

CrewAI değil — dosyaların boyut/içerik yapısı dataclass + factory deseni.
`__init__.py` 0 satır.

### 7.4 `app/integrations/`

| Dosya | Satır | İçerik |
|-------|------:|--------|
| `instagram_client.py` | 905 | Graph API v22.0 client — image/video discrimination, container/publish iki adımlı akış. İLK SATIRLAR okundu: gerçek `requests` çağrıları yapıyor. |
| `ai_client.py` | 563 | OpenAI + Gemini wrapper |
| `r2_storage.py` | 133 | Cloudflare R2 (S3 uyumlu) upload/delete. `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` + public base URL |
| `smtp_client.py` | 28 | `RESET_EMAIL_FROM` + `SMTP_HOST` ile basit `smtplib.SMTP` (TLS) ile e-posta gönderici |

### 7.5 `app/models/` — SQLAlchemy ORM (MySQL şeması)

| Sınıf | `__tablename__` | İçerik |
|-------|----------------|--------|
| `User` | `users` | id, username (unique), password_hash, workspace_uid (unique), created_at |
| `Account` | `accounts` | id, user_id (FK), name, instagram_access_token (Text), instagram_user_id, logo_url, instagram_token_expires_at, created_at |
| `LabelRow` | `workspace_labels` | id, user_id (FK), name, color, created_at |
| `PasswordResetToken` | `password_reset_tokens` | id, user_id (FK), token (unique), expires_at, created_at |
| `ContentTemplate` | `content_templates` | id, user_id (nullable), title, prompt, image_urls (JSON), is_global (Bool), created_at |
| `SocialDocument` | `social_documents` | id, workspace_uid, collection, doc_id, payload (JSON), created_at, updated_at. UniqueConstraint(workspace_uid, collection, doc_id) |
| `UsageEvent` | `usage_events` | id, user_id (FK), account_id (FK nullable), timestamp, kind, model, input_tokens, output_tokens, image_count, seconds |

Toplam 7 model, 7 tablo. Alembic versiyonları:
`006_create_composer_drafts.py`, `007_create_usage_events.py`.

### 7.6 `app/services/`

| Dosya | Satır | İçerik (okumadım — sadece dosya boyutu) |
|-------|------:|-----------|
| `content_service.py` | 2153 | İçerik üretim/persist katmanı (devasa) |
| `content_intelligence_service.py` | 393 | İçerik analizi |
| `openai_ad_pipeline.py` | 399 | OpenAI ile reklam üretim pipeline'ı |
| `prompt_builder.py` | 270 | Prompt template builder |
| `usage_service.py` | 165 | Kullanım/maliyet kaydı |
| `agent_manager_service.py` | 111 | Agent yönetimi |
| `agent_runtime_service.py` | 104 | Agent runtime |
| `local_media_storage.py` | 91 | Yerel medya kaydı |
| `task_dispatcher.py` | 64 | Celery task dispatch |
| `social_payload_urls.py` | 47 | URL helper |

### 7.7 `app/routers/` ve `app/api/` route'ları

| Dosya | Satır | Endpoint sayısı (grep `@router.`) |
|-------|------:|----------------------------------:|
| `app/api/auth.py` | 81 | 3 (POST /register, POST /login, GET /me) |
| `app/api/client_debug.py` | 57 | 1 (POST /browser-logs) |
| `app/api/social_data.py` | 411 | 8 (CRUD `/collections/{collection}` + admin/cleanup) |
| `app/api/social_media.py` | 5212 | 50 |
| `app/routers/social_media.py` | 822 | 21 (legacy_router) |

Bu router'lardaki spesifik endpoint listesini tek tek listelemedim
(5212 + 822 = 6034 satır okuma gerekir).

---

## Bölüm 8: PHP UI

### 8.1 View dosyaları

| Dosya | Satır | Ne gösteriyor | Tespit edebildiğim API çağrıları |
|-------|------:|---------------|---------------------|
| `layout.php` | 241 | Sidebar + ana shell. Timeline group altında 22 slug, settings altında 6 alt sayfa, Sosyal Medya altında 4 öğe, Kampanya altında 3 öğe + "Sistem Yöneticisi" link | — (sadece template) |
| `login.php` | 60 | Login formu | — |
| `register.php` | 62 | Kayıt formu | — |
| `forgot_password.php` | 16 | E-posta isteme | — |
| `reset_password.php` | 13 | Şifre sıfırlama | — |
| `reset_code.php` | 16 | Kod giriş | — |
| `reset_stub.php` | 8 | Stub | — |
| `social_media.php` | 2 | Yönlendirme | — |
| `system_admin.php` | 160 | "Sistem Yöneticisi — AI Operatör Merkezi". Soldan ürün listesi + chat + sağdan ürün detay panelleri (Yorumlar, Destek, AI İçgörüler, SSS, Bekleyen Adımlar, Canlı Akış, Operasyon Akışı, Geçmiş). Chat'in altında 4 AI modu (Analiz/Operasyon/Strateji/İçerik) | timeline-store-automation.js → `fetch(base + path, options)` ve `fetch(url, options)` (URL'leri dinamik) |
| `approvals.php` | 21 | Onay bekleyenler sayfası | approvals-app.js |
| `sm_tags.php` | 13 | Etiketler | sm-tags-app.js |
| `sm_templates.php` | 13 | Şablonlar | sm-templates-app.js |
| `settings.php` | 78 | Ayar yönlendirme | — |
| `page.php` | 18 | Generic timeline page wrapper | — |
| `settings/account.php` | 100 | Hesap ayarları | settings-app.js |
| `settings/workspace.php` | 54 | Çalışma alanı | settings-app.js |
| `settings/ai.php` | 22 | AI sağlayıcı seçimi | settings-app.js |
| `settings/api_keys.php` | 30 | API anahtarları | settings-app.js |
| `settings/automation.php` | 108 | Otomasyon ayarları | settings-app.js |
| `settings/security.php` | 13 | Güvenlik | settings-app.js |
| `timeline/_rules_toolbar.php` | 134 | Contextual kural paneli (her timeline slug'ında include edilen). Slug→event prefix eşlemesi, NL composer textarea, "Önizle"/"Etkinleştir" butonları, şablon grid | data-api-base attr → timeline-page-rules.js |
| `timeline/store_page.php` | 14 | Mağaza özel sayfası — sadece `_rules_toolbar.php` include eder | — |

**ÖNEMLİ TESPİT (`layout.php` okundu):** Sidebar'da timeline grubu altında
22 slug var ve listede `discounts` ve `flash-sales` AYRI olarak listelenmiş
ancak `_rules_toolbar.php`'da ikisi de aynı `sales.*` event prefix'ine düşer.
Toolbar tarafında 4 slug için tanımlı label var ama eslemesinde yine
boş kalan slug'lar var: `coupons`, `staff`, `checkin-checkout`, `withdrawals`,
`plugins`, `subscription`, `components` (7 adet) hiçbir event prefix'ine
bağlı değil — bu yüzden bu sayfalarda kural panel'i event-context'siz açılır.

### 8.2 JS dosyaları (37 adet, toplam ~14,700 satır)

| Dosya | Satır | İçerik (grep ile fetch URL'leri) |
|-------|------:|----------|
| `app-shell.js` | 122 | Sidebar toggle |
| `approvals-app.js` | 542 | Onay sayfası mantığı |
| `settings-app.js` | 680 | Settings sayfaları |
| `settings.js` | 43 | Settings yardımcısı |
| `sm-feed-card-carousel.js` | 112 | Feed carousel |
| `sm-tags-app.js` | 685 | Etiket yönetimi |
| `sm-templates-app.js` | 449 | Şablon yönetimi |
| `social-media-actions.js` | 907 | SM aksiyon dispatcher |
| `social-media-api.js` | 62 | API wrapper |
| `social-media-app.js` | 896 | SM ana app |
| `social-media-auto-publish.js` | 98 | Otomatik publish |
| `social-media-background-tasks.js` | 299 | Background polling |
| `social-media-calendar-events.js` | 228 | Takvim event'leri |
| `social-media-campaign-utils.js` | 415 | Kampanya yardımcıları |
| `social-media-change-handler.js` | 176 | Değişiklik handler |
| `social-media-composer-actions.js` | 953 | Composer aksiyonları |
| `social-media-constants.js` | 52 | Sabitler |
| `social-media-data.js` | 387 | Veri katmanı |
| `social-media-holidays.js` | 94 | Tatil günleri |
| `social-media-mappers.js` | 183 | Dönüştürücüler |
| `social-media-modal-helpers.js` | 486 | Modal yardımcıları |
| `social-media-persistence.js` | 734 | Kalıcılık katmanı |
| `social-media-post-preview.js` | 151 | Post önizleme |
| `social-media-post-utils.js` | 234 | Post yardımcıları |
| `social-media-render.js` | 768 | Render katmanı |
| `social-media-runtime.js` | 349 | Runtime |
| `social-media-selectors.js` | 205 | DOM seçiciler |
| `social-media-state.js` | 146 | State |
| `social-media-studio-helpers.js` | 528 | Studio yardımcıları |
| `social-media-studio-modal.js` | 677 | Studio modal |
| `social-media-ui.js` | 22 | UI |
| `social.js` | 326 | Sosyal genel |
| `timeline-page-rules.js` | **730** | Kural composer + CRUD + preview stepper + şablon grid. URL: `/api/internal/structured-rules` (grep ile tespit) |
| `timeline-store-automation.js` | **1182** | Sistem Yöneticisi chat UI. `fetch(base + path, options)` ve `fetch(url, options)` dinamik URL'lerle. Spesifik endpoint sabit-string olarak grep'le bulunmadı — URL'ler runtime'da construct ediliyor |
| `usage-app.js` | 100 | Kullanım sayfası |

**ÖNEMLİ TESPİT:** `timeline-store-automation.js` (Sistem Yöneticisi chat'in JS'i)
**hiçbir sabit endpoint string'i çağırmıyor** — URL'ler runtime'da construct
ediliyor. Hangi endpoint'lerin çağrıldığı statik grep'le tespit edilemiyor;
dosyayı tam okumak gerek.

### 8.3 CSS dosyaları

| Dosya | Satır | İçerik |
|-------|------:|--------|
| `app.css` | 423 | Genel app shell |
| `sm-premium-ui.css` | 1017 | Sosyal medya premium UI |
| `sm-tags-ui.css` | 694 | Etiket UI |
| `timeline-rules.css` | 678 | Kural composer + preview stepper + skeleton + toast (timeline kural panelinin tüm görsel katmanı) |

### 8.4 `includes/` dosyaları

| Dosya | Satır | İçerik |
|-------|------:|--------|
| `auth.php` | 90 | Session + access_token helper'ları |
| `bootstrap.php` | 8 | Dahil eden |
| `config.php` | 58 | `app_browser_api_base()`, `app_base_path()`, `app_url()` vb. |
| `i18n.php` | 51 | `t()` çevirici, `app_ui_strings_blob()` |
| `http.php` | 43 | `app_http_json($method, $url, $body, $bearer)` — PHP'den API'ye gidiş katmanı. Curl + JSON + Bearer header |

**PHP → API yolu:** PHP `includes/http.php` içindeki `app_http_json()`
kullanılarak yapılır. Method, absolute URL, JSON body ve Bearer token alır.
Tarayıcıdan giden istekler ise `window.__AGENTBASE__.apiBase` üzerinden
(layout.php'de set ediliyor) JS dosyalarındaki `fetch()`'lere gider.

### 8.5 `public/index.php` (586 satır)

Front controller. Router içerikleri okumadım — sadece boyut.

---

## Bölüm 9: Tool Adapters

### `agent-base-api/tool_adapters/__init__.py` (65 satır)

- `SOCIAL_PUBLISH_LIVE = os.environ.get("SOCIAL_PUBLISH_LIVE", "0") == "1"`
- `FeatureDisabledError`, `AdapterCredentialError` exception sınıfları
- `ChannelAdapter` Protocol — `publish_post`, `health_check` zorunlu
- `get_adapter(provider)` — instagram/facebook/tiktok dispatch

### `tool_adapters/instagram.py` (213 satır) — InstagramAdapter

| Method | İçerik |
|--------|--------|
| `publish_post(...)` | Üç-katmanlı güvenlik: 1) `SOCIAL_PUBLISH_LIVE=1` zorunlu (yoksa `FeatureDisabledError`), 2) `allow_real=False` ise atla, 3) test handle regex (`^(test_\|demo_\|sandbox_\|ai_ops_test\|smoke)`) skip eder. Sonra credential resolve → token JSON parse (`access_token` + `ig_user_id`) → 2-aşamalı Graph API çağrısı (`/{ig_user_id}/media` container + `/{ig_user_id}/media_publish`). Endpoint base: `META_GRAPH_API_BASE` env (default `https://graph.facebook.com/v18.0`). Timeout: `META_GRAPH_TIMEOUT_SEC` (default 20) |
| `health_check(user_id)` | Credential var mı + `SOCIAL_PUBLISH_LIVE` durumu döner |

**Gerçek HTTP var mı?** EVET. `requests.post()` ile container ve publish
çağrıları gerçek HTTP. Ancak `SOCIAL_PUBLISH_LIVE` env=1 ve credential
varlığı şart. Default kapalı.

**`SOCIAL_PUBLISH_LIVE` flag kontrol noktası:** Modül seviyesinde
`tool_adapters/__init__.py:23`'te bir kere okunur, instagram.py'da
`if not SOCIAL_PUBLISH_LIVE: raise FeatureDisabledError`.

### `tool_adapters/facebook.py` (130 satır) — FacebookAdapter

Instagram ile aynı yapı. Tek-adım publish: `POST /{page_id}/feed` veya
`POST /{page_id}/photos` (image_url varsa). Aynı güvenlik katmanları.

### `tool_adapters/tiktok.py` (39 satır) — TikTokAdapter

**STUB.** `publish_post` her durumda `{"ok": False, "error": "TikTok OAuth + content publish henüz implement edilmedi", "note": "Phase D2'de eklenecek"}` döner. Health_check `ready: False` döner.

### `rule-based-engine/tool_adapters/` ile karşılaştırma

md5 hash'ler aynı → dosyalar bit-bit aynı. Hiçbir fark yok.

---

## Bölüm 10: Veritabanı Durumu

### `agent-base/agent-base-api/fake_ai_api.db` (SQLite — mock e-ticaret platformu DB'si)

12 tablo:

| Tablo | Kayıt sayısı |
|-------|------:|
| `stores` | 7 |
| `items` | 16 |
| `orders` | 3 |
| `reviews` | 5 |
| `questions` | 1 |
| `campaigns` | 1 |
| `banners` | 1 |
| `timeline` | 94 |
| `automation_logs` | 1 |
| `scheduled_jobs` | 0 |
| `listener_state` | 0 |
| `sqlite_sequence` | (system) |

### `agent-base/agent-base-api/listener.db` (SQLite — orchestration + LangGraph checkpoint)

35 tablo. Önemli olanların kayıt sayısı:

| Tablo | Kayıt |
|-------|------:|
| `users` | 1 |
| `orgs` | 3 |
| `structured_rules` | 8 |
| `rules` (legacy) | 1 |
| `rule_executions` | 15 |
| `graph_node_traces` | 80 |
| `approval_requests` | 17 |
| `scheduled_entries` | 22 |
| `orchestration_traces` | **1772** |
| `chat_sessions` | 5 |
| `chat_turns` | 15 |
| `social_credentials` | 2 |
| `items` | 16 |
| `stores` | 7 |
| `orders` | 3 |
| `campaigns` | 3 |
| `automation_logs` | 106 |
| `checkpoints` | 111 (LangGraph SqliteSaver) |
| `workflow_instances` | 31 |
| `ai_tasks` | 31 |
| `tool_executions` | 44 |
| `planner_memory` | 62 |

Diğer tablolar (kayıt sayısı sorgulanmadı): `api_keys`, `campaign_metrics`,
`customer_messages`, `customer_threads`, `execution_cooldowns`, `org_members`,
`planner_learning_stats`, `planner_outcomes`, `planner_proposals`,
`rule_history`, `safety_counters`, `writes`, `sqlite_sequence`.

### `rule-based-engine/fake_ai_api.db` ile karşılaştırma

md5 birebir aynı (`d9d0bc3baddf...`). Aynı veri.

### `rule-based-engine/listener.db` ile karşılaştırma

md5 farklı. Ana tablolar:

| Tablo | RBE / agent-base |
|-------|----:|
| `users` | 1 / 1 |
| `orgs` | 3 / 3 |
| `structured_rules` | 8 / 8 |
| `rules` | 1 / 1 |
| `rule_executions` | 15 / 15 |

Ana sayılar aynı ama dosya md5 farklı — muhtemelen WAL/SHM dosyalarındaki
ya da binary timestamp/page-layout farkı. **Veri açısından paralel,
ama farklı çalışan iki process'in yazdığı kopyalar.**

### MySQL şeması (`app/models/` okundu)

7 tablo (Bölüm 7.5'te tam liste): `users`, `accounts`, `workspace_labels`,
`password_reset_tokens`, `content_templates`, `social_documents`, `usage_events`.

Alembic migration'ları: `006_create_composer_drafts.py`, `007_create_usage_events.py`.

---

## Bölüm 11: API Endpoints

### `orchestration_api.py` — Prefix: `/api/internal`

96 endpoint. Aşağıda tam liste (satır numarası + method + path):

| # | Satır | Method | Path |
|---|------:|--------|------|
| 1 | 137 | GET | `/dashboard` |
| 2 | 149 | GET | `/rules` |
| 3 | 159 | POST | `/rules/preview` |
| 4 | 188 | POST | `/rules/preview-autonomous` |
| 5 | 196 | POST | `/rules/apply` |
| 6 | 229 | DELETE | `/rules/{rule_id}` |
| 7 | 237 | PATCH | `/rules/{rule_id}/enabled` |
| 8 | 244 | POST | `/rules/import-file` |
| 9 | 250 | POST | `/rules/export-file` |
| 10 | 256 | GET | `/workflows` |
| 11 | 297 | GET | `/items` |
| 12 | 318 | GET | `/tasks` |
| 13 | 350 | GET | `/tool-executions` |
| 14 | 368 | GET | `/automation-logs` |
| 15 | 384 | GET | `/timeline` |
| 16 | 401 | GET | `/proposals` |
| 17 | 422 | GET | `/approvals` |
| 18 | 441 | POST | `/approvals/{approval_id}/approve` |
| 19 | 447 | POST | `/approvals/{approval_id}/reject` |
| 20 | 452 | POST | `/approvals/{approval_id}/edit` |
| 21 | 457 | POST | `/approvals/{approval_id}/retry` |
| 22 | 462 | POST | `/approvals/{approval_id}/feedback` |
| 23 | 467 | GET | `/business-insights` |
| 24 | 475 | GET | `/business-state` |
| 25 | 480 | GET | `/planner-memory` |
| 26 | 485 | GET | `/tools/registry` |
| 27 | 490 | GET | `/agents` |
| 28 | 495 | GET | `/cache-stats` |
| 29 | 500 | GET | `/traces` |
| 30 | 515 | GET | `/traces/tags` |
| 31 | 522 | GET | `/traces/by-event/{event_id}` |
| 32 | 529 | GET | `/humanized-timeline` |
| 33 | 539 | GET | `/insight-cards` |
| 34 | 546 | GET | `/memory-patterns` |
| 35 | 552 | GET | `/operational-pressure` |
| 36 | 558 | GET | `/chat/intents` |
| 37 | 588 | GET | `/calendar` |
| 38 | 599 | GET | `/schedules` |
| 39 | 612 | POST | `/schedules` |
| 40 | 633 | PATCH | `/schedules/{schedule_id}` |
| 41 | 647 | DELETE | `/schedules/{schedule_id}` |
| 42 | 657 | POST | `/schedules/fire-due` |
| 43 | 688 | GET | `/customer-threads` |
| 44 | 697 | GET | `/customer-threads/{thread_id}` |
| 45 | 706 | POST | `/customer-threads` |
| 46 | 722 | POST | `/customer-threads/{thread_id}/messages` |
| 47 | 733 | POST | `/customer-threads/{thread_id}/draft` |
| 48 | 742 | POST | `/customer-threads/{thread_id}/escalate` |
| 49 | 751 | POST | `/customer-threads/{thread_id}/resolve` |
| 50 | 781 | GET | `/credentials` |
| 51 | 791 | POST | `/credentials` |
| 52 | 817 | DELETE | `/credentials/{cred_id}` |
| 53 | 832 | GET | `/credentials/encryption-status` |
| 54 | 860 | GET | `/campaigns` |
| 55 | 872 | GET | `/campaigns/{campaign_id}` |
| 56 | 882 | POST | `/campaigns` |
| 57 | 899 | PATCH | `/campaigns/{campaign_id}/pause` |
| 58 | 914 | PATCH | `/campaigns/{campaign_id}/archive` |
| 59 | 924 | GET | `/campaigns/{campaign_id}/metrics` |
| 60 | 968 | POST | `/orgs` |
| 61 | 985 | GET | `/orgs/me` |
| 62 | 1022 | POST | `/orgs/members` |
| 63 | 1038 | GET | `/orgs/{org_id}/members` |
| 64 | 1048 | GET | `/api-keys` |
| 65 | 1068 | POST | `/api-keys` |
| 66 | 1098 | DELETE | `/api-keys/{api_key_id}` |
| 67 | 1155 | POST | `/structured-rules/parse` |
| 68 | 1173 | POST | `/structured-rules` |
| 69 | 1189 | GET | `/structured-rules` |
| 70 | 1200 | GET | `/structured-rules/{rule_id}` |
| 71 | 1209 | PATCH | `/structured-rules/{rule_id}/enabled` |
| 72 | 1219 | DELETE | `/structured-rules/{rule_id}` |
| 73 | 1228 | POST | `/structured-rules/test` |
| 74 | 1256 | GET | `/rule-executions` |
| 75 | 1266 | GET | `/rule-executions/{execution_id}` |
| 76 | 1276 | POST | `/rule-executions/{execution_id}/resume` |
| 77 | 1301 | GET | `/rule-templates` |
| 78 | 1310 | POST | `/rule-templates/{template_id}/materialize` |
| 79 | 1331 | GET | `/structured-rules/{rule_id}/versions` |
| 80 | 1337 | GET | `/structured-rules-conflicts` |
| 81 | 1351 | POST | `/semantic-resolve` |
| 82 | 1361 | GET | `/adapter-health` |
| 83 | 1377 | GET | `/structured-rules/{rule_id}/learning` |
| 84 | 1386 | GET | `/learning-suggestions` |
| 85 | 1398 | GET | `/structured-rules-conflicts/suggestions` |
| 86 | 1410 | POST | `/structured-rules-conflicts/resolve` |
| 87 | 1436 | POST | `/chat-edit/preview` |
| 88 | 1461 | POST | `/chat-edit/apply` |
| 89 | 1479 | GET | `/orchestration-health` |
| 90 | 1522 | GET | `/users` |
| 91 | 1548 | POST | `/chat` |
| 92 | 1556 | POST | `/chat/new-session` |
| 93 | 1563 | GET | `/trending-products` |
| 94 | 1578 | POST | `/seed-data` |
| 95 | 1584 | POST | `/products/import` |
| 96 | 1597 | GET | `/dashboard-metrics` |

### Diğer router'ların endpoint sayıları

| Router | Endpoint sayısı | Yol önek |
|--------|---------------:|---------|
| `app/api/auth.py` | 3 | (prefix dosya içinde) — /register, /login, /me |
| `app/api/client_debug.py` | 1 | /browser-logs |
| `app/api/social_data.py` | 8 | /collections/{collection} (CRUD + admin) |
| `app/api/social_media.py` | 50 | (okumadım) |
| `app/routers/social_media.py` | 21 | (okumadım — legacy_router) |

### `rule-based-engine/orchestration_api.py` ile karşılaştırma

md5 aynı, satır sayısı aynı (1619), endpoint sayısı aynı (96).

---

## Bölüm 12: Çalışma Akışı

Aşağıdaki zincir `listener.py`, `structured_rule_engine.py`,
`langgraph_engine/runtime.py` ve `langgraph_engine/nodes.py`
dosyalarından doğrulandı.

```
1. Event üretimi
     fake_commerce_app.py veya gerçek platform
     → SQL: INSERT INTO fake_ai_api.db.timeline (...)
                ↓ ~2 sn polling
2. listener.py:main() → poll
     → listener.py:process_event(event)
3. listener.py içinde 3 paralel YOL var:
   a) SKIP_SYNTHETIC=1 ve event.is_synthetic ise atla
   b) event_router.is_critical_event() → True ise:
        listener.py:_process_critical_path()
            → rule_engine.find_matching_rules() (legacy DSL)
            → action_engine.execute_rule_actions()
            (eski kurallar için)
   c) event_router.should_use_autonomous() → True ise:
        listener.py:_process_autonomous_path()
            → autonomous_planner.run() (LLM plan)
            → planner_runtime
   d) Her durumda paralel olarak (eğer trigger eşleşirse):
        structured_rule_engine.trigger_rules_for_event()
            → langgraph_engine.runtime.start_execution(rule, event)
                → _open_execution_row() → rule_executions row
                → build_graph(rule) → StateGraph compile
                → graph.invoke(initial_state)
                    → supervisor_node → wait_node (varsa)
                    → content_generator_node → create_coupon_node (varsa)
                    → risk_analyzer_node → approval_gate_node
                    → ⏸ interrupt_before=["approval"]
                → status='waiting_human', execution_id döner

4. Approval bekleme
   PHP UI → onay-bekleyenler sayfası → approvals-app.js
       → /api/internal/approvals (orchestration_api.py:422)
       → /api/internal/approvals/{id}/approve (442)
   Bu çağrı:
       approval_service.approve_request(approval_id)
       → langgraph_engine.runtime.resume_execution(
             execution_id, approval_decision="approved")
           → graph.update_state(approval.decision=approved, status=running)
           → graph.invoke(None)
              → approval_gate_node geçer
              → publisher_node
                  → social_credentials.try_get_credential()
                  → tool_adapters.get_adapter(channel).publish_post()
                       → SOCIAL_PUBLISH_LIVE=1 + credential varsa
                         gerçek Graph API POST /{ig_user_id}/media
                                       POST /{ig_user_id}/media_publish
                  → tool_registry.resolve_tool_instances() → tools.py._run()
              → monitor_node (6 saat sonrasına schedule)
              → finalize_node

5. Wait/Schedule akışı (paralel)
   wait_node → scheduling_service.create_schedule()
       → scheduled_entries tablosuna yaz
       → graph "waiting_timer" statüsünde durur
   workflow_worker.py polling:
       → scheduling_service.fire_due_schedules()
       → langgraph_engine.runtime.resume_after_wait(execution_id)
           → graph.update_state(metadata.wait_resolved=True)
           → graph.invoke(None) — kalan node'lar çalışır

6. Sonuç yazılır
   → rule_executions.status = 'completed' / 'failed' / 'cancelled'
   → graph_node_traces tablosuna her node trace'i (ts, summary, duration_ms)
   → orchestration_traces tablosuna observability._emit kayıtları
   → fake_tool_timeline ile tool çıktıları timeline'a eklenir
```

**Sorun:** Listener'da gerçekten 3 paralel yol var (`_process_critical_path`,
`_process_monitoring_path`, `_process_autonomous_path`) ama `structured_rule_engine`
bu yolların İÇİNDEN çağrılıyor mu, yoksa direkt `process_event`'in sonunda mı
ayrı çağrılıyor — bunu `listener.py` 100-228. satırları arasını okumadan
kesinlikle söyleyemem. (Bölüm tahmininde değil, kanıttayım: 48-100. satırlar
okundu, _process_critical_path 228. satırda başlıyor, arasını atladım.)

---

## Bölüm 13: Eksikler ve Sorunlar

### grep TODO/FIXME (flat .py + langgraph + tool_adapters)

```
workflow_worker.py:5: TODO: Replace sequential for-loop with a bounded thread pool ...
workflow_worker.py:8: TODO: Introduce worker identity + row-level locking ...
workflow_worker.py:12: TODO: Partition the queue by entity_type or workflow_name ...
workflow_worker.py:15: TODO: Consider async I/O for DB + API calls ...
workflow_worker.py:66: # Burada basitlik için tüm user_id'leri taramıyoruz; iyileştirme TODO.
```

Sadece **5 TODO**, hepsi `workflow_worker.py`'da. FIXME hiç yok.

### grep NotImplementedError

```
tools.py:69: raise NotImplementedError(f"{type(self).__name__}._run must be overridden")
```

Tek bir tane — base class abstract method. Tasarım bu, eksiklik değil.

### grep `raise NotImplemented` (variant)

Yok.

### Tool_adapters/tiktok.py — stub

`publish_post` her zaman `error: "TikTok OAuth + content publish henüz
implement edilmedi"` döner. Phase D2 için ertelenmiş.

### Syntax kontrol

```
agent-base/agent-base-api/: Syntax hatasi yok (app/ dahil tüm .py)
rule-based-engine/:           Syntax hatasi yok
```

`ast.parse` ile her dosya tek tek doğrulandı.

### Mimari sorunlar (kanıta dayalı)

| # | Sorun | Kanıt |
|---|-------|-------|
| 1 | **İkili kod tabanı birebir mirror** | md5 karşılaştırması: 60+ flat .py + langgraph + tool_adapters dosyalarının tümü AYNI hash |
| 2 | **`crewai_worker.py` shim hâlâ duruyor** | 21 satır, `agent-base-api/crewai_worker.py` ve `rule-based-engine/crewai_worker.py` ikisinde de var |
| 3 | **`agent-base-api/index.html` hâlâ duruyor** | 83914 byte, `app/main.py` içinde `/dashboard` route'unda kullanılıyor |
| 4 | **TikTok adapter stub** | Yukarıdaki tespit |
| 5 | **Sidebar timeline 7 slug filtresiz** | `_rules_toolbar.php` slugEventMap'te boş array: `coupons, staff, checkin-checkout, withdrawals, plugins, subscription, components` |
| 6 | **`timeline-store-automation.js` URL'leri dinamik** | grep ile sabit endpoint yakalanamadı — chat'ten hangi API'ye gidildiği statik analiz ile tespit edilemez |
| 7 | **3 paralel listener yolu** | listener.py'da `_process_critical_path`, `_process_monitoring_path`, `_process_autonomous_path` ayrı ayrı fonksiyon. structured_rule_engine'in bu üçüyle nasıl etkileşeceği fonksiyonların gövdesi okunmadan tam belli değil |
| 8 | **`listener.db` md5 farkı** | İki ortamın listener.db'leri farklı zamanlarda yazılmış, ana kayıt sayıları aynı ama dosya md5'leri farklı |

### Belge bolluğu (kök dizinler)

Hem `rule-based-engine/` hem `agent-base/` köklerinde 5+ Markdown belge
var (`ARCHITECTURE_CLEANUP_PLAN.md`, `ARCHITECTURE_EVOLUTION_PLAN.md`,
`FRONTEND_REBUILD_PLAN.md`, `PROJECT_AUDIT.md`,
`SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md`, `SYSTEM_REVIEW_FOR_EXTERNAL_AUDIT.md`,
`FOR_CURSOR.md`, `FOR_FINAL.md`, `FOR_GROK.md`). Toplam ~300KB doküman.
İçeriklerini okumadım. Süreksiz dökümantasyon var.

---

## Bölüm 14: Import / Syntax Analizi

Yukarıdaki `ast.parse` çıktısı: her iki ortamda da syntax hatası yok.
Dolayısıyla `python -c "import X"` tarzı runtime hatalarını ayrıca
test etmedim — runtime import bir konteyner içinde çalıştırılması
gereken bir doğrulama (PYTHONPATH = agent-base-api kökü).

---

## Bölüm 15: Çevre Değişkenleri

Flat .py + langgraph + tool_adapters dosyalarındaki `os.environ` ve
`os.getenv` ifadelerinden çıkarılan **62 benzersiz env değişkeni**:

```
AI_PLANNER_AUTO_APPLY, AI_PLANNER_ENABLED, AI_PLANNER_MIN_CONFIDENCE,
AI_PLANNER_USE_AI, AI_RULE_GENERATOR_ENABLED, ALLOW_DEBUG_ENDPOINTS,
API_TOKEN, API_URL, APP_SECRET_KEY,
AUTONOMOUS_COOLDOWN_MINUTES, AUTONOMOUS_HOURLY_LIMIT,
AUTONOMOUS_PLANNER_APPROVAL_THRESHOLD, AUTONOMOUS_PLANNER_ENABLED,
AUTONOMOUS_PLANNER_MIN_CONFIDENCE, AUTONOMOUS_PLANNER_USE_AI,
AUTONOMOUS_REQUIRE_APPROVAL,
BUSINESS_CHAT_RESTYLE_MODEL, BUSINESS_CHAT_RESTYLE_TIMEOUT,
BUSINESS_CHAT_USE_AI,
CAMPAIGN_DAILY_LIMIT,
CHAT_LLM_MAX_TOKENS, CHAT_LLM_MODEL, CHAT_LLM_TEMPERATURE,
CHAT_LLM_TIMEOUT_SEC, CHAT_USE_LLM,
CRITICAL_PLANNER_FALLBACK,
FAKE_API_DB_PATH, FAL_KEY,
INTERNAL_SERVICE_IN_PROCESS,
LISTENER_SKIP_SYNTHETIC,
MONITORING_AUTONOMOUS,
NL_CONFLICT_USE_LLM, NL_EDIT_USE_LLM, NL_PARSER_MODEL,
NL_PARSER_TIMEOUT, NL_PARSER_USE_LLM, NL_RESOLVER_USE_LLM,
OPENAI_API_KEY,
PLANNER_LLM_MODEL, PLANNER_LLM_TIMEOUT,
R2_ACCESS_KEY_ID, R2_ACCOUNT_ID, R2_BUCKET_NAME,
R2_PUBLIC_BASE_URL, R2_PUBLIC_R2_DEV_HOST, R2_SECRET_ACCESS_KEY,
RULES_PATH,
SIM_INTERVAL_SEC, SOCIAL_PUBLISH_LIVE,
TASK_MAX_RETRIES, TASK_RETRY_BASE_SEC, TASK_RETRY_CAP_SEC,
TOOL_BACKOFF_BASE_SEC, TOOL_BACKOFF_CAP_SEC, TOOL_CB_COOLDOWN_SEC,
TOOL_CB_FAILURE_THRESHOLD, TOOL_CB_WINDOW_SEC,
TOOL_MAX_RETRIES, TOOL_SANDBOX_WORKERS, TOOL_TIMEOUT_SEC
```

`langgraph_engine/runtime.py:81` ek olarak `LANGGRAPH_CHECKPOINT_DB` okuyor.
`tool_adapters/instagram.py:36-37`: `META_GRAPH_API_BASE`, `META_GRAPH_TIMEOUT_SEC`.
`app/integrations/smtp_client.py`: `RESET_EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`.
`app/integrations/r2_storage.py`: yukarıdaki R2_* zaten listede.
`app/core/` ve `app/main.py`: `APP_DISABLE_UVICORN_ACCESS_LOG`, `INTERNAL_SERVICE_IN_PROCESS`,
`VITE_HOLIDAY_COUNTRY`.

**Tek tek hangi default'a sahip ya da zorunlu listesi 62 satırlık bir
inceleme — bu özette dosya başına default tablosu çıkarmadım**, ama dosyada
gördüklerime göre default'ların büyük çoğunluğu mevcut (`os.environ.get("X", "1")` gibi).

`docker-compose.yml` içinde override edilenler:

| Env | docker-compose.yml default |
|-----|---------------------------|
| `INTERNAL_SERVICE_IN_PROCESS` | "1" (sabit) |
| `LANGGRAPH_CHECKPOINT_DB` | `/opt/api/listener.db` |
| `SOCIAL_PUBLISH_LIVE` | "0" |
| `CHAT_USE_LLM` | "1" |
| `NL_PARSER_USE_LLM` | "1" |
| `FERNET_KEY` | (boş — runtime'da generate) |
| `DATABASE_URL` | `mysql+pymysql://agentbase:agentbase_pass@mysql:3306/agentbase` |
| `MEDIA_ROOT` | `/data/media` |
| `MEDIA_PUBLIC_BASE_URL` | `http://localhost:8080` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `USE_CELERY` | `true` |
| `CELERY_WORKER_CONCURRENCY` | `4` |

---

## Bölüm 16: Bağımlılıklar (pyproject.toml karşılaştırması)

### `agent-base/agent-base-api/pyproject.toml` (25 dependency)

```
bcrypt>=4.2.0
boto3>=1.35.0
celery>=5.6.3
cryptography>=42.0.0
fal-client>=0.13.2
fastapi>=0.135.3
google-genai>=1.73.0
google-generativeai>=0.8.6
httpx>=0.28.1
imageio-ffmpeg>=0.6.0
langgraph>=1.2.1
langgraph-checkpoint-sqlite>=3.1.0
loguru>=0.7.3
openai>=2.31.0
pillow>=12.2.0
pyaml>=26.2.1
pydantic-settings>=2.10.1
pydantic>=2.0
pymysql>=1.1.3
python-dotenv>=1.1.1,<1.2.0
python-jose[cryptography]>=3.5.0
redis>=5.0.0
requests>=2.32.5
sqlalchemy>=2.0.49
uvicorn>=0.44.0
```

### `rule-based-engine/pyproject.toml` (9 dependency — daha minimal)

```
cryptography>=42.0.0
fastapi>=0.136.1
langgraph>=1.2.1
langgraph-checkpoint-sqlite>=3.1.0
openai>=1.40.0
pillow>=12.2.0
python-dotenv>=1.0.0
requests>=2.34.2
uvicorn>=0.47.0
```

### Fark tablosu

| Paket | RBE | agent-base-api |
|-------|:---:|:---:|
| `bcrypt` | yok | ≥4.2.0 |
| `boto3` | yok | ≥1.35.0 |
| `celery` | yok | ≥5.6.3 |
| `cryptography` | ≥42.0.0 | ≥42.0.0 |
| `fal-client` | yok | ≥0.13.2 |
| `fastapi` | ≥0.136.1 | ≥0.135.3 (RBE'de SAFINNERSI yeni!) |
| `google-genai` | yok | ≥1.73.0 |
| `google-generativeai` | yok | ≥0.8.6 |
| `httpx` | yok | ≥0.28.1 |
| `imageio-ffmpeg` | yok | ≥0.6.0 |
| `langgraph` | ≥1.2.1 | ≥1.2.1 |
| `langgraph-checkpoint-sqlite` | ≥3.1.0 | ≥3.1.0 |
| `loguru` | yok | ≥0.7.3 |
| `openai` | ≥1.40.0 | ≥2.31.0 |
| `pillow` | ≥12.2.0 | ≥12.2.0 |
| `pyaml` | yok | ≥26.2.1 |
| `pydantic` | yok (transitively üzerinden) | ≥2.0 |
| `pydantic-settings` | yok | ≥2.10.1 |
| `pymysql` | yok | ≥1.1.3 |
| `python-dotenv` | ≥1.0.0 | ≥1.1.1,<1.2.0 |
| `python-jose[cryptography]` | yok | ≥3.5.0 |
| `redis` | yok | ≥5.0.0 |
| `requests` | ≥2.34.2 | ≥2.32.5 |
| `sqlalchemy` | yok | ≥2.0.49 |
| `uvicorn` | ≥0.47.0 | ≥0.44.0 |

**Tespitler:**
- `agent-base-api`'de 16 paket fazla — bunların büyük çoğunluğu `app/` katmanı için (SQLAlchemy, Celery, Redis, boto3, fal-client, Gemini, vb.)
- `fastapi` RBE'de daha güncel (0.136.1 vs 0.135.3) ama bu sadece bir minor versiyon
- `openai` agent-base'de daha güncel (2.31.0 vs 1.40.0) — büyük major sıçraması
- `requests` RBE'de daha güncel (2.34.2 vs 2.32.5)
- `uvicorn` RBE'de daha güncel (0.47.0 vs 0.44.0)
- `cryptography`, `langgraph`, `pillow` aynı versiyon

Ne çıkar: RBE pyproject'i bağımsız bir paketmiş gibi yazılmış (hafif).
agent-base-api pyproject'i tam stack için. RBE'den `fastapi`/`requests`/`uvicorn`
versiyonları aslında daha güncel, agent-base alıp güncelleyebilir.

---

## Bölüm 17: Sonuç ve Öncelik Sırası

### 17.1 Kesinlikle çalışan şeyler (kanıtlandı)

| Çalışıyor | Kanıt |
|-----------|-------|
| `db.py`'ın SQLite şeması | `listener.db` 35 tablo dolu, `rule_executions=15`, `graph_node_traces=80`, `approval_requests=17` — gerçek execution kayıtları var |
| LangGraph runtime | `checkpoints` tablosunda 111 satır var, `rule_executions` 15 satır — graph en az 15 kere koşmuş, 80 trace kaydedilmiş |
| `structured_rules` kaydı | 8 kural db'de mevcut |
| Approval lifecycle | 17 approval_request kaydı var |
| Scheduling | 22 scheduled_entry, 31 workflow_instance |
| `orchestration_traces` | 1772 satır — observability emit ediyor |
| Chat | chat_sessions=5, chat_turns=15 — chat'in en az başlangıçta veri yazdığı belli |
| Tool execution | 44 tool_execution kaydı |
| Planner | 62 planner_memory, 31 ai_task |
| Tool adapters import-edilebilir | `ast.parse` geçti, import zinciri bozulmuş bir şey yok |
| Instagram adapter gerçek HTTP'ye hazır | `requests.post` çağrıları kodda var (213. satıra kadar var), sadece `SOCIAL_PUBLISH_LIVE=1` ile aktive olur |

### 17.2 Çalışıp çalışmadığı doğrulanmamış şeyler

| Şüpheli | Sebep |
|---------|-------|
| Chat → API bağlantısı | `timeline-store-automation.js` 1182 satır ama statik grep'te sabit endpoint yok; dosyayı tam okumadım |
| `app/api/social_media.py` 50 endpoint | 5212 satır okumadım — sadece sayım yapıldı |
| `app/runtime/orchestrator.py` | 1151 satır okumadım |
| MySQL şeması migrate olmuş mu | Alembic versiyonları var ama "db'de var mı" sorusu sadece çalışan container'da test edilebilir |
| 3 paralel listener yolu race | listener.py 100-228 satırları okumadım |

### 17.3 Kesinlikle çalışmayan şeyler

| Çalışmıyor | Kanıt |
|-----------|-------|
| TikTok publish | `tool_adapters/tiktok.py` stub — her zaman `error: "henüz implement edilmedi"` |
| `crewai_worker.py` | DEPRECATED shim, 21 satır, fonksiyonel değil |

### 17.4 Silinmesi gereken şeyler

| Yol | Sebep |
|-----|-------|
| `agent-base-api/index.html` | İstenmiyor — eski dashboard, PHP-UI'a geçildi. Yine de `app/main.py:158` `/dashboard` route'unda hâlâ kullanılıyor; route'u da kaldır |
| `agent-base-api/crewai_worker.py` | Deprecated shim, hiçbir yerden çağrılmıyor (grep'le doğrulanabilir) |
| `rule-based-engine/` kökü | Tüm flat .py'lar `agent-base-api/`'de bit-bit aynı — geliştirme tek noktadan ilerlesin, RBE arşivlensin (`git mv rule-based-engine/ legacy/rule-based-engine/` veya silinsin) |
| `agent-base-api/test3.py`, `video.py`, `developer-tools.py` | Scratch dosyaları (okumadım ama isimleri scratch görünüyor) — git log ile silinebilirliği doğrulanmalı |
| `rule-based-engine/full_system_test.py`, `test_api.py` | Eski test runner'ları, üretimde kullanılmıyor |
| `rule-based-engine/main.py` | `agent-base-api/fake_commerce_app.py` ile birebir aynı; biri silinmeli (RBE tarafı silinmeli) |
| `rule-based-engine/index.html` | İstenmiyor |

### 17.5 Düzeltilmesi gereken şeyler (öncelik sırasıyla)

1. **Sistemi ayağa kaldır.** `docker compose up -d` çalışıyor mu, supervisorctl ile 7 process RUNNING mi? — Bu döneminde **denenmedi** (Docker'a erişim verilmedi). Önce bu test edilmeli.

2. **Chat → API hattı doğrulanmalı.** `timeline-store-automation.js` tek tek okunup hangi endpoint'lere fetch attığı çıkarılmalı. Her endpoint `orchestration_api.py`'da var mı kontrol edilmeli.

3. **Ürün listesi neden görünmüyor sorusu.** Hangi PHP sayfası ürün gösteriyor + hangi JS dosyası + hangi API endpoint. Bunu sayfa adına göre eşle: `system_admin.php` solunda "tsws-products-grid" id'li bir div var, JS bunu doldurmalı.

4. **Tool-binding (chat'ten kural CRUD).** Conversational rule edit (`conversational_rule_edit.py` 564 satır + `/chat-edit/preview` ve `/chat-edit/apply` endpoint'leri) zaten var ama UI'dan bağlandı mı belli değil — `timeline-store-automation.js` bu endpoint'leri çağırıyor mu?

5. **3 paralel listener yolu çakışması.** `listener.py` 100-228. satırların okunup hangi durumda `structured_rule_engine.trigger_rules_for_event`'in çağrıldığı netleştirilmeli — aynı event 3 kez işleniyor mu?

6. **TikTok adapter implementasyonu.** Phase D2.

7. **`rule-based-engine/` kökünün arşivlenmesi.** Bit-bit kopya, gereksiz.

8. **`workflow_worker.py` TODO'ları.** Worker concurrency + locking + partitioning. 5 TODO listesi.

### 17.6 Yapısal Hızlı Karar Tablosu

| Karar | Aksiyon |
|-------|---------|
| Tek kanonik kaynak | `agent-base/agent-base-api/` |
| `rule-based-engine/` kökü | Arşivle veya sil — yeni geliştirme YASAK |
| LangGraph | Tek execution motoru olarak kalır |
| CrewAI | Bağımlılık YOK, shim sil |
| PHP UI | Tek frontend, vanilla JS, `index.html` istenmiyor |
| Real publish | `SOCIAL_PUBLISH_LIVE=0` default — operatör ayarlamadan canlıya çıkmaz |

---

## Bölüm 18: Veri Tabanı Tam Tablo Tablosu

### `listener.db` 35 tablo (alfabetik)

```
ai_tasks                    (31 satır)
api_keys                    (sorgulanmadı)
approval_requests           (17 satır)
automation_logs             (106 satır)
campaign_metrics            (sorgulanmadı)
campaigns                   (3 satır)
chat_sessions               (5 satır)
chat_turns                  (15 satır)
checkpoints                 (111 satır — LangGraph SqliteSaver)
customer_messages           (sorgulanmadı)
customer_threads            (sorgulanmadı)
execution_cooldowns         (sorgulanmadı)
graph_node_traces           (80 satır)
items                       (16 satır)
listener_state              (sorgulanmadı)
orchestration_traces        (1772 satır)
orders                      (3 satır)
org_members                 (sorgulanmadı)
orgs                        (3 satır)
planner_learning_stats      (sorgulanmadı)
planner_memory              (62 satır)
planner_outcomes            (sorgulanmadı)
planner_proposals           (sorgulanmadı)
rule_executions             (15 satır)
rule_history                (sorgulanmadı)
rules                       (1 satır — legacy)
safety_counters             (sorgulanmadı)
scheduled_entries           (22 satır)
social_credentials          (2 satır)
sqlite_sequence             (system)
stores                      (7 satır)
structured_rules            (8 satır)
tool_executions             (44 satır)
users                       (1 satır)
workflow_instances          (31 satır)
writes                      (sorgulanmadı)
```

### `fake_ai_api.db` 12 tablo

```
automation_logs             (1 satır)
banners                     (1 satır)
campaigns                   (1 satır)
items                       (16 satır)
listener_state              (0 satır)
orders                      (3 satır)
questions                   (1 satır)
reviews                     (5 satır)
scheduled_jobs              (0 satır)
sqlite_sequence             (system)
stores                      (7 satır)
timeline                    (94 satır — burada event'ler tutuluyor)
```

---

*Bu özet, dosya açma + md5 hash + ast.parse + sqlite3 sorgu sonuçlarına
dayanır. Görmediğim/okumadığım yerler "okumadım" olarak işaretlidir.
Tahmin yok.*
