"""
Türkçe doğal dil → StructuredRule parser.

Akış:
    1) Deterministic prefilter — yaygın kalıpları (zaman ifadeleri, kanal,
       şablon, event tetik) regex/keyword ile yakala. Bu, LLM olmadan da
       çoğu örnek için yeterli temel doldurur.
    2) LLM ince ayar — OpenAI gpt-4o-mini'ye prefilter'ın bulgularını ve
       ham metni vererek Pydantic schema'sına uygun JSON üretmesini iste.
    3) Validation — sonucu StructuredRule(**...) ile validate et. Hata
       varsa parse_confidence düşürülür ve missing_fields doldurulur.

Bu hibrit yaklaşım önemli çünkü:
    - LLM down/keysiz çalışırken bile çoğu kural makul şekilde parse edilir.
    - LLM ile daha akıcı dil yapıları (örn. "Çanakkale'deki yeni mağazalar
      için") yakalanır.
    - Her zaman Pydantic ile son doğrulama vardır — runtime'a şüpheli
      yapılar ulaşmaz.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from structured_rule import (
    ACTION_KINDS,
    CHANNELS,
    CONTENT_TEMPLATES,
    NODE_TYPES,
    RULE_MODULES,
    TRIGGER_EVENT_TYPES,
    ActionStep,
    Condition,
    ContentSpec,
    GraphDefinition,
    NodeDefinition,
    StructuredRule,
    TargetSpec,
    TimingSpec,
    TriggerSpec,
    empty_rule_template,
    utcnow_iso,
)


# ---------------------------------------------------------------------------
# Deterministic prefilter
# ---------------------------------------------------------------------------


# Türkçe event → canonical event_type mapping.
_EVENT_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\byeni\s+ma[ğg]aza\b|\bma[ğg]aza\s+olu[şs]\w*\b|\bma[ğg]aza\s+a[çc][ıi]l\w*\b", re.IGNORECASE), "store.created"),
    (re.compile(r"\bma[ğg]aza\s+g[üu]nce\w*\b", re.IGNORECASE), "store.updated"),
    (re.compile(r"\bma[ğg]aza\s+sil\w*\b|\bma[ğg]aza\s+kapan\w*\b", re.IGNORECASE), "store.deleted"),
    (re.compile(r"\byeni\s+[üu]r[üu]n\b|\b[üu]r[üu]n\s+eklen\w*\b|\b[üu]r[üu]n\s+olu[şs]\w*\b", re.IGNORECASE), "product.created"),
    (re.compile(r"\b[üu]r[üu]n\s+g[üu]nce\w*\b", re.IGNORECASE), "product.updated"),
    (re.compile(r"\byeni\s+sipari[şs]\b|\bsipari[şs]\s+olu[şs]\w*\b", re.IGNORECASE), "order.created"),
    (re.compile(r"\bkargo\w*\s+(?:gecik\w*|geç\w*)\b", re.IGNORECASE), "shipping.delayed"),
    (re.compile(r"\bstok\w*\s+(?:de[ğg]i[şs]\w*|g[üu]ncel\w*)\b", re.IGNORECASE), "stock.updated"),
    (re.compile(r"\bolumsuz\s+yorum\w*\b|\bnegatif\s+yorum\w*\b|\bk[öo]t[üu]\s+yorum\w*\b", re.IGNORECASE), "review.negative"),
    (re.compile(r"\byorum\s+gel\w*\b|\byeni\s+yorum\b", re.IGNORECASE), "review.created"),
    (re.compile(r"\bm[üu][şs]teri\s+sor\w*\b|\bsoru\s+gel\w*\b", re.IGNORECASE), "customer.question"),
    (re.compile(r"\byeni\s+kampanya\b|\bkampanya\s+ba[şs]la\w*\b|\bkampanya\s+olu[şs]\w*\b", re.IGNORECASE), "campaign.created"),
    (re.compile(r"\bbanner\w*\s+(?:g[üu]ncel\w*|de[ğg]i[şs]\w*)\b", re.IGNORECASE), "banner.updated"),
    (re.compile(r"\byeni\s+banner\b|\bbanner\s+olu[şs]\w*\b|\bbanner\s+eklen\w*\b", re.IGNORECASE), "banner.created"),
    (re.compile(r"\byeni\s+hikaye\b|\bhikaye\s+olu[şs]\w*\b|\bhikaye\s+payla[şs]\w*\b|\bstory\s+olu[şs]\w*\b", re.IGNORECASE), "story.created"),
    (re.compile(r"\byeni\s+kupon\b|\bkupon\s+olu[şs]\w*\b|\bkupon\s+üretil\w*\b|\bkupon\s+verildi\w*\b", re.IGNORECASE), "coupon.created"),
    (re.compile(r"\bsat[ıi][şs]\w*\s+de[ğg]i[şs]\w*\b", re.IGNORECASE), "sales.updated"),
    (re.compile(r"%\s*\d+.*?indirim|indirim.*?%\s*\d+", re.IGNORECASE), "sales.updated"),
)


# Türkçe zaman ifadeleri → saniye.
_TIME_PATTERNS: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(r"(\d+)\s*(?:dakika|dk)\s*sonra", re.IGNORECASE), 60),
    (re.compile(r"(\d+)\s*saat\s*sonra", re.IGNORECASE), 3600),
    (re.compile(r"(\d+)\s*g[üu]n\s*sonra", re.IGNORECASE), 86400),
    (re.compile(r"(\d+)\s*hafta\s*sonra", re.IGNORECASE), 604800),
    (re.compile(r"(\d+)\s*ay\s*sonra", re.IGNORECASE), 2592000),
)

# "Anında", "hemen", "şimdi" → 0 saniye.
_IMMEDIATE_RE = re.compile(r"\b(?:hemen|an[ıi]nda|[şs]imdi|derhal)\b", re.IGNORECASE)


# Kanal anahtar kelimeleri.
_CHANNEL_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("instagram hikaye", "story"),
    ("instagram story",  "story"),
    ("hikaye",           "story"),
    ("story",            "story"),
    ("instagram", "instagram"),
    ("facebook",  "facebook"),
    ("banner",    "banner"),
    ("kupon",     "coupon"),
    ("coupon",    "coupon"),
    ("e-posta",   "email"),
    ("eposta",    "email"),
    ("email",     "email"),
    ("sms",       "sms"),
    ("trendyol",  "trendyol"),
    ("shopify",   "shopify"),
    ("sss",       "faq"),
    ("faq",       "faq"),
    ("destek",    "support"),
)


# Şablon anahtar kelimeleri — pre-defined holiday/season templates.
_TEMPLATE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("anneler g",      "anneler_gunu"),
    ("babalar g",      "babalar_gunu"),
    ("y[ıi]lba[şs][ıi]", "yilbasi"),
    ("ramazan",        "ramazan"),
    ("kurban",         "kurban_bayrami"),
    ("yaz indirim",    "yaz_indirim"),
    ("k[ıi][şs] indirim", "kis_indirim"),
    ("kara cuma",      "kara_cuma"),
    ("black friday",   "kara_cuma"),
    ("yeni [üu]r[üu]n lansman", "yeni_urun_lansman"),
    ("magaza a[çc][ıi]l", "magaza_acilis"),
    ("ma[ğg]aza a[çc][ıi]l", "magaza_acilis"),
    ("te[şs]ekk[üu]r",  "tesekkur"),
    ("[öo]z[üu]r",      "ozur"),
    ("[öo]zel indirim", "ozel_indirim"),
)


# "Yapılacak" eylem ifadeleri.
_ACTION_VERBS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bpayla[şs][ıi]m\s+yap\w*\b|\bpayla[şs]\w+\b|\b(?:gönder|paylas)\w*\b", re.IGNORECASE), "publish"),
    (re.compile(r"\bkupon\s+olu[şs]\w*\b|\bkupon\s+ver\w*\b", re.IGNORECASE), "create_coupon"),
    (re.compile(r"\bm[üu][şs]teriye\s+bildir\w*\b|\bbilgilendir\w*\b|\bhaber\s+ver\w*\b", re.IGNORECASE), "notify_customer"),
    (re.compile(r"\btakip\s+et\w*\b|\bizle\w*\b|\bg[öo]zlemle\w*\b|\bperformans\s+izle\w*\b", re.IGNORECASE), "monitor"),
    (re.compile(r"\briski\s+kontrol\b|\briski\s+de[ğg]erlendir\w*\b", re.IGNORECASE), "risk_check"),
    (re.compile(r"\bonay\w*\s+al\w*\b|\bonayla\w*\b", re.IGNORECASE), "approval"),
    (re.compile(r"\b(?:i[çc]erik|metin|caption|banner)\s+olu[şs]\w*\b|\b(?:i[çc]erik|metin)\s+[üu]ret\w*\b", re.IGNORECASE), "generate_content"),
)


# Account / şehir handle ipucu — "Çanakkale hesabında" gibi.
_HANDLE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(?P<handle>[A-Za-zÇĞİÖŞÜçğıöşü0-9_]+)\s+hesab[ıi]n[dt]a", re.IGNORECASE),
    re.compile(r"@(?P<handle>[A-Za-z0-9_\.]+)", re.IGNORECASE),
)


# --- Yeni sözdizimi (Bölüm 2) ---

# @hesap                — hedef hesap (birden fazla)
# @magaza:urun_adi      — spesifik mağaza + ürün
# @magaza?kategori      — spesifik mağaza + kategori filtresi
# @hesap kullanılınca   — yalnız @hesap pattern'i
#
# Sıralama: önce store/category formlarını yakala, sonra plain @hesap.
_REF_STORE_ITEM_RE = re.compile(
    r"@(?P<store>[A-Za-z0-9_\.çğıöşüÇĞİÖŞÜ]+):(?P<item>[A-Za-z0-9_\.\-çğıöşüÇĞİÖŞÜ]+)",
)
_REF_STORE_CATEGORY_RE = re.compile(
    r"@(?P<store>[A-Za-z0-9_\.çğıöşüÇĞİÖŞÜ]+)\?(?P<category>[A-Za-z0-9_\.\-çğıöşüÇĞİÖŞÜ]+)",
)
_REF_PLAIN_HANDLE_RE = re.compile(
    r"@(?P<handle>[A-Za-z0-9_\.çğıöşüÇĞİÖŞÜ]+)(?![:?])",
)

# /sablon_adi — şablon referansı.
_REF_TEMPLATE_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?P<template>[A-Za-z0-9_çğıöşüÇĞİÖŞÜ]+)",
)

# Yüzde / threshold ifadeleri: %40 üzeri, %40 üstü, %40 altı, %25 üzerinde
_PERCENT_THRESHOLD_RE = re.compile(
    r"%\s*(?P<pct>\d+)\s*(?P<dir>üzeri|üstü|üzerinde|üstünde|altı|altında|altinda)",
    re.IGNORECASE,
)

# Kategori formları:
#   "elektronik kategorisinde"       → cat
#   "kategori=elektronik"            → cat2
#   "kategori: elektronik"           → cat2
#   "kategori elektronik (ise|olursa|olduğunda)"  → cat3
#   "elektronik kategorisi"          → cat4
_CATEGORY_RE = re.compile(
    r"(?P<cat>[A-Za-zçğıöşüÇĞİÖŞÜ]+)\s+kategorisinde"
    r"|kategori\s*[:=]\s*(?P<cat2>[A-Za-zçğıöşüÇĞİÖŞÜ]+)"
    r"|\bkategori\s+(?P<cat3>[A-Za-zçğıöşüÇĞİÖŞÜ]+)(?:\s+(?:ise|olursa|olduğunda|oldugunda)\b|\b)"
    r"|(?P<cat4>[A-Za-zçğıöşüÇĞİÖŞÜ]+)\s+kategorisi\b",
    re.IGNORECASE,
)

# Yayın türü kombinasyonları: "post + story", "post ve story", "banner + post",
# "Instagram postu", "hikayesi", "hikayeye", "banner'ı"  — Türkçe ek formlarını da yakala.
# Eklenen: s[iı] (iyelik eki "si"/"sı" — "hikayesi", "postsı" vb.)
_PUBLISH_TYPES_RE = re.compile(
    r"\b(?P<kind>post|stor[iy]|hikaye|banner|reel)(?:u|i|y[ae]|ye|a|ı|s[iı]|'?[ıi])?\b",
    re.IGNORECASE,
)

# "senkron"/"senkronize" — paralel/birlikte ifadesi
_SYNC_RE = re.compile(r"\bsenkron(?:iz[eé])?\b|\bbirlikte\b|\bayn[ıi]\s+anda\b", re.IGNORECASE)


# Yayın türü → node_type eşlemesi (publish_post, publish_story, publish_banner).
_PUBLISH_TYPE_TO_NODE: dict[str, str] = {
    "post":    "publish_post",
    "story":   "publish_story",
    "hikaye":  "publish_story",
    "reel":    "publish_story",
    "banner":  "publish_banner",
}


# Modül tahmini için event_type → module eşlemesi.
_EVENT_TO_MODULE: dict[str, str] = {
    "store.created":     "social_media",
    "store.updated":     "social_media",
    "store.deleted":     "social_media",
    "store.rejected":    "social_media",
    "product.created":   "product",
    "product.updated":   "product",
    "product.deleted":   "product",
    "order.created":     "order",
    "order.shipped":     "order",
    "order.cancelled":   "order",
    "stock.updated":     "stock",
    "shipping.delayed":  "order",
    "review.created":    "review",
    "review.negative":   "review",
    "customer.question": "customer",
    "campaign.created":  "campaign",
    "banner.created":    "campaign",  # Bölüm 6
    "banner.updated":    "campaign",
    "sales.updated":     "campaign",
    "story.created":     "social_media",  # Bölüm 6
    "coupon.created":    "campaign",      # Bölüm 6
}


@dataclass
class _PrefilterResult:
    event_type: str | None = None
    delay_seconds: int = 0
    immediate: bool = False
    channel: str | None = None
    template: str | None = None
    account_handle: str | None = None
    detected_actions: list[str] = None
    # --- Yeni alanlar (Bölüm 2) ---
    target_accounts: list[str] = None          # @hesap1, @hesap2
    target_template: str | None = None          # /mers, /kara_cuma
    target_store: str | None = None             # @magaza:urun → store
    target_item: str | None = None              # @magaza:urun → item
    target_category: str | None = None          # @magaza?kategori veya "X kategorisinde"
    conditions: list[Condition] = None          # %X üzeri, kategori=X
    publish_types: list[str] = None             # post, story, banner
    is_parallel_publish: bool = False           # "post + story" veya "senkron"
    module: str | None = None                   # event_type'tan türetilir

    def __post_init__(self):
        if self.detected_actions is None:
            self.detected_actions = []
        if self.target_accounts is None:
            self.target_accounts = []
        if self.conditions is None:
            self.conditions = []
        if self.publish_types is None:
            self.publish_types = []


def _prefilter(text: str) -> _PrefilterResult:
    """Regex tabanlı kaba parser. LLM'siz çalışırken bile temel doldurma."""
    result = _PrefilterResult()
    if not text:
        return result

    # Event tipi
    for pattern, event_type in _EVENT_PATTERNS:
        if pattern.search(text):
            result.event_type = event_type
            break

    # Zaman ifadeleri
    if _IMMEDIATE_RE.search(text):
        result.immediate = True
        result.delay_seconds = 0
    else:
        for pattern, multiplier in _TIME_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    result.delay_seconds = int(m.group(1)) * multiplier
                    break
                except (ValueError, IndexError):
                    continue

    # Kanal
    lower = text.lower()
    for keyword, canonical in _CHANNEL_KEYWORDS:
        if keyword in lower:
            result.channel = canonical
            break

    # Şablon
    for keyword_re, template in _TEMPLATE_KEYWORDS:
        if re.search(keyword_re, lower):
            result.template = template
            break

    # Hesap handle
    for pattern in _HANDLE_PATTERNS:
        m = pattern.search(text)
        if m:
            result.account_handle = m.group("handle").strip().lower()
            break

    # Eylem fiilleri
    for pattern, action_kind in _ACTION_VERBS:
        if pattern.search(text):
            if action_kind not in result.detected_actions:
                result.detected_actions.append(action_kind)

    # --- Yeni sözdizimi (Bölüm 2) ---

    # 1) @magaza:urun (store + item)
    m_si = _REF_STORE_ITEM_RE.search(text)
    if m_si:
        result.target_store = m_si.group("store").strip().lower()
        result.target_item = m_si.group("item").strip().lower()

    # 2) @magaza?kategori (store + category filter)
    m_sc = _REF_STORE_CATEGORY_RE.search(text)
    if m_sc:
        # store_item zaten yakaladıysa store'u override etme — ikisi farklı
        if not result.target_store:
            result.target_store = m_sc.group("store").strip().lower()
        result.target_category = m_sc.group("category").strip().lower()

    # 3) plain @hesap — store:item ve store?category zaten match olanları
    #    skip et (re.sub ile çıkar, kalan üzerinde aranır).
    cleaned = _REF_STORE_ITEM_RE.sub("", text)
    cleaned = _REF_STORE_CATEGORY_RE.sub("", cleaned)
    for m in _REF_PLAIN_HANDLE_RE.finditer(cleaned):
        h = m.group("handle").strip().lower()
        if h and h not in result.target_accounts:
            result.target_accounts.append(h)
    # Tek-hesap legacy alanı: target_accounts varsa ilkini kullan
    if result.target_accounts and not result.account_handle:
        result.account_handle = result.target_accounts[0]

    # 4) /sablon_adi
    m_tmpl = _REF_TEMPLATE_RE.search(text)
    if m_tmpl:
        tmpl = m_tmpl.group("template").strip().lower()
        if tmpl:
            result.target_template = tmpl
            # target_template her zaman template olarak kullan (CONTENT_TEMPLATES dışındakiler de)
            result.template = tmpl

    # 5) %X üzeri/altı koşulu
    for m_pct in _PERCENT_THRESHOLD_RE.finditer(text):
        try:
            pct = int(m_pct.group("pct"))
        except (ValueError, TypeError):
            continue
        direction = (m_pct.group("dir") or "").lower()
        if direction.startswith(("üzer", "üst", "uzer", "ust")):
            op = ">="
        elif direction.startswith(("alt", "alti")):
            op = "<="
        else:
            op = ">="
        result.conditions.append(Condition(
            field="discount_percent",
            operator=op,
            value=pct,
        ))

    # 6) Kategori filtresi (NL form — "elektronik kategorisinde", "kategori X ise", ...)
    if not result.target_category:
        m_cat = _CATEGORY_RE.search(text)
        if m_cat:
            cat = (
                m_cat.group("cat")
                or m_cat.group("cat2")
                or m_cat.group("cat3")
                or m_cat.group("cat4")
                or ""
            ).strip().lower()
            # "kategori" kelimesi yanlışlıkla cat3'e düşmesin — anlamsız değer filtresi
            if cat and cat not in ("kategori", "kategorisinde", "olursa", "ise"):
                result.target_category = cat
                result.conditions.append(Condition(
                    field="category",
                    operator="==",
                    value=cat,
                ))

    # 7) Yayın türleri: post / story / banner
    # Regex farklı yazılışları yakalıyor (postu, story, hikaye, ...). Normalize et.
    _NORMALIZE = {
        "post": "post", "stori": "story", "story": "story",
        "hikaye": "story",
        "banner": "banner",
        "reel": "story",
    }
    for m_pub in _PUBLISH_TYPES_RE.finditer(text):
        kind = m_pub.group("kind").strip().lower()
        norm = _NORMALIZE.get(kind, kind)
        if norm not in result.publish_types:
            result.publish_types.append(norm)
    # "post + story" veya "senkron" varsa paralel publish
    if len(result.publish_types) >= 2:
        result.is_parallel_publish = True
    elif _SYNC_RE.search(text):
        result.is_parallel_publish = True

    # 8) Modül tahmini
    if result.event_type:
        result.module = _EVENT_TO_MODULE.get(result.event_type, "generic")

    return result


