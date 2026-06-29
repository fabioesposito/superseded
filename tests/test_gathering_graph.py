from __future__ import annotations

from pathlib import Path


def test_gather_context_graph_false_uses_rg(monkeypatch):
    """When graph=False, the rg path (retrieve_usages) is used."""
    import superseded.context.gathering as g

    rg_called = {"yes": False}
    graph_called = {"yes": False}

    monkeypatch.setattr(
        g, "retrieve_usages", lambda diff, root: rg_called.__setitem__("yes", True) or "rg-result"
    )
    monkeypatch.setattr(
        g.graph_retrieval,
        "retrieve_usages_via_graph",
        lambda diff, root, **kw: graph_called.__setitem__("yes", True) or "graph-result",
    )
    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: True)
    monkeypatch.setattr(g.graph_retrieval, "ensure_graph_fresh", lambda root: None)
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=False)
    assert rg_called["yes"] is True
    assert graph_called["yes"] is False
    assert result["usage_signals"] == "rg-result"


def test_gather_context_graph_true_available_uses_graph(monkeypatch):
    """When graph=True and CRG is available, the graph path is used and the
    graph is refreshed first."""
    import superseded.context.gathering as g

    events: list[str] = []
    refresh_called = {"yes": False}

    monkeypatch.setattr(g, "retrieve_usages", lambda diff, root: events.append("rg") or "rg-result")
    monkeypatch.setattr(
        g.graph_retrieval,
        "retrieve_usages_via_graph",
        lambda diff, root, **kw: events.append("graph") or "graph-result",
    )

    def fake_refresh(root):
        refresh_called["yes"] = True
        events.append("refresh")

    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: True)
    monkeypatch.setattr(g.graph_retrieval, "ensure_graph_fresh", fake_refresh)
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=True)
    assert refresh_called["yes"] is True
    assert events.index("refresh") < events.index("graph")
    assert result["usage_signals"] == "graph-result"


def test_gather_context_graph_true_unavailable_falls_back(monkeypatch):
    """When graph=True but CRG unavailable, the rg path is used and refresh is
    NOT called."""
    import superseded.context.gathering as g

    refresh_called = {"yes": False}

    monkeypatch.setattr(g, "retrieve_usages", lambda diff, root: "rg-result")
    monkeypatch.setattr(
        g.graph_retrieval,
        "retrieve_usages_via_graph",
        lambda diff, root, **kw: "graph-result",
    )
    monkeypatch.setattr(g.graph_retrieval, "is_available", lambda root: False)
    monkeypatch.setattr(
        g.graph_retrieval,
        "ensure_graph_fresh",
        lambda root: refresh_called.__setitem__("yes", True),
    )
    monkeypatch.setattr(g, "compute_file_context", lambda diff, root=None: "fc")
    monkeypatch.setattr(g, "run_static_analysis", lambda files, root: None)
    monkeypatch.setattr(g, "discover_conventions", lambda root: None)
    monkeypatch.setattr(g, "discover_repo_specs", lambda diff, root: None)

    result = g.gather_context("diff", Path("/repo"), usage_retrieval=True, graph=True)
    assert refresh_called["yes"] is False
    assert result["usage_signals"] == "rg-result"
