"""
Agent runtime — Tur 4 native OpenAI/Gemini path.

CrewAI Crew/Task katmanı kaldırıldı; bunun yerine LightAgent system prompt'u
+ OpenAI/Gemini chat completion. Mevcut public API (run_agent, manager_run)
korunuyor — caller'lar değişmiyor.
"""

from __future__ import annotations

import os
import time

from app.agents.manager.agent_factory import LightAgent, build_agent
from app.services.agent_manager_service import AgentManagerService


class AgentRuntimeService:
    def __init__(self, manager_service: AgentManagerService | None = None) -> None:
        self.manager_service = manager_service or AgentManagerService()

    def run_agent(
        self,
        agent_id: str,
        message: str,
        gemini_api_key: str | None = None,
    ) -> dict:
        agent_doc = self.manager_service.get_agent(agent_id)
        if not agent_doc:
            raise RuntimeError("Agent bulunamadi.")
        if not agent_doc.get("is_active", True):
            raise RuntimeError("Agent pasif.")
        agent = build_agent(agent_doc, gemini_api_key=gemini_api_key)

        start = int(time.time() * 1000)
        output = self._run_native(agent, message)
        latency = int(time.time() * 1000) - start
        return {
            "agent_id": agent_id,
            "output": output,
            "latency_ms": latency,
            "tools_used": agent_doc.get("tool_ids") or [],
        }

    def manager_run(
        self,
        message: str,
        agent_id: str | None = None,
        gemini_api_key: str | None = None,
    ) -> dict:
        selected = agent_id
        if not selected:
            agents = self.manager_service.list_agents()
            active = [a for a in agents if a.get("is_active", True)]
            if not active:
                raise RuntimeError("Calisabilir agent yok.")
            defaults = [a for a in active if a.get("is_default")]
            selected = (defaults[0] if defaults else active[0])["id"]
        return self.run_agent(selected, message, gemini_api_key=gemini_api_key)

    # -----------------------------------------------------------------------
    # Native LLM
    # -----------------------------------------------------------------------

    def _run_native(self, agent: LightAgent, message: str) -> str:
        """OpenAI / Gemini doğrudan chat completion.

        Model adı 'gemini/...' ile başlıyorsa Gemini, aksi halde OpenAI.
        """
        model = agent.model.strip()
        if model.startswith("gemini/"):
            return self._run_gemini(agent, message)
        return self._run_openai(agent, message)

    def _run_openai(self, agent: LightAgent, message: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=agent.api_key or os.environ.get("OPENAI_API_KEY"))
        completion = client.chat.completions.create(
            model=agent.model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": agent.system_prompt()},
                {"role": "user", "content": message},
            ],
            temperature=0.4,
            max_tokens=600,
        )
        return (completion.choices[0].message.content or "").strip()

    def _run_gemini(self, agent: LightAgent, message: str) -> str:
        # google-genai zaten dependency'de var — direct kullan
        try:
            from google import genai
        except ImportError:
            # Fallback: OpenAI'a düş
            return self._run_openai(agent, message)

        model_name = agent.model.split("/", 1)[1] if "/" in agent.model else "gemini-2.5-flash"
        client = genai.Client(api_key=agent.api_key or os.environ.get("GEMINI_API_KEY"))
        resp = client.models.generate_content(
            model=model_name,
            contents=f"{agent.system_prompt()}\n\nKULLANICI: {message}",
        )
        return (getattr(resp, "text", "") or "").strip()
