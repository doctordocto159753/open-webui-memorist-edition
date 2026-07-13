from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "memorist-core/tests/test_pr4b_scope_closure.py"
text = path.read_text(encoding="utf-8")
old = '''        model_control.set_default(
            ModelRole.MEMORY_EXTRACTION,
            foreign_project_profile,
            workspace_uuid=workspace_b,
            project_uuid=project_b,
        )
    project_key = f"capture-{uuid4().hex}"
'''
new = '''        model_control.set_default(
            ModelRole.MEMORY_EXTRACTION,
            foreign_project_profile,
            workspace_uuid=workspace_b,
            project_uuid=project_b,
        )
        connection.commit()
    project_key = f"capture-{uuid4().hex}"
'''
if text.count(old) != 1:
    raise RuntimeError("Full scoped-default transaction block changed unexpectedly")
path.write_text(text.replace(old, new), encoding="utf-8")
print("PR4-B Full scope fixture transaction aligned")
