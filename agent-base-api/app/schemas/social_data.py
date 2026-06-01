from pydantic import BaseModel, Field


class SocialPatchBody(BaseModel):
    merge: dict = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)


class SocialCreateResponse(BaseModel):
    id: str
    payload: dict
