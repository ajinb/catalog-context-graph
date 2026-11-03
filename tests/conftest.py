from pathlib import Path

import pytest

from catalog_context_graph import ContextTools, load_catalog
from catalog_context_graph.telemetry import load_incidents, load_service_graph

ACME = Path(__file__).resolve().parent.parent / "examples" / "acme"


@pytest.fixture
def graph():
    return load_catalog([ACME / "catalog-info.yaml"])


@pytest.fixture
def full_graph():
    g = load_catalog([ACME / "catalog-info.yaml"])
    load_service_graph(ACME / "service-graph.json", g)
    load_incidents(ACME / "incidents.json", g)
    return g


@pytest.fixture
def tools(graph):
    return ContextTools(graph)


@pytest.fixture
def full_tools(full_graph):
    return ContextTools(full_graph)
