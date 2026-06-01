import json
import time
from urllib.parse import unquote, urlparse

import requests


GRAPH_URL = "https://graph.facebook.com/v22.0"


def _path_suggests_video(url: str) -> bool:
    base = (url or "").strip().split("?", 1)[0].lower()
    try:
        tail = unquote(base).lower()
    except Exception:
        tail = base
    return tail.endswith((".mp4", ".webm", ".mov", ".m4v"))


def _path_suggests_image(url: str) -> bool:
    base = (url or "").strip().split("?", 1)[0].lower()
    try:
        tail = unquote(base).lower()
    except Exception:
        tail = base
    return tail.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp"))


def _host_suggests_generated_video(url: str) -> bool:
    try:
        host = urlparse((url or "").strip()).netloc.lower()
    except Exception:
        return False
    return any(x in host for x in ("fal.media", "fal-cdn", "cdn.fal"))


def _heuristic_media_bucket(url: str) -> str | None:
    """When HTTP probes disagree, classify by path extension or known video CDN hosts."""
    if _path_suggests_image(url):
        return "image"
    if _path_suggests_video(url) or _host_suggests_generated_video(url):
        return "video"
    return None


def _http_probe_content_type(url: str) -> tuple[int, str]:
    """Return (http_status, content_type_first_part). status 0 means transport failure."""
    u = (url or "").strip()
    if not u:
        return 0, ""
    headers = {"User-Agent": "facebookexternalhit/1.1", "Accept": "*/*"}
    try:
        head = requests.head(u, timeout=15, allow_redirects=True, headers=headers)
        if head.status_code < 400:
            ct = (head.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ct:
                return head.status_code, ct
    except requests.RequestException:
        pass
    try:
        with requests.get(u, timeout=25, stream=True, headers=headers, allow_redirects=True) as r:
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return r.status_code, ct
    except requests.RequestException:
        return 0, ""


def ordered_publish_urls_for_stories(
    original: list[str],
    carousel_images: list[str],
    reel_videos: list[str],
) -> list[str]:
    """Preserve ``original`` order; only URLs that passed ``partition_publish_media_urls``."""
    allowed = {s.strip() for s in carousel_images if (s or "").strip()} | {s.strip() for s in reel_videos if (s or "").strip()}
    out: list[str] = []
    seen: set[str] = set()
    for raw in original:
        u = (raw or "").strip()
        if not u or u not in allowed or u in seen:
            continue
        out.append(u)
        seen.add(u)
    return out


def collect_image_urls_for_publish_preflight(
    image_urls: list[str],
    *,
    want_feed: bool,
    want_story: bool,
    want_facebook: bool,
) -> list[str]:
    """
    Image-only URL list for preflight (HTTPS + image Content-Type) before Graph publish.
    Skips the single-video feed path where no raster image is uploaded.
    """
    if not (want_feed or want_story or want_facebook):
        return []
    cleaned = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if not cleaned:
        return []
    carousel_images, reel_videos = partition_publish_media_urls(cleaned)
    fallback_media_url = cleaned[0] if cleaned else ""
    story_urls = ordered_publish_urls_for_stories(cleaned, carousel_images, reel_videos)
    carousel_set = {s.strip() for s in carousel_images if (s or "").strip()}
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        t = (u or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    if len(reel_videos) != 1:
        if len(carousel_images) > 1:
            for u in carousel_images:
                add(u)
        else:
            u0 = carousel_images[0] if carousel_images else fallback_media_url
            if u0:
                add(u0)
    for su in story_urls:
        if su in carousel_set:
            add(su)
    return out


def _page_picture_url(page: dict) -> str:
    pic = page.get("picture")
    if isinstance(pic, dict):
        inner = pic.get("data")
        if isinstance(inner, dict):
            u = str(inner.get("url") or "").strip()
            if u:
                return u
    return ""


def _page_tasks(page: dict) -> list[str]:
    raw = page.get("tasks")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def list_graph_publish_destinations(access_token: str) -> list[dict[str, object]]:
    """
    Same layout as check.html: one Facebook card per Page, plus an Instagram card when linked.

    Uses ``/me/accounts`` with pictures and Page ``tasks`` (role capabilities). If ``tasks``
    cannot be read with this token, retries without that field.
    """
    token = (access_token or "").strip()
    if not token:
        raise RuntimeError("Instagram access token gerekli.")

    fields_with_tasks = (
        "id,name,picture{url},instagram_business_account{id,username,profile_picture_url},tasks"
    )
    fields_basic = "id,name,picture{url},instagram_business_account{id,username,profile_picture_url}"

    def _fetch(fields: str) -> list[dict]:
        me_resp = requests.get(
            f"{GRAPH_URL}/me/accounts",
            params={"access_token": token, "fields": fields},
            timeout=30,
        )
        me_data = me_resp.json() if me_resp.content else {}
        if not isinstance(me_data, dict):
            raise RuntimeError("Sayfa listesi okunamadi (gecersiz yanit).")
        if _graph_error_payload(me_data):
            raise RuntimeError(_format_graph_api_failure(me_data))
        out_pages: list[dict] = []
        for page in me_data.get("data") or []:
            if isinstance(page, dict):
                out_pages.append(page)
        return out_pages

    try:
        pages = _fetch(fields_with_tasks)
    except RuntimeError as exc:
        if "tasks" in str(exc).lower():
            pages = _fetch(fields_basic)
        else:
            raise

    cards: list[dict[str, object]] = []
    for page in pages:
        pid = str(page.get("id") or "").strip()
        pname = str(page.get("name") or "").strip()
        if not pid:
            continue
        pic_url = _page_picture_url(page)
        tasks = _page_tasks(page)
        cards.append(
            {
                "kind": "facebook",
                "page_id": pid,
                "name": pname or pid,
                "picture_url": pic_url,
                "tasks": tasks,
            }
        )
        ig = page.get("instagram_business_account")
        if isinstance(ig, dict):
            ig_id = str(ig.get("id") or "").strip()
            ig_user = str(ig.get("username") or "").strip()
            ig_pic = str(ig.get("profile_picture_url") or "").strip()
            if ig_id:
                cards.append(
                    {
                        "kind": "instagram",
                        "ig_user_id": ig_id,
                        "username": ig_user,
                        "page_id": pid,
                        "picture_url": ig_pic,
                        "tasks": tasks,
                    }
                )
    return cards


def list_instagram_accounts_for_user_token(access_token: str) -> list[dict[str, str]]:
    """
    Pages from /me/accounts that have an instagram_business_account.
    Used by the app UI so the user can pick which IG Business profile to store.
    """
    token = (access_token or "").strip()
    if not token:
        raise RuntimeError("Instagram access token gerekli.")
    me_resp = requests.get(
        f"{GRAPH_URL}/me/accounts",
        params={
            "access_token": token,
            "fields": "id,name,instagram_business_account{id,username}",
        },
        timeout=30,
    )
    me_data = me_resp.json() if me_resp.content else {}
    if not isinstance(me_data, dict):
        raise RuntimeError("Instagram hesap listesi okunamadi (gecersiz yanit).")
    if _graph_error_payload(me_data):
        raise RuntimeError(_format_graph_api_failure(me_data))
    out: list[dict[str, str]] = []
    for page in me_data.get("data") or []:
        if not isinstance(page, dict):
            continue
        pid = str(page.get("id") or "").strip()
        pname = str(page.get("name") or "").strip()
        ig = page.get("instagram_business_account")
        if not isinstance(ig, dict):
            continue
        ig_id = str(ig.get("id") or "").strip()
        if not ig_id:
            continue
        ig_user = str(ig.get("username") or "").strip()
        out.append(
            {
                "facebook_page_id": pid,
                "facebook_page_name": pname,
                "instagram_user_id": ig_id,
                "instagram_username": ig_user,
            }
        )
    return out


def _fetch_accounts(access_token: str) -> list[dict]:
    """Return the raw page list from /me/accounts."""
    me_resp = requests.get(
        f"{GRAPH_URL}/me/accounts",
        params={"access_token": access_token},
        timeout=30,
    )
    me_data = me_resp.json() if me_resp.content else {}
    if not isinstance(me_data, dict):
        raise RuntimeError("Instagram hesaplari okunamadi (gecersiz yanit).")
    if _graph_error_payload(me_data):
        raise RuntimeError(_format_graph_api_failure(me_data))
    return list(me_data.get("data") or [])


def resolve_instagram_user_id_from_access_token(access_token: str) -> str:
    """Public: IG Business Account id from a User/Page token (same traversal as internal resolver)."""
    return _resolve_instagram_user_id_from_token((access_token or "").strip())


def resolve_single_facebook_page_id_if_obvious(access_token: str) -> str:
    """If ``/me/accounts`` returns exactly one Page, return its id (safe default for token-only setups)."""
    token = (access_token or "").strip()
    if not token:
        return ""
    try:
        pages = _fetch_accounts(token)
    except RuntimeError as exc:
        if is_meta_application_request_limit_error(str(exc)):
            raise
        return ""
    if len(pages) != 1:
        return ""
    return str((pages[0] or {}).get("id") or "").strip()


def _resolve_instagram_user_id_from_token(access_token: str) -> str:
    """
    Resolve Instagram Business Account ID directly from a valid user token.
    Mirrors the working flow used in local test.py.
    """
    for page in _fetch_accounts(access_token):
        page_id = str((page or {}).get("id") or "").strip()
        if not page_id:
            continue
        page_resp = requests.get(
            f"{GRAPH_URL}/{page_id}",
            params={"fields": "instagram_business_account,connected_instagram_account", "access_token": access_token},
            timeout=30,
        )
        page_data = page_resp.json() if page_resp.content else {}
        if not isinstance(page_data, dict):
            continue
        if _graph_error_payload(page_data):
            continue
        ig = page_data.get("instagram_business_account") or page_data.get("connected_instagram_account") or {}
        ig_id = str((ig or {}).get("id") or "").strip()
        if ig_id:
            return ig_id
    raise RuntimeError("Token ile bagli Instagram Business Account ID bulunamadi.")


def resolve_facebook_page_credentials(access_token: str, facebook_page_id: str | None = None) -> tuple[str, str]:
    """
    Return (page_id, page_access_token) for the first (or specified) Facebook Page
    connected to the given user access token.

    The Page access token is required for publishing to Facebook Pages
    (/{page_id}/photos, /{page_id}/videos). It is fetched from /me/accounts
    exactly as test.py does.
    """
    pages = _fetch_accounts(access_token)
    for page in pages:
        pid = str((page or {}).get("id") or "").strip()
        ptok = str((page or {}).get("access_token") or "").strip()
        if not pid or not ptok:
            continue
        if facebook_page_id and pid != facebook_page_id.strip():
            continue
        return pid, ptok
    raise RuntimeError("Token ile bagli Facebook Page bulunamadi veya belirtilen page_id eslesmiyor.")


def post_photo_to_facebook_page(
    image_url: str,
    caption: str,
    access_token: str,
    facebook_page_id: str | None = None,
) -> dict:
    """
    Publish a photo to a Facebook Page (/{page_id}/photos).
    Uses Page access token resolved from /me/accounts.
    """
    page_id, page_token = resolve_facebook_page_credentials(access_token, facebook_page_id)
    url = f"{GRAPH_URL}/{page_id}/photos"
    resp = requests.post(
        url,
        data={"url": image_url, "message": caption, "access_token": page_token},
        timeout=60,
    )
    data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Facebook photos endpoint gecersiz yanit: {data}")
    if _graph_error_payload(data):
        raise RuntimeError(_format_graph_api_failure(data))
    if not data.get("id"):
        raise RuntimeError(f"Facebook photo yayinlanamadi: {data}")
    return {"photo_id": str(data["id"]), "facebook_page_id": page_id}


def post_multi_photo_to_facebook_page(
    image_urls: list[str],
    caption: str,
    access_token: str,
    facebook_page_id: str | None = None,
) -> dict:
    """Publish multiple photos as a single Facebook feed post."""
    page_id, page_token = resolve_facebook_page_credentials(access_token, facebook_page_id)
    urls = [str(u or "").strip() for u in image_urls if str(u or "").strip()]
    if len(urls) < 2:
        raise RuntimeError("Coklu Facebook post icin en az 2 gorsel gerekli.")
    media_ids: list[str] = []
    for idx, url in enumerate(urls):
        resp = requests.post(
            f"{GRAPH_URL}/{page_id}/photos",
            data={"url": url, "published": "false", "access_token": page_token},
            timeout=90,
        )
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Facebook photo upload gecersiz yanit ({idx + 1}/{len(urls)}): {data}")
        if _graph_error_payload(data):
            raise RuntimeError(_format_graph_api_failure(data))
        media_id = str(data.get("id") or "").strip()
        if not media_id:
            raise RuntimeError(f"Facebook photo upload id donmedi ({idx + 1}/{len(urls)}): {data}")
        media_ids.append(media_id)
    attached_media = [json.dumps({"media_fbid": media_id}) for media_id in media_ids]
    payload: dict[str, str] = {"message": (caption or "").strip(), "access_token": page_token}
    # Graph API expects attached_media as indexed fields: attached_media[0], attached_media[1], ...
    for idx, item in enumerate(attached_media):
        payload[f"attached_media[{idx}]"] = item
    feed_resp = requests.post(
        f"{GRAPH_URL}/{page_id}/feed",
        data=payload,
        timeout=90,
    )
    feed_data = feed_resp.json() if feed_resp.content else {}
    if not isinstance(feed_data, dict):
        raise RuntimeError(f"Facebook feed endpoint gecersiz yanit: {feed_data}")
    if _graph_error_payload(feed_data):
        raise RuntimeError(_format_graph_api_failure(feed_data))
    post_id = str(feed_data.get("id") or "").strip()
    if not post_id:
        raise RuntimeError(f"Facebook album post yayinlanamadi: {feed_data}")
    return {"post_id": post_id, "facebook_page_id": page_id, "items_count": len(media_ids)}


def post_video_to_facebook_page(
    video_url: str,
    caption: str,
    access_token: str,
    facebook_page_id: str | None = None,
) -> dict:
    """
    Publish a video to a Facebook Page (/{page_id}/videos).
    Uses Page access token resolved from /me/accounts.
    """
    page_id, page_token = resolve_facebook_page_credentials(access_token, facebook_page_id)
    url = f"{GRAPH_URL}/{page_id}/videos"
    resp = requests.post(
        url,
        data={"file_url": video_url, "description": caption, "access_token": page_token},
        timeout=120,
    )
    data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Facebook videos endpoint gecersiz yanit: {data}")
    if _graph_error_payload(data):
        raise RuntimeError(_format_graph_api_failure(data))
    if not data.get("id"):
        raise RuntimeError(f"Facebook video yayinlanamadi: {data}")
    return {"video_id": str(data["id"]), "facebook_page_id": page_id}


def _resolve_credentials(access_token: str | None, instagram_user_id: str | None) -> tuple[str, str]:
    token = (access_token or "").strip()
    user_id = (instagram_user_id or "").strip()
    if not token:
        raise RuntimeError("Instagram access token gerekli.")
    if not user_id:
        user_id = _resolve_instagram_user_id_from_token(token)
    return token, user_id


def _graph_error_payload(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    return err if isinstance(err, dict) else None


def _is_token_expired_error(data: dict) -> bool:
    err = _graph_error_payload(data)
    if not err:
        return False
    return int(err.get("code") or 0) == 190 and int(err.get("error_subcode") or 0) == 463


def _format_graph_api_failure(data: dict) -> str:
    """Human-readable message for Meta Graph `error` objects (and success body checks)."""
    err = _graph_error_payload(data)
    if not err:
        return str(data)
    code = int(err.get("code") or 0)
    sub = int(err.get("error_subcode") or 0)
    msg = (err.get("message") or "").strip()
    typ = (err.get("type") or "").strip()
    base = f"{msg} (type={typ}, code={code}" + (f", subcode={sub}" if sub else "") + ")"

    # OAuthException code 200 — "API access blocked" (not HTTP 200; Meta uses code field 200)
    if code == 200 and "access blocked" in msg.lower():
        return (
            f"{base} — Meta bu istek icin API erisimini engelledi. "
            "Olası nedenler: Uygulama Development modunda ve Instagram hesabi test kullanicisi/rolu degil; "
            "instagram_content_publish / gerekli izinler Advanced Access veya App Review onayi yok; "
            "uygulama kisitli veya Business dogrulamasi eksik. "
            "Meta Developer: App Mode, Use cases, Permissions, Instagram hesabi baglantisi ve "
            "Business Verification adimlarini kontrol edin."
        )
    if code == 190:
        return (
            f"{base} — Token gecersiz veya suresi dolmus; Instagram/Facebook yeniden baglantisini yapin "
            "(gerekirse uzun omurlu token + dogru izinler)."
        )
    # Container not ready for publish yet (often fixed by polling status_code before media_publish)
    if code == 9007 and sub == 2207027:
        return (
            f"{base} — Medya konteyneri henuz yayina hazir degildi. "
            "Sunucu artik konteyner FINISHED olana kadar bekliyor; yine de olursa birkac saniye sonra tekrar dene."
        )
    return base


def is_meta_application_request_limit_error(message: str) -> bool:
    """OAuthException (#4) — too many Graph calls in a short window (dev app / polling)."""
    t = (message or "").strip().lower()
    if "application request limit" in t:
        return True
    if "(#4)" in (message or "") and "oauth" in t:
        return True
    if "code=4" in t.replace(" ", "") and "oauth" in t:
        return True
    return False


def _merge_token_meta(response_body: dict, meta: dict | None) -> None:
    """If token was refreshed, expose new token + expires_in for API responses / DB update."""
    if meta is None:
        return
    tok = response_body.get("_refreshed_access_token")
    if tok:
        meta["instagram_access_token"] = str(tok)
    exp = response_body.get("_token_expires_in_seconds")
    if exp is not None:
        try:
            meta["token_expires_in_seconds"] = int(exp)
        except (TypeError, ValueError):
            pass


def _post_with_auto_refresh(
    url: str,
    data: dict,
    access_token: str,
    meta: dict | None = None,
) -> dict:
    """POST to Graph API with single-token flow and readable errors."""
    payload = dict(data)
    payload["access_token"] = access_token
    resp = requests.post(url, data=payload, timeout=60)
    parsed: dict = resp.json() if resp.content else {}
    if not isinstance(parsed, dict):
        parsed = {}

    if _is_token_expired_error(parsed):
        raise RuntimeError(
            "Instagram access token expired (190/463). "
            "Yeni User Access Token alip hesapta güncelleyin."
        )

    if _graph_error_payload(parsed):
        raise RuntimeError(_format_graph_api_failure(parsed))

    if resp.status_code != 200:
        raise RuntimeError(_format_graph_api_failure(parsed) if _graph_error_payload(parsed) else str(parsed))

    if _graph_error_payload(parsed):
        raise RuntimeError(_format_graph_api_failure(parsed))
    _merge_token_meta(parsed, meta)
    return parsed


def validate_instagram_image_url(image_url: str) -> str | None:
    u = (image_url or "").strip().lower()
    if not u.startswith("https://"):
        return "Instagram sadece herkese acik https gorsel URL kabul eder."
    blocked = ("localhost", "127.0.0.1", "192.168.", "10.0.", "172.16.", "file://", "0.0.0.0")
    if any(b in u for b in blocked):
        return "Yerel ag URL'leri Instagram tarafindan erisilemez."
    return None


def _is_permanent_instagram_container_error(snippet: str) -> bool:
    """True when re-polling the same container is unlikely to recover (saves rate limit)."""
    low = (snippet or "").lower()
    if "aspect ratio" in low and "not supported" in low:
        return True
    if "aspect ratio is not supported" in low:
        return True
    if "unsupported" in low and "ratio" in low:
        return True
    if "missing or invalid image" in low:
        return True
    if "only photo or video can be accepted" in low:
        return True
    if "invalid image" in low and "file" in low:
        return True
    if "36003" in snippet:
        return True
    return False


def wait_for_media_container_ready(
    container_id: str,
    access_token: str,
    *,
    poll_interval_sec: float = 3.5,
    max_wait_sec: float = 120.0,
) -> None:
    """
    Instagram creates a container first; publish only works when status_code is FINISHED.
    Without waiting, Graph returns OAuthException 9007 / subcode 2207027 (Media ID is not available).
    """
    cid = (container_id or "").strip()
    token = (access_token or "").strip()
    if not cid or not token:
        raise RuntimeError("Container beklemesi icin container_id ve access_token gerekli.")
    deadline = time.monotonic() + max_wait_sec
    # Transient ERROR (rare): at most one short backoff. Permanent validation errors: no retry.
    error_retries = 1
    error_backoff_sec = 6.0
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{GRAPH_URL}/{cid}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            time.sleep(poll_interval_sec)
            continue
        if _graph_error_payload(data):
            raise RuntimeError(_format_graph_api_failure(data))
        status = str(data.get("status_code") or "").strip().upper()
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            snippet = json.dumps(data, ensure_ascii=False)[:920]
            if status == "ERROR" and _is_permanent_instagram_container_error(snippet):
                raise RuntimeError(
                    f"Instagram medya konteyneri basarisiz: status_code={status}. detay={snippet} "
                    "Kalici dogrulama hatasi (or. aspect ratio); ayni URL ile tekrar denemeyin."
                )
            if status == "ERROR" and error_retries > 0:
                error_retries -= 1
                time.sleep(error_backoff_sec)
                continue
            raise RuntimeError(
                f"Instagram medya konteyneri basarisiz: status_code={status}. detay={snippet} "
                "Video: herkese acik MP4 (H.264+AAC), Reels icin 9:16 ve sure sinirlari. "
                "Carousel: tum kareler ayni en-boy (orn. 4:5 veya 1:1), JPEG/PNG."
            )
        if status == "PUBLISHED":
            return
        time.sleep(poll_interval_sec)
    raise RuntimeError(
        "Instagram medya konteyneri hazir olmadi (zaman asimi). Gorsel URL veya Instagram tarafini kontrol et."
    )


def probe_video_url(video_url: str) -> str | None:
    """Return an error string if *video_url* is not suitable for Instagram video/Reels; None if OK."""
    code, ct = _http_probe_content_type(video_url)
    if code == 0:
        return "Video URL on kontrolu basarisiz (baglanti)."
    if code >= 400:
        return f"Video URL HTTP {code}"
    if ct.startswith("text/html"):
        return "URL bir HTML sayfasi donduruyor."
    if ct.startswith("video/"):
        return None
    if ct == "application/octet-stream" and (_path_suggests_video(video_url) or _host_suggests_generated_video(video_url)):
        return None
    if ct and not ct.startswith("video/"):
        return f"URL video degil (Content-Type: {ct})"
    if not ct and _path_suggests_video(video_url):
        return None
    if not ct:
        return "Video URL Content-Type belirlenemedi (video/mp4 beklenir)."
    return None


def probe_image_url(image_url: str) -> str | None:
    code, ct = _http_probe_content_type(image_url)
    if code == 0:
        return "Gorsel URL on kontrolu basarisiz (baglanti)."
    if code >= 400:
        return f"Gorsel URL HTTP {code}"
    if ct.startswith("text/html"):
        return "URL bir HTML sayfasi donduruyor."
    if ct.startswith("image/"):
        return None
    if ct == "application/octet-stream" and _path_suggests_image(image_url):
        return None
    if ct and not ct.startswith("image/"):
        return f"URL gorsel degil (Content-Type: {ct})"
    if not ct and _path_suggests_image(image_url):
        return None
    if not ct:
        return "Gorsel URL Content-Type belirlenemedi (image/jpeg beklenir)."
    return None


def partition_publish_media_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """``urls`` sırasını koruyarak (carousel | Reels) için ayır.

    Dönüş: ``(instagram_carousel_image_urls, reel_or_single_video_urls)``.
    Video probe başarılıysa (veya yol/CDN ipucu video) önce video listesine yazılır.
    """
    images: list[str] = []
    videos: list[str] = []
    for raw in urls:
        u = (raw or "").strip()
        if not u:
            continue
        pv = probe_video_url(u)
        pi = probe_image_url(u)
        if pv is None:
            videos.append(u)
        elif pi is None:
            images.append(u)
        else:
            hint = _heuristic_media_bucket(u)
            if hint == "video":
                if pv and ("HTTP 404" in pv or "HTTP 410" in pv):
                    continue
                videos.append(u)
            elif hint == "image":
                if pi and ("HTTP 404" in pi or "HTTP 410" in pi):
                    continue
                images.append(u)
    return images, videos


def create_media_container(
    image_url: str,
    caption: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media"
    payload = {"image_url": image_url, "caption": caption}
    try:
        data = _post_with_auto_refresh(
            url,
            payload,
            token,
            meta=meta,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"Container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Container olusturulamadi: {data}")


def create_reel_container(
    video_url: str,
    caption: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    *,
    share_to_feed: bool = True,
    meta: dict | None = None,
) -> str:
    """Create an Instagram Reels container from a public ``video_url`` (Kling / hosted mp4).

    Meta currently expects ``media_type=REELS`` + ``video_url`` for API-published short video
    (classic ``media_type=VIDEO`` feed containers are unreliable/deprecated in practice).
    """
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media"
    payload: dict[str, str] = {
        "media_type": "REELS",
        "video_url": (video_url or "").strip(),
        "caption": (caption or "").strip(),
        "share_to_feed": "true" if share_to_feed else "false",
    }
    try:
        data = _post_with_auto_refresh(url, payload, token, meta=meta)
    except RuntimeError as exc:
        raise RuntimeError(f"Reels container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Reels container olusturulamadi: {data}")


def create_story_video_container(
    video_url: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """Instagram Story from a hosted video URL (``media_type=STORIES`` + ``video_url``)."""
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media"
    payload = {"media_type": "STORIES", "video_url": (video_url or "").strip()}
    try:
        data = _post_with_auto_refresh(url, payload, token, meta=meta)
    except RuntimeError as exc:
        raise RuntimeError(f"Video story container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Video story container olusturulamadi: {data}")


def create_story_container(
    image_url: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """
    Create an Instagram *Story* media container.

    Instagram Graph API requires `media_type=STORIES` for photo stories and
    does NOT support a `caption` parameter on story containers.
    """
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media"
    payload = {"media_type": "STORIES", "image_url": image_url}
    try:
        data = _post_with_auto_refresh(
            url,
            payload,
            token,
            meta=meta,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"Story container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Story container olusturulamadi: {data}")


def create_carousel_item_container(
    image_url: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """Create a child media container for an Instagram carousel."""
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media"
    payload = {"image_url": image_url, "is_carousel_item": "true"}
    try:
        data = _post_with_auto_refresh(url, payload, token, meta=meta)
    except RuntimeError as exc:
        raise RuntimeError(f"Carousel item container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Carousel item container olusturulamadi: {data}")


def create_carousel_container(
    children: list[str],
    caption: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """Create parent carousel container from child IDs."""
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    child_ids = [str(x).strip() for x in children if str(x).strip()]
    if len(child_ids) < 2:
        raise RuntimeError("Carousel icin en az 2 child medya gerekir.")
    url = f"{GRAPH_URL}/{user_id}/media"
    payload = {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": (caption or "").strip()}
    try:
        data = _post_with_auto_refresh(url, payload, token, meta=meta)
    except RuntimeError as exc:
        raise RuntimeError(f"Carousel container olusturulamadi: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Carousel container olusturulamadi: {data}")


def publish_media(
    container_id: str,
    access_token: str | None = None,
    instagram_user_id: str | None = None,
    meta: dict | None = None,
) -> str:
    token, user_id = _resolve_credentials(access_token, instagram_user_id)
    url = f"{GRAPH_URL}/{user_id}/media_publish"
    data: dict = {}
    for attempt in range(2):
        try:
            data = _post_with_auto_refresh(
                url,
                {"creation_id": container_id},
                token,
                meta=meta,
            )
            break
        except RuntimeError as exc:
            msg = str(exc)
            if attempt == 0 and "9007" in msg and "2207027" in msg:
                time.sleep(4.0)
                continue
            raise RuntimeError(f"Yayinlama basarisiz: {exc}") from exc
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    raise RuntimeError(f"Yayinlama basarisiz: {data}")
