"""PromptBuilder — stateless service that converts a ContentContext into
final model prompts for image generation, caption generation, and revision.

All image prompts are in English.
All caption prompts instruct the model to respond in Turkish.
"""

from __future__ import annotations

from app.schemas.content import ContentContext

# ---------------------------------------------------------------------------
# Platform composition directives
# ---------------------------------------------------------------------------

_PLATFORM_IMAGE_RULES: dict[str, str] = {
    "feed": (
        "PLATFORM — INSTAGRAM FEED (1:1 square):\n"
        "- Rich detail, strong central focal point, balanced composition.\n"
        "- Avoid dead edges; subject should anchor the square frame.\n"
    ),
    "story": (
        "PLATFORM — INSTAGRAM / FACEBOOK STORY (9:16 vertical):\n"
        "- Single bold visual message, minimal surrounding elements.\n"
        "- Subject should fill the vertical frame; top and bottom safe zones respected.\n"
    ),
    "video": (
        "PLATFORM — VIDEO / REEL THUMBNAIL (16:9 or 1:1):\n"
        "- Motion-friendly scene: clear subject with room to animate around it.\n"
        "- Dynamic potential: strong leading lines or implied movement.\n"
    ),
}

_PLATFORM_CAPTION_RULES: dict[str, str] = {
    "feed": "Uzunluk: 2-4 cumle + 5 hashtag",
    "story": "Uzunluk: 1 etkili cumle + maksimum 3 hashtag",
    "video": "Uzunluk: 2 cumle + harekete geçirici cagri (call-to-action) + 5 hashtag",
}


