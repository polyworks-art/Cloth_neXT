"""Typed, UI-independent Cloth NeXt errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Mapping


class ErrorCategory(Enum):
    USER_INPUT = auto()
    SCENE_VALIDATION = auto()
    SOLVER_INSTALLATION = auto()
    SOLVER_CONNECTION = auto()
    PROTOCOL_COMPATIBILITY = auto()
    SIMULATION = auto()
    CACHE = auto()
    UPDATE = auto()
    DEPENDENCY = auto()
    INTERNAL = auto()


def _freeze_context(context: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Create a deterministic, safe-to-log context representation."""
    return tuple(sorted((str(key), repr(value)) for key, value in context.items()))


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    category: ErrorCategory
    user_message: str
    technical_message: str
    recommended_action: str
    recoverable: bool = False
    context: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    original_exception_type: str | None = None

    @classmethod
    def create(
        cls,
        *,
        category: ErrorCategory,
        user_message: str,
        technical_message: str,
        recommended_action: str,
        recoverable: bool = False,
        context: Mapping[str, Any] | None = None,
        exception: BaseException | None = None,
    ) -> "ErrorRecord":
        return cls(
            category=category,
            user_message=user_message,
            technical_message=technical_message,
            recommended_action=recommended_action,
            recoverable=recoverable,
            context=_freeze_context(context or {}),
            original_exception_type=type(exception).__name__ if exception else None,
        )


class ClothNextError(Exception):
    def __init__(self, record: ErrorRecord) -> None:
        super().__init__(record.technical_message)
        self.record = record

