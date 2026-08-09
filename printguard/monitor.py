"""Monitoring orchestration for PrintGuard."""

import argparse
import asyncio
import logging
from collections import deque
from datetime import datetime

from alarm_state import AlarmState
from .ai import analyze_frames, catastrophe_type, check_ollama_startup, normalize_verdict, unload_ollama_model_async
from .camera import CameraCapture
from .configuration import load_config
from .printer import PrinterClient
from .review import rotate_logs, save_review_frames
from .stats import MonitorStats

log = logging.getLogger(__name__)


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
    listener = None
    try:
        await printer.connect()
        listener = asyncio.create_task(printer.listen_status())
        confirmed = await printer.pause_print(monitoring.get("pause_timeout", 20))
        log.info("✅ Manueller Pause-Test erfolgreich bestätigt." if confirmed else "❌ Manueller Pause-Test nicht bestätigt.")
    finally:
        if listener and not listener.done():
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass
        if printer.ws:
            await printer.ws.close()


async def main():
    log.info("=" * 60)
    log.info("🛡️  PrintGuard - 3D-Druck-Fehlererkennung")
    log.info("=" * 60)
    config = load_config()
    printer_config, ai_config = config["printer"], config["ai"]
    monitoring, logging_config = config["monitoring"], config.get("logging", {})
    check_interval = monitoring["check_interval"]
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
    listener = asyncio.create_task(printer.listen_status())
    camera = CameraCapture(printer_config["camera_url"])
    try:
        camera.open()
    except Exception:
        listener.cancel()
        if printer.ws:
            await printer.ws.close()
        raise
    await asyncio.sleep(2)
    if not printer.is_active_print(active_print_statuses):
        log.warning(
            f"⚠️  KI wartet auf echten Druck: "
            f"Maschine={printer.current_status}, Druck={printer.print_status}."
        )
    previous_frame = camera.grab_frame()
    review_frames = deque(maxlen=review_count)
    alarm = AlarmState(alarm_frames_count, alarm_errors, clear_ok_count=clear_ok_count)
    check_count = 0
    was_active_print = printer.is_active_print(active_print_statuses)

    try:
        while True:
            await asyncio.sleep(check_interval)
            check_count += 1
            try:
                current_frame = camera.grab_frame()
            except RuntimeError as exc:
                log.error(f"❌ Kamera-Fehler: {exc}")
                stats.camera_errors += 1
                recovered = False
                for attempt in range(1, 4):
                    try:
                        await asyncio.sleep(1)
                        camera.reconnect()
                        current_frame = camera.grab_frame()
                        log.info(f"✅ Kamera-Reconnect erfolgreich (Versuch {attempt}/3).")
                        recovered = True
                        break
                    except RuntimeError as reconnect_exc:
                        log.warning(
                            f"⚠️  Kamera-Reconnect {attempt}/3 fehlgeschlagen: "
                            f"{reconnect_exc}"
                        )
                if not recovered:
                    log.error("❌ Kamera bleibt nicht verfügbar; nächster Check versucht es erneut.")
                    continue
            stats.checks += 1
            active_print = printer.is_active_print(active_print_statuses)
            if not active_print:
                if was_active_print or alarm.state != "IDLE" or review_frames:
                    log.info(
                        f"⏭️  KI-Prüfung übersprungen: kein echter Druck "
                        f"(Maschine={printer.current_status}, Druck={printer.print_status})."
                    )
                was_active_print = False
                alarm.reset()
                review_frames.clear()
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
            entry = {"frame": current_frame, "check": check_count, "captured_at": datetime.now().isoformat(timespec="seconds"), "verdict": "UNKNOWN: Analyse ausstehend", "task_id": printer.print_info.get("TaskId"), "filename": printer.print_info.get("Filename"), "layer": printer.print_info.get("CurrentLayer"), "print_status": printer.print_status}
            review_frames.append(entry)
            try:
                evidence = list(review_frames)[-max(1, evidence_count):]
                verdict = await asyncio.wait_for(
                    asyncio.to_thread(
                        analyze_frames,
                        [(f"Bild {item['check']} ({item['captured_at']})", item["frame"]) for item in evidence],
                        ai_config["model"],
                        ai_config["ollama_host"],
                    ),
                    ai_config.get("timeout", 120),
                )
            except asyncio.TimeoutError:
                verdict = "UNKNOWN: Ollama-Analyse Timeout"
                log.error("❌ Ollama-Analyse überschreitet das Timeout.")
            verdict = normalize_verdict(verdict)
            entry["verdict"] = verdict
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
        camera.release()
        if not listener.done():
            listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass
        if printer.ws:
            await printer.ws.close()
        log.info("🏁 PrintGuard beendet.")
