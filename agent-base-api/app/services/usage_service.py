"""Üretim çağrılarının token/maliyet kayıtlarını DB'ye yazma & raporlama."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.pricing import compute_cost
from app.models.account import Account
from app.models.usage_event import UsageEvent


def log_usage(
    db: Session,
    *,
    user_id: int,
    kind: str,
    model: str,
    account_id: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    image_count: int | None = None,
    seconds: float | None = None,
    post_id: str | None = None,
    draft_id: str | None = None,
) -> float:
    """Bir üretim olayını kaydeder ve hesaplanan USD maliyetini döner."""
    cost = compute_cost(
        kind,
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        image_count=image_count,
    )
    evt = UsageEvent(
        user_id=user_id,
        account_id=account_id,
        kind=kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        image_count=image_count,
        seconds=seconds,
        cost_usd=Decimal(str(cost)),
        post_id=post_id,
        draft_id=draft_id,
    )
    db.add(evt)
    db.commit()
    return cost


def _to_iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _to_iso_month(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def usage_summary(
    db: Session,
    *,
    user_id: int,
    account_id: int | None = None,
    days: int = 90,
) -> dict[str, Any]:
    """Kullanıcı için gün/ay/hesap/türkırılımlı maliyet özeti döner."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    q = db.query(UsageEvent).filter(
        UsageEvent.user_id == user_id, UsageEvent.timestamp >= start
    )
    if account_id is not None:
        q = q.filter(UsageEvent.account_id == account_id)
    events = q.all()

    total = sum((float(e.cost_usd or 0) for e in events), 0.0)
    today_key = _to_iso_date(now)
    today_total = 0.0
    by_day: dict[str, float] = {}
    by_month: dict[str, float] = {}
    by_kind: dict[str, dict[str, float]] = {}
    by_account: dict[int, float] = {}

    for e in events:
        cost = float(e.cost_usd or 0)
        d = _to_iso_date(e.timestamp)
        m = _to_iso_month(e.timestamp)
        by_day[d] = by_day.get(d, 0.0) + cost
        by_month[m] = by_month.get(m, 0.0) + cost
        if d == today_key:
            today_total += cost
        entry = by_kind.setdefault(e.kind, {"count": 0.0, "cost_usd": 0.0})
        entry["count"] += 1
        entry["cost_usd"] += cost
        if e.account_id is not None:
            by_account[e.account_id] = by_account.get(e.account_id, 0.0) + cost

    accounts_named: list[dict[str, Any]] = []
    if by_account:
        accs = (
            db.query(Account.id, Account.name)
            .filter(Account.id.in_(list(by_account.keys())))
            .all()
        )
        name_by_id = {a.id: a.name for a in accs}
        for aid, c in by_account.items():
            accounts_named.append(
                {"account_id": aid, "account_name": name_by_id.get(aid, "(silinmiş)"), "cost_usd": round(c, 4)}
            )

    by_day_list = [
        {"date": d, "cost_usd": round(c, 4)} for d, c in sorted(by_day.items())
    ]
    by_month_list = [
        {"month": m, "cost_usd": round(c, 4)} for m, c in sorted(by_month.items())
    ]
    by_kind_list = [
        {"kind": k, "count": int(v["count"]), "cost_usd": round(v["cost_usd"], 4)}
        for k, v in sorted(by_kind.items())
    ]

    # Ortalama günlük: gerçekleşen gün sayısına böl (boş günler dahil değil → daha gerçekçi)
    avg_daily = round(total / len(by_day), 4) if by_day else 0.0

    month_key = _to_iso_month(now)
    this_month = by_month.get(month_key, 0.0)

    return {
        "total_usd": round(total, 4),
        "today_usd": round(today_total, 4),
        "this_month_usd": round(this_month, 4),
        "average_daily_usd": avg_daily,
        "by_day": by_day_list,
        "by_month": by_month_list,
        "by_kind": by_kind_list,
        "by_account": accounts_named,
    }


def cost_for_post(db: Session, *, user_id: int, post_id: str) -> float:
    if not post_id:
        return 0.0
    total = (
        db.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0))
        .filter(UsageEvent.user_id == user_id, UsageEvent.post_id == post_id)
        .scalar()
    )
    return float(total or 0)


def cost_for_draft(db: Session, *, user_id: int, draft_id: str) -> float:
    if not draft_id:
        return 0.0
    total = (
        db.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0))
        .filter(UsageEvent.user_id == user_id, UsageEvent.draft_id == draft_id)
        .scalar()
    )
    return float(total or 0)
