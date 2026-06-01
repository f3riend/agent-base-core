"""
Structured Rule schema — operatörün doğal dil niyetinin canonical formu.

Operatör Türkçe yazar:
    "Yeni mağaza oluştuktan 3 gün sonra Çanakkale hesabında Anneler Günü
     şablonu kullanarak Instagram paylaşımı yap."

nl_rule_parser bunu bir StructuredRule'a dönüştürür. structured_rule_engine
gelen olayları enabled=true rules ile eşler. langgraph.runtime bu rule'u
runtime'da bir StateGraph'a derler.

Tüm alanlar Pydantic ile validated — runtime'a ulaşmadan önce yapısal
hatalar (bilinmeyen event_type, geçersiz channel, vb.) yakalanır.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Canonical taxonomy — uzantı için tek nokta
# ---------------------------------------------------------------------------


# Tetik olabilecek event tipleri (mevcut event_router.CRITICAL/CREATIVE
# prefix tablolarıyla aynı vokabüler). Yeni event eklerken bu listeyi
# güncelle.
TRIGGER_EVENT_TYPES: tuple[str, ...] = (
    "store.created",
    "store.updated",
    "store.rejected",
    "store.deleted",
    "product.created",
    "product.updated",
    "product.deleted",
    "order.created",
    "order.shipped",
    "order.cancelled",
    "stock.updated",
    "shipping.delayed",
    "review.created",
    "review.negative",
    "customer.question",
    "campaign.created",
    "banner.created",       # Bölüm 6
    "banner.updated",
    "sales.updated",
    "story.created",        # Bölüm 6
    "coupon.created",       # Bölüm 6
)

CHANNELS: tuple[str, ...] = (
    "instagram",
    "facebook",
    "banner",
    "coupon",
    "faq",
    "support",
    "email",
    "sms",
    "trendyol",
    "shopify",
)

# Operatörün konuşma dilinde kullanabileceği şablon isimleri.
# Yeni şablon ekleme: bu listeyi genişlet + nl_rule_parser'a yansı.
CONTENT_TEMPLATES: tuple[str, ...] = (
    "anneler_gunu",
    "babalar_gunu",
    "yilbasi",
    "ramazan",
    "kurban_bayrami",
    "yaz_indirim",
    "kis_indirim",
    "kara_cuma",
    "yeni_urun_lansman",
    "magaza_acilis",
    "tesekkur",
    "ozur",
    "ozel_indirim",
    "generic",
)

# Eylem türleri — graph'taki node'lara birebir karşılık gelir.
ACTION_KINDS: tuple[str, ...] = (
    "wait",
    "generate_content",
    "risk_check",
    "approval",
    "publish",
    "monitor",
    "notify_customer",
    "create_coupon",
    "schedule_followup",
)


# Dinamik graph için kullanılabilen node tipleri. ACTION_KINDS ile büyük
# kısmı örtüşür ama dinamik sistem yeni tipler de tanır.
NODE_TYPES: tuple[str, ...] = (
    "supervisor",
    "wait",
    "condition_check",
    "generate_content",
    "create_coupon",
    "risk_check",
    "approval_gate",
    "publish",
    "publish_post",
    "publish_story",
    "publish_banner",
    "web_publish",
    "kampanya_sync",
    "notify_customer",
    "monitor",
    "finalize",
)


# Condition operatörleri — condition_check node'unda kullanılır.
CONDITION_OPERATORS: tuple[str, ...] = (
    ">=", ">", "<=", "<", "==", "!=", "in", "not_in", "contains",
)


# Approval tipleri — approval_requests tablosunun approval_type kolonu.
APPROVAL_TYPES: tuple[str, ...] = (
    "post_approval",
    "story_approval",
    "campaign_approval",
    "banner_approval",
    "generic_approval",
)


# Modüller — kural hangi domain'e aittir.
RULE_MODULES: tuple[str, ...] = (
    "social_media",
    "campaign",
    "product",
    "customer",
    "stock",
    "review",
    "order",
    "generic",
)


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TriggerSpec(BaseModel):
    """Hangi olay bu kuralı tetikler."""
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(
        description="Tam canonical event adı (örn. 'store.created')."
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Olay payload'unda eşleşmesi gereken alanlar.",
    )

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in TRIGGER_EVENT_TYPES:
            raise ValueError(
                f"unknown trigger event_type {v!r}; expected one of "
                f"{TRIGGER_EVENT_TYPES}"
            )
        return v


class TimingSpec(BaseModel):
    """Eylem zamanlama: tetik sonrası gecikme veya schedule."""
    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(
        default=0, ge=0, le=60 * 60 * 24 * 365,
        description="Tetik anından eyleme kadar geçecek saniye.",
    )
    schedule_at: str | None = Field(
        default=None,
        description="ISO 8601 mutlak zaman — verilirse delay_seconds göz ardı edilir.",
    )
    recurrence: Literal["once", "daily", "weekly", "monthly"] = "once"


class TargetSpec(BaseModel):
    """Eylemin yöneldiği hesap / varlık filtresi."""
    model_config = ConfigDict(extra="forbid")

    account_handle: str | None = Field(
        default=None,
        description="Sosyal hesap handle'ı (örn. 'canakkale_store').",
    )
    entity_type: str | None = Field(
        default=None,
        description="store | item | order | review — opsiyonel daraltma.",
    )
    entity_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Tetiklenen entity'de aranan alanlar (örn. {'city': 'Çanakkale'}).",
    )


class ContentSpec(BaseModel):
    """Üretilecek içeriğin şablonu ve kanalı."""
    model_config = ConfigDict(extra="forbid")

    template: str = Field(
        default="generic",
        description="İçerik şablonu (anneler_gunu, yilbasi, vb.).",
    )
    channel: str = Field(
        default="instagram",
        description="Yayın kanalı (instagram, banner, ...).",
    )
    headline_hint: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template")
    @classmethod
    def _validate_template(cls, v: str) -> str:
        v = (v or "generic").strip().lower()
        if v not in CONTENT_TEMPLATES:
            # Bilinmeyen şablon hata değil — parser tahmin etmiş olabilir.
            # generic'e düşür.
            return "generic"
        return v

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, v: str) -> str:
        v = (v or "instagram").strip().lower()
        if v not in CHANNELS:
            raise ValueError(
                f"unsupported channel {v!r}; expected one of {CHANNELS}"
            )
        return v


class ActionStep(BaseModel):
    """Graph içinde tek bir node'a karşılık gelen eylem."""
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="Eylem türü (action_kinds içinden).")
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ACTION_KINDS:
            raise ValueError(
                f"unknown action kind {v!r}; expected one of {ACTION_KINDS}"
            )
        return v


