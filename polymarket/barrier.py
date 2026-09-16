"""Price Polymarket's crypto threshold markets as one-touch barrier options.

"Will Bitcoin dip to $72,000 by Saturday" is not a matter of opinion -- it is a
first-passage probability. Under a driftless geometric random walk the
reflection principle gives it in closed form, so these markets can be priced
from realized volatility and compared against what the book is charging.

P(touch barrier B before T) = 2 * Phi(-|ln(B/S)| / (sigma * sqrt(T)))

Caveats worth keeping in view: real crypto returns are fat-tailed, which makes
this understate touch probability for distant barriers; and realized volatility
is backward-looking. Both are reported as a sensitivity band rather than a
single number.
"""
import json, math, os, re, sys, time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable, fee_per_share

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KRAKEN = "https://api.kraken.com/0/public"
PAIRS = {"bitcoin": "XBTUSD", "ethereum": "ETHUSD", "solana": "SOLUSD",
         "xrp": "XRPUSD", "dogecoin": "DOGEUSD", "cardano": "ADAUSD"}
ALIAS = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana",
         "doge": "dogecoin", "ada": "cardano"}


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def kraken_ohlc(pair, interval=1440):
    r = requests.get(f"{KRAKEN}/OHLC", params={"pair": pair, "interval": interval}, timeout=30)
    d = r.json()
    if d.get("error"):
        return None
    key = next(iter(d["result"].keys() - {"last"}), None)
    rows = d["result"][key]
    return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in rows]


def realized_vol(closes, window):
    """Annualized close-to-close volatility over the last `window` days."""
    c = closes[-(window + 1):]
    if len(c) < 10:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c)) if c[i - 1] > 0]
    if len(rets) < 5:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 365.0)


def touch_prob(spot, barrier, sigma, years):
    """Probability of touching `barrier` at ANY point before expiry."""
    if years <= 0 or sigma <= 0 or spot <= 0 or barrier <= 0:
        return None
    b = abs(math.log(barrier / spot))
    denom = sigma * math.sqrt(years)
    if denom <= 0:
        return None
    return min(1.0, 2.0 * phi(-b / denom))


def terminal_prob(spot, barrier, sigma, years):
    """Probability of finishing beyond `barrier` AT expiry.

    Distinct from touching it: "above $76,000 on September 17" only cares where
    the price lands, so it is half the touch probability, not the same number.
    Conflating the two doubles every estimate.
    """
    if years <= 0 or sigma <= 0 or spot <= 0 or barrier <= 0:
        return None
    b = abs(math.log(barrier / spot))
    denom = sigma * math.sqrt(years)
    if denom <= 0:
        return None
    return phi(-b / denom)


# "be above $X on <date>" settles on the closing level; "reach/hit/dip to"
# settles the moment the level trades.
TERMINAL_RE = re.compile(r"\bbe\s+(above|below|under|over)\b|\bclose\s+(above|below)\b", re.I)


def contract_type(question):
    return "terminal" if TERMINAL_RE.search(question or "") else "touch"


ASSET_RE = re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|dogecoin|doge|cardano|ada)\b", re.I)
LEVEL_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
UP_RE = re.compile(r"\b(reach|hit|above|exceed|surpass|climb|rise)\b", re.I)
DOWN_RE = re.compile(r"\b(dip|fall|drop|below|decline|under)\b", re.I)


def parse(question):
    a = ASSET_RE.search(question or "")
    lv = LEVEL_RE.search(question or "")
    if not a or not lv:
        return None
    asset = a.group(1).lower()
    asset = ALIAS.get(asset, asset)
    if asset not in PAIRS:
        return None
    try:
        level = float(lv.group(1).replace(",", ""))
    except ValueError:
        return None
    q = question.lower()
    if "(low)" in q:
        direction = "down"
    elif "(high)" in q:
        direction = "up"
    elif DOWN_RE.search(q):
        direction = "down"
    elif UP_RE.search(q):
        direction = "up"
    else:
        return None
    return asset, level, direction


