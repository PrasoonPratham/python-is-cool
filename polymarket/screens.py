"""The individual edge screens. Each returns executable, fee-adjusted results.

Implication/ladder screens live in ladders.py -- establishing that one market
entails another needs more care than a threshold comparison, so it gets its own
module."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import (load, market_ctx, tradable, buy_cost, max_profitable_size,
                    fee_per_share, annualize, levels)

MIN_PROFIT_USD = 5.0


def best(side_levels):
    return side_levels[0] if side_levels else None


# ---------------------------------------------------------------- screen 1
def binary_yes_no_arb(ctxs):
    """Buy YES and NO on the same market. One dollar is paid out no matter what,
    so any all-in cost below $1 is locked-in profit."""
    hits = []
    for c in ctxs:
        s = c["sides"]
        if set(s) != {"Yes", "No"}:
            continue
        ya, na = s["Yes"]["asks"], s["No"]["asks"]
        if not ya or not na:
            continue
        if ya[0][0] + na[0][0] >= 1.0:
            continue  # cheap pre-filter before the depth walk
        size, profit = max_profitable_size([(ya, c["sched"]), (na, c["sched"])], 1.0)
        if profit < MIN_PROFIT_USD:
            continue
        cost = buy_cost(ya, size, c["sched"]) + buy_cost(na, size, c["sched"])
        hits.append({
            "screen": "binary_yes_no_arb", "question": c["question"], "slug": c["slug"],
            "yes_ask": ya[0][0], "no_ask": na[0][0], "raw_sum": ya[0][0] + na[0][0],
            "shares": round(size, 2), "capital": round(cost, 2),
            "profit_usd": round(profit, 2),
            "return_pct": round(100 * profit / cost, 3) if cost else 0,
            "days": c["days"], "feeType": c["feeType"], "volume24hr": c["volume24hr"],
        })
    return sorted(hits, key=lambda h: -h["profit_usd"])


# ---------------------------------------------------------------- screen 2
def negrisk_arb(events, books):
    """In a neg-risk event exactly one outcome resolves YES, enforced on-chain.

    Buying every YES costs sum(asks) and pays exactly $1.
    Buying every NO costs sum(asks) and pays exactly $(n-1).
    """
    hits = []
    for ev in events:
        if not ev.get("negRisk"):
            continue
        ms = [market_ctx(m, books) for m in (ev.get("markets") or [])]
        ms = [c for c in ms if tradable(c) and set(c["sides"]) == {"Yes", "No"}]
        n = len(ms)
        if n < 2 or n != len(ev.get("markets") or []):
            continue  # need the complete, still-tradable outcome set
        for side, payout, tag in (("Yes", 1.0, "buy_all_yes"), ("No", float(n - 1), "buy_all_no")):
            legs = [(c["sides"][side]["asks"], c["sched"]) for c in ms]
            if any(not a for a, _ in legs):
                continue
            if sum(a[0][0] for a, _ in legs) >= payout:
                continue
            size, profit = max_profitable_size(legs, payout)
            if profit < MIN_PROFIT_USD:
                continue
            cost = sum(buy_cost(a, size, s) for a, s in legs)
            hits.append({
                "screen": f"negrisk_{tag}", "event": ev.get("title"), "slug": ev.get("slug"),
                "outcomes": n, "raw_sum": round(sum(a[0][0] for a, _ in legs), 4),
                "payout": payout, "shares": round(size, 2), "capital": round(cost, 2),
                "profit_usd": round(profit, 2),
                "return_pct": round(100 * profit / cost, 3) if cost else 0,
                "volume24hr": sum(c["volume24hr"] for c in ms),
                "legs": [c["question"] for c in ms],
            })
    return sorted(hits, key=lambda h: -h["profit_usd"])


# ---------------------------------------------------------------- screen 3
def near_certainty_yield(ctxs, min_price=0.80, max_days=400, min_depth_usd=200):
    """Positions priced as near-certain, ranked by annualized return.

    Not risk-free -- this is where the market's residual doubt gets paid out.
    Mechanical ranking only; the tail risk needs judgement, not arithmetic.
    """
    hits = []
    for c in ctxs:
        if c["days"] is None or c["days"] <= 0.5 or c["days"] > max_days:
            continue
        for out, side in c["sides"].items():
            asks = side["asks"]
            if not asks:
                continue
            p = asks[0][0]
            if p < min_price or p >= 0.999:
                continue
            depth = sum(pr * sz for pr, sz in asks if pr <= min(p + 0.01, 0.999))
            if depth < min_depth_usd:
                continue
            shares = depth / p
            cost = buy_cost(asks, shares, c["sched"])
            if cost is None or cost <= 0:
                continue
            all_in = cost / shares
            apy = annualize(all_in, c["days"])
            if apy is None or apy <= 0:
                continue
            hits.append({
                "screen": "near_certainty_yield", "question": c["question"],
                "slug": c["slug"], "outcome": out, "ask": p,
                "all_in_price": round(all_in, 4),
                "gross_return_pct": round(100 * (1 - all_in) / all_in, 2),
                "days": round(c["days"], 1), "apy_pct": round(100 * apy, 1),
                "depth_usd": round(depth, 0), "volume24hr": c["volume24hr"],
                "volume": c["volume"], "feeType": c["feeType"],
                "one_day_move": c["oneDayPriceChange"],
            })
    return sorted(hits, key=lambda h: -h["apy_pct"])


# ---------------------------------------------------------------- screen 4
def maker_arb(ctxs, tick=0.001):
    """Makers pay zero fees. Posting both legs one tick inside the spread turns
    trades that lose money as a taker into locked-in profit -- at the cost of
    needing both orders to actually get filled."""
    hits = []
    for c in ctxs:
        s = c["sides"]
        if set(s) != {"Yes", "No"}:
            continue
        yb, nb = best(s["Yes"]["bids"]), best(s["No"]["bids"])
        ya, na = best(s["Yes"]["asks"]), best(s["No"]["asks"])
        if not (yb and nb and ya and na):
            continue
        # Post a bid one tick better than the current best on each side.
        py, pn = round(yb[0] + tick, 4), round(nb[0] + tick, 4)
        if py >= ya[0] or pn >= na[0]:
            continue  # would cross and pay taker fees
        edge = 1.0 - (py + pn)
        if edge <= 0.002:
            continue
        hits.append({
            "screen": "maker_arb", "question": c["question"], "slug": c["slug"],
            "post_yes_at": py, "post_no_at": pn, "combined_cost": round(py + pn, 4),
            "edge_per_share": round(edge, 4), "return_pct": round(100 * edge / (py + pn), 2),
            "yes_spread": round(ya[0] - yb[0], 4), "no_spread": round(na[0] - nb[0], 4),
            "days": c["days"], "volume24hr": c["volume24hr"], "liquidity": c["liquidity"],
        })
    return sorted(hits, key=lambda h: -h["edge_per_share"])


def main():
    events, books = load()
    ctxs = []
    for ev in events:
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if tradable(c):
                ctxs.append(c)
    print(f"tradable markets: {len(ctxs)}", file=sys.stderr)

    out = {
        "binary_yes_no_arb": binary_yes_no_arb(ctxs),
        "negrisk_arb": negrisk_arb(events, books),
        "near_certainty_yield": near_certainty_yield(ctxs),
        "maker_arb": maker_arb(ctxs),
    }
    for k, v in out.items():
        print(f"  {k}: {len(v)} hits", file=sys.stderr)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("wrote data/results.json", file=sys.stderr)


if __name__ == "__main__":
    main()
