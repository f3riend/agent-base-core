"""
benchmark_chat.py — business_chat.answer_question için davranışsal benchmark.

Ne ölçer:
  - Doğruluk + halüsinasyon: gerçek DB verisi (ground truth) yargıca enjekte
    edilir; gpt-4o-judge uydurma sayıyı yakalar.
  - Coreference: follow-up zincirinde active_entity deterministik kontrol edilir.
  - Token + latency: answer_question çıktısından okunur.
  - Scope: çok-ürünlü kullanıcıda (user_id=1) yanlış ürün sızıntısı.

Çalıştırma:
    uv run python benchmark_chat.py

Çıktı: kategori bazlı + genel skor tablosu, toplam token, ortalama latency,
ve benchmark_report.json (regression için saklanır).

NOT: Bu bir regression test setidir. Deploy öncesi koşulur; genel skor
eşiğin (DEFAULT 75) altına düşerse exit code 1 döner → CI gate olarak kullan.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from env_bootstrap import load_app_env

load_app_env()

from business_chat import answer_question  # noqa: E402

PANTENE_USER = 2          # tek ürün (Pantene) — coreference + doğruluk için
MULTI_USER = 1            # 45 ürün — scope sızıntısı testi için
SCORE_GATE = 75           # genel skor bunun altındaysa exit 1


# ---------------------------------------------------------------------------
# Ground truth — gerçek DB'den çek, yargıca enjekte et
# ---------------------------------------------------------------------------
def _fetch_ground_truth(user_id: int) -> dict:
    from app.core.database import SessionLocal
    from app.models.product import Product
    from app.models.store import Store
    from app.models.product_image import ProductImage  # noqa: F401
    from app.models.product_review import ProductReview  # noqa: F401
    from app.models.product_faq import ProductFaq  # noqa: F401
    from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
    from sqlalchemy import select, func

    facts: dict = {"products": []}
    try:
        with SessionLocal() as s:
            rows = list(s.scalars(
                select(Product)
                .join(Store, Product.store_id == Store.id)
                .where(Store.user_id == int(user_id))
            ).all())
            facts["product_count"] = len(rows)
            for p in rows[:10]:
                rc = s.scalar(
                    select(func.count(ProductReview.id))
                    .where(ProductReview.product_id == p.id)
                ) or 0
                sample_reviews = [
                    (c or "")[:120] for c in s.scalars(
                        select(ProductReview.content)
                        .where(ProductReview.product_id == p.id)
                        .where(ProductReview.content.isnot(None))
                        .limit(4)
                    ).all()
                ]
                facts["products"].append({
                    "name": p.name,
                    "price": float(p.price) if p.price is not None else None,
                    "cost_price": float(p.cost_price) if getattr(p, "cost_price", None) is not None else None,
                    "stock": getattr(p, "stock_quantity", None) if getattr(p, "stock_quantity", None) is not None else getattr(p, "stock", None),
                    "rating": float(p.rating) if p.rating is not None else None,
                    "rating_count": p.rating_count,
                    "review_rows": int(rc),
                    "sample_reviews": sample_reviews,
                })
    except Exception as exc:
        print(f"[BENCH] ground truth fetch failed: {exc}")
    return facts


# ---------------------------------------------------------------------------
# Test seti — (kategori, soru, beklenen_davranış, beklenen_entity, chain_id)
# Aynı chain_id'li ardışık sorular TEK session paylaşır (coreference testi).
# ---------------------------------------------------------------------------
SINGLE_CASES: list[dict] = [
    {"cat": "temel", "q": "Pantene fiyatı ne?",
     "expect": "Pantene'nin gerçek fiyatını vermeli, uydurmamalı."},
    {"cat": "temel", "q": "Kaç ürünüm var?",
     "expect": "Doğru ürün sayısını (tek ürün) söylemeli."},
    {"cat": "stok", "q": "Pantene stok durumu nasıl?",
     "expect": "Pantene'nin gerçek stok adedini vermeli."},
    {"cat": "yorum", "q": "Pantene hakkında müşteriler ne diyor?",
     "expect": "Gerçek yorumlara dayanmalı; yorum yoksa yok demeli."},
    {"cat": "puan", "q": "Pantene'nin ortalama puanı kaç?",
     "expect": "Gerçek rating'i vermeli."},
    {"cat": "kar", "q": "Pantene'den ne kadar kâr ediyorum?",
     "expect": "cost_price varsa marj hesaplamalı; yoksa 'maliyet verisi yok' demeli, UYDURMAMALI."},
    {"cat": "belirsiz", "q": "Genel durum nasıl?",
     "expect": "Eldeki veriyle kısa özet; jargon ve boş tavsiye olmamalı."},
    {"cat": "belirsiz", "q": "Bugün ne yapmalıyım?",
     "expect": "Veriye dayalı somut öneri ya da veri yoksa dürüstçe sınırını söylemeli."},
    {"cat": "nodata", "q": "Geçen ay kaç satış yaptım?",
     "expect": "Sipariş verisi yoksa 'satış kaydı yok / 0' demeli, sayı UYDURMAMALI."},
    {"cat": "nodata", "q": "Rakiplerim ne kadar satıyor?",
     "expect": "Bu veri sistemde yok; dürüstçe 'bilmiyorum/veri yok' demeli."},
    {"cat": "nodata", "q": "Müşterilerimin yaş ortalaması kaç?",
     "expect": "Müşteri/demografi verisi yoksa uydurmadan yok demeli."},
    {"cat": "meta", "q": "Bana yalan söylüyor musun?",
     "expect": "Kısa, makul bir cevap; alakasız mağaza/ürün sayımı yapmamalı."},
]

CHAIN_CASES: list[list[dict]] = [
    [
        {"cat": "coref", "q": "Pantene fiyatı ne?", "entity": "Pantene"},
        {"cat": "coref", "q": "Peki bu ürünün stok durumu ne?", "entity": "Pantene"},
        {"cat": "coref", "q": "Yorumları nasıl peki?", "entity": "Pantene"},
        {"cat": "hafiza", "q": "Az önce ne konuşuyorduk?",
         "expect": "Önceki turlarda konuşulan ürünü/konuyu (Pantene) doğru hatırlamalı; uydurmamalı."},
    ],
]

# Çok-ürünlü kullanıcıda scope sızıntısı
SCOPE_CASES: list[dict] = [
    {"cat": "scope", "q": "Kaç ürünüm var?",
     "expect": "user_id=1'in gerçek ürün sayısını vermeli; Pantene (başka kullanıcı) SIZMAMALI."},
    {"cat": "scope", "q": "Pantene fiyatı ne?",
     "expect": "Bu kullanıcının Pantene'si YOK; 'bulamadım/kayıt yok' demeli, başka ürün uydurmamalı."},
]


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------
_JUDGE_PROMPT = (
    "Sen bir e-ticaret asistanının cevaplarını puanlayan katı ama ADİL bir "
    "değerlendiricisin.\n"
    "Sana: kullanıcı sorusu, asistanın cevabı, beklenen davranış ve GERÇEK VERİ "
    "(ground truth) verilecek.\n"
    "HALÜSİNASYON tanımı (tek ve kesin): asistan GERÇEK VERİ'de KARŞILIĞI OLMAYAN "
    "bir sayı/iddia UYDURDUĞUNDA vardır. Bu ağır cezalandırılır.\n"
    "DÜRÜST YOKLUK halüsinasyon DEĞİLDİR: veri yoksa 'kayıt yok / bilmiyorum / 0 / "
    "bu veri bende yok' demek DOĞRU davranıştır → puanı YÜKSEK ver, halusinasyon=false.\n"
    "GERÇEK VERİ notları:\n"
    "- 'product_count' gerçek TOPLAM ürün sayısıdır; 'products' listesi yalnızca "
    "örnektir, eksik olabilir. Asistanın verdiği toplam product_count ile uyumluysa "
    "DOĞRUdur.\n"
    "- 'rating' kanonik ürün puanıdır (yorum ortalaması DEĞİL).\n"
    "- 'sample_reviews' gerçek yorum örnekleridir; asistanın yorum özeti bunlarla "
    "uyumluysa halüsinasyon değildir.\n"
    "SADECE şu JSON'u döndür:\n"
    '{"dogruluk": <0-5>, "alaka": <0-5>, "ton": <0-5>, '
    '"halusinasyon": <true|false>, "puan": <0-100>, "gerekce": "<tek cümle>"}'
)


def _judge(question: str, answer: str, expect: str, facts: dict, api_key: str) -> dict:
    from openai import OpenAI
    payload = (
        f"SORU: {question}\n\n"
        f"ASİSTAN CEVABI: {answer}\n\n"
        f"BEKLENEN DAVRANIŞ: {expect}\n\n"
        f"GERÇEK VERİ (ground truth): {json.dumps(facts, ensure_ascii=False)}"
    )
    try:
        client = OpenAI(api_key=api_key, timeout=20)
        c = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _JUDGE_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        return json.loads((c.choices[0].message.content or "{}").strip())
    except Exception as exc:
        print(f"[BENCH] judge failed: {exc}")
        return {"dogruluk": 0, "alaka": 0, "ton": 0, "halusinasyon": False,
                "puan": 0, "gerekce": f"judge error: {exc}"}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _run_one(case: dict, user_id: int, facts: dict, api_key: str,
             session_id: str | None) -> tuple[dict, str | None]:
    t0 = time.monotonic()
    try:
        resp = answer_question(case["q"], user_id=user_id, session_id=session_id)
    except Exception as exc:
        return ({"cat": case["cat"], "q": case["q"], "error": str(exc),
                 "puan": 0, "tokens": 0, "latency_ms": 0}, session_id)
    wall = int((time.monotonic() - t0) * 1000)

    answer = resp.get("answer") or ""
    sid = resp.get("session_id") or session_id

    record: dict[str, Any] = {
        "cat": case["cat"],
        "q": case["q"],
        "answer": answer[:200],
        "active_entity": resp.get("active_entity"),
        "is_followup": resp.get("is_followup"),
        "tokens": resp.get("tokens_used") or 0,
        "latency_ms": resp.get("latency_ms") or wall,
        "mode": resp.get("mode"),
    }

    # Coreference: deterministik entity kontrolü
    if case.get("entity"):
        ent = (resp.get("active_entity") or "")
        rewritten = (resp.get("resolved_question") or "")
        ok = case["entity"].lower() in ent.lower() or case["entity"].lower() in rewritten.lower()
        record["coref_ok"] = ok
        record["puan"] = 100 if ok else 0
        record["gerekce"] = (
            f"entity={ent!r} (beklenen {case['entity']!r})"
        )
        return record, sid

    # Diğerleri: LLM judge
    verdict = _judge(case["q"], answer, case.get("expect", ""), facts, api_key)
    record["puan"] = verdict.get("puan", 0)
    record["halusinasyon"] = verdict.get("halusinasyon", False)
    record["dogruluk"] = verdict.get("dogruluk")
    record["gerekce"] = verdict.get("gerekce", "")
    return record, sid


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY yok — çıkılıyor.")
        return 1

    facts_pantene = _fetch_ground_truth(PANTENE_USER)
    facts_multi = _fetch_ground_truth(MULTI_USER)
    print(f"[BENCH] user={PANTENE_USER} ground truth: {facts_pantene.get('product_count')} ürün")
    print(f"[BENCH] user={MULTI_USER} ground truth: {facts_multi.get('product_count')} ürün\n")

    results: list[dict] = []

    # Single cases (Pantene user)
    for case in SINGLE_CASES:
        rec, _ = _run_one(case, PANTENE_USER, facts_pantene, api_key, None)
        results.append(rec)
        print(f"  [{rec['cat']:8}] {rec['puan']:3}/100  {case['q'][:45]}")

    # Chains (coreference, shared session)
    for chain in CHAIN_CASES:
        sid = None
        for case in chain:
            rec, sid = _run_one(case, PANTENE_USER, facts_pantene, api_key, sid)
            results.append(rec)
            mark = "OK" if rec.get("coref_ok") else "X"
            print(f"  [{rec['cat']:8}] {rec['puan']:3}/100 [{mark}] {case['q'][:40]}")

    # Scope cases (multi-product user)
    for case in SCOPE_CASES:
        rec, _ = _run_one(case, MULTI_USER, facts_multi, api_key, None)
        results.append(rec)
        print(f"  [{rec['cat']:8}] {rec['puan']:3}/100  {case['q'][:45]}")

    # --- Aggregate ---
    by_cat: dict[str, list[int]] = {}
    for r in results:
        by_cat.setdefault(r["cat"], []).append(r.get("puan", 0))

    total_tokens = sum(r.get("tokens", 0) for r in results)
    lat = [r.get("latency_ms", 0) for r in results if r.get("latency_ms")]
    avg_lat = int(sum(lat) / len(lat)) if lat else 0
    hallu = sum(1 for r in results if r.get("halusinasyon"))
    overall = round(sum(r.get("puan", 0) for r in results) / len(results), 1)

    print("\n" + "=" * 52)
    print("KATEGORİ BAZLI SKOR")
    print("=" * 52)
    for cat, scores in sorted(by_cat.items()):
        avg = round(sum(scores) / len(scores), 1)
        print(f"  {cat:10} {avg:5}/100   ({len(scores)} soru)")

    print("-" * 52)
    print(f"  GENEL SKOR        {overall}/100")
    print(f"  Halüsinasyon      {hallu} adet")
    print(f"  Toplam token      {total_tokens}")
    print(f"  Ort. latency      {avg_lat} ms")
    print("=" * 52)

    report = {
        "overall": overall,
        "by_category": {c: round(sum(s) / len(s), 1) for c, s in by_cat.items()},
        "hallucinations": hallu,
        "total_tokens": total_tokens,
        "avg_latency_ms": avg_lat,
        "results": results,
    }
    with open("benchmark_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nbenchmark_report.json yazıldı.")

    return 0 if overall >= SCORE_GATE else 1


if __name__ == "__main__":
    sys.exit(main())