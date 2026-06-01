from datetime import datetime

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SocialDocument(Base):
    """Sosyal veri: workspace + koleksiyon + doc_id + JSON payload."""

    __tablename__ = "social_documents"
    __table_args__ = (
        UniqueConstraint("workspace_uid", "collection", "doc_id", name="uq_social_documents_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collection: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
