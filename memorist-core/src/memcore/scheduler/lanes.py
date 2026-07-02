from enum import StrEnum


class SchedulerLane(StrEnum):
    CRITICAL_PRIVACY = "critical_privacy"
    LIVE_CHAT_CAPTURE = "live_chat_capture"
    PREFLIGHT_PERSIST = "preflight_persist"
    ASSISTANT_CAPTURE = "assistant_capture"
    MEMORY_EXTRACTION = "memory_extraction"
    IMPORT_COMMIT = "import_commit"
    IMPORT_RECONSTRUCTION = "import_reconstruction"
    GRAPH_PROJECTION = "graph_projection"
    EMBEDDING_INDEX = "embedding_index"
    BLOCK_REBUILD = "block_rebuild"
    MAINTENANCE = "maintenance"


LANE_PRIORITIES: dict[SchedulerLane, int] = {
    SchedulerLane.CRITICAL_PRIVACY: 110,
    SchedulerLane.LIVE_CHAT_CAPTURE: 100,
    SchedulerLane.PREFLIGHT_PERSIST: 95,
    SchedulerLane.ASSISTANT_CAPTURE: 90,
    SchedulerLane.MEMORY_EXTRACTION: 60,
    SchedulerLane.IMPORT_COMMIT: 40,
    SchedulerLane.IMPORT_RECONSTRUCTION: 30,
    SchedulerLane.GRAPH_PROJECTION: 25,
    SchedulerLane.EMBEDDING_INDEX: 25,
    SchedulerLane.BLOCK_REBUILD: 20,
    SchedulerLane.MAINTENANCE: 10,
}

LOW_PRIORITY_LANES = {
    SchedulerLane.IMPORT_COMMIT,
    SchedulerLane.IMPORT_RECONSTRUCTION,
    SchedulerLane.GRAPH_PROJECTION,
    SchedulerLane.EMBEDDING_INDEX,
    SchedulerLane.BLOCK_REBUILD,
    SchedulerLane.MAINTENANCE,
}

HOT_LANES = {
    SchedulerLane.CRITICAL_PRIVACY,
    SchedulerLane.LIVE_CHAT_CAPTURE,
    SchedulerLane.PREFLIGHT_PERSIST,
    SchedulerLane.ASSISTANT_CAPTURE,
}
