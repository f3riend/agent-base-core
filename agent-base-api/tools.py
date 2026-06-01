"""
Fake tools with per-tool argument schemas — runtime-agnostic.

Daha önce CrewAI BaseTool'unu miras alıyordu; Tur 2'de CrewAI tamamen
kaldırıldı. Bunun yerine kendi minimum `BaseTool` protocol class'ımız
var (aşağıda). Bu sayede tool'lar HEM LangGraph node'larından HEM de
eski (artık deprecated) `task_executor` worker'ından çağırılabiliyor.

Her tool şunları sağlar:
    - name: stable string identifier (registry lookup key)
    - description: AI tarafından okunan amacı
    - args_schema: Pydantic model — strict argument validation
    - _run(**kwargs): asıl iş

Tools deterministik mock üretip fake_ai_api timeline'a event emit eder.
Gerçek dış API çağrısı yok — gerçek publish `tool_adapters/` paketinde
feature flag arkasında.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Type

from pydantic import BaseModel, ConfigDict, Field

from fake_tool_timeline import emit_tool_event


# ---------------------------------------------------------------------------
# Runtime-agnostic BaseTool — CrewAI replacement
# ---------------------------------------------------------------------------


class BaseTool:
    """Tool protocol class — runtime-agnostic.

    Eski CrewAI BaseTool ile aynı public API'ye sahip (name, description,
    args_schema, run, _run, set_execution_context). Mevcut tüm
    tool_registry / langgraph_engine.nodes çağrıları değişmeden çalışır.

    Subclass override etmesi gereken:
        - name (class attr)
        - description (class attr)
        - args_schema (class attr — Pydantic BaseModel subclass)
        - _run(**kwargs) — asıl iş
    """

    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel] | None = None

    def __init__(self) -> None:
        # Subclass class-attr olarak set etmiş olabilir; instance copy
        if not self.name:
            self.name = type(self).__name__
        if not self.description:
            self.description = self.__doc__ or ""

    def run(self, **kwargs: Any) -> dict:
        """Public entry: argümanları args_schema ile validate edip _run çağır."""
        if self.args_schema is not None:
            validated = self.args_schema(**kwargs).model_dump()
        else:
            validated = dict(kwargs)
        return self._run(**validated)

    def _run(self, **kwargs: Any) -> dict:
        raise NotImplementedError(f"{type(self).__name__}._run must be overridden")

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"


def _strict() -> ConfigDict:
    return ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Per-tool argument schemas
# ---------------------------------------------------------------------------


class InstagramCampaignArgs(BaseModel):
    model_config = _strict()
    headline: str = Field(description="Short attention-grabbing headline (Turkish or English)")
    hook: str | None = Field(default=None, description="One-sentence hook for the caption")
    target_audience: str | None = Field(default=None, description="Target audience description")
    hashtags: list[str] = Field(default_factory=list, description="Suggested hashtags without '#'")


class BannerGeneratorArgs(BaseModel):
    model_config = _strict()
    headline: str = Field(description="Banner headline")
    subline: str | None = Field(default=None, description="Optional subline / supporting copy")
    cta: str = Field(description="Call-to-action label (e.g. 'Hemen al')")


class CouponGeneratorArgs(BaseModel):
    model_config = _strict()
    label: str = Field(description="Customer-visible coupon label")
    percent: int = Field(default=10, ge=1, le=90, description="Discount percent (1..90)")
    expires_in_days: int = Field(default=7, ge=1, le=90, description="Days until coupon expires")


class FaqUpdateArgs(BaseModel):
    model_config = _strict()
    topic: str = Field(description="FAQ section / topic (e.g. 'kargo', 'iade')")
    question: str = Field(description="The customer question to answer")
    answer: str = Field(description="The answer to publish")


class SupportResponseArgs(BaseModel):
    model_config = _strict()
    customer_question: str = Field(description="Original customer message")
    tone: str = Field(default="friendly", description="Tone hint: friendly | formal | apologetic")


class TrendAnalysisArgs(BaseModel):
    model_config = _strict()
    focus: str = Field(description="Subject under analysis (product name / category / store)")
    lookback_days: int = Field(default=7, ge=1, le=90, description="Window of days to analyze")


class LowStockNotificationArgs(BaseModel):
    model_config = _strict()
    item_name: str = Field(description="Item whose stock is low")
    current_stock: int = Field(ge=0, description="Current stock count")
    threshold: int = Field(default=10, ge=0, description="Stock threshold that triggered the alert")


# ---------------------------------------------------------------------------
# Logging mixin
# ---------------------------------------------------------------------------


class LoggingToolMixin:
    _log_callback: Callable | None = None
    _task_id: int | None = None

    def set_execution_context(self, task_id: int, log_callback: Callable):
        self._log_callback = log_callback
        self._task_id = task_id

    def _log_execution(
        self,
        input_payload: dict,
        output_payload: dict | None,
        status: str,
        duration_ms: int,
        error: str | None = None,
    ):
        if self._log_callback and self._task_id is not None:
            self._log_callback(
                task_id=self._task_id,
                tool_name=self.name,
                input_payload=input_payload,
                output_payload=output_payload,
                status=status,
                duration_ms=duration_ms,
                error=error,
            )

    def _resolve_user_id(self) -> int | None:
        """Best-effort lookup of the AI task's tenant via self._task_id.

        Tools execute inside the CrewAI worker which calls
        set_execution_context(task_id, log_callback) before kickoff. The
        task row carries user_id, so we trace task → user. Returns None if
        the task row can't be resolved — callers should treat None as
        "no real credentials available, fall back to fake behavior".
        """
        if self._task_id is None:
            return None
        try:
            from task_service import get_task
            row = get_task(self._task_id)
            if row is None:
                return None
            return int(row["user_id"]) if "user_id" in row.keys() else None
        except Exception:
            return None


def _fake_run(
    tool,
    tool_name: str,
    message: str,
    log_group: str,
    input_payload: dict,
    output_extra: dict | None = None,
) -> dict:
    """Common fake execution path: sandbox + log + timeline + JSON result.

    The actual mock work is wrapped by tool_sandbox.execute_in_sandbox so that
    every tool invocation gets real timeout enforcement, retry-with-backoff
    and circuit-breaker protection — even fake tools must execute safely.
    """
    from tool_sandbox import execute_in_sandbox

    def _do_work() -> dict:
        print(f"[TOOL] {tool_name}: {message} payload={input_payload}")
        time.sleep(0.2)
        result = {
            "success": True,
            "tool": tool_name,
            "message": message,
            "input": input_payload,
        }
        if output_extra:
            result.update(output_extra)
        return result

    sandboxed = execute_in_sandbox(
        tool,
        _do_work,
        input_payload,
        tool_name=tool_name,
        task_id=getattr(tool, "_task_id", None),
    )

    if sandboxed["status"] == "success":
        output = sandboxed["output"]
        tool._log_execution(
            input_payload, output, "success", sandboxed["duration_ms"]
        )
        emit_tool_event(tool_name, message, log_group=log_group, payload=output)
        return output

    error_payload = {
        "success": False,
        "tool": tool_name,
        "error": sandboxed["error"],
        "input": input_payload,
    }
    tool._log_execution(
        input_payload,
        error_payload,
        "failed",
        sandboxed["duration_ms"],
        error=sandboxed["error"],
    )
    emit_tool_event(
        tool_name,
        f"{message} (failed: {sandboxed['error']})",
        log_group=log_group,
        payload=error_payload,
    )
    return error_payload


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


class InstagramCampaignTool(LoggingToolMixin, BaseTool):
    name: str = "instagram_campaign_tool"
    description: str = (
        "Drafts an Instagram post. Use for campaign/launch/promotion intents. "
        "Requires a headline; optionally hook, target_audience, hashtags. "
        "Will publish to a real account only if a credential is connected "
        "for the executing tenant; otherwise produces a draft."
    )
    args_schema: Type[BaseModel] = InstagramCampaignArgs

    def _run(
        self,
        headline: str,
        hook: str | None = None,
        target_audience: str | None = None,
        hashtags: list[str] | None = None,
    ) -> dict:
        payload = {
            "headline": headline,
            "hook": hook,
            "target_audience": target_audience,
            "hashtags": hashtags or [],
        }
        caption = f"{headline}"
        if hook:
            caption = f"{headline} — {hook}"

        # Credential resolution — never raise. If anything fails, fall
        # back silently to the fake-draft path so the runtime stays alive
        # even with no APP_SECRET_KEY / no connected accounts.
        cred = None
        user_id = self._resolve_user_id()
        if user_id is not None:
            try:
                from social_credentials import try_get_credential
                cred = try_get_credential(user_id, "instagram")
            except Exception as exc:
                print(f"[INSTAGRAM_TOOL] credential lookup failed: {exc}")
                cred = None

        if cred is not None:
            # Real-publish capability detected. We DO NOT actually hit the
            # Instagram API yet — that lives behind a separate provider
            # adapter (Phase D of the evolution plan) and an explicit
            # SOCIAL_PUBLISH_LIVE feature flag. For now we log the intent
            # explicitly so observability/audit can show a real account
            # WOULD have received this post.
            output_extra = {
                "caption": caption,
                "hashtags": payload["hashtags"],
                "publish_mode": "real_publish_would_happen",
                "account_handle": cred.account_handle,
                "credential_id": cred.id,
                "scope": cred.scope,
            }
            print(
                f"[INSTAGRAM_TOOL] REAL_PUBLISH_WOULD_HAPPEN "
                f"user={user_id} handle=@{cred.account_handle} "
                f"credential_id={cred.id} headline={headline!r}"
            )
            return _fake_run(
                self,
                self.name,
                f"Instagram gönderisi hazır (real-publish hedefi: @{cred.account_handle}): {headline}",
                "campaign",
                payload,
                output_extra=output_extra,
            )

        # No credential → existing fake-draft behavior, untouched.
        return _fake_run(
            self,
            self.name,
            f"Instagram gönderisi taslağı: {headline}",
            "campaign",
            payload,
            output_extra={
                "caption": caption,
                "hashtags": payload["hashtags"],
                "publish_mode": "draft_only",
            },
        )


class BannerGeneratorTool(LoggingToolMixin, BaseTool):
    name: str = "banner_generator_tool"
    description: str = (
        "Generates a banner. Use for storefront promotions or campaign visuals. "
        "Requires headline + cta."
    )
    args_schema: Type[BaseModel] = BannerGeneratorArgs

    def _run(
        self,
        headline: str,
        cta: str,
        subline: str | None = None,
    ) -> dict:
        payload = {"headline": headline, "subline": subline, "cta": cta}
        return _fake_run(
            self,
            self.name,
            f"Banner: {headline}",
            "banner",
            payload,
            output_extra={"image_url": f"https://placehold.co/1200x400/png?text={headline[:24]}"},
        )


class CouponGeneratorTool(LoggingToolMixin, BaseTool):
    name: str = "coupon_generator_tool"
    description: str = (
        "Issues a coupon. Use for discount promotions and customer recovery."
    )
    args_schema: Type[BaseModel] = CouponGeneratorArgs

    def _run(
        self,
        label: str,
        percent: int = 10,
        expires_in_days: int = 7,
    ) -> dict:
        payload = {"label": label, "percent": percent, "expires_in_days": expires_in_days}
        code = f"PROMO{percent}-{label[:6].upper()}"
        return _fake_run(
            self,
            self.name,
            f"Kupon oluşturuldu: %{percent} ({code})",
            "campaign",
            payload,
            output_extra={"code": code},
        )


class FaqUpdateTool(LoggingToolMixin, BaseTool):
    name: str = "faq_update_tool"
    description: str = (
        "Adds or updates an FAQ entry. Use to address recurring customer questions."
    )
    args_schema: Type[BaseModel] = FaqUpdateArgs

    def _run(self, topic: str, question: str, answer: str) -> dict:
        payload = {"topic": topic, "question": question, "answer": answer}
        return _fake_run(
            self,
            self.name,
            f"SSS güncellendi: {topic}",
            "customer",
            payload,
        )


class SupportResponseTool(LoggingToolMixin, BaseTool):
    name: str = "support_response_tool"
    description: str = (
        "Drafts a support reply to a customer question. Use for complaints, "
        "shipping/order issues, and reputation responses."
    )
    args_schema: Type[BaseModel] = SupportResponseArgs

    def _run(self, customer_question: str, tone: str = "friendly") -> dict:
        payload = {"customer_question": customer_question, "tone": tone}
        return _fake_run(
            self,
            self.name,
            "Destek cevabı hazırlandı",
            "customer",
            payload,
            output_extra={"draft_reply": f"({tone}) Cevap: {customer_question[:60]}..."},
        )


class TrendAnalysisTool(LoggingToolMixin, BaseTool):
    name: str = "trend_analysis_tool"
    description: str = (
        "Runs a trend analysis. Use for sales velocity, engagement spikes, "
        "and viral-product detection."
    )
    args_schema: Type[BaseModel] = TrendAnalysisArgs

    def _run(self, focus: str, lookback_days: int = 7) -> dict:
        payload = {"focus": focus, "lookback_days": lookback_days}
        return _fake_run(
            self,
            self.name,
            f"Trend analizi: {focus} ({lookback_days}g)",
            "insight",
            payload,
            output_extra={"window_days": lookback_days, "subject": focus},
        )


class LowStockNotificationTool(LoggingToolMixin, BaseTool):
    name: str = "low_stock_notification_tool"
    description: str = (
        "Emits a low-stock alert. Use for critical inventory paths."
    )
    args_schema: Type[BaseModel] = LowStockNotificationArgs

    def _run(
        self,
        item_name: str,
        current_stock: int,
        threshold: int = 10,
    ) -> dict:
        payload = {
            "item_name": item_name,
            "current_stock": current_stock,
            "threshold": threshold,
        }
        return _fake_run(
            self,
            self.name,
            f"Düşük stok uyarısı: {item_name} ({current_stock}<{threshold})",
            "stock",
            payload,
        )


ALL_TOOLS_RAW = {
    "instagram_campaign_tool": InstagramCampaignTool(),
    "banner_generator_tool": BannerGeneratorTool(),
    "coupon_generator_tool": CouponGeneratorTool(),
    "faq_update_tool": FaqUpdateTool(),
    "support_response_tool": SupportResponseTool(),
    "trend_analysis_tool": TrendAnalysisTool(),
    "low_stock_notification_tool": LowStockNotificationTool(),
}


def _load_validated_tools() -> dict:
    from tool_schema_validator import get_valid_tools, validate_all_tools

    validate_all_tools(ALL_TOOLS_RAW)
    return get_valid_tools(ALL_TOOLS_RAW)


TOOLS = _load_validated_tools()
