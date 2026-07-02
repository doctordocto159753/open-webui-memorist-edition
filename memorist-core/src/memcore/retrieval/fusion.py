from collections import defaultdict
from typing import Any


def reciprocal_rank_fusion(
    generator_results: list[list[dict[str, Any]]],
    weights: dict[str, float] | None = None,
    k: int = 60,
    limit: int = 50,
) -> list[dict[str, Any]]:
    weights = weights or {}
    fused: dict[str, dict[str, Any]] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    traces: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for results in generator_results:
        for result in results:
            version_uuid = str(result["memory_version_uuid"])
            generator_type = str(result["generator_type"])
            rank = int(result.get("rank") or result.get("generator_rank") or 1)
            weight = weights.get(generator_type, 1.0)
            scores[version_uuid] += weight / (k + rank)
            traces[version_uuid].append(
                {"generator_type": generator_type, "rank": rank, "weight": weight}
            )
            fused.setdefault(version_uuid, result)

    ranked = []
    for version_uuid, score in scores.items():
        item = dict(fused[version_uuid])
        item["fused_score"] = score
        item["score_trace"] = traces[version_uuid]
        ranked.append(item)
    return sorted(
        ranked,
        key=lambda item: (-float(item["fused_score"]), item["memory_version_uuid"]),
    )[:limit]
