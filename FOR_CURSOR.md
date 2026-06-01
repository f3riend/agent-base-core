# FOR_CURSOR.md — Projenin Tam Analizi ve Vizyon Özeti

> **Amaç:** Cursor oturumları için tek kaynaklı mimari brief. Kod değişikliği değil; mevcut durum, hedef vizyon, boşluklar ve yol haritası özeti.  
> **Tarih:** 2026-05-27  
> **Kapsam:** `agent-base/` monorepo (üretim yüzü) + `rule-based-engine/` kök dizini (paralel Python modülleri ve geliştirme dashboard’u).

---

## 1. Mevcut Sistem Durumu (Detaylı Özet)

### 1.1 Klasör yapısı ve ana dosyalar

Proje fiilen **iki katmanlı** bir yapıdadır:

| Katman | Konum | Rol |
|--------|--------|-----|
| **Üretim monorepo** | `agent-base/` | Docker all-in-one, PHP UI, birleşik FastAPI, supervisord ile 7 process |
| **Paralel Python ağacı** | `rule-based-engine/` (repo kökü) | ~67 flat `.py` modülü; LangGraph, listener, orchestration API; `index.html` geliştirme dashboard’u |

**`agent-base/` iç yapısı (özet):**

```
agent-base/
├── docker-compose.yml, Dockerfile, Makefile, README.md
├── FOR_GROK.md, ÖZET.md, SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md
├── docker/                    (nginx, supervisord — 7 process)
├── agent-base-api/            (Python 3.11 + FastAPI + LangGraph)
│   ├── app/main.py            (birleşik entrypoint)
│   ├── app/                   (auth, social_media, runtime/orchestrator, LightAgent)
│   ├── langgraph_engine/      (state.py, nodes.py, runtime.py)
│   ├── tool_adapters/         (instagram, facebook, tiktok stub)
│   └── ~68 orchestration modülü (listener, structured_rule*, nl_rule_parser, …)
└── php-ui/                    (PHP 8 standalone frontend)
    ├── public/index.php       (front controller, route’lar)
    ├── views/layout.php       (sidebar)
    ├── views/timeline/        (_rules_toolbar.php, store_page.php)
    ├── views/system_admin.php (AI Operatör Merkezi)
    └── public/assets/js/      (timeline-page-rules.js, timeline-store-automation.js)
```

**Repo kökündeki önemli modüller** (`rule-based-engine/*.py`): `listener.py`, `structured_rule_engine.py`, `structured_rule.py`, `nl_rule_parser.py`, `orchestration_api.py`, `langgraph_engine/`, `rule_templates.py`, `conversational_rule_edit.py`, `business_chat.py`, `workflow_worker.py`, `task_executor.py`, `crewai_worker.py` (shim), `index.html`.

İki ağaç **yaklaşık aynı orchestration kodunu** taşır (~68 vs ~67 dosya); senkronizasyon riski vardır — değişiklikler bazen yalnızca bir tarafta kalabilir.

**Çalışma zamanı (agent-base):**

- **nginx :80** → `/api/*` → uvicorn; `/*` → php-fpm  
- **supervisord:** api, celery worker, `rbe-listener`, `rbe-workflow`, `rbe-task`, php-fpm, nginx  
- **Veri:** `listener.db` (SQLite orchestration), `fake_ai_api.db` (timeline/events), MySQL (kullanıcı, sosyal medya içeriği)

---

### 1.2 Sidebar yapısı (php-ui — operatörün gördüğü arayüz)

`php-ui/views/layout.php` üzerinden:

| Grup | Alt öğeler |
|------|------------|
| **Sosyal Medya** | Takvim, Etiketler, Şablonlar (post), Onay Bekleyenler |
| **Kampanya Yönetimi** | Takvim, Şablonlar (banner), Onay Bekleyenler |
| **Sistem Yöneticisi** | Tek link → `/social-media/system-admin` (AI Operatör Merkezi) |
| **Zaman Tüneli** | 22 alt sayfa (aşağıda) |
| **Ayarlar** | Hesap, Çalışma Alanı, Yapay Zeka, Anahtarlar, Otomasyon, Güvenlik |

