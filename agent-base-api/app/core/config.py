from functools import lru_cache
from pathlib import Path
import yaml
import os


_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_CANDIDATES = (
    _ROOT / "config" / "config.yaml",
    _ROOT / "config" / "settings.yaml",
)


def _resolve_config_path() -> Path:
    for path in _CONFIG_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Config dosyasi bulunamadi. Beklenen dosyalardan biri gerekli: "
        + ", ".join(str(p) for p in _CONFIG_CANDIDATES)
    )


@lru_cache
def load_config():
    with open(_resolve_config_path(), "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    

    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    

    if 'celery' in config:
        config['celery']['broker'] = redis_url
        config['celery']['backend'] = redis_url
    
    return config