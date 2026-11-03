"""The graph: typed entities and typed, directed relations.

Edge direction follows Backstage's own relation names, read left to right:
`component:checkout --dependsOn--> resource:orders-db`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Relations an outage propagates *against*: if B fails, every A with A -rel-> B is hit.
IMPACT_RELATIONS = ("dependsOn", "consumesApi", "calls")
# Relations that connect an API to the component behind it (impact flows API -> consumers).
PROVIDES = "providesApi"


@dataclass
class Entity:
    ref: str
    kind: str
    name: str
    title: str = ""
    description: str = ""
    owner: str | None = None
    system: str | None = None
    lifecycle: str = ""
    type: str = ""
    annotations: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def tier(self) -> int | None:
        value = self.annotations.get("cloudandsre.com/tier")
        return int(value) if value and value.isdigit() else None


@dataclass(frozen=True, order=True)
class Edge:
    src: str
    rel: str
    dst: str
    source: str = "catalog"  # "catalog" (declared) or "observed" (telemetry)


class Graph:
    """In-memory property graph. Small catalogs (thousands of entities) fit comfortably."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.edges: set[Edge] = set()
        self._out: dict[str, set[Edge]] = defaultdict(set)
        self._in: dict[str, set[Edge]] = defaultdict(set)

    def add_entity(self, entity: Entity) -> None:
        self.entities[entity.ref] = entity

    def add_edge(self, edge: Edge) -> None:
        if edge in self.edges:
            return
        self.edges.add(edge)
        self._out[edge.src].add(edge)
        self._in[edge.dst].add(edge)

    def out_edges(self, ref: str, rels: tuple[str, ...] | None = None) -> list[Edge]:
        return sorted(e for e in self._out.get(ref, ()) if rels is None or e.rel in rels)

    def in_edges(self, ref: str, rels: tuple[str, ...] | None = None) -> list[Edge]:
        return sorted(e for e in self._in.get(ref, ()) if rels is None or e.rel in rels)

    def get(self, ref: str) -> Entity | None:
        return self.entities.get(ref)

    def __contains__(self, ref: str) -> bool:
        return ref in self.entities

    def stats(self) -> dict[str, Any]:
        kinds: dict[str, int] = defaultdict(int)
        for e in self.entities.values():
            kinds[e.kind] += 1
        rels: dict[str, int] = defaultdict(int)
        for e in self.edges:
            rels[f"{e.rel}" + ("" if e.source == "catalog" else f" ({e.source})")] += 1
        return {"entities": dict(sorted(kinds.items())), "relations": dict(sorted(rels.items()))}
