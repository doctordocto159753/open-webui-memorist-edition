from __future__ import annotations

from pathlib import Path

SURFACES = Path("open-webui-integration/memorist/ui/surfaces.ts")
PROCESSING_NODES = Path("open-webui-integration/memorist/ui/processingNodes.ts")


def test_processing_nodes_component_is_registered_and_settings_mounted() -> None:
    surfaces = SURFACES.read_text(encoding="utf-8")
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert 'import "./processingNodes";' in surfaces
    assert 'import { MEMORIST_PROCESSING_NODES_ROUTE } from "./processingNodes";' in surfaces
    assert 'label: "Processing Nodes"' in surfaces
    assert 'href: MEMORIST_SETTINGS_ROUTES.processingNodes' in surfaces
    assert 'path: MEMORIST_SETTINGS_ROUTES.processingNodes' in surfaces
    assert 'element: "memorist-processing-nodes-settings"' in surfaces
    assert "document.createElement(route.element)" in surfaces
    assert 'customElements.define("memorist-processing-nodes-settings"' in component


def test_processing_nodes_exposes_model_capability_controls() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert "supports_json_mode" in component
    assert "supports_structured_output" in component


def test_processing_nodes_blocks_unacknowledged_remote_role_defaults() -> None:
    component = PROCESSING_NODES.read_text(encoding="utf-8")

    assert 'privacy_acknowledged_at' in component
    assert 'endpoint_is_local === false' in component
    assert 'Privacy acknowledgement required' in component
    assert 'privacyAckRequired ? "disabled"' in component
    assert 'requiresPrivacyAcknowledgement(profile)' in component
    assert 'setState({ error: PRIVACY_ACK_REQUIRED_ERROR })' in component
    assert 'setModelControlDefault({ role, model_profile_uuid: modelProfileUuid }' in component
    assert component.index('setState({ error: PRIVACY_ACK_REQUIRED_ERROR })') < component.index('setModelControlDefault({ role, model_profile_uuid: modelProfileUuid }')
