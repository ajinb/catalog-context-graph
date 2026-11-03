"""Backstage entity references: `[kind:][namespace/]name`, normalized to `kind:namespace/name`.

See https://backstage.io/docs/features/software-catalog/references. Matching is
case-insensitive in Backstage, so refs are lowercased. Anything that does not match
the grammar is rejected before it reaches a query — the graph tools take refs from a
model, and a ref is never interpolated into a query string.
"""

from __future__ import annotations

import re

KINDS = {"component", "api", "resource", "system", "domain", "group", "user", "incident"}
_PART = r"[a-z0-9][a-z0-9._-]{0,62}"
REF_RE = re.compile(rf"^(?:(?P<kind>[a-z]+):)?(?:(?P<ns>{_PART})/)?(?P<name>{_PART})$")


class RefError(ValueError):
    pass


def parse_ref(ref: str, default_kind: str | None = None, default_namespace: str = "default") -> str:
    m = REF_RE.match(ref.strip().lower())
    if not m:
        raise RefError(f"not a valid entity reference: {ref!r}")
    kind = m.group("kind") or default_kind
    if kind is None:
        raise RefError(f"reference {ref!r} needs a kind, e.g. component:{ref}")
    if kind not in KINDS:
        raise RefError(f"unknown kind {kind!r} in {ref!r}")
    return f"{kind}:{m.group('ns') or default_namespace}/{m.group('name')}"


def kind_of(ref: str) -> str:
    return ref.split(":", 1)[0]


def short(ref: str) -> str:
    """`component:default/checkout` -> `component:checkout` for display."""
    kind, rest = ref.split(":", 1)
    ns, name = rest.split("/", 1)
    return f"{kind}:{name}" if ns == "default" else ref
