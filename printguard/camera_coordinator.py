"""Coordinate independent capture and reconnect handling for camera streams."""

import asyncio
import logging

from .camera import CameraCapture

log = logging.getLogger(__name__)


class CameraCoordinator:
    """Capture the latest frame from each camera without coupling their failures."""

    def __init__(self, cameras: list[CameraCapture]):
        self.cameras = cameras

    async def capture_views(self) -> dict[str, bytes | None]:
        frames = await asyncio.gather(*(self._capture_camera(camera) for camera in self.cameras))
        return {
            camera.role: frame
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