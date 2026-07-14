from __future__ import annotations

from dataclasses import dataclass

from memcore.models import (
    GateDecisionValue,
    MemorySignalRoute,
    MemorySignalRouteStatus,
    MemorySignalRouteType,
)
from memcore.repositories.memory_worker import (
    GateDecisionRepository,
    JakobsonAnalysisRepository,
    MemorySignalRouteRepository,
)


@dataclass(frozen=True)
class CanonicalRouteReference:
    route_uuid: str
    annotation_uuid: str
    route_type: MemorySignalRouteType
    route_status: MemorySignalRouteStatus
    priority: int


@dataclass(frozen=True)
class CandidateAuthorityContext:
    gate_decision: GateDecisionValue | None
    requires_high_confidence_pass: bool
    selected_route: CanonicalRouteReference | None
    analysis_run_uuid: str | None
    prompt_execution_uuid: str | None


def select_authoritative_route(
    routes: list[CanonicalRouteReference],
) -> CanonicalRouteReference | None:
    """Select one deterministic canonical route for candidate construction."""

    if not routes:
        return None
    ordered = sorted(
        routes,
        key=lambda route: (
            route.route_status is not MemorySignalRouteStatus.READY,
            route.route_type is MemorySignalRouteType.IGNORE,
            -route.priority,
            route.route_uuid,
        ),
    )
    return ordered[0]


def route_reference(route: MemorySignalRoute) -> CanonicalRouteReference:
    return CanonicalRouteReference(
        route_uuid=route.route_uuid,
        annotation_uuid=route.annotation_uuid,
        route_type=route.route_type,
        route_status=route.status,
        priority=route.priority,
    )


class LiteCandidateAuthorityResolver:
    """Resolve persisted Jakobson/gate/route authority for one Lite unit."""

    def __init__(self, connection: object) -> None:
        self.gates = GateDecisionRepository(connection)  # type: ignore[arg-type]
        self.jakobson = JakobsonAnalysisRepository(connection)  # type: ignore[arg-type]
        self.routes = MemorySignalRouteRepository(connection)  # type: ignore[arg-type]

    def resolve(
        self,
        *,
        text_unit_uuid: str,
        processing_run_uuid: str,
        analysis_run_uuid: str,
    ) -> CandidateAuthorityContext:
        gate = next(
            (
                decision
                for decision in self.gates.list_decisions_for_unit(text_unit_uuid)
                if decision.processing_run_uuid == processing_run_uuid
            ),
            None,
        )
        annotations = self.jakobson.list_annotations_for_run(analysis_run_uuid)
        annotation = next(
            (item for item in annotations if item.unit_uuid == text_unit_uuid),
            None,
        )
        routes = (
            self.routes.list_for_annotation(annotation.annotation_uuid)
            if annotation is not None
            else []
        )
        run = self.jakobson.get_run(analysis_run_uuid)
        return CandidateAuthorityContext(
            gate_decision=gate.decision if gate is not None else None,
            requires_high_confidence_pass=bool(
                gate.requires_high_confidence_pass if gate is not None else False
            ),
            selected_route=select_authoritative_route([route_reference(item) for item in routes]),
            analysis_run_uuid=analysis_run_uuid if run is not None else None,
            prompt_execution_uuid=run.prompt_execution_uuid if run is not None else None,
        )
