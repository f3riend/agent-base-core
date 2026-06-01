import time
import uuid

from sqlalchemy import select

from app.agents.manager.default_registry import DEFAULT_AGENTS
from app.core.database import SessionLocal
from app.models.social_document import SocialDocument

_WORKSPACE = "__system__"
_COLLECTION = "agents"


class AgentManagerService:
    def seed_defaults_if_missing(self) -> None:
        db = SessionLocal()
        try:
            for default_id, payload in DEFAULT_AGENTS.items():
                existing = db.scalar(
                    select(SocialDocument).where(
                        SocialDocument.workspace_uid == _WORKSPACE,
                        SocialDocument.collection == _COLLECTION,
                        SocialDocument.doc_id == default_id,
                    ),
                )
                if existing is None:
                    now = int(time.time() * 1000)
                    data = dict(payload)
                    data["created_at"] = now
                    data["updated_at"] = now
                    db.add(
                        SocialDocument(
                            workspace_uid=_WORKSPACE,
                            collection=_COLLECTION,
                            doc_id=default_id,
                            payload=data,
                        ),
                    )
            db.commit()
        finally:
            db.close()

    def create_agent(self, payload: dict) -> dict:
        db = SessionLocal()
        try:
            now = int(time.time() * 1000)
            data = dict(payload)
            data["created_at"] = now
            data["updated_at"] = now
            doc_id = str(uuid.uuid4())
            row = SocialDocument(
                workspace_uid=_WORKSPACE,
                collection=_COLLECTION,
                doc_id=doc_id,
                payload=data,
            )
            db.add(row)
            db.commit()
            return {"id": doc_id, **data}
        finally:
            db.close()

    def list_agents(self) -> list[dict]:
        db = SessionLocal()
        try:
            rows = db.scalars(
                select(SocialDocument)
                .where(SocialDocument.workspace_uid == _WORKSPACE, SocialDocument.collection == _COLLECTION)
                .order_by(SocialDocument.created_at.asc()),
            ).all()
            return [{"id": r.doc_id, **(r.payload or {})} for r in rows]
        finally:
            db.close()

    def get_agent(self, agent_id: str) -> dict | None:
        db = SessionLocal()
        try:
            row = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == _WORKSPACE,
                    SocialDocument.collection == _COLLECTION,
                    SocialDocument.doc_id == agent_id,
                ),
            )
            if row is None:
                return None
            return {"id": row.doc_id, **(row.payload or {})}
        finally:
            db.close()

    def update_agent(self, agent_id: str, payload: dict) -> dict | None:
        db = SessionLocal()
        try:
            row = db.scalar(
                select(SocialDocument).where(
                    SocialDocument.workspace_uid == _WORKSPACE,
                    SocialDocument.collection == _COLLECTION,
                    SocialDocument.doc_id == agent_id,
                ),
            )
            if row is None:
                return None
            merged = dict(row.payload or {})
            updates = {k: v for k, v in payload.items() if v is not None}
            updates["updated_at"] = int(time.time() * 1000)
            merged.update(updates)
            row.payload = merged
            db.commit()
            return {"id": agent_id, **merged}
        finally:
            db.close()
