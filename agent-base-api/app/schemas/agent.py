from pydantic import BaseModel, Field


class ToolBinding(BaseModel):
    tool_id: str = Field(min_length=1)
    enabled: bool = True


class AgentConfig(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role: str = Field(min_length=3, max_length=200)
    goal: str = Field(min_length=3, max_length=400)
    backstory: str = Field(min_length=3, max_length=4000)
    model: str = "gemini/gemini-2.5-flash"
    tool_ids: list[str] = []
    is_active: bool = True
    is_default: bool = False


class AgentCreateRequest(AgentConfig):
    pass


class AgentUpdateRequest(BaseModel):
    role: str | None = Field(default=None, min_length=3, max_length=200)
    goal: str | None = Field(default=None, min_length=3, max_length=400)
    backstory: str | None = Field(default=None, min_length=3, max_length=4000)
    model: str | None = None
    tool_ids: list[str] | None = None
    is_active: bool | None = None


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    session_id: str | None = None
    gemini_api_key: str | None = None


class ManagerRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    session_id: str | None = None
    agent_id: str | None = None
    gemini_api_key: str | None = None
