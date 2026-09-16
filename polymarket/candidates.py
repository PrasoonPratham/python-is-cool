"""Build the shortlist worth human/agent research.

The mechanical screens came back empty: there is no risk-free arbitrage. What
remains is positions where the *residual* risk may be smaller than the price
implies. Arithmetic can rank those; only research can judge them.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable, buy_cost, annualize

HERE = os.path.dirname(os.path.abspath(__file__))


def depth_usd(asks, limit):
    return sum(p * s for p, s in asks if p <= limit)


def build(events, books):
    rows = []
    for ev in events:
        tags = [t.get("label") for t in (ev.get("tags") or []) if t.get("label")]
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c):
                continue
            if c["days"] is None or not (2.0 <= c["days"] <= 200):
                continue
            if c["volume24hr"] < 5000 or c["liquidity"] < 5000:
                continue
            for out, side in c["sides"].items():
                asks, bids = side["asks"], side["bids"]
                if not asks or not bids:
                    continue
                p = asks[0][0]
                if not (0.85 <= p <= 0.985):
                    continue
                d = depth_usd(asks, min(p + 0.005, 0.995))
                if d < 2000:
                    continue
                shares = d / p
                cost = buy_cost(asks, shares, c["sched"])
                if not cost:
                    continue
                all_in = cost / shares
                apy = annualize(all_in, c["days"])
                if apy is None:
                    continue
                rows.append({
                    "question": c["question"], "slug": c["slug"], "outcome": out,
                    "event": ev.get("title"), "tags": tags[:4],
                    "ask": p, "bid": bids[0][0], "all_in_price": round(all_in, 4),
                    "return_pct": round(100 * (1 - all_in) / all_in, 2),
                    "days": round(c["days"], 1), "apy_pct": round(100 * apy, 1),
                    "tradable_usd": round(d, 0),
                    "volume24hr": round(c["volume24hr"]),
                    "liquidity": round(c["liquidity"]),
                    "one_day_move": c["oneDayPriceChange"],
                    "end_date": c["endDate"], "fee_rate": c["sched"].get("rate"),
                    "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
                })
    # one row per market: keep the better-yielding side
    bestby = {}
    for r in rows:
        k = r["slug"]
        if k not in bestby or r["return_pct"] > bestby[k]["return_pct"]:
            bestby[k] = r
    return sorted(bestby.values(), key=lambda r: -r["return_pct"])


def maker_income(events, books, top=40):
    """Wide-spread markets with live volume: makers pay no fees and earn rebates,
    so capturing a fraction of the spread is income rather than a directional bet."""
    rows = []
    for ev in events:
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            if c["volume24hr"] < 20000 or c["liquidity"] < 10000:
                continue
            y = c["sides"]["Yes"]
            if not y["bids"] or not y["asks"]:
                continue
            spread = y["asks"][0][0] - y["bids"][0][0]
            if spread < 0.02:
                continue
            rewards = (m.get("clobRewards") or [])
            daily = sum(float(r.get("rewards_daily_rate") or 0) for r in rewards) if rewards else 0
            rows.append({
                "question": c["question"], "slug": c["slug"], "event": ev.get("title"),
                "bid": y["bids"][0][0], "ask": y["asks"][0][0], "spread": round(spread, 4),
                "spread_pct": round(100 * spread / max(y["asks"][0][0], 1e-9), 2),
                "volume24hr": round(c["volume24hr"]), "liquidity": round(c["liquidity"]),
                "rewards_daily_usd": daily, "days": c["days"],
                "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
            })
    rows.sort(key=lambda r: -(r["rewards_daily_usd"] * 100 + r["spread"] * r["volume24hr"] / 1000))
    return rows[:top]


if __name__ == "__main__":
    events, books = load()
    cands = build(events, books)
    makers = maker_income(events, books)
    print(f"research candidates: {len(cands)}", file=sys.stderr)
    print(f"maker-income rows:  {len(makers)}", file=sys.stderr)
    with open(os.path.join(HERE, "data", "candidates.json"), "w") as f:
        json.dump({"candidates": cands, "maker_income": makers}, f, indent=1)
