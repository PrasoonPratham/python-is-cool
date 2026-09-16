"""Rank Polymarket's liquidity-reward programme by yield on capital.

Polymarket pays makers to quote. This is the one strategy here that is neither
directional nor arbitrage: you are paid for providing a service, in a market
where makers also pay zero fees. What it costs you is adverse selection -- you
are filled precisely when the price is about to move against you -- so the
rewards are compensation for a real risk, not free money.

Eligibility comes from the CLOB's own reward config: quotes must sit within
`max_spread` cents of the midpoint and carry at least `min_size` shares.
"""
import json, os, sys, time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable

CLOB = "https://clob.polymarket.com"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def fetch_rewarded():
    s = requests.Session()
    s.headers["User-Agent"] = "polymarket-rewards/1.0"
    rows, cursor = [], ""
    for _ in range(80):
        r = s.get(f"{CLOB}/sampling-simplified-markets",
                  params={"next_cursor": cursor}, timeout=40)
        if r.status_code != 200:
            break
        d = r.json()
        rows.extend(d.get("data") or [])
        cursor = d.get("next_cursor") or ""
        if not cursor or cursor == "LTE=":
            break
        time.sleep(0.2)
    out = {}
    for m in rows:
        rw = m.get("rewards") or {}
        daily = sum(float(x.get("rewards_daily_rate") or 0) for x in (rw.get("rates") or []))
        if daily > 0:
            out[m.get("condition_id")] = {
                "daily": daily,
                "min_size": rw.get("min_size"),
                "max_spread": rw.get("max_spread"),
            }
    return out


def main():
    rewarded = fetch_rewarded()
    print(f"{len(rewarded)} markets paying rewards, "
          f"${sum(r['daily'] for r in rewarded.values()):,.0f}/day total", file=sys.stderr)
    events, books = load()
    rows = []
    for ev in events:
        for m in ev.get("markets") or []:
            rw = rewarded.get(m.get("conditionId"))
            if not rw:
                continue
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            y = c["sides"]["Yes"]
            if not y["bids"] or not y["asks"]:
                continue
            spread = y["asks"][0][0] - y["bids"][0][0]
            mid = (y["asks"][0][0] + y["bids"][0][0]) / 2
            # Capital to post min_size on both sides at the reward band's edge.
            band = (rw["max_spread"] or 0) / 100.0
            minsz = float(rw["min_size"] or 0)
            capital = minsz * (mid + (1 - mid)) if minsz else 0.0
            rows.append({
                "question": c["question"], "event": ev.get("title"), "slug": c["slug"],
                "daily_rewards": rw["daily"], "min_size": minsz,
                "max_spread_cents": rw["max_spread"],
                "current_spread": round(spread, 4), "mid": round(mid, 4),
                "inside_band": spread <= band * 2,
                "min_capital_usd": round(capital, 2),
                "volume24hr": round(c["volume24hr"]), "liquidity": round(c["liquidity"]),
                "days": c["days"],
                # ceiling: what one maker earns holding the whole rewarded book
                "reward_apr_ceiling_pct": round(
                    100 * rw["daily"] * 365 / max(c["liquidity"], 1), 1),
                "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
            })
    rows.sort(key=lambda r: -r["daily_rewards"])
    with open(os.path.join(DATA, "rewards_ranked.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print(f"matched {len(rows)} to live markets\n", file=sys.stderr)
    print(f"{'$/day':>7}{'minSz':>7}{'band¢':>7}{'spread':>8}{'liq':>10}{'vol24h':>11}"
          f"{'aprCeil%':>10}  question", file=sys.stderr)
    for r in rows[:25]:
        print(f"{r['daily_rewards']:>7.0f}{r['min_size']:>7.0f}{r['max_spread_cents'] or 0:>7.1f}"
              f"{r['current_spread']:>8.3f}${r['liquidity']:>9,}${r['volume24hr']:>10,}"
              f"{r['reward_apr_ceiling_pct']:>10.0f}  {r['question'][:42]}", file=sys.stderr)


if __name__ == "__main__":
    main()
