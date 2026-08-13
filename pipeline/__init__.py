"""Pipeline of script blocks (Jenkins-like) for the Slave-ops panel.

Two independent halves:

- ``store`` — pure data + DAG logic, no subprocess/HTTP, unit-testable.
- ``runner`` — the stateful execution engine (subprocesses, venv, scheduler).

``backend.py`` wires the HTTP API on top of both.
"""

from .store import (
    Store,
    CycleError,
    ValidationError,
    STATUSES,
    FAILED_STATUSES,
    ACTIVE_STATUSES,
    new_block,
    slugify,
    topo_sort,
    topo_levels,
    dependents,
    descendants,
)

__all__ = [
    "Store",
    "CycleError",
    "ValidationError",
    "STATUSES",
    "FAILED_STATUSES",
    "ACTIVE_STATUSES",
    "new_block",
    "slugify",
    "topo_sort",
    "topo_levels",
    "dependents",
    "descendants",
]
