# catalog-context-graph

**Turn a Backstage software catalog into an operational context graph that AI agents query over MCP.**

An on-call agent asked *"orders-db is down — what breaks and who do we page?"* needs a
dependency walk, not a document search. `catalog-context-graph` loads your `catalog-info.yaml`
files (plus optional OpenTelemetry service-graph edges and incident history) into a typed graph
and exposes eight bounded, read-only tools over the Model Context Protocol: blast radius,
ownership and escalation, dependencies, paths, change risk, and catalog drift. Neo4j is an
optional system of record.

[![CI](https://github.com/ajinb/catalog-context-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/ajinb/catalog-context-graph/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

```
catalog-info.yaml ─┐
OTel service graph ┼─▶ typed graph ─▶ bounded tools ─▶ MCP (stdio / streamable HTTP) ─▶ agent
incident history ──┘        │
                            └─▶ Neo4j (optional: system of record, Bloom/Browser exploration)
```

## Why a graph, measured

The usual alternative is "RAG over the catalog": embed or index each entity's YAML and
retrieve the top-k for the question. The repository ships a deterministic evaluation of that
baseline on the example catalog (`ccg eval`): 16 operational questions, ground truth computed
from declared relations, and BM25 top-k retrieval scored by recall. The recall measure is
deliberately generous to the baseline — an answer entity counts as covered if its *name
appears anywhere* in the retrieved text, whether or not the chain connecting it to the
question is there.

| BM25 top-k | recall (upper bound) | strict recall | questions fully covered | recall at 1 / 2 / 3 / 4 hops |
|---|---|---|---|---|
| k=3 | 0.40 | 0.14 | 1/16 | 1.00 / 0.31 / 0.37 / 0.50 |
| k=5 | 0.56 | 0.23 | 3/16 | 1.00 / 0.42 / 0.57 / 0.50 |
| k=10 | 0.66 | 0.33 | 4/16 | 1.00 / 0.69 / 0.64 / 0.50 |
| graph tools | 1.00 | 1.00 | 16/16 | exact at every depth |

One-hop questions are easy for retrieval. Everything past the first hop — *which teams* to
page, *which* storefront component fails when a database two layers down does — is where flat
retrieval loses the answer, even before a model has to reason over it. Full output:
[`eval/results-acme.json`](eval/results-acme.json). Limitations are below; the questions are in
[`eval/questions.yaml`](eval/questions.yaml).

## Quick start

```bash
pip install "catalog-context-graph[mcp] @ git+https://github.com/ajinb/catalog-context-graph@main"

ccg stats --catalog path/to/catalog/                     # entity and relation counts
ccg query --catalog path/to/catalog/ blast_radius resource:orders-db
ccg query --catalog path/to/catalog/ owners checkout-service
ccg query --catalog catalog/ --service-graph sg.json catalog_drift
ccg serve --catalog path/to/catalog/                     # MCP over stdio
```

Try it on the bundled example (44 entities: 4 systems, 8 teams, 11 components, 8 resources, 4 APIs):

```bash
ccg query --catalog examples/acme/catalog-info.yaml \
  --service-graph examples/acme/service-graph.json \
  --incidents examples/acme/incidents.json catalog_drift
```

```json
{
  "undeclared_calls": [
    "component:checkout-service -> component:fraud-scoring",
    "component:order-worker -> component:payments-service"
  ],
  "unobserved_dependencies": ["component:order-worker -> component:email-service"]
}
```

### MCP client configuration

```json
{
  "mcpServers": {
    "catalog": {"command": "ccg", "args": ["serve", "--catalog", "/path/to/catalog"]}
  }
}
```

## Tools

| Tool | Answers |
|---|---|
| `search(query)` | "what is the ref for the payments thing?" |
| `entity(ref)` | kind, owner, system, lifecycle, tier, runbook, direct relations |
| `owners(ref)` | owning team, escalation chain up the group hierarchy, members, PagerDuty service |
| `dependencies(ref, depth)` | what it needs, transitively — `dependsOn`, the providers behind consumed APIs, observed calls |
| `blast_radius(ref, depth)` | what breaks if it fails, by distance with the path, plus impacted systems and teams to notify |
| `path(src, dst)` | the shortest dependency chain between two entities |
| `change_risk(ref)` | a 0-100 score with its factors: fan-in, production lifecycle, tier, statefulness, undeclared calls, past incidents |
| `catalog_drift()` | runtime calls the catalog does not declare, and declared dependencies telemetry never saw |

Every tool is annotated `readOnlyHint` and closed-world, is depth- and size-capped, and takes
entity references — never query text.

## Security model

- **No model-written queries.** There is no "run this Cypher" tool. Agents get eight bounded
  questions; a prompt-injected agent can at most ask one of them about a different entity.
- **Refs are validated** against the Backstage reference grammar before any lookup; anything else
  is rejected.
- **The Neo4j backend uses constant, parameterized statements.** Entity text is only ever a
  parameter value; labels and relationship types come from a fixed allowlist. Tests assert both.
- **Read-only by construction.** The server holds the graph in memory and has no write path.

## Inputs

- **Backstage catalog** — `catalog-info.yaml` files or directories (multi-document YAML). Reads
  `owner`, `system`, `domain`, `dependsOn`, `providesApis`, `consumesApis`, `subcomponentOf`,
  group `parent` and user `memberOf`. Annotations used: `cloudandsre.com/tier`,
  `cloudandsre.com/runbook`, `pagerduty.com/service-id`, `opentelemetry.io/service-name`.
- **Service graph** — `{"edges": [{"client", "server", "requests"}]}`, the shape the OpenTelemetry
  Collector `servicegraph` connector, Tempo and Jaeger can be exported to. Adds observed `calls`
  edges; unknown services are reported, not guessed.
- **Incidents** — `[{"id", "title", "date", "affects": [refs]}]`.

## Neo4j

```bash
pip install "catalog-context-graph[neo4j]"
export NEO4J_PASSWORD=...
ccg neo4j-push --catalog catalog/ --service-graph sg.json --uri bolt://localhost:7687
ccg serve --neo4j-uri bolt://localhost:7687        # serve from Neo4j as the system of record
```

Entities become `:Entity` nodes with a kind label (`:Component`, `:Resource`, `:API`…);
relations become `DEPENDS_ON`, `CONSUMES_API`, `CALLS` and so on. The agent-facing tools run on
the graph read back from Neo4j, so they behave identically on either backend.

## Limitations

- The evaluation uses a small hand-built catalog and a lexical (BM25) baseline. Dense-embedding
  retrieval and end-to-end LLM answer accuracy are not measured yet; both are on the roadmap.
- Graph traversal is in memory. That is comfortable for catalogs of thousands of entities; much
  larger catalogs need server-side traversal in Neo4j.
- `change_risk` is a transparent heuristic, not a calibrated model. Its factors are returned so a
  human can disagree with them.

## Roadmap

- Live Backstage catalog API ingestion (in addition to files)
- Dense-retrieval baseline and an LLM answer-accuracy eval mode
- Server-side Cypher traversal for very large catalogs
- Ingest Kubernetes ownership labels and Terraform state as additional edges

## Related

Part of the [cloudandsre.com](https://cloudandsre.com) toolkit:
[`mcp-schema-drift`](https://github.com/ajinb/mcp-schema-drift) (lock the tool surface an agent
sees), [`alert-explainer`](https://github.com/ajinb/alert-explainer),
[`cloudandsre-skills`](https://github.com/ajinb/cloudandsre-skills).

## License

Apache-2.0. See [CITATION.cff](CITATION.cff) to cite this software.
