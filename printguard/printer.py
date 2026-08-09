"""SDCP WebSocket client for the Centauri Carbon."""

import asyncio
import json
import logging
import time
import uuid

import websockets

log = logging.getLogger(__name__)
MACHINE_STATUS_NAMES = {0: "Idle", 1: "Printing", 2: "File Transferring", 3: "Calibrating", 4: "Device Testing"}
PRINT_STATUS_NAMES = {
    0: "Idle",
    1: "Homing",
    2: "Dropping",
    3: "Exposing",
    4: "Lifting",
    5: "Pausing",
    6: "Paused",
    7: "Stopping",
    8: "Stopped",
    9: "Complete",
    10: "File Checking",
    16: "Preparation",
    20: "Bed Leveling",
    21: "Leveling Preparation",
}


def format_status(value, names):
    if isinstance(value, list):
        value = value[0] if value else None
    return f"{names.get(value, 'Unknown')} ({value})"


class PrinterClient:
    def __init__(self, ip: str, port: int = 3030):
        self.url = f"ws://{ip}:{port}/websocket"
        self.ws = None
        self.mainboard_id = None
        self.current_status = None
        self.status_data = {}
        self.print_info = {}
        self.print_status = None
        self.print_error = None
        self.status_updated_at = None
        self.status_changed = asyncio.Event()
        self.command_events = {}
        self.command_acks = {}
        self.status_sequence = 0
        self.pause_active = False
        self.pause_baseline_status = None
        self.pause_status_seen = False
        self.reconnect_count = 0
        self.connection_drop_count = 0
        self.last_pause_ack = None
        self.last_pause_result = None

    async def connect(self, timeout: float = 15):
        log.info(f"🔌 Verbinde mit Drucker: {self.url}")
        self.mark_status_stale()
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            self.ws = await asyncio.wait_for(
                websockets.connect(self.url, ping_interval=30, ping_timeout=10),
                timeout=remaining,
            )
            for _ in range(20):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                data = self._parse_message(await asyncio.wait_for(self.ws.recv(), timeout=min(5, remaining)))
                self._update_status(data)
                if data and "MainboardID" in data:
                    self.mainboard_id = data["MainboardID"]
                    log.info(f"✅ Verbunden! MainboardID: {self.mainboard_id}")
                    await self._refresh_status_after_connect(timeout=min(5, max(0.1, deadline - asyncio.get_running_loop().time())))
                    return
            raise TimeoutError("Keine MainboardID innerhalb des Reconnect-Zeitlimits empfangen.")
        except asyncio.CancelledError:
            if self.ws:
                await self.ws.close()
            self.mark_status_stale()
            raise
        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as exc:
            if self.ws:
                await self.ws.close()
            self.mark_status_stale()
            raise TimeoutError(f"Drucker-Reconnect überschreitet {timeout:.0f}s: {exc}") from exc

    async def _refresh_status_after_connect(self, timeout: float = 5):
        """Request and consume a fresh status snapshot before listening normally."""
        request_id = str(uuid.uuid4())
        payload = {
            "Id": str(uuid.uuid4()),
            "Data": {
                "Cmd": 0,
                "Data": {},
                "RequestID": request_id,
                "MainboardID": self.mainboard_id,
                "TimeStamp": int(time.time()),
                "From": 0,
            },
            "Topic": f"sdcp/request/{self.mainboard_id}",
        }
        await self.ws.send(json.dumps(payload, separators=(",", ":")))
        log.info("📤 Fordere Status nach Verbindung aktiv an (Cmd 0).")

        deadline = asyncio.get_running_loop().time() + timeout
        ack = None
        status_seen = False
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            data = self._parse_message(raw)
            if not data:
                continue
            self._handle_response(data)
            self._handle_error(data)
            self._handle_notice(data)
            self._update_status(data)
            if "response" in data.get("Topic", "").lower():
                response = data.get("Data", {})
                response_data = response.get("Data", {}) if isinstance(response, dict) else {}
                if isinstance(response, dict) and response.get("RequestID") == request_id:
                    ack = response_data.get("Ack") if isinstance(response_data, dict) else None
            if "status" in data.get("Topic", "").lower():
                status_seen = True
            if ack is not None and status_seen:
                break

        if ack != 0 or not status_seen:
            self.mark_status_stale()
            raise TimeoutError(f"Status-Refresh nach Verbindung nicht bestätigt: Ack={ack}, Status={status_seen}.")
        log.info(
            f"✅ Status nach Verbindung aktualisiert: "
            f"Maschine={format_status(self.current_status, MACHINE_STATUS_NAMES)}, "
            f"Druck={format_status(self.print_status, PRINT_STATUS_NAMES)}"
        )

    @staticmethod
    def _normalize_status(value):
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _update_status(self, data):
        if not data or "status" not in data.get("Topic", "").lower():
            return
        status = data.get("Status", {})
        if not isinstance(status, dict):
            return
        previous_status, previous_print = self.current_status, self.print_status
        self.status_data = status
        self.status_sequence += 1
        self.status_updated_at = time.monotonic()
        if "CurrentStatus" in status:
            self.current_status = status["CurrentStatus"]
        self.print_info = status.get("PrintInfo", {})
        if isinstance(self.print_info, dict):
            self.print_status = self._normalize_status(self.print_info.get("Status", self.print_status))
            self.print_error = self._normalize_status(self.print_info.get("ErrorNumber"))
            if self.pause_active and self.print_status in (5, 6):
                if self.print_status == 5 or self.pause_baseline_status != 6:
                    self.pause_status_seen = True
        if self.current_status != previous_status or self.print_status != previous_print:
            self.status_changed.set()
            log.info(f"📡 Status geändert: Maschine {format_status(self.current_status, MACHINE_STATUS_NAMES)}, Druck {format_status(self.print_status, PRINT_STATUS_NAMES)}")

    def _handle_response(self, data):
        if "response" not in data.get("Topic", "").lower():
            return
        response = data.get("Data", {})
        if not isinstance(response, dict):
            return
        request_id, command = response.get("RequestID"), response.get("Data", {})
        if not request_id or not isinstance(command, dict) or command.get("Ack") is None:
            return
        self.command_acks[request_id] = command["Ack"]
        if request_id in self.command_events:
            self.command_events[request_id].set()
        log.info(f"📨 Antwort für RequestID {request_id}: Cmd={response.get('Cmd')}, Ack={command['Ack']}")

    def _handle_error(self, data):
        if "error" not in data.get("Topic", "").lower():
            return
        payload = data.get("Data", {})
        nested = payload.get("Data", {}) if isinstance(payload, dict) else {}
        code = nested.get("ErrorCode") if isinstance(nested, dict) else None
        self.print_error = self._normalize_status(code)
        log.error(f"🚨 Druckerfehler empfangen: ErrorCode={code}")

    def _handle_notice(self, data):
        if "notice" not in data.get("Topic", "").lower():
            return
        payload = data.get("Data", {})
        nested = payload.get("Data", {}) if isinstance(payload, dict) else {}
        if isinstance(nested, dict):
            log.info(f"📣 Druckerhinweis: Type={nested.get('Type')}, Message={nested.get('Message')}")
        else:
            log.info(f"📣 Druckerhinweis: {nested}")

    def _parse_message(self, raw):
        if raw == "ping":
            asyncio.create_task(self._send_pong())
            return None
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return None

    async def _send_pong(self):
        if self.ws:
            await self.ws.send("pong")

    async def listen_status(self):
        delay = 2
        while True:
            try:
                async for raw in self.ws:
                    data = self._parse_message(raw)
                    if data:
                        self._handle_response(data)
                        self._handle_error(data)
                        self._handle_notice(data)
                    self._update_status(data)
                self.connection_drop_count += 1
                log.warning(
                    "⚠️  WebSocket-Verbindung geschlossen ohne Ausnahme "
                    f"(Close-Code={self.ws.close_code}, Grund={self.ws.close_reason!r}); "
                    f"automatischer Reconnect #{self.connection_drop_count}."
                )
            except (websockets.ConnectionClosed, OSError) as exc:
                self.mark_status_stale()
                self.connection_drop_count += 1
                close_code = getattr(exc, "code", getattr(self.ws, "close_code", None))
                close_reason = getattr(exc, "reason", getattr(self.ws, "close_reason", None))
                log.warning(
                    "⚠️  WebSocket-Verbindung unterbrochen "
                    f"(Close-Code={close_code}, Grund={close_reason!r}, Fehler={exc}); "
                    f"automatischer Reconnect #{self.connection_drop_count}."
                )
            delay = min(delay, 30)
            log.info(f"🔄 Neuer Verbindungsversuch in {delay}s...")
            await asyncio.sleep(delay)
            try:
                await self.connect()
            except (OSError, TimeoutError, websockets.ConnectionClosed) as exc:
                log.error(f"❌ Reconnect fehlgeschlagen (nächster Versuch folgt): {exc}")
                delay = min(delay * 2, 30)
                continue
            self.reconnect_count += 1
            log.info("✅ Reconnect erfolgreich; Status wurde neu eingelesen.")
            delay = 2

    async def send_command(self, cmd: int, cmd_data=None) -> str:
        request_id = str(uuid.uuid4())
        payload = {"Id": str(uuid.uuid4()), "Data": {"Cmd": cmd, "Data": cmd_data or {}, "RequestID": request_id, "MainboardID": self.mainboard_id, "TimeStamp": int(time.time()), "From": 0}, "Topic": f"sdcp/request/{self.mainboard_id}"}
        self.command_events[request_id] = asyncio.Event()
        await self.ws.send(json.dumps(payload, separators=(",", ":")))
        log.info(f"📤 Sende Cmd {cmd} an {payload['Topic']}")
        return request_id

    async def request_status_refresh(self, timeout: float = 5):
        """Request a fresh status through the listener-owned WebSocket reader."""
        if not self._connection_is_open():
            self.mark_status_stale()
            raise ConnectionError("Keine aktive Druckerverbindung für Status-Refresh.")
        sequence_before = self.status_sequence
        request_id = await asyncio.wait_for(self.send_command(0), timeout=timeout)
        ack = await self.wait_for_ack(request_id, timeout)
        if ack != 0:
            self.mark_status_stale()
            raise TimeoutError(f"Status-Refresh nicht bestätigt: Ack={ack}.")
        deadline = asyncio.get_running_loop().time() + timeout
        while self.status_sequence == sequence_before:
            if asyncio.get_running_loop().time() >= deadline:
                self.mark_status_stale()
                raise TimeoutError("Status-Refresh bestätigt, aber keine neuen Statusdaten empfangen.")
            await asyncio.sleep(0.05)
        log.info(
            f"🔄 Status regelmäßig aktualisiert: "
            f"Maschine={format_status(self.current_status, MACHINE_STATUS_NAMES)}, "
            f"Druck={format_status(self.print_status, PRINT_STATUS_NAMES)}"
        )

    def _connection_is_open(self) -> bool:
        if self.ws is None:
            return False
        closed = getattr(self.ws, "closed", None)
        if closed is not None:
            return not closed
        state = getattr(self.ws, "state", None)
        state_name = getattr(state, "name", None)
        return state_name in (None, "OPEN")

    def mark_status_stale(self):
        self.status_updated_at = None

    def status_is_fresh(self, max_age: float) -> bool:
        return self.status_updated_at is not None and time.monotonic() - self.status_updated_at <= max_age

    async def wait_for_ack(self, request_id: str, timeout: float):
        event = self.command_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.command_events.pop(request_id, None)
        return self.command_acks.pop(request_id, None)

    def is_printing(self) -> bool:
        status = self.current_status[0] if isinstance(self.current_status, list) and self.current_status else self.current_status
        return status == 1

    def is_active_print(self, active_statuses: set[int] | list[int] | tuple[int, ...], max_age: float | None = None) -> bool:
        """Return whether the printer is in an explicitly approved print phase."""
        return (
            self.is_printing()
            and self.print_status in active_statuses
            and (max_age is None or self.status_is_fresh(max_age))
        )

    def is_paused(self) -> bool:
        return self.print_status in (5, 6)

    async def pause_print(self, timeout: float = 20) -> bool:
        if not self.is_printing():
            log.error(f"❌ Pause nicht gesendet: Druckstatus ist {self.current_status}.")
            return False
        self.status_changed.clear()
        self.pause_active = True
        self.pause_baseline_status = self.print_status
        self.pause_status_seen = False
        try:
            request_id = await asyncio.wait_for(self.send_command(129), timeout=timeout)
        except (asyncio.TimeoutError, OSError, websockets.ConnectionClosed) as exc:
            log.error(f"❌ Pausebefehl konnte nicht gesendet werden: {exc}")
            self.pause_active = False
            return False
        ack = await self.wait_for_ack(request_id, timeout)
        self.last_pause_ack = ack
        if ack != 0:
            self.pause_active = False
            self.last_pause_result = False
            log.error(f"❌ Pause vom Drucker nicht akzeptiert: Ack={ack}.")
            return False
        try:
            refresh_id = await asyncio.wait_for(self.send_command(0), timeout=timeout)
            if await self.wait_for_ack(refresh_id, timeout) != 0:
                log.warning("⚠️  Status-Refresh nicht bestätigt.")
        except (asyncio.TimeoutError, OSError, websockets.ConnectionClosed) as exc:
            log.warning(f"⚠️  Status-Refresh fehlgeschlagen: {exc}")
        deadline = asyncio.get_running_loop().time() + timeout
        while not (self.pause_status_seen and self.print_status in (5, 6)):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.pause_active = False
                self.last_pause_result = False
                log.error(f"❌ Pause nicht bestätigt: Druck={self.print_status}")
                return False
            try:
                await asyncio.wait_for(self.status_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                self.pause_active = False
                self.last_pause_result = False
                return False
            self.status_changed.clear()
        self.pause_active = False
        self.last_pause_result = True
        log.info(f"✅ Druck pausiert bestätigt (Druck={self.print_status}).")
        return True

    async def stop_print(self):
        await self.send_command(130)
