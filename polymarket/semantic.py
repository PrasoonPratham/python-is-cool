"""Find semantically linked markets that are priced inconsistently.

Polymarket's bots arbitrage markets that are MECHANICALLY linked -- the two
sides of one book, or the outcome set of a neg-risk event. Nothing links
"Will the Iranian regime fall before 2027?" to "Iran leadership change by
December 31?" except meaning, so those can drift apart and stay apart.

This screen is deliberately only a candidate generator. High lexical similarity
is not logical equivalence: "Bitcoin reach $80,000" and "Bitcoin reach $85,000"
are nearly identical strings and completely different claims. So pairs that
differ only in a number or a date are separated out as ladder variants (handled
correctly, and mechanically, in ladders.py), and everything surviving is meant
to be checked by a reader before it is believed.

No sklearn here, so TF-IDF and the inverted index are built by hand. Comparing
67k markets pairwise would be ~2.2bn comparisons; indexing on rare terms and
only scoring pairs that share one keeps it tractable.
"""
import json, math, os, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screen import load, market_ctx, tradable

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RARE_DF = 60   # a token rarer than this is treated as identifying, not structural

STOP = set("""a an and are as at be been being by для do does for from had has have
how if in into is it its of on or that the their there these this to was were
what when where which who whom why will with would you your before after any
between most least than then there's not no nor own same so too very can just
get got make made next new other over under out up down off""".split())

MONTHS = ("january february march april may june july august september october "
          "november december").split()
DATE_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+\d{1,2}(?:,)?\s*\d{0,4}\b", re.I)
NUM_RE = re.compile(r"\$?\b\d[\d,]*\.?\d*\s*(?:k|m|bn|b|billion|million|trillion|%)?\b", re.I)
WORD_RE = re.compile(r"[a-z][a-z'\-]+")


def tokens(text):
    t = DATE_RE.sub(" ", (text or "").lower())
    t = NUM_RE.sub(" ", t)
    return [w for w in WORD_RE.findall(t) if w not in STOP and len(w) > 2]


# Words that flip a claim's polarity. TF-IDF cannot see the difference between
# "win the most seats" and "win the second-most seats" -- one token out of a
# dozen -- but they are opposite claims, and the first pass was almost entirely
# pairs like that. A pair only counts as the same claim if these agree.
ORDINAL = re.compile(r"\b(second|third|fourth|fifth|runner[- ]?up|next|another|"
                     r"2nd|3rd|4th|5th)\b", re.I)
NEGATIVE = re.compile(r"\b(not|no|never|less|below|under|fewer|lower|without|"
                      r"fail|fails|lose|loses|miss|misses|neither|nor|out)\b", re.I)
SUPERLATIVE = re.compile(r"\b(most|largest|highest|biggest|top|first|win|wins|"
                         r"at least|more|above|over|exceed)\b", re.I)
HEDGE = re.compile(r"\b(de facto|ballot|nominee|nominated|candidate|announce|"
                   r"announced|confirm|confirmed|attempt)\b", re.I)


def polarity(text):
    t = (text or "").lower()
    return (bool(ORDINAL.search(t)), bool(NEGATIVE.search(t)),
            bool(SUPERLATIVE.search(t)), bool(HEDGE.search(t)))


def quantities(text):
    """The numbers and dates a question turns on, so ladder variants can be split off."""
    t = (text or "").lower()
    dates = tuple(sorted(m.group(0).strip() for m in DATE_RE.finditer(t)))
    nums = tuple(sorted(m.group(0).strip() for m in NUM_RE.finditer(DATE_RE.sub(" ", t))))
    return dates, nums


def mid_price(c):
    y = c["sides"].get("Yes")
    if not y:
        return None
    b, a = y["bids"], y["asks"]
    if b and a:
        return (b[0][0] + a[0][0]) / 2
    return None


def build(events, books, min_liq=2000, min_vol=500):
    rows = []
    for ev in events:
        for m in ev.get("markets") or []:
            c = market_ctx(m, books)
            if not tradable(c) or set(c["sides"]) != {"Yes", "No"}:
                continue
            if c["liquidity"] < min_liq or c["volume"] < min_vol:
                continue
            if c["days"] is None or c["days"] < 1:
                continue
            p = mid_price(c)
            if p is None or not (0.02 <= p <= 0.98):
                continue
            tk = tokens(c["question"])
            if len(tk) < 3:
                continue
            rows.append({
                "q": c["question"], "slug": c["slug"], "event_id": ev.get("id"),
                "event": ev.get("title"), "price": p, "days": c["days"],
                "liq": c["liquidity"], "vol24": c["volume24hr"],
                "tokens": tk, "qty": quantities(c["question"]),
                "pol": polarity(c["question"]),
                "end": c["endDate"],
                "url": f"https://polymarket.com/event/{ev.get('slug')}/{c['slug']}",
            })
    return rows


def tfidf(rows):
    df = Counter()
    for r in rows:
        df.update(set(r["tokens"]))
    n = len(rows)
    vecs = []
    for r in rows:
        tf = Counter(r["tokens"])
        v = {}
        for w, c in tf.items():
            idf = math.log((n + 1) / (df[w] + 1)) + 1.0
            v[w] = (1 + math.log(c)) * idf
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs, df


