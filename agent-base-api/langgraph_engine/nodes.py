"""
LangGraph node implementations.

Her node aynı imzaya sahip: `(state: RuleExecutionState) -> dict`. Dönüşler
mevcut state ile reducer'lar üzerinden birleştirilir. Hiçbir node doğrudan
state'i mutate etmez — return ile partial update verir.

Genel desen:
    1. trace start kaydı oluştur
    2. node işini yap (Pydantic validation içeren tool çağrıları, vb.)
    3. trace ok/failed kaydı oluştur
    4. partial state döndür

Hata durumunda: status="failed", last_error doldurulur, ama node exception
fırlatmaz — graph'ın kontrolünü kaybetmememiz gerekiyor.

NOT: approval_gate_node işin doğası gereği "no-op pre-interrupt" şeklinde
çalışır. LangGraph compile çağrısı bu node'u interrupt_before=[...] ile
işaretler — graph node'u çalıştırmadan ÖNCE duraklar. Operatör onay verince
graph state.approval.decision güncellenmiş olarak resume edilir, node ondan
sonra çalıştırılır ve karar yoluna göre branch eder.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

from langgraph_engine.state import (
    ApprovalDecision,
    EventContext,
    GeneratedContent,
    MonitorResult,
    PublishResult,
    RiskAssessment,
    RuleExecutionState,
    make_trace,
)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _rule_from_state(state: RuleExecutionState) -> dict:
    return state.get("rule") or {}


def _content_template(state: RuleExecutionState) -> str:
    return ((state.get("rule") or {}).get("content") or {}).get("template", "generic")


def _channel(state: RuleExecutionState) -> str:
    return ((state.get("rule") or {}).get("content") or {}).get("channel", "instagram")


def _action_config(state: RuleExecutionState, kind: str) -> dict:
    for a in (state.get("rule") or {}).get("actions", []):
        if a.get("kind") == kind:
            return a.get("config") or {}
    return {}


def _emit(tag: str, payload: dict, *, user_id: int | None = None):
    """observability._emit'e güvenli wrapper."""
    try:
        from observability import _emit as oemit
        oemit(tag, payload, persist=True, user_id=user_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Node: supervisor — graph girişinde context'i hazırla
# ---------------------------------------------------------------------------


def supervisor_node(state: RuleExecutionState) -> dict:
    """Tüm graph'ın "giriş" node'u. State'i log'lar, mevcut current_node'u
    siler, trace event başlatır.
    """
    t0 = time.monotonic()
    rule = _rule_from_state(state)
    event = state.get("event") or {}
    summary = (
        f"Kural #{state.get('rule_id')} tetiklendi: "
        f"{rule.get('name', '—')} (olay: {event.get('event_type')})"
    )
    _emit("RULE_EXECUTION_START", {
        "rule_id": state.get("rule_id"),
        "execution_id": state.get("execution_id"),
        "event_id": event.get("event_id"),
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "supervisor",
        "status": "running",
        "trace_events": [make_trace(
            "supervisor", "ok", summary,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: wait — gecikme talep edildi
# ---------------------------------------------------------------------------


def wait_node(state: RuleExecutionState) -> dict:
    """Gecikme zamanı geldi mi kontrol et — Tur 2: gerçek duraklat/resume.

    Davranış:
        - delay <= 0  → hemen devam et.
        - metadata.wait_resolved == True (resume_after_wait set etti) →
          süre dolmuş, hemen devam et.
        - Aksi halde: scheduled_entry oluştur (workflow_worker
          fire_due_schedules → resume_after_wait çağıracak),
          status='waiting_timer' yap, graph akışını DURAKLAT
          (LangGraph END'e gider, runtime row'u waiting_timer kabul eder).

    workflow_worker._handle_wait_resumes() entry'yi tetiklediğinde
    runtime.resume_after_wait() çağrılır, state.metadata.wait_resolved=True
    set edilir ve graph.invoke(None) ile bu node'a tekrar gelinir; bu sefer
    geçer.
    """
    t0 = time.monotonic()
    cfg = _action_config(state, "wait")
    delay = int(cfg.get("delay_seconds") or
                (state.get("rule") or {}).get("timing", {}).get("delay_seconds", 0))

    # Resume durumu — wait süresi dolmuş.
    if (state.get("metadata") or {}).get("wait_resolved"):
        return {
            "current_node": "wait",
            "status": "running",
            "trace_events": [make_trace(
                "wait", "ok", "Bekleme süresi doldu, akış devam ediyor.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    # Hiç bekleme gerekmiyorsa direkt geç.
    if delay <= 0:
        return {
            "current_node": "wait",
            "trace_events": [make_trace(
                "wait", "ok", "Bekleme yok, akış devam ediyor.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    fire_at = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()
    summary = (
        f"Bu kural {_humanize(delay)} sonra ({fire_at}) devam edecek "
        f"— graph duraklatıldı, planlama oluşturuldu."
    )

    # Scheduling_service'e fire entry — workflow_worker'ın görüp resume
    # çağıracağı tetik. payload.resume_after_wait=True işareti kritik.
    try:
        from scheduling_service import create_schedule
        create_schedule(
            user_id=state.get("user_id") or 1,
            kind="workflow",
            scheduled_at=fire_at,
            title=f"Kural #{state.get('rule_id')} devamı",
            description=summary,
            workflow_name=f"rule_resume_{state.get('rule_id')}_{state.get('execution_id')}",
            payload={
                "execution_id": state.get("execution_id"),
                "thread_id": state.get("thread_id"),
                "resume_after_wait": True,
            },
        )
    except Exception as exc:
        print(f"[NODE wait] schedule create failed: {exc}")

    # State'i waiting_timer yap — runtime.start_execution bunu görüp
    # rule_executions tablosunda status='waiting_timer' set eder ve
    # graph'ı bu node'da bırakır.
    return {
        "current_node": "wait",
        "status": "waiting_timer",
        "metadata": {"resume_at": fire_at, "wait_delay_seconds": delay,
                     "wait_resolved": False},
        "trace_events": [make_trace(
            "wait", "interrupted", summary,
            details={"delay_seconds": delay, "resume_at": fire_at},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


def _humanize(s: int) -> str:
    if s < 60:
        return f"{s} saniye"
    if s < 3600:
        return f"{s//60} dakika"
    if s < 86400:
        return f"{s//3600} saat"
    return f"{s//86400} gün"


# ---------------------------------------------------------------------------
# Node: content_generator
# ---------------------------------------------------------------------------


_TEMPLATE_HEADLINES: dict[str, tuple[str, str]] = {
    "anneler_gunu":  ("Anneler Günü’ne özel", "Sevgisini hep yanında hisset"),
    "babalar_gunu":  ("Babalar Günü", "Hayatımızın kahramanlarına"),
    "yilbasi":       ("Yeni yıla özel", "Yeni başlangıçlar, yeni indirimler"),
    "ramazan":       ("Ramazan özel", "Bereketli günlere yakışır seçkiler"),
    "kurban_bayrami":("Kurban Bayramı’na özel", "Bayram coşkusu indirime dönüştü"),
    "yaz_indirim":   ("Yaz İndirimi", "Yaz sezonu fırsatları seni bekliyor"),
    "kis_indirim":   ("Kış İndirimi", "Soğuk havada sıcacık fırsatlar"),
    "kara_cuma":     ("Kara Cuma", "Yılın en büyük indirim günü"),
    "yeni_urun_lansman": ("Yeni Ürün", "Tanıtmaktan heyecan duyduğumuz yeniliğimiz"),
    "magaza_acilis":("Mağazamız Açıldı", "İlk müşterilerimize özel hoş geldin fırsatları"),
    "tesekkur":      ("Teşekkürler", "Sizinle olmak güzel"),
    "ozur":          ("Özür dileriz", "Yaşananları telafi etmek için buradayız"),
    "ozel_indirim":  ("Özel İndirim", "Sadece sana özel bir fırsat"),
    "generic":       ("Yeni paylaşım", "Sizler için hazırlandı"),
}


def _check_url_alive(url: str | None, timeout: int = 5) -> bool:
    """URL erişilebilir mi kontrol et.

    CDN URL'leri (cdn.dsmcdn.com, images.unsplash.com, vb.) için HEAD
    başarısız olursa GET ile retry yap. Lokal URL'ler (127.0.0.1, localhost)
    için her zaman True dön — alive check yapmaya gerek yok.
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        return False

    import urllib.parse
    parsed = urllib.parse.urlparse(u)
    if parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0"):
        return True

    import requests as _req
    ua = {"User-Agent": "Mozilla/5.0"}

    # 1) HEAD ile dene
    try:
        r = _req.head(u, timeout=timeout, allow_redirects=True, headers=ua)
        if r.status_code < 400:
            return True
        if r.status_code == 405:
            raise Exception("HEAD not allowed")
    except Exception:
        pass

    # 2) GET ile retry (stream=True — sadece header'ı al)
    try:
        r = _req.get(u, timeout=timeout, allow_redirects=True,
                     stream=True, headers=ua)
        try:
            r.close()
        except Exception:
            pass
        return r.status_code < 400
    except Exception:
        pass

    return False


def _fetch_template_from_mysql(
    template_name: str,
    channel: str | None = None,
    module: str = "social_media",
) -> dict | None:
    """social_documents'tan şablon getir (iki aşamalı arama).

    is_campaign (module='campaign' veya channel='banner') True ise
        Aşama 1: campaign_templates + campaign_templates_global
        Aşama 2: content_templates + content_templates_global (fallback)
    Aksi halde
        Aşama 1: content_templates + content_templates_global
        Aşama 2: campaign_templates + campaign_templates_global (fallback)

    Böylece operatör şablonu campaign mi content mi koleksiyonuna kaydetmiş
    fark etmez — biri boşsa diğerinde bulunur.

    channel='story' verilirse outputSize='story' olan şablonları önceliklendir;
    aşama başına önce sql_story sonra sql_any denenir, ilk eşleşme kazanır.

    DB bağlanamazsa veya kayıt yoksa graceful None döner.
    """
    if not template_name:
        return None
    pat = f"%{template_name.lower()}%"
    is_story = (channel or "").strip().lower() in ("story", "instagram_story")
    is_campaign = (
        (module or "").strip().lower() == "campaign"
        or (channel or "").strip().lower() == "banner"
    )
    campaign_set = "'campaign_templates','campaign_templates_global'"
    content_set = "'content_templates','content_templates_global'"
    search_sets = (
        [campaign_set, content_set] if is_campaign
        else [content_set, campaign_set]
    )
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            row = None
            for tpl_collections in search_sets:
                # Story için outputSize='story' filtresiyle önce dene
                sql_story = (
                    "SELECT payload FROM social_documents "
                    f"WHERE collection IN ({tpl_collections}) "
                    "AND ("
                    " LOWER(payload->>'title') LIKE :pat"
                    " OR LOWER(payload->>'name') LIKE :pat"
                    " OR LOWER(payload->>'templateName') LIKE :pat"
                    " OR LOWER(doc_id) LIKE :pat"
                    ") "
                    "AND LOWER(payload->>'outputSize') = 'story' "
                    "ORDER BY id DESC LIMIT 1"
                )
                # Genel arama (story filtresi yok)
                sql_any = (
                    "SELECT payload FROM social_documents "
                    f"WHERE collection IN ({tpl_collections}) "
                    "AND ("
                    " LOWER(payload->>'title') LIKE :pat"
                    " OR LOWER(payload->>'name') LIKE :pat"
                    " OR LOWER(payload->>'templateName') LIKE :pat"
                    " OR LOWER(doc_id) LIKE :pat"
                    ") "
                    "ORDER BY id DESC LIMIT 1"
                )
                stage_row = None
                if is_story:
                    stage_row = db.execute(text(sql_story), {"pat": pat}).fetchone()
                if not stage_row:
                    stage_row = db.execute(text(sql_any), {"pat": pat}).fetchone()
                if stage_row:
                    row = stage_row
                    break  # primary set'te bulundu, fallback'e gerek yok
            if row and row[0]:
                payload = row[0]
                if isinstance(payload, str):
                    import json as _json
                    try:
                        payload = _json.loads(payload)
                    except Exception:
                        return None
                if isinstance(payload, dict):
                    # Şablonun imageUrls'lerini canlılık kontrolüne tabi tut
                    # — ölü 404 URL'leri pipeline'a referans olarak verilmez.
                    raw_urls = payload.get("imageUrls") or payload.get("image_urls")
                    if isinstance(raw_urls, list) and raw_urls:
                        valid: list = []
                        for entry in raw_urls:
                            url_str = entry if isinstance(entry, str) else (entry or {}).get("url")
                            if _check_url_alive(url_str):
                                valid.append(entry)
                        payload = {**payload, "imageUrls": valid}
                    # Singular field için de check — ölüyse boşalt
                    for k in ("imageUrl", "image_url", "image", "thumbnail"):
                        v = payload.get(k)
                        if isinstance(v, str) and v and not _check_url_alive(v):
                            payload = {**payload, k: None}
                    return payload
    except Exception as exc:
        print(f"[_fetch_template_from_mysql] skip ({type(exc).__name__}): {exc}")
    return None


def _ai_generate_caption(
    event_payload: dict,
    rule_meta: dict,
    template_data: dict | None,
    channel: str,
) -> str | None:
    """OpenAI ile sosyal medya caption üret. Şablon prompt'unu + event'ten
    gelen ürün/mağaza bilgisini birlikte AI'a verir. Hata/timeout → None.

    event_payload flat veya nested (store/item/product) olabilir; iki yapıdan da
    bilgileri toplayıp tek bir bağlam bloğu oluşturur.
    """
    import os as _os
    api_key = (_os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            timeout=float(_os.environ.get("CONTENT_LLM_TIMEOUT", "20")),
        )

        # 1) Event bağlamı — flat alanlar (+ event.payload.X fallback)
        context: dict[str, Any] = {}
        flat_keys = (
            "name", "title", "category", "price", "store_name", "description",
            "discount_percent", "logo_url", "brand", "city", "tagline",
            "currency", "stock", "color", "size",
        )
        for key in flat_keys:
            val = event_payload.get(key)
            if val in (None, "", []):
                nested_payload = event_payload.get("payload")
                if isinstance(nested_payload, dict):
                    val = nested_payload.get(key)
            if val not in (None, "", []):
                context[key] = val

        # 2) Nested objeler — store / item / product / order
        for nested_key in ("store", "item", "product", "order"):
            nested = event_payload.get(nested_key)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    if v not in (None, "", []) and k not in context:
                        context[f"{nested_key}_{k}"] = v

        # 3) Bağlam metni — okunaklı liste formatı
        if context:
            ctx_str = "\n".join(f"- {k}: {v}" for k, v in context.items())
        else:
            ctx_str = "Yeni içerik"

        # 4) Şablon talimatı (prompt önceliği)
        template_instruction = ""
        if template_data:
            template_instruction = (
                template_data.get("prompt")
                or template_data.get("aiInstructions")
                or template_data.get("ai_instructions")
                or template_data.get("description")
                or template_data.get("caption")
                or ""
            )

        channel_name = (
            "Instagram Story" if channel in ("story", "instagram_story")
            else f"{(channel or 'instagram').title()} Post"
        )
        rule_name = (rule_meta or {}).get("name") or "kural"

        sys_msg = (
            "Sen bir sosyal medya içerik uzmanısın. SADECE Türkçe paylaşım "
            "metinleri yazarsın. Şablon talimatı veya ürün bilgisi İngilizce "
            "olsa bile çıktın TAMAMEN Türkçe olur — İngilizce kelime, marka "
            "sloganı veya hashtag KULLANMA. Marka/ürün adlarını koruyabilirsin "
            "ama açıklama, çağrı ve hashtag'ler Türkçe. Verilen ürün/mağaza "
            "bilgilerini ve şablon talimatlarını kullanarak platforma uygun, "
            "etkileyici, kısa ve öz içerik üretirsin."
        )
        user_msg = (
            f"{channel_name} için sosyal medya paylaşım metni yaz.\n"
            f"Kural: {rule_name}\n\n"
            f"ÜRÜN/MAĞAZA BİLGİLERİ:\n{ctx_str}\n"
            + (f"\nŞABLON TALİMATI:\n{template_instruction}\n" if template_instruction else "")
            + "\nKURALLAR:\n"
            "- ZORUNLU: Tüm metin Türkçe olmalı. İngilizce kelime kullanma.\n"
            "  (Marka/ürün adı haricinde; örn. 'Razer Cobra' kalabilir ama "
            "  cümle Türkçe.)\n"
            "- Hashtag'ler de Türkçe olsun (örn. #YeniÜrün, #İndirim, #Hediye).\n"
            "- Maksimum 3 cümle.\n"
            "- İlgili hashtag'leri ekle (5-7 adet, # işaretiyle).\n"
            "- Samimi ve etkileyici bir dil kullan.\n"
            "- Sadece paylaşım metnini yaz, başka açıklama ekleme.\n"
        )
        completion = client.chat.completions.create(
            model=_os.environ.get("CONTENT_LLM_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=400,
        )
        text_out = (completion.choices[0].message.content or "").strip()
        return text_out or None
    except Exception as exc:
        print(f"[_ai_generate_caption] fallback ({type(exc).__name__}): {exc}")
        return None


def _build_image_prompt(
    event_payload: dict, template_data: dict | None, channel: str, template_name: str
) -> str:
    """Görsel üretim prompt'u — şablonda varsa onu kullan, yoksa türet."""
    if template_data:
        for k in ("imagePrompt", "image_prompt", "imageDescription",
                  "visualPrompt", "prompt"):
            val = template_data.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    name = (
        event_payload.get("name")
        or event_payload.get("title")
        or (event_payload.get("store") or {}).get("name") if isinstance(event_payload.get("store"), dict) else None
        or (event_payload.get("item") or {}).get("name") if isinstance(event_payload.get("item"), dict) else None
        or template_name
        or "ürün"
    )
    return (
        f"Profesyonel sosyal medya görseli: {name}, tema {template_name}, "
        f"{channel} formatı, modern tasarım, canlı renkler, premium görünüm. "
        f"GÖRSELDEKİ TÜM YAZI VE METİNLER TÜRKÇE OLMALI. İngilizce metin kullanma."
    )


def _extract_hashtags(text_value: str) -> list[str]:
    """Caption metninden #etiketleri ayır (# işareti olmadan döner)."""
    if not text_value:
        return []
    import re as _re
    return [m.lstrip("#") for m in _re.findall(r"#\w+", text_value)]


def _resolve_openai_key_for_state(state: RuleExecutionState | dict | None) -> str | None:
    """OpenAI API key'i state → env → DB (workspace app_settings) sırasında ara.

    Celery worker'da env'de OPENAI_API_KEY olmayabilir; key UI'dan workspace'a
    yazılır (app/api/social_media._resolve_workspace_openai_key ile aynı kaynak).
    state.user_id → User.workspace_uid → SocialDocument(app_settings/api_keys).
    Bulamazsa None döner; çağıran taraf graceful fallback'e gider.
    """
    # 1) State içinde explicit key var mı (gelecekte enjekte edilebilir)
    if isinstance(state, dict):
        for path in (("openai_api_key",), ("rule", "openai_api_key"), ("event", "openai_api_key")):
            cur = state
            for p in path:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and cur.strip():
                return cur.strip()

    # 2) Env (lokal dev / docker)
    import os as _os
    env_key = (_os.environ.get("OPENAI_API_KEY") or "").strip()
    if env_key:
        return env_key

    # 3) DB lookup — user_id → workspace_uid → app_settings/api_keys.openaiApiKey
    user_id = state.get("user_id") if isinstance(state, dict) else None
    if not user_id:
        return None
    try:
        from sqlalchemy import desc, select
        from app.core.database import SessionLocal
        from app.models.social_document import SocialDocument
        from app.models.user import User
        # social_media.py'deki sabitlerle aynı (avoid circular import — inline)
        APP_SETTINGS_COLLECTION = "app_settings"
        APP_SETTINGS_DOC_ID = "api_keys"
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.id == int(user_id)))
            if user is None or not getattr(user, "workspace_uid", None):
                return None
            wsid = user.workspace_uid
            doc = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == wsid,
                    SocialDocument.collection == APP_SETTINGS_COLLECTION,
                    SocialDocument.doc_id == APP_SETTINGS_DOC_ID,
                )
            )
            if doc is not None:
                payload = dict(doc.payload or {})
                key = str(payload.get("openaiApiKey") or "").strip()
                if key:
                    return key
            # Fallback: app_settings koleksiyonunun en son güncellenen kaydı
            any_doc = db.scalar(
                select(SocialDocument)
                .where(
                    SocialDocument.workspace_uid == wsid,
                    SocialDocument.collection == APP_SETTINGS_COLLECTION,
                )
                .order_by(desc(SocialDocument.updated_at))
            )
            if any_doc is not None:
                payload = dict(any_doc.payload or {})
                key = str(payload.get("openaiApiKey") or "").strip()
                if key:
                    return key
    except Exception as exc:
        print(f"[_resolve_openai_key_for_state] DB lookup failed: {exc}")
    return None


def _generate_image_via_pipeline(
    *,
    prompt: str,
    reference_image_url: str | None,
    reference_image_urls: list[str] | None,
    product_image_url: str | None = None,
    product_image_urls: list[str] | None = None,
    store_logo_url: str | None = None,
    channel: str,
    output_size: str | None,
    skip_professionalization: bool = False,
    openai_api_key: str | None = None,
) -> str | None:
    import os as _os
    fal_key = (_os.environ.get("FAL_KEY") or "").strip() or None
    openai_key = (
        (openai_api_key or "").strip()
        or (_os.environ.get("OPENAI_API_KEY") or "").strip()
        or None
    )
    if not fal_key and not openai_key:
        return None

    all_refs: list[str] = []
    seen: set[str] = set()

    def _push(u):
        if not u:
            return
        s = u.strip() if isinstance(u, str) else ""
        if not s or s in seen:
            return
        import urllib.parse as _up
        parsed = _up.urlparse(s)
        is_local = parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0")
        if is_local:
            try:
                from app.services.local_media_storage import get_media_root
                import base64 as _b64
                marker = "/media/"
                if marker in s:
                    rel = s.split(marker, 1)[1].split("?", 1)[0]
                    file_path = _os.path.join(get_media_root(), rel)
                    if _os.path.isfile(file_path):
                        with open(file_path, "rb") as _f:
                            raw = _f.read()
                        ext = file_path.rsplit(".", 1)[-1].lower()
                        mime = (
                            "image/png" if ext == "png"
                            else "image/webp" if ext == "webp"
                            else "image/jpeg"
                        )
                        data_uri = f"data:{mime};base64,{_b64.b64encode(raw).decode()}"
                        if data_uri not in seen:
                            seen.add(data_uri)
                            all_refs.append(data_uri)
            except Exception as _exc:
                pass
        else:
            seen.add(s)
            all_refs.append(s)

    # 1) ŞABLON GÖRSELİ — BİRİNCİL (zemin/layout)
    _push(reference_image_url)
    if reference_image_urls:
        for entry in reference_image_urls:
            url = entry if isinstance(entry, str) else (entry or {}).get("url", "")
            _push(url)

    # 2) ÜRÜN FOTOĞRAFI — İKİNCİL (şablona yerleştirilir)
    if product_image_urls:
        for u in product_image_urls:
            _push(u)
    elif product_image_url:
        _push(product_image_url)

    # 3) MAĞAZA LOGOSU
    _push(store_logo_url)

    primary_ref = all_refs[0] if all_refs else None

    extras_text: list[str] = []
    if reference_image_url or reference_image_urls:
        extras_text.append(
            "Use the template image as the PRIMARY base layout — "
            "keep its design structure, colors and composition"
        )
    if product_image_url or product_image_urls:
        extras_text.append(
            "Replace the product in the template with this product image, "
            "keeping the template layout intact"
        )
    if store_logo_url:
        extras_text.append("Include the store logo subtly in the design")
    if extras_text:
        prompt = (prompt or "").strip() + ". " + ". ".join(extras_text) + "."

    plat = "story" if (channel or "").strip().lower() == "story" else "feed"
    try:
        from app.api.social_media import _sync_generate_images_task
        images = _sync_generate_images_task(
            prompt=prompt or "",
            count=1,
            platform=plat,
            reference_image_url=primary_ref,
            fal_api_key=fal_key,
            openai_api_key=openai_key,
            use_gpt=False,
            reference_image_urls=all_refs or None,
            output_size=output_size,
            skip_professionalization=skip_professionalization,
        )
        if isinstance(images, list) and images:
            first = images[0] if isinstance(images[0], dict) else {}
            url = (first.get("url") or "").strip()
            return url or None
    except Exception as exc:
        print(f"[_generate_image_via_pipeline] skip ({type(exc).__name__}): {exc}")
    return None


def _is_story_rule(rule_meta: dict | None) -> bool:
    """Rule'da story sinyali var mı? 3 yoldan tespit:
        1. rule.content.channel veya rule.target_channel "story" içeriyor mu
        2. natural_language metninde "story"/"hikaye" geçiyor mu
        3. graph_definition'da publish_story node var mı
    """
    if not isinstance(rule_meta, dict):
        return False
    # 1) content.channel veya content_type
    content = rule_meta.get("content") or {}
    if isinstance(content, dict):
        ch = (content.get("channel") or content.get("content_type") or "").lower()
        if "story" in ch or "hikaye" in ch:
            return True
    # 2) natural_language ipucu
    nl = (rule_meta.get("natural_language") or "").lower()
    if any(tok in nl for tok in ("hikaye", "story", "reel")):
        return True
    # 3) graph_definition'da publish_story
    gd = rule_meta.get("graph_definition") or {}
    for n in (gd.get("nodes") or []):
        if isinstance(n, dict) and n.get("node_type") == "publish_story":
            return True
    return False


def content_generator_node(state: RuleExecutionState) -> dict:
    """Şablon + event bağlamından gerçek içerik üret.

    Akış:
        1. Parametreleri topla (template, channel, accounts) — params veya rule'dan
        2. MySQL content_templates'tan template_data fetch et (varsa)
        3. AI ile caption üret (OPENAI_API_KEY varsa)
        4. AI başarısızsa statik _TEMPLATE_HEADLINES fallback
        5. Image prompt + hashtag'ler + (şablonda varsa) image_url

    AI yoksa veya MySQL yoksa graceful skip — eski statik davranışa düşer.
    """
    t0 = time.monotonic()
    params = _get_node_params(state)
    rule_meta = _rule_from_state(state)
    event = state.get("event") or {}
    event_payload = event.get("payload") or {}

    template = (
        params.get("template")
        or (rule_meta.get("target_template") if isinstance(rule_meta, dict) else None)
        or _content_template(state)
    )
    template = (template or "generic").strip().lower()
    channel = (params.get("channel") or _channel(state) or "instagram").strip().lower()
    accounts = (
        params.get("accounts")
        or (rule_meta.get("target_accounts") if isinstance(rule_meta, dict) else None)
        or []
    )

    is_story = (
        params.get("content_type") == "story"
        or channel == "story"
        or _is_story_rule(rule_meta)
    )
    if is_story:
        channel = "story"

    rule_module = (
        (rule_meta.get("module") if isinstance(rule_meta, dict) else None)
        or params.get("module")
        or "social_media"
    )
    template_data = (
        _fetch_template_from_mysql(template, channel=channel, module=rule_module)
        if template != "generic" else None
    )

    # 2) AI caption
    ai_caption = _ai_generate_caption(event_payload, rule_meta, template_data, channel)

    if ai_caption:
        caption = ai_caption
        first_sentence = caption.split(".")[0].strip() or caption[:80]
        headline = first_sentence[:80]
        body = caption[: 280]
        hashtags = _extract_hashtags(caption)
        source = "ai"
    else:
        h_static, b_static = _TEMPLATE_HEADLINES.get(template, _TEMPLATE_HEADLINES["generic"])
        entity_name = (
            (event.get("item") or {}).get("name")
            or (event.get("store") or {}).get("name")
            or event_payload.get("name")
            or ""
        )
        if entity_name:
            b_static = f"{b_static} — {entity_name}"
        hashtags = {
            "anneler_gunu":   ["AnnelerGünü", "AnneSevgisi"],
            "babalar_gunu":   ["BabalarGünü"],
            "yilbasi":        ["YeniYıl", "Yılbaşı"],
            "ramazan":        ["Ramazan"],
            "kara_cuma":      ["KaraCuma", "BlackFriday"],
            "yaz_indirim":    ["YazIndirimi", "Sezon"],
            "kis_indirim":    ["KısIndirimi"],
            "yeni_urun_lansman": ["YeniÜrün", "Lansman"],
            "magaza_acilis":  ["YeniMağaza", "HoşGeldiniz"],
        }.get(template, [])
        headline = h_static
        body = b_static
        caption = f"{h_static} — {b_static}"
        source = "static"

    # 3) Image prompt + image_url
    image_prompt = _build_image_prompt(event_payload, template_data, channel, template)

    if template_data and template_data.get("prompt"):
        _name = (
            event_payload.get("name")
            or event_payload.get("title")
            or ""
        )
        _price = float(event_payload.get("price") or 0)
        _discount = float(event_payload.get("discount_percent") or 0)
        _old_price = _price
        _new_price = round(_price * (1 - _discount / 100), 2) if _discount else _price
        _product_img = (
            event_payload.get("image_url")
            or event_payload.get("primary_image_url")
            or ""
        )
        _category = event_payload.get("category") or ""
        _description = f"{_category}, {_price} TL" if _category else f"{_price} TL"
        _today = datetime.now().strftime("%Y-%m-%d")
        _end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        _top_text = _name[:40] if _name else "Yeni Ürün"
        _main_title = _name[:60] if _name else "Yeni Ürün"
        _subtitle = f"%{int(_discount)} İndirim" if _discount else _description
        _button_text = "Satın Al"

        image_prompt = image_prompt.replace("{{PRODUCT_IMAGE}}", _product_img)
        image_prompt = image_prompt.replace("{{PRODUCT_NAME}}", _name)
        image_prompt = image_prompt.replace("{{PRODUCT_DESCRIPTION}}", _description)
        image_prompt = image_prompt.replace("{{TOP_TEXT}}", _top_text)
        image_prompt = image_prompt.replace("{{MAIN_TITLE}}", _main_title)
        image_prompt = image_prompt.replace("{{SUBTITLE}}", _subtitle)
        image_prompt = image_prompt.replace("{{BUTTON_TEXT}}", _button_text)

        _campaign_suffix = (
            f'\n\nCAMPAIGN_DATA:\n{{'
            f'\n  "pricing": {{'
            f'\n    "old_price": {_old_price},'
            f'\n    "new_price": {_new_price},'
            f'\n    "discount_percent": {_discount}'
            f'\n  }},'
            f'\n  "campaign_dates": {{'
            f'\n    "start_date": "{_today}",'
            f'\n    "end_date": "{_end}"'
            f'\n  }}'
            f'\n}}'
        )
        image_prompt = image_prompt + _campaign_suffix

    # Olumlu kullanıcı yorumlarını prompt'a ekle
    _reviews = event_payload.get("reviews_positive") or []
    if _reviews:
        _review_text = " | ".join(str(r)[:80] for r in _reviews[:3])
        image_prompt = image_prompt + f"\n\nMÜŞTERİ GÖRÜŞLERİ (referans için): {_review_text}"

    # Türkçe metin zorunluluğu
    image_prompt = image_prompt + "\n\nÖNEMLİ: Görseldeki tüm yazılar, başlıklar, butonlar ve metinler TÜRKÇE olmalı. İngilizce metin kesinlikle kullanma."

    # Şablonun referans görseli
    template_image_url: str | None = None
    template_image_urls: list[str] = []
    if template_data:
        for k in ("imageUrl", "image_url", "image", "thumbnail"):
            v = template_data.get(k)
            if isinstance(v, str) and v.strip():
                template_image_url = v.strip()
                break
        urls_field = template_data.get("imageUrls") or template_data.get("image_urls")
        if isinstance(urls_field, list):
            for entry in urls_field:
                if isinstance(entry, str) and entry.strip():
                    template_image_urls.append(entry.strip())
                elif isinstance(entry, dict):
                    u = (entry.get("url") or "").strip()
                    if u:
                        template_image_urls.append(u)
        if not template_image_url and template_image_urls:
            template_image_url = template_image_urls[0]

    # 4) Görsel üretimi
    output_size = None
    if template_data:
        output_size = (
            (template_data.get("outputSize") or template_data.get("output_size") or "")
            .strip()
            or None
        )
    params_output_size = (params.get("output_size") or "").strip()
    if params_output_size:
        output_size = params_output_size

    if not output_size:
        if channel in ("story", "instagram_story"):
            output_size = "story"
        elif channel in ("post", "instagram", "facebook"):
            output_size = "post"
        elif channel in ("banner", "campaign_banner"):
            output_size = "campaign_banner"

    if channel == "banner":
        output_size = "campaign_banner"

    product_image_url: str | None = (
        event_payload.get("primary_image_url")
        or event_payload.get("image_url")
    )
    product_image_urls_list: list[str] = []
    pi_pl = event_payload.get("image_urls")
    if isinstance(pi_pl, list):
        product_image_urls_list.extend([u for u in pi_pl if isinstance(u, str) and u.strip()])

    nested_item = event.get("item") or event_payload.get("item") or {}
    if isinstance(nested_item, dict):
        if not product_image_url:
            product_image_url = (
                nested_item.get("primary_image_url")
                or nested_item.get("image_url")
            )
        nested_urls = nested_item.get("image_urls")
        if isinstance(nested_urls, list):
            for u in nested_urls:
                if isinstance(u, str) and u.strip() and u not in product_image_urls_list:
                    product_image_urls_list.append(u)

    store_logo_url: str | None = (
        event_payload.get("store_logo_url")
        or event_payload.get("logo_url")
    )
    if not store_logo_url:
        nested_store = event.get("store") or event_payload.get("store") or {}
        if isinstance(nested_store, dict):
            store_logo_url = nested_store.get("logo_url")
        if not store_logo_url and isinstance(nested_item, dict):
            store_logo_url = nested_item.get("store_logo_url")

    store_banner_url: str | None = (
        event_payload.get("banner_url")
        or event_payload.get("store_banner_url")
    )
    if not store_banner_url:
        nested_store = event.get("store") or event_payload.get("store") or {}
        if isinstance(nested_store, dict):
            store_banner_url = nested_store.get("banner_url")

    if not product_image_url and not product_image_urls_list and store_banner_url:
        product_image_url = store_banner_url

    resolved_openai_key = _resolve_openai_key_for_state(state)

    generated_image_url = _generate_image_via_pipeline(
        prompt=image_prompt,
        reference_image_url=template_image_url,
        reference_image_urls=template_image_urls or None,
        product_image_url=product_image_url,
        product_image_urls=product_image_urls_list or None,
        store_logo_url=store_logo_url,
        channel=channel,
        output_size=output_size,
        skip_professionalization=True,
        openai_api_key=resolved_openai_key,
    )
    if generated_image_url:
        image_url = generated_image_url
        image_source = "pipeline_generated"
    else:
        image_url = template_image_url
        image_source = "template_reference" if template_image_url else "none"

    content = GeneratedContent(
        channel=channel,
        template=template,
        headline=headline,
        body=body,
        caption=caption,
        hashtags=hashtags,
        image_prompt=image_prompt,
        image_url=image_url,
        extras={
            "content_source": source,
            "template_data_used": bool(template_data),
            "accounts": list(accounts),
            "image_source": image_source,
            "template_image_url": template_image_url,
        },
    )

    summary = (
        f"İçerik üretildi ({source}): {headline[:60]} — "
        f"kanal {channel}, şablon {template}"
        + (" + template_data" if template_data else "")
        + f" | image={image_source}"
    )
    _emit("CONTENT_GENERATED", {
        "execution_id": state.get("execution_id"),
        "template": template, "channel": channel,
        "source": source,
        "template_data_used": bool(template_data),
        "image_source": image_source,
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "content_generator",
        "content": content.model_dump(),
        "trace_events": [make_trace(
            "content_generator", "ok", summary,
            details={"template": template, "channel": channel,
                     "source": source, "template_data_used": bool(template_data)},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: risk_analyzer
# ---------------------------------------------------------------------------


_RISKY_WORDS = (
    "tıbbi", "ilaç", "hasta", "tedavi", "garanti", "kesinlikle",
    "sınırsız", "para iadesi", "geri ödeme", "yasal",
    "free money", "guarantee", "cure",
)


def risk_analyzer_node(state: RuleExecutionState) -> dict:
    """Üretilen içeriği + eylem türünü risk açısından değerlendir.

    Heuristik: dış yayın + duyarlı keyword + olumsuz event geçmişi → risk
    skoru yüksek.
    """
    t0 = time.monotonic()
    content = state.get("content") or {}
    text = " ".join([
        content.get("headline", ""),
        content.get("body", ""),
        content.get("caption", ""),
    ]).lower()

    flags: list[str] = []
    score = 0.0

    for word in _RISKY_WORDS:
        if word in text:
            flags.append(f"risky_word:{word}")
            score += 0.15

    channel = content.get("channel") or _channel(state)
    if channel in ("instagram", "facebook"):
        score += 0.2          # dış yayın baseline
        flags.append("external_publish")

    # Olumsuz event tipiyle yayın yapmak da risk artırır
    event_type = (state.get("event") or {}).get("event_type", "")
    if event_type in ("review.negative", "shipping.delayed", "store.rejected"):
        score += 0.25
        flags.append(f"sensitive_event:{event_type}")

    score = min(1.0, score)
    level = "high" if score >= 0.55 else ("medium" if score >= 0.3 else "low")
    requires_human = level in ("medium", "high")

    explanation = "; ".join(flags) if flags else "Belirgin risk sinyali yok."

    risk = RiskAssessment(
        risk_level=level,
        risk_score=score,
        flags=flags,
        requires_human=requires_human,
        explanation=explanation,
    )

    summary = f"Risk seviyesi: {level} (skor {score:.2f}). {explanation[:120]}"
    _emit("RISK_ASSESSED", {
        "execution_id": state.get("execution_id"),
        "risk_level": level, "score": score, "flags": flags,
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "risk_analyzer",
        "risk": risk.model_dump(),
        "trace_events": [make_trace(
            "risk_analyzer",
            "ok" if level != "high" else "ok",
            summary,
            details={"flags": flags, "score": score},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: approval_gate
# ---------------------------------------------------------------------------


def approval_gate_node(state: RuleExecutionState) -> dict:
    """Onay node'u. interrupt_before ile compile edilir, dolayısıyla
    LangGraph bu node'u çalıştırmadan ÖNCE duraklar. Resume sırasında
    state.approval.decision güncel olur ve buraya tekrar girilir.

    Burada yaptığımız: approval row'unu oluştur (eğer yoksa) ve karar
    durumunu state'e yansıt. Karar yoksa interrupt zaten devreyi
    durdurur — bu node'un kodu çağrılmaz.
    """
    t0 = time.monotonic()

    existing = state.get("approval") or {}
    if existing.get("decision") in ("approved", "rejected", "edited"):
        summary = f"Onay kararı alındı: {existing.get('decision')}"
        _emit("APPROVAL_RESOLVED", {
            "execution_id": state.get("execution_id"),
            "decision": existing.get("decision"),
            "summary": summary,
        }, user_id=state.get("user_id"))
        return {
            "current_node": "approval_gate",
            "trace_events": [make_trace(
                "approval_gate", "ok", summary,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    # Karar yoksa: approval request oluştur ve "waiting_human" işaretle.
    # Bu code path normalde ulaşılmaz çünkü graph interrupt_before ile
    # duruyor, ama tetik anında approval row'u kurmak gerek.
    try:
        from approval_service import create_approval_request

        # approval_type'i dinamik node params'tan veya rule.graph_definition'dan al.
        # Eski yolda params boş döner → "generic_approval" default'una düşer.
        params = _get_node_params(state)
        approval_type = (params.get("approval_type") or "generic_approval").strip().lower()

        proposal = {
            "decision": "create_workflow",
            "workflow_name": f"rule_{state.get('rule_id')}_exec_{state.get('execution_id')}",
            "reason": (state.get("rule") or {}).get("name", "AI önerisi"),
            "tools": [],
            "priority": "high" if (state.get("risk") or {}).get("risk_level") == "high" else "medium",
            "confidence": 1.0 - float((state.get("risk") or {}).get("risk_score") or 0),
            "requires_approval": True,
            "business_intent": "structured_rule_execution",
            "approval_type": approval_type,
            "task_payload": {
                "execution_id": state.get("execution_id"),
                "thread_id": state.get("thread_id"),
                "content": state.get("content"),
                "risk": state.get("risk"),
            },
            "entity_type": "rule_execution",
            "entity_id": state.get("execution_id"),
        }
        approval_id = create_approval_request(
            user_id=state.get("user_id") or 1,
            proposal=proposal,
            event_id=(state.get("event") or {}).get("event_id"),
            approval_type=approval_type,
        )
    except Exception as exc:
        approval_id = None
        print(f"[NODE approval_gate] create_approval_request failed: {exc}")

    summary = "İnsan onayı bekleniyor."
    _emit("APPROVAL_REQUESTED", {
        "execution_id": state.get("execution_id"),
        "approval_id": approval_id,
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "approval_gate",
        "status": "waiting_human",
        "approval": ApprovalDecision(
            approval_id=approval_id, decision="pending"
        ).model_dump(),
        "trace_events": [make_trace(
            "approval_gate", "interrupted", summary,
            details={"approval_id": approval_id},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: publisher
# ---------------------------------------------------------------------------


def publisher_node(state: RuleExecutionState) -> dict:
    """İçeriği seçilen kanala "yayınla". Şu an gerçek API çağrısı yok —
    InstagramCampaignTool ile aynı mantık: credential varsa
    REAL_PUBLISH_WOULD_HAPPEN, yoksa draft.
    """
    t0 = time.monotonic()

    if state.get("status") == "waiting_human":
        # Sanırım approval interrupt sonrası status="running"a güncellenmedi.
        # Defensive: burada running'e geri al.
        pass

    approval = state.get("approval") or {}
    if approval.get("decision") == "rejected":
        msg = "Yayın reddedildi; akış iptal ediliyor."
        return {
            "current_node": "publisher",
            "status": "cancelled",
            "publish": PublishResult(
                success=False, message=msg,
                channel=_channel(state),
            ).model_dump(),
            "trace_events": [make_trace(
                "publisher", "ok", msg,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    content = state.get("content") or {}
    channel = content.get("channel") or _channel(state)

    # Credential layer'ı kontrol et + adapter (Tur 2)
    cred_id, handle, mode = None, None, "draft_only"
    adapter_attempt: dict | None = None
    user_id = state.get("user_id") or 1
    if channel in ("instagram", "facebook", "tiktok"):
        try:
            from social_credentials import try_get_credential
            cred = try_get_credential(user_id, channel)
            if cred is not None:
                cred_id = cred.id
                handle = cred.account_handle
                mode = "real_publish_would_happen"

                # Real adapter çağrısını DENE — SOCIAL_PUBLISH_LIVE
                # açıksa gerçek HTTP'ye yaklaşır; kapalıysa
                # FeatureDisabledError fırlatır ve fake'e geri döner.
                try:
                    from tool_adapters import (
                        AdapterCredentialError, FeatureDisabledError, get_adapter,
                    )
                    adapter = get_adapter(channel)
                    if adapter is not None:
                        try:
                            adapter_attempt = adapter.publish_post(
                                user_id=user_id,
                                account_handle=cred.account_handle,
                                caption=f"{content.get('headline','')} — {content.get('body','')}",
                                image_url=content.get("image_url"),
                                hashtags=content.get("hashtags", []),
                            )
                            mode = "real_published"
                        except FeatureDisabledError:
                            adapter_attempt = {"ok": False, "reason": "feature_disabled"}
                        except AdapterCredentialError as exc:
                            adapter_attempt = {"ok": False, "reason": "credential", "error": str(exc)}
                except Exception as exc:
                    print(f"[NODE publisher] adapter attempt failed: {exc}")
        except Exception as exc:
            print(f"[NODE publisher] credential lookup failed: {exc}")

    # Tool'a delege et — gerçek runtime'da fake tool zaten emit ediyor
    try:
        from tool_registry import resolve_tool_instances
        tool_name = {
            "instagram": "instagram_campaign_tool",
            "banner":    "banner_generator_tool",
            "coupon":    "coupon_generator_tool",
            "faq":       "faq_update_tool",
            "support":   "support_response_tool",
        }.get(channel, "instagram_campaign_tool")
        tools = resolve_tool_instances([tool_name])
        if tools:
            t = tools[0]
            # task_id ataması — execution_id'yi kullan
            t._task_id = state.get("execution_id")
            kwargs = {"headline": content.get("headline", "")}
            if tool_name == "instagram_campaign_tool":
                kwargs["hook"] = content.get("body")
                kwargs["hashtags"] = content.get("hashtags", [])
            elif tool_name == "banner_generator_tool":
                kwargs["subline"] = content.get("body")
                kwargs["cta"] = "Hemen incele"
            elif tool_name == "coupon_generator_tool":
                kwargs = {"label": content.get("headline", "İndirim")}
            elif tool_name == "faq_update_tool":
                kwargs = {
                    "topic": "genel",
                    "question": content.get("headline", "Soru"),
                    "answer": content.get("body", "Cevap"),
                }
            elif tool_name == "support_response_tool":
                kwargs = {"customer_question": content.get("body", "")}
            tool_out = t._run(**kwargs)
        else:
            tool_out = {"success": True, "message": "tool not found (no-op)"}
    except Exception as exc:
        tool_out = {"success": False, "error": str(exc)}

    success = bool(tool_out.get("success"))
    message = tool_out.get("message", "")[:240]
    timeline_id = None  # fake_tool_timeline emit ediyor; id'yi tracking yok

    result = PublishResult(
        channel=channel,
        mode=mode,
        account_handle=handle,
        credential_id=cred_id,
        timeline_event_id=timeline_id,
        success=success,
        message=message,
    )

    summary = (
        f"Yayın {'gerçekleştirildi (mock)' if mode == 'real_publish_would_happen' else 'taslak'}: "
        f"{channel} kanalı."
    )
    _emit("PUBLISH_DONE", {
        "execution_id": state.get("execution_id"),
        "channel": channel, "mode": mode, "success": success,
        "summary": summary,
    }, user_id=user_id)

    return {
        "current_node": "publisher",
        "publish": result.model_dump(),
        "trace_events": [make_trace(
            "publisher", "ok" if success else "failed", summary,
            details={"channel": channel, "mode": mode, "handle": handle},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: monitor
# ---------------------------------------------------------------------------


def monitor_node(state: RuleExecutionState) -> dict:
    """Yayın sonrası izleme kurulumunu yap (toy: 6 saat sonra check ekle)."""
    t0 = time.monotonic()
    publish = state.get("publish") or {}
    if not publish.get("success"):
        # Yayın olmamış, izleme anlamsız
        return {
            "current_node": "monitor",
            "monitor": MonitorResult(note="Yayın başarısız — izleme kurulmadı.").model_dump(),
            "trace_events": [make_trace(
                "monitor", "ok", "İzleme atlandı (yayın başarısız).",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    when = (datetime.utcnow() + timedelta(hours=6)).isoformat()
    try:
        from scheduling_service import create_schedule
        create_schedule(
            user_id=state.get("user_id") or 1,
            kind="workflow",
            scheduled_at=when,
            title=f"Yayın performans izleme #{state.get('execution_id')}",
            description="Yayının ilk 6 saatlik performansı kontrol edilecek.",
            workflow_name=f"monitor_rule_exec_{state.get('execution_id')}",
            payload={
                "execution_id": state.get("execution_id"),
                "kind": "monitor_check",
            },
        )
    except Exception as exc:
        print(f"[NODE monitor] schedule create failed: {exc}")

    monitor = MonitorResult(
        scheduled_check_at=when,
        initial_metrics={"impressions": 0, "clicks": 0},
        note=f"6 saat sonra ({when}) performans okuma planlandı.",
    )
    summary = monitor.note
    _emit("MONITOR_SCHEDULED", {
        "execution_id": state.get("execution_id"),
        "check_at": when,
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "monitor",
        "monitor": monitor.model_dump(),
        "trace_events": [make_trace(
            "monitor", "ok", summary,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: notify_customer  (opsiyonel — risky/shipping akışlarında kullanılır)
# ---------------------------------------------------------------------------


def notify_customer_node(state: RuleExecutionState) -> dict:
    t0 = time.monotonic()
    event = state.get("event") or {}
    content = state.get("content") or {}

    try:
        from tool_registry import resolve_tool_instances
        tools = resolve_tool_instances(["support_response_tool"])
        if tools:
            t = tools[0]
            t._task_id = state.get("execution_id")
            t._run(
                customer_question=event.get("payload", {}).get("question") or
                                  content.get("body", "Müşteri konusu"),
                tone="friendly",
            )
    except Exception as exc:
        print(f"[NODE notify_customer] tool failed: {exc}")

    return {
        "current_node": "notify_customer",
        "trace_events": [make_trace(
            "notify_customer", "ok", "Müşteriye bilgilendirme gönderildi (taslak).",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: create_coupon  (kupon-yan-eylem)
# ---------------------------------------------------------------------------


def create_coupon_node(state: RuleExecutionState) -> dict:
    t0 = time.monotonic()
    content = state.get("content") or {}
    label = content.get("headline") or "İndirim"
    percent = (_action_config(state, "create_coupon") or {}).get("percent", 10)

    try:
        from tool_registry import resolve_tool_instances
        tools = resolve_tool_instances(["coupon_generator_tool"])
        if tools:
            t = tools[0]
            t._task_id = state.get("execution_id")
            t._run(label=label, percent=int(percent))
    except Exception as exc:
        print(f"[NODE create_coupon] tool failed: {exc}")

    return {
        "current_node": "create_coupon",
        "trace_events": [make_trace(
            "create_coupon", "ok", f"Kupon oluşturuldu (%{percent}, {label}).",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: finalize — graph sonu
# ---------------------------------------------------------------------------


def finalize_node(state: RuleExecutionState) -> dict:
    t0 = time.monotonic()
    publish = state.get("publish") or {}
    approval = state.get("approval") or {}

    if approval.get("decision") == "rejected":
        status = "cancelled"
        summary = "Kural insan tarafından reddedildi."
    elif publish and not publish.get("success", True):
        status = "failed"
        summary = "Yayın başarısız oldu — akış kapatıldı."
    else:
        status = "completed"
        summary = "Kural başarıyla tamamlandı."

    _emit("RULE_EXECUTION_END", {
        "execution_id": state.get("execution_id"),
        "rule_id": state.get("rule_id"),
        "final_status": status,
        "summary": summary,
    }, user_id=state.get("user_id"))

    return {
        "current_node": "finalize",
        "status": status,
        "trace_events": [make_trace(
            "finalize", "ok", summary,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ===========================================================================
# YENİ NODE FONKSİYONLARI (Bölüm 4.1 — dinamik graph desteği)
#
# Mevcut node'lar yukarıda — bunlara dokunulmadı. Aşağıdaki node'lar yeni
# graph_definition modelinden çağrılır. Her birinin imzası standart:
#   def X_node(state: RuleExecutionState) -> dict
# Dinamik runtime, NodeDefinition.params'ı state["metadata"]["__node_params"]
# içine yerleştirir (build_dynamic_graph factory'sinde). Node bu params'ı
# kendi okur.
# ===========================================================================


def _get_node_params(state: RuleExecutionState) -> dict:
    """Dinamik runtime'ın geçirdiği per-node parametreleri al.

    runtime._build_dynamic_graph her node fonksiyonunu wrapper ile sarar;
    wrapper o node'un NodeDefinition.params'ını state.metadata['__node_params']
    içine yerleştirir. Bu helper o anki node'un params'ını döndürür.

    Fallback: metadata.__node_params yoksa state.current_node'dan
    rule.graph_definition.nodes içinden eşleşeni arar (eski davranış).
    """
    md = state.get("metadata") or {}
    pinj = md.get("__node_params")
    if isinstance(pinj, dict):
        return pinj
    # Fallback (canonical yol veya wrapper'sız invoke):
    current = state.get("current_node") or ""
    rule = state.get("rule") or {}
    gd = (rule.get("graph_definition") or {}) if isinstance(rule, dict) else {}
    for n in (gd.get("nodes") or []):
        if n.get("node_id") == current:
            return n.get("params") or {}
    return {}


# ---------------------------------------------------------------------------
# Node: condition_check — koşul değerlendir + dallan
# ---------------------------------------------------------------------------


def _eval_condition(field: str, op: str, expected: Any, payload: dict) -> bool:
    """Tek bir Condition'ı event payload'una karşı değerlendir."""
    if not field:
        return True
    actual = payload.get(field) if isinstance(payload, dict) else None
    # Nested store/item gibi yapıları da dene
    if actual is None and isinstance(payload, dict):
        for nested_key in ("store", "item", "order"):
            nested = payload.get(nested_key) or {}
            if isinstance(nested, dict) and field in nested:
                actual = nested[field]
                break

    try:
        if op == ">=":   return float(actual) >= float(expected)
        if op == ">":    return float(actual) >  float(expected)
        if op == "<=":   return float(actual) <= float(expected)
        if op == "<":    return float(actual) <  float(expected)
        if op == "==":   return str(actual).lower() == str(expected).lower()
        if op == "!=":   return str(actual).lower() != str(expected).lower()
        if op == "in":
            if isinstance(expected, (list, tuple, set)):
                return actual in expected
            return str(actual) in str(expected)
        if op == "not_in":
            if isinstance(expected, (list, tuple, set)):
                return actual not in expected
            return str(actual) not in str(expected)
        if op == "contains":
            return str(expected).lower() in str(actual or "").lower()
    except (ValueError, TypeError):
        return False
    return False


def condition_check_node(state: RuleExecutionState) -> dict:
    """Kuralın conditions listesini event payload'una karşı doğrula.

    params (NodeDefinition'dan):
        - conditions: list[dict] — her biri {field, operator, value}
        - match_mode: "all" (AND, default) | "any" (OR)

    Koşul sağlanmazsa state.status='cancelled' ve metadata.conditions_failed=True
    set edilir; sonraki publish node'ları bu bayrağı görüp atlayacak şekilde
    tasarlandı. Graph'ı kırmak yerine sinyal veriyoruz — finalize node'u
    'cancelled' status'unu raporlar.
    """
    t0 = time.monotonic()
    params = _get_node_params(state)
    conditions = params.get("conditions") or []
    match_mode = (params.get("match_mode") or "all").lower()
    payload = (state.get("event") or {}).get("payload") or {}

    if not conditions:
        return {
            "current_node": "condition_check",
            "trace_events": [make_trace(
                "condition_check", "ok",
                "Koşul yok — devam ediliyor.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    results: list[bool] = []
    details: list[dict] = []
    for c in conditions:
        f = c.get("field") if isinstance(c, dict) else None
        op = c.get("operator") if isinstance(c, dict) else None
        v = c.get("value") if isinstance(c, dict) else None
        ok = _eval_condition(str(f or ""), str(op or "=="), v, payload)
        results.append(ok)
        details.append({"field": f, "op": op, "value": v, "passed": ok})

    if match_mode == "any":
        passed = any(results)
    else:
        passed = all(results)

    summary = (
        f"Koşullar sağlandı ({sum(results)}/{len(results)}) — devam."
        if passed else
        f"Koşullar sağlanmadı ({sum(results)}/{len(results)}) — akış iptal."
    )

    out: dict[str, Any] = {
        "current_node": "condition_check",
        "trace_events": [make_trace(
            "condition_check",
            "ok" if passed else "interrupted",
            summary,
            details={"results": details, "match_mode": match_mode, "passed": passed},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
        "metadata": {"conditions_passed": passed, "condition_results": details},
    }
    if not passed:
        out["status"] = "cancelled"
    return out


# ---------------------------------------------------------------------------
# Yardımcı: account_handle → campaign_accounts.doc_id resolve
# ---------------------------------------------------------------------------


def _resolve_account_id(account_handle: str | None) -> str | None:
    """account_handle ile MySQL'den `doc_id` (UUID) bul. UI'nin Yayınla
    sekmesi `accountId` olarak bu UUID'i kullanır.

    İki koleksiyonu sırayla tarar:
        1. `campaign_accounts` (kampanya hesapları — name/handle/username)
        2. `accounts` (Instagram hesap profilleri — username/handle/name)
    Eşleşme yoksa None.
    """
    if not account_handle:
        return None
    handle = account_handle.lstrip("@").strip().lower()
    if not handle:
        return None
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text

        sql_campaign = (
            "SELECT doc_id FROM social_documents "
            "WHERE collection='campaign_accounts' "
            "AND ("
            " LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.name'))) LIKE :h"
            " OR LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.handle'))) LIKE :h"
            " OR LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.username'))) LIKE :h"
            " OR LOWER(doc_id) LIKE :h"
            ") "
            "ORDER BY id DESC LIMIT 1"
        )
        sql_accounts = (
            "SELECT doc_id FROM social_documents "
            "WHERE collection='accounts' "
            "AND ("
            " LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.username'))) LIKE :h"
            " OR LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.handle'))) LIKE :h"
            " OR LOWER(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.name'))) LIKE :h"
            " OR LOWER(doc_id) LIKE :h"
            ") "
            "ORDER BY id DESC LIMIT 1"
        )
        with SessionLocal() as db:
            row = db.execute(text(sql_campaign), {"h": f"%{handle}%"}).fetchone()
            if row and row[0]:
                return str(row[0])
            row2 = db.execute(text(sql_accounts), {"h": f"%{handle}%"}).fetchone()
            if row2 and row2[0]:
                return str(row2[0])
    except Exception as exc:
        print(f"[_resolve_account_id] skip ({type(exc).__name__}): {exc}")
    return None


# ---------------------------------------------------------------------------
# Yardımcı: MySQL social_documents'a draft kart yaz (UI takvimi için)
# ---------------------------------------------------------------------------


def _save_to_social_documents(
    state: RuleExecutionState,
    *,
    collection: str,
    channel: str,
    content: dict,
    params: dict,
    publish_result: dict,
) -> int | None:
    """Onaylanan publish için MySQL social_documents tablosuna draft kart yaz.

    SocialDocument modeli generic: workspace_uid + collection + doc_id + payload(JSON).
    Yayın kartı payload'ın içine konur. UI takvimi /social-data/collections/X
    endpoint'inden çekip görüntüler.

    MySQL bağlantısı yoksa (lokal geliştirme — DATABASE_URL boş veya host_unreachable)
    sessizce geç. scheduled_entries zaten yazılmış oluyor.

    Returns:
        Yeni kayıt id (>0) veya hata/skip durumunda None.
    """
    import os as _os
    import uuid as _uuid
    from datetime import datetime as _dt

    try:
        from app.core.database import SessionLocal
        from app.models.social_document import SocialDocument

        # workspace_uid resolve — user'dan veya default
        try:
            from app.models.user import User
            with SessionLocal() as db:
                user = db.get(User, int(state.get("user_id") or 1))
                if user and getattr(user, "workspace_uid", None):
                    ws_uid = user.workspace_uid
                else:
                    ws_uid = _os.environ.get("DEFAULT_WORKSPACE_UID", "default_ws")
        except Exception:
            ws_uid = _os.environ.get("DEFAULT_WORKSPACE_UID", "default_ws")

        accounts = params.get("accounts") or []
        primary_handle = accounts[0] if accounts else None
        account_id = _resolve_account_id(primary_handle)
        # publishTargets — UI Yayınla sekmesi bu bayrakları okur.
        # story durumunda instagramStory=true, diğer durumlarda instagramPost=true.
        is_story_card = (channel or "").strip().lower() == "story"
        publish_targets = {
            "instagramPost":  not is_story_card,
            "instagramStory": is_story_card,
            "facebookPost":   False,
        }
        doc_id = f"rule-{state.get('rule_id')}-exec-{state.get('execution_id')}-{_uuid.uuid4().hex[:8]}"
        payload = {
            "source":          "rule_engine",
            "rule_id":         state.get("rule_id"),
            "execution_id":    state.get("execution_id"),
            "channel":         channel,
            "content_type":    publish_result.get("mode") or "post",
            "account_handle":  primary_handle,
            "accounts":        accounts,
            "accountId":       account_id,
            "accountName":     f"@{primary_handle}" if primary_handle else None,
            "publishTargets":  publish_targets,
            "template_name":   params.get("template"),
            "headline":        content.get("headline") or "",
            "caption":         content.get("caption") or "",
            "body":            content.get("body") or "",
            "image_prompt":    content.get("image_prompt") or "",
            "image_url":       content.get("image_url"),
            "imageUrl":        content.get("image_url"),
            "hashtags":        content.get("hashtags") or [],
            "category":        params.get("category"),
            "store":           params.get("store"),
            "status":          "draft",
            "publish_success": publish_result.get("success", False),
            "publish_message": publish_result.get("message", ""),
            "scheduled_at":    _dt.utcnow().isoformat(),
        }

        with SessionLocal() as db:
            row = SocialDocument(
                workspace_uid=ws_uid,
                collection=collection,
                doc_id=doc_id,
                payload=payload,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)
    except Exception as exc:
        # MySQL yoksa, host unreachable veya başka hata — graceful skip
        print(f"[NODE _save_to_social_documents] skip ({type(exc).__name__}): {exc}")
        return None


# ---------------------------------------------------------------------------
# Yardımcı: takvim entry'si oluştur (publish_post/story/banner sonrası)
# ---------------------------------------------------------------------------


def _create_calendar_entry(
    state: RuleExecutionState,
    *,
    kind: str,
    channel: str,
    title: str,
    description: str,
    params: dict,
    publish_result: dict,
) -> dict:
    """Publish sonrası scheduled_entries tablosuna kayıt at.

    Operatör takviminde (calendar / schedules endpoint'i) bu kural-tabanlı
    yayınların görünmesi için. publish başarılıysa status='fired' (tamamlandı),
    aksi halde 'failed'.

    Hata olursa publish akışını bozmaz — sadece log atar ve {} döner.
    """
    import os as _os
    try:
        from scheduling_service import create_schedule, SCHEDULE_FIRED, SCHEDULE_FAILED
        from db import execute_write, now_iso

        # create_schedule default'ta status=pending koyar. Publish çoktan
        # yapıldığı için entry'yi yarat ve sonra status'u güncelle.
        entry = create_schedule(
            user_id=int(state.get("user_id") or 1),
            kind=kind,                       # "content_post" veya "campaign"
            channel=channel,
            title=title or "Kural paylaşımı",
            description=description or "",
            scheduled_at=now_iso(),
            workflow_name=state.get("thread_id") or
                f"rule_{state.get('rule_id')}_exec_{state.get('execution_id')}",
            payload={
                "rule_id": state.get("rule_id"),
                "execution_id": state.get("execution_id"),
                "content": state.get("content"),
                "accounts": params.get("accounts") or [],
                "template": params.get("template"),
                "channel": channel,
                "category": params.get("category"),
                "publish_mode": publish_result.get("mode"),
                "publish_success": publish_result.get("success"),
                "publish_message": publish_result.get("message"),
            },
            recurrence="once",
            requires_approval=False,
            created_by="rule_engine",
        )
        # Tamamlanmış statüsünü işaretle
        final_status = SCHEDULE_FIRED if publish_result.get("success", True) else SCHEDULE_FAILED
        execute_write(
            "UPDATE scheduled_entries SET status=?, fired_at=?, updated_at=? WHERE id=?",
            (final_status, now_iso(), now_iso(), int(entry["id"])),
        )
        return entry
    except Exception as exc:
        print(f"[NODE _create_calendar_entry] scheduled_entries write failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Yardımcı: publish için ortak adapter çağrısı
# ---------------------------------------------------------------------------


def _resolve_accounts(state: RuleExecutionState, params: dict) -> list[str]:
    """params.accounts → liste; yoksa rule'dan target_accounts + account_handle."""
    accs = params.get("accounts")
    if isinstance(accs, (list, tuple)) and accs:
        return [str(a).strip().lower() for a in accs if a]
    rule = state.get("rule") or {}
    if isinstance(rule, dict):
        ta = rule.get("target_accounts") or []
        if ta:
            return [str(a).strip().lower() for a in ta if a]
        target = rule.get("target") or {}
        h = target.get("account_handle")
        if h:
            return [str(h).strip().lower()]
    return []


def _publish_via_adapter(
    state: RuleExecutionState,
    *,
    channel: str,
    content_type: str,
    accounts: list[str],
    extra_payload: dict | None = None,
) -> tuple[bool, str, list[dict]]:
    """Tek bir kanal/içerik-türü kombinasyonu için tüm hesaplara yayın.

    Returns:
        (overall_success, summary_message, per_account_results)
    """
    content = state.get("content") or {}
    caption_base = f"{content.get('headline','')} — {content.get('body','')}"
    extras = dict(content.get("extras") or {})
    if extra_payload:
        extras.update(extra_payload)

    # Hiç hesap yoksa fake/draft publisher_node'un yaptığı gibi geç
    if not accounts:
        return True, f"{channel} {content_type}: hesap yok, taslak kabul edildi.", []

    user_id = state.get("user_id") or 1
    results: list[dict] = []
    any_failure = False

    try:
        from tool_adapters import (
            AdapterCredentialError, FeatureDisabledError, get_adapter,
        )
    except Exception as exc:
        return False, f"adapter import failed: {exc}", []

    adapter = get_adapter(channel)
    if adapter is None:
        return False, f"{channel} için adapter bulunamadı.", []

    for handle in accounts:
        attempt: dict[str, Any] = {"account": handle, "content_type": content_type}
        try:
            res = adapter.publish_post(
                user_id=user_id,
                account_handle=handle,
                caption=caption_base,
                image_url=content.get("image_url"),
                hashtags=content.get("hashtags", []),
            )
            attempt.update({"ok": bool(res.get("ok")), "raw": res})
            if not res.get("ok"):
                any_failure = True
        except FeatureDisabledError as exc:
            attempt.update({"ok": False, "reason": "feature_disabled", "error": str(exc)})
        except AdapterCredentialError as exc:
            attempt.update({"ok": False, "reason": "credential", "error": str(exc)})
            any_failure = True
        except Exception as exc:
            attempt.update({"ok": False, "reason": "exception", "error": str(exc)})
            any_failure = True
        results.append(attempt)

    success = not any_failure
    summary = (
        f"{channel} {content_type}: {len(accounts)} hesap"
        f" — {'OK' if success else 'kısmi/başarısız'}"
    )
    return success, summary, results


# ---------------------------------------------------------------------------
# Node: publish_post — Instagram/Facebook post (birden fazla hesap)
# ---------------------------------------------------------------------------


def publish_post_node(state: RuleExecutionState) -> dict:
    """post tipi yayın. params.accounts veya rule.target_accounts kullanılır.

    condition_check_node failed bayrağını set ettiyse atlanır.
    """
    t0 = time.monotonic()
    if (state.get("metadata") or {}).get("conditions_passed") is False:
        return {
            "current_node": "publish_post",
            "trace_events": [make_trace(
                "publish_post", "ok",
                "Koşul başarısız — publish atlandı.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    params = _get_node_params(state)
    channel = params.get("channel") or "instagram"
    accounts = _resolve_accounts(state, params)

    ok, summary, results = _publish_via_adapter(
        state, channel=channel, content_type="post", accounts=accounts,
    )

    publish_payload = {
        "channel": channel,
        "mode": "post",
        "success": ok,
        "message": summary,
        "per_account": results,
    }

    # Takvim entry'si oluştur — operatör takviminde bu post görünsün
    content = state.get("content") or {}
    calendar_entry = _create_calendar_entry(
        state,
        kind="content_post",
        channel=channel,
        title=content.get("headline") or "Kural — post",
        description=content.get("caption") or "",
        params=params,
        publish_result=publish_payload,
    )
    if calendar_entry.get("id"):
        publish_payload["calendar_entry_id"] = calendar_entry["id"]

    # MySQL social_documents'a draft kart (UI takvimi için)
    social_doc_id = _save_to_social_documents(
        state,
        collection="scheduled_posts",
        channel=channel,
        content=content,
        params=params,
        publish_result=publish_payload,
    )
    if social_doc_id:
        publish_payload["social_doc_id"] = social_doc_id

    _emit("PUBLISH_POST_DONE", {
        "execution_id": state.get("execution_id"),
        "channel": channel, "accounts": accounts,
        "success": ok, "summary": summary,
        "calendar_entry_id": calendar_entry.get("id"),
    }, user_id=state.get("user_id"))

    return {
        "current_node": "publish_post",
        "publish": publish_payload,
        "trace_events": [make_trace(
            "publish_post", "ok" if ok else "failed", summary,
            details={
                "accounts": accounts,
                "results_count": len(results),
                "calendar_entry_id": calendar_entry.get("id"),
            },
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: publish_story — Instagram/Facebook story (birden fazla hesap)
# ---------------------------------------------------------------------------


def publish_story_node(state: RuleExecutionState) -> dict:
    """story tipi yayın. adapter'a content_type='story' parametresi yollanır
    (adapter destekliyorsa); aksi halde post API'sine düşülür ve sadece
    content_type log'lanır.
    """
    t0 = time.monotonic()
    if (state.get("metadata") or {}).get("conditions_passed") is False:
        return {
            "current_node": "publish_story",
            "trace_events": [make_trace(
                "publish_story", "ok",
                "Koşul başarısız — publish atlandı.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    params = _get_node_params(state)
    channel = params.get("channel") or "instagram"
    accounts = _resolve_accounts(state, params)

    ok, summary, results = _publish_via_adapter(
        state, channel=channel, content_type="story", accounts=accounts,
        extra_payload={"content_type": "story"},
    )

    publish_payload = {
        "channel": channel,
        "mode": "story",
        "success": ok,
        "message": summary,
        "per_account": results,
    }

    content = state.get("content") or {}
    calendar_entry = _create_calendar_entry(
        state,
        kind="content_post",
        channel=channel,
        title=content.get("headline") or "Kural — story",
        description=content.get("caption") or "",
        params=params,
        publish_result=publish_payload,
    )
    if calendar_entry.get("id"):
        publish_payload["calendar_entry_id"] = calendar_entry["id"]

    # MySQL social_documents'a draft story kart — story için ayrı collection
    social_doc_id = _save_to_social_documents(
        state,
        collection="story_scheduled_posts",
        channel="story",
        content=content,
        params=params,
        publish_result=publish_payload,
    )
    if social_doc_id:
        publish_payload["social_doc_id"] = social_doc_id

    _emit("PUBLISH_STORY_DONE", {
        "execution_id": state.get("execution_id"),
        "channel": channel, "accounts": accounts,
        "success": ok, "summary": summary,
        "calendar_entry_id": calendar_entry.get("id"),
    }, user_id=state.get("user_id"))

    return {
        "current_node": "publish_story",
        "publish": publish_payload,
        "trace_events": [make_trace(
            "publish_story", "ok" if ok else "failed", summary,
            details={
                "accounts": accounts,
                "results_count": len(results),
                "calendar_entry_id": calendar_entry.get("id"),
            },
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: publish_banner — kampanya banner'ı (harici web sitesine POST)
# ---------------------------------------------------------------------------


def publish_banner_node(state: RuleExecutionState) -> dict:
    """Banner publish. EXTERNAL_WEBHOOK_URL env varsa POST atılır.
    Yoksa automation_logs'a yazılır ve OK döner.
    """
    import os as _os
    t0 = time.monotonic()
    if (state.get("metadata") or {}).get("conditions_passed") is False:
        return {
            "current_node": "publish_banner",
            "trace_events": [make_trace(
                "publish_banner", "ok",
                "Koşul başarısız — banner atlandı.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    params = _get_node_params(state)
    content = state.get("content") or {}
    webhook = (_os.environ.get("EXTERNAL_WEBHOOK_URL") or "").strip()

    payload = {
        "kind": "banner",
        "execution_id": state.get("execution_id"),
        "headline": content.get("headline"),
        "body": content.get("body"),
        "image_url": content.get("image_url"),
        "category": params.get("category"),
        "store": params.get("store"),
        "template": params.get("template"),
    }

    if not webhook:
        # Webhook yoksa automation_logs'a yaz
        try:
            from automation_log_service import log_action
            log_action(
                user_id=state.get("user_id") or 1,
                rule_id=state.get("rule_id"),
                action_kind="banner_publish_skipped",
                detail={"reason": "no_webhook", "payload": payload},
            )
        except Exception:
            pass
        summary = "EXTERNAL_WEBHOOK_URL yok — banner taslak kabul edildi."
        publish_payload = {"channel": "banner", "mode": "draft", "success": True,
                           "message": summary}
        # Takvime banner entry (kind=campaign)
        content = state.get("content") or {}
        calendar_entry = _create_calendar_entry(
            state,
            kind="campaign",
            channel="banner",
            title=content.get("headline") or "Kural — banner",
            description=content.get("caption") or "",
            params=params,
            publish_result=publish_payload,
        )
        if calendar_entry.get("id"):
            publish_payload["calendar_entry_id"] = calendar_entry["id"]
        social_doc_id = _save_to_social_documents(
            state, collection="campaign_scheduled_posts", channel="banner",
            content=content, params=params, publish_result=publish_payload,
        )
        if social_doc_id:
            publish_payload["social_doc_id"] = social_doc_id
        return {
            "current_node": "publish_banner",
            "publish": publish_payload,
            "trace_events": [make_trace(
                "publish_banner", "ok", summary,
                details={"calendar_entry_id": calendar_entry.get("id"),
                         "social_doc_id": social_doc_id},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    # Webhook varsa POST
    try:
        import requests
        r = requests.post(webhook, json=payload, timeout=15)
        success = 200 <= r.status_code < 300
        summary = f"Banner webhook → {r.status_code}"
    except Exception as exc:
        success = False
        summary = f"Banner webhook hata: {exc}"

    publish_payload = {"channel": "banner", "mode": "real", "success": success,
                       "message": summary}
    content = state.get("content") or {}
    calendar_entry = _create_calendar_entry(
        state,
        kind="campaign",
        channel="banner",
        title=content.get("headline") or "Kural — banner",
        description=content.get("caption") or "",
        params=params,
        publish_result=publish_payload,
    )
    if calendar_entry.get("id"):
        publish_payload["calendar_entry_id"] = calendar_entry["id"]
    social_doc_id = _save_to_social_documents(
        state, collection="campaign_scheduled_posts", channel="banner",
        content=content, params=params, publish_result=publish_payload,
    )
    if social_doc_id:
        publish_payload["social_doc_id"] = social_doc_id

    return {
        "current_node": "publish_banner",
        "publish": publish_payload,
        "trace_events": [make_trace(
            "publish_banner", "ok" if success else "failed", summary,
            details={"webhook": webhook[:80],
                     "calendar_entry_id": calendar_entry.get("id"),
                     "social_doc_id": social_doc_id},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: web_publish — genel harici sistem yayını + retry
# ---------------------------------------------------------------------------


def web_publish_node(state: RuleExecutionState) -> dict:
    """Genel harici sistem yayını. EXTERNAL_WEBHOOK_URL env'i kullanılır.

    2 retry, exponential backoff (1s, 2s). Hâlâ başarısızsa publish.success=False.
    """
    import os as _os
    import time as _time
    t0 = time.monotonic()
    if (state.get("metadata") or {}).get("conditions_passed") is False:
        return {
            "current_node": "web_publish",
            "trace_events": [make_trace(
                "web_publish", "ok",
                "Koşul başarısız — web_publish atlandı.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    params = _get_node_params(state)
    webhook = (_os.environ.get("EXTERNAL_WEBHOOK_URL") or "").strip()

    payload = {
        "execution_id": state.get("execution_id"),
        "event_type": (state.get("event") or {}).get("event_type"),
        "event_payload": (state.get("event") or {}).get("payload"),
        "content": state.get("content"),
        "params": params,
    }

    if not webhook:
        summary = "EXTERNAL_WEBHOOK_URL yok — web_publish atlandı."
        return {
            "current_node": "web_publish",
            "trace_events": [make_trace(
                "web_publish", "ok", summary,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    last_error = ""
    attempts = 0
    success = False
    try:
        import requests
        for attempt in range(3):  # 1 initial + 2 retry
            attempts = attempt + 1
            try:
                r = requests.post(webhook, json=payload, timeout=15)
                if 200 <= r.status_code < 300:
                    success = True
                    last_error = f"OK {r.status_code}"
                    break
                last_error = f"HTTP {r.status_code}"
            except Exception as exc:
                last_error = str(exc)
            if attempt < 2:
                _time.sleep(1.0 * (2 ** attempt))
    except Exception as exc:
        last_error = f"requests import failed: {exc}"

    summary = f"web_publish: {attempts} deneme, {'OK' if success else 'BAŞARISIZ'} — {last_error}"
    return {
        "current_node": "web_publish",
        "publish": {"channel": "web", "mode": "real", "success": success,
                    "message": summary},
        "trace_events": [make_trace(
            "web_publish", "ok" if success else "failed", summary,
            details={"attempts": attempts, "last_error": last_error[:200]},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# Node: kampanya_sync — banner + post'u senkron tetikle
# ---------------------------------------------------------------------------


def kampanya_sync_node(state: RuleExecutionState) -> dict:
    """Banner + post'u tek node içinden senkron tetikle.

    Bu node, parallel_with kullanmadan tek bir sequential noktada her ikisini
    çalıştırmak isteyen operatörler için kısayoldur. Her iki publish çağrısı
    sırayla yapılır; state.publish hem post hem banner sonuçlarını içerir.
    """
    t0 = time.monotonic()
    if (state.get("metadata") or {}).get("conditions_passed") is False:
        return {
            "current_node": "kampanya_sync",
            "trace_events": [make_trace(
                "kampanya_sync", "ok",
                "Koşul başarısız — kampanya sync atlandı.",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )],
        }

    # Sıra: önce banner sonra post
    banner_state_delta = publish_banner_node(state)
    # banner_state_delta state'i mutate etmiyor, sadece partial return — biz
    # de mantıksal olarak iki ayrı publish çağrısı yapıp birleştiriyoruz.
    post_state_delta = publish_post_node(state)

    banner_res = (banner_state_delta or {}).get("publish") or {}
    post_res = (post_state_delta or {}).get("publish") or {}
    combined_success = bool(banner_res.get("success", True)) and bool(post_res.get("success", True))

    summary = (
        f"Kampanya sync — banner={'OK' if banner_res.get('success') else 'FAIL'}, "
        f"post={'OK' if post_res.get('success') else 'FAIL'}"
    )

    return {
        "current_node": "kampanya_sync",
        "publish": {
            "channel": "campaign_sync",
            "mode": "combined",
            "success": combined_success,
            "message": summary,
            "banner_result": banner_res,
            "post_result": post_res,
        },
        "trace_events": [make_trace(
            "kampanya_sync", "ok" if combined_success else "failed", summary,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )],
    }


# ---------------------------------------------------------------------------
# NODE_FUNCTIONS registry (Bölüm 4.1 — public export)
#
# Hem yeni dinamik node'ları hem alias'larıyla mevcut/canonical node'ları
# tek bir haritada tutar. runtime._build_dynamic_graph buradan factory çeker.
# Geriye dönük uyum: önceki turda runtime.py içinde _DYNAMIC_NODE_FUNCS
# tanımlıydı; bu registry onun ayna kopyasıdır (modülün public yüzeyinde).
# ---------------------------------------------------------------------------


NODE_FUNCTIONS: dict = {
    # Yeni node'lar (Bölüm 4.1)
    "condition_check":  condition_check_node,
    "publish_post":     publish_post_node,
    "publish_story":    publish_story_node,
    "publish_banner":   publish_banner_node,
    "web_publish":      web_publish_node,
    "kampanya_sync":    kampanya_sync_node,
    # Canonical / mevcut node'lar
    "supervisor":        supervisor_node,
    "wait":              wait_node,
    "content_generator": content_generator_node,
    "generate_content":  content_generator_node,    # alias
    "risk_analyzer":     risk_analyzer_node,
    "risk_check":        risk_analyzer_node,        # alias
    "approval_gate":     approval_gate_node,
    "publisher":         publisher_node,
    "publish":           publisher_node,            # alias
    "monitor":           monitor_node,
    "notify_customer":   notify_customer_node,
    "create_coupon":     create_coupon_node,
    "finalize":          finalize_node,
}
