"""Implication screens: pairs of markets where one outcome logically entails another.

If A implies B, then P(A) <= P(B) must hold. Buying NO on A and YES on B pays
at least $1 in every state of the world, so an all-in cost under $1 is locked-in
profit regardless of what actually happens.

The hard part is establishing implication *correctly*. Two traps:
  - Direction. "reach $85k" gets harder as the number rises; "dip to $35k" gets
    harder as it FALLS. Treating them alike inverts the trade into one with a
    guaranteed-loss branch.
  - Dates masquerading as thresholds. "by September 21" vs "by December 31"
    yields the numbers 21 and 31, which say nothing about strictness.
So: pair only markets whose text is identical apart from one varying quantity,
and decide strictness from that template's own wording.
"""
import json, os, re, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable, buy_cost, max_profitable_size

MIN_PROFIT_USD = 5.0

MONTHS = ("january february march april may june july august september "
          "october november december").split()
DATE_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:,)?\s*(\d{4})?\b", re.I)
MONEY_RE = re.compile(r"\$?\b(\d[\d,]*\.?\d*)\s*(k|m|bn|b|billion|million|trillion)?\b", re.I)
MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9,
        "million": 1e6, "billion": 1e9, "trillion": 1e12}

UP_WORDS = ("reach", "above", "exceed", "surpass", "or higher", "or more",
            "at least", "greater than", "over ", "top ", "cross", "hit")
DOWN_WORDS = ("dip to", "dip below", "below", "under", "fall to", "drop to",
              "or lower", "or less", "less than", "down to")


def direction(text):
    """+1 if a larger number is a STRICTER claim, -1 if a smaller one is.

    Polymarket tags touch markets "(HIGH)"/"(LOW)", and that tag overrides the
    verb: "hit (LOW) $60" is a deeper dip than "hit (LOW) $85", so the smaller
    number is the stricter claim even though the verb is "hit".
    """
    t = (text or "").lower()
    if "(low)" in t:
        return -1
    if "(high)" in t:
        return 1
    down = next((i for w in DOWN_WORDS if (i := t.find(w)) >= 0), None)
    up = next((i for w in UP_WORDS if (i := t.find(w)) >= 0), None)
    if down is not None and up is not None:
        return -1 if down < up else 1  # whichever phrase governs the threshold
    if down is not None:
        return -1
    if up is not None:
        return 1
    return 0


def mid(c, side="Yes"):
    s = c["sides"][side]
    b, a = s["bids"], s["asks"]
    if b and a:
        return (b[0][0] + a[0][0]) / 2
    if a:
        return a[0][0]
    if b:
        return b[0][0]
    return None


def price_direction(rungs):
    """Direction implied by the ladder's own prices: rising threshold vs rising
    probability, scored over every adjacent pair. Returns (+1/-1/0, agreement)."""
    pts = [(t, mid(c)) for t, c in rungs]
    pts = [(t, m) for t, m in pts if m is not None]
    if len(pts) < 3:
        return 0, 0.0
    pts.sort(key=lambda x: x[0])
    up = down = 0
    for (t1, m1), (t2, m2) in zip(pts, pts[1:]):
        if abs(m2 - m1) < 1e-9:
            continue
        # higher threshold -> lower probability means higher is stricter
        if m2 < m1:
            up += 1
        else:
            down += 1
    total = up + down
    if total == 0:
        return 0, 0.0
    if up >= down:
        return 1, up / total
    return -1, down / total


def parse_date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    month = MONTHS.index(m.group(1).lower()) + 1
    day, year = int(m.group(2)), int(m.group(3) or 0)
    if not year:
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def money_template(text):
    """Blank out the threshold so two ladder rungs compare as equal templates."""
    return MONEY_RE.sub("<N>", DATE_RE.sub("<D>", text or "").lower()).strip()


def date_template(text):
    return DATE_RE.sub("<D>", text or "").lower().strip()


def threshold(text):
    """The single numeric threshold, ignoring anything inside a date."""
    stripped = DATE_RE.sub(" ", text or "")
    vals = []
    for m in MONEY_RE.finditer(stripped):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        vals.append(v * MULT.get((m.group(2) or "").lower(), 1.0))
    return vals[0] if len(vals) == 1 else None


