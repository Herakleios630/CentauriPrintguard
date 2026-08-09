# Multi-Camera Implementer

Use only when the user explicitly starts the second-camera phase.

## Required Order
1. Read `Konzept_2ndCamera.md` and current sources.
2. Keep printer status single-sourced by `PrinterClient`.
3. If changing WebSocket message handling, use one reader/dispatcher for all `recv()` calls. `connect()` may wait for handshake events, but must not create a competing reader.
4. Add environment-based RTSP resolution and URL redaction.
5. Add labeled camera instances with independent reconnect state while preserving the single-camera API.
6. Pass time-labeled views to Ollama and preserve missing/stale/conflicting evidence as `UNSICHER`.
7. Add focused tests before any supervised live run.

Do not change alarm categories or thresholds as part of camera integration.