def _default_action_chain(prefilter: _PrefilterResult) -> list[ActionStep]:
    """Prefilter sinyallerinden makul bir eylem zinciri kur.

    Default güvenli akış: bekle → içerik üret → risk kontrol → onay → yayınla → izle
    """
    chain: list[ActionStep] = []

    if prefilter.delay_seconds > 0:
        chain.append(ActionStep(
            kind="wait",
            config={"delay_seconds": prefilter.delay_seconds},
        ))

    if "generate_content" in prefilter.detected_actions or prefilter.template:
        chain.append(ActionStep(
            kind="generate_content",
            config={
                "template": prefilter.template or "generic",
                "channel": prefilter.channel or "instagram",
            },
        ))

    if "create_coupon" in prefilter.detected_actions:
        chain.append(ActionStep(kind="create_coupon"))

    chain.append(ActionStep(kind="risk_check"))

    # Dış yayın varsa onay zorunlu.
    needs_approval = (
        "publish" in prefilter.detected_actions
        or "approval" in prefilter.detected_actions
        or (prefilter.channel in ("instagram", "facebook"))
    )
    if needs_approval:
        chain.append(ActionStep(kind="approval"))

    if "publish" in prefilter.detected_actions or prefilter.template:
        chain.append(ActionStep(kind="publish", config={"channel": prefilter.channel or "instagram"}))

    if "notify_customer" in prefilter.detected_actions:
        chain.append(ActionStep(kind="notify_customer"))

    if "monitor" in prefilter.detected_actions or "publish" in prefilter.detected_actions:
        chain.append(ActionStep(kind="monitor"))

    # En az bir eylem garanti
    if not chain:
        chain.append(ActionStep(kind="generate_content"))

    return chain


