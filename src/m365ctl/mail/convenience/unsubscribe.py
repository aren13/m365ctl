"""RFC 2369 / RFC 8058 ``List-Unsubscribe`` parser + dispatcher.

Pure functions: header string → typed methods. No Graph or HTTP calls inside
this module — the CLI layer fetches the message and decides whether to act.

RFC 2369 grammar:

    List-Unsubscribe: <https://example.com/unsub?u=42>, <mailto:unsub@example.com?subject=remove>

Each angle-bracketed entry is a URI; commas separate alternatives. We
categorise into ``mailto`` and ``https`` (treating ``http``/``https`` the
same — most mailers normalise to ``https``).

RFC 8058 one-click adds a companion header::

    List-Unsubscribe-Post: List-Unsubscribe=One-Click

When present, http(s) methods should be POSTed (with that single
form-encoded field) rather than GETed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class UnsubscribeMethod:
    kind: Literal["mailto", "https"]
    target: str
    one_click: bool = False


def parse_list_unsubscribe(header_value: str) -> list[UnsubscribeMethod]:
    """Parse a ``List-Unsubscribe`` header into typed methods.

    Malformed entries (no angle brackets, unknown scheme) are silently
    discarded. Whitespace around entries is tolerated.
    """
    if not header_value:
        return []
    methods: list[UnsubscribeMethod] = []
    # Split on commas at the top level. RFC 2369 entries are simple
    # angle-bracketed URIs, so a naive comma split is correct in practice.
    for raw in header_value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if not (entry.startswith("<") and entry.endswith(">")):
            continue
        uri = entry[1:-1].strip()
        if not uri:
            continue
        scheme, _, rest = uri.partition(":")
        scheme = scheme.lower()
        if not rest:
            continue
        if scheme == "mailto":
            methods.append(UnsubscribeMethod(kind="mailto", target=uri))
        elif scheme in ("http", "https"):
            # Normalise http→https for display; the actual URL keeps its
            # original scheme so the caller can hit it as-is.
            methods.append(UnsubscribeMethod(kind="https", target=uri))
        # else: unknown scheme — discard.
    return methods


def discover_methods(message: dict[str, Any]) -> list[UnsubscribeMethod]:
    """Pull ``List-Unsubscribe`` (+ optional one-click flag) from a Graph message.

    Expects ``message["internetMessageHeaders"]`` to be a list of
    ``{"name": ..., "value": ...}`` dicts (Graph's representation when
    ``$select=internetMessageHeaders`` is requested).
    """
    headers = message.get("internetMessageHeaders") or []
    list_unsub = ""
    one_click = False
    for h in headers:
        name = (h.get("name") or "").strip().lower()
        value = h.get("value") or ""
        if name == "list-unsubscribe":
            list_unsub = value
        elif name == "list-unsubscribe-post":
            # RFC 8058: case-insensitive "List-Unsubscribe=One-Click".
            if "one-click" in value.strip().lower():
                one_click = True
    methods = parse_list_unsubscribe(list_unsub)
    if one_click:
        methods = [
            UnsubscribeMethod(
                kind=m.kind, target=m.target,
                one_click=(m.kind == "https"),
            )
            for m in methods
        ]
    return methods


__all__ = ["UnsubscribeMethod", "parse_list_unsubscribe", "discover_methods"]