class PromptBuilder:
    """Stateless prompt builder. No instance variables."""

    # ------------------------------------------------------------------
    # Image prompt
    # ------------------------------------------------------------------

    def build_image_prompt(
        self,
        context: ContentContext,
        variant_index: int,
        total: int,
        *,
        is_reference_mode: bool = False,
    ) -> str:
        """Build a final English image prompt from *context*.

        Args:
            context: Populated ContentContext from ContentIntelligenceService.
            variant_index: 0-based index of this variant in the batch.
            total: Total number of variants being generated.
            is_reference_mode: When True, prepends a strict product identity lock
                block instructing the model to preserve the reference product exactly.

        Returns:
            A structured English prompt string ready to be sent to the image model.
        """
        platform = context.target_platform or "feed"
        platform_rule = _PLATFORM_IMAGE_RULES.get(platform, _PLATFORM_IMAGE_RULES["feed"])

        # Strict product identity lock when a reference image is provided
        reference_lock = ""
        if is_reference_mode:
            reference_lock = (
                "## PRODUCT IDENTITY LOCK — HIGHEST PRIORITY\n"
                "A REFERENCE IMAGE is attached. The product in that image must appear in the output\n"
                "EXACTLY as-is — zero changes allowed:\n"
                "  ✗ Do NOT change product shape, silhouette, or proportions\n"
                "  ✗ Do NOT change product colors, gradients, or material finish\n"
                "  ✗ Do NOT alter, remove, or regenerate any label, logo, text, or badge\n"
                "  ✗ Do NOT morph, warp, or stylise the product itself\n"
                "  ✗ Do NOT replace the product with a 'similar-looking' object\n"
                "You may ONLY change: background, scene, environment, props around the product,\n"
                "and scene-level lighting/color grading — never touching the product surface.\n\n"
            )

        # Physics hints — only from context (pre-filtered by CIS)
        physics_block = ""
        if context.relevant_physics_hints:
            hints_text = "\n".join(f"- {h}" for h in context.relevant_physics_hints)
            physics_block = f"PHYSICS / REALISM CONSTRAINTS (apply ONLY these):\n{hints_text}\n\n"

        # Variation clause
        if total <= 1:
            variation = "VARIATION: Single definitive output."
        elif variant_index == 0:
            variation = "VARIATION 1 — PRIMARY: closest match to the brief, strongest composition."
        else:
            variation = (
                f"VARIATION {variant_index + 1} — ALTERNATE: same subject and scene, "
                "change ONLY lighting or crop angle — do NOT change subject identity."
            )

        return (
            "ROLE: Senior commercial photographer and CGI art director.\n"
            "TASK: Generate ONE high-resolution marketing image.\n\n"
            f"{reference_lock}"
            f"BRIEF (English):\n{context.refined_image_prompt_en.strip()}\n\n"
            f"INTENT: {context.intent_summary}\n\n"
            f"{platform_rule}\n"
            f"{physics_block}"
            "GLOBAL CONSTRAINTS:\n"
            "- Photoreal or premium CGI-photoreal finish for paid social.\n"
            "- No watermarks, UI chrome, or random text unless brief demands it.\n"
            "- No collage frames, no split-screen unless requested.\n\n"
            f"{variation}\n"
        )

    def build_image_prompt_with_label(
        self,
        context: ContentContext,
        label_info: dict[str, object],
        variant_index: int,
        total: int,
    ) -> str:
        """Build reference-image prompt with strict label text preservation block."""
        base_prompt = self.build_image_prompt(
            context=context,
            variant_index=variant_index,
            total=total,
            is_reference_mode=True,
        )
        brand_name = str(label_info.get("brand_name") or "").strip()
        product_name = str(label_info.get("product_name") or "").strip()
        label_style = str(label_info.get("label_style") or "").strip()
        label_layout = str(label_info.get("label_layout") or "").strip()
        label_texts = label_info.get("label_texts")
        label_colors = label_info.get("label_colors")

        text_lines = [str(x).strip() for x in (label_texts or []) if str(x).strip()] if isinstance(label_texts, list) else []
        color_lines = [str(x).strip() for x in (label_colors or []) if str(x).strip()] if isinstance(label_colors, list) else []

        label_block = (
            "## LABEL PRESERVATION — CRITICAL PRIORITY\n"
            "The following text appears on the product label.\n"
            "Reproduce EVERY character EXACTLY as listed.\n"
            "Do NOT hallucinate, translate, or alter any text.\n"
            "Turkish characters (Ğ, Ü, Ş, İ, Ö, Ç) must appear correctly.\n\n"
            f"Brand: {brand_name}\n"
            f"Product name: {product_name}\n"
            "Label texts (exact, line by line):\n"
            f"{chr(10).join(text_lines) if text_lines else '(none)'}\n"
            f"Label visual style: {label_style}\n"
            f"Label colors: {', '.join(color_lines) if color_lines else ''}\n"
            f"Label layout: {label_layout}\n\n"
        )
        return f"{label_block}{base_prompt}"

    # ------------------------------------------------------------------
    # Caption prompt
    # ------------------------------------------------------------------

    def build_caption_prompt(
        self,
        context: ContentContext,
        tone: str = "professional",
    ) -> str:
        """Build a Turkish caption generation prompt from *context*.

        The output instructs the language model to write the caption in Turkish.

        Args:
            context: Populated ContentContext.
            tone: Tone descriptor (e.g. "eglenceli", "profesyonel", "samimi").

        Returns:
            A structured prompt string (Turkish instruction) for the caption model.
        """
        platform = context.target_platform or "feed"
        length_rule = _PLATFORM_CAPTION_RULES.get(platform, _PLATFORM_CAPTION_RULES["feed"])

        return (
            "Sen deneyimli bir Türkçe Instagram içerik uzmanısın.\n"
            "Sadece istenen caption metnini üretirsin, ekstra açıklama yazmazsın.\n\n"
            f"Konu (zenginleştirilmiş brief):\n{context.refined_caption_tr.strip()}\n\n"
            f"İçerik amacı: {context.intent_summary}\n"
            f"Ton: {tone}\n"
            f"{length_rule}\n\n"
            "ZORUNLU ÇIKTI DİLİ: Türkçe — cümleler ve hashtagler Türkçe.\n"
            "Kurallar:\n"
            "- Sadece caption metnini yaz, başka açıklama/öngörü yazma.\n"
            "- Hashtagleri caption sonuna ekle.\n"
        )

    # ------------------------------------------------------------------
    # Revision prompt
    # ------------------------------------------------------------------

    def build_revision_prompt(
        self,
        context: ContentContext,
        user_feedback: str,
        variant_index: int,
        total: int,
    ) -> str:
        """Build a production revision prompt in English from *context* + feedback.

        Args:
            context: ContentContext from CIS analysis of the current image (+ feedback as prompt).
            user_feedback: Raw user feedback text (may be Turkish or English).
            variant_index: 0-based variant index.
            total: Total variants for this revision batch.

        Returns:
            A structured English revision prompt ready for the image model.
        """
        feedback = (user_feedback or "").strip()

        # Interpret feedback category from known keywords.
        # Use casefold() for Unicode-aware case insensitive matching.
        feedback_cf = feedback.casefold()
        if any(k in feedback_cf for k in [
            "renk", "color", "colour", "tint", "shade", "ton", "palette",
            "sicak", "warm", "cool", "bright", "dark", "parlak", "koyu",
            # raw unicode variants (casefold keeps them)
            "sıcak", "soğuk", "açık",
        ]):
            category = "COLOR ADJUSTMENT"
        elif any(k in feedback_cf for k in ["kompozisyon", "composition", "frame", "crop", "angle", "aci", "açı"]):
            category = "COMPOSITION / FRAMING"
        elif any(k in feedback_cf for k in ["arkaplan", "background", "bg", "arka plan"]):
            category = "BACKGROUND"
        elif any(k in feedback_cf for k in ["isik", "ışı", "light", "shadow", "golge", "gölge", "aydinlatma", "aydınlatma", "lighting", "yumusat", "yumuşat"]):
            category = "LIGHTING"
        elif any(k in feedback_cf for k in ["konu", "subject", "object", "nesne", "urun", "ürün", "product"]):
            category = "SUBJECT / OBJECT"
        else:
            category = "GENERAL EDIT"

        # Physics hints
        physics_block = ""
        if context.relevant_physics_hints:
            hints_text = "\n".join(f"- {h}" for h in context.relevant_physics_hints)
            physics_block = f"\nPHYSICS / REALISM CONSTRAINTS (apply ONLY these):\n{hints_text}\n"

        # Variation clause
        if total <= 1:
            variation = "VARIATION: Single definitive revised output."
        else:
            variation = (
                f"VARIATION {variant_index + 1}/{total}: "
                "same revision intent, slightly different execution (lighting/crop only)."
            )

        return (
            "ROLE: Senior commercial retoucher performing a PRODUCTION REVISION.\n"
            "INPUT: The attached image is the CURRENT MASTER.\n\n"
            f"FEEDBACK CATEGORY: {category}\n"
            f"USER FEEDBACK: {feedback}\n\n"
            "REVISION PROTOCOL:\n"
            f"1) Apply {category} change as described — make it clearly visible.\n"
            "2) Preserve all other elements (subject identity, palette, SKU silhouette).\n"
            "3) Run implicit QA for physical plausibility after applying the change.\n"
            "4) Clean halos, no cutout fringe, consistent noise/grain vs sharpness.\n"
            f"{physics_block}\n"
            "GLOBAL CONSTRAINTS:\n"
            "- Do NOT add watermarks, text overlays, or branding unless brief requires it.\n"
            "- Photoreal or premium CGI-photoreal finish.\n\n"
            f"{variation}\n"
        )
