"""
Agent factory — Tur 4'te native LLM'e geçildi.

CrewAI bağımlılığı kaldırıldı; CrewAI'nin Agent/LLM yapısı yerine küçük
bir `LightAgent` wrapper'ı kullanılıyor. `agent_runtime_service` bu
wrapper'ı doğrudan OpenAI / Gemini chat completion ile çalıştırıyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.manager.tool_registry import get_tools
from app.integrations.ai_client import resolve_gemini_key


@dataclass
class LightAgent:
    """Minimal CrewAI-replacement: tek-shot prompt + tool listing."""
    role: str
    goal: str
    backstory: str
    model: str
    api_key: str | None
    tools: list[Any] = field(default_factory=list)

    def system_prompt(self) -> str:
        return (
            f"Rol: {self.role}\n"
            f"Hedef: {self.goal}\n"
            f"Geçmiş: {self.backstory}\n\n"
            f"Mevcut araçlar: {[getattr(t, 'name', repr(t)) for t in self.tools]}\n"
            "Kısa, net ve Türkçe cevap ver."
        )


def build_agent(agent_data: dict, gemini_api_key: str | None = None) -> LightAgent:
    model = (agent_data.get("model") or "gemini/gemini-2.5-flash").strip()
    key = resolve_gemini_key(gemini_api_key)
    tools = get_tools(agent_data.get("tool_ids") or [])
    return LightAgent(
        role=agent_data["role"],
        goal=agent_data["goal"],
        backstory=agent_data["backstory"],
        model=model,
        api_key=key,
        tools=tools,
    )
