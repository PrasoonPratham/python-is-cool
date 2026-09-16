"""Estimate liquidity-reward yield on LIVE books, using Polymarket's own scoring.

Two corrections drive this file, both found by checking the model against a real
order book instead of trusting it:

  - Scoring is quadratic, S(v, s) = ((v - s) / v) ** 2 * size, where v is the
    market's max_spread and s the order's distance from the midpoint. An earlier
    exponential-per-tick discount scored every competing order at ~zero on a
    0.001 tick and produced five-figure APRs out of nothing.
  - Reward programmes that started today have thin books for only as long as it
    takes other makers to notice. The flagship example went from $5.5k to $24.7k
    of liquidity within an hour of being sampled, so a cached snapshot overstates
    the opportunity badly. Books here are fetched live.

Rewards are split pro-rata on each maker's own Q_min, where
Q_min = max(min(Q_one, Q_two), max(Q_one, Q_two) / 3): quoting both sides scores
in full, one side scores at a third, and outside a 0.10-0.90 midpoint only
two-sided quotes score at all.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STAKE = 2000.0
C = 3.0

session = requests.Session()
session.headers["User-Agent"] = "polymarket-farming/1.0"


def weight(dist, band):
    if dist < 0 or dist >= band or band <= 0:
        return 0.0
    return ((band - dist) / band) ** 2


def q_side(levels, mid, band, side):
    return sum(sz * weight((mid - p) if side == "bid" else (p - mid), band)
               for p, sz in levels)


def q_min(q_one, q_two, mid):
    if 0.10 <= mid <= 0.90:
        return max(min(q_one, q_two), max(q_one, q_two) / C)
    return min(q_one, q_two)   # in the tails, one-sided scores nothing


def live_book(token):
    for attempt in range(4):
        try:
            r = session.post(f"{CLOB}/books", json=[{"token_id": token}], timeout=30)
            if r.status_code == 200 and r.json():
                b = r.json()[0]
                bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids") or []), reverse=True)
                asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks") or [])
                return bids, asks
        except (requests.RequestException, ValueError, KeyError, IndexError):
            pass
        time.sleep(1.2 * (attempt + 1))
    return None, None


def evaluate(meta):
    """Model a two-sided STAKE quote posted one tick inside the current touch."""
    bids, asks = live_book(meta["yes_token"])
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    if not (0.05 <= mid <= 0.95):
        return None
    band = (meta["max_spread"] or 0) / 100.0
    if band <= 0:
        return None

    q_one = q_side(bids, mid, band, "bid")
    q_two = q_side(asks, mid, band, "ask")
    others = q_min(q_one, q_two, mid)

    tick = meta.get("tick") or 0.01
    our_dist = max((asks[0][0] - bids[0][0]) / 2 - tick, tick)
    w = weight(our_dist, band)
    if w <= 0:
        return None
    yes_sh = STAKE / 2.0 / max(mid, 0.01)
    no_sh = STAKE / 2.0 / max(1.0 - mid, 0.01)
    if min(yes_sh, no_sh) < (meta["min_size"] or 0):
        return None
    ours = q_min(yes_sh * w, no_sh * w, mid)

    share = ours / (others + ours) if (others + ours) > 0 else 0.0
    daily = meta["daily"] * share
    return {
        "question": meta["question"], "slug": meta["slug"],
        "daily_pool": meta["daily"], "mid": round(mid, 4),
        "spread": round(asks[0][0] - bids[0][0], 4),
        "band_cents": meta["max_spread"], "min_size": meta["min_size"],
        "competing_q": round(others, 1), "our_q": round(ours, 1),
        "pool_share_pct": round(100 * share, 1),
        "est_daily_usd": round(daily, 2),
        "est_apr_pct": round(100 * daily * 365 / STAKE, 0),
        "in_band_notional": round(
            sum(sz * p for p, sz in bids if mid - p <= band)
            + sum(sz * (1 - p) for p, sz in asks if p - mid <= band), 0),
        "volume24hr": meta["volume24hr"], "days": meta["days"],
        "url": meta["url"],
    }


def main():
    ranked = json.load(open(os.path.join(DATA, "rewards_ranked.json")))
    ranked = [r for r in ranked if r["daily_rewards"] >= 40
              and r["days"] is not None and r["days"] >= 3]
    ranked.sort(key=lambda r: -r["daily_rewards"])
    ranked = ranked[:220]
    print(f"re-pricing the {len(ranked)} best-funded reward markets on live books...",
          file=sys.stderr)

    # token ids and tick size come from Gamma, in bulk
    metas = []
    for r in ranked:
        g = None
        for _ in range(3):
            try:
                g = session.get(f"{GAMMA}/markets", params={"slug": r["slug"]}, timeout=30).json()
                break
            except (requests.RequestException, ValueError):
                time.sleep(1)
        if not g:
            continue
        m = g[0]
        if not m.get("acceptingOrders") or m.get("closed"):
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if len(toks) != 2:
            continue
        metas.append({**r, "daily": r["daily_rewards"], "yes_token": toks[0],
                      "max_spread": m.get("rewardsMaxSpread") or r["max_spread_cents"],
                      "min_size": m.get("rewardsMinSize") or r["min_size"],
                      "tick": float(m.get("orderPriceMinTickSize") or 0.01)})
    print(f"{len(metas)} still open and accepting orders", file=sys.stderr)

    out = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for res in pool.map(evaluate, metas):
            if res:
                out.append(res)
    out.sort(key=lambda r: -r["est_daily_usd"])
    with open(os.path.join(DATA, "farming.json"), "w") as f:
        json.dump(out, f, indent=1)

    print(f"\n{len(out)} modelled with a ${STAKE:,.0f} two-sided quote (LIVE books)\n")
    print(f"{'$/day':>8}{'APR%':>8}{'pool$':>7}{'share%':>8}{'inBand$':>10}"
          f"{'mid':>7}{'band¢':>7}{'days':>6}  question")
    for r in out[:22]:
        print(f"{r['est_daily_usd']:>8.2f}{r['est_apr_pct']:>8.0f}{r['daily_pool']:>7.0f}"
              f"{r['pool_share_pct']:>8.1f}${r['in_band_notional']:>9,.0f}"
              f"{r['mid']:>7.3f}{r['band_cents']:>7.1f}{r['days']:>6.0f}  {r['question'][:38]}")
    tot = sum(r["est_daily_usd"] for r in out[:10])
    print(f"\ntop 10: ${tot:,.2f}/day on ${10*STAKE:,.0f} = {100*tot*365/(10*STAKE):,.0f}% APR")
    print("Ceiling, not a forecast: the pool is pro-rata, so every maker who follows")
    print("you into a thin book cuts your share, and two-sided quotes get filled")
    print("precisely when the price is about to move against you.")


if __name__ == "__main__":
    main()
