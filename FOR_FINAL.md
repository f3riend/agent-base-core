# FOR_FINAL.md — Son Vizyon ve Durum Analizi

> **Amaç:** `FOR_GROK.md` (teknik mimari) ve `FOR_CURSOR.md`
> (vizyon-gap analizi) sentezinden çıkarılmış kesin durum
> raporu. Kod değişikliği önermez — sadece **mevcut gerçeklik,
> vizyon, gap'ler ve öncelikler** üzerine analiz.
>
> **Tarih:** 2026-05-27
> **Kapsam:** `agent-base/` monorepo (üretim yüzü) + `rule-based-
> engine/` repo kökü (paralel ağaç).
> **Kaynaklar:** `FOR_GROK.md` (824 satır), `FOR_CURSOR.md`
> (328 satır), `ÖZET.md`, `SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md`.

---

## 1. Mevcut Sistem Durumu (Kısa ve Net)

### 1.1 Klasör Yapısı ve İki Paralel Kod Tabanı

Proje **iki paralel ağaç** taşıyor:

| Katman | Konum | Rol |
|--------|-------|-----|
| **Üretim monorepo** | `agent-base/` | Tek Docker container, all-in-one (FastAPI + PHP UI + worker'lar), supervisord 7 process |
| **Paralel kök ağacı** | `rule-based-engine/*.py` | ~67 flat Python modülü + `index.html` geliştirme dashboard'u |

İki ağaç **benzer ama eşit olmayan** orchestration kodunu taşır
(~68 vs ~67 dosya). Tur 4 birleşiminde dosyalar `agent-base-api/`
altına **kopyalandı**, taşınmadı — bu yüzden senkronizasyon riski
canlı (değişiklik bir tarafa yapılıp diğerine yansımayabilir).

`agent-base/` iç yapı (özet):

```
agent-base/
├── docker-compose.yml, Dockerfile, README.md, Makefile
├── FOR_GROK.md, FOR_CURSOR.md, FOR_FINAL.md (bu dosya)
├── ÖZET.md, SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md
├── docker/ (supervisord 7 process, nginx)
├── agent-base-api/
│   ├── app/main.py                  (birleşik FastAPI entrypoint)
│   ├── app/                         (auth, social_media, runtime/orchestrator)
│   ├── langgraph_engine/            (state, nodes, runtime)
│   ├── tool_adapters/               (instagram, facebook, tiktok stub)
│   └── 68 flat orchestration modülü (listener, structured_rule, ...)
└── php-ui/
    ├── public/index.php             (front controller)
    ├── views/layout.php             (sidebar)
    ├── views/system_admin.php       (AI Operatör Merkezi)
    ├── views/timeline/_rules_toolbar.php
    └── public/assets/{css,js}/      (timeline-rules.css, timeline-page-rules.js)
```

### 1.2 Sidebar ve Zaman Tüneli Alt Sayfaları

`php-ui/views/layout.php` üzerinden:

| Grup | Alt öğeler |
|------|-----------|
| **Sosyal Medya** | Takvim, Etiketler, Şablonlar (post), Onay Bekleyenler |
| **Kampanya Yönetimi** | Takvim, Şablonlar (banner), Onay Bekleyenler |
| **Sistem Yöneticisi** | Tek link → AI Operatör Merkezi |
| **Zaman Tüneli** | 22 alt sayfa (slug bazlı) |
| **Ayarlar** | Hesap, Çalışma Alanı, Yapay Zeka, Anahtarlar, Otomasyon, Güvenlik |

**Tur 5'te:** "Kurallar" ayrı sekmesi sidebar'dan **kaldırıldı**.
`/kurallar` ve `/rules` route'ları `/page/timeline/all` adresine
302 redirect oluyor.

**Zaman Tüneli slug'ları (22 adet)** ve event_type filtreleri:

| Filtreli (anlamlı bağlam) | Filtresiz (boş prefix) |
|---------------------------|------------------------|
| `store` (store.*), `orders` (order.*), `products` (product.*), `stock` (stock.*), `reviews` (review.*), `questions` (customer.question), `messages` (customer.question), `campaigns` (campaign.*), `ads` (banner.+sales.), `banners` (banner.*), `flash-sales`/`discounts` (sales.*), `delivery` (shipping.+order.shipped), `returns` (order.cancelled) | `all` (kasten boş), `coupons`, `staff`, `checkin-checkout`, `withdrawals`, `plugins`, `subscription`, `components` |

**14 slug anlamlı filtreye sahip**; 7-8 slug için panel var ama
event filtresi tanımlı değil.

### 1.3 Kurallar Altyapısı (LangGraph + Parser + Resolver + Templates)

| Bileşen | Konum | İşlevi |
|---------|-------|--------|
| **Şema** | `structured_rule.py` | Pydantic `StructuredRule`: trigger / timing / target / content / actions. 18 event, 10 channel, 14 template, 9 action_kind |
| **NL parse** | `nl_rule_parser.py` | Türkçe NL → kanonik kural (regex prefilter + LLM augmentation, Pydantic validation, fail-fast 422) |
| **Semantic resolver** | `semantic_entity_resolver.py` | "Çanakkale hesabı" → store_id (runtime resolve) |
| **Motor** | `langgraph_engine/{state, nodes, runtime}.py` | Dinamik StateGraph + SqliteSaver checkpoint + `interrupt_before=["approval"]` + `interrupt_after=["wait"]` |
| **Node kataloğu** | `langgraph_engine/nodes.py` (10 node) | supervisor, wait, content_generator, risk_analyzer, approval_gate, publisher, monitor, notify_customer, create_coupon, finalize |
| **Orchestration** | `structured_rule_engine.py`, `orchestration_api.py` | CRUD + olay-kural eşleme + graph kickoff + ~96 HTTP endpoint |
| **Şablonlar** | `rule_templates.py` | 9 hazır sistem şablonu (anneler_gunu, yilbasi, stok düşüşü, ...) |
| **Öğrenme** | `rule_learning.py` | Her execution sonrası health_score güncelleme |
| **Conversational edit** | `conversational_rule_edit.py` | Sohbet-ile kural düzenleme (`/chat-edit/preview` + `/apply`) |
| **Conflict** | `conflict_resolver.py` | Çakışma tespiti + öneri feed |
| **Adapter'lar** | `tool_adapters/{instagram,facebook,tiktok}.py` | Gerçek Graph API publish; `SOCIAL_PUBLISH_LIVE` flag arkasında |

**UI tarafı:**
- `php-ui/views/timeline/_rules_toolbar.php` — contextual panel markup
- `php-ui/public/assets/js/timeline-page-rules.js` — backend-bağlı CRUD + composer + preview stepper + şablon grid + son yürütme chip + conflict banner
- `php-ui/public/assets/css/timeline-rules.css` — premium CSS (renk paleti, toast, skeleton, optimistic UI)

### 1.4 CrewAI Durumu

| Bölge | Durum |
|-------|-------|
| **agent-base-api `pyproject.toml`** | CrewAI bağımlılığı **yok**. `langgraph + cryptography + native OpenAI/Gemini` var. |
| **`app/agents/manager/agent_factory.py`** | CrewAI Agent yerine **`LightAgent` dataclass**. |
| **`app/services/agent_runtime_service.py`** | Native chat completion (gemini/* veya openai). |
| **`tools.py`** (kök) | Kendi protocol-typed `BaseTool` (Tur 2'de CrewAI'dan ayrıldı). |
| **`crewai_worker.py`** (kök) | Deprecated shim → `task_executor.py`'a delegate eder; "DEPRECATED" log yazar. |
| **`app/agents/social_media_agent.py`, `pipeline/social_media_pipeline.py`** | Lazy try/except CrewAI import — paket yoksa graceful fallback ile direct service çağrısı. |
| **Eski dokümantasyon (`PROJECT_AUDIT.md`, kök `SON_DEGISIKLIKLER` Tur 2)** | Hâlâ CrewAI worker'dan bahsediyor — **kod ve doküman uyumsuzluğu**. |
| **Makefile target / deploy script isimleri** | Bazıları `crewai-worker` adı taşıyor olabilir. |

**Özet:** Kod düzeyinde CrewAI fiilen kaldırılmış; **isimsel / kalıtsal kalıntılar** ve eski dokümantasyon mevcut.

---

## 2. Benim Gerçek Vizyonum (Tam Netleştirilmiş)

Aşağıdaki maddeler operatörün operasyonel niyetidir; her birinin
mevcut karşılığı bir sonraki bölümde gap olarak işaretlenir.

### 2.1 Kurallar Sekmesi Tamamen Kalkacak
Sidebar'da, ana navigasyonda, alt menülerde **hiçbir yerde** "Kurallar" diye ayrı bir sayfa olmayacak. Operatör kural CRUD için doğrudan **bağlamsal** bir yere gidecek (Zaman Tüneli alt sayfası veya Sistem Yöneticisi sohbeti).

### 2.2 Kurallar Zaman Tüneli Altındaki Her Sayfaya Contextual Entegre
"Mağaza" sayfası **Mağaza Kuralları**'nı, "Ürünler" sayfası **Ürün Kuralları**'nı, "Reklamlar" **Reklam Kuralları**'nı, "Kampanyalar" **Kampanya Kuralları**'nı, "Stok" **Stok Kuralları**'nı gösterecek. Operatör hangi sayfada olduğunu **görsel olarak hisseder** — kuralları orada yönetir.

### 2.3 Her Sayfada O Sayfaya Özel Kural Paneli
Her panelin içinde:
- **Geniş NL textarea** (sayfaya özel placeholder, örn. "Stok 5 altına düştüğünde indirim postu hazırla")
- **Önizle butonu** (görsel stepper ile akış)
- **Şablonlardan seç** (sayfaya uyumlu şablonlar **öne çıkar**)
- **Kuralı Etkinleştir** (success toast + liste güncellenir)
- **Mevcut kurallar listesi** (başlık + tetik + kanal + şablon + sağlık skoru + son yürütme + AKTİF/PASİF toggle + sil)
- **Conflict banner** (çakışma varsa turuncu uyarı)

### 2.4 Şablon İsmi ile Kural Oluşturma
Operatör kural composer'ında **şablon adıyla** ifade kullanabilecek. Örnek:
> *"Mağaza oluştuğunda **/mers şablonu**nu kullanarak Instagram postu oluştur."*

Mevcut sistem şablonları (`anneler_gunu`, `yilbasi`, `kara_cuma`, ...) gibi **operatörün kendi özel şablonları** ("mers", "yaz_indirim_v2", ...) kayıtlı olacak. Sosyal medya şablonları ve kampanya banner şablonları **ayrı tutulacak**.

### 2.5 Sistem Yöneticisi = Güçlü AI Operatör Chat Merkezi
`/social-media/system-admin` sayfası:
- Büyük, sürekli açık chat alanı
- Multi-turn cognition + 4 AI modu (Analiz, Operasyon, Strateji, İçerik)
- Doğal Türkçe ile **kural oluştur, düzenle, sil, listele, sorgula**
- Conflict çözümü buradan (chat'ten) tek tıkla
- Business analytics + conversational rule edit tam çalışır

### 2.6 CrewAI Tamamen Kaldırılacak; Agent-base LangGraph ile Uyumlu
Tek execution intelligence: **LangGraph + structured rules**. agent-base'in eski CrewAI çağrı kalıntıları (shim, lazy import, Makefile target, dokümantasyon) tamamen temizlenecek. `task_executor.py` tek AI task tüketicisi olacak.

### 2.7 Premium, Modern, Operatör Dostu Arayüz
- Tailwind benzeri tutarlı renk paleti (slate/indigo/emerald/amber/rose/sky)
- 12-16px border-radius
- Micro-animasyonlar (hover lift, button press scale, toast slide)
- Skeleton loading + empty state
- Optimistic UI (toggle anında, hata olursa rollback)
- Responsive (mobile-first sticky composer)
- Kbd hint chip'leri (`⌘+Enter` ile hızlı önizle)

---

## 3. Mevcut Durum ile Vizyon Arasındaki Gap'ler

### 3.1 Vizyon Madde-Madde Statü Tablosu

| # | Vizyon Maddesi | Statü | Tamamlanma | Kalan İş |
|---|----------------|-------|------------|----------|
| 2.1 | Kurallar sekmesi tamamen kalksın | ✅ + ⚠️ | ~85% | php-ui'da yapıldı; ama kök `index.html` hâlâ "Kurallar" sekmesi içeriyor |
| 2.2 | Her timeline sayfasına contextual entegre | ✅ + ⚠️ | ~80% | 22 slug'tan 14'ünde anlamlı filter; 7-8 slug için event prefix boş |
| 2.3 | Her sayfada özel kural paneli (NL + önizle + şablon + liste) | ✅ | ~95% | Tamam; küçük cilalar (stepper, conflict, son yürütme) yapıldı |
| 2.4 | Şablon ismi ile kural ("/mers şablonu") | ⚠️ | ~50% | Sistem şablonları var (`rule_templates.py`); **operatörün kendi özel şablonu** yok (POST /rule-templates UI yok) |
| 2.5 | Sistem Yöneticisi AI Operatör chat merkezi | ✅ + ⚠️ | ~70% | Chat UI 1182 satır var; tool-binding (sohbetten doğrudan structured-rules API çağrısı) **eksik** |
| 2.6 | CrewAI tamamen kaldırılacak | ✅ + ⚠️ | ~90% | Bağımlılık ve runtime temiz; isim kalıntıları (shim, Makefile, eski docs) var |
| 2.7 | Premium, modern arayüz | ✅ + ⚠️ | ~85% | Tur 5 polish-2 ile UI canlandı; dark mode + tam mobile sticky + AI tool-binding bağlamı eksik |

### 3.2 En Kritik 5 Eksiklik (Önem Sırasıyla)

#### 1. İkili Kod Tabanı — Senkronizasyon Riski (Kritik)
`rule-based-engine/*.py` (67 modül + `index.html`) ve `agent-base/agent-base-api/*.py` (68 modül) **paralel ama bağımsız** evrim potansiyeli taşıyor. Şu an aynı durumda olduklarına dair garanti **yok**. Değişiklikler tek bir ağaca yapılırsa diğeri eskiyor.

#### 2. Üçlü Listener Yolu — Operasyonel Karmaşa (Kritik)
`listener.py` her event için **paralel üç path** deniyor:
- `structured_rule_engine` (LangGraph — yeni)
- `rule_engine.find_matching_rules` (legacy DSL)
- `autonomous_planner` (LLM yaratıcı)

Aynı event üçü tarafından da yakalanabilir. Operatör hangi yolun sonucunu gördüğünü ayırt etmekte zorlanır; çift iş riski var.

#### 3. AI Operatör Tool-Binding Eksik (Kritik)
Sistem Yöneticisi chat'inde "şu kuralı pasifleştir" denildiğinde **doğrudan API çağrısı yapılmıyor**. `conversational_rule_edit` endpoint'i hazır (`/chat-edit/preview` + `/apply`) ama chat agent'in sistem prompt'unda explicit tool-binding yok. Operatör chat üzerinden CRUD yapamıyor, sadece preview alabiliyor.

#### 4. Operatör Özel Şablonları Yok (Önemli)
`rule_templates.py` sabit kütüphane. "Mers şablonu" gibi kullanıcıya özel kayıt için:
- `POST /rule-templates` endpoint'i yok (CREATE)
- UI'da "Bu kuralı şablon olarak kaydet" butonu yok
- Workspace bazlı şablon scoping yok

Vizyon 2.4 yalnızca **mevcut sistem şablonu** seçimi düzeyinde çalışıyor; özel şablon eksik.

#### 5. Boş Filtreli Timeline Slug'ları (Orta)
7-8 timeline alt sayfasında (`coupons`, `staff`, `checkin-checkout`, `withdrawals`, `plugins`, `subscription`, `components`, `all`) event prefix'i tanımlı değil. Bu sayfalarda "contextual" panel görüntülenir ama o sayfaya özgü kural filtresi olmaz; tüm kurallar listelenir veya hiçbiri görünmez. "Bağlamsal" vaat zayıflar.

### 3.3 Daha Düşük Öncelikli Gap'ler

| Gap | Etki | Önem |
|-----|------|------|
| **Kök `index.html` dashboard** hâlâ "Kurallar" sekmesi içeriyor | Geliştirici dashboard'u operatör vizyonu ile uyumsuz | Orta |
| **Timeline sayfa içeriği placeholder** — `page.php` "Dinamik sayfa" yazıyor | Kural paneli dolu, iş feed'i boş — yarım hisli | Orta |
| **Dokümantasyon parçalı** — FOR_GROK / FOR_CURSOR / ÖZET / SON_DEGISIKLIKLER / kök audit dosyaları farklı evrim anları | Bilgi otoritesi belirsiz | Düşük |
| **Onay yönetimi iki yerde** — `/onay-bekleyenler` (sosyal) vs `approval_requests` (LangGraph) | Aynı mental model, iki URL | Düşük |
| **Mobile sticky composer + dark mode** | Tur 6 adayı | Düşük |
| **Vector retrieval (rule similarity)** | `embedding_placeholder` Tur 1'de eklenmişti, kullanılmıyor | Düşük |
| **TikTok gerçek publish** | Stub | Düşük |

---

## 4. Öneriler

> **Not:** Bu bölüm kod yazma veya refactor talimatı **içermez**.
> Yalnızca ürün ve mimari öncelik sıralaması sunar.

### 4.1 Yüksek Öncelikli Adımlar (Vizyonun Tamamlanması İçin)

**1. Tek Canonical Kod Tabanı Kararı**
- Üretim yüzü olarak `agent-base/` netleştirilmeli.
- Kök `rule-based-engine/*.py` **arşiv** veya **dev-only** olarak işaretlenmeli.
- Kök `index.html` ya `agent-base/agent-base-api/index.html`'e taşınmalı ve "Kurallar" sekmesi vizyonla hizalanmalı, ya da operatör erişimi kesilmeli.
- **Etki:** Senkronizasyon riski sıfırlanır; tek değişiklik tek yere.

**2. Listener Yol Birleştirme Politikası**
- `structured_rule_engine` **birincil yol** olarak ilan edilmeli.
- Legacy `rule_engine.find_matching_rules` ya **sadece eski kayıtlı kurallar için** çalışsın, ya tamamen migrate edilsin.
- `autonomous_planner` için açık politika: "Ne zaman tetiklenir? Operatör görür mü?". `CRITICAL_FALLBACK=0` varsayılan kararı **dokümante** edilmeli.
- **Etki:** Operatör için "hangi yol çalıştı" netliği; çift iş riski biter.

**3. AI Operatör Tool-Binding (Sistem Yöneticisi Chat)**
- Chat agent prompt'una `structured-rules`, `chat-edit`, `conflicts` endpoint'leri tool olarak tanımlanmalı.
- "Bu kuralı pasifleştir" → chat doğrudan `PATCH .../enabled?enabled=false` çağırsın.
- Timeline contextual panel ile **oturum bağlamı paylaşımı** (chat'te bir kural üzerinde konuşuluyorsa panel o kurala scroll etsin).
- **Etki:** "Tüm kuralları doğal Türkçe ile yönet" vizyonu **gerçekten** tamamlanır.

**4. Operatör Özel Şablon Katmanı**
- `rule_templates` tablosuna `user_id`, `workspace_id`, `is_user_template` kolonları.
- `POST /rule-templates` (CREATE) + UI'da "Bu kuralı şablon olarak kaydet" butonu.
- NL parser composer'da `/mers` ya da `@mers` syntax'ı ile çağrılabilsin.
- **Etki:** Operatör kişisel iş akışı kütüphanesi oluşturur; vizyon 2.4 tam çalışır.

**5. Timeline Slug-Event Eşlemesini Tamamlama veya Sadeleştirme**
- 7-8 boş slug için ya gerçek event_type namespace tanımlanmalı (ör. `coupons` → `coupon.*`), ya sidebar'dan **kaldırılmalı / birleştirilmeli**.
- "Bütün timeline'da contextual kural" iddiası ya tam, ya net şekilde **kısmen** sunulmalı.
- **Etki:** Operatör beklentisi ile gerçekleşen davranış uyumlanır.

### 4.2 Orta Öncelikli Adımlar

**6. Dokümantasyon Konsolidasyonu**
- `FOR_FINAL.md` (bu dosya) tek otorite olarak ilan edilmeli; FOR_GROK, FOR_CURSOR, ÖZET, kök `PROJECT_AUDIT` dosyaları **arşivlenmeli** veya FOR_FINAL'a referans olmalı.
- Eski CrewAI anlatımları güncellenmeli (`kod ile doküman uyumu`).

**7. CrewAI Kalıntılarının Operasyonel Temizliği**
- `crewai_worker.py` shim dosyasının silinmesi.
- Makefile target'larındaki `crewai-worker` referansları temizlenmeli.
- `app/agents/social_media_agent.py` + `pipeline/social_media_pipeline.py` içindeki lazy try/except CrewAI import'ları (her ne kadar graceful olsa da) tamamen kaldırılmalı.
- **Etki:** Operatör ve geliştirici zihninde "CrewAI yok" netliği.

**8. Timeline Sayfa İçeriğinin Doldurulması**
- `page.php` "Dinamik sayfa" placeholder yerine gerçek event feed + entity detay yerleştirilmeli.
- Kural paneli + iş feed'i birlikte "Zaman Tüneli" algısı verir.

### 4.3 Düşük Öncelikli / Tur 6+

- Dark mode (CSS variables hazır, `prefers-color-scheme` query yeter)
- Mobile sticky composer
- Vector similarity (`embedding_placeholder` aktive etme)
- TikTok real publish
- Visual drag-drop rule builder
- Time-travel checkpoint viewer
- Per-channel `SOCIAL_PUBLISH_LIVE`
- Cost/quota tracking (org-level)
- Org/role-aware kural görünürlüğü

### 4.4 Başarı Kriterleri (Vizyon Tamamlandığında)

Aşağıdakilerin **tümü** gerçekleştiğinde vizyon %100 tamamlanmıştır:

- [ ] Sidebar'da hiçbir "Kurallar" girişi yok (php-ui ✓ / kök index.html ✗)
- [ ] Tüm timeline alt sayfalarında anlamlı event filter + bağlamsal placeholder
- [ ] Production execution tek motor: LangGraph structured rules
- [ ] CrewAI ismi ve worker süreci deploy'da yok; `task_executor` tek tüketici
- [ ] Operatör chat'ten **uçtan uca** kural CRUD: oluştur, listele, düzenle, pasifleştir, sil
- [ ] Operatör kendi özel şablonunu kaydedebilir, NL'de adıyla çağırabilir
- [ ] Örnek akış: *mağaza oluştu → şablon seç → önizle → etkinleştir → event geldi → onay → yayın* — uçtan uca **tek sayfa hissi** ile yürür
- [ ] Premium UI: toast, optimistic UI, skeleton, hover-lift, responsive, dark mode

---

## 5. Hızlı Referans

### 5.1 Önemli Dosyalar

| Alan | Dosya |
|------|-------|
| PHP sidebar | `php-ui/views/layout.php` |
| Contextual rule panel | `php-ui/views/timeline/_rules_toolbar.php` |
| Panel JS | `php-ui/public/assets/js/timeline-page-rules.js` |
| Premium CSS | `php-ui/public/assets/css/timeline-rules.css` |
| AI Operatör chat UI | `php-ui/views/system_admin.php` + `timeline-store-automation.js` |
| Route / redirect | `php-ui/public/index.php` |
| FastAPI entry | `agent-base-api/app/main.py` |
| API router (~96 endpoint) | `agent-base-api/orchestration_api.py` |
| Event dinleyici | `agent-base-api/listener.py` (kökte aynı isimle ikiz) |
| LangGraph runtime | `agent-base-api/langgraph_engine/runtime.py` |
| Kural şeması | `agent-base-api/structured_rule.py` |
| NL parse | `agent-base-api/nl_rule_parser.py` |
| Şablonlar | `agent-base-api/rule_templates.py` |
| Eski dev dashboard | `rule-based-engine/index.html` (repo kökü) |
| Önceki briefler | `FOR_GROK.md`, `FOR_CURSOR.md` |

### 5.2 Mevcut Kaynak Dosya Boyutları

| Dosya | Satır | Boyut |
|-------|-------|-------|
| `FOR_GROK.md` | 824 | 38 KB |
| `FOR_CURSOR.md` | 328 | 19 KB |
| `FOR_FINAL.md` (bu) | ~330 | ~17 KB |
| `ÖZET.md` | 718 | 34 KB |
| `SON_DEGISIKLIKLER_VE_GENEL_SISTEM.md` | 898 | 40 KB |
| `timeline-rules.css` | 678 | premium UI |
| `timeline-page-rules.js` | ~720 | backend-bağlı CRUD |
| `_rules_toolbar.php` | 134 | sade markup |

---

## 6. Sonuç (Tek Cümlede)

> **Vizyon %85-90 oranında uygulanmış durumda.** Kalan kritik 5
> gap (ikili kod tabanı, üçlü listener yolu, AI Operatör tool-
> binding, operatör özel şablonları, boş filtreli timeline
> slug'ları) Tur 6'da hedef alınmalı; bunlar tamamlandığında
> operatör "tek AI operasyon merkezi" deneyimine kavuşur ve
> sistem tam vizyon hizasına gelir.

---

*Bu doküman karar verme amaçlıdır. Uygulama kararları FOR_GROK
ve FOR_CURSOR'da ifade edilen teknik gerçeklik ile bu dosyadaki
öncelik sırasının çakışmasıyla alınmalıdır.*
