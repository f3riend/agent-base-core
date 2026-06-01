"""
Rule conflict resolver — çakışma tespiti + akıllı çözüm önerisi.

Tur 2'de basit detect_conflicts vardı (structured_rule_engine içinde).
Bu modül onun üstüne katmanlı tavsiye ve operatör-onaylı çözüm uygulama
ekliyor.

Tasarım kararları:
    - Otomatik karar YOK. Bu modül sadece detect + suggest. Karar
      operatöre kalır; operatör bir endpoint'i çağırarak çözümü uygular.
    - LLM opsiyonel ve adım katmanı. Önce deterministic kalıp:
      "Aynı tetik + handle + kanal → biri pasifleştirilsin (en eskisi)".
      "Aynı tetik, farklı handle → conflict DEĞİL, suppress."
      Sonra LLM (NL_CONFLICT_USE_LLM=1) doğal dil özet üretir.
    - Çözüm türleri:
        * deactivate_older   — sadece en yenisi açık kalsın
        * deactivate_lower_health — düşük health_score olanları kapat
        * keep_one_review    — operatör elle gözden geçirsin (no-op fix)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConflictResolution:
    """Operatöre sunulan tek bir çözüm önerisi."""
    action: str           # 'deactivate_older' | 'deactivate_lower_health' | 'keep_one_review'
    rule_ids_to_disable: list[int] = field(default_factory=list)
    rule_id_to_keep: int | None = None
    summary: str = ""
    severity: str = "medium"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "rule_ids_to_disable": self.rule_ids_to_disable,
            "rule_id_to_keep": self.rule_id_to_keep,
            "summary": self.summary,
            "severity": self.severity,
        }


@dataclass
class ConflictWithSuggestions:
    """detect_conflicts'in genişletilmiş çıktısı."""
    trigger_event: str
    account_handle: str
    channel: str
    rule_ids: list[int]
    rule_names: list[str]
    severity: str
    summary: str
    resolutions: list[ConflictResolution] = field(default_factory=list)
    conflict_key: str = ""           # stabil id; operatör butonu için

    def to_dict(self) -> dict:
        return {
            "conflict_key": self.conflict_key,
            "trigger_event": self.trigger_event,
            "account_handle": self.account_handle,
            "channel": self.channel,
            "rule_ids": self.rule_ids,
            "rule_names": self.rule_names,
            "severity": self.severity,
            "summary": self.summary,
            "resolutions": [r.to_dict() for r in self.resolutions],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conflict_key(event: str, handle: str, channel: str) -> str:
    h = (handle or "any").replace(" ", "_")
    return f"{event}::{h}::{channel}"


def _suggest_resolutions(
    rules: list[Any],
) -> list[ConflictResolution]:
    """Bir conflict cluster için önerilebilir çözümler."""
    if len(rules) < 2:
        return []

    # En yenisini bul (id en yüksek)
    sorted_by_id = sorted(rules, key=lambda r: r.id, reverse=True)
    newest = sorted_by_id[0]
    others = sorted_by_id[1:]

    # En yüksek health_score'lu kuralı bul
    sorted_by_health = sorted(
        rules,
        key=lambda r: getattr(r, "parse_confidence", 0.7),  # parse_confidence fallback
        reverse=True,
    )
    # health_score storage'da var ama modele eklenmediği için indirect erişim:
    from db import execute_query
    health_map: dict[int, float] = {}
    for r in rules:
        row = execute_query(
            "SELECT health_score FROM structured_rules WHERE id=?",
            (int(r.id),), one=True,
        )
        if row and row["health_score"] is not None:
            health_map[r.id] = float(row["health_score"])
        else:
            health_map[r.id] = 0.7
    best_health = max(rules, key=lambda r: health_map[r.id])
    worst_set = [r for r in rules if r.id != best_health.id]

    resolutions: list[ConflictResolution] = []

    # 1) Sadece en yenisi kalsın
    resolutions.append(ConflictResolution(
        action="deactivate_older",
        rule_ids_to_disable=[r.id for r in others],
        rule_id_to_keep=newest.id,
        severity="low",
        summary=(
            f'Sadece en yeni kuralı (“{newest.name}”) aktif bırak, eskileri '
            f"({len(others)} tane) devre dışı al."
        ),
    ))

    # 2) En sağlıklı kural kalsın
    if best_health.id != newest.id and worst_set:
        resolutions.append(ConflictResolution(
            action="deactivate_lower_health",
            rule_ids_to_disable=[r.id for r in worst_set],
            rule_id_to_keep=best_health.id,
            severity="medium",
            summary=(
                f'En sağlıklı kuralı (“{best_health.name}”, sağlık '
                f"%{int(health_map[best_health.id]*100)}) aktif bırak, "
                f"diğer {len(worst_set)} kuralı kapat."
            ),
        ))

    # 3) Manuel gözden geçirme
    resolutions.append(ConflictResolution(
        action="keep_one_review",
        rule_ids_to_disable=[],
        rule_id_to_keep=None,
        severity="low",
        summary=(
            "Hepsi açık kalsın — sen elle inceleyip karar vereceksen "
            "bu çözümü işaretleme."
        ),
    ))

    return resolutions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def conflicts_with_suggestions(user_id: int) -> list[dict]:
    """Tüm aktif conflict'leri çözüm önerileriyle birlikte döndür.

    structured_rule_engine.detect_conflicts'i wrap eder.
    """
    from structured_rule_engine import detect_conflicts, get_rule

    raw_conflicts = detect_conflicts(user_id)
    out: list[ConflictWithSuggestions] = []

    for c in raw_conflicts:
        rule_objs = []
        for rid in c["rule_ids"]:
            r = get_rule(int(rid))
            if r is not None:
                rule_objs.append(r)
        if len(rule_objs) < 2:
            continue
        resolutions = _suggest_resolutions(rule_objs)
        key = _make_conflict_key(
            c["trigger_event"], c["account_handle"], c["channel"]
        )

        # Türkçe insan-okunabilir özet — LLM olmadan deterministic
        summary = (
            f"{c['trigger_event']} olayı için "
            f"'{c['account_handle']}' hesabında {c['channel']} kanalında "
            f"{c['rule_count']} aktif kural var. "
            f"Bunlar aynı olayda tetiklenince hangi kuralın geçerli "
            f"olacağı belirsiz."
        )

        out.append(ConflictWithSuggestions(
            conflict_key=key,
            trigger_event=c["trigger_event"],
            account_handle=c["account_handle"],
            channel=c["channel"],
            rule_ids=c["rule_ids"],
            rule_names=c["rule_names"],
            severity=c["severity"],
            summary=summary,
            resolutions=resolutions,
        ))

    # Opsiyonel LLM zenginleştirme
    if os.environ.get("NL_CONFLICT_USE_LLM", "0") == "1":
        out = _llm_enrich(out)

    return [c.to_dict() for c in out]


def apply_resolution(
    conflict_key: str,
    action: str,
    user_id: int = 1,
) -> dict:
    """Operatörün seçtiği çözümü uygula.

    Sadece deactivate_* aksiyonları uygulanır (keep_one_review no-op).
    İdempotent: zaten devre dışı olan kuralı tekrar disable etmek hata
    fırlatmaz.
    """
    if action == "keep_one_review":
        return {
            "ok": True,
            "no_op": True,
            "summary": "Bir şey yapılmadı; gözden geçirme operatöre bırakıldı.",
        }

    conflicts = conflicts_with_suggestions(user_id)
    target = next((c for c in conflicts if c["conflict_key"] == conflict_key), None)
    if target is None:
        return {"ok": False, "error": "conflict_key not found"}

    # Hangi resolution'u uygulayacağımızı bul
    chosen = next((r for r in target["resolutions"] if r["action"] == action), None)
    if chosen is None:
        return {"ok": False, "error": f"unknown action {action!r} for this conflict"}

    from structured_rule_engine import set_enabled

    disabled: list[int] = []
    for rid in chosen["rule_ids_to_disable"]:
        if set_enabled(int(rid), False):
            disabled.append(int(rid))

    # Trace
    try:
        from observability import _emit
        _emit(
            "CONFLICT_RESOLVED",
            {
                "conflict_key": conflict_key,
                "action": action,
                "disabled_rule_ids": disabled,
                "kept_rule_id": chosen.get("rule_id_to_keep"),
                "summary": (
                    f"Çakışma çözüldü: {len(disabled)} kural devre dışı "
                    f"bırakıldı, {chosen.get('rule_id_to_keep')} aktif."
                ),
            },
            persist=True, user_id=user_id,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "conflict_key": conflict_key,
        "action": action,
        "disabled_rule_ids": disabled,
        "kept_rule_id": chosen.get("rule_id_to_keep"),
        "summary": chosen.get("summary"),
    }


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------


def _llm_enrich(conflicts: list[ConflictWithSuggestions]) -> list[ConflictWithSuggestions]:
    """Conflict özetlerini LLM ile daha doğal Türkçeye dönüştür."""
    if not os.environ.get("OPENAI_API_KEY"):
        return conflicts
    try:
        from openai import OpenAI
        client = OpenAI(timeout=10)
        for c in conflicts:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "Sen bir Türkçe iş operasyon asistanısın. Aşağıdaki "
                        "kural çakışmasını operatöre 2 cümlede özetle. "
                        "Selamlama yok. Önerme yok — sadece tanımla."
                    )},
                    {"role": "user", "content": (
                        f"Tetik: {c.trigger_event}\nHesap: {c.account_handle}\n"
                        f"Kanal: {c.channel}\nKural sayısı: {len(c.rule_ids)}\n"
                        f"Kural isimleri: {', '.join(c.rule_names)}\n"
                    )},
                ],
                temperature=0.3, max_tokens=120,
            )
            content = (completion.choices[0].message.content or "").strip()
            if content:
                c.summary = content
    except Exception as exc:
        print(f"[CONFLICT_LLM] enrich failed: {exc}")
    return conflicts
