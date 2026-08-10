"""Dry-run image ring buffer and AI diagnostics."""

import json
import hashlib
from pathlib import Path


class DryRunDiagnostics:
    """Persist recent camera pairs and explainable AI responses."""

    def __init__(self, directory: str | Path, pair_count: int = 10):
        self.directory = Path(directory)
        self.pair_count = pair_count
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.directory / "analysis.jsonl"

    def save_pair(self, check: int, views: list[dict], captured_at: str) -> dict:
        slot = ((check - 1) % self.pair_count) + 1
        saved_views = []
        for view in views:
            role = view["camera_role"]
            image_name = f"pair_{slot:02d}_{role}.jpg"
            image_path = self.directory / image_name
            frame = view.get("frame")
            if frame is not None:
                image_path.write_bytes(frame)
            elif image_path.exists():
                image_path.unlink()
            saved_views.append({
                "file": image_name if frame is not None else None,
                "camera_role": role,
                "camera_label": view.get("camera_label"),
                "available": frame is not None,
                "captured_at": view.get("captured_at", captured_at),
                "age_seconds": view.get("age_seconds"),
            })
        return {"slot": slot, "views": saved_views}

    def save_analysis(self, check: int, captured_at: str, pair: dict, analysis: dict) -> None:
        prompt = analysis.get("prompt")
        prompt_file = None
        prompt_sha256 = None
        if prompt:
            prompt_path = self.directory / f"prompt_{check:06d}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            prompt_file = prompt_path.name
            prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        record = {
            "check": check,
            "captured_at": captured_at,
            "slot": pair["slot"],
            "views": pair["views"],
            "time_offset_seconds": analysis.get("time_offset_seconds"),
            "prompt_file": prompt_file,
            "prompt_sha256": prompt_sha256,
            "raw_response": analysis.get("raw_response"),
            "verdict": analysis.get("verdict"),
            "error": analysis.get("error"),
        }
        with self.jsonl_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        analysis_path = self.directory / f"analysis_{check:06d}.json"
        analysis_path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
