"""Find the Walmart query shape that actually surfaces a set's sealed products.

Run on the Pi (uses the same .env keys as the bot):
    venv/bin/python -m scripts.walmart_probe "destined rivals"
    venv/bin/python -m scripts.walmart_probe "destined rivals" --pages 6
    venv/bin/python -m scripts.walmart_probe --raw "pokemon elite trainer box"

For a set name it tries several query shapes, pages through the results (25 per
page), and prints only the ETBs and Booster Bundles it finds, so you can see which
shape finds them and on which page. Walmart's search ignores `sort`, so it isn't
tested. Costs one API call per page (default: up to 5 shapes x 4 pages = 20 calls).
"""
import argparse
import asyncio

from dotenv import load_dotenv

from monitors import walmart
from monitors.http import http
from utils.config import PROJECT_ROOT
from utils.text import title_matches

SEALED = ("elite trainer box", "etb", "booster bundle")
NOT_SEALED = ("sleeve", "sleeves", "playmat", "binder", "case for", "protector", "acrylic")


def shapes_for(set_name: str) -> list:
    return [
        set_name,
        f"pokemon {set_name}",
        f"{set_name} elite trainer box",
        f"{set_name} booster bundle",
        f"{set_name} etb",
    ]


async def run_query(query: str, pages: int) -> tuple:
    """Returns (totalResults, items fetched, sealed matches with their page number)."""
    total, fetched, sealed = None, 0, []
    for page in range(pages):
        params = {"query": query, "numItems": "25", "start": str(page * 25)}
        data = await walmart._signed_get(walmart.SEARCH_URL, params)
        items = (data.get("items") or []) if isinstance(data, dict) else []
        if total is None and isinstance(data, dict):
            total = data.get("totalResults")
        if not items:
            break
        fetched += len(items)
        for item in items:
            name = item.get("name", "")
            if title_matches(name, include_any=SEALED, exclude=NOT_SEALED):
                sealed.append((page + 1, item))
        if total is not None and fetched >= int(total):
            break
    return total, fetched, sealed


async def probe_set(set_name: str, pages: int) -> None:
    for query in shapes_for(set_name):
        try:
            total, fetched, sealed = await run_query(query, pages)
        except Exception as e:
            print(f"\n=== \"{query}\": FAILED: {e}")
            continue
        print(f"\n=== \"{query}\": totalResults={total}, scanned {fetched}, sealed found: {len(sealed)}")
        for page, item in sealed:
            seller = "WALMART " if walmart.is_first_party(item) else "reseller"
            stock = item.get("stock") or "?"
            print(f"    p{page} {seller} {item.get('itemId')} ${item.get('salePrice')} [{stock}]  "
                  f"{item.get('name', '')[:85]}")


async def probe_raw(query: str, pages: int) -> None:
    total, fetched, _ = None, 0, None
    for page in range(pages):
        data = await walmart._signed_get(walmart.SEARCH_URL, {"query": query, "numItems": "25",
                                                              "start": str(page * 25)})
        items = (data.get("items") or []) if isinstance(data, dict) else []
        print(f"\n=== \"{query}\" page {page + 1}: {len(items)} items, "
              f"totalResults={data.get('totalResults') if isinstance(data, dict) else '?'}")
        if not items:
            print("    raw:", str(data)[:300])
            break
        for item in items:
            seller = "WALMART " if walmart.is_first_party(item) else "reseller"
            print(f"    {seller} {item.get('itemId')} ${item.get('salePrice')}  {item.get('name', '')[:90]}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help='set name, e.g. "destined rivals" (or any query with --raw)')
    parser.add_argument("--pages", type=int, default=4, help="pages of 25 to scan per query (default 4)")
    parser.add_argument("--raw", action="store_true", help="print every result for one exact query")
    args = parser.parse_args()
    try:
        if args.raw:
            await probe_raw(args.query, args.pages)
        else:
            await probe_set(args.query, args.pages)
    finally:
        await http.close()


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    asyncio.run(main())
