from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Integration context (unchanged)
# ---------------------------------------------------------------------------


class IntegrationContext(BaseModel):
    fal_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None


# ---------------------------------------------------------------------------
# Content intelligence — scene + intent analysis
# ---------------------------------------------------------------------------


class ContentContext(BaseModel):
    """Rich analysis produced by ContentIntelligenceService.analyze()."""

    # Scene analysis — only populated if a reference image was provided
    scene_subject: str | None = None
    scene_lighting: str | None = None
    scene_background: str | None = None
    scene_mood: str | None = None
    scene_color_palette: str | None = None
    scene_composition: str | None = None

    # Intent — always populated
    intent_category: Literal[
        "product_showcase", "lifestyle", "announcement", "engagement", "informational"
    ]
    intent_summary: str
    target_platform: Literal["feed", "story", "video"] = "feed"

    # Enriched outputs
    refined_image_prompt_en: str
    refined_caption_tr: str
    relevant_physics_hints: list[str] = []

    # Confidence / clarification
    intent_confidence: float = 1.0
    needs_clarification: bool = False
    clarification_question: str | None = None


class ContentContextResponse(ContentContext):
    """API response wrapper — mirrors ContentContext."""


# ---------------------------------------------------------------------------
# Task status (Celery async result)
# ---------------------------------------------------------------------------


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["pending", "started", "success", "failure", "retry"]
    result: dict | None = None
    error: str | None = None
    progress: int = 0  # 0-100


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------


class CaptionRequest(IntegrationContext):
    konu: str = Field(min_length=2, max_length=6000)
    tone: str = "eglenceli"
    reference_image_url: str | None = None
    platform: Literal["feed", "story", "video"] = "feed"


class RevizeRequest(IntegrationContext):
    mevcut_caption: str = Field(min_length=1, max_length=6000)
    revize_talebi: str = Field(min_length=1, max_length=3000)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class PublishTargets(BaseModel):
    instagram_post: bool = True
    instagram_story: bool = False
    facebook_post: bool = False


class PostRequest(IntegrationContext):
    image_url: str = Field(min_length=8)
    image_urls: list[str] | None = None
    caption: str = Field(default="", max_length=6000)
    publish_targets: PublishTargets | None = None
    facebook_page_id: str | None = None


class InstagramLinkedAccountsRequest(BaseModel):
    """User/Page access token — list Facebook Pages with linked Instagram Business accounts."""

    access_token: str = Field(min_length=10, max_length=4000)


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------


class ImageGenerateRequest(IntegrationContext):
    prompt: str = Field(min_length=2, max_length=3000)
    count: int = 1
    platform: Literal["feed", "story", "video"] = "feed"
    reference_image_url: str | None = None
    reference_image_urls: list[str] | None = Field(
        default=None,
        description="Ek referans görselleri (Celery/async üretim ile birlikte).",
    )
    use_gpt: bool = Field(
        default=False,
        description="İstemci uyumu; raster hep OpenAI ile çalışır.",
    )
    output_size: str | None = Field(
        default=None,
        description="Opsiyonel tam çıktı boyutu (ör. '1088x1360' Instagram 4:5, '1088x1920' story).",
    )
    banner_size: str | None = Field(
        default=None,
        description="Geriye uyumlu alias; output_size yoksa kullanılır.",
    )


class ImageReferenceGenerateRequest(IntegrationContext):
    reference_image_url: str = Field(min_length=8)
    prompt: str = Field(min_length=2, max_length=3000)
    count: int = 1
    reference_image_urls: list[str] | None = Field(
        default=None,
        description="Ek referans görselleri (birincil reference_image_url ile birlikte çoklu düzenleme).",
    )

    mode: Literal["background", "lifestyle", "gpt"] = Field(
        default="background",
        description=(
            "PromptBuilder / CIS niyeti (background vs lifestyle sözlüğü). "
            "Raster hep OpenAI gpt-image-2 düzenleme."
        ),
    )
    skip_professionalization: bool = Field(
        default=False,
        description="True ise CIS + PromptBuilder atlanır, verilen prompt doğrudan kullanılır.",
    )
    output_size: str | None = Field(
        default=None,
        description="Opsiyonel tam çıktı boyutu, ör. campaign banner için 1600x700.",
    )
    banner_size: str | None = Field(
        default=None,
        description="Geriye uyumlu alias; campaign banner için 1600x700.",
    )


class ImageReviseRequest(IntegrationContext):
    image_url: str = Field(min_length=8)
    feedback: str = Field(min_length=2, max_length=10000)
    count: int = 1
    platform: Literal["feed", "story", "video"] = "feed"
    reference_image_urls: list[str] | None = Field(
        default=None,
        description="Revizyon sırasında ek bağlam katmanları (ürün tutarlılığı vb.).",
    )
    output_size: str | None = Field(
        default=None,
        description="Opsiyonel tam çıktı boyutu, ör. campaign banner için 1600x700.",
    )
    banner_size: str | None = Field(
        default=None,
        description="Geriye uyumlu alias; campaign banner için 1600x700.",
    )
    revision_context: Literal["social", "campaign_banner"] = Field(
        default="social",
        description="social: CIS + feed odaklı revize; campaign_banner: şablon tuvali, feed bias için CIS atlanır.",
    )


# ---------------------------------------------------------------------------
# Holiday content generation
# ---------------------------------------------------------------------------


