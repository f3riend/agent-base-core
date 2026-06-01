"""
Hazır kural şablonları — operatör seçer, parametreleri doldurur, kural oluşur.

Her template:
    - id (kararlı slug)
    - name (operatör için Türkçe başlık)
    - description (ne yaptığını anlatan tek cümle)
    - category (anniversary | inventory | onboarding | customer | seasonal)
    - parameters (operatörün doldurması gereken alanlar)
    - build(params) → natural_language string üretir; bu da
      nl_rule_parser üzerinden StructuredRule'a çevrilir

Tasarım gereği template'ler NL üretiyor (yapay structured rule değil):
böylece her template ile manuel olarak yazılmış bir kural arasında
parser tutarlılığı garanti.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Parametre türleri
# ---------------------------------------------------------------------------


@dataclass
class TemplateParam:
    key: str
    label: str
    kind: str = "text"   # text | number | select | date
    default: Any = None
    options: list[str] | None = None   # select için
    required: bool = True
    hint: str | None = None


@dataclass
class RuleTemplate:
    id: str
    name: str
    category: str
    icon: str
    description: str
    parameters: list[TemplateParam]
    build: Callable[[dict[str, Any]], str]    # params → NL text

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "icon": self.icon,
            "description": self.description,
            "parameters": [
                {
                    "key": p.key, "label": p.label, "kind": p.kind,
                    "default": p.default, "options": p.options,
                    "required": p.required, "hint": p.hint,
                }
                for p in self.parameters
            ],
        }


# ---------------------------------------------------------------------------
# Şablon registry
# ---------------------------------------------------------------------------


def _t_anneler_gunu(p: dict) -> str:
    handle = (p.get("hesap") or "").strip()
    delay = int(p.get("gun_sonra") or 3)
    handle_part = f"{handle} hesabında " if handle else ""
    return (
        f"Yeni mağaza oluştuktan {delay} gün sonra {handle_part}Anneler Günü "
        f"şablonu kullanarak Instagram paylaşımı yap."
    )


def _t_babalar_gunu(p: dict) -> str:
    handle = (p.get("hesap") or "").strip()
    delay = int(p.get("gun_sonra") or 3)
    handle_part = f"{handle} hesabında " if handle else ""
    return (
        f"Yeni mağaza oluştuktan {delay} gün sonra {handle_part}Babalar Günü "
        f"şablonu kullanarak Instagram paylaşımı yap."
    )


def _t_stok_dususu(p: dict) -> str:
    threshold = int(p.get("esik") or 10)
    return (
        f"Stok {threshold} adedin altına düştüğünde hemen müşteriye "
        f"bildirim gönder ve düşük stok uyarısı yayınla."
    )


def _t_yeni_magaza(p: dict) -> str:
    delay = int(p.get("gun_sonra") or 1)
    return (
        f"Yeni mağaza oluştuktan {delay} gün sonra Mağaza Açılışı şablonu "
        f"kullanarak Instagram paylaşımı yap."
    )


def _t_negatif_yorum(p: dict) -> str:
    return (
        "Olumsuz yorum geldiğinde hemen müşteriye bildirim gönder ve "
        "destek akışı başlat."
    )


def _t_yilbasi(p: dict) -> str:
    handle = (p.get("hesap") or "").strip()
    handle_part = f"{handle} hesabında " if handle else ""
    return (
        f"Yeni yıla 7 gün kala {handle_part}Yılbaşı şablonu kullanarak "
        f"Instagram paylaşımı yap ve %20 kupon oluştur."
    )


def _t_kara_cuma(p: dict) -> str:
    pct = int(p.get("indirim") or 30)
    return (
        f"Kara Cuma günü hemen Kara Cuma şablonu kullanarak Instagram "
        f"paylaşımı yap ve %{pct} kupon oluştur."
    )


def _t_kargo_bilgi(p: dict) -> str:
    return (
        "Kargo gecikmesi olduğunda hemen müşteriye bilgilendirme gönder "
        "ve destek akışı başlat."
    )


def _t_musteri_soru(p: dict) -> str:
    return (
        "Müşteri sorusu geldiğinde hemen destek akışı başlat ve "
        "müşteriye taslak yanıt hazırla."
    )


TEMPLATES: dict[str, RuleTemplate] = {
    t.id: t for t in [
        RuleTemplate(
            id="anneler_gunu",
            name="Anneler Günü Kampanyası",
            category="seasonal",
            icon="🌷",
            description="Yeni mağaza açılışından N gün sonra Anneler Günü temalı paylaşım.",
            parameters=[
                TemplateParam("hesap", "Hesap / Mağaza", "text", default="",
                              required=False, hint="örn. Çanakkale"),
                TemplateParam("gun_sonra", "Gün sonra", "number", default=3),
            ],
            build=_t_anneler_gunu,
        ),
        RuleTemplate(
            id="babalar_gunu",
            name="Babalar Günü Kampanyası",
            category="seasonal",
            icon="🎁",
            description="Yeni mağaza açılışından N gün sonra Babalar Günü temalı paylaşım.",
            parameters=[
                TemplateParam("hesap", "Hesap / Mağaza", "text", default="",
                              required=False),
                TemplateParam("gun_sonra", "Gün sonra", "number", default=3),
            ],
            build=_t_babalar_gunu,
        ),
        RuleTemplate(
            id="stok_dususu",
            name="Stok Düşüşü Uyarısı",
            category="inventory",
            icon="📦",
            description="Stok belli bir eşiğin altına indiğinde otomatik uyarı.",
            parameters=[
                TemplateParam("esik", "Stok eşiği", "number", default=10),
            ],
            build=_t_stok_dususu,
        ),
        RuleTemplate(
            id="yeni_magaza",
            name="Yeni Mağaza Karşılaması",
            category="onboarding",
            icon="🏪",
            description="Yeni mağaza açıldıktan sonra otomatik karşılama paylaşımı.",
            parameters=[
                TemplateParam("gun_sonra", "Gün sonra", "number", default=1),
            ],
            build=_t_yeni_magaza,
        ),
        RuleTemplate(
            id="negatif_yorum",
            name="Olumsuz Yorum Tepkisi",
            category="customer",
            icon="🛑",
            description="Olumsuz yorum geldiğinde hemen müşteriye dönüş + destek akışı.",
            parameters=[],
            build=_t_negatif_yorum,
        ),
        RuleTemplate(
            id="yilbasi",
            name="Yılbaşı Kampanyası",
            category="seasonal",
            icon="🎄",
            description="Yılbaşına 7 gün kala otomatik kampanya akışı.",
            parameters=[
                TemplateParam("hesap", "Hesap / Mağaza", "text", default="",
                              required=False),
            ],
            build=_t_yilbasi,
        ),
        RuleTemplate(
            id="kara_cuma",
            name="Kara Cuma Kampanyası",
            category="seasonal",
            icon="🛍️",
            description="Kara Cuma günü tema + indirim kuponu.",
            parameters=[
                TemplateParam("indirim", "İndirim yüzdesi", "number", default=30),
            ],
            build=_t_kara_cuma,
        ),
        RuleTemplate(
            id="kargo_bilgi",
            name="Kargo Gecikme Bilgilendirme",
            category="customer",
            icon="🚚",
            description="Kargo gecikmesi tespit edildiğinde proaktif müşteri bilgilendirmesi.",
            parameters=[],
            build=_t_kargo_bilgi,
        ),
        RuleTemplate(
            id="musteri_soru",
            name="Müşteri Sorusuna Otomatik Yanıt",
            category="customer",
            icon="❓",
            description="Müşteri sorusu geldiğinde AI taslak yanıt hazırlar.",
            parameters=[],
            build=_t_musteri_soru,
        ),
    ]
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_templates(category: str | None = None) -> list[dict]:
    items = list(TEMPLATES.values())
    if category:
        items = [t for t in items if t.category == category]
    return [t.to_dict() for t in items]


def get_template(template_id: str) -> RuleTemplate | None:
    return TEMPLATES.get(template_id)


def materialize(template_id: str, params: dict[str, Any]) -> dict:
    """Şablonu params ile doldur — natural language + parse output döndür.

    Operatör UI'da "Şablon seç → form doldur → Önizle/Etkinleştir" akışı için.
    """
    template = TEMPLATES.get(template_id)
    if template is None:
        raise ValueError(f"unknown template_id={template_id!r}")
    natural = template.build(params or {})
    return {
        "template_id": template_id,
        "template_name": template.name,
        "natural_language": natural,
    }


CATEGORY_LABELS = {
    "seasonal":   "Özel günler ve sezonlar",
    "inventory":  "Stok ve envanter",
    "onboarding": "Yeni mağaza karşılama",
    "customer":   "Müşteri etkileşimi",
}
