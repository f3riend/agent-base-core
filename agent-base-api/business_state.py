"""
Business State Graph — aggregated operational understanding for autonomous planning.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from db import DEFAULT_USER_ID, execute_query, now_iso


def _count_by_status(table: str, user_id: int, status_col: str = "status") -> dict:
    rows = execute_query(
        f"""
        SELECT {status_col}, COUNT(*) as cnt
        FROM {table}
        WHERE user_id=?
        GROUP BY {status_col}
        """,
        (user_id,),
    )
    return {r[status_col]: r["cnt"] for r in rows} if rows else {}


def build_business_state(user_id: int = DEFAULT_USER_ID, store_id: int | None = None) -> dict:
    """Aggregate current business snapshot."""
    ts = now_iso()

    workflow_stats = _count_by_status("workflow_instances", user_id)
    task_stats = _count_by_status("ai_tasks", user_id)

    items_query = "SELECT id, name, stock, sales, price, store_id FROM items WHERE user_id=?"
    params: list = [user_id]
    if store_id:
        items_query += " AND store_id=?"
        params.append(store_id)

    items = execute_query(items_query, tuple(params))

    total_stock = sum(i["stock"] or 0 for i in items)
    low_stock_items = [i for i in items if (i["stock"] or 0) < 10]
    top_sellers = sorted(items, key=lambda x: -(x["sales"] or 0))[:5]

    tool_rows = execute_query(
        """
        SELECT te.tool_name, te.status, COUNT(*) as cnt
        FROM tool_executions te
        JOIN ai_tasks t ON t.id = te.task_id
        WHERE t.user_id=?
        GROUP BY te.tool_name, te.status
        """,
        (user_id,),
    )
    tool_performance: dict[str, dict] = {}
    for r in tool_rows:
        name = r["tool_name"]
        if name not in tool_performance:
            tool_performance[name] = {"success": 0, "failed": 0, "total": 0}
        tool_performance[name]["total"] += r["cnt"]
        if r["status"] == "success":
            tool_performance[name]["success"] += r["cnt"]
        else:
            tool_performance[name]["failed"] += r["cnt"]

    recent_campaigns = execute_query(
        """
        SELECT workflow_name, status, created_at, metadata
        FROM workflow_instances
        WHERE user_id=?
          AND workflow_name LIKE '%campaign%'
           OR workflow_name LIKE '%instagram%'
           OR workflow_name LIKE '%marketing%'
        ORDER BY id DESC LIMIT 10
        """,
        (user_id,),
    )

    pending_approvals = execute_query(
        """
        SELECT COUNT(*) as cnt FROM approval_requests
        WHERE user_id=? AND status='pending'
        """,
        (user_id,),
        one=True,
    )
    pending_count = pending_approvals["cnt"] if pending_approvals else 0

    negative_reviews = 0
    try:
        nr = execute_query(
            """
            SELECT COUNT(*) as cnt FROM reviews
            WHERE rating <= 2
            """,
            (),
            one=True,
        )
        negative_reviews = nr["cnt"] if nr else 0
    except Exception:
        pass

    return {
        "generated_at": ts,
        "user_id": user_id,
        "store_id": store_id,
        "sales": {
            "total_item_sales": sum(i["sales"] or 0 for i in items),
            "top_products": [
                {"id": i["id"], "name": i["name"], "sales": i["sales"]}
                for i in top_sellers
            ],
        },
        "inventory": {
            "total_stock_units": total_stock,
            "low_stock_count": len(low_stock_items),
            "low_stock_items": [
                {"id": i["id"], "name": i["name"], "stock": i["stock"]}
                for i in low_stock_items[:5]
            ],
            "health": "critical" if len(low_stock_items) > 3 else (
                "warning" if low_stock_items else "healthy"
            ),
        },
        "campaigns": {
            "active_count": workflow_stats.get("running", 0) + workflow_stats.get("scheduled", 0),
            "recent": [
                {
                    "name": r["workflow_name"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in recent_campaigns
            ],
        },
        "workflows": workflow_stats,
        "tasks": task_stats,
        "engagement": {
            "pending_approvals": pending_count,
            "negative_reviews": negative_reviews,
        },
        "tool_performance": tool_performance,
        "sentiment": _infer_sentiment(negative_reviews, workflow_stats),
    }


def _infer_sentiment(negative_reviews: int, workflow_stats: dict) -> str:
    if negative_reviews >= 5:
        return "negative"
    failed = workflow_stats.get("cancelled", 0)
    if failed > 3:
        return "mixed"
    return "positive"


def state_summary_for_planner(state: dict) -> str:
    lines = [
        f"Stok sağlığı: {state['inventory']['health']}",
        f"Düşük stok ürün: {state['inventory']['low_stock_count']}",
        f"Aktif kampanya iş akışı: {state['campaigns']['active_count']}",
        f"Bekleyen onay: {state['engagement']['pending_approvals']}",
        f"Müşteri duyarlılığı: {state['sentiment']}",
    ]
    if state["sales"]["top_products"]:
        top = state["sales"]["top_products"][0]
        lines.append(f"En çok satan: {top['name']} ({top['sales']} satış)")
    return "\n".join(lines)
