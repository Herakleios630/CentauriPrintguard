"""Resolve alarm results into monitor actions."""

from dataclasses import dataclass

from alarm_state import AlarmResult


@dataclass(frozen=True)
class AlarmDecision:
    """Decision after applying pause cooldown to an alarm result."""

    action: str
    remaining_cooldown: float | None = None


def pause_cooldown_active(
    last_pause: float | None, now: float, cooldown: float
) -> bool:
    """Return whether another pause attempt must still be deferred."""
    return last_pause is not None and now - last_pause < cooldown


def resolve_alarm_action(
    result: AlarmResult,
    last_pause: float | None,
    now: float,
    cooldown: float,
) -> AlarmDecision:
    """Apply cooldown only to pause actions; preserve all other actions."""
    if result.action != "PAUSE" or not pause_cooldown_active(last_pause, now, cooldown):
        return AlarmDecision(result.action)
    return AlarmDecision(
        "DEFER_PAUSE",
        remaining_cooldown=cooldown - (now - last_pause),
    )