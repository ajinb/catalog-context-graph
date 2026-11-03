"""ccg — build, query and serve a catalog context graph.

    ccg stats      --catalog examples/acme [--service-graph sg.json] [--incidents inc.json]
    ccg query      --catalog examples/acme blast_radius resource:orders-db
    ccg serve      --catalog examples/acme                 # MCP over stdio
    ccg eval       --catalog examples/acme --questions eval/questions.yaml
    ccg neo4j-push --catalog examples/acme --uri bolt://localhost:7687
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .backstage import load_catalog
from .model import Graph
from .refs import RefError
from .telemetry import load_incidents, load_service_graph
from .tools import ContextTools, ToolError

QUERIES = ("search", "entity", "owners", "dependencies", "blast_radius", "path",
           "change_risk", "catalog_drift")


def _build(args: argparse.Namespace) -> Graph:
    if getattr(args, "neo4j_uri", None):
        from .neo4j_backend import connect, read_graph

        with connect(args.neo4j_uri, args.neo4j_user, os.environ.get("NEO4J_PASSWORD", "")) as d, \
                d.session() as s:
            return read_graph(s)
    graph = load_catalog([Path(p) for p in args.catalog])
    if args.service_graph:
        unknown = load_service_graph(Path(args.service_graph), graph)
        if unknown:
            print(f"note: telemetry services not in the catalog: {', '.join(unknown)}", file=sys.stderr)
    if args.incidents:
        load_incidents(Path(args.incidents), graph)
    return graph


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ccg", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def source(sp):
        sp.add_argument("--catalog", nargs="+", default=[], help="catalog-info.yaml files or dirs")
        sp.add_argument("--service-graph", help="observed service-graph edges (JSON)")
        sp.add_argument("--incidents", help="incident list (JSON)")
        sp.add_argument("--neo4j-uri", help="read the graph from Neo4j instead of files")
        sp.add_argument("--neo4j-user", default="neo4j")

    source(sub.add_parser("stats", help="entity and relation counts"))
    q = sub.add_parser("query", help="run one graph tool and print JSON")
    source(q)
    q.add_argument("tool", choices=QUERIES)
    q.add_argument("args", nargs="*")
    source(sub.add_parser("serve", help="serve the tools over MCP"))
    e = sub.add_parser("eval", help="retrieval-coverage evaluation")
    source(e)
    e.add_argument("--questions", required=True)
    e.add_argument("--json", action="store_true")
    n = sub.add_parser("neo4j-push", help="write the graph to Neo4j (NEO4J_PASSWORD env)")
    source(n)
    n.add_argument("--uri", required=True)
    n.add_argument("--user", default="neo4j")

    args = p.parse_args(argv)
    try:
        graph = _build(args)
        if args.cmd == "stats":
            print(json.dumps(graph.stats(), indent=2))
        elif args.cmd == "query":
            fn = getattr(ContextTools(graph), args.tool)
            try:
                result = fn(*args.args)
            except TypeError as err:  # wrong number of arguments for this tool
                raise ToolError(f"{args.tool}: {err}") from err
            print(json.dumps(result, indent=2))
        elif args.cmd == "serve":
            from .mcp_server import build_server

            build_server(graph).run("stdio")
        elif args.cmd == "eval":
            from .evaluate import format_report, run

            report = run(graph, Path(args.questions))
            print(json.dumps(report, indent=2, default=list) if args.json else format_report(report))
        elif args.cmd == "neo4j-push":
            from .neo4j_backend import connect, write_graph

            with connect(args.uri, args.user, os.environ.get("NEO4J_PASSWORD", "")) as d, d.session() as s:
                print(json.dumps(write_graph(graph, s)))
    except (ToolError, RefError, FileNotFoundError) as err:
        print(f"ccg: {err}", file=sys.stderr)
        return 1
    return 0


def entrypoint() -> None:
    sys.exit(main())
