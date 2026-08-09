"""Camera frame capture."""

import contextlib
import logging
import os

import cv2

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _suppress_native_stderr():
    """Hide native decoder noise while preserving Python logging output."""
    stderr_fd = os.dup(2)
    null_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_fd, 2)
        yield
    finally:
        os.dup2(stderr_fd, 2)
        os.close(null_fd)
        os.close(stderr_fd)


class CameraCapture:
    """Read JPEG frames from an MJPEG camera stream."""

    def __init__(self, url: str):
        self.url = url
        self.cap = None

    def open(self):
        log.info(f"📷 Öffne Kamera-Stream: {self.url}")
        if self.cap:
            with _suppress_native_stderr():
                self.cap.release()
        with _suppress_native_stderr():
            self.cap = cv2.VideoCapture(self.url)
            opened = self.cap.isOpened()
        if not opened:
            with _suppress_native_stderr():
                self.cap.release()
            self.cap = None
            raise RuntimeError(f"Konnte Kamera-Stream nicht öffnen: {self.url}")
        log.info("✅ Kamera-Stream geöffnet.")

    def reconnect(self):
        """Close and reopen a broken MJPEG stream."""
        log.warning("🔄 Kamera-Stream wird neu verbunden.")
        self.release()
        self.open()

    def grab_frame(self) -> bytes:
        if not self.cap:
            raise RuntimeError("Kamera-Stream ist nicht geöffnet!")
        with _suppress_native_stderr():
            for _ in range(3):
                ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Konnte kein Frame von der Kamera lesen!")
        encoded, jpeg = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
        )
        if not encoded:
            raise RuntimeError("Konnte Kamera-Frame nicht als JPEG encodieren!")
        return jpeg.tobytes()

    def release(self):
        if self.cap:
            with _suppress_native_stderr():
                self.cap.release()
            self.cap = None
            log.info("📷 Kamera-Stream geschlossen.")
