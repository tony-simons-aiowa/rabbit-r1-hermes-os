import sys
from contextvars import ContextVar
from types import ModuleType

from hermes_r1.bridge.memory_graph import read_memory_graph


def test_graph_scopes_hermes_and_projects_only_device_metadata(monkeypatch, tmp_path):
    active_home = ContextVar("active_home", default=None)
    constants = ModuleType("hermes_constants")
    constants.set_hermes_home_override = active_home.set
    constants.reset_hermes_home_override = active_home.reset
    graph_module = ModuleType("agent.learning_graph")

    def build_graph():
        assert active_home.get() == tmp_path
        return {
            "nodes": [
                {"id": "private-fingerprint", "label": "Private memory text", "kind": "memory", "timestamp": 2},
                {"id": "test-skill", "label": "Test skill", "kind": "skill", "timestamp": 1},
            ],
            "edges": [{"source": "private-fingerprint", "target": "test-skill"}],
            "memory": [{"body": "Private memory text"}],
        }

    graph_module.build_learning_graph = build_graph
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setitem(sys.modules, "agent.learning_graph", graph_module)

    assert read_memory_graph(tmp_path) == {
        "nodes": [
            {"id": "0", "label": "Memory 1", "kind": "memory"},
            {"id": "1", "label": "Test skill", "kind": "skill"},
        ],
        "edges": [{"source": "0", "target": "1"}],
        "total": 2,
    }
    assert active_home.get() is None
