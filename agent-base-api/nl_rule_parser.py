"""
Türkçe doğal dil → StructuredRule parser.

Tek bir gpt-4o-mini çağrısı ile JSON schema üretimi.
Hata veya API yoksa deterministic fallback skeleton döner.

Public API (değişmez):
    parse_rule(natural_language, *, user_id, org_id, name_hint) → StructuredRule
    explain_rule(rule) → str
    _humanize_seconds(s) → str          # business_chat.py tarafından import edilir
    _channel_label(channel) → str       # business_chat.py tarafından import edilir
"""

from __future__ import annotations

import json
import os
from typing import Any

from structured_rule import (
    ACTION_KINDS,
    CHANNELS,
    CONTENT_TEMPLATES,
    TRIGGER_EVENT_TYPES,
    ActionStep,
    Condition,
    StructuredRule,
    empty_rule_template,
    utcnow_iso,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""Sen bir Türkçe iş kuralı parser'ısın.
Operatörün yazdığı doğal dil niyetini, aşağıdaki JSON şemasına dönüştür.
Cevabın SADECE geçerli JSON olmalı — başka metin yok.

ŞEMA:
{{
  "name": "2-6 kelimelik Türkçe kural adı",
  "trigger_event_type": "<event_type>",
  "delay_seconds": 0,
  "recurrence": "once",
  "account_handle": null,
  "target_accounts": [],
  "template": "<template>",
  "channel": "<channel>",
  "discount_pct": null,
  "campaign_start": null,
  "campaign_end": null,
  "conditions": [],
  "actions": ["wait","generate_content","risk_check","approval","publish","monitor"],
  "requires_approval": true,
  "module": "social_media",
  "missing_fields": []
}}

GEÇERLİ trigger_event_type değerleri:
{", ".join(TRIGGER_EVENT_TYPES)}

GEÇERLİ channel değerleri:
{", ".join(CHANNELS)}

GEÇERLİ template değerleri:
{", ".join(CONTENT_TEMPLATES)}

GEÇERLİ actions öğeleri:
{", ".join(ACTION_KINDS)}

KURALLAR:
- "hikaye" veya "story" → channel="story"
- "banner" → channel="banner"
- "@hesap" → target_accounts listesine ekle, account_handle ilkine set et
- "/sablon" → template olarak kullan (content_templates dışındakiler de geçerli — olduğu gibi al)
- "%10 kampanya" → discount_pct=10
- "X gün sonra" → delay_seconds = X * 86400
- "X saat sonra" → delay_seconds = X * 3600
- Dış yayın (instagram/facebook/banner/story) varsa requires_approval=true
- actions sırası: önce wait (varsa), sonra generate_content, risk_check, approval (requires_approval=true ise), publish, monitor
- Anlayamadığın zorunlu alanları missing_fields'a yaz
- recurrence: "once" | "daily" | "weekly" | "monthly"
- module: "social_media" | "campaign" | "product" | "customer" | "stock" | "review" | "order" | "generic"
- conditions: her öğe {{"field": "...", "operator": ">=", "value": ...}} formatında
"""


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

    Asla exception fırlatmaz; başarısız parse durumunda parse_confidence=0.0
    ve missing_fields dolu iskelet döner.
    """
    text = (natural_language or "").strip()
    if not text:
        return empty_rule_template("")

    raw = _llm_parse(text)
    if not raw:
        fallback = empty_rule_template(text)
        fallback.parse_confidence = 0.1
        fallback.missing_fields = ["llm_unavailable"]
        return fallback

    return _build_rule(raw, text, user_id=user_id, org_id=org_id, name_hint=name_hint)


def explain_rule(rule: StructuredRule) -> str:
    """Operatöre kuralın ne yapacağını anlatan kısa Türkçe özet."""
    parts: list[str] = []

    parts.append(f"**Tetik:** {_event_label(rule.trigger.event_type)}.")

    if rule.timing.delay_seconds > 0:
        parts.append(f"**Bekleme:** {_humanize_seconds(rule.timing.delay_seconds)} sonra.")
    if rule.timing.recurrence != "once":
        parts.append(f"**Tekrar:** {_recurrence_label(rule.timing.recurrence)}.")

    accounts = rule.all_target_accounts()
    if accounts:
        parts.append(f"**Hesap(lar):** {', '.join('@' + a for a in accounts)}.")

    if rule.content.template != "generic":
        parts.append(f"**Şablon:** {_template_label(rule.content.template)}.")
    parts.append(f"**Kanal:** {_channel_label(rule.content.channel)}.")

    if rule.actions:
        action_labels = [_action_label(a.kind) for a in rule.actions]
        parts.append("**Akış:** " + " → ".join(action_labels) + ".")

    if rule.requires_approval:
        parts.append("**Onay:** Yayın öncesi insan onayı bekleyecek.")

    if rule.conditions:
        cond_strs = [f"{c.field} {c.operator} {c.value}" for c in rule.conditions]
        parts.append("**Koşullar:** " + ", ".join(cond_strs) + ".")

    if rule.missing_fields:
        parts.append(
            "**Eksik:** " + ", ".join(rule.missing_fields) + ". Lütfen netleştirin."
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _llm_parse(text: str) -> dict[str, Any] | None:
    """gpt-4o-mini ile JSON üret. Hata/key yoksa None döner."""
    if os.environ.get("NL_PARSER_USE_LLM", "1") == "0":
        return None
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            timeout=float(os.environ.get("NL_PARSER_TIMEOUT", "15")),
        )
        completion = client.chat.completions.create(
            model=os.environ.get("NL_PARSER_MODEL", "gpt-4o-mini"),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        raw = (completion.choices[0].message.content or "").strip()
        return json.loads(raw) if raw else None
    except Exception as exc:
        print(f"[NL_PARSER] LLM failed: {exc}")
        return None


def _safe_str(v: Any, default: str = "") -> str:
    return str(v).strip() if v is not None else default


def _build_rule(
    raw: dict[str, Any],
    natural_language: str,
    *,
    user_id: int,
    org_id: int | None,
    name_hint: str | None,
) -> StructuredRule:
    """LLM JSON çıktısını StructuredRule'a dönüştür."""
    from structured_rule import (
        ContentSpec, GraphDefinition, NodeDefinition, TargetSpec,
        TimingSpec, TriggerSpec,
    )

    # --- trigger ---
    event_type = _safe_str(raw.get("trigger_event_type"), "store.created").lower()
    if event_type not in TRIGGER_EVENT_TYPES:
        event_type = "store.created"

    # --- timing ---
    try:
        delay = max(0, int(raw.get("delay_seconds") or 0))
    except (TypeError, ValueError):
        delay = 0
    recurrence = _safe_str(raw.get("recurrence"), "once")
    if recurrence not in ("once", "daily", "weekly", "monthly"):
        recurrence = "once"

    # --- target / accounts ---
    account_handle = _safe_str(raw.get("account_handle")) or None
    target_accounts_raw = raw.get("target_accounts") or []
    if not isinstance(target_accounts_raw, list):
        target_accounts_raw = []
    target_accounts = [
        str(a).strip().lstrip("@").lower()
        for a in target_accounts_raw
        if str(a).strip()
    ]
    if account_handle:
        account_handle = account_handle.lstrip("@").lower()
        if account_handle not in target_accounts:
            target_accounts.insert(0, account_handle)
    if target_accounts and not account_handle:
        account_handle = target_accounts[0]

    # --- content ---
    template = _safe_str(raw.get("template"), "generic").lower()
    # /sablon şeklindeki özel şablonları kabul et (CONTENT_TEMPLATES dışı da olabilir)
    channel = _safe_str(raw.get("channel"), "instagram").lower()
    if channel not in CHANNELS:
        channel = "instagram"

    # --- conditions ---
    conditions: list[Condition] = []
    raw_conds = raw.get("conditions") or []
    if isinstance(raw_conds, list):
        for c in raw_conds:
            if not isinstance(c, dict):
                continue
            try:
                conditions.append(Condition(
                    field=str(c.get("field") or ""),
                    operator=str(c.get("operator") or "=="),
                    value=c.get("value"),
                ))
            except Exception:
                pass

    # Kampanya indirim koşulunu conditions'a ekle
    discount_pct = raw.get("discount_pct")
    if discount_pct is not None:
        try:
            dp = float(discount_pct)
            if dp > 0:
                conditions.append(Condition(
                    field="discount_percent",
                    operator=">=",
                    value=dp,
                ))
        except (TypeError, ValueError):
            pass

    # --- actions ---
    raw_actions = raw.get("actions") or []
    if not isinstance(raw_actions, list):
        raw_actions = []
    actions: list[ActionStep] = []
    for kind in raw_actions:
        kind_str = str(kind).strip().lower()
        if kind_str in ACTION_KINDS:
            cfg: dict[str, Any] = {}
            if kind_str == "wait":
                cfg["delay_seconds"] = delay
            elif kind_str in ("generate_content", "publish"):
                cfg["template"] = template
                cfg["channel"] = channel
                if target_accounts:
                    cfg["accounts"] = target_accounts
            actions.append(ActionStep(kind=kind_str, config=cfg))
    if not actions:
        actions = [ActionStep(kind="generate_content")]

    # --- misc ---
    requires_approval = bool(raw.get("requires_approval", True))
    module = _safe_str(raw.get("module"), "social_media").lower()
    from structured_rule import RULE_MODULES
    if module not in RULE_MODULES:
        module = "social_media"

    missing = [str(f) for f in (raw.get("missing_fields") or [])]
    name = name_hint or _safe_str(raw.get("name")) or _auto_name(natural_language, event_type)

    # --- graph_definition ---
    # Channel + actions'tan dinamik GraphDefinition üret.
    # publish_story_node / publish_post_node / publish_banner_node çalışır;
    # calendar entry ve social_documents kaydı bu node'larda yapılır.
    graph_def = _build_graph_definition(
        channel=channel,
        template=template,
        target_accounts=target_accounts,
        delay=delay,
        requires_approval=requires_approval,
        conditions=conditions,
        raw_actions=[str(k).strip().lower() for k in (raw.get("actions") or [])],
    )

    try:
        return StructuredRule(
            user_id=user_id,
            org_id=org_id,
            name=name[:120] or "Yeni Kural",
            natural_language=natural_language,
            trigger=TriggerSpec(event_type=event_type),
            timing=TimingSpec(delay_seconds=delay, recurrence=recurrence),
            target=TargetSpec(account_handle=account_handle),
            content=ContentSpec(template=template, channel=channel),
            actions=actions,
            requires_approval=requires_approval,
            enabled=True,
            module=module,
            target_accounts=target_accounts,
            target_template=template if template not in CONTENT_TEMPLATES else None,
            conditions=conditions,
            graph_definition=graph_def,
            parse_confidence=0.9,
            missing_fields=missing,
            created_at=utcnow_iso(),
            updated_at=utcnow_iso(),
        )
    except Exception as exc:
        print(f"[NL_PARSER] StructuredRule validation failed: {exc}")
        fallback = empty_rule_template(natural_language)
        fallback.missing_fields = [f"validation_error: {exc}"]
        return fallback


def _build_graph_definition(
    *,
    channel: str,
    template: str,
    target_accounts: list[str],
    delay: int,
    requires_approval: bool,
    conditions: list,
    raw_actions: list[str],
) -> "GraphDefinition":
    """Channel + actions'tan dinamik GraphDefinition üret.

    publish_story_node / publish_post_node / publish_banner_node kullanır;
    bu node'lar _create_calendar_entry ve _save_to_social_documents çağırır.
    canonical graph'taki publisher_node bunları çağırmaz.
    """
    from structured_rule import GraphDefinition, NodeDefinition

    # Channel → publish node tipi
    _PUBLISH_NODE = {
        "story":           "publish_story",
        "instagram_story": "publish_story",
        "banner":          "publish_banner",
        "instagram":       "publish_post",
        "facebook":        "publish_post",
    }
    publish_node_type = _PUBLISH_NODE.get(channel, "publish_post")
    publish_params: dict = {
        "channel": channel,
        "template": template,
        "accounts": target_accounts,
    }

    nodes: list[NodeDefinition] = [
        NodeDefinition(node_id="supervisor", node_type="supervisor", params={}),
    ]
    interrupt_after: list[str] = []

    # wait
    if delay > 0:
        nodes.append(NodeDefinition(
            node_id="wait",
            node_type="wait",
            params={"delay_seconds": delay},
        ))
        interrupt_after.append("wait")

    # condition_check
    if conditions:
        nodes.append(NodeDefinition(
            node_id="condition_check",
            node_type="condition_check",
            params={
                "conditions": [
                    c.model_dump() if hasattr(c, "model_dump") else dict(c)
                    for c in conditions
                ],
                "match_mode": "all",
            },
        ))

    # generate_content
    nodes.append(NodeDefinition(
        node_id="generate_content",
        node_type="generate_content",
        params={"template": template, "channel": channel},
    ))

    # risk_check
    nodes.append(NodeDefinition(
        node_id="risk_check",
        node_type="risk_check",
        params={},
    ))

    # approval_gate
    if requires_approval:
        _approval_type_map = {
            "story":           "story_approval",
            "instagram_story": "story_approval",
            "banner":          "banner_approval",
            "instagram":       "post_approval",
            "facebook":        "post_approval",
        }
        nodes.append(NodeDefinition(
            node_id="approval_gate",
            node_type="approval_gate",
            params={"approval_type": _approval_type_map.get(channel, "campaign_approval")},
        ))
        interrupt_after.append("approval_gate")

    # publish
    nodes.append(NodeDefinition(
        node_id="publish",
        node_type=publish_node_type,
        params=publish_params,
    ))

    # finalize
    nodes.append(NodeDefinition(
        node_id="finalize",
        node_type="finalize",
        params={},
    ))

    return GraphDefinition(
        nodes=nodes,
        entry_node="supervisor",
        exit_node="finalize",
        interrupt_before=[],
        interrupt_after=interrupt_after,
    )


def _auto_name(text: str, event_type: str) -> str:
    words = text.split()[:5]
    label = " ".join(words)
    return label[:80] if label else f"Kural: {event_type}"


# ---------------------------------------------------------------------------
# Label helpers — UI & business_chat.py tarafından import edilir
# ---------------------------------------------------------------------------


def _humanize_seconds(s: int) -> str:
    if s <= 0:
        return "hemen"
    if s < 3600:
        return f"{max(1, s // 60)} dakika"
    if s < 86400:
        return f"{max(1, s // 3600)} saat"
    if s < 604800:
        return f"{max(1, s // 86400)} gün"
    if s < 2592000:
        return f"{max(1, s // 604800)} hafta"
    return f"{max(1, s // 2592000)} ay"


def _event_label(event_type: str) -> str:
    return {
        "store.created":      "Yeni mağaza oluşturulduğunda",
        "store.updated":      "Mağaza güncellendiğinde",
        "store.deleted":      "Mağaza silindiğinde",
        "store.rejected":     "Mağaza reddedildiğinde",
        "product.created":    "Yeni ürün eklendiğinde",
        "product.updated":    "Ürün güncellendiğinde",
        "product.deleted":    "Ürün silindiğinde",
        "order.created":      "Yeni sipariş geldiğinde",
        "order.shipped":      "Sipariş kargoya verildiğinde",
        "order.cancelled":    "Sipariş iptal edildiğinde",
        "shipping.delayed":   "Kargo gecikmesi olduğunda",
        "stock.updated":      "Stok değiştiğinde",
        "review.created":     "Yeni müşteri yorumu geldiğinde",
        "review.negative":    "Olumsuz yorum geldiğinde",
        "customer.question":  "Müşteri sorusu olduğunda",
        "campaign.created":   "Yeni kampanya başladığında",
        "banner.created":     "Yeni banner oluşturulduğunda",
        "banner.updated":     "Banner güncellendiğinde",
        "sales.updated":      "Satış verileri güncellendiğinde",
        "story.created":      "Yeni hikaye paylaşıldığında",
        "coupon.created":     "Yeni kupon oluşturulduğunda",
    }.get(event_type, event_type)


def _template_label(template: str) -> str:
    return {
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
    }.get(template, template)


def _channel_label(channel: str) -> str:
    return {
        "story":            "Instagram Hikaye",
        "instagram_story":  "Instagram Hikaye",
        "instagram":        "Instagram",
        "facebook":         "Facebook",
        "banner":           "Banner",
        "coupon":           "Kupon",
        "faq":              "SSS",
        "support":          "Destek",
        "email":            "E-posta",
        "sms":              "SMS",
        "trendyol":         "Trendyol",
        "shopify":          "Shopify",
    }.get(channel, channel)


def _action_label(kind: str) -> str:
    return {
        "wait":              "Bekle",
        "generate_content":  "İçerik üret",
        "risk_check":        "Risk kontrolü",
        "approval":          "Onay",
        "publish":           "Yayınla",
        "monitor":           "İzle",
        "notify_customer":   "Müşteriye bildir",
        "create_coupon":     "Kupon üret",
        "schedule_followup": "Takip planla",
    }.get(kind, kind)


def _recurrence_label(rec: str) -> str:
    return {
        "once":    "Bir kez",
        "daily":   "Her gün",
        "weekly":  "Haftalık",
        "monthly": "Aylık",
    }.get(rec, rec)