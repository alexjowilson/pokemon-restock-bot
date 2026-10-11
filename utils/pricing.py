"""Compare a price to MSRP: "+214% over MSRP" and a 🟢/🟡/🔴 verdict."""
from __future__ import annotations

from utils.text import has_word

# US MSRPs (Pokémon TCG, Mega Evolution era and 30th Celebration). Override per
# product with `msrp:` in config.yaml, or globally with `msrp:` at the top level.
DEFAULT_MSRP = {"elite trainer box": 49.99, "booster bundle": 26.94}
GOOD_PCT = 15    # within 15% of MSRP: 🟢 fair price
OK_PCT = 50      # within 50%: 🟡 marked up; beyond: 🔴 scalper price


def msrp_for(name: str, explicit=None, table: dict | None = None) -> float | None:
    """The product's own `msrp:` wins; otherwise match its name against the table."""
    if explicit not in (None, ""):
        return float(explicit)
    for kind, price in (table or DEFAULT_MSRP).items():
        if has_word(name, kind) or (kind == "elite trainer box" and has_word(name, "etb")):
            return float(price)
    return None


def pct_over(price, msrp) -> float | None:
    if price is None or not msrp:
        return None
    return (float(price) - float(msrp)) / float(msrp) * 100


def verdict(price, msrp) -> str:
    """'🟢 +4% vs $49.99 MSRP', or '' when there's nothing to compare."""
    pct = pct_over(price, msrp)
    if pct is None:
        return ""
    icon = "🟢" if pct <= GOOD_PCT else ("🟡" if pct <= OK_PCT else "🔴")
    sign = "+" if pct >= 0 else "−"
    return f"{icon} {sign}{abs(pct):.0f}% vs ${float(msrp):.2f} MSRP"


def is_deal(price, msrp, max_pct) -> bool:
    pct = pct_over(price, msrp)
    return pct is not None and max_pct is not None and pct <= float(max_pct)