# ---------------------------------------------------------------------------
# Dinamik graph şeması — Bölüm 3 (yeni mimari)
# ---------------------------------------------------------------------------


class Condition(BaseModel):
    """condition_check node'unun değerlendirdiği tek bir koşul.

    Örnekler:
        Condition(field="discount_percent", operator=">=", value=40)
        Condition(field="category", operator="==", value="elektronik")
        Condition(field="store_id", operator="in", value=[1, 2, 3])
    """
    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Event payload'undan okunacak alan adı.")
    operator: str = Field(description="Karşılaştırma operatörü.")
    value: Any = Field(description="Karşılaştırılacak değer (int, str, list, ...).")

    @field_validator("operator")
    @classmethod
    def _validate_operator(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in CONDITION_OPERATORS:
            raise ValueError(
                f"unknown operator {v!r}; expected one of {CONDITION_OPERATORS}"
            )
        return v


class NodeDefinition(BaseModel):
    """Graph'taki tek bir node'un tanımı — node_factory bunu okur."""
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(
        description=(
            "Bu node'un graph içinde unique kimliği — depends_on ve "
            "parallel_with referansları bu id'yi kullanır."
        ),
    )
    node_type: str = Field(
        description="Node tipi (NODE_TYPES içinden). Factory bunu okur.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Node-spesifik parametreler. publish için account/template, "
            "wait için delay_seconds, condition_check için conditions, vb."
        ),
    )
    parallel_with: list[str] = Field(
        default_factory=list,
        description=(
            "Bu node hangi node_id'lerle EŞ ZAMANLI çalışır — fan-out dalı."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description=(
            "Bu node hangi node_id'ler bitmeden başlamaz — edge tanımı. "
            "Boş bırakılırsa graph builder linear sırayı kullanır."
        ),
    )

    @field_validator("node_type")
    @classmethod
    def _validate_node_type(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in NODE_TYPES:
            raise ValueError(
                f"unknown node_type {v!r}; expected one of {NODE_TYPES}"
            )
        return v

    @field_validator("node_id")
    @classmethod
    def _validate_node_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("node_id boş olamaz")
        if len(v) > 64:
            raise ValueError("node_id 64 karakteri aşamaz")
        return v


class GraphDefinition(BaseModel):
    """Bir kuralın LangGraph derleyicisine verilen tam graph tanımı.

    Akış:
        - nodes: tüm node'lar, her biri NodeDefinition.
        - entry: graph'a girilecek ilk node (default: ilk node).
        - exit: terminal node (default: son node).
        - interrupt_before / interrupt_after: LangGraph compile flag'leri.
    """
    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeDefinition] = Field(
        min_length=1,
        description="Sıralı/paralel node listesi.",
    )
    entry_node: str | None = Field(
        default=None,
        description=(
            "Graph'a girilecek ilk node_id. None ise nodes[0] kullanılır."
        ),
    )
    exit_node: str | None = Field(
        default=None,
        description=(
            "Graph'tan çıkılacak son node_id. None ise nodes[-1] kullanılır."
        ),
    )
    interrupt_before: list[str] = Field(
        default_factory=list,
        description=(
            "Bu node'lara girmeden ÖNCE LangGraph duraklar (örn. approval_gate)."
        ),
    )
    interrupt_after: list[str] = Field(
        default_factory=list,
        description=(
            "Bu node'lar çalıştıktan SONRA LangGraph duraklar (örn. wait)."
        ),
    )

    def node_ids(self) -> list[str]:
        return [n.node_id for n in self.nodes]

    def get_node(self, node_id: str) -> NodeDefinition | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None


# ---------------------------------------------------------------------------
# Top-level rule
# ---------------------------------------------------------------------------


class StructuredRule(BaseModel):
    """Operatörün doğal dil niyetinin canonical, deterministic temsili.

    `id` ve `org_id` veritabanı tarafı; parser tarafından doldurulmaz.
    """
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    user_id: int = 1
    org_id: int | None = None

    name: str = Field(min_length=2, max_length=120)
    natural_language: str = Field(
        description="Operatörün yazdığı ham Türkçe metin."
    )

    trigger: TriggerSpec
    timing: TimingSpec = Field(default_factory=TimingSpec)
    target: TargetSpec = Field(default_factory=TargetSpec)
    content: ContentSpec = Field(default_factory=ContentSpec)
    actions: list[ActionStep] = Field(
        min_length=1,
        description=(
            "Eylem zinciri — graph builder bunları sırayla node'lara dönüştürür."
        ),
    )

    # --- Dinamik mimari (Bölüm 3) — opsiyonel, geriye dönük uyumlu. ---
    graph_definition: GraphDefinition | None = Field(
        default=None,
        description=(
            "Eğer doluysa, runtime.build_graph bu tanımdan node'ları üretir. "
            "Boşsa eski actions listesinden synthesize edilir."
        ),
    )
    module: str = Field(
        default="generic",
        description=(
            "Kuralın ait olduğu modül — UI gruplama ve approval sekme türü için."
        ),
    )
    target_accounts: list[str] = Field(
        default_factory=list,
        description=(
            "Birden çok hedef hesap (@deneme, @istanbul). target.account_handle "
            "geriye dönük tek-hesap alanıdır."
        ),
    )
    target_template: str | None = Field(
        default=None,
        description=(
            "/mers, /kara_cuma gibi şablon adı. Resolver şablon tablosundan "
            "match eder; bulunamazsa missing_fields'a yazılır."
        ),
    )
    target_store: str | None = Field(
        default=None,
        description="@magaza:X sözdizimi — spesifik mağaza referansı.",
    )
    target_category: str | None = Field(
        default=None,
        description="@magaza?kategori sözdizimi — kategori filtresi.",
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description=(
            "condition_check node'unun değerlendireceği koşul listesi "
            "(%40 üzeri indirim, kategori=elektronik, ...)."
        ),
    )

    requires_approval: bool = True
    enabled: bool = True

    @field_validator("module")
    @classmethod
    def _validate_module(cls, v: str) -> str:
        v = (v or "generic").strip().lower()
        if v not in RULE_MODULES:
            return "generic"
        return v

    parse_confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Parser'ın bu yapıya ne kadar emin olduğu.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Parser'ın belirleyemediği ama belki gereken alanlar.",
    )

    created_at: str | None = None
    updated_at: str | None = None

    # ----- Convenience -----

    def trigger_key(self) -> str:
        """structured_rule_engine.find_matching'in O(1) lookup için kullandığı anahtar."""
        return self.trigger.event_type

    def has_action(self, kind: str) -> bool:
        return any(a.kind == kind for a in self.actions)

    def get_action(self, kind: str) -> ActionStep | None:
        for a in self.actions:
            if a.kind == kind:
                return a
        return None

    def all_target_accounts(self) -> list[str]:
        """target_accounts + legacy target.account_handle birleştir."""
        seen: set[str] = set()
        out: list[str] = []
        for a in self.target_accounts or []:
            a = (a or "").strip().lower()
            if a and a not in seen:
                seen.add(a); out.append(a)
        legacy = (self.target.account_handle or "").strip().lower()
        if legacy and legacy not in seen:
            seen.add(legacy); out.append(legacy)
        return out

    def effective_graph_definition(self) -> GraphDefinition:
        """graph_definition doluysa onu döner; değilse actions'tan synthesize.

        Bu sayede eski kurallar yeni runtime'la çalışmaya devam eder —
        runtime sadece effective_graph_definition() çağırır.
        """
        if self.graph_definition is not None:
            return self.graph_definition
        return _synthesize_graph_from_actions(self)

    def to_storage_dict(self) -> dict[str, Any]:
        """DB'ye yazılacak temiz dict."""
        return self.model_dump(mode="json", exclude_none=False)

    @classmethod
    def from_storage(cls, row: dict[str, Any] | Any) -> "StructuredRule":
        """structured_rules DB satırından restore.

        ÖNEMLİ: rule_json içindeki `id` çoğunlukla None'dır (kayıt
        sırasında üretilmemişti). DB sütunundaki canonical id'yi her
        zaman zorla bind et — setdefault hatası birden fazla turda
        execution id=None'a yol açıyordu.
        """
        import json
        d = dict(row)
        rule_json = d.get("rule_json") or "{}"
        try:
            parsed = json.loads(rule_json)
        except json.JSONDecodeError:
            parsed = {}
        # DB sütunlarını authoritative kabul et — rule_json eski/None
        # değerlere sahip olabilir.
        parsed["id"]               = d.get("id")
        parsed["user_id"]          = d.get("user_id", parsed.get("user_id", 1))
        parsed["org_id"]           = d.get("org_id", parsed.get("org_id"))
        parsed["name"]             = d.get("name", parsed.get("name", "rule"))
        parsed["natural_language"] = d.get("natural_language",
                                           parsed.get("natural_language", ""))
        parsed["created_at"]       = d.get("created_at", parsed.get("created_at"))
        parsed["updated_at"]       = d.get("updated_at", parsed.get("updated_at"))
        if "enabled" in d:
            parsed["enabled"] = bool(d.get("enabled"))
        return cls(**parsed)


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def empty_rule_template(natural_language: str) -> StructuredRule:
    """Parser başarısız olduğunda fallback olarak dönen iskelet."""
    return StructuredRule(
        name="Parse Edilemedi",
        natural_language=natural_language,
        trigger=TriggerSpec(event_type="store.created"),
        actions=[ActionStep(kind="generate_content")],
        parse_confidence=0.0,
        missing_fields=["trigger.event_type", "actions"],
        enabled=False,
    )


