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

## Round two: semantics and news

Two questions the structural screens cannot ask — whether prices are consistent
with *meaning*, and whether they are consistent with *what has happened*.

### The news is already in the prices

Every live story checked on 2026-09-16 had been absorbed, usually within a day:

- **CLARITY Act.** The Senate cloture vote failed on 15 Sep, killing the crypto
  market-structure bill for 2026. The platform's highest-volume market
  ($1.56M/day) sits at 0.0645 — having moved −0.09 over the week and
  **−0.0005 on the day**. It repriced *before and during* the vote, not after.
- **Saudi East–West pipeline.** Drone strikes from Iraq shut the 4–5M bpd line
  on 10–11 Sep; oil broke $100. "Restarts by September 30" jumped **+26.5pts
  to 0.68** on the US Energy Secretary saying repairs were a matter of days.
- **Oil complex.** Consistent repricing across the board the same day: WTI
  $110-touch −22.5pts, $115-touch −15.9pts, $100-low +16.5pts.

A first read of the pipeline market looked like a short: Reuters sources put
repairs at five to six weeks, which lands well past 30 September, against a
market at 0.68. That read was wrong twice over. The resolution text accepts
**partial or reduced capacity**, and an earlier 2026 strike on this same
pipeline was repaired by Aramco to full capacity in about a week. The market
is defensible; the "obvious" trade was an artifact of reading one number and
stopping.

Note also what does *not* qualify there: the announcement must come from the
Saudi government and state the pipeline is **presently operating**. Statements
that it "will operate soon", or that describe work underway — exactly what the
US Energy Secretary said, and what moved the price 26 points — are explicitly
excluded.

### Read the resolution text, not the title

The sharpest thing found in this round is not a mispricing but a trap, on the
Russian legislative election resolving in three days:

| market | price | 24h volume |
|---|---|---|
| United Russia **gain** the most seats | 0.735 | $823,054 |
| United Russia **win** the most seats | 0.991 | $31,462 |

Nearly the same sentence, 26 points apart — and both correct. "Win" resolves on
plurality, which United Russia has never lost. "Gain" resolves on seats *"compared
to before the election"*: the largest **net increase**, which a party holding 326
of 450 seats can easily lose to a small party growing from a low base. New People
is 0.225 to gain the most and 0.003 to win the most, for the same reason.

The market with the misleading title carries **26× the volume** of the clear one.

Deriving "gain the most" from the platform's own per-party seat-count markets
was inconclusive: the answer swings between 0.002 and 0.353 depending purely on
how wide the open-ended buckets ("fewer than 280", "355 or more") are assumed to
be. `gains.py` runs that sensitivity sweep and reports the instability rather
than picking a flattering assumption.

### What the semantic screen actually found

Nothing tradable — but the reasons are the useful part. Of the pairs it surfaced:

- Most were **polarity flips**: "most seats" vs "second-most seats" differ by one
  token and mean opposite things.
- Many were **complements** whose prices sum to ~1.00 — correct pricing, not a gap.
- The rest were **same template, different entity**: "Lula wins Bahia" vs "Lula
  wins Pernambuco", "OpenAI hits $1.75T" vs "Anthropic hits $1.5T".

Where markets genuinely nest, the ordering held: "next Gemini Pro released **by**
September 30" at 0.170 against "**on** September 30" at 0.032.

### Verification results: 77 agents, zero surviving edges

Two adversarial workflows ran over this universe — 26 shortlisted positions
researched individually, and a 9-domain news sweep — each finding checked by
independent agents told to refute it.

| | candidates | survived |
|---|---|---|
| shortlisted positions | 26 researched (4 claimed edge, 17 fair, 5 avoid) | **0** |
| news-staleness findings | 10 across 9 domains | **0** |
| verification votes | 42 | 42 refuted |

The refutations were substantive rather than reflexive, and three themes recur.

**The market had already repriced.** The most common failure. On "Russia-Ukraine
peace talks by October 31", an analyst argued traders were anchored and had not
marked the Peskov/Lavrov headlines. A verifier pulled the CLOB price history:
the Yes token went 0.440 → 0.530 over 27 hours on exactly those headlines, then
faded to 0.485. The market had marked the news within hours, bid it up, and
rejected it. "Unmoved" was an artifact of reading a single snapshot instead of
the tape.

**The entry price did not exist.** Several findings quoted a price between the
bid and the ask. Tick size on these markets is a full cent, so a derived mid of
0.9428 is not takeable — the live ask was 0.95, and on a 5.5-cent tail a
one-cent spread is roughly 18% of the contract's value. Checking the snapshot
against the live book confirmed this is decay, not a calculation error: the
0.94 level had 3,238 shares (~$3,044) when sampled and was gone within hours.
**Six of the 26 quoted entries had already moved against us about two hours
after sampling.**

**The resolution text was truncated.** Descriptions were cut to 1,800 characters
when building the research input, and in at least two cases the missing half was
decisive. On the Iran blockade market the omitted clause reads: *"Once a
qualifying announcement is made, this market will resolve to 'Yes' regardless of
whether it is later reversed."* Combined with a trigger set including
"suspension", that converts a policy-change hazard into an utterance hazard —
a much fatter tail, and one that inverted the trade. A data-prep shortcut, caught
only because the verifier went back to the source.

### A caution about news agents

On the Middle East the agents contradicted each other outright. The domain
reporter found continued escalation (Houthi offensive, Iran striking ten ships
on 9 Sep) and returned zero findings; a verifier on a different market asserted
a ceasefire had held since ~9-10 Sep with a framework deal circulating.
Independent searches could not confirm the second account. In a fast-moving
conflict these summaries are not reliable enough to trade on without going to
primary sources, and the disagreement itself is the useful signal.

Two details worth keeping, both of which *explain* prices rather than beat them:
the CLARITY Act cloture failed 49-50 with Tillis switching to "no" and
immediately filing a motion to reconsider — which keeps the bill technically
alive and is why "signed into law in 2026" sits at 0.0645 rather than near zero;
and the FOMC raised 25bp to 3.75-4.00% on 16 Sep, 12-0. That last one lands
directly on the Fed markets flagged above for liquidity-reward farming, where a
major scheduled event is exactly when a two-sided quote gets run over.
