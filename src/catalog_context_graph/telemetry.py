"""Observed reality to lay over the declared catalog: service-graph edges and incidents.

Service-graph edges can come from the OpenTelemetry Collector `servicegraph` connector
(metric `traces_service_graph_request_total{client,server}`), Tempo or Jaeger. This
module reads the minimal JSON both can be exported to:

    {"edges": [{"client": "checkout-service", "server": "payments-service", "requests": 1200}]}

Service names are matched to components by name, or by the
`opentelemetry.io/service-name` annotation when the two differ.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Edge, Entity, Graph
from .refs import RefError, parse_ref


def _service_index(graph: Graph) -> dict[str, str]:
    index: dict[str, str] = {}
    for e in graph.entities.values():
        if e.kind == "component":
            index[e.name] = e.ref
            alias = e.annotations.get("opentelemetry.io/service-name")
            if alias:
                index[alias.lower()] = e.ref
    return index


def load_service_graph(path: Path, graph: Graph) -> list[str]:
    """Add `calls` edges (source="observed"). Returns service names that matched no component."""
    data = json.loads(path.read_text(encoding="utf-8"))
    index = _service_index(graph)
    unknown: set[str] = set()
    for edge in data.get("edges", []):
        client, server = str(edge["client"]).lower(), str(edge["server"]).lower()
        if client not in index or server not in index:
            unknown.update(s for s in (client, server) if s not in index)
            continue
        if int(edge.get("requests", 1)) > 0:
            graph.add_edge(Edge(index[client], "calls", index[server], source="observed"))
    return sorted(unknown)


def load_incidents(path: Path, graph: Graph) -> None:
    """`[{"id": "INC-42", "title": "...", "date": "2026-09-14", "affects": ["resource:payments-db"]}]`"""
    for inc in json.loads(path.read_text(encoding="utf-8")):
        ref = parse_ref(f"incident:{str(inc['id']).lower()}")
        graph.add_entity(Entity(ref=ref, kind="incident", name=str(inc["id"]).lower(),
                                title=inc.get("title", ""), description=inc.get("date", "")))
        for target in inc.get("affects", []):
            try:
                graph.add_edge(Edge(ref, "affects", parse_ref(target, default_kind="component"),
                                    source="incident"))
            except RefError:
                continue
