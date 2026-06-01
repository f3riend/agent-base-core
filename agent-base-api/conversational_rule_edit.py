"""
Conversational rule editing — Türkçe doğal dil ile structured rule güncelleme.

Operatör chat'inde şu tip ifadeleri anlamalıyız:
    "Yeni mağaza paylaşımı kuralını 5 güne çıkar"
    "Bu kuralı devre dışı bırak"
    "Kural #3'ü Instagram yerine Facebook'a al"
    "Kural #5'i sil"
    "Çanakkale kuralını sadece 1 saat sonrasına ayarla"

Tasarım kararları:
    1. **Deterministic-first**: regex/keyword ile intent + parametre yakalanır.
       LLM SADECE belirsizliği gidermek için fallback olarak çağrılır.
    2. **AI proposes, runtime executes**: LLM rule body'sini değiştirmez.
       LLM sadece intent + parametre çıkarır; gerçek değişikliği bu
       modülün Python kodu yapar.
    3. **Version semantic**: substantive değişiklik (delay/channel/handle)
       → save_rule(new_version=True). Toggle/delete in-place.
    4. **Confirm gate**: silme operatöre `confirm=True` parametresiyle
       açıkça istenir; varsayılan `dry_run` gibi davranır.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from structured_rule import (
    ActionStep, ContentSpec, StructuredRule, TargetSpec, TimingSpec, TriggerSpec,
)


# ---------------------------------------------------------------------------
# Operation types
# ---------------------------------------------------------------------------


EditKind = Literal[
    "set_delay",
    "set_channel",
    "set_handle",
    "set_template",
    "enable",
    "disable",
    "delete",
    "rename",
    "unknown",
]


@dataclass
class EditIntent:
    """Bir kullanıcı mesajının çıkartılmış edit niyeti."""
    kind: EditKind
    target_rule_id: int | None = None
    target_rule_label: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.7
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target_rule_id": self.target_rule_id,
            "target_rule_label": self.target_rule_label,
            "params": self.params,
            "confidence": round(self.confidence, 2),
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Deterministic intent detection
# ---------------------------------------------------------------------------


# Edit-trigger kelimeler. Sadece bunlardan biri varsa "muhtemelen edit"
# olarak değerlendiririz.
_EDIT_TRIGGERS = (
    "değiştir", "guncelle", "güncelle",
    "kapat", "aç", "ac",
    "devre dışı", "devre disi", "pasif",
    "etkinleştir", "aktif",
    "sil", "kaldır", "kaldir",
    "yap", "ayarla", "çıkar", "cikar",
    "ertele",
)


def _looks_like_edit(text: str) -> bool:
    """Bu mesaj edit komutu mu, sıradan soru mu?"""
    t = (text or "").lower()
    if not t:
        return False
    if "kural" not in t and "bu " not in t and "şunu" not in t and "sunu" not in t:
        # "Yeni mağaza paylaşımı kuralını" gibi 'kural' içeren ifadeler
        # ya da pronoun ("bu" / "şunu") barındırmıyorsa edit değil.
        return False
    return any(trig in t for trig in _EDIT_TRIGGERS)


# Numerik rule id'yi yakala: "#3", "5 numaralı kural", "Kural 7"
_RULE_ID_RE = re.compile(
    r"(?:kural\s+(?:numaras[ıi]\s+|#)?(?P<num>\d+)|"
    r"#(?P<num2>\d+)|"
    r"(?P<num3>\d+)\s*numaral[ıi]\s+kural)",
    re.IGNORECASE,
)


def _extract_rule_id(text: str) -> int | None:
    m = _RULE_ID_RE.search(text)
    if not m:
        return None
    for key in ("num", "num2", "num3"):
        v = m.group(key)
        if v:
            try:
                return int(v)
            except ValueError:
                return None
    return None


# Zaman ifadeleri — nl_rule_parser ile aynı tablodan ama burada bağımsız tutuyoruz
_TIME_PATTERNS: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(r"(\d+)\s*(?:dakika|dk)", re.IGNORECASE), 60),
    (re.compile(r"(\d+)\s*saat", re.IGNORECASE), 3600),
    (re.compile(r"(\d+)\s*g[üu]n", re.IGNORECASE), 86400),
    (re.compile(r"(\d+)\s*hafta", re.IGNORECASE), 604800),
    (re.compile(r"(\d+)\s*ay", re.IGNORECASE), 2592000),
)


def _extract_delay_seconds(text: str) -> int | None:
    t = text.lower()
    if any(k in t for k in ("hemen", "anında", "şimdi", "derhal")):
        return 0
    for pat, mult in _TIME_PATTERNS:
        m = pat.search(t)
        if m:
            try:
                return int(m.group(1)) * mult
            except ValueError:
                continue
    return None


_CHANNEL_KEYWORDS = (
    ("instagram", "instagram"),
    ("facebook", "facebook"),
    ("banner", "banner"),
    ("kupon", "coupon"),
    ("e-posta", "email"),
    ("email", "email"),
    ("sms", "sms"),
    ("destek", "support"),
    ("sss", "faq"),
)


def _extract_channel(text: str) -> str | None:
    t = text.lower()
    for kw, ch in _CHANNEL_KEYWORDS:
        if kw in t:
            return ch
    return None


# Handle yakalama: "Çanakkale", "demo_store", "@handle"
_HANDLE_RE = re.compile(
    r"(?:sadece\s+)?(?P<h>[A-Za-zÇĞİÖŞÜçğıöşü0-9_]{2,32})\s+(?:i[çc]in|hesab[ıi])",
    re.IGNORECASE,
)


def _extract_handle(text: str) -> str | None:
    m = re.search(r"@(?P<h>[A-Za-z0-9_\.]+)", text)
    if m:
        return m.group("h").lower()
    m = _HANDLE_RE.search(text)
    if m:
        return m.group("h").lower()
    return None


def _detect_kind(text: str) -> EditKind:
    t = text.lower()
    if any(k in t for k in ("sil", "kaldır", "kaldir")):
        return "delete"
    if any(k in t for k in ("devre dışı", "devre disi", "kapat", "pasif")):
        return "disable"
    if any(k in t for k in ("etkinleştir", "etkinlestir", "aktif", "aç ", " aç", "ac ")):
        return "enable"
    # Substantive — content/timing changes
    if _extract_delay_seconds(t) is not None and ("gün" in t or "saat" in t or "dakika" in t or "hafta" in t or "ay " in t or "hemen" in t):
        return "set_delay"
    if _extract_channel(t):
        return "set_channel"
    if _extract_handle(t):
        return "set_handle"
    # Şablon değişikliği
    for tmpl in ("anneler", "babalar", "yılbaşı", "yilbasi", "ramazan",
                 "kara cuma", "yaz indirim", "kış indirim"):
        if tmpl in t:
            return "set_template"
    return "unknown"


# ---------------------------------------------------------------------------
# Identification — hangi kuralı düzenliyoruz
# ---------------------------------------------------------------------------


def _resolve_target_rule(
    text: str,
    user_id: int,
    session_active_rule_id: int | None,
) -> tuple[int | None, str | None]:
    """Hangi rule_id?

    Sıra:
        1. Metinde explicit "Kural #3" / "3 numaralı kural" varsa o.
        2. Aktif kural listesinden isim eşleşmesi.
        3. Aksi halde session'ın active_rule_id'si (pronoun "bu kural").
    """
    rid = _extract_rule_id(text)
    if rid is not None:
        return rid, None

    # Name match
    try:
        from structured_rule_engine import list_rules
        rules = list_rules(user_id=user_id, limit=200)
    except Exception:
        rules = []

    tlow = (text or "").lower()
    best_match = None
    best_score = 0
    for r in rules:
        name_low = (r.name or "").lower()
        if not name_low:
            continue
        # token overlap
        name_tokens = set(re.findall(r"\w{3,}", name_low))
        text_tokens = set(re.findall(r"\w{3,}", tlow))
        score = len(name_tokens & text_tokens)
        if score > best_score:
            best_score = score
            best_match = r
    if best_match and best_score >= 1:
        return best_match.id, best_match.name

    # Session fallback
    return session_active_rule_id, None


# ---------------------------------------------------------------------------
# Public API: detect + apply
# ---------------------------------------------------------------------------


def detect_edit_intent(
    text: str,
    *,
    user_id: int = 1,
    session_active_rule_id: int | None = None,
) -> EditIntent | None:
    """Mesaj edit niyeti taşıyor mu? Taşıyorsa EditIntent, taşımıyorsa None."""
    if not _looks_like_edit(text):
        return None

    kind = _detect_kind(text)
    target_id, target_name = _resolve_target_rule(text, user_id, session_active_rule_id)

    params: dict[str, Any] = {}
    if kind == "set_delay":
        d = _extract_delay_seconds(text)
        if d is not None:
            params["delay_seconds"] = int(d)
    elif kind == "set_channel":
        ch = _extract_channel(text)
        if ch:
            params["channel"] = ch
    elif kind == "set_handle":
        h = _extract_handle(text)
        if h:
            params["account_handle"] = h
    elif kind == "set_template":
        # Hangi şablon — basit map
        t = text.lower()
        tmpl_map = (
            ("anneler", "anneler_gunu"),
            ("babalar", "babalar_gunu"),
            ("yılbaşı", "yilbasi"),
            ("yilbasi", "yilbasi"),
            ("ramazan", "ramazan"),
            ("kara cuma", "kara_cuma"),
            ("yaz indirim", "yaz_indirim"),
            ("kış indirim", "kis_indirim"),
            ("kis indirim", "kis_indirim"),
        )
        for needle, tmpl in tmpl_map:
            if needle in t:
                params["template"] = tmpl
                break

    confidence = 0.6
    if target_id is not None and kind != "unknown":
        confidence = 0.85
    elif target_id is not None or kind != "unknown":
        confidence = 0.7

    return EditIntent(
        kind=kind,
        target_rule_id=target_id,
        target_rule_label=target_name,
        params=params,
        confidence=confidence,
        rationale=(
            f"Tespit: {kind}"
            + (f", hedef #{target_id}" if target_id else "")
            + (f", param={params}" if params else "")
        ),
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@dataclass
class EditResult:
    success: bool
    summary: str
    rule_id: int | None = None
    new_version: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "summary": self.summary,
            "rule_id": self.rule_id,
            "new_version": self.new_version,
            "error": self.error,
        }


def apply_edit(
    intent: EditIntent,
    *,
    user_id: int = 1,
    confirm_delete: bool = False,
) -> EditResult:
    """EditIntent'i kurala uygula.

    Substantive değişiklikler (timing/channel/handle/template) yeni sürüm
    oluşturur; toggle (enable/disable) ve delete in-place.

    Silme için confirm_delete=True şart — varsayılan dry-run.
    """
    if not intent.target_rule_id:
        return EditResult(
            success=False,
            summary="Hangi kural? Açık değil. Kural numarasını belirt veya kural adını söyle.",
            error="target_rule_not_resolved",
        )

    try:
        from structured_rule_engine import (
            delete_rule, get_rule, save_rule, set_enabled,
        )
    except Exception as exc:
        return EditResult(success=False, summary="İç hata",
                          error=f"import_failed: {exc}")

    rule = get_rule(intent.target_rule_id)
    if not rule:
        return EditResult(
            success=False,
            summary=f"#{intent.target_rule_id} numaralı kuralı bulamadım.",
            error="rule_not_found",
        )

    if intent.kind == "disable":
        set_enabled(rule.id, False)
        return EditResult(
            success=True,
            summary=f'"{rule.name}" kuralını devre dışı bıraktım. Yeniden açmak istersen söyle.',
            rule_id=rule.id,
        )

    if intent.kind == "enable":
        set_enabled(rule.id, True)
        return EditResult(
            success=True,
            summary=f'"{rule.name}" kuralını tekrar etkinleştirdim.',
            rule_id=rule.id,
        )

    if intent.kind == "delete":
        if not confirm_delete:
            return EditResult(
                success=False,
                summary=(
                    f'"{rule.name}" kuralını silmek istediğine emin misin? '
                    f'Onayla butonuna basarak gerçekten sil.'
                ),
                rule_id=rule.id,
                error="confirm_required",
            )
        delete_rule(rule.id)
        return EditResult(
            success=True,
            summary=f'"{rule.name}" kuralı silindi.',
            rule_id=rule.id,
        )

    # Substantive değişiklikler — yeni sürüm
    changed = False
    if intent.kind == "set_delay" and "delay_seconds" in intent.params:
        new_delay = int(intent.params["delay_seconds"])
        if rule.timing.delay_seconds != new_delay:
            rule.timing = TimingSpec(
                delay_seconds=new_delay,
                schedule_at=rule.timing.schedule_at,
                recurrence=rule.timing.recurrence,
            )
            changed = True

    elif intent.kind == "set_channel" and "channel" in intent.params:
        new_ch = intent.params["channel"]
        if rule.content.channel != new_ch:
            rule.content = ContentSpec(
                template=rule.content.template,
                channel=new_ch,
                headline_hint=rule.content.headline_hint,
                extras=rule.content.extras,
            )
            changed = True

    elif intent.kind == "set_handle" and "account_handle" in intent.params:
        new_h = intent.params["account_handle"]
        if rule.target.account_handle != new_h:
            rule.target = TargetSpec(
                account_handle=new_h,
                entity_type=rule.target.entity_type,
                entity_filters=rule.target.entity_filters,
            )
            changed = True

    elif intent.kind == "set_template" and "template" in intent.params:
        new_t = intent.params["template"]
        if rule.content.template != new_t:
            rule.content = ContentSpec(
                template=new_t,
                channel=rule.content.channel,
                headline_hint=rule.content.headline_hint,
                extras=rule.content.extras,
            )
            changed = True

    else:
        return EditResult(
            success=False,
            summary=(
                "Ne değiştirmek istediğini tam anlayamadım. Lütfen "
                "spesifik söyle (örn. 'delay'i 5 güne çıkar' veya "
                "'kanalı Facebook yap')."
            ),
            rule_id=rule.id,
            error="ambiguous_edit",
        )

    if not changed:
        return EditResult(
            success=False,
            summary="Bu değer zaten ayarlıydı, değişiklik gerekmedi.",
            rule_id=rule.id,
        )

    # Yeni sürüm kaydet
    saved = save_rule(rule, new_version=True)

    # Türkçe özet
    if intent.kind == "set_delay":
        d = int(intent.params["delay_seconds"])
        from nl_rule_parser import _humanize_seconds
        summary = (
            f'"{saved.name}" kuralının bekleme süresini '
            f'{_humanize_seconds(d)} olarak güncelledim '
            f'(yeni sürüm: v{saved.model_dump().get("version", "?")}).'
        )
    elif intent.kind == "set_channel":
        from nl_rule_parser import _channel_label
        summary = (
            f'"{saved.name}" kuralının kanalını '
            f'{_channel_label(intent.params["channel"])} olarak güncelledim.'
        )
    elif intent.kind == "set_handle":
        summary = (
            f'"{saved.name}" kuralının hedef hesabını '
            f'@{intent.params["account_handle"]} olarak güncelledim.'
        )
    elif intent.kind == "set_template":
        from nl_rule_parser import _template_label
        summary = (
            f'"{saved.name}" kuralının içerik şablonunu '
            f'{_template_label(intent.params["template"])} olarak güncelledim.'
        )
    else:
        summary = f'"{saved.name}" kuralı güncellendi.'

    return EditResult(
        success=True,
        summary=summary,
        rule_id=saved.id,
    )


# ---------------------------------------------------------------------------
# Optional LLM clarifier (when deterministic intent is "unknown" but text
# looks like edit)
# ---------------------------------------------------------------------------


def clarify_with_llm(text: str) -> dict | None:
    """Belirsiz edit isteğini LLM'e clarify ettir.

    Sadece NL_EDIT_USE_LLM=1 ile aktive olur. LLM SADECE intent + param
    çıkarır — rule body'sini yazmasına izin verilmez.
    """
    if os.environ.get("NL_EDIT_USE_LLM", "0") != "1":
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        client = OpenAI(timeout=8)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "Sen bir intent parser'sın. Türkçe edit isteğini "
                    "JSON'a çevir.\n"
                    'Şema: {"kind": "set_delay|set_channel|set_handle|'
                    'set_template|enable|disable|delete|unknown", '
                    '"params": { ... }, "rule_label_hint": null}'
                )},
                {"role": "user", "content": text},
            ],
            temperature=0.0, max_tokens=120,
        )
        return json.loads(completion.choices[0].message.content or "{}")
    except Exception as exc:
        print(f"[RULE_EDIT_LLM] clarify failed: {exc}")
        return None
