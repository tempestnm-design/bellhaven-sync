"""Comparison-only normalization. Source values remain unchanged."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


STREET_TOKENS = {
    "street": "st", "st": "st", "avenue": "ave", "ave": "ave",
    "boulevard": "blvd", "blvd": "blvd", "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr", "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct", "circle": "cir", "cir": "cir",
    "highway": "hwy", "hwy": "hwy", "parkway": "pkwy", "pkwy": "pkwy",
    "place": "pl", "pl": "pl", "terrace": "ter", "ter": "ter",
    "pike": "pike", "pk": "pike",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}


def text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).split())


def address(value: str) -> str:
    return " ".join(STREET_TOKENS.get(token, token) for token in text(value).split())


def zip_code(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[:5]


def phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def name(value: str) -> str:
    return text(value)


def name_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, name(left), name(right)).ratio()


def facility_key(street: str, city: str, state: str, zip_value: str) -> str:
    return "|".join((address(street), text(city), text(state), zip_code(zip_value)))
