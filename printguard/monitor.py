"""Monitoring orchestration for PrintGuard."""

import argparse
import asyncio
import logging
from collections import deque
from datetime import datetime

from alarm_state import AlarmState
from .ai import analyze_frames, analyze_frames_diagnostic, catastrophe_type, check_ollama_startup, extract_verdict, normalize_verdict, unload_ollama_model_async
from .camera import CameraCapture
from .configuration import load_config
from .diagnostics import DryRunDiagnostics
from .printer import PrinterClient
from .review import rotate_logs, save_review_frames
from .stats import MonitorStats

log = logging.getLogger(__name__)


async def _capture_camera(camera: CameraCapture) -> bytes | None:
    """Read one frame and reconnect only the camera that failed."""
    try:
        return await asyncio.to_thread(camera.grab_frame)
    except RuntimeError as exc:
        log.error(f"❌ {camera.label}-Fehler: {exc}")
        for attempt in range(1, 4):
            try:
                await asyncio.sleep(1)
                await asyncio.to_thread(camera.reconnect)
                frame = await asyncio.to_thread(camera.grab_frame)
                log.info(f"✅ {camera.label}-Reconnect erfolgreich (Versuch {attempt}/3).")
                return frame
            except RuntimeError as reconnect_exc:
                log.warning(
                    f"⚠️  {camera.label}-Reconnect {attempt}/3 fehlgeschlagen: "
                    f"{reconnect_exc}"
                )
        log.error(f"❌ {camera.label} bleibt nicht verfügbar; nächster Check versucht es erneut.")
        return None


async def _capture_views(cameras: list[CameraCapture]) -> dict[str, bytes | None]:
    frames = await asyncio.gather(*(_capture_camera(camera) for camera in cameras))
    return {camera.role: frame for camera, frame in zip(cameras, frames)}


def parse_args():
    parser = argparse.ArgumentParser(description="KI-Überwachung für den Centauri Carbon")
    parser.add_argument("--test-pause", action="store_true", help="Cmd 129 senden und die Pause bestätigen")
    return parser.parse_args()


async def manual_pause_test():
    config = load_config()
    printer_config, monitoring = config["printer"], config["monitoring"]
    if monitoring.get("dry_run", False):
        log.warning("🧪 DRY-RUN: Manueller Pause-Test simuliert; kein Cmd 129 gesendet.")
        return
    printer = PrinterClient(printer_config["ip"], printer_config["ws_port"])
    try:
        await printer.connect()
        confirmed = await printer.pause_print(monitoring.get("pause_timeout", 20))
        log.info("✅ Manueller Pause-Test erfolgreich bestätigt." if confirmed else "❌ Manueller Pause-Test nicht bestätigt.")
    finally:
        await printer.close()


