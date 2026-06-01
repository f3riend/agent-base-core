from app.core.settings import settings
from loguru import logger
import sys

_logging_configured = False

def setup_logging():
    global _logging_configured
    if _logging_configured:
        return
    
    logger.remove()

    logger.add(
        sys.stdout,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level} | "
            "{extra[module]} | "
            "{file.name}:{line} | "
            "{message}"
        ),
        level="INFO",
        enqueue=False,
    )

    logger.add(
        "logs/app.log",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level} | "
            "{extra[module]} | "
            "{file.name}:{line} | "
            "{message}"
        ),
        level=settings.logger.level,
        rotation=settings.logger.rotation,
        enqueue=False,
    )
    
    _logging_configured = True