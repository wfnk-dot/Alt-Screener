"""'Most active' score: percentile rank of each metric across the coins that passed, weighted."""

# config key -> function giving the metric (None = n/a)
METRICS = {
    "atr_pct": lambda c: c.atr_pct,
    "volume_ratio": lambda c: c.vol_ratio,
    "oi_change_abs": lambda c: None if c.oi_change_pct is None else abs(c.oi_change_pct),
    "return_7d_abs": lambda c: None if c.return_7d_pct is None else abs(c.return_7d_pct),
    "oi_size": lambda c: c.oi_usd,
}


def percentile_ranks(values):
    """0..1 rank of each value among the others (ties share the average rank).
    n/a values get 0.5 (middle) so they neither help nor hurt."""
    known = sorted(v for v in values if v is not None)
    n = len(known)
    out = []
    for v in values:
        if v is None:
            out.append(0.5)
        elif n == 1:
            out.append(1.0)
        else:
            below = sum(1 for x in known if x < v)
            equal = sum(1 for x in known if x == v)
            avg_rank = below + (equal - 1) / 2  # 0-based
            out.append(avg_rank / (n - 1))
    return out


def score_and_rank(coins, weights):
    """Sets coin.score (0-100) and returns the coins sorted best first."""
    total = sum(weights.values()) or 1
    scores = [0.0] * len(coins)
    for key, weight in weights.items():
        ranks = percentile_ranks([METRICS[key](c) for c in coins])
        for i, r in enumerate(ranks):
            scores[i] += weight * r
    for c, s in zip(coins, scores):
        c.score = s / total * 100
    return sorted(coins, key=lambda c: (-c.score, c.symbol))
