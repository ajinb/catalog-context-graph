"""Serve the bounded graph tools over MCP. Every tool is annotated read-only and idempotent."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .model import Graph
from .tools import ContextTools

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


def build_server(graph: Graph) -> MCPServer:
    tools = ContextTools(graph)
    server = MCPServer(
        "catalog-context-graph",
        instructions=(
            "Operational context from the software catalog. Refs look like component:checkout-service, "
            "resource:orders-db, api:payments-api, group:team-payments. Use search() when unsure of a "
            "ref. blast_radius() answers 'what breaks / who do we page if X fails'; owners() answers "
            "'who owns X'; change_risk() scores a planned change."
        ),
    )
    server.tool(annotations=READ_ONLY)(tools.search)
    server.tool(annotations=READ_ONLY)(tools.entity)
    server.tool(annotations=READ_ONLY)(tools.owners)
    server.tool(annotations=READ_ONLY)(tools.dependencies)
    server.tool(annotations=READ_ONLY)(tools.blast_radius)
    server.tool(annotations=READ_ONLY)(tools.path)
    server.tool(annotations=READ_ONLY)(tools.change_risk)
    server.tool(annotations=READ_ONLY)(tools.catalog_drift)
    return server
