"""API routers. `routers` is the list app.main includes.

Every module in this package that exposes a `router` is picked up, in name order
so the route table is deterministic. Adding a router means adding a file.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter


def discover_routers() -> list[APIRouter]:
    """Import every module in app.routers and collect their `router` attribute."""
    found: list[APIRouter] = []
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append(router)
    return found


routers: list[APIRouter] = discover_routers()
