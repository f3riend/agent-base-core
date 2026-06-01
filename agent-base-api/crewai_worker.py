"""
DEPRECATED — geriye dönük import path için tutuluyor.

Tur 2'de CrewAI tamamen kaldırıldı. Yeni worker: `task_executor.py`.
Bu dosya sadece `python crewai_worker.py` çağrıları kırılmasın diye
duruyor; içerik `task_executor.main()`'e delegate ediyor.
"""

from __future__ import annotations

print("[DEPRECATED] crewai_worker.py is now a thin shim for task_executor — "
      "CrewAI has been fully removed (Phase 2-of-LangGraph migration).")


def main() -> None:
    from task_executor import main as _main
    _main()


if __name__ == "__main__":
    main()
