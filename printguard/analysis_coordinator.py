"""Prepare camera evidence for vision analysis."""

from collections.abc import Iterable


def select_labeled_frames(
    review_frames: Iterable[dict], evidence_count: int
) -> list[tuple[str, bytes]]:
    """Return the newest available camera frames in review order."""
    evidence = list(review_frames)[-max(1, evidence_count):]
    return [
        (
            f"{view['camera_label']} / Bild {entry['check']} / {view['captured_at']}",
            view["frame"],
        )
        for entry in evidence
        for view in entry["views"]
        if view["available"] and view["frame"] is not None
    ]