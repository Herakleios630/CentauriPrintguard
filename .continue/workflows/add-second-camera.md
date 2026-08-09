# Add Second Camera

1. Confirm the Tapo account, RTSP path, mounting position, and desired view.
2. Add only an environment-variable reference to YAML; never add credentials.
3. Test `redact_url()` with credentials, IPv6, invalid input, and log/exception paths.
4. Preserve the existing primary-camera behavior and public `grab_frame()` return type.
5. Integrate secondary capture with independent reconnect and stale-frame tracking.
6. Send labeled, time-ordered views to the existing catastrophe-focused AI path.
7. Treat missing, stale, or conflicting views as `UNSICHER`, never implicit `OK`.
8. Test primary failure, secondary failure, reconnect, time skew, hidden object, and one-view spaghetti evidence.
9. Run a supervised dry-run before any overnight use.
