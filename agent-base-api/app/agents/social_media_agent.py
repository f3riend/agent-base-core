import uuid
from typing import Any

from app.services.content_service import generate_images, revise_image_with_feedback

try:
    from crewai.flow.flow import Flow, listen, start
    from crewai.flow.human_feedback import human_feedback
except ImportError:  # pragma: no cover
    Flow = object

    def start():
        def deco(fn):
            return fn

        return deco

    def listen(_event_name: str):
        def deco(fn):
            return fn

        return deco

    def human_feedback(_prompt: str):
        return None


class SocialMediaImageFlow(Flow):
    """
    Human-in-the-loop image flow:
    1) Baslangicta birden fazla aday gorsel uretir.
    2) Kullanici secim + feedback verir.
    3) Secilen gorselden revize varyantlar uretir.
    """

    sessions: dict[str, dict[str, Any]] = {}

    @start()
    def generate_candidates(
        self,
        prompt: str,
        count: int = 4,
        gemini_api_key: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())[:10]
        images = generate_images(prompt, count=count, gemini_api_key=gemini_api_key)
        payload = {
            "session_id": session_id,
            "prompt": prompt,
            "images": images,
            "selected_image_url": None,
            "feedback": None,
            "revisions": [],
            "status": "awaiting_feedback",
        }
        self.sessions[session_id] = payload
        return payload

    @listen("collect_selection_feedback")
    def revise_selected(
        self,
        session_id: str,
        selected_image_url: str,
        feedback: str | None = None,
        revised_count: int = 2,
        gemini_api_key: str | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            raise RuntimeError("Flow session bulunamadi.")
        resolved_feedback = (feedback or "").strip()
        if not resolved_feedback:
            prompt_text = (
                "Revizyon geri bildirimi girin (ornek: arkadaki cocuklari kaldir, arka plani sade yap): "
            )
            try:
                resolved_feedback = human_feedback(prompt_text) or ""
            except TypeError:
                resolved_feedback = human_feedback(prompt_text) or ""
        if not resolved_feedback:
            raise RuntimeError("Revizyon feedback gerekli.")
        revised = revise_image_with_feedback(
            image_url=selected_image_url,
            feedback=resolved_feedback,
            count=revised_count,
            gemini_api_key=gemini_api_key,
        )
        session["selected_image_url"] = selected_image_url
        session["feedback"] = resolved_feedback
        session["revisions"] = revised
        session["status"] = "completed"
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)
