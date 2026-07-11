from __future__ import annotations

from pathlib import Path

SURFACES = Path("open-webui-integration/memorist/ui/surfaces.ts")
PROCESSING_NODES = Path("open-webui-integration/memorist/ui/processingNodes.ts")
IMPORT_WORKFLOW = Path("open-webui-integration/memorist/ui/importWorkflow.ts")


def test_processing_nodes_component_is_registered_and_settings_mounted() -> None:
    surfaces = SURFACES.read_text(encoding="utf-8")
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert 'import "./processingNodes";' in surfaces
    assert (
        'import { MEMORIST_PROCESSING_NODES_ROUTE } from "./processingNodes";'
        in surfaces
    )
    assert 'label: "Processing Nodes"' in surfaces
    assert "href: MEMORIST_SETTINGS_ROUTES.processingNodes" in surfaces
    assert "path: MEMORIST_SETTINGS_ROUTES.processingNodes" in surfaces
    assert 'element: "memorist-processing-nodes-settings"' in surfaces
    assert "document.createElement(route.element)" in surfaces
    assert 'customElements.define("memorist-processing-nodes-settings"' in component


def test_import_workflow_is_registered_and_settings_mounted() -> None:
    surfaces = SURFACES.read_text(encoding="utf-8")
    component = IMPORT_WORKFLOW.read_text(encoding="utf-8")

    assert 'import "./importWorkflow";' in surfaces
    assert 'label: "Import"' in surfaces
    assert "href: MEMORIST_SETTINGS_ROUTES.import" in surfaces
    assert "path: MEMORIST_SETTINGS_ROUTES.import" in surfaces
    assert "path: MEMORIST_SETTINGS_ROUTES.importRun" in surfaces
    assert 'element: "memorist-import-workflow"' in surfaces
    assert "path.startsWith(`${MEMORIST_SETTINGS_ROUTES.import}/`)" in surfaces
    assert 'customElements.define("memorist-import-workflow"' in component


def test_import_workflow_uses_full_reconstruction_sequence() -> None:
    component = IMPORT_WORKFLOW.read_text(encoding="utf-8")

    assert 'value="full_memory_reconstruction" selected' in component
    assert "/imports/${encodeURIComponent(this.runUuid)}/inspect" in component
    assert "/imports/${encodeURIComponent(this.runUuid)}/reconstruct" in component
    assert "/imports/${encodeURIComponent(this.runUuid)}/dry-run" in component
    assert "/imports/${encodeURIComponent(this.runUuid)}/dry-run-report" in component
    assert "/imports/${encodeURIComponent(this.runUuid)}/commit" in component
    assert "processing_mode: mode" in component
    assert (
        'new Set(["fully_reconstructed", "completed_with_failures", "cancelled", "failed"])'
        in component
    )


def test_processing_nodes_exposes_model_capability_controls() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "supports_json_mode" in component
    assert "supports_structured_output" in component


def test_processing_nodes_blocks_unacknowledged_remote_role_defaults() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "privacy_acknowledged_at" in component
    assert "endpoint_is_local === false" in component
    assert "Privacy acknowledgement required" in component
    assert 'privacyAckRequired || disabledProfile ? "disabled"' in component
    assert "requiresPrivacyAcknowledgement(profile)" in component
    assert "setState({ error: PRIVACY_ACK_REQUIRED_ERROR })" in component
    assert (
        "setModelControlDefault({ role, model_profile_uuid: modelProfileUuid }"
        in component
    )
    assert component.index(
        "setState({ error: PRIVACY_ACK_REQUIRED_ERROR })"
    ) < component.index(
        "setModelControlDefault({ role, model_profile_uuid: modelProfileUuid }"
    )


def test_processing_nodes_keeps_main_chat_and_non_nodes_out_of_processing_selectors() -> (
    None
):
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "function isProcessingNodeProfile" in component
    assert 'profile.role !== "main_chat_observed"' in component
    assert "this.state.profiles.filter(isProcessingNodeProfile)" in component
    assert "MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES.map" in component
    assert "MAIN_CHAT_OBSERVED_NOTE" in component


def test_processing_nodes_does_not_clear_existing_secret_reference_on_edit() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "Enter an environment variable name only, never a raw API key." in component
    assert "Leave blank to keep the existing configured secret reference." in component
    assert (
        "if (this.state.editingProfileUuid && !payload.secret_env_var_name)"
        in component
    )
    assert "delete payload.secret_strategy" in component
    assert "delete payload.secret_env_var_name" in component


def test_processing_nodes_blocks_disabled_profile_defaults() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "Profile disabled" in component
    assert "DISABLED_PROFILE_DEFAULT_ERROR" in component
    assert "profile?.is_enabled === false" in component
