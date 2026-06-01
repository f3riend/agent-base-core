from __future__ import annotations

from typing import Any


class ConversationMemorySummarizer:
    """Compact memory summarizer to keep LLM context persistent."""

    def summarize(self, history: list[dict[str, Any]], max_items: int = 8) -> str:
        if not history:
            return ""
        rows = []
        for item in history[-max_items:]:
            role = str(item.get("role") or "user").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            short = content[:180]
            if len(content) > 180:
                short += "..."
            prefix = "Kullanici" if role == "user" else "Asistan"
            rows.append(f"{prefix}: {short}")
        if not rows:
            return ""
        text = " | ".join(rows)
        lower = text.lower()
        topic = ""
        if "teslimat" in lower or "kargo" in lower:
            topic = "Gecmis konusmalarda teslimat ekseni tekrar etti."
        elif "kampanya" in lower:
            topic = "Gecmis konusmalarda kampanya performansi odaktaydi."
        elif "fiyat" in lower:
            topic = "Gecmis konusmalarda fiyat etkisi tartisildi."
        if topic:
            return f"{topic} {text}"
        return text
