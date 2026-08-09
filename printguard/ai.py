"""Ollama vision analysis and model lifecycle."""

import asyncio
import base64
import logging

import ollama

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist ein sicherheitsorientiertes Überwachungssystem für einen FDM-3D-Drucker.
Du erhältst bis zu vier zeitlich geordnete Bilder desselben Drucks. Das letzte Bild ist
AKTUELL. Prüfe immer das aktuelle Bild und vergleiche die Objektform, Position und
Neigung mit den älteren Bildern.

WICHTIGE AUSNAHME - DRUCKKOPF:
Der Druckkopf, die Düse und das Hotend sind bewegliche, erwartete Komponenten.
Ihre Position, Bewegung, Kontur, Schatten und wechselnde Verdeckung dürfen niemals
als Bewegung, Verschiebung, Layer-Shift, Umkippen oder Ablösung des Druckobjekts
bewertet werden. Vergleiche die Position des Druckobjekts nur anhand des Druckbetts,
seiner sichtbaren Standfläche, Kontur und Layerstruktur außerhalb des Druckkopfs.
Der Druckkopf selbst darf nur auf direkt sichtbare Materialfehler geprüft werden:
Spaghetti am Druckkopf, Stringing, Filamentklumpen, hängendes Filament oder eine
offensichtliche Düsen-/Extrusionsstörung.

AUTOMATISCH PAUSENRELEVANTE KATASTROPHEN:
- UMGEKIPPT: Das Bauteil ist sichtbar seitlich gekippt, liegt flach oder steht nicht mehr stabil.
- ABGELOEST: Das Bauteil liegt sichtbar neben seiner ursprünglichen Standfläche oder ist eindeutig vom Bett losgerissen.
- SPAGHETTI: Sichtbares ungeordnetes Filament liegt großflächig außerhalb der erwarteten Objektkontur.
- FILAMENT_OHNE_OBJEKT: Sichtbares Filament tritt großflächig aus, obwohl kein Zielobjekt mehr vorhanden ist.
- MATERIALKLUEMPEN: Ein großer sichtbarer Filamentklumpen sitzt am Druckkopf und gefährdet den Druckkopf.

Layer-Shift, Warping, Unterextrusion, Stringing, kleine Verformungen, schlechte Oberfläche
und einzelne Düsenprobleme sind keine automatischen Pausenfehler. Melde sie als UNSICHER.

Beachte: Der Druckkopf kann das Bauteil teilweise verdecken. Wenn die Objektgeometrie,
Standfläche oder mögliche Neigung wegen des Druckkopfs nicht sicher beurteilbar ist,
antworte UNSICHER, niemals OK und niemals ABGELOEST. Ein einzelnes unauffälliges Bild
widerlegt keinen Fehler in den älteren Bildern. Ein ABGELOEST- oder UMGEKIPPT-Befund
ist nur zulässig, wenn mindestens ein Bild einen positiven sichtbaren Beleg zeigt:
verschobene Objektposition, sichtbare leere ursprüngliche Standfläche, separat
liegendes Objekt oder eindeutige Materialreste außerhalb der Druckkopfzone.

Antworte ausschließlich mit genau einer Zeile in einem dieser Formate:
OK
UNSICHER: <kurze Begründung>
FEHLER: UMGEKIPPT
FEHLER: ABGELOEST
FEHLER: SPAGHETTI
FEHLER: FILAMENT_OHNE_OBJEKT
FEHLER: MATERIALKLUEMPEN
Keine weiteren Erklärungen."""


def analyze_frames(frames: list[tuple[str, bytes]], model: str, host: str) -> str:
    client = ollama.Client(host=host)
    images = [base64.b64encode(frame).decode("utf-8") for _, frame in frames]
    timeline = "\n".join(f"{index + 1}. {label}" for index, (label, _) in enumerate(frames))
    prompt = f"{SYSTEM_PROMPT}\n\nBildreihenfolge:\n{timeline}"
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": images}],
            options={"temperature": 0.1, "num_predict": 50},
        )
        result = response["message"]["content"].strip()
        log.debug(f"🤖 KI-Antwort: {result}")
        return result if result else "FEHLER: Leere Antwort"
    except Exception as exc:
        log.error(f"❌ Ollama-Fehler: {exc}")
        return "UNKNOWN: Ollama-Analyse nicht verfügbar"


def analyze_frame(frame_now: bytes, frame_before: bytes, model: str, host: str) -> str:
    """Keep the two-frame API available for callers and focused tests."""
    return analyze_frames([
        ("älteres Bild", frame_before),
        ("aktuelles Bild", frame_now),
    ], model, host)


def unload_ollama_model(model: str, host: str, timeout: float = 10) -> bool:
    try:
        ollama.Client(host=host).generate(model=model, keep_alive=0)
        log.info(f"🧠 Ollama-Modell aus VRAM entladen: {model}")
        return True
    except Exception as exc:
        log.warning(f"⚠️  Ollama-VRAM-Cleanup fehlgeschlagen: {exc}")
        return False


async def unload_ollama_model_async(model: str, host: str, timeout: float = 10) -> bool:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(unload_ollama_model, model, host, timeout),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning(f"⚠️  Ollama-VRAM-Cleanup überschreitet Timeout ({timeout:g}s).")
        return False


def normalize_verdict(verdict: str) -> str:
    normalized = verdict.strip()
    if normalized == "OK":
        return normalized
    if normalized.startswith("UNSICHER:") and normalized[9:].strip():
        return normalized
    if normalized.startswith("FEHLER: ") and normalized[8:].strip():
        return normalized
    return "UNKNOWN: Ungültige KI-Antwort"


def catastrophe_type(verdict: str) -> str | None:
    """Return the allow-listed catastrophe type that may trigger a pause."""
    if not verdict.startswith("FEHLER: "):
        return None
    category = verdict[8:].strip().split(":", 1)[0].strip().upper()
    allowed = {
        "UMGEKIPPT",
        "ABGELOEST",
        "SPAGHETTI",
        "FILAMENT_OHNE_OBJEKT",
        "MATERIALKLUEMPEN",
    }
    return category if category in allowed else None


async def check_ollama_startup(ai_config: dict, timeout: float = 180) -> bool:
    """Load the configured model and keep it resident for monitoring."""
    model = ai_config["model"]
    try:
        client = ollama.Client(host=ai_config["ollama_host"])
        log.info(f"🧠 Lade Ollama-Modell für Startprüfung: {model}")
        await asyncio.wait_for(
            asyncio.to_thread(
                client.generate,
                model=model,
                prompt="Bereit.",
                keep_alive=-1,
                options={"num_predict": 1},
            ),
            timeout=timeout,
        )
        log.info(f"✅ Ollama-Modell geladen und bereit: {model}")
        return True
    except asyncio.TimeoutError:
        log.warning(f"⚠️  Ollama-Modell lädt länger als {timeout:g}s: {model}")
        return False
    except Exception as exc:
        log.warning(f"⚠️  Ollama-Startprüfung fehlgeschlagen für {model}: {exc}")
        return False
