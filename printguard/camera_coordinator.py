"""Coordinate independent capture and reconnect handling for camera streams."""

import asyncio
import logging
from dataclasses import dataclass

from .camera import CameraCapture

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameSnapshot:
    """Latest frame and timing metadata for one camera."""

    frame: bytes | None
    camera_role: str
    camera_label: str
    captured_at: str | None
    success_at: float | None
    age_seconds: float | None
    available: bool

    def as_view_entry(self) -> dict:
        return {
            "frame": self.frame,
            "camera_role": self.camera_role,
            "camera_label": self.camera_label,
            "captured_at": self.captured_at,
            "age_seconds": self.age_seconds,
            "available": self.available,
        }


class CameraCoordinator:
    """Capture the latest frame from each camera without coupling their failures."""

    def __init__(self, cameras: list[CameraCapture]):
        self.cameras = cameras

    async def capture_views(self) -> dict[str, bytes | None]:
        snapshots = await self.capture_snapshots()
        return {
            role: snapshot.frame
            for role, snapshot in snapshots.items()
        }

    async def capture_snapshots(self, analysis_time: float | None = None) -> dict[str, FrameSnapshot]:
        frames = await asyncio.gather(*(self._capture_camera(camera) for camera in self.cameras))
        if analysis_time is None:
            analysis_time = asyncio.get_running_loop().time()
        return {
            camera.role: FrameSnapshot(
                frame=frame,
                camera_role=camera.role,
                camera_label=getattr(camera, "label", camera.role),
                captured_at=getattr(camera, "last_captured_at", None),
                success_at=camera.last_success_at,
                age_seconds=(
                    max(0.0, analysis_time - camera.last_success_at)
                    if camera.last_success_at is not None
                    else None
                ),
                available=frame is not None,
            )
            for camera, frame in zip(self.cameras, frames)
        }

    async def _capture_camera(self, camera: CameraCapture) -> bytes | None:
        try:
            return await asyncio.to_thread(camera.grab_frame)
        except RuntimeError as exc:
            log.error(f"❌ {camera.label}-Fehler: {exc}")
            for attempt in range(1, 4):
                try:
                    await asyncio.sleep(1)
                    await asyncio.to_thread(camera.reconnect)
                    frame = await asyncio.to_thread(camera.grab_frame)
                    log.info(f"✅ {camera.label}-Reconnect erfolgreich (Versuch {attempt}/3).")
                    return frame
                except RuntimeError as reconnect_exc:
                    log.warning(
                        f"⚠️  {camera.label}-Reconnect {attempt}/3 fehlgeschlagen: "
                        f"{reconnect_exc}"
                    )
            log.error(
                f"❌ {camera.label} bleibt nicht verfügbar; nächster Check versucht es erneut."
            )
            return None

    def open_all(self) -> None:
        for camera in self.cameras:
            camera.open()

    def close_all(self) -> None:
        for camera in self.cameras:
            camera.release()