**Önemli:** Tur 5 ile sidebar’dan **ayrı “Kurallar” sekmesi kaldırıldı**. `/kurallar` ve `/rules` → `302` ile `/page/timeline/all` yönlendirmesi (`public/index.php`).

---

### 1.3 Zaman Tüneli altındaki sayfalar

Sidebar’daki slug’lar (`/page/timeline/<slug>`):

| Slug | Etiket | Kural paneli event filtresi |
|------|--------|-----------------------------|
| `all` | Tümü | Filtre yok (tüm kurallar) |
| `orders` | Siparişler | `order.*` |
| `products` | Ürünler | `product.*` |
| `stock` | Stok | `stock.*` |
| `reviews` | Değerlendirmeler | `review.*` |
| `questions` | Sorular | `customer.question` |
| `messages` | Mesajlar | `customer.question` |
| `store` | Mağaza Sayfası | `store.*` (+ özel `store_page.php`) |
| `campaigns` | Kampanyalar | `campaign.*` |
| `ads` | Reklamlar | `banner.*`, `sales.*` |
| `banners` | Bannerlar | `banner.*` |
| `flash-sales` | Flash Satış | `sales.*` |
| `discounts` | İndirimler | `sales.*` |
| `delivery` | Teslimat | `shipping.*`, `order.shipped` |
| `returns` | İadeler | `order.cancelled` |
| `coupons`, `staff`, `checkin-checkout`, `withdrawals`, `plugins`, `subscription`, `components` | (çeşitli) | **Boş prefix** — panel var, olay filtresi yok |

Her timeline sayfasında (slug doluysa) `views/page.php` → `timeline/_rules_toolbar.php` include edilir; `timeline-rules.css` + `timeline-page-rules.js` modül olarak yüklenir.

Panel yetenekleri (Tur 5 Polish-2): NL composer, şablon grid, önizleme stepper, aktif kural listesi (slug’a göre filtre), çakışma banner’ı, son yürütme chip’i, toast, optimistic toggle, Cmd/Ctrl+Enter.

---

### 1.4 LangGraph entegrasyonu

**Şema ve parse:**

- `structured_rule.py` — Pydantic `StructuredRule` (trigger, timing, target, content, actions)
- `nl_rule_parser.py` — Türkçe NL → yapılandırılmış kural (regex ön filtre + LLM)
- `semantic_entity_resolver.py` — “Çanakkale hesabı” gibi ifadeler → entity ID

**Motor:**

- `langgraph_engine/state.py` — `RuleExecutionState`, alt domain modelleri
- `langgraph_engine/nodes.py` — supervisor, wait, content_generator, risk_analyzer, approval_gate, publisher, monitor, notify_customer, create_coupon, finalize
- `langgraph_engine/runtime.py` — dinamik `StateGraph`, `SqliteSaver`, `interrupt_before=["approval"]`, `interrupt_after=["wait"]`, `start_execution` / `resume_after_wait` / `resume_execution`

**Orkestrasyon:**

- `structured_rule_engine.py` — CRUD, olay–kural eşleme, graph kickoff
- `orchestration_api.py` — ~96 internal endpoint (`/api/internal/structured-rules*`, executions, templates, chat-edit, conflicts, …)
- `db.py` — `structured_rules`, `rule_executions`, `graph_node_traces` tabloları

**Yardımcı modüller:** `rule_templates.py` (9 hazır şablon), `rule_learning.py`, `conversational_rule_edit.py`, `conflict_resolver.py`, `tool_adapters/*` (Instagram/Facebook gerçek HTTP; TikTok stub; `SOCIAL_PUBLISH_LIVE` kapısı).

---

### 1.5 Kural akışı (event → rule → execution)

