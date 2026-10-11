"""Nested meters: a load can sit behind another meter (its parent).

A garage fed through its own meter is the typical case: the garage meter measures the
incoming cable, and the CEE outlet, a heater and a water heater inside the garage have
meters of their own. Only meters without a parent are subtracted from the main fuse;
a parent's children are subtracted from the parent instead, which leaves the parent's
own "rest" (lighting, sockets) per phase.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol


class _Load(Protocol):
    subentry_id: str
    parent: str | None
    channels: dict[str, str]

    @property
    def phases(self) -> list[str]: ...

    @property
    def estimated(self) -> bool: ...


def effective_parents(loads: list[_Load]) -> dict[str, str | None]:
    """The parent each load is actually counted under; None means directly on the main fuse.

    A parent is ignored (the load counts as a root) when it no longer exists, has no
    meter, does not measure every phase the load uses, or would form a cycle.
    """
    by_id = {load.subentry_id: load for load in loads}
    out: dict[str, str | None] = {}
    for load in loads:
        parent = by_id.get(load.parent or "")
        ok = (
            parent is not None
            and parent.subentry_id != load.subentry_id
            and not parent.estimated
            and set(load.phases) <= set(parent.channels)
        )
        out[load.subentry_id] = parent.subentry_id if ok else None
    for load_id in out:
        seen = {load_id}
        current = out[load_id]
        while current is not None:
            if current in seen:
                out[load_id] = None
                break
            seen.add(current)
            current = out[current]
    return out


def descendants(loads: list[_Load], load_id: str) -> set[str]:
    """Every load below `load_id`, following the configured parents."""
    children: dict[str, list[str]] = defaultdict(list)
    for load in loads:
        if load.parent:
            children[load.parent].append(load.subentry_id)
    found: set[str] = set()
    stack = [load_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found and child != load_id:
                found.add(child)
                stack.append(child)
    return found


def split(
    loads: list[_Load],
    amps: dict[str, dict[str, float | None]],
    parents: dict[str, str | None],
    phases: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, float | None]]]:
    """Metered current per main-fuse phase (roots only) and each parent's rest per phase.

    `amps` holds each metered load's current per phase (None when unavailable).
    Negative readings count as zero. A parent's rest is None on a phase where the
    parent itself is unavailable.
    """
    metered = {p: 0.0 for p in phases}
    children: dict[str, list[str]] = defaultdict(list)
    for load in loads:
        parent = parents.get(load.subentry_id)
        if parent is not None:
            children[parent].append(load.subentry_id)
        elif not load.estimated:
            for p, a in amps.get(load.subentry_id, {}).items():
                if a is not None and p in metered:
                    metered[p] += max(a, 0.0)
    rest: dict[str, dict[str, float | None]] = {}
    for parent_id, kids in children.items():
        own = amps.get(parent_id, {})
        rest[parent_id] = {}
        for p, a in own.items():
            if a is None:
                rest[parent_id][p] = None
                continue
            below = sum(max(amps.get(k, {}).get(p) or 0.0, 0.0) for k in kids)
            rest[parent_id][p] = max(a, 0.0) - below
    return metered, rest
