"""Pull the live Polymarket universe: events, markets, and CLOB order books."""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

session = requests.Session()
session.headers["User-Agent"] = "polymarket-screener/1.0"


def get(url, **params):
    for attempt in range(5):
        try:
            r = session.get(url, params=params, timeout=40)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 422:
                return None  # gamma refuses offsets past its cap
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {url}")


# Gamma refuses offsets past ~2100, so we sweep the same universe under several
# sort orders and union the results to reach past any single ordering's window.
SWEEPS = [
    {"order": "volume24hr", "ascending": "false"},
    {"order": "liquidity", "ascending": "false"},
    {"order": "endDate", "ascending": "true"},
    {"order": "endDate", "ascending": "false"},
    {"order": "startDate", "ascending": "false"},
    {"order": "volume", "ascending": "false"},
    {"order": "competitive", "ascending": "false"},
]


def fetch_events():
    """Page through every open event. Events carry the negRisk grouping we need."""
    by_id = {}
    for sweep in SWEEPS:
        offset, added = 0, 0
        while True:
            page = get(f"{GAMMA}/events", limit=100, offset=offset,
                       active="true", closed="false", archived="false", **sweep)
            if not page:
                break
            for ev in page:
                if ev["id"] not in by_id:
                    by_id[ev["id"]] = ev
                    added += 1
            offset += 100
            print(f"  events: {len(by_id)} (sweep {sweep['order']})   ",
                  file=sys.stderr, end="\r")
            if offset > 5000:
                break
        print(f"  sweep {sweep['order']}/{sweep['ascending']}: +{added} new"
              f" (total {len(by_id)})          ", file=sys.stderr)
    return list(by_id.values())


def fetch_books(token_ids, chunk=100):
    """Batch-fetch order books. The CLOB accepts a POST list of token ids."""
    books = {}

    def one(batch):
        for attempt in range(5):
            try:
                r = session.post(f"{CLOB}/books", json=[{"token_id": t} for t in batch], timeout=45)
                if r.status_code == 200:
                    return r.json()
                time.sleep(2 ** attempt)
            except requests.RequestException:
                time.sleep(2 ** attempt)
        return []

    batches = [token_ids[i:i + chunk] for i in range(0, len(token_ids), chunk)]
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(one, batches):
            for b in result:
                books[b["asset_id"]] = {"bids": b.get("bids") or [], "asks": b.get("asks") or []}
            done += 1
            print(f"  books: {done}/{len(batches)} batches", file=sys.stderr, end="\r")
    print(file=sys.stderr)
    return books


def main():
    os.makedirs(OUT, exist_ok=True)
    print("fetching events...", file=sys.stderr)
    events = fetch_events()

    token_ids = []
    for ev in events:
        for m in ev.get("markets") or []:
            if not m.get("enableOrderBook") or m.get("closed"):
                continue
            try:
                token_ids.extend(json.loads(m.get("clobTokenIds") or "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
    token_ids = list(dict.fromkeys(token_ids))
    print(f"{len(events)} events, {len(token_ids)} tradable tokens", file=sys.stderr)

    print("fetching order books...", file=sys.stderr)
    books = fetch_books(token_ids)
    print(f"{len(books)} books retrieved", file=sys.stderr)

    with open(os.path.join(OUT, "events.json"), "w") as f:
        json.dump(events, f)
    with open(os.path.join(OUT, "books.json"), "w") as f:
        json.dump(books, f)
    print(f"wrote {OUT}/events.json and {OUT}/books.json", file=sys.stderr)


if __name__ == "__main__":
    main()
