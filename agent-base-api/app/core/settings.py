from pydantic import BaseModel, Field
from typing import List
from app.core.config import load_config
from dotenv import load_dotenv
import os




class AppSettings(BaseModel):
    name: str
    description: str
    env: str
    version: float

class ServerSettings(BaseModel):
    host: str
    port: int
    reload: bool


class CelerySettings(BaseModel):
    broker: str
    backend: str


class LoggerSettings(BaseModel):
    level: str
    format: str
    rotation: str


class CorsSettings(BaseModel):
    allow_origins: List[str]
    allow_methods: List[str]
    allow_headers: List[str]
    allow_credentials: bool


class WhisperModel(BaseModel):
    model: str
    device: str
    compute_type: str



class Settings(BaseModel):
    app: AppSettings
    server: ServerSettings
    celery: CelerySettings
    whisper: WhisperModel = Field(
        default_factory=lambda: WhisperModel(
            model="small",
            device="cpu",
            compute_type="int8",
        )
    )
    logger: LoggerSettings
    cors: CorsSettings



def get_settings()->Settings:
    raw = load_config()
    load_dotenv()
    if 'REDIS_URL' in os.environ:
        raw['celery']['broker'] = os.environ['REDIS_URL']
        raw['celery']['backend'] = os.environ['REDIS_URL']
    return Settings(**raw)




settings = get_settings()

from app.core.logging import setup_logging
setup_logging()