# ---------------------------------------------------------------------------
# LLM ince ayar
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = """Sen bir Türkçe iş kuralı parser'ısın. Operatörün
yazdığı doğal dil niyetini, JSON yapısına çevireceksin.

Çıktın SADECE geçerli JSON olmalı, başka metin yok. Aşağıdaki şemaya uy:

{
  "name": "Kural için 2-6 kelimelik Türkçe başlık",
  "trigger": {
    "event_type": "store.created | store.updated | store.deleted | store.rejected | product.created | product.updated | product.deleted | order.created | order.shipped | order.cancelled | shipping.delayed | stock.updated | review.created | review.negative | customer.question | campaign.created | banner.created | banner.updated | story.created | coupon.created | sales.updated",
    "filters": {}
  },
  "timing": {
    "delay_seconds": 0,
    "recurrence": "once | daily | weekly | monthly"
  },
  "target": {
    "account_handle": null,
    "entity_filters": {}
  },
  "content": {
    "template": "anneler_gunu | babalar_gunu | yilbasi | ramazan | kurban_bayrami | yaz_indirim | kis_indirim | kara_cuma | yeni_urun_lansman | magaza_acilis | tesekkur | ozur | ozel_indirim | generic",
    "channel": "story | instagram_story | instagram | facebook | banner | coupon | faq | support | email | sms | trendyol | shopify",
    "headline_hint": null
  },
  "actions": [
    {"kind": "wait | generate_content | risk_check | approval | publish | monitor | notify_customer | create_coupon | schedule_followup", "config": {}}
  ],
  "requires_approval": true,
  "missing_fields": []
}

DESTEKLENEN EVENT TİPLERİ ve kelime ipuçları:
  - store.created     → "yeni mağaza", "mağaza oluştu", "mağaza açıldı"
  - store.updated     → "mağaza güncellendi", "mağaza değişti"
  - product.created   → "yeni ürün", "ürün eklendi"
  - product.updated   → "ürün güncellendi"
  - order.created     → "yeni sipariş", "sipariş geldi"
  - order.shipped     → "sipariş kargolandı"
  - order.cancelled   → "sipariş iptal"
  - shipping.delayed  → "kargo gecikti"
  - stock.updated     → "stok değişti", "stok güncellendi"
  - review.created    → "yeni yorum", "yorum geldi"
  - review.negative   → "olumsuz yorum", "negatif yorum"
  - customer.question → "müşteri sordu", "soru geldi"
  - campaign.created  → "yeni kampanya", "kampanya başladı", "kampanya oluştu"
  - banner.created    → "yeni banner", "banner oluştu", "banner eklendi"
  - banner.updated    → "banner güncellendi", "banner değişti"
  - story.created     → "yeni hikaye", "yeni story", "hikaye paylaşıldı", "story oluştu"
  - coupon.created    → "yeni kupon", "kupon oluşturuldu", "kupon üretildi"
  - sales.updated     → "satış değişti", "indirim güncellendi"

ZORUNLU KURALLAR:
  1. Metinde "hikaye" veya "story" geçiyorsa → trigger.event_type = "story.created".
  2. Metinde "kupon" geçiyorsa → trigger.event_type = "coupon.created".
  3. Metinde "banner" geçiyor ve "oluş/eklen/yeni" varsa → trigger.event_type = "banner.created".
  4. Metinde "yorum" geçiyor ve "olumsuz/negatif" yoksa → trigger.event_type = "review.created".
  5. ASLA bilinmeyen bir kelimede store.created fallback yapma — yukarıdaki tabloya tam uy.
  6. Eğer hangisi olduğundan emin değilsen missing_fields'a "trigger.event_type" yaz ve store.created kullan (en son çare).

DİĞER KURALLAR:
  - "X gün sonra" → timing.delay_seconds = X * 86400.
  - "X saat sonra" → timing.delay_seconds = X * 3600.
  - "X dakika sonra" → timing.delay_seconds = X * 60.
  - Hesap handle "Çanakkale hesabında" veya "@deneme" gibi belirtilmişse target.account_handle = küçük harf.
  - Şablon adı tablodaki bir değere uyuyorsa content.template'i doğru seç; bilinmiyorsa "generic".
  - Kanal geçiyorsa content.channel'ı seç.
  - actions sırası: wait varsa önce, sonra generate_content, risk_check, approval, publish, monitor.
  - Eğer dış yayın (instagram/facebook/banner) varsa requires_approval=true.
  - Anlayamadığın alanları missing_fields listesine ekle.

Cevabın SADECE JSON, başka açıklama yok."""


