"""Configuration loading and validation."""

from pathlib import Path

import yaml


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
    if not isinstance(pause_timeout, (int, float)) or isinstance(pause_timeout, bool) or pause_timeout <= 0:
        raise ValueError("monitoring.pause_timeout muss größer als 0 sein.")
    if not isinstance(pause_cooldown, (int, float)) or isinstance(pause_cooldown, bool) or pause_cooldown < 0:
        raise ValueError("monitoring.pause_cooldown darf nicht negativ sein.")
    if not isinstance(status_refresh_interval, (int, float)) or isinstance(status_refresh_interval, bool) or status_refresh_interval <= 0:
        raise ValueError("monitoring.status_refresh_interval muss größer als 0 sein.")
    if not isinstance(status_stale_after, (int, float)) or isinstance(status_stale_after, bool) or status_stale_after < status_refresh_interval:
        raise ValueError("monitoring.status_stale_after muss mindestens dem Refresh-Intervall entsprechen.")
    if not isinstance(monitoring.get("dry_run", False), bool):
        raise ValueError("monitoring.dry_run muss true oder false sein.")
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
