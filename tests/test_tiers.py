from tiers import cluster_tiers, tiers_for_position


def test_obvious_gaps_become_tier_breaks():
    values = [100, 98, 97, 70, 69, 68, 40, 39, 10]
    labels = cluster_tiers(values, k=4)
    assert labels == [1, 1, 1, 2, 2, 2, 3, 3, 4]


def test_labels_are_contiguous_and_start_at_one():
    values = [300, 250, 249, 248, 200, 150, 149, 100, 50]
    labels = cluster_tiers(values, k=6)
    assert labels[0] == 1
    assert sorted(set(labels)) == list(range(1, max(labels) + 1))
    assert labels == sorted(labels)  # descending values -> non-decreasing tiers


def test_deterministic():
    values = [float(v) for v in range(120, 0, -3)]
    assert cluster_tiers(values, k=6) == cluster_tiers(values, k=6)


def test_fewer_values_than_clusters():
    assert cluster_tiers([50, 40], k=5) == [1, 2]
    assert cluster_tiers([], k=3) == []
    assert cluster_tiers([7], k=3) == [1]


def test_tiers_for_position_limits_to_the_draftable_pool():
    players = [{"points": 300 - i * 4} for i in range(40)]
    tiers_for_position(players, pool_size=20)
    in_pool = [p["tier"] for p in players[:20]]
    beyond = {p["tier"] for p in players[20:]}
    assert in_pool[0] == 1 and in_pool == sorted(in_pool)
    assert len(beyond) == 1 and beyond.pop() == max(in_pool) + 1