def _pair(strict_c, loose_c, kind, ev):
    """Buy NO on the stricter claim and YES on the looser one."""
    no_strict = strict_c["sides"]["No"]["asks"]
    yes_loose = loose_c["sides"]["Yes"]["asks"]
    if not no_strict or not yes_loose:
        return None
    if no_strict[0][0] + yes_loose[0][0] >= 1.0:
        return None
    size, profit = max_profitable_size(
        [(no_strict, strict_c["sched"]), (yes_loose, loose_c["sched"])], 1.0)
    if profit < MIN_PROFIT_USD:
        return None
    cost = (buy_cost(no_strict, size, strict_c["sched"])
            + buy_cost(yes_loose, size, loose_c["sched"]))
    return {
        "screen": kind, "event": ev.get("title"), "event_slug": ev.get("slug"),
        "buy_no_on": strict_c["question"], "no_slug": strict_c["slug"],
        "buy_yes_on": loose_c["question"], "yes_slug": loose_c["slug"],
        "no_ask": no_strict[0][0], "yes_ask": yes_loose[0][0],
        "raw_sum": round(no_strict[0][0] + yes_loose[0][0], 4),
        "shares": round(size, 2), "capital": round(cost, 2),
        "profit_usd": round(profit, 2),
        "return_pct": round(100 * profit / cost, 3) if cost else 0,
        "volume24hr": strict_c["volume24hr"] + loose_c["volume24hr"],
    }


def numeric_ladders(events, books):
    hits = []
    for ev in events:
        if ev.get("negRisk"):
            continue
        ms = [c for c in (market_ctx(m, books) for m in (ev.get("markets") or []))
              if tradable(c) and set(c["sides"] if c else {}) == {"Yes", "No"}]
        groups = {}
        for c in ms:
            t = threshold(c["question"])
            if t is None:
                continue
            groups.setdefault(money_template(c["question"]), []).append((t, c))
        for tmpl, rungs in groups.items():
            if len(rungs) < 2:
                continue
            d = direction(tmpl)
            if d == 0:
                continue  # wording does not establish which way strictness runs
            # Cross-check the wording against the ladder's own price ordering.
            # Every false positive so far came from a direction flip, so when
            # text and prices disagree we drop the group rather than guess.
            pd, agree = price_direction(rungs)
            if pd == 0 or pd != d or agree < 0.7:
                continue
            rungs.sort(key=lambda x: x[0], reverse=(d < 0))
            # rungs now run loosest -> strictest
            for i in range(len(rungs)):
                for j in range(i + 1, len(rungs)):
                    if rungs[j][0] == rungs[i][0]:
                        continue
                    hit = _pair(rungs[j][1], rungs[i][1], "numeric_ladder", ev)
                    if hit:
                        hit["template"] = tmpl
                        hit["strict_threshold"] = rungs[j][0]
                        hit["loose_threshold"] = rungs[i][0]
                        hit["direction"] = "higher_is_stricter" if d > 0 else "lower_is_stricter"
                        hits.append(hit)
    return sorted(hits, key=lambda h: -h["profit_usd"])


def date_ladders(events, books):
    """"X by <date>" markets: an earlier deadline implies a later one."""
    hits = []
    for ev in events:
        if ev.get("negRisk"):
            continue
        ms = [c for c in (market_ctx(m, books) for m in (ev.get("markets") or []))
              if tradable(c) and set(c["sides"] if c else {}) == {"Yes", "No"}]
        groups = {}
        for c in ms:
            d = parse_date(c["question"])
            if d is None or threshold(c["question"]) is not None:
                continue  # skip anything that also varies a numeric threshold
            groups.setdefault(date_template(c["question"]), []).append((d, c))
        for tmpl, rungs in groups.items():
            if len(rungs) < 2:
                continue
            rungs.sort(key=lambda x: x[0])  # earliest (strictest) first
            for i in range(len(rungs)):
                for j in range(i + 1, len(rungs)):
                    if rungs[j][0] == rungs[i][0]:
                        continue
                    hit = _pair(rungs[i][1], rungs[j][1], "date_ladder", ev)
                    if hit:
                        hit["template"] = tmpl
                        hit["strict_deadline"] = rungs[i][0].strftime("%Y-%m-%d")
                        hit["loose_deadline"] = rungs[j][0].strftime("%Y-%m-%d")
                        hits.append(hit)
    return sorted(hits, key=lambda h: -h["profit_usd"])


if __name__ == "__main__":
    events, books = load()
    num = numeric_ladders(events, books)
    dat = date_ladders(events, books)
    print(f"numeric_ladders: {len(num)}", file=sys.stderr)
    print(f"date_ladders:    {len(dat)}", file=sys.stderr)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ladders.json"), "w") as f:
        json.dump({"numeric_ladders": num, "date_ladders": dat}, f, indent=1)
