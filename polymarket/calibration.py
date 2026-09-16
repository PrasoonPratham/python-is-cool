"""Calibration backtest, bias-controlled.

The first attempt produced a positive "edge" in every single price bucket, which
is the signature of a sampling artifact rather than a real inefficiency: markets
that resolve NO stop trading, lose their price history, and drop out of the
sample, leaving it stacked with YES resolutions (53% vs 33% in the population).

Polymarket also prunes price history to roughly two weeks after a market closes,
so a long-horizon study is not available from this endpoint at all.

This version therefore (a) restricts to a cohort resolved recently enough that
history is still retained, and (b) reports the YES-rate of the kept sample
against the YES-rate of the full cohort, so the bias is measured rather than
assumed away. If those two rates diverge, the calibration numbers are not
trustworthy and the script says so.
"""
import json, os, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HORIZONS = [1, 2, 5]
NOW = datetime.now(timezone.utc)

session = requests.Session()
session.headers["User-Agent"] = "polymarket-calibration/1.0"


def get(url, **params):
    for attempt in range(5):
        try:
            r = session.get(url, params=params, timeout=40)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 422:
                return None
            time.sleep(1.2 * (attempt + 1))
        except requests.RequestException:
            time.sleep(1.2 * (attempt + 1))
    return None


def cohort(max_age_days=13, min_volume=5000):
    """Resolved binary markets whose history should still be retained."""
    out, cutoff = {}, NOW - timedelta(days=max_age_days)
    for sweep in ({"order": "endDate", "ascending": "false"},
                  {"order": "volume24hr", "ascending": "false"},
                  {"order": "volume", "ascending": "false"},
                  {"order": "liquidity", "ascending": "false"}):
        offset = 0
        while offset <= 2000:
            page = get(f"{GAMMA}/markets", limit=100, offset=offset,
                       closed="true", archived="false", **sweep)
            if not page:
                break
            for m in page:
                if m.get("id") in out or m.get("umaResolutionStatus") != "resolved":
                    continue
                if float(m.get("volumeNum") or 0) < min_volume:
                    continue
                end = m.get("endDate") or ""
                try:
                    dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if not (cutoff <= dt <= NOW):
                    continue
                try:
                    prices = json.loads(m.get("outcomePrices") or "[]")
                    toks = json.loads(m.get("clobTokenIds") or "[]")
                    outs = json.loads(m.get("outcomes") or "[]")
                except (json.JSONDecodeError, TypeError):
                    continue
                if len(prices) != 2 or len(toks) != 2 or outs != ["Yes", "No"]:
                    continue
                try:
                    yf = float(prices[0])
                except ValueError:
                    continue
                if yf not in (0.0, 1.0):
                    continue
                out[m["id"]] = {"id": m["id"], "question": m.get("question"),
                                "slug": m.get("slug"), "yes_token": toks[0],
                                "outcome": yf, "end": dt,
                                "volume": float(m.get("volumeNum") or 0)}
            offset += 100
            print(f"  cohort: {len(out)}   ", file=sys.stderr, end="\r")
    print(file=sys.stderr)
    return list(out.values())


def observe(r):
    """Price at each horizon before the market's own resolution time."""
    d = get(f"{CLOB}/prices-history", market=r["yes_token"], interval="max", fidelity=60)
    h = sorted((int(x["t"]), float(x["p"])) for x in ((d or {}).get("history") or []))
    if not h:
        return r, None
    end_ts = r["end"].timestamp()
    got = {}
    for days in HORIZONS:
        target = end_ts - days * 86400
        if target < h[0][0]:
            continue
        px = None
        for t, p in h:
            if t <= target:
                px = p
            else:
                break
        if px is not None and 0.0 < px < 1.0:
            got[days] = px
    return r, (got or None)


BUCKETS = [(0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.50),
           (0.50, 0.70), (0.70, 0.85), (0.85, 0.95), (0.95, 1.0)]


def calibrate(obs, days):
    b = defaultdict(lambda: {"n": 0, "yes": 0.0, "px": 0.0})
    for o in obs:
        p = o["prices"].get(days)
        if p is None:
            continue
        for lo, hi in BUCKETS:
            if lo <= p < hi:
                c = b[(lo, hi)]
                c["n"] += 1; c["yes"] += o["outcome"]; c["px"] += p
                break
    rows = []
    for (lo, hi), c in sorted(b.items()):
        if c["n"] < 10:
            continue
        mp, rf = c["px"] / c["n"], c["yes"] / c["n"]
        # binomial standard error on the realized frequency
        se = (rf * (1 - rf) / c["n"]) ** 0.5
        rows.append({"bucket": f"{lo:.2f}-{hi:.2f}", "n": c["n"],
                     "mean_price": round(mp, 4), "realized": round(rf, 4),
                     "edge_pp": round(100 * (rf - mp), 2),
                     "se_pp": round(100 * se, 2),
                     "significant": abs(rf - mp) > 2 * se})
    return rows


if __name__ == "__main__":
    print("building cohort...", file=sys.stderr)
    rows = cohort()
    pop_yes = sum(r["outcome"] for r in rows) / max(len(rows), 1)
    print(f"cohort: {len(rows)} markets, population YES rate {100*pop_yes:.1f}%", file=sys.stderr)

    obs, dropped = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (r, got) in enumerate(pool.map(observe, rows)):
            if i % 50 == 0:
                print(f"  observed {i}/{len(rows)}   ", file=sys.stderr, end="\r")
            if got:
                obs.append({"id": r["id"], "question": r["question"], "slug": r["slug"],
                            "outcome": r["outcome"], "volume": r["volume"], "prices": got})
            else:
                dropped.append(r)
    print(file=sys.stderr)

    kept_yes = sum(o["outcome"] for o in obs) / max(len(obs), 1)
    drop_yes = sum(r["outcome"] for r in dropped) / max(len(dropped), 1)
    bias_pp = 100 * (kept_yes - pop_yes)
    trustworthy = abs(bias_pp) < 5.0

    print(f"\nkept {len(obs)}  dropped {len(dropped)}", file=sys.stderr)
    print(f"YES rate  population {100*pop_yes:.1f}%  kept {100*kept_yes:.1f}%  "
          f"dropped {100*drop_yes:.1f}%", file=sys.stderr)
    print(f"selection bias: {bias_pp:+.1f}pp -> "
          f"{'ACCEPTABLE' if trustworthy else 'SAMPLE IS BIASED, DO NOT TRUST'}",
          file=sys.stderr)

    result = {"n": len(obs), "population_yes_rate": pop_yes, "kept_yes_rate": kept_yes,
              "selection_bias_pp": bias_pp, "trustworthy": trustworthy,
              "calibration": {str(d): calibrate(obs, d) for d in HORIZONS}}
    with open(os.path.join(DATA, "calibration2.json"), "w") as f:
        json.dump({"summary": result, "observations": obs}, f)

    for d in HORIZONS:
        print(f"\n=== {d}d before resolution ===", file=sys.stderr)
        print(f"{'bucket':>12}{'n':>6}{'mean_px':>10}{'realized':>10}{'edge_pp':>9}{'se_pp':>8}{'sig':>6}", file=sys.stderr)
        for r in result["calibration"][str(d)]:
            print(f"{r['bucket']:>12}{r['n']:>6}{r['mean_price']:>10.4f}{r['realized']:>10.4f}"
                  f"{r['edge_pp']:>9.2f}{r['se_pp']:>8.2f}{'  *' if r['significant'] else '   '}", file=sys.stderr)
