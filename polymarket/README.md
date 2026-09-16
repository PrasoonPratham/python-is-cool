# Polymarket screener

Tooling to find mispriced positions on Polymarket, and a record of what it found
on 2026-09-16 across 10,364 events / 66,898 tradable markets / ~171,000 live
order books.

## The headline

**There is no risk-free arbitrage.** Every mechanical screen returned zero:

| screen | hits |
|---|---|
| YES + NO below $1 on the same market | 0 |
| neg-risk events whose outcome set misprices | 0 |
| numeric threshold ladders (A entails B) | 0 |
| date ladders ("by March" entails "by June") | 0 |

The first pass *did* report 531 ladder violations. All 531 were bugs in the
screen, not opportunities — see "Three ways to be confidently wrong" below.
Bots have this layer covered.

## Fees decide everything

`fee = shares × rate × p × (1-p)`, charged to **takers only**. Makers pay zero
and earn a rebate. Rates by category: politics/finance/tech 0.04, sports/
economics/culture/weather 0.05, crypto 0.07, geopolitics 0.

The `p(1-p)` term is the whole story. At p=0.50 a politics trade pays 1.0¢/share;
at p=0.99 it pays 0.04¢. So fees are brutal on coin-flips and nearly free in the
tails — and any two-legged arbitrage pays the fee twice, which is why a 2¢
mispricing at even money is not a trade.

## What the backtest says about "safe" bets

The obvious-looking play — buy something at 0.93 that "can't" lose — **backtests
negative at every threshold**, net of fees, on 282 markets resolved in the
trailing 13 days:

| buy side at ≥ | trades | win rate | net return |
|---|---|---|---|
| 0.85 | 106 | 93.4% | −0.74% |
| 0.90 | 83 | 92.8% | −3.14% |
| 0.95 | 50 | 96.0% | −1.93% |
| 0.97 | 34 | 97.1% | −1.60% |

Confidence intervals straddle zero, so the honest reading is "no detectable
edge", not "a proven loser". But the point estimate is negative everywhere, and
the distribution is exactly the wrong shape: you win 93% of the time and the 7%
takes more than the 93% paid you.

Calibration is flat across the board (95% Wilson intervals contain the market
price in 7 of 8 buckets). Polymarket is, empirically, well priced.

The blowups cluster hard: near-certainties in **weather** markets lost 9.0% net
across 29 trades, and exact-score sports lost 6.5%. Those two categories are
where "this can't lose" goes to die.

Caveat on scope: this cohort is 36% weather and 45% sports, because those are
what resolve quickly enough to still have price history. It says little about
long-dated political markets.

## The one strategy that isn't a coin flip

Polymarket pays **~$142,000/day** across ~17,100 markets to makers who quote
inside a per-market spread band — and makers pay no fees. That is the only edge
here that is neither directional nor arbitrage: you are paid for a service.

The catch is adverse selection (you get filled precisely when the price is about
to move) and pro-rata dilution. Yields compress fast: the best-funded market in
this scan went from $5.5k to $24.7k of resting liquidity **within an hour** of
first being sampled, cutting its modelled yield 73%.

`farming.py` models this on live books using Polymarket's own scoring,
`S(v,s) = ((v-s)/v)² × size` with `Q_min = max(min(Q₁,Q₂), max(Q₁,Q₂)/3)`.

## Three ways to be confidently wrong

Each of these produced plausible, specific, large-dollar "opportunities" that
were entirely artifacts. They are the reason this repo cross-checks itself.

1. **Ladder direction.** "Dip to $35,000" is *harder* than "dip to $70,000", and
   Polymarket's `(LOW)` tag inverts the verb "hit". Ordering rungs by magnitude
   built the trade backwards — into one with a guaranteed-loss branch. Direction
   is now read from the wording *and* cross-checked against the ladder's own
   price ordering; disagreement drops the group. That one fix took 531 "arbs"
   to zero.
2. **Dates parsed as thresholds.** "by September 21" vs "by December 31" yields
   21 and 31, which say nothing about strictness. Numeric and date ladders are
   now separate screens over identical-template pairs.
3. **Touch vs terminal contracts.** "Be above $76,000 **on** Sep 17" settles on
   the close; "reach $76,000" settles the moment it trades. Pricing the first
   with the reflection principle doubles every estimate and invented a +0.47
   edge on a coin flip.

A fourth lived in the backtest itself: markets that resolve NO stop trading and
lose their price history, which silently stacked the first sample at 53% YES
against a 33% population rate and manufactured a positive edge in *every*
bucket. `calibration.py` now reports kept-vs-population YES rate so the bias is
measured, not assumed away.

The pattern is consistent: every spectacular result was a bug. Screens that
survived contact with a real order book returned zero.

## Files

| file | does |
|---|---|
| `fetch.py` | pull events + every CLOB order book |
| `screen.py` | fee model, depth-walking, shared helpers |
| `screens.py` | YES/NO arb, neg-risk arb, near-certainty yield, maker quotes |
| `ladders.py` | implication pairs, direction-checked |
| `candidates.py` | liquid shortlist worth researching |
| `calibration.py` | bias-controlled resolved-market backtest |
| `backtest.py` | strategy P&L net of fees, Wilson intervals |
| `barrier.py` | crypto thresholds priced as one-touch options |
| `rewards.py` / `farming.py` | liquidity-reward yield on live books |

```bash
python3 polymarket/fetch.py        # ~5 min, writes data/ (gitignored, ~700MB)
python3 polymarket/screens.py
python3 polymarket/calibration.py && python3 polymarket/backtest.py
python3 polymarket/rewards.py && python3 polymarket/farming.py
```

Analysis, not financial advice. Prices move; re-run before acting on anything.
