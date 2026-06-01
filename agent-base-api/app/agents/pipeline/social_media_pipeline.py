"""Multi-agent CrewAI pipeline for social media content generation and publishing.

Agent chain:
    Analyst → Caption + Image (parallel context) → Publisher

Uses the same try/except import fallback pattern as social_media_agent.py
so it degrades gracefully if crewai is not installed.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# CrewAI optional import with graceful fallback
# ---------------------------------------------------------------------------

try:
    from crewai import Agent, Crew, LLM, Task  # type: ignore[import]

    _CREWAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CREWAI_AVAILABLE = False

    class Agent:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

    class Task:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

    class Crew:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass

        def kickoff(self, inputs: dict | None = None) -> Any:  # noqa: ANN401
            raise RuntimeError(
                "crewai is not installed — install it with: pip install crewai"
            )

    class LLM:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            pass


_logger = logger.bind(module="social-media-pipeline")

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class SocialMediaCrewPipeline:
    """Multi-agent pipeline: Analyst → Caption + Image → Publisher.

    Task dependency chain ensures each agent receives context from upstream agents.
    """

    def run(
        self,
        message: str,
        reference_image_url: str | None = None,
        platform: str = "feed",
        publish_targets: dict[str, bool] | None = None,
        gemini_api_key: str | None = None,
        openai_api_key: str | None = None,
        fal_api_key: str | None = None,
        instagram_access_token: str | None = None,
        instagram_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline and return a result dict.

        When crewai is not available, falls back to the direct service layer.
        """
        if not _CREWAI_AVAILABLE:
            _logger.warning("crewai not available — falling back to direct service call.")
            return self._fallback_run(
                message=message,
                reference_image_url=reference_image_url,
                platform=platform,
                openai_api_key=openai_api_key,
                fal_api_key=fal_api_key,
                instagram_access_token=instagram_access_token,
                instagram_user_id=instagram_user_id,
                publish_targets=publish_targets or {},
            )

        targets = publish_targets or {"instagram_post": True}
        llm = LLM(
            model="gpt-4o",
            api_key=openai_api_key or "",
        )

        # ------------------------------------------------------------------
        # Agents
        # ------------------------------------------------------------------

        analyst_agent = Agent(
            role="Content Strategist & Scene Analyst",
            goal=(
                "Analyse the user's prompt and reference image (if any). "
                "Return a JSON ContentContext with enriched English image prompt, "
                "Turkish caption brief, intent, and relevant physics hints."
            ),
            backstory=(
                "You are a senior social media strategist with a deep understanding "
                "of visual composition, brand identity, and platform-specific requirements. "
                "You translate raw user prompts into structured creative briefs."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        caption_agent = Agent(
            role="Turkish Caption Writer",
            goal="Write a platform-aware Turkish Instagram caption based on the ContentContext.",
            backstory=(
                "You are an expert Turkish copywriter specialised in Instagram and social media. "
                "You write engaging captions that match the platform, tone, and brand voice."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        image_agent = Agent(
            role="AI Image Director",
            goal="Generate high-quality marketing images using the ContentContext and fal.ai.",
            backstory=(
                "You are a senior CGI art director who translates creative briefs into "
                "precise image generation prompts. You ensure physical plausibility and "
                "platform-optimised composition."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        publisher_agent = Agent(
            role="Social Media Publisher",
            goal="Publish caption and images to the specified platforms.",
            backstory=(
                "You are responsible for the final step of the content pipeline. "
                "You coordinate multi-platform publishing and report results accurately."
            ),
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        # ------------------------------------------------------------------
        # Tasks with explicit context dependencies
        # ------------------------------------------------------------------

        ref_note = (
            f" Reference image URL: {reference_image_url}" if reference_image_url else ""
        )
        analyze_task = Task(
            description=(
                f"Analyse the following user message and produce a ContentContext JSON.\n"
                f"Message: {message}\n"
                f"Platform: {platform}{ref_note}\n\n"
                "Return ONLY valid JSON matching the ContentContext schema "
                "(intent_category, intent_summary, target_platform, refined_image_prompt_en, "
                "refined_caption_tr, relevant_physics_hints, intent_confidence, "
                "needs_clarification, clarification_question, and optional scene_* fields)."
            ),
            expected_output="A JSON object matching ContentContext schema.",
            agent=analyst_agent,
        )

        caption_task = Task(
            description=(
                f"Using the ContentContext from the analyst, write a Turkish Instagram caption "
                f"for platform '{platform}'. Tone: professional. "
                "Output only the caption text (no JSON wrapper)."
            ),
            expected_output="A Turkish Instagram caption string with hashtags.",
            agent=caption_agent,
            context=[analyze_task],
        )

        image_task = Task(
            description=(
                f"Using the ContentContext from the analyst, generate {1} marketing image(s) "
                f"for platform '{platform}'. "
                "Return a JSON list of objects with a 'url' field for each generated image."
            ),
            expected_output="JSON list: [{\"url\": \"https://...\"}]",
            agent=image_agent,
            context=[analyze_task],
        )

        publish_task = Task(
            description=(
                f"Publish the caption (from caption_task) and images (from image_task) "
                f"to these platforms: {json.dumps(targets)}. "
                "Return a JSON summary of publish results per platform."
            ),
            expected_output="JSON object with per-platform publish results.",
            agent=publisher_agent,
            context=[caption_task, image_task],
        )

        # ------------------------------------------------------------------
        # Crew
        # ------------------------------------------------------------------

        crew = Crew(
            agents=[analyst_agent, caption_agent, image_agent, publisher_agent],
            tasks=[analyze_task, caption_task, image_task, publish_task],
            verbose=False,
        )

        _logger.info(
            "SocialMediaCrewPipeline.run starting platform={} message_len={}",
            platform,
            len(message),
        )

        try:
            crew_result = crew.kickoff(
                inputs={
                    "message": message,
                    "platform": platform,
                    "publish_targets": json.dumps(targets),
                    "fal_api_key": fal_api_key or "",
                    "openai_api_key": openai_api_key or "",
                    "instagram_access_token": instagram_access_token or "",
                    "instagram_user_id": instagram_user_id or "",
                }
            )
        except Exception as exc:
            _logger.exception("SocialMediaCrewPipeline.run failed")
            raise RuntimeError(f"CrewAI pipeline failed: {exc}") from exc

        _logger.info("SocialMediaCrewPipeline.run complete")
        return {
            "pipeline": "crew",
            "platform": platform,
            "result": str(crew_result) if not isinstance(crew_result, dict) else crew_result,
        }

    # ------------------------------------------------------------------
    # Fallback (no crewai)
    # ------------------------------------------------------------------

    def _fallback_run(
        self,
        message: str,
        reference_image_url: str | None,
        platform: str,
        openai_api_key: str | None,
        fal_api_key: str | None,
        instagram_access_token: str | None,
        instagram_user_id: str | None,
        publish_targets: dict[str, bool],
    ) -> dict[str, Any]:
        """Direct service calls when crewai is not installed."""
        from app.services.content_intelligence_service import ContentIntelligenceService
        from app.services.content_service import generate_caption, generate_images

        cis = ContentIntelligenceService()
        ctx = cis.analyze(
            user_prompt=message,
            reference_image_url=reference_image_url,
            platform=platform,
            openai_api_key=openai_api_key,
        )

        caption = generate_caption(
            konu=message,
            tone="profesyonel",
            openai_api_key=openai_api_key,
            context=ctx,
            platform=platform,
        )

        images = generate_images(
            prompt=message,
            count=1,
            fal_api_key=fal_api_key,
            platform=platform,
            openai_api_key=openai_api_key,
            context=ctx,
        )

        return {
            "pipeline": "fallback",
            "platform": platform,
            "caption": caption,
            "images": images,
            "context": ctx.model_dump(),
        }