class HolidayGenerateRequest(IntegrationContext):
    """Generate caption + image prompt for a specific holiday, then produce the image."""

    holiday_name: str = Field(min_length=2, max_length=300)
    """Human-readable holiday name, e.g. 'Cumhuriyet Bayramı' or 'New Year'."""

    date_key: str = Field(min_length=10, max_length=10)
    """ISO date string, e.g. '2026-10-29'."""

    locale: str = Field(default="tr", max_length=5)
    """Language for caption: 'tr' or 'en'."""

    generate_image: bool = True
    """When True (default), generate an image using the AI-crafted image prompt."""

    generate_video: bool = False
    """When True, produce a short vertical video (fal.ai) from a GPT motion prompt."""

    extra_instructions: str | None = Field(
        default=None,
        max_length=2000,
        description="Ek yönergeler: görsel/metin dil veya sembol kısıtları (örn. Arapça yazı isteme).",
    )


class HolidayGenerateResponse(BaseModel):
    caption: str
    image_prompt: str
    image_url: str = ""
    video_prompt: str = ""
    video_url: str = ""


class VideoGenerateRequest(IntegrationContext):
    """Text-to-video or image-to-video (single reference) via fal.ai."""

    prompt: str = Field(min_length=2, max_length=3000)
    image_url: str | None = Field(default=None, max_length=2000)
    duration_sec: int = Field(
        default=5,
        ge=3,
        le=15,
        description="Kling v3 Pro clip length in seconds (sent to fal as duration).",
    )
    generate_audio: bool = Field(
        default=True,
        description="Kling native audio when true (fal generate_audio).",
    )


class VideoGenerateResponse(BaseModel):
    video_url: str
    cost_usd: float | None = None


# ---------------------------------------------------------------------------
# Analyze (new)
# ---------------------------------------------------------------------------


class AnalyzeRequest(IntegrationContext):
    prompt: str = Field(min_length=2, max_length=3000)
    reference_image_url: str | None = None
    platform: Literal["feed", "story", "video"] = "feed"


# ---------------------------------------------------------------------------
# Session flow (unchanged)
# ---------------------------------------------------------------------------


class FlowSessionStartRequest(IntegrationContext):
    prompt: str = Field(min_length=2, max_length=3000)
    count: int = 4


class FlowSessionFeedbackRequest(IntegrationContext):
    session_id: str = Field(min_length=6, max_length=80)
    selected_image_url: str = Field(min_length=8)
    feedback: str | None = Field(default=None, max_length=3000)
    revised_count: int = 2


class AutomationEventRequest(IntegrationContext):
    """External event payload that triggers rule-based social automation."""

    event_type: str = Field(min_length=2, max_length=120)
    event_payload: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = Field(default=None, max_length=128)
    override_delay_days: int | None = Field(default=None, ge=0, le=365)
    override_publish_time: str | None = Field(default=None, min_length=4, max_length=5)
    dry_run: bool = False


class AutomationEventResponse(BaseModel):
    queued: bool = False
    event_id: str
    matched_rule_id: str
    scheduled_post_id: str | None = None
    scheduled_date: str
    scheduled_time: str
    caption: str
    image_url: str
    publish_targets: dict[str, bool]


class AutomationChatTriggerRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    rule_id: str | None = Field(default=None, max_length=128)
    dry_run: bool = False


class AutomationChatTriggerResponse(BaseModel):
    event_id: str
    scheduled_post_id: str | None = None
    scheduled_date: str
    scheduled_time: str
    event_type: str
    caption: str
    image_url: str
    interpreted: dict[str, Any]


class AIOperateContext(BaseModel):
    product_id: str | None = None
    store_id: str | None = None
    order_id: str | None = None
    mode: Literal["analiz", "operasyon", "strateji", "icerik"] | None = "analiz"


class AIOperateHistoryItem(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=4000)


class AIOperateRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    context: AIOperateContext = Field(default_factory=AIOperateContext)
    history: list[AIOperateHistoryItem] = Field(default_factory=list)
    conversation_id: str | None = Field(default=None, max_length=128)


class ActionCommand(BaseModel):
    action_id: str | None = None
    action_type: str = Field(min_length=2, max_length=120)
    entity_type: Literal["product", "store", "order", "workspace"] = "product"
    entity_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmation_required: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"
    source_message_id: str | None = None


class AIOperateAction(BaseModel):
    label: str
    command: str


class AIOperateEvent(BaseModel):
    type: str
    tool: str | None = None
    status: str
    timestamp: str
    description: str = ""


class AIOperateToolState(BaseModel):
    tool: str
    status: Literal["pending", "running", "completed"]
    timestamp: str
    description: str = ""


class AIOperateCard(BaseModel):
    type: Literal[
        "text",
        "warning_card",
        "recommendation_card",
        "analytics_card",
        "approval_card",
        "tool_execution",
        "success_card",
    ]
    title: str
    description: str
    actions: list[AIOperateAction] = Field(default_factory=list)
    preview_image: str | None = None


class AIOperateMessage(BaseModel):
    role: Literal["assistant", "system"]
    content: str
    timestamp: str


class AIOperatePendingAction(BaseModel):
    id: str
    title: str
    status: str
    timestamp: str


class AIOperateResponse(BaseModel):
    conversation_id: str
    events: list[AIOperateEvent] = Field(default_factory=list)
    tool_states: list[AIOperateToolState] = Field(default_factory=list)
    cards: list[AIOperateCard] = Field(default_factory=list)
    messages: list[AIOperateMessage] = Field(default_factory=list)
    pending_actions: list[AIOperatePendingAction] = Field(default_factory=list)
