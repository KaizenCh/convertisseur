# -*- coding: utf-8 -*-
"""Result container pattern for robust diagnostic pipelines."""

from typing import Generic, TypeVar, Optional, Any

T = TypeVar("T")

class Result(Generic[T]):
    __slots__ = ("_value", "_error")

    def __init__(self, value: Optional[T] = None, error: Optional[str] = None):
        self._value = value
        self._error = error

    @property
    def is_ok(self) -> bool:
        return self._error is None

    @property
    def is_err(self) -> bool:
        return self._error is not None

    @property
    def value(self) -> Optional[T]:
        return self._value

    @property
    def error(self) -> Optional[str]:
        return self._error

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        return cls(value=value, error=None)

    @classmethod
    def err(cls, error: str) -> "Result[T]":
        return cls(value=None, error=error)

    def to_tuple(self) -> tuple[Optional[T], Optional[str]]:
        return (self._value, self._error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self._value,
            "error": self._error,
            "is_ok": self.is_ok
        }

    def __repr__(self) -> str:
        if self.is_ok:
            return f"Result.ok({self._value!r})"
        return f"Result.err({self._error!r})"
