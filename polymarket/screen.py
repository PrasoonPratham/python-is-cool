"""Screen the Polymarket universe for structurally mispriced positions.

Fee model (confirmed against docs.polymarket.com and each market's own
feeSchedule payload): fee_per_share = rate * (p * (1 - p)) ** exponent,
charged to TAKERS ONLY. Makers pay nothing and earn a rebate. Fees therefore
collapse toward zero at extreme prices and peak at p = 0.50 -- which is why
the arbitrage that survives fees lives in the tails.
"""
import json, math, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NOW = datetime.now(timezone.utc)

DEFAULT_FEE = {"rate": 0.05, "exponent": 1}


def fee_per_share(price, sched):
    """Polymarket taker fee for one share filled at `price`."""
    if not sched:
        sched = DEFAULT_FEE
    rate = sched.get("rate", 0.05)
    exponent = sched.get("exponent", 1) or 1
    return rate * (price * (1.0 - price)) ** exponent


def levels(book, side):
    """Order book levels sorted best-first. Bids descend, asks ascend."""
    out = [(float(l["price"]), float(l["size"])) for l in (book or {}).get(side, [])]
    out = [(p, s) for p, s in out if s > 0]
    out.sort(key=lambda x: -x[0] if side == "bids" else x[0])
    return out


def walk(asks, shares):
    """Cost to buy `shares` by taking asks. Returns (cost, filled, worst_price)."""
    cost = filled = 0.0
    worst = 0.0
    for price, size in asks:
        if filled >= shares:
            break
        take = min(size, shares - filled)
        cost += take * price
        filled += take
        worst = price
    return cost, filled, worst


def buy_cost(asks, shares, sched):
    """All-in taker cost (price + fee) to acquire `shares`. None if book too thin."""
    cost = filled = 0.0
    for price, size in asks:
        if filled >= shares:
            break
        take = min(size, shares - filled)
        cost += take * (price + fee_per_share(price, sched))
        filled += take
    if filled < shares - 1e-9:
        return None
    return cost


def max_profitable_size(legs, payout, cap=100000.0):
    """Largest share count where buying one share of every leg still pays.

    `legs` is a list of (asks, feeSchedule). Binary-searches size against the
    real book depth so the result is an executable number, not a top-of-book
    mirage.
    """
    def net(n):
        total = 0.0
        for asks, sched in legs:
            c = buy_cost(asks, n, sched)
            if c is None:
                return None
            total += c
        return payout * n - total

    if net(1.0) is None or net(1.0) <= 0:
        return 0.0, 0.0
    lo, hi = 1.0, 1.0
    while hi < cap:
        nxt = hi * 2
        v = net(nxt)
        if v is None or v <= 0:
            break
        lo, hi = nxt, nxt
        if hi >= cap:
            break
    hi = min(hi * 2, cap)
    for _ in range(60):
        mid = (lo + hi) / 2
        v = net(mid)
        if v is not None and v > 0:
            lo = mid
        else:
            hi = mid
    return lo, net(lo) or 0.0


def days_out(market):
    for key in ("endDate", "endDateIso"):
        v = market.get(key)
        if not v:
            continue
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - NOW).total_seconds() / 86400.0
        except ValueError:
            continue
    return None


def annualize(cost_per_dollar, days):
    """APY of a position costing `cost_per_dollar` that pays $1 in `days`."""
    if not days or days <= 0 or cost_per_dollar <= 0 or cost_per_dollar >= 1:
        return None
    years = days / 365.0
    if years < 1e-6:
        return None
    try:
        return (1.0 / cost_per_dollar) ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return None


def load():
    events = json.load(open(os.path.join(DATA, "events.json")))
    books = json.load(open(os.path.join(DATA, "books.json")))
    return events, books


def market_ctx(m, books):
    """Normalize a market into the fields every screen needs."""
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(toks) != len(outs) or not toks:
        return None
    sched = m.get("feeSchedule") or DEFAULT_FEE
    sides = {}
    for tok, out in zip(toks, outs):
        b = books.get(tok)
        if b is None:
            return None
        sides[out] = {
            "token": tok,
            "asks": levels(b, "asks"),
            "bids": levels(b, "bids"),
        }
    return {
        "id": m.get("id"),
        "question": m.get("question"),
        "slug": m.get("slug"),
        "groupItemTitle": m.get("groupItemTitle") or "",
        "sched": sched,
        "feeType": m.get("feeType"),
        "sides": sides,
        "days": days_out(m),
        "volume24hr": float(m.get("volume24hr") or 0),
        "volume": float(m.get("volumeNum") or m.get("volume") or 0),
        "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
        "uma": m.get("umaResolutionStatus"),
        "accepting": bool(m.get("acceptingOrders")),
        "negRisk": bool(m.get("negRisk")),
        "endDate": m.get("endDate"),
        "spread": m.get("spread"),
        "lastTradePrice": m.get("lastTradePrice"),
        "oneDayPriceChange": m.get("oneDayPriceChange"),
        "raw": m,
    }


def tradable(c):
    """Exclude anything already settling -- a 'proposed' UMA resolution means
    the outcome is known and the price is not an opportunity."""
    if c is None or not c["accepting"]:
        return False
    if c["uma"] in ("proposed", "disputed", "resolved", "settled"):
        return False
    if c["days"] is not None and c["days"] <= 0:
        return False
    return True
