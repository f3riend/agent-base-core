"""Workflow scheduler worker.

Concurrency architecture (future — not implemented):
----------------------------------------------------------------
TODO: Replace sequential for-loop with a bounded thread pool so
      multiple workflows can execute in parallel per poll cycle.

TODO: Introduce worker identity + row-level locking on
      workflow_instances (locked_by, locked_at) before execution
      to support distributed workers without double-processing.

TODO: Partition the queue by entity_type or workflow_name so
      independent shards scale horizontally.

TODO: Consider async I/O for DB + API calls once worker count
      exceeds single-process throughput.
----------------------------------------------------------------
"""

from env_bootstrap import load_app_env

load_app_env()

import time
import traceback

from db import execute_query, init_db
from scheduling_service import (
    SCHEDULE_FIRED,
    fire_due_schedules,
    list_schedules,
)
from workflow_service import (
    execute_workflow,
    get_pending_workflows,
    should_run,
)

POLL_INTERVAL = 5

init_db()


def _cleanup_ghost_executions() -> int:
    """Trace'i olmayan running execution'ları failed'a çek.

    Senaryo: listener / workflow_worker yeniden başlatıldığında
    `rule_executions` tablosunda `status='running'` ama `graph_node_traces`'te
    hiç kayıt olmayan satırlar kalır (process kill anında graph.invoke
    başlamamıştı). 5 dakika eşiği yeni başlatılan execution'lara yanlış
    pozitif vermez (start_execution çoğu durumda <30s tamamlanır).
    """
    import sqlite3 as _sq
    try:
        conn = _sq.connect("listener.db", timeout=5)
        try:
            cur = conn.execute(
                """
                UPDATE rule_executions
                SET status='failed',
                    error='ghost_cleanup',
                    ended_at=datetime('now')
                WHERE status='running'
                  AND id NOT IN (SELECT DISTINCT execution_id FROM graph_node_traces)
                  AND started_at < datetime('now', '-5 minutes')
                """
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as exc:
        print(f"[GHOST_CLEANUP] failed: {exc}")
        return 0


def _handle_wait_resumes() -> int:
    """LangGraph wait_node tarafından oluşturulmuş ve fire edilmiş
    scheduled_entries için resume_after_wait çağır.

    İş akışı:
        1. wait_node bir scheduled_entry oluşturur (payload.resume_after_wait=True).
        2. fire_due_schedules() o entry'yi fire edip status='fired' yapar.
        3. Burada FIRED durumdaki resume entry'leri tarayıp runtime'a
           resume çağrısı yapıyoruz. İşlenmiş entry'leri payload.handled=True
           ile damgalıyoruz ki tekrar işlenmesin.

    Returns: resume edilen execution sayısı.
    """
    resumed = 0
    # Tüm tenant'lar — user_id filtresi olmadan FIRED entry'leri tara.
    # DEFAULT_USER_ID değiştirildiği için (1 → 3) eski hardcode user_id=1
    # filtresi çalışmıyordu; doğrudan DB query ile tüm user'ları kapsa.
    try:
        from db import execute_query
        rows = execute_query(
            "SELECT * FROM scheduled_entries WHERE status=? "
            "ORDER BY scheduled_at ASC LIMIT 200",
            (SCHEDULE_FIRED,),
        )
        from scheduling_service import _row_to_entry
        entries = [_row_to_entry(r) for r in rows]
    except Exception as exc:
        print(f"[WAIT_RESUME] schedule scan failed: {exc}")
        return 0

    for entry in entries:
        payload = entry.get("payload") or {}
        if not payload.get("resume_after_wait"):
            continue
        if payload.get("handled"):
            continue
        execution_id = payload.get("execution_id")
        if not execution_id:
            continue

        try:
            from langgraph_engine.runtime import resume_after_wait
            result = resume_after_wait(int(execution_id))
            # Mark handled
            from db import execute_write, now_iso
            import json as _json
            new_payload = dict(payload)
            new_payload["handled"] = True
            new_payload["handled_at"] = now_iso()
            new_payload["resume_status"] = result.get("status")
            execute_write(
                """UPDATE scheduled_entries SET payload_json=?, updated_at=?
                   WHERE id=?""",
                (_json.dumps(new_payload, ensure_ascii=False, default=str),
                 now_iso(), int(entry["id"])),
            )
            if not result.get("noop"):
                resumed += 1
                print(
                    f"[WAIT_RESUME] execution #{execution_id} resumed → "
                    f"status={result.get('status')} node={result.get('current_node')}"
                )
        except Exception as exc:
            print(f"[WAIT_RESUME] execution #{execution_id} resume failed: {exc}")
            traceback.print_exc()
    return resumed


def main():
    print("\nWorkflow worker started...\n")

    while True:
        poll_started = time.monotonic()

        try:
            # 1) Fire any due operator-scheduled entries first — they
            #    materialize as workflow_instances that this same loop
            #    will pick up in the next iteration. Also: wait_node
            #    entries get resumed via _handle_wait_resumes().
            try:
                fired = fire_due_schedules(limit=50)
                if fired["count"]:
                    print(
                        f"[METRIC] scheduled_entries_fired={fired['count']} "
                        f"errors={len(fired['errors'])}"
                    )
            except Exception as exc:
                print(f"[ERROR] schedule firing loop: {exc}")
                traceback.print_exc()

            # 1b) LangGraph wait resumes — fired olan resume_after_wait
            #     entry'lerini runtime'a aktar.
            try:
                resumed_n = _handle_wait_resumes()
                if resumed_n:
                    print(f"[METRIC] wait_resumes_processed={resumed_n}")
            except Exception as exc:
                print(f"[ERROR] wait resume loop: {exc}")
                traceback.print_exc()

            # 1c) Ghost recovery — process kill'den kalan trace'siz
            #     status='running' satırlarını failed'a çek.
            try:
                cleaned = _cleanup_ghost_executions()
                if cleaned:
                    print(f"[CLEANUP] {cleaned} ghost execution temizlendi")
            except Exception as exc:
                print(f"[ERROR] ghost cleanup loop: {exc}")
                traceback.print_exc()

            workflows = get_pending_workflows()
            ran = 0

            for workflow in workflows:
                if not should_run(workflow):
                    continue

                try:
                    execute_workflow(workflow)
                    ran += 1
                except Exception as e:
                    print(
                        f"\n[ERROR] workflow #{workflow['id']} "
                        f"failed: {e}"
                    )
                    traceback.print_exc()
                    continue

            poll_ms = int((time.monotonic() - poll_started) * 1000)
            print(
                f"[METRIC] workflow_poll_ms={poll_ms} "
                f"pending={len(workflows)} executed={ran}"
            )

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\nStopped.")
            break

        except Exception as e:
            print(f"\n[ERROR] workflow worker loop: {e}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