def _llm_parse(text: str, prefilter: _PrefilterResult) -> dict[str, Any] | None:
    """LLM çağrısı — None döndürürse caller deterministic fallback'e gider."""
    if os.environ.get("NL_PARSER_USE_LLM", "1") == "0":
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(timeout=float(os.environ.get("NL_PARSER_TIMEOUT", "15")))
        hint = {
            "event_type_guess": prefilter.event_type,
            "delay_seconds_guess": prefilter.delay_seconds,
            "channel_guess": prefilter.channel,
            "template_guess": prefilter.template,
            "account_handle_guess": prefilter.account_handle,
            "detected_actions": prefilter.detected_actions,
        }
        completion = client.chat.completions.create(
            model=os.environ.get("NL_PARSER_MODEL", "gpt-4o-mini"),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"OPERATÖR METNİ:\n{text}\n\n"
                        f"DETERMINISTIC ÖN BULGULAR:\n{json.dumps(hint, ensure_ascii=False)}\n\n"
                        "Şimdi yukarıdaki şemaya uygun JSON üret."
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=600,
        )
        raw = (completion.choices[0].message.content or "").strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:
        print(f"[NL_PARSER] LLM call failed, falling back: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_rule(
    natural_language: str,
    *,
    user_id: int = 1,
    org_id: int | None = None,
    name_hint: str | None = None,
) -> StructuredRule:
    """Doğal Türkçe niyetten StructuredRule üret.

    Asla hata fırlatmaz; başarısız parse durumunda parse_confidence=0.0
    ve missing_fields dolu bir iskelet döndürür — operatör UI bunu
    "yarım kalmış, lütfen daha açık yaz" mesajıyla gösterebilir.
    """
    text = (natural_language or "").strip()
    if not text:
        return empty_rule_template("")

    prefilter = _prefilter(text)
    llm_json = _llm_parse(text, prefilter)

    # Prefilter'dan iskelet kur
    skeleton: dict[str, Any] = {
        "user_id": user_id,
        "org_id": org_id,
        "name": name_hint or _auto_name(text, prefilter),
        "natural_language": text,
        "trigger": {
            "event_type": prefilter.event_type or "store.created",
            "filters": {},
        },
        "timing": {"delay_seconds": prefilter.delay_seconds, "recurrence": "once"},
        "target": {
            "account_handle": prefilter.account_handle,
            "entity_filters": {},
        },
        "content": {
            "template": prefilter.template or "generic",
            "channel": prefilter.channel or "instagram",
        },
        "actions": [a.model_dump() for a in _default_action_chain(prefilter)],
        "requires_approval": True,
        "missing_fields": [],
        "parse_confidence": 0.55,
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        # --- Yeni alanlar (Bölüm 2) ---
        "module":            prefilter.module or "generic",
        "target_accounts":   list(prefilter.target_accounts),
        "target_template":   prefilter.target_template,
        "target_store":      prefilter.target_store,
        "target_category":   prefilter.target_category,
        "conditions":        [c.model_dump() for c in prefilter.conditions],
    }

    # LLM çıktısını over-merge — sadece geçerli alanları al
    if llm_json:
        skeleton = _merge_llm_into_skeleton(skeleton, llm_json, prefilter)
        skeleton["parse_confidence"] = 0.9

    # Eksik alanları tespit et
    missing: list[str] = []
    if not prefilter.event_type and not llm_json:
        missing.append("trigger.event_type")
    if not prefilter.template and skeleton["content"]["template"] == "generic":
        missing.append("content.template")
    if not prefilter.account_handle and not (llm_json and (llm_json.get("target") or {}).get("account_handle")):
        # Optional — only flag if rule explicitly mentions a city/handle hint
        pass
    skeleton["missing_fields"] = missing
    if missing and skeleton["parse_confidence"] > 0.7:
        skeleton["parse_confidence"] = 0.7

    # --- Dinamik graph (Bölüm 2) ---
    # Eğer prefilter yeni-stil sinyaller yakaladıysa (@hesap, /sablon, post/story,
    # koşul, paralel) graph_definition'ı doğrudan üret. Aksi halde None bırak —
    # StructuredRule.effective_graph_definition() runtime'da actions'tan
    # synthesize eder (eski davranış).
    has_new_syntax = bool(
        prefilter.target_accounts
        or prefilter.target_template
        or prefilter.target_store
        or prefilter.target_category
        or prefilter.publish_types
    )
    needs_dynamic = bool(
        prefilter.is_parallel_publish
        or prefilter.conditions
        or has_new_syntax
    )
    if needs_dynamic:
        try:
            gd = _build_graph_definition(prefilter, skeleton)
            skeleton["graph_definition"] = gd.model_dump()
        except Exception as exc:
            print(f"[NL_PARSER] graph_definition build failed, falling back: {exc}")
            skeleton["graph_definition"] = None

    try:
        return StructuredRule(**skeleton)
    except Exception as exc:
        print(f"[NL_PARSER] validation failed: {exc}")
        fallback = empty_rule_template(text)
        fallback.missing_fields = [f"validation_error: {exc}"]
        return fallback


