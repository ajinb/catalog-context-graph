"""Neo4j statements are constants with parameters; the MCP surface is read-only."""

import json
import os

import pytest
from mcp import Client

from catalog_context_graph import neo4j_backend as nb
from catalog_context_graph.mcp_server import build_server


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        return []


def test_write_graph_never_interpolates_entity_text(graph):
    session = FakeSession()
    counts = nb.write_graph(graph, session)
    assert counts["entities"] == len(graph.entities)
    names = {e.name for e in graph.entities.values()}
    for query, params in session.calls:
        for name in names:
            assert f"'{name}'" not in query and f'"{name}"' not in query
        if query.startswith("MERGE (e:Entity"):
            assert params["ref"].startswith(params["kind"] + ":")
    labels = {q.split("SET e:")[1] for q, _ in session.calls if "SET e:" in q}
    assert labels <= set(nb._LABEL.values())


def test_round_trip_through_neo4j_shaped_records(graph):
    session = FakeSession()
    nb.write_graph(graph, session)
    entities = [{"e": {**p, "annotations": p["annotations"]}} for q, p in session.calls
                if q.startswith("MERGE (e:Entity")]
    edges = [{"src": p["src"], "rel": q.split("[r:")[1].split("]")[0], "dst": p["dst"],
              "source": p["source"]} for q, p in session.calls if "MERGE (a)-[r:" in q]

    class ReadSession:
        def run(self, query, **_):
            return entities if query == nb.READ_ENTITIES else edges

    back = nb.read_graph(ReadSession())
    assert set(back.entities) == set(graph.entities)
    assert back.edges == graph.edges


@pytest.mark.skipif(not os.environ.get("NEO4J_URI"), reason="set NEO4J_URI to run against Neo4j")
def test_live_neo4j_round_trip(graph):
    with nb.connect(os.environ["NEO4J_URI"], "neo4j", os.environ.get("NEO4J_PASSWORD", "")) as d, \
            d.session() as s:
        s.run("MATCH (n:Entity) DETACH DELETE n")
        nb.write_graph(graph, s)
        back = nb.read_graph(s)
    assert set(back.entities) == set(graph.entities) and back.edges == graph.edges


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_tools_are_read_only_and_answer(graph):
    async with Client(build_server(graph)) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} >= {"blast_radius", "owners", "change_risk", "catalog_drift"}
        assert all(t.annotations.read_only_hint and t.annotations.open_world_hint is False for t in tools)
        result = await client.call_tool("blast_radius", {"ref": "resource:orders-db"})
        payload = result.structured_content or json.loads(result.content[0].text)
        payload = payload.get("result", payload)
        assert payload["impacted_count"] == 4