def candidate_pairs(rows, vecs, df, max_df=300, top_terms=6):
    """Index on each market's rarest terms; only score pairs that share one."""
    index = defaultdict(list)
    for i, v in enumerate(vecs):
        rare = sorted(v.items(), key=lambda kv: (df[kv[0]], -kv[1]))[:top_terms]
        for w, _ in rare:
            if df[w] <= max_df:
                index[w].append(i)
    seen = set()
    for w, ids in index.items():
        if len(ids) > 400:
            continue  # a term this common adds noise, not signal
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = ids[a], ids[b]
                key = (i, j) if i < j else (j, i)
                if key not in seen:
                    seen.add(key)
    return seen


def rare_terms(row, df, max_df):
    """A market's distinguishing vocabulary -- the proper nouns and rare words
    that say WHICH claim it is rather than what shape the claim has.

    Template similarity is not claim similarity: "Lula wins Bahia" and "Lula
    wins Pernambuco" share almost every token, and "OpenAI hits $1.75T" and
    "Anthropic hits $1.5T" share the whole sentence frame. The words that
    distinguish them are exactly the rare ones, so two markets are the same
    claim only if these agree.
    """
    return {t for t in set(row["tokens"]) if df[t] <= max_df}


def cosine(u, v):
    if len(u) > len(v):
        u, v = v, u
    return sum(x * v.get(w, 0.0) for w, x in u.items())


def main():
    events, books = load()
    rows = build(events, books)
    print(f"{len(rows)} liquid markets in the semantic universe", file=sys.stderr)
    vecs, df = tfidf(rows)
    pairs = candidate_pairs(rows, vecs, df)
    print(f"{len(pairs):,} candidate pairs from the rare-term index", file=sys.stderr)

    dupes, ladders = [], []
    for i, j in pairs:
        ri, rj = rows[i], rows[j]
        if ri["event_id"] == rj["event_id"]:
            continue  # same event: mechanically linked, bots already own it
        sim = cosine(vecs[i], vecs[j])
        if sim < 0.55:
            continue
        gap = abs(ri["price"] - rj["price"])
        if gap < 0.04:
            continue
        # Two claims whose prices sum to ~1 are complements, not duplicates --
        # exactly what correct pricing looks like, so not a finding.
        if abs(ri["price"] + rj["price"] - 1.0) < 0.06:
            continue
        ra, rb = rare_terms(ri, df, RARE_DF), rare_terms(rj, df, RARE_DF)
        union = ra | rb
        jac = len(ra & rb) / len(union) if union else 0.0
        rec = {
            "similarity": round(sim, 3), "price_gap": round(gap, 4),
            "a_q": ri["q"], "a_price": round(ri["price"], 4), "a_event": ri["event"],
            "a_days": round(ri["days"], 1), "a_url": ri["url"], "a_liq": ri["liq"],
            "b_q": rj["q"], "b_price": round(rj["price"], 4), "b_event": rj["event"],
            "b_days": round(rj["days"], 1), "b_url": rj["url"], "b_liq": rj["liq"],
            "min_liq": min(ri["liq"], rj["liq"]),
        }
        # Differing numbers or dates mean these are rungs of a ladder, not the
        # same claim -- a different (and mechanical) screen's job.
        if ri["qty"] != rj["qty"]:
            rec["variant"] = "differs_by_quantity_or_date"
            ladders.append(rec)
        elif ri["pol"] != rj["pol"]:
            rec["variant"] = "differs_by_polarity"
            ladders.append(rec)
        elif jac < 0.85:
            rec["variant"] = "differs_by_entity"
            rec["rare_only_a"] = sorted(ra - rb)[:6]
            rec["rare_only_b"] = sorted(rb - ra)[:6]
            ladders.append(rec)
        else:
            rec["rare_jaccard"] = round(jac, 3)
            dupes.append(rec)

    dupes.sort(key=lambda r: -(r["price_gap"] * r["similarity"] * math.log10(max(r["min_liq"], 10))))
    ladders.sort(key=lambda r: -(r["price_gap"] * r["similarity"]))
    with open(os.path.join(DATA, "semantic.json"), "w") as f:
        json.dump({"same_claim": dupes, "quantity_variants": ladders}, f, indent=1)

    print(f"\nsame-claim candidates (identical quantities): {len(dupes)}", file=sys.stderr)
    print(f"quantity/date variants (ladder-like):          {len(ladders)}\n", file=sys.stderr)
    for r in dupes[:18]:
        print(f"sim {r['similarity']:.2f} | gap {r['price_gap']:.3f} | "
              f"${r['min_liq']:,.0f} min liq | sum {r['a_price']+r['b_price']:.3f}", file=sys.stderr)
        print(f"   A {r['a_price']:.3f} ({r['a_days']:>5.0f}d) {r['a_q']}", file=sys.stderr)
        print(f"   B {r['b_price']:.3f} ({r['b_days']:>5.0f}d) {r['b_q']}", file=sys.stderr)
        print(file=sys.stderr)


if __name__ == "__main__":
    main()
