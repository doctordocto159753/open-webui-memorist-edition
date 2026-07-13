from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "memorist-core/tests/test_pr4b_scope_closure.py"
text = path.read_text(encoding="utf-8")

old = '''        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, 1)",
            (workspace, workspace, now, now),
        )
'''
new = '''        connection.execute(
            "INSERT INTO workspaces (workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT (workspace_uuid) DO NOTHING",
            (workspace, workspace, now, now),
        )
'''
if text.count(old) != 1:
    raise RuntimeError("workspace seed block changed unexpectedly")
text = text.replace(old, new)

old = '''        connection.execute(
            "INSERT INTO projects (project_uuid, workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (project, workspace, project, now, now),
        )
'''
new = '''        connection.execute(
            "INSERT INTO projects "
            "(project_uuid, workspace_uuid, name, created_at, updated_at, schema_version) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (project, workspace, project, now, now),
        )
'''
if text.count(old) != 1:
    raise RuntimeError("project seed block changed unexpectedly")
text = text.replace(old, new)

old = '''    session_workspace_b = _seed_session(
        settings,
        user=user_b,
        conversation_id=f"chat-workspace-b-{uuid4().hex}",
        workspace=workspace_b,
        project=project_b,
    )
'''
new = '''    session_workspace_b = _seed_session(
        settings,
        user=user_b,
        conversation_id=f"chat-workspace-b-{uuid4().hex}",
        workspace=workspace_b,
        project=project_b,
    )
    workspace_c = f"workspace-c-{uuid4().hex}"
    project_c = f"project-c-{uuid4().hex}"
    user_c = f"user-c-{uuid4().hex}"
    session_global = _seed_session(
        settings,
        user=user_c,
        conversation_id=f"chat-global-{uuid4().hex}",
        workspace=workspace_c,
        project=project_c,
    )
'''
if text.count(old) != 1:
    raise RuntimeError("workspace B seed block changed unexpectedly")
text = text.replace(old, new)

old = '''    foreign_capture = _capture(
        client,
        seeded=session_workspace_b,
        content="foreign project content",
        key=f"capture-{uuid4().hex}",
    )
    project_payload = _job_payload(settings, str(project_capture["message_uuid"]))
    workspace_payload = _job_payload(settings, str(workspace_capture["message_uuid"]))
    foreign_payload = _job_payload(settings, str(foreign_capture["message_uuid"]))
'''
new = '''    foreign_capture = _capture(
        client,
        seeded=session_workspace_b,
        content="foreign project content",
        key=f"capture-{uuid4().hex}",
    )
    global_capture = _capture(
        client,
        seeded=session_global,
        content="global fallback content",
        key=f"capture-{uuid4().hex}",
    )
    project_payload = _job_payload(settings, str(project_capture["message_uuid"]))
    workspace_payload = _job_payload(settings, str(workspace_capture["message_uuid"]))
    foreign_payload = _job_payload(settings, str(foreign_capture["message_uuid"]))
    global_payload = _job_payload(settings, str(global_capture["message_uuid"]))
'''
if text.count(old) != 1:
    raise RuntimeError("capture matrix block changed unexpectedly")
text = text.replace(old, new)

old = '''    assert foreign_payload["model_profile_uuid"] == foreign_project_profile
    assert project_payload["model_profile_uuid"] != foreign_project_profile
'''
new = '''    assert foreign_payload["model_profile_uuid"] == foreign_project_profile
    assert global_payload["model_profile_uuid"] == global_profile
    assert global_payload["workspace_uuid"] == workspace_c
    assert global_payload["project_uuid"] == project_c
    assert project_payload["model_profile_uuid"] != foreign_project_profile
'''
if text.count(old) != 1:
    raise RuntimeError("scope assertions changed unexpectedly")
text = text.replace(old, new)

old = '''    assert job_count == 1
    assert usage_count == 1
'''
new = '''    assert job_count == 1
    assert usage_count == 1

    assistant = client.post(
        "/memcore/assistant-response/completed",
        json={
            "input_message_uuid": project_capture["message_uuid"],
            "assistant_text": "assistant scoped content",
            "provider_response_id": f"provider-{uuid4().hex}",
            "turn_policy": "no_recall",
            "user_uuid": user_a,
            "workspace_uuid": workspace_a,
        },
        headers=_headers(user_a, workspace_a),
    )
    assert assistant.status_code == 200, assistant.text
    assistant_payload = _job_payload(
        settings, str(assistant.json()["assistant_message_uuid"])
    )
    assert assistant_payload["model_profile_uuid"] == project_a_profile
    assert assistant_payload["workspace_uuid"] == workspace_a
    assert assistant_payload["project_uuid"] == project_a
'''
if text.count(old) != 1:
    raise RuntimeError("duplicate assertions changed unexpectedly")
text = text.replace(old, new)
path.write_text(text, encoding="utf-8")

backend_path = root / "open-webui-integration/memorist/tests/test_backend_proxy.py"
backend = backend_path.read_text(encoding="utf-8")
old = '''        policy_call = next(call for call in FakeCoreClient.calls if call.get("operation") == "policy")
'''
new = '''        policy_call = next(
            call for call in FakeCoreClient.calls if call.get("operation") == "policy"
        )
'''
if backend.count(old) != 1:
    raise RuntimeError("backend policy-call assertion changed unexpectedly")
backend_path.write_text(backend.replace(old, new), encoding="utf-8")

print("PR4-B scope closure post-patch applied")
