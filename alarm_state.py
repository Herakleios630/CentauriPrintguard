"""Latched alarm confirmation for persistent print defects."""

from dataclasses import dataclass, field
from typing import Any

from printguard.ai import catastrophe_type


@dataclass
class AlarmResult:
    """Result of feeding one AI verdict into the alarm state."""

    state: str
    action: str
    error_count: int
    confirmation_count: int


@dataclass
class AlarmState:
    """Keep a first defect latched while collecting confirmation frames."""

    confirmation_frames: int = 3
    required_errors: int = 2
    state: str = "IDLE"
    normal_reference: Any = None
    error_reference: Any = None
    frames: list[dict] = field(default_factory=list)
    error_count: int = 0
    unknown_count: int = 0
    ok_streak: int = 0
    clear_ok_count: int = 3
    catastrophe: str | None = None

    def reset(self) -> None:
        self.state = "IDLE"
        self.normal_reference = None
        self.error_reference = None
        self.frames.clear()
        self.error_count = 0
        self.unknown_count = 0
        self.ok_streak = 0
        self.catastrophe = None

    def observe(self, verdict: str, frame: Any, metadata: dict | None = None) -> AlarmResult:
        """Latch the first error and decide after the configured confirmation set."""
        entry = dict(metadata or {})
        entry.update({"frame": frame, "verdict": verdict})

        current_catastrophe = catastrophe_type(verdict)

        if self.state == "IDLE":
            if current_catastrophe is None:
                return AlarmResult("IDLE", "CONTINUE", 0, 0)
            self.state = "ALARM_PENDING"
            self.catastrophe = current_catastrophe
            self.error_reference = frame
            self.frames = [entry]
            self.error_count = 1
            self.unknown_count = 0
            self.ok_streak = 0
            return AlarmResult("ALARM_PENDING", "COLLECT", self.error_count, 1)

        if self.state != "ALARM_PENDING":
            return AlarmResult(self.state, "STOP", self.error_count, len(self.frames))

        if current_catastrophe != self.catastrophe:
            self.reset()
            return AlarmResult("IDLE", "RESET", 0, 0)

        self.frames.append(entry)
        if current_catastrophe == self.catastrophe:
            self.error_count += 1
            self.ok_streak = 0

        confirmation_count = len(self.frames)
        if self.error_count >= self.required_errors:
            self.state = "ALARM_CONFIRMED"
            return AlarmResult(self.state, "PAUSE", self.error_count, confirmation_count)
        if confirmation_count >= self.confirmation_frames and self.ok_streak >= self.clear_ok_count:
            self.state = "ALARM_CLEARED"
            return AlarmResult(self.state, "RESET", self.error_count, confirmation_count)
        return AlarmResult("ALARM_PENDING", "COLLECT", self.error_count, confirmation_count)

    def context(self) -> dict:
        """Return serializable alarm context without image bytes."""
        return {
            "state": self.state,
            "error_count": self.error_count,
            "unknown_count": self.unknown_count,
            "ok_streak": self.ok_streak,
            "confirmation_frames": len(self.frames),
            "normal_reference_available": self.normal_reference is not None,
            "error_reference_available": self.error_reference is not None,
            "catastrophe": self.catastrophe,
            "verdicts": [entry.get("verdict") for entry in self.frames],
        }
