# PrintGuard Context

PrintGuard is a Python asyncio monitor for an Elegoo Centauri Carbon on Windows.

## Current State
- Single camera: the existing Centauri HTTP/MJPEG stream.
- Printer: SDCP WebSocket at the configured printer address.
- Ollama model: `qwen2.5vl:7b`.
- AI decisions are catastrophe-focused only.
- Pause-capable categories are controlled by the existing allowlist and alarm state.
- Active print analysis requires a fresh printer status and an approved print status.
- Status refresh uses SDCP `Cmd 0`; reconnects have bounded timeouts.

## Important Files
- `printguard/printer.py`: SDCP connection, status, commands, reconnect.
- `printguard/camera.py`: OpenCV capture, reconnect, URL redaction.
- `printguard/monitor.py`: status gate, camera loop, AI and pause orchestration.
- `printguard/ai.py`: Ollama prompt and verdict normalization.
- `alarm_state.py`: alarm confirmation state machine.
- `Konzept_2ndCamera.md`: future second-camera design; do not implement it unless requested.

## Validation
- Run `python -m compileall -q .` after Python edits.
- Run focused fake/status/alarm tests before any live printer test.
- Never send live printer commands during tests unless the user explicitly requests a supervised test.
