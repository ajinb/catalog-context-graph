"""The bounded query surface agents get. Every tool is read-only, depth- and size-capped,
and takes entity references, never query text. There is no "run this Cypher" tool:
model-written queries against a production graph are an injection and blast-radius risk
of their own, and these questions do not need one.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .model import IMPACT_RELATIONS, PROVIDES, Graph
from .refs import kind_of, parse_ref, short

STATEFUL_TYPES = {"database", "queue", "cache", "bucket", "storage", "stream", "search-index"}


class ToolError(ValueError):
    pass


class ContextTools:
    def __init__(self, graph: Graph, *, max_depth: int = 4, max_results: int = 50) -> None:
        self.g = graph
        self.max_depth = max_depth
        self.max_results = max_results

    # -- helpers ---------------------------------------------------------------
    def _resolve(self, ref: str) -> str:
        full = parse_ref(ref, default_kind="component")
        if full in self.g:
            return full
        if ":" not in ref:  # a bare name: accept it if exactly one entity of any kind has it
            name = full.split("/", 1)[1]
            matches = [r for r, e in self.g.entities.items() if e.name == name]
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise ToolError(f"{ref!r} is ambiguous: {', '.join(short(m) for m in sorted(matches))}")
        raise ToolError(f"{short(full)} is not in the catalog; use search() to find it")

    def _depth(self, depth: int) -> int:
        return max(1, min(int(depth), self.max_depth))

    def _dependents(self, ref: str) -> list[tuple[str, str]]:
        """Who breaks if `ref` breaks: (dependent, relation)."""
        out = [(e.src, e.rel) for e in self.g.in_edges(ref, IMPACT_RELATIONS)]
        if kind_of(ref) == "component":  # consumers of the APIs this component provides
            for api in self.g.out_edges(ref, (PROVIDES,)):
                out += [(e.src, f"consumesApi {short(api.dst)}") for e in
                        self.g.in_edges(api.dst, ("consumesApi",))]
        return out

    def _dependencies(self, ref: str) -> list[tuple[str, str]]:
        out = []
        for e in self.g.out_edges(ref, IMPACT_RELATIONS):
            if e.rel == "consumesApi":
                providers = [p.src for p in self.g.in_edges(e.dst, (PROVIDES,))]
                out += [(p, f"consumesApi {short(e.dst)}") for p in providers] or [(e.dst, e.rel)]
            else:
                out.append((e.dst, e.rel + (" (observed)" if e.source == "observed" else "")))
        return out

    def _bfs(self, start: str, step, depth: int, arrow: str = "->") -> list[dict[str, Any]]:
        seen = {start: (0, [short(start)])}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            dist, path = seen[node]
            if dist >= depth:
                continue
            for nxt, rel in step(node):
                if nxt not in seen:
                    label = f"<-{rel}-" if arrow == "<-" else f"-{rel}->"
                    seen[nxt] = (dist + 1, [*path, label, short(nxt)])
                    queue.append(nxt)
        rows = [{"ref": short(r), "kind": kind_of(r), "distance": d, "path": " ".join(p)}
                for r, (d, p) in seen.items() if r != start]
        rows.sort(key=lambda r: (r["distance"], r["ref"]))
        return rows

    def _owner_chain(self, group: str | None) -> list[str]:
        chain, current = [], group
        while current and current not in chain and len(chain) < 10:
            chain.append(current)
            parents = self.g.out_edges(current, ("childOf",))
            current = parents[0].dst if parents else None
        return chain

    # -- tools -----------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """Find entity refs by name, title or description (case-insensitive substring)."""
        q = query.lower().strip()
        if not q:
            raise ToolError("query must not be empty")
        hits = [e for e in self.g.entities.values()
                if q in e.name or q in e.title.lower() or q in e.description.lower()]
        hits.sort(key=lambda e: (q != e.name, e.ref))
        return [{"ref": short(e.ref), "kind": e.kind, "title": e.title or e.name}
                for e in hits[: min(limit, self.max_results)]]

    def entity(self, ref: str) -> dict[str, Any]:
        """One entity: kind, owner, system, lifecycle, type, and its direct relations."""
        full = self._resolve(ref)
        e = self.g.get(full)
        return {
            "ref": short(full), "kind": e.kind, "title": e.title or e.name,
            "description": e.description, "owner": short(e.owner) if e.owner else None,
            "system": short(e.system) if e.system else None, "lifecycle": e.lifecycle,
            "type": e.type, "tier": e.tier,
            "relations": [f"{x.rel} {short(x.dst)}" + (" (observed)" if x.source == "observed" else "")
                          for x in self.g.out_edges(full)],
            "runbook": e.annotations.get("cloudandsre.com/runbook"),
        }

    def owners(self, ref: str) -> dict[str, Any]:
        """The owning team, its parent groups, members, and the on-call service if annotated."""
        full = self._resolve(ref)
        e = self.g.get(full)
        chain = self._owner_chain(e.owner) if kind_of(full) != "group" else self._owner_chain(full)
        team = chain[0] if chain else None
        members = sorted(short(x.src) for x in self.g.in_edges(team, ("memberOf",))) if team else []
        return {
            "ref": short(full), "team": short(team) if team else None,
            "escalation_chain": [short(g) for g in chain], "members": members,
            "pagerduty_service": e.annotations.get("pagerduty.com/service-id"),
        }

    def dependencies(self, ref: str, depth: int = 2) -> list[dict[str, Any]]:
        """What `ref` needs to work, transitively (dependsOn, API providers, observed calls)."""
        full = self._resolve(ref)
        return self._bfs(full, self._dependencies, self._depth(depth))[: self.max_results]

    def blast_radius(self, ref: str, depth: int = 3) -> dict[str, Any]:
        """What breaks if `ref` fails: impacted entities by distance, systems, teams to notify."""
        full = self._resolve(ref)
        impacted = self._bfs(full, self._dependents, self._depth(depth), arrow="<-")
        refs = [parse_ref(r["ref"]) for r in impacted]
        systems = sorted({short(self.g.get(r).system) for r in refs
                          if self.g.get(r) and self.g.get(r).system})
        teams = sorted({short(self.g.get(r).owner) for r in [full, *refs]
                        if self.g.get(r) and self.g.get(r).owner})
        return {
            "ref": short(full), "impacted": impacted[: self.max_results],
            "impacted_count": len(impacted), "truncated": len(impacted) > self.max_results,
            "systems": systems, "teams_to_notify": teams,
        }

    def path(self, src: str, dst: str) -> dict[str, Any]:
        """Shortest dependency path from `src` to `dst`, if `src` depends on `dst` at all."""
        a, b = self._resolve(src), self._resolve(dst)
        for row in self._bfs(a, self._dependencies, self.max_depth * 2):
            if row["ref"] == short(b):
                return {"connected": True, "hops": row["distance"], "path": row["path"]}
        return {"connected": False, "hops": None, "path": None}

    def change_risk(self, ref: str) -> dict[str, Any]:
        """Heuristic 0-100 risk of changing `ref`, with the factors that produced it."""
        full = self._resolve(ref)
        e = self.g.get(full)
        factors: list[tuple[str, int]] = []
        radius = self.blast_radius(full, depth=self.max_depth)["impacted_count"]
        if radius:
            factors.append((f"{radius} entities depend on it within {self.max_depth} hops",
                            min(40, radius * 4)))
        if e.lifecycle == "production":
            factors.append(("production lifecycle", 15))
        if e.tier == 1:
            factors.append(("tier 1", 15))
        if e.kind == "resource" and (e.type in STATEFUL_TYPES or any(t in e.name for t in STATEFUL_TYPES)):
            factors.append((f"stateful {e.type or 'resource'}", 15))
        undeclared = [x for x in self.g.in_edges(full, ("calls",)) + self.g.out_edges(full, ("calls",))
                      if not self._declared(x.src, x.dst)]
        if undeclared:
            factors.append((f"{len(undeclared)} undeclared runtime call(s) touch it", 10))
        incidents = self.g.in_edges(full, ("affects",))
        if incidents:
            factors.append((f"{len(incidents)} past incident(s)", min(15, 5 * len(incidents))))
        score = min(100, sum(w for _, w in factors))
        level = "critical" if score >= 70 else "high" if score >= 45 else "medium" if score >= 20 else "low"
        return {"ref": short(full), "score": score, "level": level,
                "factors": [f for f, _ in factors]}

    def _declared(self, a: str, b: str) -> bool:
        return any(dep == b and "observed" not in rel for dep, rel in self._dependencies(a))

    def catalog_drift(self) -> dict[str, list[str]]:
        """Declared vs observed: runtime calls the catalog does not know about, and declared
        component dependencies telemetry never saw. Empty lists when no telemetry is loaded."""
        observed = [e for e in self.g.edges if e.rel == "calls"]
        if not observed:
            return {"undeclared_calls": [], "unobserved_dependencies": []}
        undeclared = sorted(f"{short(e.src)} -> {short(e.dst)}" for e in observed
                            if not self._declared(e.src, e.dst))
        seen = {(e.src, e.dst) for e in observed}
        unobserved = sorted(
            f"{short(a)} -> {short(b)}"
            for a in self.g.entities if kind_of(a) == "component"
            for b, rel in self._dependencies(a)
            if kind_of(b) == "component" and "observed" not in rel and (a, b) not in seen
        )
        return {"undeclared_calls": undeclared, "unobserved_dependencies": unobserved}
