"""
Structured rule learning — başarı/başarısızlık örüntülerinden adaptif iyileştirme.

Tur 3 (olgunlaştırma turu). planner_learning.py'dan ayrı tutuldu çünkü:
    - planner_learning generic intent confidence drift için (autonomous_planner
      LLM çıktısının ne kadar güvenilir olduğu).
    - rule_learning per-rule (structured_rules.id) runtime başarısı için.

Davranış:
    - Her execution bittiğinde (completed/cancelled/failed) hook çağrılır.
    - structured_rules tablosundaki sayaçlar (success_count, failure_count,
      cancel_count) güncellenir.
    - `health_score` bounded delta ile güncellenir: ε ∈ [0.1, 0.99].
        * completed     → +0.02
        * approved_user → +0.05 (operatör explicit onay verdi)
        * cancelled     → -0.05 (operatör reddetti veya plan invalid)
        * failed        → -0.10 (gerçek hata)
    - learning_suggestions: belirli eşiklere ulaşan kurallar için operatöre
      Türkçe öneriler döndürür.

Önemli prensip: bu modül HİÇBİR ZAMAN bir kuralı otomatik silmez,
devre dışı bırakmaz veya yeniden yazmaz. Sadece skor + öneri üretir;
karar her zaman operatöründür.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db import db_connection, execute_query, execute_write, now_iso


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Outcome → health delta tablosu. Tek nokta — tüm hesaplamalar buradan akar.
OUTCOME_DELTAS: dict[str, float] = {
    "completed":      +0.02,
    "approved_user":  +0.05,   # operatörün explicit "approve" kararı
    "cancelled":      -0.05,
    "rejected_user":  -0.08,   # operatörün explicit "reject" kararı
    "failed":         -0.10,
}

HEALTH_MIN = 0.10
HEALTH_MAX = 0.99
HEALTH_DEFAULT = 0.70

# Önerilerin tetiklenmesi için eşikler
SUGGEST_REVIEW_THRESHOLD = 0.30   # bu altındaki kurallar incelenmeli
SUGGEST_PROMOTE_THRESHOLD = 0.85   # bu üstündekiler güvenilir
MIN_OBSERVATIONS_FOR_SUGGESTION = 3


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RuleHealthStats:
    rule_id: int
    health_score: float
    success_count: int
    failure_count: int
    cancel_count: int
    total_executions: int
    success_rate: float
    last_outcome: str | None

    def to_dict(self) -> dict:
        return {
            "rule_id":          self.rule_id,
            "health_score":     round(self.health_score, 3),
            "success_count":    self.success_count,
            "failure_count":    self.failure_count,
            "cancel_count":     self.cancel_count,
            "total_executions": self.total_executions,
            "success_rate":     round(self.success_rate, 3),
            "last_outcome":     self.last_outcome,
        }


@dataclass
class LearningSuggestion:
    rule_id: int
    rule_name: str
    kind: str             # 'review' | 'promote' | 'consolidate'
    severity: str         # 'low' | 'medium' | 'high'
    summary: str          # operatöre Türkçe açıklama
    health_score: float
    stats: dict

    def to_dict(self) -> dict:
        return {
            "rule_id":      self.rule_id,
            "rule_name":    self.rule_name,
            "kind":         self.kind,
            "severity":     self.severity,
            "summary":      self.summary,
            "health_score": round(self.health_score, 3),
            "stats":        self.stats,
        }


# ---------------------------------------------------------------------------
# Core: record outcome + update health
# ---------------------------------------------------------------------------


def _resolve_canonical_outcome(
    execution_status: str,
    user_decision: str | None = None,
) -> str:
    """rule_executions.status + opsiyonel operatör kararından canonical outcome.

    user_decision: 'approved' | 'rejected' | None
    """
    if user_decision == "approved":
        return "approved_user"
    if user_decision == "rejected":
        return "rejected_user"
    if execution_status == "completed":
        return "completed"
    if execution_status == "cancelled":
        return "cancelled"
    if execution_status == "failed":
        return "failed"
    return "completed"


def record_execution_outcome(
    rule_id: int,
    execution_status: str,
    *,
    user_decision: str | None = None,
) -> RuleHealthStats:
    """Bir execution bittiğinde çağrılır.

    runtime._update_execution_row sonunda hook'lanır. Sayaçları artırır,
    health_score'u bounded delta ile günceller, last_outcome'u günceller.
    """
    if not rule_id:
        return RuleHealthStats(rule_id=0, health_score=HEALTH_DEFAULT,
                               success_count=0, failure_count=0,
                               cancel_count=0, total_executions=0,
                               success_rate=0.0, last_outcome=None)

    outcome = _resolve_canonical_outcome(execution_status, user_decision)
    delta = OUTCOME_DELTAS.get(outcome, 0.0)

    # Read current state
    row = execute_query(
        """
        SELECT health_score, success_count, failure_count, cancel_count
        FROM structured_rules WHERE id=?
        """,
        (int(rule_id),), one=True,
    )
    if not row:
        return RuleHealthStats(rule_id=rule_id, health_score=HEALTH_DEFAULT,
                               success_count=0, failure_count=0,
                               cancel_count=0, total_executions=0,
                               success_rate=0.0, last_outcome=None)

    current = float(row["health_score"] if row["health_score"] is not None else HEALTH_DEFAULT)
    new_score = max(HEALTH_MIN, min(HEALTH_MAX, current + delta))

    # Update counters
    succ = int(row["success_count"] or 0)
    fail = int(row["failure_count"] or 0)
    canc = int(row["cancel_count"] or 0)
    if outcome in ("completed", "approved_user"):
        succ += 1
    elif outcome in ("failed",):
        fail += 1
    elif outcome in ("cancelled", "rejected_user"):
        canc += 1

    execute_write(
        """
        UPDATE structured_rules
        SET health_score=?, success_count=?, failure_count=?, cancel_count=?,
            last_outcome=?, updated_at=?
        WHERE id=?
        """,
        (new_score, succ, fail, canc, outcome, now_iso(), int(rule_id)),
    )

    # Emit observability trace
    try:
        from observability import _emit
        _emit(
            "RULE_LEARNING_UPDATE",
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "delta": delta,
                "new_health_score": round(new_score, 3),
                "summary": (
                    f"Kural #{rule_id} {outcome} sonucu — sağlık skoru "
                    f"{current:.2f} → {new_score:.2f}"
                ),
            },
            persist=True,
        )
    except Exception:
        pass

    total = succ + fail + canc
    rate = (succ / total) if total else 0.0
    return RuleHealthStats(
        rule_id=int(rule_id),
        health_score=new_score,
        success_count=succ, failure_count=fail, cancel_count=canc,
        total_executions=total,
        success_rate=rate, last_outcome=outcome,
    )


def get_rule_stats(rule_id: int) -> RuleHealthStats | None:
    row = execute_query(
        """
        SELECT health_score, success_count, failure_count, cancel_count, last_outcome
        FROM structured_rules WHERE id=?
        """,
        (int(rule_id),), one=True,
    )
    if not row:
        return None
    succ = int(row["success_count"] or 0)
    fail = int(row["failure_count"] or 0)
    canc = int(row["cancel_count"] or 0)
    total = succ + fail + canc
    return RuleHealthStats(
        rule_id=int(rule_id),
        health_score=float(row["health_score"] if row["health_score"] is not None else HEALTH_DEFAULT),
        success_count=succ, failure_count=fail, cancel_count=canc,
        total_executions=total,
        success_rate=(succ / total) if total else 0.0,
        last_outcome=row["last_outcome"],
    )


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def _review_suggestion(rule: dict, stats: RuleHealthStats) -> LearningSuggestion:
    canc = stats.cancel_count
    fail = stats.failure_count
    total = stats.total_executions
    rate_neg = (canc + fail) / max(1, total)
    if rate_neg >= 0.6:
        sev = "high"
        msg = (
            f"Bu kural son {total} çalıştırmanın {canc + fail}'i reddedildi "
            f"veya başarısız oldu. Kuralı yeniden gözden geçirmek faydalı olabilir."
        )
    else:
        sev = "medium"
        msg = (
            f"Bu kuralın başarı oranı düşük seyrediyor (toplam {total} "
            f"çalıştırma, {stats.success_count} başarılı). Tetikleyici "
            f"veya hedef ayarlarını gözden geçirmek isteyebilirsin."
        )
    return LearningSuggestion(
        rule_id=stats.rule_id,
        rule_name=rule["name"],
        kind="review",
        severity=sev,
        summary=msg,
        health_score=stats.health_score,
        stats=stats.to_dict(),
    )


def _promote_suggestion(rule: dict, stats: RuleHealthStats) -> LearningSuggestion:
    return LearningSuggestion(
        rule_id=stats.rule_id,
        rule_name=rule["name"],
        kind="promote",
        severity="low",
        summary=(
            f"Bu kural son dönemde tutarlı şekilde başarılı oluyor "
            f"({stats.success_count}/{stats.total_executions} başarı, sağlık "
            f"%{int(stats.health_score*100)}). Benzer kalıplarda yeni kurallar "
            f"oluşturmak güvenli."
        ),
        health_score=stats.health_score,
        stats=stats.to_dict(),
    )


def learning_suggestions(user_id: int = 1, limit: int = 20) -> list[dict]:
    """Aktif kurallar için öneri listesi.

    İki tür öneri:
        - review: health_score < 0.30 veya başarısızlık oranı yüksek
        - promote: health_score >= 0.85 ve başarı sayısı >= 3

    Eşik altındaki kurallar (henüz yeterli observation yok) önerilemez.
    """
    rows = execute_query(
        """
        SELECT id, name, natural_language, enabled, health_score,
               success_count, failure_count, cancel_count, last_outcome
        FROM structured_rules
        WHERE user_id=? AND COALESCE(is_current, 1) = 1
        ORDER BY id DESC LIMIT ?
        """,
        (int(user_id), int(limit)),
    )

    suggestions: list[LearningSuggestion] = []
    for row in rows:
        rule = dict(row)
        succ = int(rule.get("success_count") or 0)
        fail = int(rule.get("failure_count") or 0)
        canc = int(rule.get("cancel_count") or 0)
        total = succ + fail + canc
        if total < MIN_OBSERVATIONS_FOR_SUGGESTION:
            continue
        score = float(rule.get("health_score") if rule.get("health_score") is not None else HEALTH_DEFAULT)
        stats = RuleHealthStats(
            rule_id=int(rule["id"]),
            health_score=score,
            success_count=succ, failure_count=fail, cancel_count=canc,
            total_executions=total,
            success_rate=(succ / total) if total else 0.0,
            last_outcome=rule.get("last_outcome"),
        )
        if score <= SUGGEST_REVIEW_THRESHOLD or (fail + canc) / max(1, total) >= 0.6:
            suggestions.append(_review_suggestion(rule, stats))
        elif score >= SUGGEST_PROMOTE_THRESHOLD and succ >= 3:
            suggestions.append(_promote_suggestion(rule, stats))

    # Severity'e göre sırala
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: severity_rank.get(s.severity, 9))
    return [s.to_dict() for s in suggestions]


# ---------------------------------------------------------------------------
# Health view for the dashboard
# ---------------------------------------------------------------------------


def rules_health_overview(user_id: int = 1) -> dict:
    """Tüm aktif kuralların sağlık özet bilgisi — dashboard KPI/badge için."""
    rows = execute_query(
        """
        SELECT id, name, enabled, health_score, success_count,
               failure_count, cancel_count
        FROM structured_rules
        WHERE user_id=? AND COALESCE(is_current, 1) = 1
        """,
        (int(user_id),),
    )
    total = 0
    healthy = unhealthy = neutral = 0
    score_sum = 0.0
    for r in rows:
        score = float(r["health_score"] if r["health_score"] is not None else HEALTH_DEFAULT)
        score_sum += score
        total += 1
        if score >= 0.75:
            healthy += 1
        elif score <= 0.35:
            unhealthy += 1
        else:
            neutral += 1
    return {
        "total_active_rules": total,
        "avg_health_score": round(score_sum / total, 3) if total else 0.0,
        "healthy": healthy,
        "neutral": neutral,
        "unhealthy": unhealthy,
    }
