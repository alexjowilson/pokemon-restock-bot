"""See exactly what Walmart's search returns for a query, with each sort order.

Run on the Pi (uses the same .env keys as the bot):
    venv/bin/python -m scripts.walmart_probe "pokemon destined rivals elite trainer box"

Costs one API call per sort order (3 by default).
"""
import asyncio
import sys

from dotenv import load_dotenv

from monitors import walmart
from monitors.http import http
from utils.config import PROJECT_ROOT

SORTS = (None, "new", "relevance")


async def probe(query: str) -> None:
    for sort in SORTS:
        params = {"query": query, "numItems": "25"}
        if sort:
            params["sort"] = sort
        label = f"sort={sort or '(none)'}"
        try:
            data = await walmart._signed_get(walmart.SEARCH_URL, params)
        except Exception as e:  # show the failure and keep going
            print(f"\n=== {label}: FAILED: {e}")
            continue
        keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        items = data.get("items") or [] if isinstance(data, dict) else []
        total = data.get("totalResults") if isinstance(data, dict) else None
        print(f"\n=== {label}: {len(items)} items returned, totalResults={total}, keys={keys}")
        if isinstance(data, dict) and not items:
            print("    raw:", str(data)[:400])
        for item in items[:25]:
            seller = "WALMART " if walmart.is_first_party(item) else "reseller"
            print(f"    {seller} {item.get('itemId')} ${item.get('salePrice')}  {item.get('name', '')[:90]}")
    await http.close()


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    q = " ".join(sys.argv[1:]) or "pokemon destined rivals elite trainer box"
    asyncio.run(probe(q))
