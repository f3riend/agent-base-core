# Agent Base — Resim Üretme Sistemi Mimarisi

> Tarih: 2026-05-28
> Kapsam: Manuel UI vs otomatik LangGraph kural akışı, referans öncelik sırası.

---

## Manuel UI Akışı (Takvimden)

Kullanıcı takvime sağ tıklar → "Instagram Hikaye/Post Oluştur" seçer
→ İçerik oluştur modali açılır (`social-media-studio-modal.js`)
→ Sol menüden referans resimler seçilir (checkbox — her biri ayrı referans)
→ Şablon seçilir (layout rehberi)
→ "Üret" basılır (`social-media-composer-actions.js:252`)
→ `POST /social-media/flow/generate-from-reference`
   payload:
   ```
   {
     reference_image_url:  seçilen resimler[0],   ← ürün fotoğrafı BİRİNCİL
     reference_image_urls: [tüm seçilen resimler],
     prompt:               şablon.prompt + kullanıcı notu,
     output_size:          "story" | "feed"
   }
   ```
→ `_sync_generate_from_reference_task()` ([app/api/social_media.py:339](agent-base/agent-base-api/app/api/social_media.py#L339))
→ `generate_images_from_reference()` → FAL/OpenAI
→ Görsel üretilir, kullanıcı önizler, revize edebilir (`/flow/revise-image`)

**Önemli:** UI'da kullanıcı *ürün fotoğrafını* checkbox ile seçer ve o resim `reference_image_url` (singular, **birincil**) olarak yollanır. Şablon prompt'a katkı yapar ama görsel olarak BİRİNCİL pozisyonda ürün vardır.

---

## Otomatik Kural Akışı (LangGraph)

Event gelir (`product.created` / `store.created`)
→ `listener.py:process_event` yakalar
→ `structured_rule_engine.trigger_rules_for_event(user_id=None)` — multi-tenant tüm aktif kurallar taranır
→ `runtime.start_execution(rule, event)`
→ `supervisor → content_generator_node`:
   1. `_fetch_template_from_mysql(template_name, channel)` — MySQL `content_templates`'tan şablon getirir, ölü URL'leri filtreler
   2. `_ai_generate_caption(event_payload, rule_meta, template_data, channel)` — OpenAI gpt-4o-mini ile Türkçe caption
   3. `_build_image_prompt(event_payload, template_data, channel, template)` — şablonun `prompt` alanını birinci sıra alır
   4. **Event'ten görsel topla:**
      - `product_image_url`  ← `items.image_url` (fake_ai_api.db)
      - `product_image_urls` ← `items.images_json` (fake_ai_api.db)
      - `store_logo_url`     ← `stores.logo_url` (fake_ai_api.db)
   5. `_generate_image_via_pipeline(prompt, reference_image_url=şablon, product_image_url=ürün, store_logo_url=logo, channel, output_size)` ([nodes.py:478](agent-base/agent-base-api/langgraph_engine/nodes.py#L478))
      - Tüm URL'ler `_check_url_alive` ile filtrelenir
      - **all_refs öncelik sırası** (DÜZELTİLDİ — aşağıda)
      - `_sync_generate_images_task(reference_image_url=primary_ref, reference_image_urls=all_refs, ...)` → FAL/OpenAI
→ `risk_analyzer → approval_gate ⏸` (insan onayı)
→ Onay → `publish_post` veya `publish_story` (story rule ise `_is_story_rule` 3-yol)
→ MySQL `scheduled_posts` / `story_scheduled_posts` (kart) + `listener.db.scheduled_entries` (takvim)
→ UI takvim kartı `/social-data/collections/scheduled_posts` ile çekip gösterir

---

## Öncelik Sırası (DOĞRU mantık)

Manuel UI'da kullanıcı ürün fotoğrafını referans olarak seçer → ürün BİRİNCİL referans. Otomatik akışta da aynı öncelik olmalı:

| Sıra | Görsel | Rol |
|------|--------|-----|
| 1 | **Ürün fotoğrafı** | BİRİNCİL — ne üretileceğini belirler (ürünün kendisi) |
| 2 | **Mağaza logosu** | İKİNCİL — tasarıma dahil edilir (köşeye logo) |
| 3 | **Şablon görseli** | LAYOUT REHBERİ — tasarım yapısını/stilini belirler |

---

## Mevcut Sorun (DÜZELTİLDİ)

### Eski davranış (YANLIŞ)

`_generate_image_via_pipeline`'da `_push` çağrı sırası:
```python
_push(reference_image_url)   # ŞABLON görseli   ← 1. sırada
if product_image_urls:
    for u in product_image_urls:
        _push(u)              # Ürün fotoğrafı   ← 2. sırada
_push(store_logo_url)         # Logo            ← 3. sırada
```

Sonuç: `all_refs = [şablon_görseli, ürün_fotoğrafı, logo]` → `primary_ref = şablon`
- AI şablon görselini (örn. berjer fotoğrafı) birincil referans alıyor
- Ürün fotoğrafı (Aula F75 klavye) ikincil kalıyor
- Üretilen görsel berjer'i andırıyor, klavye ya yok ya da küçük

### Yeni davranış (DOĞRU)

`all_refs` aşağıdaki sırada doldurulur:
```python
# 1. Ürün fotoğrafı — BİRİNCİL
if product_image_urls:
    for u in product_image_urls:
        _push(u)
elif product_image_url:
    _push(product_image_url)

# 2. Mağaza logosu
_push(store_logo_url)

# 3. Şablon görseli — LAYOUT REHBERİ
_push(reference_image_url)
if reference_image_urls:
    for entry in reference_image_urls:
        _push(entry)
```

Sonuç: `all_refs = [ürün, logo, şablon]` → `primary_ref = ürün`
- AI ürün fotoğrafını birincil referans alıyor
- Şablon layout/stil rehberi olarak kullanılıyor
- Üretilen görsel ürün-merkezli, şablon tasarımına uygun

---

## Prompt Zenginleştirme

Görsel üretim çağrısının prompt'ına AI'a açık talimat eklenir:

| Şart | Eklenen cümle |
|------|---------------|
| Ürün fotoğrafı varsa | "The product image is the PRIMARY subject — feature it prominently in the design" |
| Şablon görseli varsa | "Use the template image as layout/design reference only — adapt its style and structure for the product" |
| Logo varsa | "Include the store logo subtly in the design" |

---

## Dosya Referansları

| Konu | Dosya | Satır |
|------|-------|-------|
| Manuel görsel üretim helper | `agent-base-api/app/api/social_media.py` | 339 (`_sync_generate_from_reference_task`) |
| Manuel görsel üretim helper (text-only) | `agent-base-api/app/api/social_media.py` | 226 (`_sync_generate_images_task`) |
| Manuel revize | `agent-base-api/app/api/social_media.py` | 262 (`_sync_revise_image_task`) |
| Composer JS | `agent-base-api/../php-ui/public/assets/js/social-media-composer-actions.js` | 252 (`flow/generate-from-reference` çağrısı) |
| Otomatik pipeline | `agent-base-api/langgraph_engine/nodes.py` | 478 (`_generate_image_via_pipeline`) |
| Event görsel toplama | `agent-base-api/langgraph_engine/nodes.py` | `content_generator_node` içinde |
| DB görsel helper'lar | `agent-base-api/resource_service.py` | 218 (`fetch_item_images`, `fetch_store_logo`, `fetch_store_banner`) |
| URL alive check | `agent-base-api/langgraph_engine/nodes.py` | 232 (`_check_url_alive`) |
| Şablon fetch | `agent-base-api/langgraph_engine/nodes.py` | 259 (`_fetch_template_from_mysql`) |
| Caption üretimi | `agent-base-api/langgraph_engine/nodes.py` | `_ai_generate_caption` |
| `_sync_generate_images_task` (alt katman) | `agent-base-api/app/services/content_service.py` | `generate_images_from_reference`, `generate_images` |
