"""Review frame export and log retention."""

import json
import logging
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def rotate_logs(log_dir: str | Path, prefix: str, retention_days: int) -> int:
    directory = Path(log_dir)
    if not directory.exists():
        return 0
    cutoff = time.time() - retention_days * 24 * 60 * 60
    pattern = re.compile(rf"^{re.escape(prefix)}-\d{{8}}\.log$")
    removed = 0
    for path in directory.iterdir():
        if path.is_file() and pattern.match(path.name) and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def save_review_frames(
    frames: deque, review_dir: str | Path, pause_time: datetime, context: dict | None = None
) -> Path | None:
    if not frames:
        log.warning("⚠️  Keine Analysebilder für den menschlichen Gegencheck vorhanden.")
        return None
    task_id = str((context or {}).get("task_id") or "no-task")
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)[:48]
    output_dir = Path(review_dir) / f"{pause_time:%Y%m%d_%H%M%S}_{safe_task_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = []
    for index, entry in enumerate(frames, start=1):
        views = entry.get("views")
        if not views:
            views = [{
                "frame": entry["frame"],
                "camera_role": "primary",
                "camera_label": "Frontansicht",
                "captured_at": entry["captured_at"],
                "available": True,
            }]
        for view in views:
            if not view.get("available") or view.get("frame") is None:
                metadata.append({
                    "file": None,
                    "camera_role": view.get("camera_role"),
                    "camera_label": view.get("camera_label"),
                    "available": False,
                    "captured_at": view.get("captured_at"),
                    "age_seconds": view.get("age_seconds"),
                    "time_offset_seconds": entry.get("time_offset_seconds"),
                    "check": entry["check"],
                    "verdict": entry["verdict"],
                })
                continue
            role = re.sub(r"[^A-Za-z0-9_-]+", "_", view.get("camera_role", "camera"))
            image_name = f"frame_{index:02d}_{role}.jpg"
            (output_dir / image_name).write_bytes(view["frame"])
            metadata.append({
                "file": image_name,
                "camera_role": view.get("camera_role"),
                "camera_label": view.get("camera_label"),
                "available": True,
                "captured_at": view.get("captured_at"),
                "age_seconds": view.get("age_seconds"),
                "time_offset_seconds": entry.get("time_offset_seconds"),
                "check": entry["check"],
                "verdict": entry["verdict"],
                "task_id": entry.get("task_id"),
                "filename": entry.get("filename"),
                "layer": entry.get("layer"),
                "print_status": entry.get("print_status"),
            })
    (output_dir / "metadata.json").write_text(
        json.dumps({"saved_at": pause_time.isoformat(timespec="seconds"),
                    "frame_count": len(metadata), "pause_context": context or {},
                    "frames": metadata}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.error(f"📸 {len(metadata)} Bilder für Gegencheck gespeichert: {output_dir}")
    return output_dir
