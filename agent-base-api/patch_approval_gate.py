#!/usr/bin/env python3
"""
apply_approved_proposal → LangGraph resume_execution fix.

Sorun:
  UI'dan onay verilince /approvals/{id}/approve → apply_approved_proposal çağrılıyor.
  Ama apply_approved_proposal LangGraph resume_execution'ı HİÇ çağırmıyor.
  proposal_json içinde execution_id ve thread_id var, bunlar okunmuyor.
  rule_executions.302 sonsuza kadar waiting_human kalıyor.
  scheduled_entries'e takvim kaydı düşmüyor.

Çözüm:
  apply_approved_proposal sonuna LangGraph resume bloğu ekle:
    proposal_json["task_payload"]["execution_id"] → resume_execution(...)

Kullanım:
  python3 patch_planner_runtime.py
"""

import sys
import json
import shutil
from pathlib import Path

BASE = Path("/home/bypasa10/Desktop/rule-based-engine/agent-base/agent-base-api")
TARGET = BASE / "planner_runtime.py"

if not TARGET.exists():
    TARGET = Path(__file__).parent / "planner_runtime.py"

if not TARGET.exists():
    print(f"ERROR: planner_runtime.py bulunamadı: {TARGET}")
    sys.exit(1)

print(f"Hedef: {TARGET}")

content = TARGET.read_text(encoding="utf-8")

# ── Mevcut fonksiyonu bul ─────────────────────────────────────────────────
if "def apply_approved_proposal" not in content:
    print("ERROR: apply_approved_proposal fonksiyonu bulunamadı.")
    sys.exit(1)

# ── apply_approved_proposal'ın sonunu bul ────────────────────────────────
# Fonksiyonun return satırını yakala ve arkasına resume bloğunu ekle.
# Fonksiyonun son return'ü genellikle {"success": True, ...} şeklinde.
# Farklı yapılar için birden fazla pattern dene.

RESUME_BLOCK = '''
    # ── LangGraph resume (structured rule execution) ──────────────────────
    # proposal_json.task_payload içinde execution_id varsa
    # waiting_human execution'ı resume et.
    try:
        _task = (proposal or {}).get("task_payload") or {}
        _exec_id = _task.get("execution_id")
        if _exec_id:
            from langgraph_engine.runtime import get_execution, resume_execution
            _row = get_execution(int(_exec_id))
            if _row and _row.get("status") == "waiting_human":
                resume_execution(
                    int(_exec_id),
                    approval_decision="approved",
                    decided_by="dashboard_user",
                )
    except Exception as _resume_exc:
        print(f"[APPROVAL] LangGraph resume skipped: {_resume_exc}")
    # ─────────────────────────────────────────────────────────────────────
'''

# Zaten eklenmiş mi?
if "LangGraph resume (structured rule execution)" in content:
    print("Patch zaten uygulanmış, atlanıyor.")
    sys.exit(0)

# Pattern 1: return {"success": True ile biten fonksiyon
PATTERNS = [
    # En yaygın: fonksiyon return {"success": True, ...} ile bitiyor
    '    return {"success": True, "approval_id": approval_id}',
    '    return {"success": True, "approval_id": approval_id, "proposal": proposal}',
    '    return {"success": True}',
    '    return result',
    '    return {"approved": True}',
]

patched = False
for pattern in PATTERNS:
    if content.count(pattern) >= 1:
        # apply_approved_proposal fonksiyonunun içindeki ilk eşleşmeyi bul
        func_start = content.find("def apply_approved_proposal")
        func_content_after = content[func_start:]

        # Bir sonraki def'e kadar olan bölümde ara
        next_def = func_content_after.find("\ndef ", 1)
        func_body = func_content_after[:next_def] if next_def > 0 else func_content_after

        if pattern not in func_body:
            continue

        # Sadece fonksiyon içindeki ilk eşleşmeyi değiştir
        new_func_body = func_body.replace(
            pattern,
            RESUME_BLOCK + pattern,
            1
        )
        content = content[:func_start] + new_func_body + (content[func_start + len(func_content_after):] if next_def < 0 else content[func_start + next_def:])
        patched = True
        print(f"Patch uygulandı (pattern: {pattern[:60]}...)")
        break

if not patched:
    # Fallback: fonksiyonun son satırını bul ve önüne ekle
    func_start = content.find("def apply_approved_proposal")
    func_content_after = content[func_start:]
    next_def_pos = func_content_after.find("\ndef ", 1)

    if next_def_pos > 0:
        func_body = func_content_after[:next_def_pos]
    else:
        func_body = func_content_after

    # Fonksiyondaki son return satırını bul
    lines = func_body.split("\n")
    last_return_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("return ") or stripped == "return":
            last_return_idx = i

    if last_return_idx is not None:
        lines.insert(last_return_idx, RESUME_BLOCK)
        new_func_body = "\n".join(lines)
        if next_def_pos > 0:
            content = content[:func_start] + new_func_body + content[func_start + next_def_pos:]
        else:
            content = content[:func_start] + new_func_body
        patched = True
        print("Patch uygulandı (fallback: son return öncesi)")
    else:
        # Son çare: fonksiyonun sonuna ekle
        insert_pos = func_start + (next_def_pos if next_def_pos > 0 else len(func_content_after))
        content = content[:insert_pos] + RESUME_BLOCK + "\n" + content[insert_pos:]
        patched = True
        print("Patch uygulandı (son çare: fonksiyon sonu)")

# ── Yedek + yaz ──────────────────────────────────────────────────────────
backup = TARGET.with_suffix(".py.bak")
shutil.copy2(TARGET, backup)
TARGET.write_text(content, encoding="utf-8")
print(f"Dosya güncellendi: {TARGET}")
print(f"Yedek: {backup}")

# ── Syntax kontrol ────────────────────────────────────────────────────────
import subprocess
result = subprocess.run(
    ["python3", "-m", "py_compile", str(TARGET)],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"SYNTAX HATASI: {result.stderr}")
    print("Yedekten geri yükleniyor...")
    shutil.copy2(backup, TARGET)
    sys.exit(1)
print("Syntax kontrolü: OK")

# ── Manuel resume testi ───────────────────────────────────────────────────
print()
print("Şimdi execution 302'yi manuel resume et:")
print("""
  cd /home/bypasa10/Desktop/rule-based-engine/agent-base/agent-base-api
  uv run python3 -c "
import sys; sys.path.insert(0, '.')
from langgraph_engine.runtime import resume_execution
result = resume_execution(302, approval_decision='approved', decided_by='manual_fix')
print('status:', result['status'])
print('current_node:', result.get('current_node'))
"
""")
print("Sonra kontrol:")
print('  sqlite3 listener.db "SELECT id, status, current_node, ended_at FROM rule_executions WHERE id=302;"')
print('  sqlite3 listener.db "SELECT id, kind, status, title FROM scheduled_entries ORDER BY id DESC LIMIT 3;"')