```
[Kaynak] fake_commerce / gerçek platform
    → INSERT fake_ai_api.db.timeline
         ↓ (listener.py ~2s poll)
[listener.process_event]
    → EventEnvelope, tenant user_id, route (critical | monitoring | creative)
    → (paralel) structured_rule_engine.trigger_rules_for_event
         → langgraph_engine.runtime.start_execution
              → node zinciri (wait → content → risk → approval ⏸ → publish → …)
    → (paralel) rule_engine.find_matching_rules → action_engine (legacy DSL)
    → (paralel) autonomous_planner → planner_runtime (LLM plan, workflow)
         ↓
[Wait] scheduled_entries → workflow_worker → resume_after_wait
[Approval] approval_requests → operatör onayı → resume_execution
[Publish] tool_adapters / tools.py
         ↓
rule_executions + graph_node_traces + rule_learning health_score
```

**Tasarım ilkesi:** *AI proposes, runtime executes* — LLM/NL parser kanonik kural üretir; çalıştırma LangGraph ile deterministik ve checkpoint’li.

---

### 1.6 CrewAI durumu

| Bölge | Durum |
|-------|--------|
| **agent-base-api** | CrewAI bağımlılığı yok (`pyproject.toml`); `agent_factory.LightAgent` + native OpenAI/Gemini |
| **rule-based-engine kökü** | `pyproject.toml` içinde CrewAI yok; `tools.py` protocol-typed `BaseTool`; `crewai_worker.py` → `task_executor.py` shim (deprecated uyarı) |
| **Eski dokümantasyon** | `PROJECT_AUDIT.md`, `SYSTEM_REVIEW_FOR_EXTERNAL_AUDIT.md`, kök `SON_DEGISIKLIKLER` Tur 2 bölümü hâlâ CrewAI worker’dan bahsedebilir — **kod ile doküman arasında uyumsuzluk** |
| **autonomous_planner** | CrewAI kaldırıldı; LLM planlama native client ile |

**Özet:** Hedef mimariye göre CrewAI **fiilen kaldırılmış**; kalan risk eski isimlendirme (`crewai_worker`, Makefile target `crewai-worker`) ve legacy `ai_tasks` + autonomous path’in LangGraph ile çakışan üçlü yapı.

---

### 1.7 Mevcut güçlü yönler

1. **LangGraph yolu üretim kalitesinde:** checkpoint, onay kapısı, wait/resume, idempotency, trace tabloları.
2. **Türkçe NL kural yazımı** ve şablon kütüphanesi operatör dostu.
3. **Tur 5 contextual rules (php-ui):** Kurallar timeline altına taşındı; slug bazlı filtre; premium UX (toast, stepper, conflict banner).
4. **Monorepo + Docker:** tek container’da API, worker’lar, PHP UI.
5. **Üç şablon katmanı ayrımı:** kural şablonları (`rule_templates`) vs SM post şablonları vs kampanya banner şablonları.
6. **Güvenlik katmanları:** `SOCIAL_PUBLISH_LIVE`, Fernet credentials, synthetic event skip, Pydantic fail-fast.

### 1.8 Mevcut zayıf yönler

1. **İkili kod tabanı:** kök `rule-based-engine/` ile `agent-base/agent-base-api/` senkronizasyon belirsizliği.
2. **Üç paralel tetikleme yolu** (structured + legacy rule_engine + autonomous) — karmaşıklık, çift iş riski.
3. **Bazı timeline slug’larında boş event filtresi** — kupon, çalışan, eklenti vb. sayfalarda “contextual” panel var ama anlamsal filtre yok.
4. **Generic timeline sayfaları** (`page.php`) hâlâ “Dinamik sayfa” placeholder; gerçek iş verisi/timeline feed entegrasyonu zayıf.
5. **Kök `index.html` dashboard** hâlâ ayrı **“Kurallar” sekmesi** taşıyor — php-ui vizyonu ile **çelişki**.
6. **Sistem Yöneticisi chat** — `conversational_rule_edit` backend hazır; tam tool-binding (sohbetten doğrudan API) Tur 6 adayı.
7. **Dokümantasyon parçalı:** FOR_GROK, ÖZET, SON_DEGISIKLIKLER, kök audit dosyaları farklı evrim anlarını yansıtıyor.