def main():
    events, books = load()
    spots, vols = {}, {}
    for asset, pair in PAIRS.items():
        rows = kraken_ohlc(pair)
        if not rows:
            continue
        closes = [r[4] for r in rows]
        spots[asset] = closes[-1]
        vols[asset] = {w: realized_vol(closes, w) for w in (14, 30, 90)}
        time.sleep(0.4)
    print("spot / annualized realized vol", file=sys.stderr)
    for a in spots:
        v = vols[a]
        print(f"  {a:>9}: ${spots[a]:>12,.2f}  14d={v[14]:.1%} 30d={v[30]:.1%} 90d={v[90]:.1%}"
              if all(v.values()) else f"  {a:>9}: ${spots[a]:,.2f}", file=sys.stderr)

    now = datetime.now(timezone.utc)
    out = []
    for ev in events:
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            p = parse(c["question"])
            if not p:
                continue
            asset, level, direction = p
            if asset not in spots:
                continue
            spot = spots[asset]
            # A barrier already breached is not a forecast; skip those.
            if (direction == "up" and level <= spot) or (direction == "down" and level >= spot):
                continue
            days = c["days"]
            if days is None or days <= 0.2 or days > 180:
                continue
            if c["volume24hr"] < 3000 or c["liquidity"] < 3000:
                continue
            years = days / 365.0
            ctype = contract_type(c["question"])
            model = touch_prob if ctype == "touch" else terminal_prob
            band = {}
            for w in (14, 30, 90):
                s = vols[asset].get(w)
                if s:
                    band[w] = model(spot, level, s, years)
            # Fat tails: crypto crashes harder than a lognormal allows, so stress
            # volatility up by 50% and require any edge to survive that too.
            v30 = vols[asset].get(30)
            stressed = model(spot, level, v30 * 1.5, years) if v30 else None
            if not band or band.get(30) is None:
                continue
            fair = band[30]
            if stressed is None:
                continue
            yes, no = c["sides"]["Yes"], c["sides"]["No"]
            if not yes["asks"] or not no["asks"]:
                continue
            ya, na = yes["asks"][0][0], no["asks"][0][0]
            ycost = ya + fee_per_share(ya, c["sched"])
            ncost = na + fee_per_share(na, c["sched"])
            # Edge on each side, using the most conservative vol in the band.
            vals = [v for v in band.values() if v is not None] + [stressed]
            lo_fair, hi_fair = min(vals), max(vals)
            out.append({
                "question": c["question"], "asset": asset, "spot": round(spot, 2),
                "contract_type": ctype, "stressed_fair": round(stressed, 4),
                "barrier": level, "direction": direction,
                "move_pct": round(100 * (level / spot - 1), 2),
                "days": round(days, 2),
                "fair_touch_30d_vol": round(fair, 4),
                "fair_range": [round(lo_fair, 4), round(hi_fair, 4)],
                "yes_allin": round(ycost, 4), "no_allin": round(ncost, 4),
                # buying YES wins if it touches; buying NO wins if it does not
                "yes_edge": round(lo_fair - ycost, 4),
                "no_edge": round((1 - hi_fair) - ncost, 4),
                "volume24hr": round(c["volume24hr"]),
                "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
            })
    out.sort(key=lambda r: -max(r["yes_edge"], r["no_edge"]))
    with open(os.path.join(DATA, "barrier.json"), "w") as f:
        json.dump({"spots": spots, "vols": vols, "markets": out}, f, indent=1)
    print(f"\npriced {len(out)} crypto barrier markets", file=sys.stderr)
    print(f"{'edge':>7} {'side':>4} {'mkt':>7} {'fair':>7} {'range':>17} {'move':>8} {'days':>6}  question", file=sys.stderr)
    for r in out[:25]:
        side = "YES" if r["yes_edge"] >= r["no_edge"] else "NO"
        edge = max(r["yes_edge"], r["no_edge"])
        mkt = r["yes_allin"] if side == "YES" else r["no_allin"]
        fair = r["fair_touch_30d_vol"] if side == "YES" else 1 - r["fair_touch_30d_vol"]
        rng = f"[{r['fair_range'][0]:.3f},{r['fair_range'][1]:.3f}]"
        print(f"{edge:>+7.3f} {side:>4} {mkt:>7.3f} {fair:>7.3f} {rng:>17} "
              f"{r['move_pct']:>+7.1f}% {r['days']:>6.1f} {r['contract_type'][:4]:>5}  "
              f"{r['question'][:46]}", file=sys.stderr)


if __name__ == "__main__":
    main()
