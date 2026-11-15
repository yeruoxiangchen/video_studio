"""Apply only confirmed temporal constraints while preserving the draft's other choices."""

from graphlib import CycleError, TopologicalSorter
from heapq import heappop, heappush
from typing import Any


def backward_candidates(reviews: list[dict[str, Any]], draft: list[str]) -> list[tuple[str, str]]:
    ranks = {name: i for i, name in enumerate(draft)}
    candidates = set()
    for review in reviews:
        for edge in review["constraints"]:
            before, after = edge["earlier_block"], edge["later_block"]
            if before not in ranks or after not in ranks:
                raise ValueError("Narrative review names an unknown block")
            if (
                edge["certainty"] == "clear"
                and ranks[before] > ranks[after]
                and len(str(edge.get("visible_evidence", "")).strip()) >= 8
            ):
                candidates.add((before, after))
    return sorted(candidates, key=lambda e: (ranks[e[0]] - ranks[e[1]], ranks[e[0]]))[:3]


def confirmed_pair_order(reviews: list[dict[str, Any]]) -> tuple[str, str] | None:
    if len(reviews) != 2:
        return None
    preferences = []
    for review in reviews:
        order, value = review["order"], review["result"]
        if (
            len(order) != 2
            or len(set(order)) != 2
            or value.get("certainty") != "clear"
            or value.get("likely_earlier") not in {"A", "B"}
            or len(str(value.get("observed_causal_link", "")).strip()) < 8
        ):
            return None
        index = 0 if value["likely_earlier"] == "A" else 1
        preferences.append((order[index], order[1 - index]))
    return preferences[0] if preferences[0] == preferences[1] else None


def stable_block_order(draft: list[str], precedence: list[tuple[str, str]]) -> list[str]:
    ranks = {name: i for i, name in enumerate(draft)}
    if len(ranks) != len(draft):
        raise ValueError("Draft repeats a narrative block")
    dependencies: dict[str, set[str]] = {name: set() for name in draft}
    for before, after in precedence:
        if before not in ranks or after not in ranks:
            raise ValueError("Temporal constraint names an unknown block")
        dependencies[after].add(before)
    graph = TopologicalSorter(dependencies)
    try:
        graph.prepare()
    except CycleError as error:
        raise ValueError("Confirmed narrative constraints contain a cycle") from error
    ready: list[tuple[int, str]] = []
    for name in graph.get_ready():
        heappush(ready, (ranks[name], name))
    result = []
    while ready:
        _, name = heappop(ready)
        result.append(name)
        graph.done(name)
        for available in graph.get_ready():
            heappush(ready, (ranks[available], available))
    return result