def _build_graph_definition(
    prefilter: _PrefilterResult, skeleton: dict[str, Any]
) -> GraphDefinition:
    """Prefilter sinyallerinden dinamik GraphDefinition üret.

    Strateji:
        supervisor
        → (varsa) wait
        → (varsa) condition_check
        → generate_content
        → risk_check
        → (varsa) approval_gate ⏸
        → publish dalları (paralel veya tek)
        → finalize

    publish dalları:
        - is_parallel_publish=True ve publish_types=[post, story, ...]
          → fan-out: her publish_type ayrı node, hepsi parallel_with
          → finalize depends_on=hepsi
        - tek publish: tek node
    """
    accounts = list(prefilter.target_accounts)
    if not accounts and prefilter.account_handle:
        accounts = [prefilter.account_handle]

    channel = prefilter.channel or "instagram"
    template = prefilter.template or prefilter.target_template or "generic"

    nodes: list[NodeDefinition] = [
        NodeDefinition(node_id="supervisor", node_type="supervisor", params={}),
    ]
    interrupt_before: list[str] = []
    interrupt_after: list[str] = []

    # wait
    if prefilter.delay_seconds > 0:
        nodes.append(NodeDefinition(
            node_id="wait",
            node_type="wait",
            params={"delay_seconds": prefilter.delay_seconds},
        ))
        interrupt_after.append("wait")

    # condition_check
    if prefilter.conditions:
        nodes.append(NodeDefinition(
            node_id="condition_check",
            node_type="condition_check",
            params={
                "conditions": [c.model_dump() for c in prefilter.conditions],
                "match_mode": "all",  # tüm koşullar AND'lenir
            },
        ))

    # generate_content
    gc_params: dict[str, Any] = {"template": template, "channel": channel}
    if "banner" in (prefilter.publish_types or []):
        # Banner kuralı: content_generator 1600x704 üretsin, channel banner olsun
        gc_params["output_size"] = "campaign_banner"
        gc_params["channel"] = "banner"
    nodes.append(NodeDefinition(
        node_id="generate_content",
        node_type="generate_content",
        params=gc_params,
    ))

    # risk_check
    nodes.append(NodeDefinition(
        node_id="risk_check",
        node_type="risk_check",
        params={},
    ))

    # approval_gate
    # Güvenlik: dış yayın varsa (publish_post/story/banner) approval zorunlu —
    # LLM yanıtı requires_approval=False dönse bile override ediyoruz.
    has_external_publish = bool(prefilter.publish_types) or prefilter.is_parallel_publish
    requires_approval = bool(skeleton.get("requires_approval", True))
    if has_external_publish:
        requires_approval = True
    if requires_approval:
        nodes.append(NodeDefinition(
            node_id="approval_gate",
            node_type="approval_gate",
            params={
                "approval_type": _approval_type_for(prefilter, channel),
            },
        ))
        # interrupt_AFTER: approval_gate çalışsın (approval_requests'e kaydı oluştursun)
        # sonra graph dursun. Operatör UI'da kaydı görür, onaylayınca resume edilir
        # ve publisher node'ları çalışır. interrupt_before kullanılsa fonksiyon hiç
        # çağrılmaz ve approval kaydı oluşmaz.
        interrupt_after.append("approval_gate")

    # publish dalları
    publish_types = prefilter.publish_types or ["post"]
    publish_node_ids: list[str] = []
    if prefilter.is_parallel_publish and len(publish_types) >= 2:
        for ptype in publish_types:
            node_type = _PUBLISH_TYPE_TO_NODE.get(ptype)
            if not node_type:
                continue
            node_id = f"publish_{ptype}"
            if node_id in {n.node_id for n in nodes}:
                continue
            parallel_with = [
                f"publish_{p}" for p in publish_types
                if _PUBLISH_TYPE_TO_NODE.get(p) and p != ptype
            ]
            params: dict[str, Any] = {
                "channel": channel,
                "accounts": accounts,
                "template": template,
            }
            if prefilter.target_store:
                params["store"] = prefilter.target_store
            if prefilter.target_item:
                params["item"] = prefilter.target_item
            if prefilter.target_category:
                params["category"] = prefilter.target_category
            nodes.append(NodeDefinition(
                node_id=node_id,
                node_type=node_type,
                params=params,
                parallel_with=parallel_with,
            ))
            publish_node_ids.append(node_id)
    else:
        # Tek publish — varsayılan post veya verilen ilk publish_type
        ptype = publish_types[0] if publish_types else "post"
        node_type = _PUBLISH_TYPE_TO_NODE.get(ptype, "publish_post")
        params = {
            "channel": channel,
            "accounts": accounts,
            "template": template,
        }
        if prefilter.target_store:
            params["store"] = prefilter.target_store
        if prefilter.target_item:
            params["item"] = prefilter.target_item
        if prefilter.target_category:
            params["category"] = prefilter.target_category
        nodes.append(NodeDefinition(
            node_id=f"publish_{ptype}",
            node_type=node_type,
            params=params,
        ))
        publish_node_ids.append(f"publish_{ptype}")

    # finalize — paralel dallar varsa hepsini depends_on'a koy
    nodes.append(NodeDefinition(
        node_id="finalize",
        node_type="finalize",
        params={},
        depends_on=publish_node_ids if len(publish_node_ids) > 1 else [],
    ))

    return GraphDefinition(
        nodes=nodes,
        entry_node="supervisor",
        exit_node="finalize",
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )


