"""Alembic environment — MySQL hedefli, app.models meta'sından otomatik üretir."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.database import Base  # noqa: E402
from app.core.env_settings import env_settings  # noqa: E402
from app.models import account, content_template, label, password_reset, social_document, usage_event, user  # noqa: F401, E402
from app.models import store, product, product_image, product_review, product_faq, product_metrics_weekly, system_snapshot, chat_session, chat_message, chat_memory  # noqa: F401, E402
from app.models import commerce_models  # noqa: F401, E402

config = context.config
config.set_main_option("sqlalchemy.url", env_settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
