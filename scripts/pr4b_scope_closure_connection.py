from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "memorist-core/src/memcore/api/routes_openwebui.py"
text = path.read_text(encoding="utf-8")
old = '''    identity = resolve_scoped_model_identity(PostgresCompatConnection(connection), session_uuid)
    model_identity = identity.as_payload()
'''
new = '''    compat_connection = (
        connection
        if isinstance(connection, PostgresCompatConnection)
        else PostgresCompatConnection(connection)
    )
    identity = resolve_scoped_model_identity(compat_connection, session_uuid)
    model_identity = identity.as_payload()
'''
if text.count(old) != 1:
    raise RuntimeError("PostgreSQL scheduling connection block changed unexpectedly")
path.write_text(text.replace(old, new), encoding="utf-8")
print("PR4-B scheduling connection adapter normalized")
