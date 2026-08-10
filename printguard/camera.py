"""Camera frame capture."""

import contextlib
import logging
import os
import threading
import time
from datetime import datetime
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

    def __init__(self, url: str, role: str = "primary", label: str = "Kamera", max_frame_age: float = 3.0):
        self.url = url
        self.role = role
        self.label = label
        self.max_frame_age = max_frame_age
        self.cap = None
        self.available = False
        self.last_frame = None
        self.last_success_at = None
        self.last_captured_at = None
        self.read_errors = 0
        self.reconnect_count = 0
        self.last_error = None
        self._frame_lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._reader_thread = None
        self._reader_error = None

    def open(self):
        safe_url = redact_url(self.url)
        log.info(f"📷 Öffne {self.label}-Stream: {safe_url}")
        self._stop_reader()
        with _suppress_native_stderr():
            self.cap = cv2.VideoCapture(self.url)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            opened = self.cap.isOpened()
        if not opened:
            with _suppress_native_stderr():
                self.cap.release()
            self.cap = None
            self.available = False
            raise RuntimeError(f"Konnte {self.label}-Stream nicht öffnen: {safe_url}")
        self.available = True
        self.last_error = None
        self._reader_error = None
        with self._frame_lock:
            self.last_frame = None
            self.last_success_at = None
            self.last_captured_at = None
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"camera-reader-{self.role}",
            daemon=True,
        )
        self._reader_thread.start()
        log.info(f"✅ {self.label}-Stream geöffnet.")

    def _stop_reader(self):
        self._reader_stop.set()
        reader = self._reader_thread
        cap = self.cap
        if cap:
            with _suppress_native_stderr():
                cap.release()
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)
            if reader.is_alive():
                log.warning(
                    f"⚠️  {self.label}-Reader beendet sich nicht innerhalb des Stop-Timeouts."
                )
        self._reader_thread = None
        self.cap = None

    def _reader_loop(self):
        while not self._reader_stop.is_set():
            cap = self.cap
            if cap is None:
                break
            with _suppress_native_stderr():
                ret, frame = cap.read()
            if self._reader_stop.is_set():
                break
            if not ret or frame is None:
                self.available = False
                self.read_errors += 1
                self._reader_error = f"Konnte kein Frame von {self.label} lesen"
                self._reader_stop.wait(0.1)
                continue
            encoded, jpeg = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90]
            )
            if not encoded:
                self.available = False
                self.read_errors += 1
                self._reader_error = f"Konnte {self.label}-Frame nicht als JPEG encodieren"
                continue
            frame_bytes = jpeg.tobytes()
            with self._frame_lock:
                self.last_frame = frame_bytes
                self.last_success_at = time.monotonic()
                self.last_captured_at = datetime.now().isoformat(timespec="milliseconds")
            self.available = True
            self.last_error = None
            self._reader_error = None

    def reconnect(self):
        """Close and reopen a broken MJPEG stream."""
        log.warning(f"🔄 {self.label}-Stream wird neu verbunden.")
        self.reconnect_count += 1
        self.release()
        self.open()

    def grab_frame(self) -> bytes:
        if not self.cap or not self._reader_thread:
            self.available = False
            raise RuntimeError(f"{self.label}-Stream ist nicht geöffnet!")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self._frame_lock:
                frame = self.last_frame
                frame_time = self.last_success_at
            if (
                frame is not None
                and frame_time is not None
                and time.monotonic() - frame_time <= self.max_frame_age
            ):
                self.available = True
                return frame
            if self._reader_error:
                break
            time.sleep(0.01)
        self.available = False
        self.last_error = self._reader_error or f"Kein aktuelles Frame von {self.label} verfügbar"
        raise RuntimeError(f"{self.last_error}!")

    def release(self):
        had_stream = self.cap is not None or self._reader_thread is not None
        self._stop_reader()
        if had_stream:
            self.available = False
            log.info(f"📷 {self.label}-Stream geschlossen.")