---

## 2. Kullanıcının Gerçek Vizyonu (Netleştirilmiş)

Aşağıdaki maddeler kullanıcı niyetinin operasyonel tanımıdır; Tur 5 ile kısmen hayata geçmiş, tamamlanması gerekenler işaretlidir.

### 2.1 Kurallar sekmesi tamamen kalkacak

- **Hedef:** Global “Kurallar” navigasyonu ve merkezi kural sayfası yok.
- **Durum:** php-ui’da **yapıldı** (sidebar linki yok, `/kurallar` redirect).
- **Eksik:** Repo kökündeki `index.html` hâlâ **“Kurallar”** sekmesi içeriyor — geliştirme dashboard’u vizyonla uyumsuz.

### 2.2 Kurallar Zaman Tüneli altındaki her sayfaya contextual entegre

- **Hedef:** Mağaza, Ürünler, Reklamlar, Kampanyalar, Stok vb. — her sayfada o sayfaya özel kural paneli.
- **Durum:** php-ui’da `_rules_toolbar.php` + `timeline-page-rules.js` ile **büyük ölçüde yapıldı**.
- **Eksik:** Filtresiz slug’lar; placeholder sayfa içeriği; kök `index.html` alternatif UX.

### 2.3 Her sayfada o sayfaya özel kural paneli

- **Hedef:** Başlık, placeholder, şablon önerileri ve liste filtresi sayfa bağlamına göre (ör. “Mağaza Kuralları”, `store.*`).
- **Durum:** `slugLabels` + `slugEventMap` ile **tanımlı**; boş prefix’li slug’lar için bağlam zayıf.

### 2.4 Kural oluştururken var olan şablonlar

- **Hedef:** Örnek: *“Mağaza oluştuğunda /mers şablonunu kullanarak Instagram postu oluştur.”*
- **Durum:** `rule_templates.py` + `GET /rule-templates` + UI şablon grid **mevcut**; operatörün **kendi özel şablonunu kaydetmesi** (ör. “mers”) henüz ürünleşmemiş (Tur 6 adayı).

### 2.5 Sistem Yöneticisi = güçlü AI Operatör chat merkezi

- **Hedef:** Tüm kuralları, çakışmaları, operasyonu doğal Türkçe sohbetten yönetmek; analytics + multi-turn cognition.
- **Durum:** `system_admin.php` + `timeline-store-automation.js` (~1182 satır), 4 mod (Analiz/Operasyon/Strateji/İçerik); sidebar vurgusu Tur 5’te güçlendirildi.
- **Eksik:** Sohbetten doğrudan structured-rule API tool-binding; timeline contextual panel ile derin entegrasyon (tek tık “bu kuralı düzenle”).

### 2.6 CrewAI tamamen kaldırılacak; agent-base LangGraph ile uyumlu

- **Hedef:** Tek execution intelligence: LangGraph + `task_executor`; agent-base içi LightAgent/native LLM.
- **Durum:** Bağımlılık ve runtime **büyük ölçüde temiz**; shim ve Makefile isimleri, legacy autonomous/ai_tasks yolu **tam temizlik değil**.
- **Eksik:** Kök ve monorepo tek kaynak; eski dokümanların güncellenmesi; autonomous path’in rolünün netleştirilmesi (kapatma veya LangGraph altına alma).

---

## 3. Sorun Tespiti

### 3.1 Sistemdeki mevcut sorunlar

