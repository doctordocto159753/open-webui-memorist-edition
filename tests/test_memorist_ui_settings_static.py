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