async def main():
    log.info("=" * 60)
    log.info("🛡️  PrintGuard - 3D-Druck-Fehlererkennung")
    log.info("=" * 60)
    config = load_config()
    printer_config, ai_config = config["printer"], config["ai"]
    monitoring, logging_config = config["monitoring"], config.get("logging", {})
    check_interval = monitoring["check_interval"]
    status_refresh_interval = monitoring.get("status_refresh_interval", 10)
    status_stale_after = monitoring.get("status_stale_after", max(30, status_refresh_interval * 3))
    max_camera_time_offset = monitoring.get("max_camera_time_offset", 5)
    alarm_frames_count = monitoring.get("alarm_confirmation_frames", 3)
    alarm_errors = monitoring.get("alarm_required_errors", 2)
    pause_timeout = monitoring.get("pause_timeout", 20)
    pause_cooldown = monitoring.get("pause_cooldown", 60)
    dry_run = monitoring.get("dry_run", False)
    review_dir = monitoring.get("review_dir", "review_frames")
    review_count = monitoring.get("review_image_count", 10)
    evidence_count = monitoring.get("evidence_frame_count", 4)
    pending_review_enabled = monitoring.get("pending_review_enabled", True)
    clear_ok_count = monitoring.get("alarm_clear_ok_count", 3)
    active_print_statuses = set(monitoring.get("active_print_statuses", [2, 3, 4]))
    unload_on_pause = ai_config.get("unload_on_pause", True)
    unload_on_exit = ai_config.get("unload_on_exit", True)
    unload_timeout = ai_config.get("unload_timeout", 10)
    dry_run_diagnostics = None
    if dry_run and monitoring.get("dry_run_diagnostics", True):
        dry_run_diagnostics = DryRunDiagnostics(
            monitoring.get("dry_run_picture_dir", "tests/pictures"),
            monitoring.get("dry_run_picture_pairs", 10),
        )
    secondary_config = config["cameras"].get("secondary", {})
    if not secondary_config.get("enabled", True) or not secondary_config.get("url"):
        raise RuntimeError("Secondary-Kamera ist nicht aktiviert oder nicht konfiguriert.")

    removed = rotate_logs(logging_config.get("directory", "logs"), logging_config.get("prefix", "printguard"), logging_config.get("retention_days", 30))
    if removed:
        log.info(f"🧹 {removed} alte Logdatei(en) entfernt.")
    stats = MonitorStats()
    state, last_pause = "STARTING", None

    def set_state(new_state):
        nonlocal state
        if new_state != state:
            log.info(f"🔄 Monitorzustand: {state} -> {new_state}")
            state = new_state

    log.info(f"⚙️  Modus: {'DRY-RUN (keine SDCP-Befehle)' if dry_run else 'LIVE'}")
    await check_ollama_startup(ai_config, ai_config.get("startup_timeout", 180))
    printer = PrinterClient(printer_config["ip"], printer_config["ws_port"])
    await printer.connect()
    set_state("MONITORING")
    camera_configs = config["cameras"]
    cameras = [
        CameraCapture(
            camera_configs["primary"]["url"],
            role="primary",
            label=camera_configs["primary"].get("label", "Frontansicht"),
        ),
        CameraCapture(
            camera_configs["secondary"]["url"],
            role="secondary",
            label=camera_configs["secondary"].get("label", "Seitenansicht"),
        ),
    ]
    try:
        for camera in cameras:
            camera.open()
    except Exception:
        for camera in cameras:
            camera.release()
        await printer.close()
        raise
    await asyncio.sleep(2)
    if not printer.is_active_print(active_print_statuses):
        log.warning(
            f"⚠️  KI wartet auf echten Druck: "
            f"Maschine={printer.current_status}, Druck={printer.print_status}."
        )
    initial_views = await _capture_views(cameras)
    if any(frame is None for frame in initial_views.values()):
        for camera in cameras:
            camera.release()
        await printer.close()
        raise RuntimeError("Beide Kamera-Streams müssen für den Start verfügbar sein.")
    previous_frame = initial_views["primary"]
    review_frames = deque(maxlen=review_count)
    alarm = AlarmState(alarm_frames_count, alarm_errors, clear_ok_count=clear_ok_count)
    check_count = 0
    was_active_print = printer.is_active_print(active_print_statuses, status_stale_after)
    last_status_refresh = asyncio.get_running_loop().time() - status_refresh_interval

    try:
        while True:
            await asyncio.sleep(check_interval)
            check_count += 1
            now = asyncio.get_running_loop().time()
            if now - last_status_refresh >= status_refresh_interval:
                try:
                    await printer.request_status_refresh(timeout=min(status_refresh_interval, 5))
                    last_status_refresh = now
                except Exception as exc:
                    printer.mark_status_stale()
                    log.warning(f"⚠️  Regelmäßiger Status-Refresh fehlgeschlagen: {exc}")
            current_views = await _capture_views(cameras)
            current_frame = current_views["primary"]
            captured_at = datetime.now().isoformat(timespec="seconds")
            analysis_time = asyncio.get_running_loop().time()
            capture_times = {
                camera.role: camera.last_success_at
                for camera in cameras
            }
            if all(capture_times[camera.role] is not None for camera in cameras):
                time_offset = abs(capture_times["primary"] - capture_times["secondary"])
            else:
                time_offset = None
            view_entries = [
                {
                    "frame": current_views[camera.role],
                    "camera_role": camera.role,
                    "camera_label": camera.label,
                    "captured_at": camera.last_captured_at,
                    "age_seconds": (
                        max(0.0, analysis_time - capture_times[camera.role])
                        if capture_times[camera.role] is not None
                        else None
                    ),
                    "available": current_views[camera.role] is not None,
                }
                for camera in cameras
            ]
            pair = None
            if dry_run_diagnostics is not None:
                try:
                    pair = dry_run_diagnostics.save_pair(check_count, view_entries, captured_at)
                except OSError as exc:
                    log.error(f"❌ Dry-Run-Bilder konnten nicht gespeichert werden: {exc}")
            if current_frame is None or current_views["secondary"] is None:
                stats.camera_errors += 1
                verdict = "UNSICHER: Kameraevidenz unvollständig"
            else:
                verdict = None
            stats.checks += 1
            active_print = printer.is_active_print(active_print_statuses, status_stale_after)
            if not active_print:
                if was_active_print or alarm.state != "IDLE" or review_frames:
                    log.info(
                        f"⏭️  KI-Prüfung übersprungen: kein echter Druck "
                        f"(Maschine={printer.current_status}, Druck={printer.print_status})."
                    )
                was_active_print = False
                alarm.reset()
                review_frames.clear()
                if dry_run_diagnostics is not None and pair is not None:
                    try:
                        dry_run_diagnostics.save_analysis(
                            check_count,
                            captured_at,
                            pair,
                            {
                                "prompt": None,
                                "raw_response": "",
                                "verdict": "UNSICHER: Kein aktiver Druck",
                                "error": "Keine KI-Analyse außerhalb eines aktiven Drucks",
                                "time_offset_seconds": time_offset,
                            },
                        )
                    except OSError as exc:
                        log.error(f"❌ Dry-Run-Diagnose konnte nicht gespeichert werden: {exc}")
                previous_frame = current_frame
                continue
            if not was_active_print:
                log.info(
                    f"▶️  Echter Druck erkannt (PrintInfo.Status={printer.print_status}); "
                    "neuer Kamera-Referenzpunkt."
                )
                alarm.reset()
                review_frames.clear()
                previous_frame = current_frame
                was_active_print = True
                continue
            was_active_print = True
            entry = {
                "frame": current_frame,
                "views": view_entries,
                "multi_view_complete": (
                    current_frame is not None
                    and current_views["secondary"] is not None
                    and time_offset is not None
                    and time_offset <= max_camera_time_offset
                ),
                "time_offset_seconds": time_offset,
                "analysis_max_age_seconds": max(
                    (analysis_time - capture_times[camera.role])
                    for camera in cameras
                    if capture_times[camera.role] is not None
                ) if any(capture_times[camera.role] is not None for camera in cameras) else None,
                "check": check_count,
                "captured_at": captured_at,
                "verdict": "UNKNOWN: Analyse ausstehend",
                "task_id": printer.print_info.get("TaskId"),
                "filename": printer.print_info.get("Filename"),
                "layer": printer.print_info.get("CurrentLayer"),
                "print_status": printer.print_status,
            }
            review_frames.append(entry)
            if verdict is None and time_offset is not None and time_offset > max_camera_time_offset:
                verdict = f"UNSICHER: Kamera-Zeitversatz {time_offset:.1f}s überschreitet {max_camera_time_offset:g}s"
            if verdict is None:
                analysis_result = None
                try:
                    evidence = list(review_frames)[-max(1, evidence_count):]
                    labeled_frames = [
                        (
                            f"{view['camera_label']} / Bild {item['check']} / {view['captured_at']}",
                            view["frame"],
                        )
                        for item in evidence
                        for view in item["views"]
                        if view["available"]
                    ]
                    if dry_run_diagnostics is not None:
                        analysis_result = await asyncio.wait_for(
                            asyncio.to_thread(
                                analyze_frames_diagnostic,
                                labeled_frames,
                                ai_config["model"],
                                ai_config["ollama_host"],
                            ),
                            ai_config.get("timeout", 120),
                        )
                        verdict = extract_verdict(analysis_result["raw_response"])
                    else:
                        verdict = await asyncio.wait_for(
                            asyncio.to_thread(
                                analyze_frames,
                                labeled_frames,
                                ai_config["model"],
                                ai_config["ollama_host"],
                            ),
                            ai_config.get("timeout", 120),
                        )
                except asyncio.TimeoutError:
                    verdict = "UNKNOWN: Ollama-Analyse Timeout"
                    log.error("❌ Ollama-Analyse überschreitet das Timeout.")
                    analysis_result = {
                        "prompt": None,
                        "raw_response": "",
                        "error": "Ollama-Analyse Timeout",
                    }
            verdict = normalize_verdict(verdict)
            entry["verdict"] = verdict
            if dry_run_diagnostics is not None and pair is not None:
                analysis_result = analysis_result or {
                    "prompt": None,
                    "raw_response": "",
                    "error": "Keine KI-Analyse durchgeführt",
                }
                analysis_result.update({
                    "verdict": verdict,
                    "time_offset_seconds": time_offset,
                })
                try:
                    dry_run_diagnostics.save_analysis(
                        check_count, captured_at, pair, analysis_result
                    )
                except OSError as exc:
                    log.error(f"❌ Dry-Run-Diagnose konnte nicht gespeichert werden: {exc}")
            stats.record_verdict(verdict)
            if alarm.state == "IDLE" and catastrophe_type(verdict) is not None:
                alarm.normal_reference = previous_frame
            result = alarm.observe(verdict, current_frame, entry)
            if result.action == "CONTINUE":
                log.info(f"   ✅ {verdict}")
            elif result.action == "COLLECT":
                log.warning(f"   ⚠️  Alarmprüfung: {verdict} ({result.error_count} Fehler, {result.confirmation_count}/{alarm_frames_count} Bilder)")
                if pending_review_enabled and result.confirmation_count == 1:
                    pending_context = {"reason": verdict, "alarm": alarm.context(), "pending": True}
                    save_review_frames(deque(review_frames, maxlen=review_count), review_dir, datetime.now(), context=pending_context)
            elif result.action == "RESET":
                log.info(f"   ✅ Alarm verworfen: {result.error_count}/{result.confirmation_count} Bestätigungsfehler.")
                alarm.reset()
            elif result.action == "PAUSE":
                pause_time = datetime.now()
                context = {"reason": verdict, "error_count": result.error_count, "task_id": printer.print_info.get("TaskId"), "filename": printer.print_info.get("Filename"), "current_layer": printer.print_info.get("CurrentLayer"), "total_layer": printer.print_info.get("TotalLayer"), "current_status": printer.current_status, "print_status": printer.print_status, "temperature_nozzle": printer.status_data.get("TempOfNozzle", "?"), "captured_at": pause_time.isoformat(timespec="seconds"), "dry_run": dry_run, "alarm": alarm.context()}
                try:
                    save_review_frames(deque(alarm.frames, maxlen=review_count), review_dir, pause_time, context=context)
                except OSError as exc:
                    log.error(f"❌ Gegencheck-Bilder konnten nicht gespeichert werden: {exc}")
                now = asyncio.get_running_loop().time()
                if last_pause is not None and now - last_pause < pause_cooldown:
                    log.error(f"⏳ Pause-Cooldown aktiv; nächster Versuch in {pause_cooldown - (now - last_pause):.1f}s.")
                    if dry_run:
                        alarm.reset()
                        set_state("MONITORING")
                        previous_frame = current_frame
                        continue
                    set_state("ERROR")
                    break
                last_pause = now
                stats.pause_attempts += 1
                set_state("PAUSING")
                confirmed = True if dry_run else await printer.pause_print(pause_timeout)
                if confirmed:
                    set_state("PAUSED")
                    log.error("⏸️  DRUCK PAUSIERT!")
                    if unload_on_pause:
                        await unload_ollama_model_async(ai_config["model"], ai_config["ollama_host"], unload_timeout)
                else:
                    set_state("ERROR")
                    log.error("❌ DRUCKPAUSE NICHT BESTÄTIGT!")
                context.update({"pause_ack": printer.last_pause_ack, "pause_confirmed": confirmed, "current_status_after": printer.current_status, "print_status_after": printer.print_status})
                try:
                    save_review_frames(review_frames, review_dir, pause_time, context=context)
                except OSError as exc:
                    log.error(f"❌ Aktualisierte Gegencheck-Metadaten konnten nicht gespeichert werden: {exc}")
                if dry_run:
                    log.warning("🧪 DRY-RUN: Simulierte Pause abgeschlossen; Überwachung läuft weiter.")
                    alarm.reset()
                    set_state("MONITORING")
                    previous_frame = current_frame
                    continue
                log.error("🛑 PrintGuard beendet. Bitte Druck manuell prüfen!")
                break
            previous_frame = current_frame
    except KeyboardInterrupt:
        log.info("👋 Manuell beendet (Ctrl+C).")
    finally:
        if state not in ("PAUSED", "ERROR"):
            set_state("STOPPING")
        stats.reconnects = printer.reconnect_count
        stats.connection_drops = printer.connection_drop_count
        log.info(stats.summary(state))
        if unload_on_exit:
            await unload_ollama_model_async(ai_config["model"], ai_config["ollama_host"], unload_timeout)
        for camera in cameras:
            camera.release()
        await printer.close()
        log.info("🏁 PrintGuard beendet.")