# ---------------------------------------------------------------------------
# Synthesize: eski actions listesinden GraphDefinition üret
# ---------------------------------------------------------------------------


# action_kind → node_type eşlemesi. publish için tek node_type ("publish")
# kullanırız — yayın türü (post/story/banner) channel'dan veya params'tan
# türetilir.
_ACTION_KIND_TO_NODE_TYPE: dict[str, str] = {
    "wait":             "wait",
    "generate_content": "generate_content",
    "risk_check":       "risk_check",
    "approval":         "approval_gate",
    "publish":          "publish",
    "monitor":          "monitor",
    "notify_customer":  "notify_customer",
    "create_coupon":    "create_coupon",
    "schedule_followup": "wait",
}


def _synthesize_graph_from_actions(rule: "StructuredRule") -> GraphDefinition:
    """Eski actions listesinden ve rule metadata'sından GraphDefinition türet.

    Bu fonksiyon "sabit zincir" değildir — sadece geriye dönük uyumluluk
    katmanı. Yeni kurallar parser tarafında doğrudan graph_definition
    üretir. Eski rulelar bu fonksiyondan geçer.

    Üretilen graph:
        supervisor → [actions ...] → finalize

    Linear sıra. Paralel dal yok. interrupt_before approval_gate üzerinde,
    interrupt_after wait üzerinde (delay_seconds > 0 ise).
    """
    nodes: list[NodeDefinition] = [
        NodeDefinition(
            node_id="supervisor",
            node_type="supervisor",
            params={},
        )
    ]

    interrupt_before: list[str] = []
    interrupt_after: list[str] = []

    for idx, action in enumerate(rule.actions):
        node_type = _ACTION_KIND_TO_NODE_TYPE.get(action.kind, action.kind)
        if node_type not in NODE_TYPES:
            continue
        node_id = f"{action.kind}_{idx}" if action.kind != "approval" else "approval_gate"
        # Approval ve wait için tek-instance kuralı: aynı id tekrar gelirse atla.
        existing_ids = {n.node_id for n in nodes}
        if node_id in existing_ids:
            node_id = f"{node_id}_{idx}"

        params = dict(action.config or {})
        # generate_content / publish için content & target alanlarını params'a aktar
        if node_type == "generate_content":
            params.setdefault("template", rule.content.template)
            params.setdefault("channel", rule.content.channel)
        elif node_type == "publish":
            params.setdefault("channel", rule.content.channel)
            accounts = rule.all_target_accounts()
            if accounts:
                params.setdefault("accounts", accounts)
        elif node_type == "wait":
            params.setdefault("delay_seconds", rule.timing.delay_seconds)

        nodes.append(NodeDefinition(
            node_id=node_id,
            node_type=node_type,
            params=params,
        ))

        if node_type == "approval_gate":
            interrupt_before.append(node_id)
        if node_type == "wait" and int(params.get("delay_seconds") or 0) > 0:
            interrupt_after.append(node_id)

    nodes.append(NodeDefinition(
        node_id="finalize",
        node_type="finalize",
        params={},
    ))

    return GraphDefinition(
        nodes=nodes,
        entry_node="supervisor",
        exit_node="finalize",
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )
