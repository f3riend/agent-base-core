#!/usr/bin/env python3
"""
Continuous fake business activity simulator.

Drives the fake commerce timeline by calling internal_service directly.
No localhost HTTP — see internal_service for the loop-avoidance reasoning.
This module is safe to import inside the FastAPI process (no self-HTTP)
and also runnable as a standalone CLI.
"""

from __future__ import annotations

import os
import random
import time

import internal_service


INTERVAL = float(os.environ.get("SIM_INTERVAL_SEC", "8"))


def one_tick(store_id: int = 1, item_id: int = 1):
    action = random.choice([
        "order", "discount", "review", "question", "stock", "sales", "campaign",
    ])
    try:
        if action == "order":
            internal_service.create_order(
                store_id=store_id, item_id=item_id,
                quantity=random.randint(1, 2),
            )
        elif action == "discount":
            internal_service.update_discount(
                item_id=item_id,
                discount=random.randint(5, 30),
                store_id=store_id,
            )
        elif action == "review":
            rating = random.choices([5, 4, 2, 1], weights=[4, 2, 1, 1])[0]
            internal_service.create_review(
                store_id=store_id, item_id=item_id,
                rating=rating, comment="Simülasyon yorumu",
            )
        elif action == "question":
            internal_service.create_question(
                store_id=store_id, question="Stokta var mı?",
            )
        elif action == "stock":
            internal_service.update_stock(
                item_id=item_id, stock=random.randint(1, 15),
            )
        elif action == "sales":
            internal_service.update_sales(
                item_id=item_id,
                sales_change_pct=random.choice([-25, -10, 15, 40]),
            )
        elif action == "campaign":
            internal_service.create_campaign(
                store_id=store_id,
                name=f"Auto Kampanya {random.randint(1,999)}",
                discount_pct=random.randint(10, 25),
            )
        print(f"[SIM] {action} ok")
    except Exception as exc:
        print(f"[SIM] {action} fail: {exc}")


def main():
    print(f"[SIM] Aktivite simülatörü (her {INTERVAL}s)")
    while True:
        one_tick()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
