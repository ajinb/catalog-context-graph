import pytest

from catalog_context_graph.refs import RefError, parse_ref, short
from catalog_context_graph.tools import ToolError


def test_refs_normalize_and_reject_injection():
    assert parse_ref("Component:Checkout-Service") == "component:default/checkout-service"
    assert parse_ref("orders-db", default_kind="resource") == "resource:default/orders-db"
    assert short("component:default/x") == "component:x"
    for bad in ["x'}) DETACH DELETE n //", "component:a b", "spaceship:x", "", "a/b/c"]:
        with pytest.raises(RefError):
            parse_ref(bad, default_kind="component")


def test_catalog_relations_are_loaded(graph):
    stats = graph.stats()
    assert stats["entities"]["component"] == 11 and stats["entities"]["api"] == 4
    assert stats["relations"]["providesApi"] == 4
    checkout = graph.get("component:default/checkout-service")
    assert checkout.owner == "group:default/team-checkout"
    assert checkout.system == "system:default/storefront" and checkout.tier == 1


def test_blast_radius_follows_dependsOn_and_api_consumption(tools):
    br = tools.blast_radius("resource:orders-db")
    impacted = {r["ref"]: r["distance"] for r in br["impacted"]}
    assert impacted == {"component:checkout-service": 1, "component:order-worker": 1,
                        "component:reporting-job": 1, "component:web-frontend": 2}
    assert br["teams_to_notify"] == ["group:team-checkout", "group:team-data", "group:team-web"]
    assert "consumesApi api:checkout-api" in next(
        r["path"] for r in br["impacted"] if r["ref"] == "component:web-frontend")


def test_dependencies_resolve_api_providers(tools):
    deps = {r["ref"] for r in tools.dependencies("web-frontend", depth=4)}
    assert {"component:checkout-service", "component:payments-service", "component:feature-store",
            "resource:orders-db", "resource:sessions-cache"} <= deps


def test_depth_and_result_caps(graph):
    from catalog_context_graph import ContextTools

    capped = ContextTools(graph, max_depth=1, max_results=2)
    br = capped.blast_radius("resource:orders-db", depth=99)
    assert all(r["distance"] == 1 for r in br["impacted"])
    assert len(br["impacted"]) == 2 and br["truncated"] is True


def test_owners_walk_the_group_hierarchy(tools):
    o = tools.owners("payments-service")
    assert o["escalation_chain"] == ["group:team-payments", "group:commerce-org"]
    assert o["members"] == ["user:ken"] and o["pagerduty_service"] == "PPAY01"


def test_path_and_bare_name_resolution(tools):
    p = tools.path("web-frontend", "warehouse")
    assert p["connected"] and p["path"].endswith("resource:warehouse")
    assert not tools.path("warehouse", "web-frontend")["connected"]
    with pytest.raises(ToolError, match="not in the catalog"):
        tools.entity("component:does-not-exist")


def test_drift_flags_undeclared_and_unobserved(full_tools):
    drift = full_tools.catalog_drift()
    assert drift["undeclared_calls"] == ["component:checkout-service -> component:fraud-scoring",
                                         "component:order-worker -> component:payments-service"]
    assert drift["unobserved_dependencies"] == ["component:order-worker -> component:email-service"]


def test_drift_is_empty_without_telemetry(tools):
    assert tools.catalog_drift() == {"undeclared_calls": [], "unobserved_dependencies": []}


def test_change_risk_accounts_for_state_incidents_and_drift(full_tools):
    db = full_tools.change_risk("resource:payments-db")
    assert "stateful database" in db["factors"] and "1 past incident(s)" in db["factors"]
    fraud = full_tools.change_risk("component:fraud-scoring")
    assert any("undeclared runtime call" in f for f in fraud["factors"])
    assert full_tools.change_risk("component:email-service")["level"] in {"low", "medium"}


def test_search(tools):
    assert tools.search("fraud")[0]["ref"] == "component:fraud-scoring"
    with pytest.raises(ToolError):
        tools.search("  ")
