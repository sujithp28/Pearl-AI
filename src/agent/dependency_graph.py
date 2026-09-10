"""
Dependency graph construction and topological ordering for Pearl plans.

Given a list of ToolCall objects with optional `step_id` and
`depends_on` fields, produces a deterministic execution order using
Kahn's BFS-based topological sort.

Steps without `step_id` or `depends_on` are treated as independent
nodes; they appear in their original relative order among steps that
become "ready" at the same depth level.

Callers must run validate_dependencies() before calling
topological_sort() — this module trusts that IDs are unique, all
references are valid, and there are no self-dependencies.

This module has no side effects and imports only from the standard
library and src.llm.parser.
"""

from __future__ import annotations

from collections import deque

from src.llm.parser import ToolCall


class DependencyCycleError(ValueError):
    """
    Raised when a dependency cycle is detected during topological sort.

    Subclasses ValueError so callers that catch ValueError (e.g.
    PlanValidationError, which also subclasses ValueError) can handle
    it correctly without additional changes.
    """


def topological_sort(steps: list[ToolCall]) -> list[ToolCall]:
    """
    Return a topologically sorted copy of `steps`.

    Steps with no `step_id` and no `depends_on` carry no named edges.
    They are treated as independent nodes and appear in their original
    relative order among steps that become ready simultaneously.

    When multiple steps are ready (in-degree 0) at the same time, they
    are processed in ascending order of their original position, keeping
    the sort deterministic.

    If the plan has no dependency annotations at all, the original order
    is returned as-is (fast path — O(n) scan, no graph work).

    Raises
    ------
    DependencyCycleError
        When a dependency cycle is detected (Kahn's algorithm produces
        fewer nodes than the input). validate_dependencies() should have
        caught this before topological_sort() is called — this is the
        defensive layer.
    """

    # Fast path: no annotations anywhere — preserve original order exactly.
    if not any(s.step_id is not None or s.depends_on for s in steps):
        return list(steps)

    n = len(steps)

    # Map from step_id → original position index.
    # Steps without a step_id cannot be named as dependencies, so they
    # are not added to this mapping.
    id_to_index: dict[str, int] = {}
    for i, step in enumerate(steps):
        if step.step_id is not None:
            id_to_index[step.step_id] = i

    # Build per-node in-degree counts and successor lists.
    # successors[i] = indices of steps that list step i as a dependency,
    # i.e. steps that must execute *after* step i.
    in_degree: list[int] = [0] * n
    successors: list[list[int]] = [[] for _ in range(n)]

    for i, step in enumerate(steps):
        for dep_id in step.depends_on:
            # An unknown reference is skipped rather than looked up blindly.
            #
            # This used to index directly, on the stated assumption that the
            # validator had already checked every reference. It has not:
            # both Planner.plan() and Planner.replan() sort *before*
            # validating, deliberately, so that read-before-write and other
            # ordering rules see execution order. validate_dependencies()
            # also calls this function itself, on unvalidated input, to
            # detect cycles.
            #
            # So a model naming a tool instead of a step_id
            # (depends_on: ["read_file"]) crashed the run with a bare
            # KeyError instead of the clear "references unknown step id"
            # error the validator produces a moment later. Dropping the
            # edge keeps ordering well-defined and lets that validation
            # error be the one the user actually sees.
            dep_index = id_to_index.get(dep_id)
            if dep_index is None:
                continue
            successors[dep_index].append(i)
            in_degree[i] += 1

    # Kahn's algorithm: start with all zero-in-degree nodes, sorted by
    # original position so ties in readiness break deterministically.
    queue: deque[int] = deque(sorted(i for i in range(n) if in_degree[i] == 0))

    result: list[ToolCall] = []

    while queue:
        i = queue.popleft()
        result.append(steps[i])

        # After processing step i, reduce in-degree of everything that
        # depended on it. Collect newly ready successors, sort for
        # determinism, then extend the queue.
        newly_ready: list[int] = []
        for j in successors[i]:
            in_degree[j] -= 1
            if in_degree[j] == 0:
                newly_ready.append(j)

        newly_ready.sort()
        queue.extend(newly_ready)

    if len(result) != n:
        # A cycle exists: Kahn's cannot drain the graph.
        raise DependencyCycleError(
            f"Dependency cycle detected: {len(result)} of {n} step(s) "
            "could be ordered. Check depends_on references for circular "
            "dependencies."
        )

    return result
