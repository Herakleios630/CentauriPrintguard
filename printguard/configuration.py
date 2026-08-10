"""Configuration loading and validation."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def _validate_camera_url(url: str, name: str) -> None:
    if not isinstance(url, str) or not url:
        raise ValueError(f"{name} muss eine URL sein.")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https", "rtsp", "rtsps"} or not parts.hostname:
            raise ValueError
        if parts.scheme in {"rtsp", "rtsps"} and parts.port not in {None, 554}:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError(f"{name} hat ein ungültiges URL-Format.") from None


def _resolve_cameras(config: dict) -> None:
    cameras = config.get("cameras")
    if cameras is None:
        config["cameras"] = {
            "primary": {
                "label": "Frontansicht",
                "url": config["printer"]["camera_url"],
                "enabled": True,
            },
            "secondary": {"label": "Seitenansicht", "enabled": False},
        }
        return
    if not isinstance(cameras, dict):
        raise ValueError("Konfigurationsbereich cameras ist ungültig.")

    primary = cameras.get("primary", {})
    secondary = cameras.get("secondary", {})
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        raise ValueError("cameras.primary und cameras.secondary müssen YAML-Objekte sein.")
    primary_url = primary.get("url", config["printer"].get("camera_url"))
    if primary.get("enabled", True):
        _validate_camera_url(primary_url, "cameras.primary.url")
        primary["url"] = primary_url
    if not isinstance(primary.get("label", "Frontansicht"), str) or not primary.get("label", "Frontansicht"):
        raise ValueError("cameras.primary.label muss gesetzt sein.")
    if not isinstance(secondary.get("enabled", True), bool):
        raise ValueError("cameras.secondary.enabled muss true oder false sein.")
    if not isinstance(secondary.get("label", "Seitenansicht"), str) or not secondary.get("label", "Seitenansicht"):
        raise ValueError("cameras.secondary.label muss gesetzt sein.")
    if not secondary.get("enabled", True):
        return
    url_env = secondary.get("url_env")
    if not isinstance(url_env, str) or not url_env:
        raise ValueError("cameras.secondary.url_env muss gesetzt sein.")
    secondary_url = os.environ.get(url_env)
    if not secondary_url:
        raise ValueError(f"Umgebungsvariable {url_env} für cameras.secondary fehlt.")
    _validate_camera_url(secondary_url, "cameras.secondary RTSP-URL")
    secondary["url"] = secondary_url


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {config_path}")
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ValueError(f"Ungültige YAML-Konfiguration: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Die Konfiguration muss ein YAML-Objekt sein.")
    for section in ("printer", "ai", "monitoring"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Konfigurationsbereich fehlt oder ist ungültig: {section}")

    required = {
        "printer": ("ip", "ws_port", "camera_url"),
        "ai": ("model", "ollama_host"),
        "monitoring": ("check_interval", "consecutive_errors"),
    }
    for section, keys in required.items():
        missing = [key for key in keys if key not in config[section]]
        if missing:
            raise ValueError(f"Fehlende Konfigurationswerte in {section}: {', '.join(missing)}")

    _resolve_cameras(config)

    monitoring = config["monitoring"]
    if (
        not isinstance(monitoring["check_interval"], (int, float))
        or isinstance(monitoring["check_interval"], bool)
        or not isinstance(monitoring["consecutive_errors"], int)
        or isinstance(monitoring["consecutive_errors"], bool)
        or monitoring["check_interval"] <= 0
        or monitoring["consecutive_errors"] <= 0
    ):
        raise ValueError("check_interval und consecutive_errors müssen größer als 0 sein.")

    ai = config["ai"]
    for key in ("timeout", "startup_timeout", "unload_timeout"):
        defaults = {"timeout": 120, "startup_timeout": 180, "unload_timeout": 10}
        value = ai.get(key, defaults[key])
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"ai.{key} muss größer als 0 sein.")
    for key in ("unload_on_pause", "unload_on_exit"):
        if not isinstance(ai.get(key, True), bool):
            raise ValueError(f"ai.{key} muss true oder false sein.")

    positive_ints = ("review_image_count", "alarm_confirmation_frames", "alarm_required_errors", "evidence_frame_count", "alarm_clear_ok_count")
    alarm_confirmation_frames = monitoring.get("alarm_confirmation_frames", 3)
    alarm_required_errors = monitoring.get("alarm_required_errors", 2)
    for key in positive_ints:
        value = monitoring.get(key, {"review_image_count": 10,
                                     "alarm_confirmation_frames": 3,
                                     "alarm_required_errors": 2,
                                     "evidence_frame_count": 4,
                                     "alarm_clear_ok_count": 3}[key])
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"monitoring.{key} muss größer als 0 sein.")
    if alarm_required_errors > alarm_confirmation_frames:
        raise ValueError("monitoring.alarm_required_errors darf nicht größer sein als alarm_confirmation_frames.")

    pause_timeout = monitoring.get("pause_timeout", 20)
    pause_cooldown = monitoring.get("pause_cooldown", 60)
    status_refresh_interval = monitoring.get("status_refresh_interval", 10)
    status_stale_after = monitoring.get("status_stale_after", max(30, status_refresh_interval * 3))
    max_camera_time_offset = monitoring.get("max_camera_time_offset", 5)
    if not isinstance(pause_timeout, (int, float)) or isinstance(pause_timeout, bool) or pause_timeout <= 0:
        raise ValueError("monitoring.pause_timeout muss größer als 0 sein.")
    if not isinstance(pause_cooldown, (int, float)) or isinstance(pause_cooldown, bool) or pause_cooldown < 0:
        raise ValueError("monitoring.pause_cooldown darf nicht negativ sein.")
    if not isinstance(status_refresh_interval, (int, float)) or isinstance(status_refresh_interval, bool) or status_refresh_interval <= 0:
        raise ValueError("monitoring.status_refresh_interval muss größer als 0 sein.")
    if not isinstance(status_stale_after, (int, float)) or isinstance(status_stale_after, bool) or status_stale_after < status_refresh_interval:
        raise ValueError("monitoring.status_stale_after muss mindestens dem Refresh-Intervall entsprechen.")
    if not isinstance(max_camera_time_offset, (int, float)) or isinstance(max_camera_time_offset, bool) or max_camera_time_offset <= 0:
        raise ValueError("monitoring.max_camera_time_offset muss größer als 0 sein.")
    if not isinstance(monitoring.get("dry_run", False), bool):
        raise ValueError("monitoring.dry_run muss true oder false sein.")
    if not isinstance(monitoring.get("dry_run_diagnostics", True), bool):
        raise ValueError("monitoring.dry_run_diagnostics muss true oder false sein.")
    dry_run_picture_pairs = monitoring.get("dry_run_picture_pairs", 10)
    if not isinstance(dry_run_picture_pairs, int) or isinstance(dry_run_picture_pairs, bool) or dry_run_picture_pairs <= 0:
        raise ValueError("monitoring.dry_run_picture_pairs muss größer als 0 sein.")
    if not isinstance(monitoring.get("pending_review_enabled", True), bool):
        raise ValueError("monitoring.pending_review_enabled muss true oder false sein.")
    active_print_statuses = monitoring.get("active_print_statuses", [2, 3, 4])
    if (
        not isinstance(active_print_statuses, list)
        or not active_print_statuses
        or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in active_print_statuses)
    ):
        raise ValueError("monitoring.active_print_statuses muss eine nichtleere Liste positiver Statuswerte sein.")

    logging_config = config.get("logging", {})
    if not isinstance(logging_config, dict):
        raise ValueError("Konfigurationsbereich logging ist ungültig.")
    retention_days = logging_config.get("retention_days", 30)
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days <= 0:
        raise ValueError("logging.retention_days muss größer als 0 sein.")
    return config