| # | Sorun | Etki |
|---|--------|------|
| 1 | **Çift Python + çift UI** (php-ui vs `index.html`) | Vizyon bir yerde uygulanır, diğerinde eski UX kalır |
| 2 | **Üçlü listener path** | Aynı event için legacy rule + structured + autonomous tetiklenebilir; operatör hangi yolun çalıştığını zor ayırt eder |
| 3 | **Boş event prefix’li timeline slug’ları** | “Contextual” iddia edilir ama kural listesi sayfa bağlamına göre filtrelenmez |
| 4 | **Timeline sayfa içeriği placeholder** | Zaman tüneli feed/detay php-ui’da zayıf; kural paneli ana değer taşıyıcı |
| 5 | **Dokümantasyon–kod sapması** | Audit dosyaları CrewAI worker anlatır; kod shim/task_executor |
| 6 | **AI Operatör tool-binding eksik** | Sohbetten kural CRUD için operatör hâlâ timeline panel veya API’ye yönlendirilir |
| 7 | **Özel operatör şablonları (“mers”) yok** | Sadece sistem `rule_templates`; kişiselleştirilmiş şablon katmanı eksik |
| 8 | **Senkronizasyon** | Kök `rule-based-engine` ile `agent-base-api` ayrı commit/evrim riski |

### 3.2 Vizyon ile mevcut yapı arasındaki farklar

| Vizyon | Mevcut gerçeklik | Gap |
|--------|------------------|-----|
| Kurallar sekmesi yok | php-ui: ✓ / kök index.html: ✗ | Dashboard hizalanmalı veya kök UI emekliye ayrılmalı |
| Her timeline sayfasında anlamlı contextual kurallar | 22 slug’ta panel var; ~8’inde filtre boş | Prefix map tamamlanmalı veya sayfalar birleştirilmeli |
| Şablonla kural (“mers”) | Sistem şablonları var; kullanıcı şablonu yok | Yeni veri modeli + UI |
| Tek AI execution (LangGraph) | LangGraph + legacy + autonomous paralel | Yol birleştirme veya net devre dışı bırakma politikası |
| Agent-base LangGraph uyumu | agent-base-api uyumlu; kök kopya ayrı | Tek canonical tree |
| AI Operatör merkezi | UI var; derin entegrasyon kısmi | Chat ↔ rules API bağlama |

### 3.3 Arayüz eksiklikleri ve tutarsızlıklar

1. **İki frontend paradigması:** Tailwind SPA (`index.html`) vs PHP `app-shell` — farklı navigasyon, farklı “Kurallar” konumu.
2. **Sistem Yöneticisi vs timeline panel:** İkisi de kural yönetiyor; operatör için “nereden yönetilir?” belirsizliği (kısmen “AI ile yönet →” linki var).
3. **Üç “şablon” kavramı:** Kural / SM post / kampanya — operatör eğitimi gerektirir; yanlış şablona gitme riski.
4. **Onaylar iki yerde:** Sosyal medya onayları vs LangGraph `approval_requests` — farklı URL’ler, ortak mental model zayıf.
5. **Boş timeline sayfaları:** Kural paneli dolu, iş olayı listesi boş — ürün hissi eksik.
6. **Mobil / dark mode:** Tur 6 adayı; henüz tam değil.

---

## 4. Öneriler

> Bu bölüm yalnızca **ürün ve mimari öncelik** önerir; kod veya refactor talimatı içermez.

### 4.1 En kritik adımlar (öncelik sırasıyla)

1. **Tek canonical kod ve UI seçimi**  
   Üretim yüzü olarak `agent-base/php-ui` + `agent-base-api` netleştirilmeli; kök `index.html` ya vizyona hizalanmalı (Kurallar sekmesi kaldırılmalı) ya da “dev-only / deprecated” olarak işaretlenip operatör yönlendirmesi kesilmeli.

2. **Timeline slug–event eşlemesini tamamlama**  
   Boş prefix’li sayfalar için ya gerçek `event_type` namespace’leri tanımlanmalı ya da sidebar’dan kaldırılmalı / birleştirilmeli; aksi halde “contextual” vaat zayıflar.

