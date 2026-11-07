"""Stable identifiers of shared devices and of their parts."""

from __future__ import annotations

import hashlib
import re
import string

KEY_MAX_LENGTH = 64
HASH_LENGTH = 8

_REGISTRY_ID = re.compile(r"^[0-9a-f]{32}$")
_OUTSIDE_KEY = re.compile(r"[^a-z0-9]+")
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def device_row_id(registry_id: str) -> str | None:
    """Return the shared id of a device registry entry, or None if it has no usable id."""
    if _REGISTRY_ID.fullmatch(registry_id):
        return f"d{registry_id}"
    return None


def entity_row_id(domain: str, platform: str, unique_id: str) -> str:
    """Return the shared id of an entity that does not belong to a device."""
    return "e" + _sha256(f"{domain}\n{platform}\n{unique_id}")[:32]


def part_key(domain: str, platform: str, unique_id: str) -> str:
    """Return the key of a part: always the same for the same entity, at most 64 characters."""
    room = KEY_MAX_LENGTH - len(domain) - 1
    digest = _sha256(f"{platform}\n{unique_id}")[:HASH_LENGTH]
    text = _OUTSIDE_KEY.sub("_", unique_id.translate(_ASCII_LOWER)).strip("_")
    text = text[: room - HASH_LENGTH - 1].rstrip("_")
    return f"{domain}.{text}_{digest}" if text else f"{domain}.{digest}"
