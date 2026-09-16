"""Cross-market consistency check on Russia's "gains the most seats" market.

Two Polymarket events look almost identical and are priced 26 points apart:
"United Russia GAIN the most seats" (0.735) and "United Russia WIN the most
seats" (0.991). The resolution texts settle it -- "gain" resolves on seats
"compared to before the election", i.e. the largest net increase, while "win"
resolves on plurality. Different questions, so the gap is not a mispricing.
But the same platform also runs per-party seat-count markets, so the gain market
can be derived from those and checked against its own quoted price.

The derivation is only as good as its handling of the open-ended buckets
("fewer than 280", "355 or more"), which carry real probability mass and no
stated width. This script therefore runs the calculation across a range of
assumed widths and reports whether the answer is stable. If it is not, the
check cannot support a conclusion and says so.
"""
import json, random, re, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable

# Seats held going into the election (2021 result plus post-election adjustments)
BASE = {"United Russia": 326, "Communist": 57, "A Just Russia": 28,
        "Liberal Democratic": 23, "New People": 15}
MARKET = {"United Russia": 0.735, "New People": 0.225,
          "Liberal Democratic": 0.024, "Communist": 0.017}


def party_of(text):
    for k in BASE:
        if k.lower() in (text or "").lower():
            return k
    return None


def distributions(events, books, open_width):
    """Per-party seat distributions, with open buckets given `open_width`."""
    out = {}
    for ev in events:
        title = ev.get("title") or ""
        if not re.search(r"seats", title, re.I):
            continue
        party = party_of(title)
        if not party:
            continue
        buckets = []
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            y = c["sides"]["Yes"]
            if not (y["bids"] and y["asks"]):
                continue
            p = (y["bids"][0][0] + y["asks"][0][0]) / 2
            q = c["question"]
            rng_ = re.search(r"between (\d+) and (\d+)", q)
            if rng_:
                lo, hi = int(rng_.group(1)), int(rng_.group(2))
            elif (mm := re.search(r"fewer than (\d+)", q)):
                hi = int(mm.group(1)) - 1
                lo = max(0, hi - open_width)
            elif (mm := re.search(r"(\d+) or more", q)):
                lo = int(mm.group(1))
                hi = min(450, lo + open_width)
            else:
                continue
            buckets.append((lo, hi, p))
        if len(buckets) >= 4:
            tot = sum(b[2] for b in buckets) or 1.0
            out[party] = [(lo, hi, p / tot) for lo, hi, p in buckets]
    return out


def simulate(d, n=300000, seed=11, enforce_total=True):
    """Draw seat counts per party and score who gained most.

    The Duma has 450 seats, so the parties' counts are not independent: a draw
    giving United Russia 340 AND the LDPR 90 describes an impossible parliament.
    Sampling each party's marginal independently generates a great many such
    draws and badly overstates the small parties' chance of posting the largest
    gain. Rejecting incoherent totals restores the zero-sum structure.
    """
    rng = random.Random(seed)
    parties = list(d)
    wins = {p: 0 for p in parties}
    modelled_base = sum(BASE[p] for p in parties)
    lo_tot, hi_tot = modelled_base - 20, min(450, modelled_base + 20)

    def draw(p):
        r, acc = rng.random(), 0.0
        for lo, hi, pr in d[p]:
            acc += pr
            if r <= acc:
                return rng.uniform(lo, hi + 1)
        lo, hi, _ = d[p][-1]
        return rng.uniform(lo, hi + 1)

    kept = tries = 0
    while kept < n and tries < n * 400:
        tries += 1
        seats = {p: draw(p) for p in parties}
        if enforce_total and not (lo_tot <= sum(seats.values()) <= hi_tot):
            continue
        gains = {p: seats[p] - BASE[p] for p in parties}
        wins[max(gains, key=gains.get)] += 1
        kept += 1
    if kept == 0:
        return {p: float("nan") for p in parties}
    return {p: wins[p] / kept for p in parties}


def main():
    events, books = load()
    results, naive = {}, {}
    for width in (10, 20, 30, 45, 60):
        d = distributions(events, books, width)
        if "United Russia" not in d:
            print("United Russia seat market not found — cannot run the check", file=sys.stderr)
            return
        results[width] = simulate(d, n=60000)
        naive[width] = simulate(d, n=60000, enforce_total=False)

    parties = sorted(results[20], key=lambda p: -MARKET.get(p, 0))
    print("P(party gains the MOST seats), derived from the seat-count markets")
    print("across assumptions about how wide the open-ended buckets really are\n")
    header = f"{'party':>20}{'market':>9}" + "".join(f"{('w=' + str(w)):>9}" for w in results)
    print(header)
    print("-" * len(header))
    for p in parties:
        row = f"{p:>20}{MARKET.get(p, 0):>9.3f}"
        for w in results:
            row += f"{results[w].get(p, 0):>9.3f}"
        print(row)

    print("\nfor comparison, ignoring the 450-seat constraint (incoherent draws kept):")
    row = f"{'':>20}{'':>9}"
    for p in parties:
        print(f"  {p:>20}" + "".join(f"{naive[w].get(p,0):>9.3f}" for w in naive))

    spread = {p: max(results[w].get(p, 0) for w in results)
                 - min(results[w].get(p, 0) for w in results) for p in parties}
    print("\nswing across assumptions:")
    for p in parties:
        print(f"  {p:>20}: {spread[p]:.3f}")
    worst = max(spread.values())
    print(f"\nLargest swing is {worst:.3f}. An arbitrary modelling choice moves the")
    print("answer by " + ("more" if worst > 0.10 else "less") + " than the gap to the market price, so this check "
          + ("CANNOT" if worst > 0.10 else "CAN") + "\nsupport a claim of mispricing.")


if __name__ == "__main__":
    main()
