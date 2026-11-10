"""A bounded, read-only projection of Hermes Desktop's learning graph."""

from pathlib import Path

from .host import HostError, Json

MAX_NODES = 48


def read_memory_graph(home: Path) -> Json:
    try:
        from agent.learning_graph import build_learning_graph
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    except ImportError as exc:
        raise HostError("graph_unavailable", "This Hermes install does not support the memory graph.", 501) from exc

    token = set_hermes_home_override(home)
    try:
        graph = build_learning_graph()
    finally:
        reset_hermes_home_override(token)

    nodes = [node for node in graph["nodes"] if node["kind"] in ("skill", "memory")]
    nodes.sort(key=lambda node: node.get("timestamp") or 0, reverse=True)
    ids = {node["id"]: str(index) for index, node in enumerate(nodes[:MAX_NODES])}
    return {
        "nodes": [
            {
                "id": ids[node["id"]],
                "kind": node["kind"],
                "label": str(node["label"])[:80] if node["kind"] == "skill" else "Memory " + str(index + 1),
            }
            for index, node in enumerate(nodes[:MAX_NODES])
        ],
        "edges": [
            {"source": ids[edge["source"]], "target": ids[edge["target"]]}
            for edge in graph["edges"] if edge["source"] in ids and edge["target"] in ids
        ],
        "total": len(nodes),
    }
