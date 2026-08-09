"""Camera frame capture."""

import contextlib
import logging
import os
import threading
from urllib.parse import urlsplit, urlunsplit

import cv2

log = logging.getLogger(__name__)
_native_io_lock = threading.Lock()


def redact_url(url: str) -> str:
    """Hide user info before a URL is written to logs or errors."""
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, f"{parts.username or ''}:***@{host}", parts.path, parts.query, parts.fragment))
    except ValueError:
        return "<invalid-url>"


@contextlib.contextmanager
def _suppress_native_stderr():
    """Hide native decoder noise while preserving Python logging output."""
    with _native_io_lock:
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

    def __init__(self, url: str, role: str = "primary", label: str = "Kamera"):
        self.url = url
        self.role = role
        self.label = label
        self.cap = None

    def open(self):
        safe_url = redact_url(self.url)
        log.info(f"📷 Öffne {self.label}-Stream: {safe_url}")
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
            raise RuntimeError(f"Konnte {self.label}-Stream nicht öffnen: {safe_url}")
        log.info(f"✅ {self.label}-Stream geöffnet.")

    def reconnect(self):
        """Close and reopen a broken MJPEG stream."""
        log.warning(f"🔄 {self.label}-Stream wird neu verbunden.")
        self.release()
        self.open()

    def grab_frame(self) -> bytes:
        if not self.cap:
            raise RuntimeError(f"{self.label}-Stream ist nicht geöffnet!")
        with _suppress_native_stderr():
            for _ in range(3):
                ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError(f"Konnte kein Frame von {self.label} lesen!")
        encoded, jpeg = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
        )
        if not encoded:
            raise RuntimeError(f"Konnte {self.label}-Frame nicht als JPEG encodieren!")
        return jpeg.tobytes()

    def release(self):
        if self.cap:
            with _suppress_native_stderr():
                self.cap.release()
            self.cap = None
            log.info(f"📷 {self.label}-Stream geschlossen.")