def _approval_type_for(prefilter: _PrefilterResult, channel: str) -> str:
    """publish_types ve module'ye göre approval_type seç."""
    types = set(prefilter.publish_types or [])
    if "banner" in types:
        return "banner_approval"
    if "story" in types or "hikaye" in types:
        return "story_approval"
    if prefilter.module == "campaign":
        return "campaign_approval"
    if "post" in types or channel in ("instagram", "facebook"):
        return "post_approval"
    return "generic_approval"


def _merge_llm_into_skeleton(
    skeleton: dict,
    llm: dict,
    prefilter: "_PrefilterResult | None" = None,
) -> dict:
    """LLM JSON'dan güvenli üyeleri skeleton'a kopyala."""
    out = dict(skeleton)

    if isinstance(llm.get("name"), str) and llm["name"].strip():
        out["name"] = llm["name"].strip()[:120]

    if isinstance(llm.get("trigger"), dict):
        et = llm["trigger"].get("event_type")
        if isinstance(et, str) and et.strip().lower() in TRIGGER_EVENT_TYPES:
            out["trigger"]["event_type"] = et.strip().lower()
        if isinstance(llm["trigger"].get("filters"), dict):
            out["trigger"]["filters"] = llm["trigger"]["filters"]

    if isinstance(llm.get("timing"), dict):
        delay = llm["timing"].get("delay_seconds")
        if isinstance(delay, (int, float)) and delay >= 0:
            out["timing"]["delay_seconds"] = int(delay)
        rec = llm["timing"].get("recurrence")
        if rec in ("once", "daily", "weekly", "monthly"):
            out["timing"]["recurrence"] = rec

    if isinstance(llm.get("target"), dict):
        h = llm["target"].get("account_handle")
        if isinstance(h, str) and h.strip():
            out["target"]["account_handle"] = h.strip().lower()
        if isinstance(llm["target"].get("entity_filters"), dict):
            out["target"]["entity_filters"] = llm["target"]["entity_filters"]

    if isinstance(llm.get("content"), dict):
        # Prefilter /şablon yakaladıysa LLM'in template'i ezmesine izin verme.
        pre_template_locked = bool(prefilter and prefilter.target_template)
        t = llm["content"].get("template")
        if (
            not pre_template_locked
            and isinstance(t, str)
            and t.strip().lower() in CONTENT_TEMPLATES
        ):
            out["content"]["template"] = t.strip().lower()
        # Prefilter "story" kanalını yakaladıysa LLM ezmesin.
        pre_channel_locked = bool(prefilter and prefilter.channel == "story")
        ch = llm["content"].get("channel")
        if (
            not pre_channel_locked
            and isinstance(ch, str)
            and ch.strip().lower() in CHANNELS
        ):
            out["content"]["channel"] = ch.strip().lower()
        hint = llm["content"].get("headline_hint")
        if isinstance(hint, str) and hint.strip():
            out["content"]["headline_hint"] = hint.strip()[:200]

    if isinstance(llm.get("actions"), list) and llm["actions"]:
        valid_actions: list[dict] = []
        for a in llm["actions"]:
            if not isinstance(a, dict):
                continue
            kind = (a.get("kind") or "").strip().lower()
            if kind in ACTION_KINDS:
                valid_actions.append({
                    "kind": kind,
                    "config": a.get("config") if isinstance(a.get("config"), dict) else {},
                })
        if valid_actions:
            out["actions"] = valid_actions

    if isinstance(llm.get("requires_approval"), bool):
        out["requires_approval"] = llm["requires_approval"]

    if isinstance(llm.get("missing_fields"), list):
        out["missing_fields"] = [str(x) for x in llm["missing_fields"]][:8]

    return out


