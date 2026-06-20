"""
campaign_intent.py — LLM tabanlı kampanya niyeti çıkarımı.

business_chat.py'deki `_parse_campaign_intent` regex parser'ının yerine geçer.
İndirim oranı, kampanya tarihleri ("bu ayın 20'si", "gelecek ayın 11'i",
"3 gün sonra", "haftaya"), ürün sayısı ve seçim kriterini tek bir gpt-4o-mini
çağrısıyla çıkarır. Regex yok, hardcoded tarih kalıbı yok.

Göreli tarihleri çözebilmek için sunucunun BUGÜNKÜ tarihi prompt'a enjekte
edilir. Çıktı şekli `_parse_campaign_intent` ile birebir aynı — drop-in.

Dönüş:
    {
        "discount_pct":   float,        # 0.0 = indirim belirtilmemiş
        "campaign_start": "YYYY-MM-DD" | None,
        "campaign_end":   "YYYY-MM-DD" | None,
        "duration_days":  int | None,
        "product_count":  int,          # default 1
        "select_by":      "top_sales" | "top_margin" | None,
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional


_PROMPT = (
    "Bir e-ticaret operatörünün kampanya komutundan yapısal bilgi çıkar.\n"
    "Bugünün tarihi: {today} ({weekday}). Göreli tarihleri buna göre hesapla "
    "('bu ayın 20'si', 'gelecek ayın 11'i', '3 gün sonra', 'haftaya' vb.).\n"
    "SADECE şu JSON'u döndür, başka hiçbir şey yazma:\n"
    "{{\n"
    '  "discount_pct": <sayı, indirim yüzdesi; yoksa 0>,\n'
    '  "campaign_start": "<YYYY-MM-DD veya null>",\n'
    '  "campaign_end": "<YYYY-MM-DD veya null>",\n'
    '  "duration_days": <gün sayısı veya null>,\n'
    '  "product_count": <kaç ürün; belirtilmemişse 1>,\n'
    '  "select_by": "<top_sales | top_margin | null>"\n'
    "}}\n"
    "Kurallar:\n"
    "- 'en çok satan' / 'çok satılan' → select_by='top_sales'.\n"
    "- 'en kârlı' / 'en karlı' → select_by='top_margin'.\n"
    "- 'en çok satan 2 ürün' → product_count=2, select_by='top_sales'.\n"
    "- Belirli bir ürün adı geçiyorsa (kriter değil) select_by=null, product_count=1.\n"
    "- Tarih yalnızca açıkça belirtilmişse doldur; yoksa null bırak (varsayım yapma).\n"
    "- 'X günlük' / 'X gün' → duration_days=X. '1 haftalık' → duration_days=7."
)


def parse_campaign_intent(question: str, *, api_key: Optional[str] = None) -> dict:
    """Kampanya komutundan yapısal niyet çıkar. Fail-safe: hata durumunda
    _parse_campaign_intent ile aynı nötr default'u döner (sistemde mutasyon
    tetiklemez — discount 0, product_count 1)."""
    default = {
        "discount_pct": 0.0,
        "campaign_start": None,
        "campaign_end": None,
        "duration_days": None,
        "product_count": 1,
        "select_by": None,
    }

    q = (question or "").strip()
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not q or not key:
        return default

    today = datetime.now()
    prompt = _PROMPT.format(
        today=today.strftime("%Y-%m-%d"),
        weekday=today.strftime("%A"),
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=10)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": q},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = (completion.choices[0].message.content or "").strip()
        data = json.loads(raw)
    except Exception as exc:
        print(f"[CAMPAIGN_INTENT] failed: {exc}")
        return default

    out = dict(default)

    # discount_pct
    try:
        dp = float(data.get("discount_pct") or 0)
        out["discount_pct"] = dp if dp > 0 else 0.0
    except (TypeError, ValueError):
        pass

    # product_count
    try:
        pc = int(data.get("product_count") or 1)
        out["product_count"] = max(1, pc)
    except (TypeError, ValueError):
        pass

    # select_by
    sb = data.get("select_by")
    if sb in ("top_sales", "top_margin"):
        out["select_by"] = sb

    # duration_days
    try:
        dd = data.get("duration_days")
        out["duration_days"] = int(dd) if dd not in (None, "", "null") else None
    except (TypeError, ValueError):
        out["duration_days"] = None

    # tarihler — sadece geçerli YYYY-MM-DD kabul et
    out["campaign_start"] = _valid_date(data.get("campaign_start"))
    out["campaign_end"] = _valid_date(data.get("campaign_end"))

    # --- _parse_campaign_intent ile aynı post-processing (downstream parite) ---
    if out["campaign_start"] and out["duration_days"] and not out["campaign_end"]:
        start = datetime.strptime(out["campaign_start"], "%Y-%m-%d")
        out["campaign_end"] = (
            start + timedelta(days=out["duration_days"])
        ).strftime("%Y-%m-%d")

    if not out["campaign_start"]:
        out["campaign_start"] = today.strftime("%Y-%m-%d")
        if out["duration_days"] and not out["campaign_end"]:
            out["campaign_end"] = (
                today + timedelta(days=out["duration_days"])
            ).strftime("%Y-%m-%d")

    return out


def _valid_date(v) -> str | None:
    """'YYYY-MM-DD' formatını doğrula; geçersizse None."""
    if not v or not isinstance(v, str):
        return None
    v = v.strip()
    if v.lower() in ("null", "none", ""):
        return None
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return v
    except ValueError:
        return None