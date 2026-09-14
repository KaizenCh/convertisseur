# -*- coding: utf-8 -*-
"""Safe execution and property access wrappers."""

from typing import Any, Callable, TypeVar, Optional

T = TypeVar("T")


def safe_call(func: Callable[..., T], default: T, *args: Any, **kwargs: Any) -> T:
    """Execute callable safely, returning default on exception."""
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


def safe_property(obj: Any, name: str, default: Any = None) -> Any:
    """Read attribute or editor property safely."""
    if obj is None:
        return default
    try:
        return getattr(obj, name)
    except Exception:
        try:
            if hasattr(obj, "get_editor_property"):
                return obj.get_editor_property(name)
        except Exception:
            pass
    return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    try:
        return bool(value)
    except Exception:
        return default