def _auto_name(text: str, prefilter: _PrefilterResult) -> str:
    """Operatöre okunaklı bir kural adı türet."""
    if prefilter.template and prefilter.event_type:
        tmpl = prefilter.template.replace("_", " ").title()
        return f"{tmpl} • {prefilter.event_type}"[:80]
    if prefilter.event_type:
        return f"Kural: {prefilter.event_type}"
    # İlk birkaç kelime
    words = text.split()[:6]
    return " ".join(words)[:80] or "Yeni Kural"


def explain_rule(rule: StructuredRule) -> str:
    """Operatöre kuralın insan diliyle ne yapacağını anlatan kısa özet.

    UI bunu "önizleme" alanında gösterir, böylece operatör yazdığı şeyin
    nasıl yorumlandığını görür.
    """
    parts: list[str] = []

    parts.append(f"**Tetik:** {_event_label(rule.trigger.event_type)}.")

    if rule.timing.delay_seconds > 0:
        parts.append(f"**Bekleme:** {_humanize_seconds(rule.timing.delay_seconds)} sonra.")
    if rule.timing.recurrence != "once":
        parts.append(f"**Tekrar:** {_recurrence_label(rule.timing.recurrence)}.")

    if rule.target.account_handle:
        parts.append(f"**Hesap:** @{rule.target.account_handle}.")

    if rule.content.template != "generic":
        parts.append(f"**İçerik şablonu:** {_template_label(rule.content.template)}.")
    parts.append(f"**Kanal:** {_channel_label(rule.content.channel)}.")

    if rule.actions:
        action_labels = [_action_label(a.kind) for a in rule.actions]
        parts.append("**Akış:** " + " → ".join(action_labels) + ".")

    if rule.requires_approval:
        parts.append("**Onay:** Yayın öncesi insan onayı bekleyecek.")

    if rule.missing_fields:
        parts.append(
            "**Eksik bilgiler:** " + ", ".join(rule.missing_fields) +
            ". Lütfen kuralı netleştirin."
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Türkçe etiket yardımcıları (UI tarafına)
# ---------------------------------------------------------------------------


def _event_label(event_type: str) -> str:
    return ({
        "store.created":      "Yeni mağaza oluşturulduğunda",
        "store.updated":      "Mağaza güncellendiğinde",
        "store.deleted":      "Mağaza silindiğinde",
        "product.created":    "Yeni ürün eklendiğinde",
        "product.updated":    "Ürün güncellendiğinde",
        "order.created":      "Yeni sipariş geldiğinde",
        "order.shipped":      "Sipariş kargoya verildiğinde",
        "order.cancelled":    "Sipariş iptal edildiğinde",
        "shipping.delayed":   "Kargo gecikmesi olduğunda",
        "stock.updated":      "Stok değiştiğinde",
        "review.created":     "Yeni müşteri yorumu geldiğinde",
        "review.negative":    "Olumsuz yorum geldiğinde",
        "customer.question":  "Müşteri sorusu olduğunda",
        "campaign.created":   "Yeni kampanya başladığında",
        "banner.updated":     "Banner güncellendiğinde",
        "sales.updated":      "Satış verileri güncellendiğinde",
    }.get(event_type, event_type))


def _template_label(template: str) -> str:
    return ({
        "anneler_gunu":       "Anneler Günü",
        "babalar_gunu":       "Babalar Günü",
        "yilbasi":            "Yılbaşı",
        "ramazan":            "Ramazan",
        "kurban_bayrami":     "Kurban Bayramı",
        "yaz_indirim":        "Yaz İndirimi",
        "kis_indirim":        "Kış İndirimi",
        "kara_cuma":          "Kara Cuma",
        "yeni_urun_lansman":  "Yeni Ürün Lansmanı",
        "magaza_acilis":      "Mağaza Açılışı",
        "tesekkur":           "Teşekkür",
        "ozur":               "Özür",
        "ozel_indirim":       "Özel İndirim",
        "generic":            "Genel İçerik",
    }.get(template, template))


def _channel_label(channel: str) -> str:
    return ({
        "instagram":  "Instagram",
        "facebook":   "Facebook",
        "banner":     "Banner",
        "coupon":     "Kupon",
        "faq":        "SSS",
        "support":    "Destek",
        "email":      "E-posta",
        "sms":        "SMS",
        "trendyol":   "Trendyol",
        "shopify":    "Shopify",
    }.get(channel, channel))


def _action_label(kind: str) -> str:
    return ({
        "wait":             "Bekle",
        "generate_content": "İçerik üret",
        "risk_check":       "Risk kontrolü",
        "approval":         "Onay",
        "publish":          "Yayınla",
        "monitor":          "İzle",
        "notify_customer":  "Müşteriye bildir",
        "create_coupon":    "Kupon üret",
        "schedule_followup": "Takip planla",
    }.get(kind, kind))


def _recurrence_label(rec: str) -> str:
    return ({
        "once":     "Bir kez",
        "daily":    "Her gün",
        "weekly":   "Haftalık",
        "monthly":  "Aylık",
    }.get(rec, rec))


def _humanize_seconds(s: int) -> str:
    if s <= 0:
        return "hemen"
    if s < 3600:
        m = max(1, s // 60)
        return f"{m} dakika"
    if s < 86400:
        h = max(1, s // 3600)
        return f"{h} saat"
    if s < 604800:
        d = max(1, s // 86400)
        return f"{d} gün"
    if s < 2592000:
        w = max(1, s // 604800)
        return f"{w} hafta"
    mo = max(1, s // 2592000)
    return f"{mo} ay"
