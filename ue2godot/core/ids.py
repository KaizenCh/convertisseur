# -*- coding: utf-8 -*-
"""Object identification and hashing utilities."""

import hashlib
from typing import Any


def object_path(obj: Any) -> str:
    """Extract a canonical path string from an Unreal object or generic object."""
    if obj is None:
        return ""
    try:
        return str(obj.get_path_name())
    except Exception:
        try:
            return str(obj.get_name())
        except Exception:
            return str(obj)


def object_name(obj: Any) -> str:
    """Extract object name."""
    if obj is None:
        return ""
    try:
        return str(obj.get_name())
    except Exception:
        return str(obj)


def object_name_from_path(path: str) -> str:
    """Extract asset name from path."""
    if not path:
        return "Asset"
    value = str(path)
    if "." in value:
        value = value.rsplit(".", 1)[1]
    if "/" in value:
        value = value.rsplit("/", 1)[1]
    return value


def class_name(obj: Any) -> str:
    """Extract class name."""
    if obj is None:
        return ""
    try:
        return str(obj.get_class().get_name())
    except Exception:
        return ""


def stable_id(prefix: str, path: str) -> str:
    """Generate a stable 16-character SHA1 ID."""
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    clean_prefix = "".join(c for c in prefix if c.isalnum() or c in ("_", "-"))
    return f"{clean_prefix}_{digest}" if clean_prefix else digest


def unique_filename_for_path(path: str, ext: str = "glb") -> str:
    """Generates a filename with SHA1 hash suffix to avoid collisions."""
    name = object_name_from_path(path)
    clean_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")) or "Asset"
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    ext_clean = ext.lstrip(".")
    return f"{clean_name}_{digest}.{ext_clean}"
