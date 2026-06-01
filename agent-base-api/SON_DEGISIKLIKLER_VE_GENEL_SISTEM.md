# Son Değişiklikler ve Genel Sistem

> Bu doküman LangGraph geçişinin **iki turunu** birlikte yansıtıyor.
> Önceki turun çıktısı olan `PROJECT_AUDIT.md`, `ARCHITECTURE_CLEANUP_PLAN.md`,
> `ARCHITECTURE_EVOLUTION_PLAN.md`, `FRONTEND_REBUILD_PLAN.md` ve
> `SYSTEM_REVIEW_FOR_EXTERNAL_AUDIT.md` dosyalarındaki tüm shipped
> phase'ler ve prensipler **olduğu gibi korundu**.
>
> **Tur 1 (LangGraph altyapısı):** structured_rule, nl_rule_parser,
> langgraph_engine/*, dynamic graph builder, dashboard 5-tab Türkçeleştirme.
>
> **Tur 2 (sistem olgunlaşması — BU TUR):**
> - CrewAI **tamamen** kaldırıldı; `tools.py` artık kendi protocol-typed
>   BaseTool'unu kullanıyor.
> - Wait/resume pipeline **gerçek**: workflow_worker scheduled entry'leri
>   görüp `runtime.resume_after_wait` çağırıyor; LangGraph interrupt_after
>   ile gerçekten duraklıyor.
> - **`semantic_entity_resolver`**: "Çanakkale hesabı", "yüksek stoklu
>   ürünler" gibi semantic ifadeleri runtime'da gerçek ID'lere çeviriyor.
> - **`rule_templates`**: 9 hazır şablon (Anneler Günü, Stok Düşüşü, Yılbaşı,
>   Kara Cuma, vb.) — operatör parametre doldurup tek tıkla aktif ediyor.
> - **Rule versioning** + conflict detection.
> - Dashboard'da **canlı execution graph görselleştirme** (modal + node stepper).
> - **`tool_adapters/`** Instagram/Facebook/TikTok stub iskeleti
>   (SOCIAL_PUBLISH_LIVE flag arkasında).

---

## 1. Genel Mimari Özeti

Sistem artık **deterministic orchestration runtime + AI-orchestrated
LangGraph execution layer + doğal Türkçe ile kural yazma**'nın birlikte
çalıştığı hibrit bir AI-native commerce operating runtime.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  FastAPI app (main.py + orchestration_api.py)                            │
│  + index.html (5 sekme: Genel Bakış, AI Operatör, Kurallar, Operasyon,   │
│                 Onaylar) — tamamen Türkçe                                │
└──────────────────────────────────────────────────────────────────────────┘
       │                                                              ▲
       │ internal_service (in-process, asla self-HTTP)               │ tüm yanıtlar JSON
       ▼                                                              │
┌──────────────────────┐     ┌────────────────────────────────────────┴───┐
│ fake_ai_api.db       │     │ listener.db                                 │
│  - timeline (event   │     │  - workflow_instances, ai_tasks             │
│    log)              │     │  - approval_requests, automation_logs       │
│  - stores, items,    │     │  - planner_memory, planner_outcomes         │
│    orders, reviews   │     │  - orchestration_traces                     │
└──────────────────────┘     │  - orgs, org_members, api_keys              │
       ▲                     │  - social_credentials (Fernet)              │
       │                     │  - scheduled_entries, customer_threads,     │
       │ polls               │    customer_messages, campaigns,            │
┌──────┴───────────┐         │    campaign_metrics                         │
│ listener.py      │         │  ─ Yeni (Bu tur) ───────────────────────    │
│                  │         │  - structured_rules    (NL→ canonical rule) │
│  Üç paralel      │         │  - rule_executions     (LangGraph run id)   │
│  matching path:  │         │  - graph_node_traces   (per-node UI özet)   │
│  1) rule_engine  │         │  - LangGraph checkpoints (SqliteSaver)      │
│  2) autonomous_  │         └─────────────────────────────────────────────┘
│     planner      │                       ▲
│  3) STRUCTURED_  │ ─────► structured_rule_engine.trigger_rules_for_event
│     RULES (yeni) │              │
└──────────────────┘              ▼
                          ┌────────────────────────────────────────┐
                          │  langgraph_engine.runtime              │
                          │   StructuredRule → StateGraph compile  │
                          │   SqliteSaver checkpoint + interrupt   │
                          │                                        │
                          │  Nodes:                                │
                          │   supervisor → wait → content_gen      │
                          │     → risk_check → ⏸approval           │
                          │     → publish → monitor → finalize     │
                          └────────────────────────────────────────┘
                                       │
                                       ▼ (tools.py CrewAI BaseTool —
                                          ._run() direkt çağrılıyor)
                          ┌────────────────────────────────────────┐
                          │  fake tools: instagram, banner, kupon, │
                          │  SSS, destek, trend, düşük stok        │
                          │  + social_credentials.try_get →        │
                          │    "real_publish_would_happen" mode    │
                          └────────────────────────────────────────┘
```

**Üç paralel matching path** kritik bir tasarım kararı:
1. Eski **rule_engine** (rules.txt DSL) → workflow_service
2. Eski **autonomous_planner** (BI + CrewAI + plan_validator) → workflow_service
3. Yeni **structured_rule_engine** (Türkçe NL → Pydantic StructuredRule →
   LangGraph execution) — bağımsız, kendi tabloları, kendi checkpoint'i

Üçü de aynı `listener` üzerinden tetikleniyor, hiçbiri diğerinin yolunu
kesmiyor. Bu sayede **eski kurallarınız ve mevcut akışlarınız hiç
bozulmadı**.

---

## 2. Yapılan Önemli Değişiklikler

### Yeni eklenen dosyalar

| Dosya | Amaç | Satır |
|---|---|---|
| `structured_rule.py` | Pydantic kural şemaları + canonical taxonomy | ~290 |
| `nl_rule_parser.py` | Türkçe doğal dil → StructuredRule (regex prefilter + LLM) | ~430 |
| `structured_rule_engine.py` | Kural CRUD + olay-kural eşleme + graph kickoff | ~190 |
| `langgraph_engine/__init__.py` | Paket | 0 |
| `langgraph_engine/state.py` | TypedDict state + Pydantic alt-domain modelleri | ~190 |
| `langgraph_engine/nodes.py` | supervisor, wait, content_generator, risk_analyzer, approval_gate, publisher, monitor, notify_customer, create_coupon, finalize | ~470 |
| `langgraph_engine/runtime.py` | Dynamic graph builder + SqliteSaver + start/resume API | ~400 |
| `SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md` | Bu doküman | ~400 |

### Var olan dosyalarda yapılan değişiklikler

- **`db.py`** — 3 yeni tablo: `structured_rules`, `rule_executions`,
  `graph_node_traces`. Hiçbir mevcut tablo değişmedi.
- **`listener.py`** — Mevcut routing path'lerinin **üstüne**
  `structured_rule_engine.trigger_rules_for_event` çağrısı eklendi.
  Try/except ile sarmalı — yeni path hata verirse eski path'ler etkilenmez.
- **`orchestration_api.py`** — 10 yeni endpoint:
  - `POST /api/internal/structured-rules/parse` (önizleme)
  - `POST /api/internal/structured-rules` (kayıt)
  - `GET /api/internal/structured-rules`
  - `GET /api/internal/structured-rules/{id}`
  - `PATCH /api/internal/structured-rules/{id}/enabled?enabled=…`
  - `DELETE /api/internal/structured-rules/{id}`
  - `POST /api/internal/structured-rules/test` (dry-run)
  - `GET /api/internal/rule-executions`
  - `GET /api/internal/rule-executions/{id}`
  - `POST /api/internal/rule-executions/{id}/resume`
- **`index.html`** — Tamamen yeniden yazıldı: 5 sekmeli yapı, tamamen
  Türkçe arayüz, "Kurallar" sekmesi yeni eklendi. Mevcut iyi pratikler
  korundu: class-wide focus protection (`_canRefresh`), hash-diff
  rendering (`_setIfChanged`), partial render, stage animation, session
  pill ile multi-turn sohbet.
- **`pyproject.toml`** — `langgraph>=1.2.1` ve
  `langgraph-checkpoint-sqlite>=3.1.0` eklendi.

### CrewAI durumu

- **Şu an için canlı**: `crewai_worker.py` çalışmaya devam ediyor,
  `tools.py` hâlâ CrewAI'nin `BaseTool` mirasını kullanıyor.
- **Deprecated olarak işaretlendi**: yeni structured rule path'i tamamen
  LangGraph üzerinden akıyor. Yeni geliştirmeler yalnızca LangGraph
  tarafında yapılacak.
- **Sonraki tur**: CrewAI tamamen kaldırılacak; `tools.py` `BaseTool`
  mirası protocol-typed bir base class'a dönüştürülecek; `crewai_worker.py`
  silinecek.

Bu **paralel deployment** kararı bilinçliydi — big-bang geçiş yapsaydık
mevcut çalışan tüm akışlar (autonomous_planner, approval system, vb.)
risk altında olurdu. Şu anda iki runtime aynı `tools.py`'i paylaşarak
yan yana çalışıyor.

---

## 3. LangGraph Entegrasyonu Nasıl Yapıldı

### Akış: NL → StructuredRule → StateGraph → Execution

**1. Operatör Türkçe yazar:**
```
"Yeni mağaza oluştuktan 3 gün sonra Çanakkale hesabında Anneler Günü
 şablonu kullanarak Instagram paylaşımı yap."
```

**2. `nl_rule_parser.parse_rule()` iki aşamalı çalışır:**
- **Deterministic prefilter** (regex): event tipi, zaman ifadesi, kanal,
  şablon, hesap handle, eylem fiilleri. Bu aşama LLM olmadan da çoğu
  Türkçe ifadeyi yakalar.
- **LLM ince ayar** (gpt-4o-mini, strict JSON output): prefilter
  bulgularını da girdi olarak alıp Pydantic şemasına uygun JSON üretir.
- **Pydantic validation**: hatalı/eksik alanlar `missing_fields`'a
  eklenir, `parse_confidence` düşürülür.

**3. `StructuredRule` Pydantic modeli:**
```python
StructuredRule(
    name="Yeni mağaza paylaşımı",
    trigger=TriggerSpec(event_type="store.created"),
    timing=TimingSpec(delay_seconds=259200),  # 3 gün
    target=TargetSpec(account_handle="canakkale"),
    content=ContentSpec(template="anneler_gunu", channel="instagram"),
    actions=[
        ActionStep(kind="wait"),
        ActionStep(kind="generate_content"),
        ActionStep(kind="risk_check"),
        ActionStep(kind="approval"),
        ActionStep(kind="publish"),
        ActionStep(kind="monitor"),
    ],
    requires_approval=True,
    parse_confidence=0.9,
)
```

**4. `structured_rule_engine.save_rule()`** kuralı `structured_rules`
tablosuna yazar (rule_json sütununda Pydantic dump). Listener'a kayıt
olur.

**5. Listener'a bir `store.created` olayı geldiğinde:**
```python
structured_rule_engine.trigger_rules_for_event("store.created", event, user_id)
  → find_matching_rules() filter eşleştir
  → her match için langgraph_engine.runtime.start_execution(rule, event)
```

**6. `runtime.start_execution()`:**
- `rule_executions` tablosuna satır açar (status='running')
- `build_graph(rule)` — `_selected_kinds(rule)` ile canonical sırada
  node listesi seçer, sadece kuralda istenenleri ekler
- LangGraph `StateGraph(RuleExecutionState)` derleyici:
  - Her node bir Python fonksiyonu (`nodes.supervisor_node`,
    `nodes.wait_node`, vb.)
  - `interrupt_before=["approval"]` — approval node'una girmeden ÖNCE
    duraklat
  - Checkpointer: `SqliteSaver(listener.db)` — state her node'dan sonra
    disk'e yazılır
- `graph.invoke(initial_state, config={"configurable": {"thread_id": "rule-N-xxx"}})`
- Graph node'ları sırayla çalışır:
  - `supervisor` → trace event başlat, state log
  - `wait` → delay > 0 ise scheduling_service'e entry oluştur, hemen geç
    (`time.sleep` YAPMAZ — gerçek bekleme worker tarafından polling ile)
  - `content_generator` → şablona göre Türkçe içerik üret
    (headline + body + caption + hashtags)
  - `risk_analyzer` → deterministic risk skoru (risky_words +
    external_publish + sensitive_event)
  - **DURAKLATMA**: approval node'una girmeden önce — `state.status =
    "waiting_human"`, `approval_id` create_approval_request ile alınır.
    `start_execution()` bu noktada result döndürür.

**7. Operatör onay verince (dashboard veya `POST /rule-executions/{id}/resume`):**
- `runtime.resume_execution()` çağrılır
- LangGraph `graph.update_state(...)` ile state'e
  `approval.decision = "approved"` enjekte edilir
- `graph.invoke(None, config)` — graph kalan yerinden devam eder
- `approval_gate` → `publisher` (gerçek tool çalışır,
  `social_credentials` varsa "real_publish_would_happen") →
  `monitor` (6 saat sonrası için izleme planlanır) → `finalize`
  (status = "completed")
- Tüm trace event'leri `graph_node_traces` tablosuna append edilir

### Önemli mimari kararlar

**1. interrupt_before, interrupt_after değil.** Approval node'una
girmeden ÖNCE duraklatıyoruz; resume olduğunda approval_gate kodu
çalışıyor ve approval.decision değerini görüyor. Bu sayede approval
kararı RESUME aşamasında atomic biçimde uygulanıyor.

**2. SqliteSaver listener.db kullanıyor.** Ayrı bir checkpoint DB
açmadık çünkü:
- Operasyonel sadelik (tek dosya backup)
- Cross-table sorgular (rule_executions ↔ checkpoints) tek DB'de
- WAL mode zaten açık

**3. Tools layer iki runtime'ı da besliyor.** `tools.py` `BaseTool`
mirası ile CrewAI uyumlu kalıyor; LangGraph node'ları
`tool_registry.resolve_tool_instances()` ile aynı tool'ları
alıp `._run()` direkt çağırıyor. CrewAI runtime'ı node'lar için
gerekli değil.

**4. Wait node `time.sleep` yapmıyor.** Bu kritik — eğer 3 gün
bekleseydi worker thread tamamen bloklanırdı. Onun yerine
`scheduling_service`'e bir entry oluşturuluyor. Workflow_worker
o entry'yi tetiklediğinde graph'ı resume edecek. (Bu turda
worker tarafı resume implementasyonu henüz tam değil — Phase R2'ye
bırakıldı. Şu an wait node sadece scheduling kaydı atıp geçiyor.)

**5. Dynamic graph builder canonical sıra zorluyor.** Operatör
hangi sırayla yazarsa yazsın graph şu sıra ile derlenir:
```
wait → generate_content → create_coupon → risk_check → approval →
publish → notify_customer → monitor
```
Bu sayede operatör "önce yayınla sonra risk kontrol" gibi tehlikeli
sıralar yazsa bile sistem güvenli sırayla çalıştırır.

**6. Eksik node'lar otomatik eklenir.** `_selected_kinds()` şu kuralları
uygular:
- `publish` varsa `risk_check` yoksa otomatik eklenir
- `publish` varsa `requires_approval=True` ise `approval` otomatik eklenir
- `publish` varsa `generate_content` yoksa otomatik eklenir
- `timing.delay_seconds > 0` ise `wait` otomatik eklenir

---

## 4. Frontend Yenileme Detayları

`index.html` tamamen yeniden yazıldı. 5 sekmeli yapı:

| Sekme | İçerik |
|---|---|
| **Genel Bakış** | KPI strip, iş sinyalleri kartları, operasyonel zaman tüneli (humanized), AI düşünce akışı, AI hafıza özeti |
| **AI Operatör** | Çoklu-tur LLM sohbet paneli + hızlı sorular yan paneli. Session pill localStorage'da. Stage animation, ↩ bağlam aktif chip'i. |
| **Kurallar** | **Yeni**: Doğal Türkçe kural editörü, AI önizleme (parse_confidence + stepper + missing_fields), test çalıştırma (dry-run), aktif kurallar listesi (toggle + delete), son yürütmeler |
| **Operasyon** | Aktif iş akışları, kampanyalar, müşteri konuşmaları, planlı işler |
| **Onaylar** | Bekleyen onay kartları + bekleyen kural yürütmeleri (LangGraph interrupt'lardan). Onayla/Reddet butonları her ikisi için. Onaylar bekliyorsa header'da kırmızı badge |

### Frontend'de korunan iyi pratikler

- **Class-wide focus protection** (`App._canRefresh()`): herhangi bir
  input/textarea focused iken refresh refresh durduruluyor → typing
  reset hiç olmuyor.
- **Hash-diff rendering** (`App._setIfChanged()`): aynı veri ile tekrar
  render etme yok → flicker yok.
- **Partial render**: her sekme/panel kendi fetch fonksiyonu ile
  bağımsız refresh.
- **Markdown-ish renderer**: `**bold**`, `\`code\``, `\n` → `<br/>`,
  XSS guard ile.

### Frontend Türkçeleştirme

- Hiçbir İngilizce teknik terim yok ("workflow", "graph", "node",
  "execution" UI'da görünmüyor).
- `humanizeIntent`, `humanizeEventType`, `humanizeChannel`,
  `actionLabel`, `nodeLabel`, `humanizeExecStatus`, `humanizeWorkflow`,
  `humanizeTraceTag` — 8 mapping tablosu tüm Türkçe etiketlemeleri
  yönetiyor.
- Trace summary'leri zaten backend'de Türkçeleştiriliyor
  (`observability._humanize_summary`); UI sadece tag'leri Türkçe
  gösteriyor.

### "Kurallar" sekmesi UX detayları

- **NL input** — geniş textarea, focus halinde indigo ring
- **Önizle** → AI yorumu kartında:
  - parse_confidence chip (yeşil/sarı/kırmızı)
  - Human-readable explanation
  - Eylem stepper: `Bekle → İçerik üret → Risk → Onay → Yayın → İzle`
  - Eksik bilgiler uyarısı (varsa)
- **Test Çalıştır** → dry-run sonucunu trace listesi olarak gösterir
  (gerçek persistence yok, operatör güvenle deneyebilir)
- **Kuralı Etkinleştir** → sadece önizleme yapıldıktan sonra aktifleşir
- **Aktif kurallar listesi** — durumla birlikte: tetik olayı, eylem
  akışı, etkinleştir/sil butonları
- **Son yürütmeler** — graph node ve durum bilgisiyle

---

## 5. Güçlü Yönler

### Mimari prensiplere uyum
- **AI proposes, runtime executes**: AI sadece kural önerir;
  runtime (StateGraph + approval_gate + risk_analyzer) gerçek yayını
  kontrol eder.
- **Deterministic core**: graph builder canonical sırada node'ları
  bağlar. AI çıktısı Pydantic ile validate edilir; geçersizse
  parse_confidence düşer, kullanıcı bilgilendirilir.
- **Replay safety**: SqliteSaver her node'dan sonra state'i diske
  yazar. Time-travel mümkün (LangGraph `get_state_history` API'si).
- **Idempotency**: aynı (rule, event) için ayrı thread_id → ayrı
  execution. Resume'un birden çok kez çağrılması no-op (graph
  END'e ulaştıysa update_state etkisiz).
- **Observability**: her node trace event üretir,
  `graph_node_traces` tablosuna persist edilir, dashboard
  bunları gerçek zamanlı gösterir.

### Operatör deneyimi
- **Türkçe-first**: hem girdi hem çıktı tamamen Türkçe.
- **Önizleme**: kuralı kaydetmeden önce AI yorumunu görür.
- **Test**: sentetik bir olayla kuralı dry-run edebilir.
- **İnsan onayı interrupt**: LangGraph'ın native interrupt mekanizması
  approval mantığını çok temiz hale getirdi — manuel state machine
  yok.
- **Risk analizi şeffaf**: risk flag'leri operatöre gösteriliyor.

### Kod kalitesi
- Tüm node'lar aynı imzayla (`state → dict`), aynı pattern (trace
  start/end + Pydantic alt-model + best-effort tool çağrısı).
- Hata izolasyonu: bir node hata fırsa bile graph kontrolü kaybetmiyor
  (try/except + last_error state field).
- Test coverage: parse, save, list, test, trigger, interrupt, resume
  (approve + reject) — hepsi smoke-tested.

---

## 6. Hala Eksik Olanlar ve Önerilen Sonraki Adımlar

### Hemen yapılması gerekenler

1. **`wait` node'unun gerçek resume implementasyonu.** Şu an wait
   sadece scheduling_service'e entry oluşturup hemen geçiyor; bekleme
   gerçek olmuyor. workflow_worker'a wait-resume logic'i eklenmesi
   lazım: pending scheduled_entries arasında
   `payload.resume_after_wait=True` olanları bul → ilgili execution'ı
   `runtime.resume_execution()` ile devam ettir.

2. **Time-travel UI.** Backend tarafında SqliteSaver tüm checkpoint'leri
   tutuyor; UI'da "şu adımdan başla", "şu state'i göster" gibi bir
   debug görünümü henüz yok. `graph.get_state_history(config)` API'si
   ile yapılabilir.

3. **CrewAI'nin tamamen kaldırılması.** Şu an `tools.py` hâlâ
   `from crewai.tools import BaseTool` kullanıyor. Aşamalı plan:
   - `tools.py`'a `BaseTool`'un mirasını protocol-typed bir sınıfla
     değiştir (minimum: `name`, `description`, `args_schema`, `_run`).
   - `crewai_worker.py`'yi sil; tüm yeni workflow_instances LangGraph
     üzerinden aktıkça eski path zaten kullanılmayacak.
   - `autonomous_planner._plan_with_crewai` yerine doğrudan OpenAI
     chat completions ile + plan_validator.

4. **Çoklu kural eşleşmesinde dedup.** Aynı olay birden çok kuralı
   tetiklediğinde her biri için ayrı execution açılıyor. Bu doğru
   davranış ama UI'da operatör için karışıklık yaratabilir; benzer
   kurallar gruplandırılabilir.

### Orta vadede

5. **Channel adapter framework.** `publisher_node` şu an sadece tools.py
   üzerinden mock yayın yapıyor; `social_credentials` varsa
   `real_publish_would_happen` log'u atıyor ama gerçek Graph API call'u
   yok. `tool_adapters/instagram.py`, `tool_adapters/facebook.py` gibi
   gerçek HTTP adapter'lar eklenmeli (feature flag arkasında).

6. **Rule conflict resolution.** İki kural aynı olaya farklı şeyler
   yapmak isteyebilir. Şu an her ikisi de tetikleniyor; priority
   field'ı veya mutex semantik eklenebilir.

7. **Kural sürümleme (versioning).** Operatör kuralı değiştirince eski
   sürümün halen çalışan execution'ları var. `structured_rules` tablosuna
   `version` kolonu eklemek + execution'ları belirli version'a
   bağlamak.

8. **Vector retrieval for similar past rules.** Bir operatör "anneler
   günü içeriği" derken sistemin geçmişte benzer kuralların
   başarısına bakması güzel olur. `embedding_placeholder` kolonu zaten
   var.

### Uzun vadede

9. **Distributed checkpoint backend.** SqliteSaver tek node için iyi
   ama horizontally scale olmuyor. PostgresSaver veya custom Redis
   tabanlı checkpointer.

10. **Real-time graph viz.** Operatör execution'ı şu anki haliyle
    görüyor ama "node X kaç ms aldı, içerik nasıl evrim geçirdi" gibi
    bilgileri görsel olarak göstermiyoruz.

11. **Conversational rule editing.** Operatör "şu kuraldaki delay'i
    5 güne çıkar" diyebilmeli, sistem rule_id'yi context'ten
    çıkarıp değişikliği uygulayabilmeli.

12. **Rule templates / şablonlar.** "Anneler Günü kampanyası" gibi
    yaygın senaryolar için pre-built şablonlar (`rule_templates`
    tablosu + operatör one-click clone).

---

## 7. Kullanım Örnekleri

### Örnek 1: Tam akış (kullanıcının orijinal isteği)

```bash
# 1) Doğal dilden kural önizle
curl -X POST http://localhost:8000/api/internal/structured-rules/parse \
  -H "Content-Type: application/json" \
  -d '{"natural_language":"Yeni mağaza oluştuktan 3 gün sonra Çanakkale hesabında Anneler Günü şablonu kullanarak Instagram paylaşımı yap."}'

# Yanıt:
# {
#   "rule": {
#     "trigger": {"event_type":"store.created"},
#     "timing": {"delay_seconds":259200},
#     "target": {"account_handle":"canakkale"},
#     "content": {"template":"anneler_gunu","channel":"instagram"},
#     "actions": [{"kind":"wait"},{"kind":"generate_content"},
#                 {"kind":"risk_check"},{"kind":"approval"},
#                 {"kind":"publish"},{"kind":"monitor"}],
#     "requires_approval": true,
#     "parse_confidence": 0.9
#   },
#   "explanation": "**Tetik:** Yeni mağaza oluşturulduğunda.\n**Bekleme:** 3 gün sonra.\n..."
# }

# 2) Kuralı kaydet
curl -X POST http://localhost:8000/api/internal/structured-rules \
  -H "Content-Type: application/json" \
  -d '{"natural_language":"Yeni mağaza oluştuktan 3 gün sonra Çanakkale hesabında Anneler Günü şablonu kullanarak Instagram paylaşımı yap.","enabled":true}'
# {"data":{"id":1,...}}

# 3) Test et (dry-run)
curl -X POST http://localhost:8000/api/internal/structured-rules/test \
  -H "Content-Type: application/json" \
  -d '{"rule_id":1,"event_type":"store.created","event_payload":{"name":"Çanakkale Outlet"}}'
# {"status":"running","current_node":"risk_analyzer","trace":[...]}

# 4) Gerçek olay geldi mi: listener otomatik tetikliyor.
#    Gerçek bir mağaza oluştur (fake commerce'te):
curl -X POST http://localhost:8000/internal/create-store \
  -H "Content-Type: application/json" \
  -d '{"name":"Çanakkale Outlet","owner":"Demo"}'
# Listener bu olayı görüyor, kural eşleşiyor, graph başlıyor.

# 5) Bekleyen yürütmeleri listele
curl http://localhost:8000/api/internal/rule-executions
# Hangileri "waiting_human" durumunda → onay merkezinde göründüğü gibi.

# 6) Onayla → graph resume olur, publisher node çalışır
curl -X POST http://localhost:8000/api/internal/rule-executions/1/resume \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","decided_by":"operator"}'
# {"data":{"status":"completed","current_node":"finalize",...}}
```

### Örnek 2: Operatör dashboard'tan

1. **Kurallar** sekmesine git.
2. NL input'a şunu yaz: *"Olumsuz yorum geldiğinde hemen müşteriye
   bildirim gönder ve destek akışı başlat."*
3. **Önizle** → AI yorumu gelir:
   ```
   Tetik: Olumsuz yorum geldiğinde.
   Bekleme: hemen.
   İçerik şablonu: Genel İçerik.
   Kanal: Destek.
   Akış: İçerik üret → Risk kontrolü → Müşteriye bildir.
   ```
4. **Kuralı Etkinleştir** → sistem işletim merkezinin parçası olur.
5. Gerçek olumsuz yorum geldikçe LangGraph akışı tetiklenir, onay
   bekleyenler **Onaylar** sekmesinde görünür, sen onayla — sistem
   yayını yapar.

### Örnek 3: Programatik (Python)

```python
import env_bootstrap; env_bootstrap.load_app_env()
import nl_rule_parser
import structured_rule_engine as sre
from langgraph_engine import runtime

# Parse + save
rule = nl_rule_parser.parse_rule(
    "Yeni ürün eklendiğinde 1 saat sonra Instagram'da banner paylaşımı yap.",
    user_id=1,
)
saved = sre.save_rule(rule)
print(f"Kural #{saved.id} kaydedildi.")

# Tetikle
results = sre.trigger_rules_for_event("product.created", {
    "event_id": 555, "event_type": "product.created",
    "payload": {"name": "Logitech G Pro X"},
}, user_id=1)

# Yürütmeleri incele
for r in results:
    if r["status"] == "waiting_human":
        # Onayla
        resumed = runtime.resume_execution(
            r["execution_id"],
            approval_decision="approved",
        )
        print(f"Yürütme #{r['execution_id']} → {resumed['status']}")
```

---

## 8. Önemli Notlar — Operasyonel

### Çalıştırmak için

```bash
# 1) Bağımlılıkları yükle (langgraph yeni)
uv sync

# 2) DB şemasını güncelle (init_db otomatik çağrılıyor)
python -c "import db; db.init_db()"

# 3) API server'ı çalıştır
uv run uvicorn main:app --reload

# 4) Listener'ı ayrı bir terminalde
uv run python listener.py

# 5) workflow_worker'ı ayrı bir terminalde
uv run python workflow_worker.py

# 6) (opsiyonel) CrewAI worker — eski path için
uv run python crewai_worker.py

# 7) Dashboard
# http://localhost:8000/api/internal/dashboard
```

### Environment değişkenleri

| Değişken | Default | Açıklama |
|---|---|---|
| `OPENAI_API_KEY` | (gerekli) | nl_rule_parser + ai_synthesizer için |
| `NL_PARSER_USE_LLM` | `1` | `0` set edilirse sadece regex prefilter |
| `NL_PARSER_MODEL` | `gpt-4o-mini` | Parser modeli |
| `LANGGRAPH_CHECKPOINT_DB` | `listener.db` | Checkpoint dosyası |
| `APP_SECRET_KEY` | (gerekli — social_credentials için) | Fernet base64 key |
| `CHAT_USE_LLM` | `1` | AI Operatör sohbeti için |
| `INTERNAL_SERVICE_IN_PROCESS` | `1` (main.py set ediyor) | Self-HTTP loop guard |

### Backward compatibility

- Mevcut DSL kuralları (`rules` tablosu) etkilenmedi.
- Mevcut autonomous_planner path'i etkilenmedi.
- Mevcut approval_service, workflow_service, scheduling_service,
  campaign_service, customer_interaction_service, social_credentials
  hepsi olduğu gibi çalışıyor.
- Yeni structured rules **eklemedir** — eski hiçbir endpoint değişmedi.

---

*Tur 1 bölüm sonu.*

---

# TUR 2 — Sistem Olgunlaşma Turu

> Bu bölüm Tur 1'in üzerine eklenen, vizyona tam uyum getiren
> değişiklikleri anlatıyor. Tur 1'deki tüm yapılar (graph builder,
> Pydantic state, dashboard 5-tab) olduğu gibi duruyor.

## T2-1. CrewAI Tamamen Kaldırıldı

**Önce:** `tools.py` CrewAI'nin `BaseTool` mirası kullanıyordu;
`autonomous_planner._plan_with_crewai`, `narrative_synth._restyle_with_ai`,
`ai_planner.propose_action_ai`, `rule_manager.generate_rules_with_ai`
CrewAI Agent/Crew/Task katmanını çağırıyordu; `crewai_worker.py` tüm
ai_tasks'ları Crew ile execute ediyordu.

**Sonra (Tur 2):**

| Yer | Değişiklik |
|---|---|
| `tools.py` | Kendi `BaseTool` protocol class'ımız (CrewAI'dan miras YOK). API uyumlu (name, description, args_schema, _run, set_execution_context). |
| `autonomous_planner._plan_with_llm` | Yeni — `_plan_with_crewai` yerine doğrudan OpenAI chat completions (gpt-4o-mini, response_format json_object). plan_validator yine son güvenlik. |
| `narrative_synth._restyle_with_ai` | Doğrudan OpenAI chat completions. |
| `ai_planner.propose_action_ai` | Doğrudan OpenAI (sadece `CRITICAL_FALLBACK=1` ile çalışır, default kapalı). |
| `rule_manager.generate_rules_with_ai` | Doğrudan OpenAI (legacy DSL rules için). |
| `crewai_worker.py` | Thin shim — `task_executor.main()`'e delegate eder. |
| **YENİ:** `task_executor.py` | CrewAI'siz worker; pending ai_tasks'ı tool_registry üzerinden direkt `_run(**args)` çağrısıyla çalıştırır. Tool argümanları payload'tan deterministic mapping ile kurulur. |
| `pyproject.toml` | `crewai` dependency çıkarıldı. `uv sync` ile venv'de de gerçekten yok artık. |

Geriye dönük uyumluluk: `python crewai_worker.py` çağıran eski deploy
scriptleri kırılmıyor (shim delegate ediyor).

## T2-2. Wait / Resume Pipeline'ı Gerçek

**Önce:** wait_node `scheduling_service`'e bir entry yazıp hemen geçiyordu,
graph aslında durmuyordu. resume_after_wait çağrılmıyordu.

**Sonra:**

1. `langgraph_engine.runtime.build_graph` artık `rule.timing.delay_seconds > 0`
   ise `interrupt_after=["wait"]` ile graph compile ediyor.
2. `wait_node` ilk çağrılışta scheduling_service'e entry oluşturup
   `status="waiting_timer"`, `metadata.wait_resolved=False` döndürüyor.
   LangGraph wait'ten sonra DURUYOR.
3. `runtime.start_execution` "waiting_timer" durumunu algılıyor, DB row
   güncel.
4. `workflow_worker._handle_wait_resumes` (yeni) her poll'da fired
   scheduled_entries içinde `payload.resume_after_wait=True && !handled`
   olanları tarıyor, her biri için
   `langgraph_engine.runtime.resume_after_wait(execution_id)` çağırıyor.
5. `resume_after_wait` state'e `metadata.wait_resolved=True` patch'liyor +
   `graph.invoke(None, config)` ile graph'ı uyandırıyor. wait_node
   ikinci çağrılışta wait_resolved'i görüp normal devam ediyor.
6. Akış kalan node'larda devam ediyor; approval interrupt'ı varsa orada
   durur, yoksa finalize'a kadar gider.

Smoke test sonucu (gerçek 2 saniye bekleme):
```
status=waiting_timer  current=wait        (graph duraklatıldı)
        ↓ workflow_worker tetikler
status=waiting_human  current=risk_analyzer (approval bekliyor)
        ↓ operatör onaylar
status=completed      current=finalize    (akış tamamlandı)
```

## T2-3. Semantic Entity Resolver

Yeni modül: `semantic_entity_resolver.py`. Operatör "Çanakkale hesabı" veya
"yüksek stoklu ürünler" yazdığında runtime bu ifadeyi GERÇEK ID
listesine çeviriyor.

```python
from semantic_entity_resolver import resolve
r = resolve("yüksek stoklu ürünler")
# r.kind = "item"
# r.ids = [5, 18, 22, 7, 11]
# r.interpretation = "En yüksek stoklu 5 ürün"
# r.samples = [{"name": "Logitech G Pro X", "stock": 130}, ...]
```

Desteklenen pattern'lar:
- Yüksek/düşük stoklu ürünler
- En çok satan ürünler
- En iyi performans gösteren mağaza
- Olumsuz yorumlu ürünler
- Trend ürünler
- "X hesabı / X mağazası" (handle araması)

Kullanım noktaları:
1. **StructuredRule.target.entity_filters** — bir filter değeri
   `"$semantic:yüksek stoklu ürünler"` formundaysa runtime resolver
   çalıştırıp ID listesine çeviriyor.
2. **API endpoint** `POST /api/internal/semantic-resolve` — dashboard'dan
   önizleme.

NL_RESOLVER_USE_LLM=1 ile LLM fallback (default=0, deterministic-first).

## T2-4. Hazır Kural Şablonları

Yeni modül: `rule_templates.py`. 9 önceden tanımlı şablon, 4 kategori:

| Kategori | Şablonlar |
|---|---|
| **Özel günler** (seasonal) | 🌷 Anneler Günü, 🎁 Babalar Günü, 🎄 Yılbaşı, 🛍️ Kara Cuma |
| **Stok / envanter** | 📦 Stok Düşüşü Uyarısı |
| **Yeni mağaza** | 🏪 Mağaza Karşılaması |
| **Müşteri** | 🛑 Olumsuz Yorum Tepkisi, 🚚 Kargo Bilgilendirme, ❓ Soruya Otomatik Yanıt |

Her şablon:
- `id`, `name`, `category`, `icon`, `description`
- `parameters` (operatörün doldurması gereken alanlar — hesap, gün sonra, indirim%, vb.)
- `build(params)` → natural_language üretir (sonra parse edilir)

UI: "Kurallar" sekmesinin en üstünde 9 şablon kartı; tıklayınca prompt'lar
açılıyor, doldurunca NL üretiyor + parse önizlemesi gösteriyor + "Etkinleştir"
butonuna basınca kural aktif oluyor.

API endpoint'leri:
- `GET /api/internal/rule-templates` — listele
- `POST /api/internal/rule-templates/{template_id}/materialize` —
  parametrele doldur, NL + parse önizlemesi döndür

## T2-5. Rule Versioning + Conflict Detection

**Versioning:** `structured_rules` tablosuna 4 yeni kolon eklendi:
- `version` (INTEGER) — 1'den başlar
- `parent_rule_id` (INTEGER, NULL) — bir sonraki sürüm öncekinin id'sini işaret eder
- `is_current` (INTEGER, default 1) — sadece son sürüm 1
- `supersedes_at` (TEXT) — bir önceki sürüm ne zaman pasifleştirildi

`save_rule(rule, new_version=True)` çağrısı:
1. Eski satır `is_current=0`, `enabled=0`, `supersedes_at=now` yapılır.
2. Yeni satır `version=eski+1`, `parent_rule_id=eski_id`, `is_current=1`
   olarak insert edilir.
3. `find_matching_rules` ve `list_rules` default olarak `is_current=1`
   filtresi uygular — eski sürümler tetiklenmez ama tarihçeye erişim
   mümkün.

API:
- `GET /api/internal/structured-rules/{id}/versions` — bir kuralın tüm sürümleri

**Conflict detection:** `detect_conflicts(user_id)` aynı
`(trigger_event, account_handle, channel)` üçlüsü için birden fazla aktif
kural varsa rapor üretiyor (severity: medium/high).

API: `GET /api/internal/structured-rules-conflicts`

UI: "Kurallar" sekmesinin üstünde otomatik uyarı banner'ı; her conflict
için trigger + hesap + kanal + ad listesi.

Çıktı örneği:
```
2 conflict
  medium — store.created olayı için genel hesabında instagram kanalında 2 aktif kural var
  high   — store.created olayı için çanakkale hesabında instagram kanalında 3 aktif kural var
```

## T2-6. Dashboard'da Canlı Execution Graph

"Kurallar" sekmesindeki son yürütmeler listesi artık tıklanabilir kart.
Tıklayınca modal açılıyor:

- **Stepper:** ilgili execution'da geçen node'lar sıralı pill'ler
  (Başlangıç → Bekleme → İçerik → Risk → Onay → Yayın → İzleme → Bitiş).
  Done/current/cancelled durumlarına göre renkli.
- **Trace listesi:** her node için durum (ok/interrupted/failed), kısa
  Türkçe özet, süre (ms), zaman damgası.
- **Canlı güncelleme:** modal 5 saniyede bir yeniden çekiyor — node'lar
  ilerledikçe operatör değişiklikleri görüyor.
- **Türkçe etiketler:** `nodeLabel`/`humanizeExecStatus` ile teknik
  string'ler operatöre uygun ifadelere dönüştürülüyor.

## T2-7. tool_adapters/ İskeleti

Yeni paket: `tool_adapters/{__init__,instagram,facebook,tiktok}.py`.

Her adapter aynı interface'i implement ediyor:
```python
class InstagramAdapter:
    provider_id = "instagram"
    def publish_post(self, *, user_id, account_handle, caption, image_url, hashtags): ...
    def health_check(self, user_id): ...
```

- `SOCIAL_PUBLISH_LIVE=1` ortamında adapter `publish_post` çağrısı
  credential resolve eder, "would_have_published" payload'u döner. **Gerçek
  HTTP çağrısı henüz yok** — Phase D'nin bir sonraki turunda eklenecek.
- `SOCIAL_PUBLISH_LIVE=0` (default) iken adapter `FeatureDisabledError`
  fırlatır; `publisher_node` bunu yakalayıp fake draft moduna düşer.

**Publisher node entegrasyonu:** Tur 2'de güncellendi.
- Credential varsa → real adapter çağrısını DENE → live flag açıksa
  `publish_mode="real_published"`, kapalıysa fake'e dön.
- Credential yoksa → her zaman fake (`publish_mode="draft_only"`).

API:
- `GET /api/internal/adapter-health` — tüm provider'ların durumu

## T2-8. Frontend Toplu Değişiklikler (Tur 2)

`index.html`'in mevcut iyi pratikleri (focus protection, hash-diff render,
session pill, stage animation) korundu. Eklenenler:

- **Kurallar sekmesi üstüne:** 9 hazır şablon kartı (grid layout, ikon +
  ad + açıklama + kategori).
- **Conflict banner:** aktif çakışma varsa kurallar sekmesinin en
  üstünde turuncu uyarı kutusu.
- **Execution detail modal:** her execution kartı tıklanabilir;
  açılır, canlı stepper + trace listesi, 5s polling.
- **waiting_timer status'u:** `humanizeExecStatus` haritasında yeni
  ("zamanı bekliyor" chip'i).

## T2-9. Doğrulanmış End-to-End Akışlar

Tur 2'de fonksiyonel testlerle doğrulandı:

```
Akış 1: 2 saniye delay'li full pipeline
─────────────────────────────────────────
parse → save → trigger → graph başlat
  → wait_node (interrupt_after) → STATUS=waiting_timer
  → workflow_worker fire_due_schedules tetikler
  → _handle_wait_resumes → runtime.resume_after_wait
  → content_generator + risk_analyzer
  → STATUS=waiting_human (approval interrupt)
  → operator approves
  → publisher + monitor + finalize
  → STATUS=completed ✓

Akış 2: Şablon → kural → kayıt
─────────────────────────────────────────
POST /rule-templates/anneler_gunu/materialize
  → NL üretildi
  → parse_confidence=0.9
  → operatör 'Etkinleştir' bastı
  → /structured-rules POST
  → rule kaydedildi (version=1, is_current=1)

Akış 3: Conflict detection
─────────────────────────────────────────
2 aktif store.created kuralı aynı handle + kanalda
  → /structured-rules-conflicts döndü
  → UI banner gösterdi

Akış 4: Semantic resolver
─────────────────────────────────────────
/semantic-resolve "yüksek stoklu ürünler"
  → kind=item, 5 ID, conf=1.0
  → samples: Logitech G Pro X, Razer Viper, ...
```

## T2-10. Mimari İlkelerin Korunması

- **AI proposes, runtime executes** ✓ — adapter feature flag kapalıyken
  hiçbir gerçek yayın yapılmıyor.
- **Deterministic core** ✓ — wait/resume timer mantığı tamamen
  deterministic (LangGraph checkpoint + DB scheduled_entry).
- **Replay safety** ✓ — SqliteSaver tüm checkpoint'leri tutuyor,
  time-travel mümkün.
- **Idempotency** ✓ — `handled` flag scheduled_entry payload'unda;
  resume çağrıları tekrarlanırsa no-op.
- **Approval gating** ✓ — wait sonrası approval hâlâ ZORLU interrupt.
- **Observability** ✓ — yeni node trace'leri persist ediliyor,
  dashboard'da canlı görünür.
- **Backward compat** ✓ — eski endpoint'ler değişmedi; eski rules.txt
  DSL hâlâ çalışıyor; `crewai_worker` shim'i delegate ediyor.

## T2-11. Sonraki Tur İçin Önerilen Roadmap

1. **Real Instagram Graph API** çağrısı — adapter stub'larına gerçek
   HTTP eklenmesi (POST /{ig-user-id}/media + /media_publish).
2. **Multi-account dispatch** — bir mağazanın N farklı hesabını
   yönetebilme; `target.account_handle` çoklu hedef desteği.
3. **Rule outcome learning** — `planner_outcomes` ile rule_id join
   edilip "bu kural genelde başarılı mı?" istatistiğini parse_confidence
   ile birleştirme.
4. **Visual rule builder** — pure-NL'in yanına drag-and-drop UI
   (event seç → eylem zinciri kur → preview).
5. **Channel adapters** — TikTok publish endpoint'i, Trendyol Q&A,
   Shopify webhook'ları.
6. **CrewAI tamamen kaldırma takip:** ai_planner.py'nin sade native
   versiyonu hâlâ duruyor (sadece CRITICAL_FALLBACK=1 ile devreye girer);
   tamamen silinebilir bu turda.
7. **Resolver UI önizlemesi** — kural editörünün altına resolver
   önizleme paneli (operatör "yüksek stoklu ürünler" yazarken kaç ürün
   olduğunu canlı görsün).

---

*Tur 2 bölüm sonu.*

---

# TUR 3 — Olgunlaştırma ve Akıllı Operatör Turu

> Bu bölüm Tur 1 (LangGraph altyapısı) ve Tur 2 (sistem olgunlaşma —
> CrewAI kaldırma, wait/resume, resolver, templates, versioning, viz,
> adapter scaffolding) üzerine eklenen üçüncü olgunlaştırma turunu
> anlatıyor. Mevcut yapılar olduğu gibi duruyor; bu tur AI'ın "öğrenip
> kendini iyileştirme" + "operatörle gerçek diyalog" + "gerçek dış
> entegrasyona hazır" katmanlarını ekliyor.

## T3-0. Bu turda yapılan değişikliklerin özeti

| # | Eksiklik | Çözüm | Sonuç |
|---|---|---|---|
| 1 | **Sürekli öğrenme ve otomatik optimizasyon yoktu** | `rule_learning.py` modülü + `runtime._update_execution_row` hook'u | ✅ Her execution sonu health_score güncelliyor; eşik tabanlı operatör önerileri |
| 2 | **Conversational rule editing yoktu** | `conversational_rule_edit.py` + `business_chat` early-dispatch + `conversation_memory.active_rule_id` | ✅ "Kural #3'ü devre dışı bırak", "delay'i 5 güne çıkar" doğal Türkçe çalışıyor |
| 3 | **Real social adapter HTTP'siz** | `tool_adapters/instagram.py` ve `facebook.py` Graph API çağrıları (requests) + güvenlik katmanları | ✅ SOCIAL_PUBLISH_LIVE=1 olunca gerçek Meta Graph API'yi vuruyor; test handle'larda otomatik skip |
| 4 | **Conflict resolution salt-tespit** | `conflict_resolver.py` — 3 çözüm tipi + operatör-onaylı `apply_resolution` | ✅ "En yenisi kalsın", "En sağlıklısı kalsın", "Manuel inceleyeceğim" butonları |
| 5 | **Şablon UI prompt-driven** | Şablon parametre formu modal'a alındı (input field'lar) | ✅ Çok daha akıcı UX |
| 6 | **Execution viz timeline'sız** | Node duration bar chart eklendi (relative width) | ✅ Hangi node uzun sürdü görsel olarak görünüyor |

## T3-1. Sürekli Öğrenme: rule_learning.py

**Tasarım:** `planner_learning.py` (generic intent confidence drift)'tan
ayrı tutuldu çünkü structured rule'lar için per-rule_id, version-aware,
operatör-decision-aware öğrenme istiyoruz.

`structured_rules` tablosuna eklenen kolonlar (Tur 3):
- `health_score` REAL DEFAULT 0.7 — runtime başarısının ölçümü (parse_confidence'tan ayrı)
- `success_count`, `failure_count`, `cancel_count` INTEGER
- `last_outcome` TEXT

**Outcome delta tablosu** (rule_learning.OUTCOME_DELTAS):
| Outcome | Delta | Açıklama |
|---|---|---|
| `approved_user` | **+0.05** | Operatör açıkça onayladı |
| `completed` | +0.02 | Otomatik tamamlandı |
| `cancelled` | -0.05 | Plan iptal veya validation kötü |
| `rejected_user` | -0.08 | Operatör açıkça reddetti |
| `failed` | -0.10 | Gerçek hata |

Skor `[0.10, 0.99]` aralığında bounded; default `0.70` (yeni kural tarafsız zemin).

**Suggestion eşikleri:**
- `health < 0.30` veya negatif oran ≥ 0.6 → **review** önerisi
- `health ≥ 0.85` ve success ≥ 3 → **promote** önerisi
- En az 3 observation şartı (yetersiz veriyle öneri verilmez)

**Runtime hook:** `langgraph_engine.runtime._update_execution_row` artık
`end=True, status in (completed, cancelled, failed)` olduğunda otomatik
`rule_learning.record_execution_outcome()` çağırıyor. Approve/reject
kararı `resume_execution` üzerinden `user_decision` parametresiyle de
hook'a iletiliyor.

Smoke test çıktısı (4 outcome zinciri):
```
cancelled  → health=0.65  (0.70 → 0.65)
failed     → health=0.55
cancelled  → health=0.50
completed  → health=0.52
```

## T3-2. Conversational Rule Editing

**`conversational_rule_edit.py`** modülü iki kademede çalışıyor:

1. **`detect_edit_intent(text)`** — deterministic regex/keyword:
   - Edit trigger keywords (`değiştir`, `kapat`, `devre dışı`, `sil`, vb.)
   - Rule identification: `Kural #3`, `5 numaralı kural`, name-token-overlap, session active_rule_id pronoun
   - Operation kind: `set_delay` / `set_channel` / `set_handle` / `set_template` / `enable` / `disable` / `delete` / `rename`
   - Parameter extraction (delay seconds, channel, handle, template)

2. **`apply_edit(intent)`** — Python yapıyor (LLM rule body'sini değiştirmez):
   - Substantive değişiklik (delay/channel/handle/template) → `save_rule(rule, new_version=True)` (immutable history)
   - Toggle (enable/disable) → `set_enabled()` (in-place)
   - Delete → `confirm_delete=True` zorunlu (operatör onayı)

**`conversation_memory`** güncellendi: `active_rule_id` + `active_rule_name`
session kolonları; `set_active_rule()` ve `get_active_rule()` API'leri.

**`business_chat.answer_question`** early-dispatch: gelen mesajda edit
intent confidence ≥ 0.65 ise retrieval + synthesizer atlanır, doğrudan
`apply_edit` sonucu Türkçe metin olarak döndürülür.

Smoke test çıktısı:
```
"Yeni mağaza paylaşımı kuralını 5 güne çıkar"
  → set_delay      target=4  params={'delay_seconds': 432000}  conf=0.85
"Kural #3'ü devre dışı bırak"
  → disable        target=3
"5 numaralı kuralı sil"
  → delete         target=5
"Bu kuralı Facebook'a al"
  → set_channel    target=99  params={'channel': 'facebook'}
"Çanakkale kuralını sadece 1 saat sonrasına ayarla"
  → set_delay      target=99  params={'delay_seconds': 3600}
"Hangi ürün en çok satıyor?"
  → (not edit)
```

Apply sonucu (chat içinden):
```
business_chat: "Yeni mağaza paylaşımı kuralını devre dışı bırak"
  intent: conversational_rule_edit
  mode: rule_edit
  answer: '"Yeni mağaza paylaşımı" kuralını devre dışı bıraktım. Yeniden
          açmak istersen söyle.'
```

## T3-3. Real Social Publish Adapter'ları

**Üç katmanlı güvenlik** (`tool_adapters/instagram.py` + `facebook.py`):

1. `SOCIAL_PUBLISH_LIVE=1` env flag (default `0`) — hiç açılmadan
   `FeatureDisabledError` fırlatır.
2. Aktif credential (`social_credentials`) gerekli — yoksa
   `AdapterCredentialError`.
3. `allow_real=True` per-call kwarg — publisher_node geçer; default `False`.
4. **Safety net**: account_handle `test_` / `demo_` / `sandbox_` /
   `ai_ops_test` / `smoke` ile başlıyorsa real HTTP YAPMAZ — accidental
   publish'ı engeller.

**Token formatı**: JSON blob (önerilen) veya bare string:
```json
{"access_token": "EAAB...", "ig_user_id": "17841401234567890"}
```
Bare string verilirse `account_handle` `ig_user_id` olarak kullanılır.

**Instagram Graph API akışı** (iki adım):
```
POST /v18.0/{ig-user-id}/media         {image_url, caption, access_token}
                                         → creation_id
POST /v18.0/{ig-user-id}/media_publish  {creation_id, access_token}
                                         → post_id
```

**Facebook Graph API akışı** (tek adım):
```
POST /v18.0/{page-id}/feed     (text-only)   or
POST /v18.0/{page-id}/photos   (with image)
```

Tüm HTTP hataları yakalanır; publisher_node düşmez, fake draft moduna
döner.

## T3-4. Conflict Resolver Geliştirme

**`conflict_resolver.py`** Tur 2'nin basit `detect_conflicts`'in üstüne
oturuyor:

`conflicts_with_suggestions(user_id)` her conflict için 3 çözüm üretir:

| Çözüm | Açıklama |
|---|---|
| **`deactivate_older`** | Sadece en yeni kural aktif kalır, eskileri kapatılır |
| **`deactivate_lower_health`** | En sağlıklı (en yüksek health_score) kalır, diğerleri kapatılır |
| **`keep_one_review`** | No-op — operatör elle inceleyecek |

`apply_resolution(conflict_key, action)` operatör onayıyla çözümü uygular
(disable işlemleri). `CONFLICT_RESOLVED` observability trace'i emit eder.

Opsiyonel LLM zenginleştirmesi (`NL_CONFLICT_USE_LLM=1`): conflict
summary'leri 2 cümlede operatöre konuşulur hâle getirilir.

## T3-5. API Endpoint'leri (Tur 3)

| Endpoint | Amaç |
|---|---|
| `GET /api/internal/structured-rules/{id}/learning` | Tek kural health istatistikleri |
| `GET /api/internal/learning-suggestions` | Tüm aktif kurallar için iyileştirme önerileri + overview |
| `GET /api/internal/structured-rules-conflicts/suggestions` | Conflict + çözüm önerileri |
| `POST /api/internal/structured-rules-conflicts/resolve` | Operatör onayıyla çözüm uygula |
| `POST /api/internal/chat-edit/preview` | NL mesajdan edit intent çıkar (uygulamadan) |
| `POST /api/internal/chat-edit/apply` | Edit intent'ini onayla uygula |

## T3-6. Frontend Yeni Özellikler

- **Şablon parametre formu modal** — `prompt()` yerine düzgün input
  field'larıyla. Şablon kartına tıkla → modal açılıyor → form doldur →
  Önizle butonu NL'i parse + preview ediyor.
- **Conflict çözüm butonları** — banner'daki her conflict altında 3
  buton ("En yenisi kalsın", "En sağlıklısı kalsın", "Manuel
  inceleyeceğim"). Buton onayla işlem uygulanıyor.
- **AI Öğrenme Önerileri paneli** — Kurallar sekmesinde, kurallar
  health_score'una göre öneriler (chip + özet metin + success/cancel/fail
  sayaçları). Ortalama sağlık skoru üst başlıkta gösteriliyor.
- **Execution timeline bar chart** — modal'da her node'un duration_ms'i
  görece genişlikte renkli bar olarak çiziliyor. Hızlı/yavaş node'lar
  görsel olarak anında belli.
- **`humanizeExecStatus`'ta "rule_edit"** mode chip'i yeni —
  conversational edit sonuçları chip'le belli oluyor.

## T3-7. Doğrulanmış End-to-End Akışlar

```
Akış 1: Conversational rule edit
─────────────────────────────────────────
chat: "Yeni mağaza paylaşımı kuralını 7 güne çıkar"
  → business_chat early-dispatch
  → detect_edit_intent: kind=set_delay, target=4, delay=604800
  → apply_edit → save_rule(new_version=True)
  → AI cevap: "'Yeni mağaza paylaşımı' kuralının bekleme süresini 1 hafta
              olarak güncelledim (yeni sürüm v?)"

Akış 2: Learning loop
─────────────────────────────────────────
Execution biter (status=completed/cancelled/failed)
  → runtime._update_execution_row hook
  → rule_learning.record_execution_outcome
  → health_score güncel + sayaç artar
  → suggestion endpoint'i çağrıldığında uygun rule_id öneri listesinde

Akış 3: Conflict resolution
─────────────────────────────────────────
GET /structured-rules-conflicts/suggestions → 2 conflict, her birine 3 öneri
Operatör "En sağlıklısı kalsın" butonuna basar
  → POST /structured-rules-conflicts/resolve {conflict_key, action}
  → conflict_resolver.apply_resolution → set_enabled(False) eskilere
  → CONFLICT_RESOLVED trace persist
  → UI refresh → conflict banner kaybolur

Akış 4: Real adapter dry-run
─────────────────────────────────────────
SOCIAL_PUBLISH_LIVE=1 ortamında publisher_node çağırılır
  → tool_adapters.instagram.publish_post (allow_real=True)
  → account_handle = "canakkale_store" (test pattern dışı)
  → credential.token JSON blob ise access_token + ig_user_id parse
  → Meta Graph API'ye iki adımlı POST
  → post_id döner; publish_mode="real_published"

Tek bir test handle'ı için (ai_ops_test) → real HTTP atılmaz,
"skipped_real_call" döner. Güvenlik korunur.
```

## T3-8. Korunan Mimari Prensipler

- **AI proposes, runtime executes** ✓ — conversational edit'te bile AI
  rule body'sini yazmıyor; Python apply_edit kodu deterministic.
- **Deterministic core** ✓ — rule_learning bounded delta, intent
  detection regex-first.
- **Replay safety** ✓ — versioning + checkpoint korunuyor.
- **Idempotency** ✓ — apply_resolution disabled rule'a re-apply no-op.
- **Approval gating** ✓ — adapter even SOCIAL_PUBLISH_LIVE=1'de
  allow_real=False ile çağırılınca skip.
- **Observability** ✓ — RULE_LEARNING_UPDATE, CONFLICT_RESOLVED yeni
  trace tagleri.
- **Backward compat** ✓ — Tur 2 endpoint'leri / eski yollar değişmedi;
  conflict suggestion endpoint'i Tur 2 conflict endpoint'inin üstüne
  geldi (eski hâlâ çalışıyor).

## T3-9. Sonraki (Tur 4) İçin Önerilen Roadmap

1. **Vector retrieval for rule similarity** — "anneler günü"
   yazıldığında geçmiş benzer kuralların başarısına bakıp önceden uyarı
   ver. `embedding_placeholder` kolonu zaten var.
2. **Multi-account dispatch** — bir hesap yerine handle pattern'i
   (`store_handle IN (...)`) — birden çok hesap için tek kural.
3. **TikTok content API** real publish (Phase D2'de stub vardı).
4. **Trendyol Q&A + Shopify webhook** real adapter'lar.
5. **Visual rule builder** — NL'in yanına drag-and-drop "trigger →
   actions" kuruculu UI.
6. **Org/role-aware rule visibility** — Tur 1'de eklenen org_id
   filtreleme kurallarda da uygulanmalı (şu an user_id-aware ama
   org-wide widening yok).
7. **Bulk import / export** — kuralları JSON dışa aktar, başka
   workspace'e içe aktar.
8. **Resolver UI live preview** — kural editörünün altında semantic
   resolver kaç entity bulduğunu canlı gösteren panel.
9. **Time-travel viewer** — LangGraph checkpoint history UI; operatör
   "şu adımdan tekrar başla" diyebilir.
10. **Cost / quota tracking** — her LLM çağrısının maliyetini ölç,
    org bazlı quota uygula.

---

*Doküman sonu. Sorular için: kod içindeki dosya başı docstring'leri
+ `SYSTEM_REVIEW_FOR_EXTERNAL_AUDIT.md`.*
