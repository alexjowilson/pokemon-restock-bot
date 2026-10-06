"""Title matching for store searches: accent/case-insensitive, whole-word starts.

"Pokémon" matches "pokemon", and "tin" matches "Tin"/"Tins" but not "Destined".
"""
import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def has_word(title: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(normalize(word)), normalize(title)) is not None


def title_matches(title: str, require_all=(), include_any=(), exclude=()) -> bool:
    if any(has_word(title, w) for w in exclude):
        return False
    if not all(has_word(title, w) for w in require_all):
        return False
    return not include_any or any(has_word(title, w) for w in include_any)
