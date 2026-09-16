"""Build a compact, domain-tagged index of markets worth checking against news.

The structural screens ask whether prices are consistent with each other. This
asks a different question: whether they are consistent with what has actually
happened. A market only reprices when someone notices, so the edge lives in
markets that are liquid enough to trade, close enough to resolution to matter,
and quiet enough that nobody has moved them lately.
"""
import json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DOMAINS = {
    "mideast": ["iran", "israel", "gaza", "hezbollah", "hormuz", "houthi", "yemen",
                "lebanon", "syria", "saudi", "blockade", "nuclear deal", "bab el-mandeb"],
    "ukraine_russia": ["ukraine", "russia", "putin", "zelensky", "zelenskyy", "nato",
                       "ceasefire", "kremlin", "moscow", "kyiv"],
    "us_politics": ["trump", "biden", "congress", "senate", "house", "midterm",
                    "supreme court", "impeach", "shutdown", "cabinet", "governor",
                    "republican", "democrat", "speaker"],
    "macro_fed": ["fed", "interest rate", "inflation", "cpi", "recession", "jobs",
                  "unemployment", "jolts", "gdp", "rate hike", "rate cut", "powell"],
    "crypto": ["bitcoin", "ethereum", "solana", "xrp", "crypto", "etf", "sec",
               "stablecoin", "clarity act"],
    "tech_ai": ["openai", "anthropic", "google", "gemini", "nvidia", "apple", "tesla",
                "microsoft", "meta", "llm", "gpt", "claude", "agi", "chip"],
    "elections_intl": ["election", "parliamentary", "presidential", "chancellor",
                       "prime minister", "coalition", "afd", "bolsonaro", "lula"],
    "energy": ["oil", "wti", "brent", "crude", "opec", "gas", "pipeline", "energy"],
}


def classify(question, event, tags):
    hay = f"{question} {event} {' '.join(tags)}".lower()
    hits = [d for d, kws in DOMAINS.items() if any(k in hay for k in kws)]
    return hits or ["other"]


def main():
    events, books = load()
    rows = []
    for ev in events:
        tags = [t.get("label") for t in (ev.get("tags") or []) if t.get("label")]
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            if c["days"] is None or not (0.5 <= c["days"] <= 120):
                continue
            if c["liquidity"] < 8000 or c["volume24hr"] < 2000:
                continue
            y = c["sides"]["Yes"]
            if not (y["bids"] and y["asks"]):
                continue
            mid = (y["bids"][0][0] + y["asks"][0][0]) / 2
            if not (0.03 <= mid <= 0.97):
                continue
            d1 = c["oneDayPriceChange"]
            wk = m.get("oneWeekPriceChange")
            rows.append({
                "q": c["question"], "price": round(mid, 3), "days": round(c["days"], 1),
                "liq": round(c["liquidity"]), "vol24": round(c["volume24hr"]),
                "move_1d": round(float(d1), 4) if d1 is not None else None,
                "move_1w": round(float(wk), 4) if wk is not None else None,
                "end": c["endDate"],
                "domains": classify(c["question"], ev.get("title") or "", tags),
                "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
            })

    by_domain = defaultdict(list)
    for r in rows:
        for d in r["domains"]:
            by_domain[d].append(r)
    for d in by_domain:
        # Quietest first: a liquid, near-dated market that has not moved all week
        # is the likeliest place for a stale price. A MISSING weekly figure is
        # unknown, not quiet -- treating None as 0.0 ranked a market that had
        # just moved 24 points in a day as the calmest thing on the board.
        by_domain[d].sort(key=lambda r: (r["move_1w"] is None,
                                         abs(r["move_1w"]) if r["move_1w"] is not None else 9.9,
                                         -r["vol24"]))

    out = {d: v[:70] for d, v in by_domain.items()}
    with open(os.path.join(DATA, "newsdesk.json"), "w") as f:
        json.dump(out, f, indent=1)

    print(f"{len(rows)} liquid near-term markets indexed\n", file=sys.stderr)
    for d in sorted(out, key=lambda k: -len(by_domain[k])):
        qs = by_domain[d]
        quiet = sum(1 for r in qs if abs(r["move_1w"] or 0) < 0.02)
        print(f"  {d:>16}: {len(qs):>4} markets  ({quiet} unmoved in a week)", file=sys.stderr)
    print(f"\nwrote {DATA}/newsdesk.json (top 70 per domain)", file=sys.stderr)


if __name__ == "__main__":
    main()
