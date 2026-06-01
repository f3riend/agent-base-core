"""Load .env once at process startup for all runtimes."""

from __future__ import annotations

import os


def load_app_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if os.environ.get("OPENAI_API_KEY"):
        print("[ENV] OPENAI_API_KEY loaded")
    else:
        print("[ENV] OPENAI_API_KEY not set — heuristic / fake tool mode")
