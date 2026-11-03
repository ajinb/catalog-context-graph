"""Neo4j as the system of record: write the graph with parameterized MERGEs, read it back.

The agent-facing tools always run on the in-memory graph loaded from Neo4j, so their
traversal semantics are identical whichever backend holds the data. Every statement
here is a constant string with parameters; entity text never becomes query text.
Labels and relationship types are restricted to a fixed allowlist for the same reason.

    pip install "catalog-context-graph[neo4j]"
"""

from __future__ import annotations

from typing import Any

from .model import Edge, Entity, Graph
from .refs import KINDS

_LABEL = {k: k.capitalize() if k != "api" else "API" for k in KINDS}
_REL = {"ownedBy": "OWNED_BY", "partOf": "PART_OF", "dependsOn": "DEPENDS_ON",
        "providesApi": "PROVIDES_API", "consumesApi": "CONSUMES_API",
        "subcomponentOf": "SUBCOMPONENT_OF", "childOf": "CHILD_OF", "memberOf": "MEMBER_OF",
        "calls": "CALLS", "affects": "AFFECTS"}
_REL_BACK = {v: k for k, v in _REL.items()}

CONSTRAINT = "CREATE CONSTRAINT entity_ref IF NOT EXISTS FOR (e:Entity) REQUIRE e.ref IS UNIQUE"
UPSERT_ENTITY = (
    "MERGE (e:Entity {ref: $ref}) "
    "SET e.kind = $kind, e.name = $name, e.title = $title, e.description = $description, "
    "e.owner = $owner, e.system = $system, e.lifecycle = $lifecycle, e.type = $type, "
    "e.annotations = $annotations"
)
# Relationship types cannot be parameters in Cypher, so one fixed statement per type.
UPSERT_EDGE = {
    rel: f"MATCH (a:Entity {{ref: $src}}), (b:Entity {{ref: $dst}}) "
         f"MERGE (a)-[r:{cypher}]->(b) SET r.source = $source"
    for rel, cypher in _REL.items()
}
READ_ENTITIES = "MATCH (e:Entity) RETURN e"
READ_EDGES = "MATCH (a:Entity)-[r]->(b:Entity) RETURN a.ref AS src, type(r) AS rel, b.ref AS dst, r.source AS source"


def _props(e: Entity) -> dict[str, Any]:
    import json

    return {"ref": e.ref, "kind": e.kind, "name": e.name, "title": e.title,
            "description": e.description, "owner": e.owner, "system": e.system,
            "lifecycle": e.lifecycle, "type": e.type, "annotations": json.dumps(e.annotations)}


def write_graph(graph: Graph, session) -> dict[str, int]:
    """Upsert every entity (with its kind label) and relation. Idempotent."""
    session.run(CONSTRAINT)
    for e in graph.entities.values():
        session.run(UPSERT_ENTITY, **_props(e))
        session.run(f"MATCH (e:Entity {{ref: $ref}}) SET e:{_LABEL[e.kind]}", ref=e.ref)
    written = 0
    for edge in sorted(graph.edges):
        if edge.rel in UPSERT_EDGE:
            session.run(UPSERT_EDGE[edge.rel], src=edge.src, dst=edge.dst, source=edge.source)
            written += 1
    return {"entities": len(graph.entities), "relations": written}


def read_graph(session) -> Graph:
    import json

    graph = Graph()
    for record in session.run(READ_ENTITIES):
        n = dict(record["e"])
        graph.add_entity(Entity(
            ref=n["ref"], kind=n["kind"], name=n["name"], title=n.get("title") or "",
            description=n.get("description") or "", owner=n.get("owner"), system=n.get("system"),
            lifecycle=n.get("lifecycle") or "", type=n.get("type") or "",
            annotations=json.loads(n.get("annotations") or "{}"),
        ))
    for record in session.run(READ_EDGES):
        rel = _REL_BACK.get(record["rel"])
        if rel:
            graph.add_edge(Edge(record["src"], rel, record["dst"], record["source"] or "catalog"))
    return graph


def connect(uri: str, user: str, password: str):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=(user, password))
