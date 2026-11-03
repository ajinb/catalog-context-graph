"""Backstage software catalog -> operational context graph for AI agents, served over MCP."""

from .backstage import load_catalog
from .model import Edge, Entity, Graph
from .tools import ContextTools, ToolError

__all__ = ["ContextTools", "Edge", "Entity", "Graph", "ToolError", "load_catalog"]
