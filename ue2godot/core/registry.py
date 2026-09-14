# -*- coding: utf-8 -*-
"""Generic AssetRegistry pattern with usage counts and back-references."""

from typing import Generic, TypeVar, Dict, Any, List

T = TypeVar("T")


class AssetRegistry(Generic[T]):
    """Generic registry tracking items, usage counts, and back-references."""

    def __init__(self):
        self._entries: Dict[str, Dict[str, Any]] = {}

    def register(self, key: str, info: Dict[str, Any], ref: str = "") -> Dict[str, Any]:
        if key not in self._entries:
            entry = dict(info)
            entry["key"] = key
            entry["usage_count"] = 0
            entry["references"] = []
            self._entries[key] = entry
        else:
            entry = self._entries[key]

        entry["usage_count"] += 1
        if ref and ref not in entry["references"]:
            entry["references"].append(ref)

        return entry

    def get(self, key: str) -> Dict[str, Any]:
        return self._entries.get(key, {})

    def contains(self, key: str) -> bool:
        return key in self._entries

    def all_entries(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._entries)
