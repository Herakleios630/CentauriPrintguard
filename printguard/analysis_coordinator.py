"""Prepare camera evidence for vision analysis."""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from .ai import (
    analyze_frames,
    analyze_frames_diagnostic,
    extract_verdict,
    guard_diagnostic_verdict,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    """Technical result of one vision analysis request."""

    verdict: str
    diagnostics: dict | None = None


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


async def run_analysis(
    labeled_frames: list[tuple[str, bytes]],
    ai_config: dict,
    diagnostic: bool = False,
) -> AnalysisResult:
    """Run one bounded vision request and preserve diagnostic details."""
    timeout = ai_config.get("timeout", 120)
    try:
        if diagnostic:
            diagnostics = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_frames_diagnostic,
                    labeled_frames,
                    ai_config["model"],
                    ai_config["ollama_host"],
                ),
                timeout,
            )
            verdict = extract_verdict(diagnostics["raw_response"])
            return AnalysisResult(
                verdict=guard_diagnostic_verdict(verdict, diagnostics),
                diagnostics=diagnostics,
            )
        verdict = await asyncio.wait_for(
            asyncio.to_thread(
                analyze_frames,
                labeled_frames,
                ai_config["model"],
                ai_config["ollama_host"],
            ),
            timeout,
        )
        return AnalysisResult(verdict=verdict)
    except asyncio.TimeoutError:
        log.error("❌ Ollama-Analyse überschreitet das Timeout.")
        return AnalysisResult(
            verdict="UNKNOWN: Ollama-Analyse Timeout",
            diagnostics={
                "prompt": None,
                "raw_response": "",
                "error": "Ollama-Analyse Timeout",
            },
        )