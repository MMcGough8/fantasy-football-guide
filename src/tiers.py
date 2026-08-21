"""Tiering by optimal 1-D clustering of projected points within the draftable pool.

A fixed points gap never fires past the top of a position (median gap inside
the draftable pool is 1 to 2 points), so tiers are found by clustering instead.
"""

MIN_CLUSTERS = 3
PLAYERS_PER_TIER = 5


def _segment_sse(prefix, prefix_sq, i, j):
    """Sum of squared deviations of values[i:j] from their mean."""
    n = j - i
    total = prefix[j] - prefix[i]
    total_sq = prefix_sq[j] - prefix_sq[i]
    return total_sq - total * total / n


def cluster_tiers(values, k):
    """Tier labels (1 = best) for values sorted descending: the exact minimum
    within-tier variance split into at most k contiguous groups (Jenks natural
    breaks by dynamic programming). Deterministic."""
    n = len(values)
    if n == 0:
        return []
    k = max(1, min(k, n))
    prefix, prefix_sq = [0.0], [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
        prefix_sq.append(prefix_sq[-1] + v * v)

    inf = float("inf")
    # cost[c][j]: best cost splitting the first j values into c groups; split[c][j]: start of last group
    cost = [[inf] * (n + 1) for _ in range(k + 1)]
    split = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0.0
    for c in range(1, k + 1):
        for j in range(c, n + 1):
            for i in range(c - 1, j):
                candidate = cost[c - 1][i] + _segment_sse(prefix, prefix_sq, i, j)
                if candidate < cost[c][j]:
                    cost[c][j] = candidate
                    split[c][j] = i

    labels = [0] * n
    j = n
    for c in range(k, 0, -1):
        i = split[c][j]
        for idx in range(i, j):
            labels[idx] = c
        j = i
    # Relabel contiguously from 1 in case fewer than k groups were used
    seen, out = {}, []
    for label in labels:
        if label not in seen:
            seen[label] = len(seen) + 1
        out.append(seen[label])
    return out


def tiers_for_position(players, pool_size, key="points", label="tier"):
    """Assign tiers in place to `players` (sorted best first). Players beyond the
    draftable pool all share one trailing tier."""
    pool = players[:pool_size]
    k = max(MIN_CLUSTERS, round(len(pool) / PLAYERS_PER_TIER))
    labels = cluster_tiers([p[key] for p in pool], k)
    for p, t in zip(pool, labels):
        p[label] = t
    trailing = (labels[-1] if labels else 0) + 1
    for p in players[pool_size:]:
        p[label] = trailing
    return players
