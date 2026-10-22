from difflib import SequenceMatcher

from .parser import norm


def match_nodes(old_nodes, new_nodes):
    """Return {new_node_id: old_node_id} using an explainable two-step match."""
    old_by_key = {node.logical_key: node for node in old_nodes}
    matches = {}
    used = set()

    for new in new_nodes:
        old = old_by_key.get(new.logical_key)
        if old is not None:
            matches[new.id] = old.id
            used.add(old.id)

    for new in new_nodes:
        if new.id in matches:
            continue
        new_parent = new.logical_key.rsplit("/", 1)[0]
        candidates = [
            old
            for old in old_nodes
            if old.id not in used
            and old.level == new.level
            and old.logical_key.rsplit("/", 1)[0] == new_parent
        ]
        scored = sorted(
            (
                SequenceMatcher(None, norm(new.heading), norm(old.heading)).ratio(),
                old,
            )
            for old in candidates
        )
        if scored:
            scored.reverse()
            best_score, best = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0
            if best_score >= 0.88 and best_score - second_score >= 0.08:
                matches[new.id] = best.id
                used.add(best.id)

    return matches
