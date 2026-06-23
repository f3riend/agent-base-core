#!/usr/bin/env python3
"""
Tenant-scope güvenlik testi — LLM/maliyet GEREKTİRMEZ, deterministiktir.

Amaç: custom SQL üreticisinin tenant izolasyonunu koda gömülü şekilde koruduğunu
doğrulamak. `_enforce_scope`, store_ids scope filtresi olmayan her custom SQL'i
reddetmeli (aksi halde sorgu tüm tabloyu tarar = kral/sadrazam izolasyon ihlali).

Çalıştırma:
    uv run python test_scope_security.py

CI/commit öncesi hızlı kapı olarak kullanılabilir; çıkış kodu != 0 ise bir
güvenlik kuralı bozulmuş demektir.
"""
import sys

from env_bootstrap import load_app_env

load_app_env()

from app.services.nl_to_sql import _enforce_scope, _is_safe  # noqa: E402


# (açıklama, sql, scope_beklenen, safe_beklenen)
CASES = [
    # --- Scope İÇEREN meşru sorgular: GEÇMELİ ---
    ("Basit store_ids filtreli",
     "SELECT name, price FROM products WHERE price > 1000 "
     "AND store_id = ANY(CAST(:store_ids AS uuid[]))",
     True, True),
    ("JOIN + store_ids",
     "SELECT p.name FROM products p JOIN stores s ON p.store_id = s.id "
     "WHERE s.id = ANY(CAST(:store_ids AS uuid[]))",
     True, True),
    ("Aggregate + store_ids",
     "SELECT COUNT(*) FROM products WHERE rating > 4.5 "
     "AND store_id = ANY(CAST(:store_ids AS uuid[]))",
     True, True),
    ("user_id + store_ids birlikte",
     "SELECT p.category, COUNT(p.id) FROM products p JOIN stores s "
     "ON p.store_id = s.id WHERE s.user_id = :user_id "
     "AND p.store_id = ANY(CAST(:store_ids AS uuid[])) GROUP BY p.category",
     True, True),

    # --- Scope İÇERMEYEN tehlikeli sorgular: REDDEDİLMELİ ---
    ("Filtresiz tüm tablo",
     "SELECT name, price FROM products WHERE price > 1000",
     False, True),
    ("Sadece user_id (store_ids yok)",
     "SELECT name FROM products p JOIN stores s ON p.store_id = s.id "
     "WHERE s.user_id = :user_id",
     False, True),
    ("Hiç WHERE yok",
     "SELECT MAX(price) - MIN(price) FROM products",
     False, True),
    ("Başka mağazaya sabit id ile sızma denemesi",
     "SELECT name FROM products WHERE store_id = 'bc004111-0000-0000-0000-000000000000'",
     False, True),

    # --- _is_safe ayrıca SELECT-dışını reddetmeli ---
    ("DELETE reddedilmeli (is_safe)",
     "DELETE FROM products WHERE store_id = ANY(CAST(:store_ids AS uuid[]))",
     True, False),
    ("DROP reddedilmeli (is_safe)",
     "SELECT 1; DROP TABLE products; --",
     False, False),
]


def main() -> int:
    failures = []
    print("=" * 60)
    print("TENANT-SCOPE GÜVENLİK TESTİ")
    print("=" * 60)
    for desc, sql, exp_scope, exp_safe in CASES:
        got_scope = _enforce_scope(sql)
        got_safe = _is_safe(sql)
        ok = (got_scope == exp_scope) and (got_safe == exp_safe)
        mark = "OK " if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        print(f"        scope: {got_scope} (beklenen {exp_scope}) | "
              f"safe: {got_safe} (beklenen {exp_safe})")
        if not ok:
            failures.append(desc)

    # Kombine kapı: bir custom SQL'in çalışması için HEM safe HEM scope'lu olmalı.
    # En kritik invariant: scope'suz hiçbir sorgu "geçerli" sayılmamalı.
    print("-" * 60)
    leaky = [
        desc for desc, sql, _, _ in CASES
        if _is_safe(sql) and not _enforce_scope(sql)
        and ":store_ids" not in sql
        and "WHERE store_id =" not in sql  # sabit-id sızma da scope'suz sayılır
    ]
    # leaky listesi, "safe görünüp scope'suz" = tehlikeli ama doğru reddedilen sorgular.
    # Bunların _enforce_scope tarafından False alması ZORUNLU (yukarıda kontrol edildi).

    print(f"\nSonuç: {len(CASES) - len(failures)}/{len(CASES)} geçti.")
    if failures:
        print("BAŞARISIZ:", ", ".join(failures))
        print("\n!!! GÜVENLİK KURALI BOZULMUŞ — commit ETME, düzelt. !!!")
        return 1
    print("Tüm güvenlik kuralları sağlam. Tenant izolasyonu koda gömülü.")
    return 0


if __name__ == "__main__":
    sys.exit(main())