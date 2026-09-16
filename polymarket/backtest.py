"""Backtest the strategy the shortlist actually represents.

Every candidate that survived screening is the same trade in different clothes:
buy the near-certain side at 0.85-0.98 and collect the few cents of residual
doubt. So test exactly that on resolved markets -- net of the real taker fee --
instead of reasoning about it.
"""
import json, os, sys
from math import sqrt

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def wilson(k, n, z=1.96):
    """Wilson score interval -- unlike the normal approximation it stays sane
    when every observation lands on the same side (k=0 or k=n)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def fee(price, rate=0.05):
    return rate * price * (1 - price)


def main():
    d = json.load(open(os.path.join(DATA, "calibration2.json")))
    obs = d["observations"]
    summary = d["summary"]
    print(f"cohort: {summary['n']} resolved markets | "
          f"selection bias {summary['selection_bias_pp']:+.1f}pp\n")

    HOR = "1"  # JSON object keys come back as strings
    rows = [(o["prices"][HOR], o["outcome"], o) for o in obs if HOR in o["prices"]]
    print(f"usable observations at {HOR}d before resolution: {len(rows)}\n")

    print("=" * 78)
    print("CALIBRATION (YES price vs realized YES frequency), 95% Wilson interval")
    print("=" * 78)
    print(f"{'YES price':>14}{'n':>6}{'mean_px':>10}{'realized':>10}{'95% CI':>18}{'verdict':>16}")
    edges = [(0.0, 0.03), (0.03, 0.07), (0.07, 0.15), (0.15, 0.30),
             (0.30, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.0)]
    for lo, hi in edges:
        sel = [(p, y) for p, y, _ in rows if lo <= p < hi]
        if len(sel) < 8:
            continue
        n = len(sel)
        k = sum(y for _, y in sel)
        mp = sum(p for p, _ in sel) / n
        rf = k / n
        clo, chi = wilson(k, n)
        inside = clo <= mp <= chi
        print(f"{lo:.2f}-{hi:.2f}".rjust(14) + f"{n:>6}{mp:>10.4f}{rf:>10.4f}"
              f"{f'[{clo:.3f},{chi:.3f}]':>18}"
              f"{'calibrated' if inside else 'MISPRICED':>16}")

    print()
    print("=" * 78)
    print("STRATEGY BACKTEST: buy the near-certain side, net of taker fees")
    print("=" * 78)
    print(f"{'buy side at >=':>15}{'trades':>8}{'win rate':>10}{'gross/$1':>10}"
          f"{'fees':>8}{'net/$1':>9}{'net %':>8}{'95% CI on net %':>22}")
    for thresh in (0.85, 0.88, 0.90, 0.93, 0.95, 0.97):
        staked = payout = fees = 0.0
        wins = trades = 0
        for p, y, _ in rows:
            # buying NO costs (1 - YES price); it wins when the market resolves NO
            for entry, won in ((p, y == 1.0), (1 - p, y == 0.0)):
                if entry < thresh or entry >= 0.999:
                    continue
                trades += 1
                staked += entry
                fees += fee(entry)
                if won:
                    wins += 1
                    payout += 1.0
        if trades < 5:
            continue
        cost = staked + fees
        net = payout - cost
        pct = 100 * net / cost
        wlo, whi = wilson(wins, trades)
        # translate the win-rate interval into a return interval at the mean entry
        mean_entry = staked / trades
        mean_cost = cost / trades
        lo_pct = 100 * (wlo - mean_cost) / mean_cost
        hi_pct = 100 * (whi - mean_cost) / mean_cost
        print(f"{thresh:>15.2f}{trades:>8}{wins/trades:>10.3f}"
              f"{payout/staked:>10.4f}{fees/trades:>8.4f}{net/trades:>9.4f}"
              f"{pct:>8.2f}{f'[{lo_pct:+.1f}%, {hi_pct:+.1f}%]':>22}")

    print()
    print("=" * 78)
    print("WORST LOSSES (near-certainties that resolved the other way)")
    print("=" * 78)
    busts = []
    for p, y, o in rows:
        for entry, won, side in ((p, y == 1.0, "Yes"), (1 - p, y == 0.0, "No")):
            if entry >= 0.90 and not won:
                busts.append((entry, side, o["question"], o["volume"]))
    busts.sort(reverse=True)
    for entry, side, q, vol in busts[:10]:
        print(f"  paid {entry:.3f} for {side:>3} | vol ${vol:>10,.0f} | {q[:62]}")
    print(f"\n  {len(busts)} of the priced->=0.90 positions in this cohort lost outright.")


if __name__ == "__main__":
    main()
