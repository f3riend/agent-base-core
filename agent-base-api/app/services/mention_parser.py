"""@mention / #template parser — saf Python regex, LLM yok.

Çıktı: MentionContext.
- @sitetescil          → scope="store",   store_slug="sitetescil"
- @fatih               → scope="user",    user_slug="fatih"
- @tümSatıcılar / @all → scope="all",     is_admin_query=True
- #razer               → product_slug="razer"
- #mers                → template_slug="mers"   (sadece # iki kere geçerse)
- normal soru          → scope="self"

clean_query: @/# token'ları temizlenmiş soru metni.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_ALL_TOKENS = {
    "all", "tumsaticilar", "tum", "tummagazalar",
    "platform", "global", "herkes", "hepsi",
}


def _normalize_tr(s: str) -> str:
    """Türkçe → ASCII normalize: ı→i, ü→u, ö→o, ş→s, ğ→g, ç→c."""
    table = str.maketrans({
        "ı": "i", "İ": "i", "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s",
        "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c",
    })
    return s.translate(table).lower()

_USER_HINT_TOKENS = {
    "fatih", "gokhan", "gökhan", "mehmet", "ali", "ayse", "ayşe",
    "ahmet", "mustafa", "user", "kullanıcı", "satıcı",
}

_MENTION_RE = re.compile(r"@([a-zA-Z0-9_çğıöşüÇĞİÖŞÜ]+)")
_HASHTAG_RE = re.compile(r"#([a-zA-Z0-9_çğıöşüÇĞİÖŞÜ]+)")


@dataclass
class MentionContext:
    scope: str            # "self" | "store" | "user" | "all"
    store_slug: str | None
    user_slug: str | None
    product_slug: str | None
    template_slug: str | None
    is_admin_query: bool
    clean_query: str
    raw_query: str


def _is_all_token(tok: str) -> bool:
    return _normalize_tr(tok) in _ALL_TOKENS


def _looks_like_user(tok: str) -> bool:
    return tok.lower() in _USER_HINT_TOKENS


def parse_mention(raw: str) -> MentionContext:
    raw = (raw or "").strip()
    if not raw:
        return MentionContext(
            scope="self", store_slug=None, user_slug=None,
            product_slug=None, template_slug=None,
            is_admin_query=False, clean_query="", raw_query="",
        )

    mentions = _MENTION_RE.findall(raw)
    hashtags = _HASHTAG_RE.findall(raw)

    scope = "self"
    store_slug: str | None = None
    user_slug: str | None = None
    is_admin_query = False

    if mentions:
        first = mentions[0]
        if _is_all_token(first):
            scope = "all"
            is_admin_query = True
        elif _looks_like_user(first):
            scope = "user"
            user_slug = first.lower()
        else:
            scope = "store"
            store_slug = first.lower()

    product_slug: str | None = None
    template_slug: str | None = None
    if hashtags:
        # İlk hashtag default olarak product. İkincisi varsa template.
        product_slug = hashtags[0].lower()
        if len(hashtags) >= 2:
            template_slug = hashtags[1].lower()

    cleaned = _MENTION_RE.sub("", raw)
    cleaned = _HASHTAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return MentionContext(
        scope=scope,
        store_slug=store_slug,
        user_slug=user_slug,
        product_slug=product_slug,
        template_slug=template_slug,
        is_admin_query=is_admin_query,
        clean_query=cleaned or raw,
        raw_query=raw,
    )
