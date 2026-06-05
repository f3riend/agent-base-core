from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.env_settings import env_settings

engine = create_engine(
    env_settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models.account import Account  # noqa: F401
    from app.models.content_template import ContentTemplate  # noqa: F401
    from app.models.label import LabelRow  # noqa: F401
    from app.models.password_reset import PasswordResetToken  # noqa: F401
    from app.models.social_document import SocialDocument  # noqa: F401
    from app.models.usage_event import UsageEvent  # noqa: F401
    from app.models.user import User  # noqa: F401
    # Yeni commerce + chat tabloları (Faz 1)
    from app.models.store import Store  # noqa: F401
    from app.models.product import Product  # noqa: F401
    from app.models.product_image import ProductImage  # noqa: F401
    from app.models.product_review import ProductReview  # noqa: F401
    from app.models.product_faq import ProductFaq  # noqa: F401
    from app.models.product_metrics_weekly import ProductMetricsWeekly  # noqa: F401
    from app.models.system_snapshot import SystemSnapshot  # noqa: F401
    from app.models.chat_session import ChatSession  # noqa: F401
    from app.models.chat_message import ChatMessage  # noqa: F401
    from app.models.chat_memory import ChatMemory  # noqa: F401

    Base.metadata.create_all(bind=engine)
