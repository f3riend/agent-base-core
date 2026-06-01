"""
Product import abstraction — Trendyol/Shopify/WooCommerce/Amazon (simulated).

Today everything is simulated; real provider integrations are intentionally
disabled. When real adapters land (future phase), they will sit behind a
provider interface and produce a normalized product dict that this module
imports through internal_service.create_product — never via self-HTTP.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any
from urllib.parse import urlparse

import internal_service


PROVIDERS = ("trendyol", "shopify", "woocommerce", "amazon", "manual", "json")


def detect_provider(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "trendyol" in host:
        return "trendyol"
    if "shopify" in host or "myshopify" in host:
        return "shopify"
    if "amazon" in host:
        return "amazon"
    if "woo" in host:
        return "woocommerce"
    return "manual"


def parse_product_json(data: dict) -> dict:
    return {
        "name": data.get("name") or data.get("title") or "İçe Aktarılan Ürün",
        "price": float(data.get("price", 99)),
        "stock": int(data.get("stock", 50)),
        "category": data.get("category", "imported"),
        "image_url": data.get("image") or data.get("image_url")
        or "https://placehold.co/200x200/png?text=Urun",
        "description": data.get("description", ""),
        "provider": data.get("provider", "json"),
    }


def simulate_import_from_url(url: str, store_id: int = 1) -> dict:
    provider = detect_provider(url)
    slug = re.sub(r"[^a-z0-9]+", "-", url.split("/")[-1][:30] or "urun").strip("-")
    product = {
        "name": f"İçe Aktarılan — {slug or 'Ürün'}",
        "price": round(random.uniform(49, 999), 2),
        "stock": random.randint(10, 100),
        "category": "imported",
        "image_url": f"https://placehold.co/200x200/png?text={slug[:12]}",
        "source_url": url,
        "provider": provider,
    }
    return import_product(store_id, product)


def import_product(store_id: int, product: dict) -> dict:
    """Create product via the in-process internal_service — no HTTP."""
    result = internal_service.create_product(
        store_id=store_id,
        name=product["name"],
        price=product["price"],
        stock=product["stock"],
        category=product.get("category", "imported"),
    )
    return {
        "success": True,
        "item_id": result["id"],
        "product": product,
        "message": f"Ürün içe aktarıldı: {product['name']}",
    }


def import_from_json_payload(store_id: int, payload: str | dict) -> dict:
    if isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = payload
    product = parse_product_json(data)
    return import_product(store_id, product)
