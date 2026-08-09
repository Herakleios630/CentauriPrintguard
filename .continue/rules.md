# PrintGuard Agent Rules

## Scope
- Keep the current single-camera implementation unless the task explicitly requests multi-camera work.
- Do not change the catastrophe allowlist, confirmation thresholds, pause semantics, or automatic continue/stop behavior unless explicitly requested.

## Secrets
- RTSP credentials and complete secret URLs come only from environment variables.
- Never write secrets to YAML, logs, exceptions, commits, filenames, review metadata, or prompts.
- Apply `redact_url()` before logging or persisting any URL.

## Configuration
- `config.yaml` may contain structure, roles, labels, and environment-variable names.
- It must not contain resolved credentials or secret URL values.

## Code Style
- Log messages are German.
- Use `logging`, never `print()` in application code.
- Keep imports ordered: standard library, third-party, project-local.
- Preserve existing public APIs unless a change is required and tested.

## Architecture
- `PrinterClient` is the only printer-status source.
- Camera code must not query printer status.
- Each camera must own its reconnect state if multi-camera work is explicitly started.
- Preserve missing/stale evidence as `UNSICHER`; never silently convert it to `OK`.
