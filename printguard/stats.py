"""Monitoring statistics."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MonitorStats:
    """Counters collected for the final run summary."""

    checks: int = 0
    ok: int = 0
    errors: int = 0
    unknown: int = 0
    camera_errors: int = 0
    reconnects: int = 0
    connection_drops: int = 0
    pause_attempts: int = 0
    started_at: datetime = field(default_factory=datetime.now)

    def record_verdict(self, verdict: str) -> None:
        if verdict == "OK":
            self.ok += 1
        elif verdict.startswith("FEHLER:"):
            self.errors += 1
        else:
            self.unknown += 1

    def summary(self, end_state: str) -> str:
        runtime = datetime.now() - self.started_at
        return (
            f"Laufzusammenfassung: Dauer={runtime}, Checks={self.checks}, "
            f"OK={self.ok}, Fehler={self.errors}, UNKNOWN={self.unknown}, "
            f"Kamera={self.camera_errors}, Reconnects={self.reconnects}, "
            f"Verbindungsabbrüche={self.connection_drops}, "
            f"Pauseversuche={self.pause_attempts}, Endzustand={end_state}"
        )
