"""Load Backstage `catalog-info.yaml` files (multi-document YAML) into a Graph.

Reads the relations Backstage itself derives from `spec`: owner, system, domain,
dependsOn, providesApis, consumesApis, subcomponentOf, parent, memberOf. `Location`
entities are skipped; their targets should be passed as paths instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .model import Edge, Entity, Graph
from .refs import RefError, parse_ref

SKIP_KINDS = {"location", "template"}


def _ref(value: str, default_kind: str, ns: str) -> str | None:
    try:
        return parse_ref(value, default_kind=default_kind, default_namespace=ns)
    except RefError:
        return None


def _entity(doc: dict[str, Any]) -> tuple[Entity, list[Edge]] | None:
    kind = str(doc.get("kind", "")).lower()
    meta = doc.get("metadata") or {}
    spec = doc.get("spec") or {}
    if kind in SKIP_KINDS or not meta.get("name"):
        return None
    ns = str(meta.get("namespace", "default")).lower()
    ref = parse_ref(f"{kind}:{ns}/{meta['name']}")
    entity = Entity(
        ref=ref,
        kind=kind,
        name=str(meta["name"]).lower(),
        title=str(meta.get("title", "")),
        description=str(meta.get("description", "")),
        lifecycle=str(spec.get("lifecycle", "")),
        type=str(spec.get("type", "")),
        annotations={str(k): str(v) for k, v in (meta.get("annotations") or {}).items()},
        raw=doc,
    )
    edges: list[Edge] = []

    def link(rel: str, value: Any, default_kind: str) -> None:
        values = value if isinstance(value, list) else [value]
        for v in values:
            if not v:
                continue
            target = _ref(str(v), default_kind, ns)
            if target:
                edges.append(Edge(ref, rel, target))

    if spec.get("owner"):
        entity.owner = _ref(str(spec["owner"]), "group", ns)
        link("ownedBy", spec["owner"], "group")
    if spec.get("system"):
        entity.system = _ref(str(spec["system"]), "system", ns)
        link("partOf", spec["system"], "system")
    if spec.get("domain"):
        link("partOf", spec["domain"], "domain")
    link("dependsOn", spec.get("dependsOn"), "component")
    link("providesApi", spec.get("providesApis"), "api")
    link("consumesApi", spec.get("consumesApis"), "api")
    link("subcomponentOf", spec.get("subcomponentOf"), "component")
    link("childOf", spec.get("parent"), "group")
    link("memberOf", spec.get("memberOf"), "group")
    return entity, edges


def load_catalog(paths: list[Path], graph: Graph | None = None) -> Graph:
    graph = graph or Graph()
    files: list[Path] = []
    for p in paths:
        files += sorted(p.rglob("*.yaml")) + sorted(p.rglob("*.yml")) if p.is_dir() else [p]
    pending: list[Edge] = []
    for f in files:
        for doc in yaml.safe_load_all(f.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or "apiVersion" not in doc:
                continue
            parsed = _entity(doc)
            if parsed:
                entity, edges = parsed
                graph.add_entity(entity)
                pending += edges
    for edge in pending:
        graph.add_edge(edge)
    return graph
