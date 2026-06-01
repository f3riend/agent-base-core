#!/usr/bin/env python3
"""
Fake commerce ecosystem seeder — stores, products, events, reviews.

Calls internal_service directly. There is NO HTTP to 127.0.0.1 in any code
path. Previously this module did `requests.post("http://127.0.0.1:8000/internal/...")`
which produced a self-referential request when invoked from /api/internal/seed-data
(same FastAPI process), starving the worker pool. The fix is structural: every
write goes through the in-process service layer.
"""

from __future__ import annotations

import random
import time

import internal_service


PRODUCTS = [
    ("Coca Cola", "beverages", 25, 45),
    ("Fanta", "beverages", 18, 38),
    ("Monster Energy", "beverages", 35, 62),
    ("Logitech G Pro X", "gaming", 899, 120),
    ("Razer Viper", "gaming", 649, 85),
    ("RK61 Keyboard", "electronics", 299, 55),
    ("Apple Watch", "electronics", 8999, 22),
    ("Samsung Monitor", "electronics", 4299, 18),
    ("Nike Hoodie", "clothing", 899, 40),
    ("JBL Speaker", "electronics", 1299, 33),
    ("Doritos", "snacks", 12, 90),
    ("Lays Classic", "snacks", 10, 75),
    ("Maybelline Lipstick", "beauty", 199, 28),
    ("Leather Wallet", "accessories", 249, 20),
]

STORE_NAMES = [
    "İçecek Dünyası", "TechZone", "ModaPlus", "GamerHub", "Güzellik Market",
]


def seed_stores(count: int = 3) -> list[int]:
    ids = []
    for i in range(count):
        name = random.choice(STORE_NAMES) + f" #{random.randint(1,99)}"
        result = internal_service.create_store(
            name=name, owner=f"Sahip {i+1}",
        )
        ids.append(result["id"])
        print(f"[SEED] Mağaza: {name} id={result['id']}")
    return ids


def seed_products(store_ids: list[int]) -> list[tuple[int, int]]:
    """Returns list of (store_id, item_id) pairs."""
    pairs: list[tuple[int, int]] = []
    for store_id in store_ids:
        for name, cat, price, stock in random.sample(PRODUCTS, min(5, len(PRODUCTS))):
            result = internal_service.create_product(
                store_id=store_id,
                name=name,
                price=price + random.uniform(-5, 20),
                stock=max(0, stock + random.randint(-10, 20)),
                category=cat,
            )
            pairs.append((store_id, result["id"]))
    print(f"[SEED] {len(pairs)} ürün oluşturuldu")
    return pairs


def seed_activity(store_id: int, item_id: int):
    if random.random() < 0.7:
        try:
            internal_service.create_order(
                store_id=store_id, item_id=item_id,
                quantity=random.randint(1, 3),
            )
        except ValueError as exc:
            print(f"[SEED] order skipped: {exc}")
    if random.random() < 0.5:
        try:
            internal_service.update_discount(
                item_id=item_id,
                discount=random.randint(10, 35),
                store_id=store_id,
            )
        except ValueError as exc:
            print(f"[SEED] discount skipped: {exc}")
    if random.random() < 0.4:
        internal_service.create_review(
            store_id=store_id,
            item_id=item_id,
            rating=random.choice([5, 5, 4, 2, 1]),
            comment=random.choice(["Harika!", "İdare eder", "Kötü deneyim"]),
            sentiment=random.choice(["positive", "negative", "neutral"]),
        )
    if random.random() < 0.3:
        internal_service.create_question(
            store_id=store_id,
            item_id=item_id,
            question="Bu ürün ne zaman gelir?",
        )
    if random.random() < 0.3:
        internal_service.create_campaign(
            store_id=store_id,
            name=f"Kampanya {random.randint(1,99)}",
            discount_pct=random.randint(15, 40),
        )


def run_seed(stores: int = 3, activity_per_item: bool = True):
    print("\n[SEED] Fake veri üretimi başlıyor…")
    store_ids = seed_stores(stores)
    product_pairs = seed_products(store_ids)
    for store_id, item_id in product_pairs:
        if activity_per_item:
            try:
                seed_activity(store_id, item_id)
            except Exception as exc:
                print(f"[SEED] aktivite hata (mağaza={store_id}, ürün={item_id}): {exc}")
        time.sleep(0.05)
    item_ids = [iid for _, iid in product_pairs]
    print(f"[SEED] Tamamlandı: {len(store_ids)} mağaza, {len(item_ids)} ürün\n")
    return {"stores": store_ids, "items": item_ids}


if __name__ == "__main__":
    run_seed()