3. **Listener yol birleştirme politikası**  
   Structured rules ana yol olarak netleştirilmeli; legacy `rules.txt` ve autonomous planner için açık “ne zaman devreye girer / varsayılan kapalı mı” kararı operatör karmaşasını azaltır.

4. **Sistem Yöneticisi ↔ LangGraph köprüsü**  
   Sohbetten `structured-rules`, `chat-edit`, `conflicts` endpoint’lerine tool-binding; timeline panelindeki kurallarla oturum bağlamı paylaşımı — vizyonun “tek AI operatör merkezi” tamamlanır.

5. **Operatör özel şablon katmanı**  
   “mers şablonu” gibi kullanıcı/workspace şablonları (`rule_templates` üzerine veya SM `content_templates` ile ilişkilendirilmiş) — NL kural composer’da birincil seçenek olmalı.

6. **Dokümantasyon konsolidasyonu**  
   FOR_GROK / ÖZET / SON_DEGISIKLIKLER / bu FOR_CURSOR tek gerçeklik çizgisi; eski CrewAI anlatımları güncellenmeli.

7. **CrewAI kalıntılarının operasyonel temizliği**  
   Shim dosya adları, Makefile target’ları, deploy script referansları — operatör ve geliştirici zihninde “CrewAI yok” netliği.

8. **Timeline sayfa içeriğinin doldurulması**  
   Kural paneli yanında gerçek event feed / entity detay — Zaman Tüneli “zaman çizelgesi” algısını güçlendirir.

### 4.2 Başarı kriterleri (vizyon tamamlandığında)

- Sidebar’da **hiçbir** “Kurallar” girişi yok; tüm kural CRUD timeline alt sayfalarında veya Sistem Yöneticisi chat’inden.
- Her aktif timeline slug’ında **anlamlı** event filtresi ve sayfa özel placeholder/şablon önerisi var.
- Production execution **tek ana motor:** LangGraph structured rules (+ onay/wait/publish).
- CrewAI adı ve worker süreci deploy’da yok; `task_executor` tek AI task tüketicisi.
- Operatör örnek akışı uçtan uca çalışır: *mağaza oluştu → şablon seç → önizle → etkinleştir → event → onay → yayın*.

### 4.3 Bilinçli olarak ertelenebilir (Tur 6+)

- Vector similarity (`embedding_placeholder`)
- TikTok gerçek publish
- Visual drag-drop rule builder
- Time-travel checkpoint UI
- Org/role-aware kural görünürlüğü
- Dark mode / mobil sticky composer

---

## 5. Hızlı Referans — Önemli dosyalar

| Alan | Dosya |
|------|--------|
| PHP sidebar | `php-ui/views/layout.php` |
| Contextual kural paneli | `php-ui/views/timeline/_rules_toolbar.php` |
| Kural panel JS | `php-ui/public/assets/js/timeline-page-rules.js` |
| AI Operatör UI | `php-ui/views/system_admin.php`, `timeline-store-automation.js` |
| Route / redirect | `php-ui/public/index.php` |
| FastAPI entry | `agent-base-api/app/main.py` |
| API router | `agent-base-api/orchestration_api.py` |
| Event dinleyici | `agent-base-api/listener.py` (kökte de aynı isim) |
| LangGraph runtime | `agent-base-api/langgraph_engine/runtime.py` |
| Kural şeması | `agent-base-api/structured_rule.py` |
| NL parse | `agent-base-api/nl_rule_parser.py` |
| Şablonlar | `agent-base-api/rule_templates.py` |
| Eski dev dashboard | `rule-based-engine/index.html` (repo kökü) |
| Mimari brief (Grok) | `agent-base/FOR_GROK.md` |

---

*Bu belge Cursor oturumlarında “baş mimar” bağlamı olarak kullanılmalıdır. Uygulama değişiklikleri için önce bu dosyadaki gap tablosu ile mevcut kod grep doğrulaması yapılmalıdır.*
