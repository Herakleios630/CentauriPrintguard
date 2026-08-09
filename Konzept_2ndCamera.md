# Konzept: Zweite Kamera fuer PrintGuard

Stand: 2026-08-09

## 1. Ziel

PrintGuard soll den Elegoo Centauri Carbon mit zwei zeitnahen Kamerabildern ueberwachen:

- **Primary / Frontansicht:** bestehende Centauri-Kamera
- **Secondary / Seitenansicht:** Tapo C100 ueber RTSP

Der Schwerpunkt bleibt die Erkennung sichtbarer Druckkatastrophen:

- umgekipptes Bauteil
- abgeloestes Bauteil
- grossflaechiges Spaghetti-Material
- Filamentextrusion ohne sichtbares Objekt
- gefaehrliche Materialklumpen

Die zweite Kamera soll vor allem kleine Objekte am Rand, insbesondere vorne rechts, aus einem zweiten Blickwinkel sichtbar machen.

## 2. Bewusste Grenzen

Diese Phase veraendert nicht:

- die bestehenden Alarm-Schwellenwerte
- die bestehende Katastrophen-Allowlist
- die automatische Continue- oder Stop-Logik
- die Entscheidung, welche Kategorien pausieren duerfen
- die Druckersteuerung ausser der bereits vorhandenen Pause-/Statuslogik

Die zweite Perspektive verbessert die Evidenz. Sie aktiviert keine neuen Fehlerkategorien.

## 3. Aktueller technischer Stand

Vor der Mehrkamera-Integration bereits vorhanden:

- Python-asyncio-Monitor
- Centauri-Kamera ueber HTTP-MJPEG
- Ollama mit `qwen2.5vl:7b`
- SDCP-WebSocket zum Drucker
- aktiver Druckstatus ueber `CurrentStatus=1` plus `PrintInfo.Status`
- regelmaessiger `Cmd 0`-Status-Refresh
- Status-Refresh nach WebSocket-Reconnect
- stale-status-Sperre fuer KI- und Pauseentscheidungen
- begrenztes Reconnect-Timeout
- Katastrophen-Allowlist und bestehende Alarmzustandsmaschine
- Review-Bilder und Metadaten

Diese Statusquelle bleibt zentral. Die Kameras duerfen keine eigene Druckerstatuslogik einfuehren.

## 4. Sicherheits- und Secret-Regeln

### 4.1 RTSP-Zugangsdaten

Die Tapo-Zugangsdaten duerfen nicht in:

- `config.yaml`
- Git oder Commits
- Logs und Terminalausgaben
- Exceptions
- Review-Metadaten
- Bilddateinamen
- Ollama-Prompts

Die konkrete RTSP-URL wird ueber eine Windows-Umgebungsvariable bereitgestellt, zum Beispiel:

```powershell
$env:CENTAURI_CAMERA_2_RTSP = 'rtsp://USER:PASSWORD@CAMERA-IP:554/STREAM-PATH'
```

Fuer einen dauerhaften Betrieb kann die Variable als Windows-Benutzervariable oder in der VS-Code-Launch-Umgebung gesetzt werden. Die Variable darf im Repository nicht eingetragen werden.

### 4.2 Tapo-Account und Streampfad

Vor der Implementierung muessen am Geraet bestaetigt werden:

1. eigener Kamera-Account fuer den RTSP-Zugriff
2. IP-Adresse der C100
3. exakter RTSP-Pfad
4. verwendeter Stream, bevorzugt der fuer die Erkennung ausreichend scharfe Stream
5. Erreichbarkeit vom PrintGuard-Rechner

Das Tapo-Cloud-Konto oder ein vermuteter Streampfad darf nicht automatisch als RTSP-Konfiguration verwendet werden.

### 4.3 URL-Redaktion

Eine zentrale `redact_url()`-Funktion maskiert Benutzername und Passwort, bevor eine URL geloggt oder in einen Fehlertext uebernommen wird. Beispiel:

```text
rtsp://USER:***@192.0.2.10:554/stream1
```

Die rohe URL darf nur fuer den tatsaechlichen OpenCV-Aufruf im Speicher verwendet werden.

## 5. Zielkonfiguration

Die bestehende Konfiguration soll kompatibel bleiben. Ein moegliches Ziel ist:

```yaml
printer:
  ip: "10.0.0.63"
  ws_port: 3030
  camera_url: "http://10.0.0.63:3031/video"

cameras:
  primary:
    label: "Frontansicht"
    url: "http://10.0.0.63:3031/video"
    enabled: true
  secondary:
    label: "Seitenansicht"
    url_env: "CENTAURI_CAMERA_2_RTSP"
    enabled: true
```

Empfehlung fuer die Migration:

- `printer.camera_url` zunaechst weiter unterstuetzen.
- `cameras.primary` optional machen.
- Falls beide Angaben vorhanden sind, eine klare Prioritaetsregel definieren und loggen.
- `cameras.secondary.url_env` enthaelt nur den Namen der Umgebungsvariable, nie deren Wert.
- Eine fehlende Secondary-Variable muss mit einer klaren Konfigurationsmeldung behandelt werden.

Offene Entscheidung: Soll eine fehlende Secondary-Kamera den Start verhindern oder soll ein Primary-only-Modus erlaubt werden? Empfehlung: im ersten Testbetrieb explizit konfigurierbar, im sicheren Nachtbetrieb fehlende Evidenz nicht als `OK` behandeln.

## 6. Kamera-Abstraktion

### 6.1 Benannte Kameraobjekte

Der Monitor erzeugt zwei getrennte `CameraCapture`-Instanzen. Jede Instanz braucht mindestens:

- stabilen Namen: `primary` oder `secondary`
- sichtbares Label: `Frontansicht` oder `Seitenansicht`
- URL intern
- redigierte URL fuer Logs
- letzten Frame
- Zeitpunkt des letzten erfolgreichen Reads
- Verfuegbarkeitsstatus
- Read-Fehlerzaehler
- Reconnectzaehler
- letzten redigierten Fehler

### 6.2 Unabhaengige Reconnects

Ein Kameraausfall darf die andere Kamera nicht blockieren.

- Primary und Secondary werden getrennt geoeffnet.
- Reconnects werden pro Kamera durchgefuehrt.
- Ein Read-Fehler einer Kamera wird als fehlende Evidenz markiert.
- Ein alter Frame darf nur verwendet werden, wenn sein Alter explizit erfasst und innerhalb einer konfigurierten Grenze liegt.
- Ein alter oder fehlender Secondary-Frame wird nicht stillschweigend als aktuelle Seitenansicht ausgegeben.

### 6.3 OpenCV und RTSP

Die bestehende `CameraCapture`-Klasse akzeptiert grundsaetzlich eine URL. Vor der Mehrkamera-Integration pruefen:

- ob HTTP-MJPEG und RTSP denselben OpenCV-Pfad verwenden koennen
- welcher Backendname tatsaechlich verwendet wird
- ob der Tapo-Stream unter Windows stabil gelesen wird
- ob Timeout und Reconnect bei RTSP ausreichend reagieren

Keine globale Kameraausnahme darf den Monitor ohne Logging beenden.

## 7. Multi-View-Aufnahme

Pro aktiver Druckpruefung:

1. aktuellen Primary-Frame aufnehmen
2. aktuellen Secondary-Frame aufnehmen
3. Aufnahmezeitpunkt je Frame speichern
4. Zeitversatz berechnen
5. beide verfuegbaren Bilder mit stabilen Labels an die KI uebergeben
6. Verfuegbarkeit und Bildalter im Analysekontext protokollieren

Zielstruktur fuer die Bilduebergabe:

```text
Bild 401 / Frontansicht / 2026-08-10T08:15:10
Bild 401 / Seitenansicht / 2026-08-10T08:15:11
```

Die Reihenfolge muss stabil sein. Ein fehlendes Bild darf nicht durch ein unmarkiertes oder falsch beschriftetes Bild ersetzt werden.

### 7.1 Zeitversatz

Der maximal erlaubte Zeitversatz muss konfigurierbar sein. Bei zu grossem Versatz:

- beide Bilder trotzdem getrennt fuer Review speichern
- den Multi-View-Aufruf als unvollstaendige Evidenz markieren
- nicht behaupten, beide Ansichten zeigten denselben Zeitpunkt
- bei der KI `UNSICHER` bevorzugen, wenn die Perspektiven nicht sicher vergleichbar sind

### 7.2 Druckstatus und Referenzpunkt

Die bestehende Druckerstatuslogik bleibt massgeblich:

- `16`, `20`, `21` und `1` gelten als Vorbereitung/Leveling/Homing.
- `13` sowie die bereits freigegebenen aktiven Statuswerte schalten die KI frei.
- Nach `16/20/21 -> 13` werden beide Kamera-Referenzen gemeinsam neu gesetzt.
- Bei WebSocket-Abbruch wird der Status stale; alte Statusdaten duerfen keine Analyse freischalten.
- Nach Reconnect und bestaetigtem `Cmd 0` wird der aktuelle Status erneut bewertet.

## 8. Ollama-Vertrag

### 8.1 Prompt-Anpassung

Der Prompt soll explizit enthalten:

- `Frontansicht` und `Seitenansicht` sind unterschiedliche Kameraperspektiven.
- Beide Bilder gemeinsam, nicht isoliert, bewerten.
- Das kleine Objekt vorne rechts gezielt suchen.
- Druckkopf, Nozzle und Hotend als erwartete bewegliche Komponenten behandeln.
- Objektkontur, Standflaeche und Material ausserhalb der erwarteten Objektzone vergleichen.
- Ein einzelner feiner String ist nicht automatisch katastrophales Spaghetti.
- Verdeckte Bereiche nicht als sicher `OK` oder sicherer Fehler bewerten.
- Bei fehlender, zu alter oder widerspruechlicher Evidenz `UNSICHER` zurueckgeben.

### 8.2 Antwortformat

Das bestehende einzeilige Antwortformat bleibt erhalten:

```text
OK
UNSICHER: kurze Begruendung
FEHLER: UMGEKIPPT
FEHLER: ABGELOEST
FEHLER: SPAGHETTI
FEHLER: FILAMENT_OHNE_OBJEKT
FEHLER: MATERIALKLUEMPEN
```

Die genaue Allowlist wird nicht in dieser Phase erweitert.

### 8.3 Fehlende Kamera

Eine fehlende Kamera ist kein normales `OK`. Der technische Zustand muss getrennt vom KI-Verdict geloggt werden. Fuer den Livebetrieb ist vorab festzulegen, ob eine einzelne sichere Ansicht noch analysiert werden darf oder ob der Ergebnisstatus zwingend `UNSICHER` sein muss.

Empfehlung fuer die erste Implementierung: Bei fehlender oder zu alter Secondary-Evidenz `UNSICHER` beziehungsweise Review, keine automatische Entwarnung aus der Primary-Ansicht allein.

## 9. Review-Daten

### 9.1 Dateinamen

Neue Multi-View-Ordner sollen die bestehende Struktur weiterverwenden. Bilder muessen Kamera und Sequenz enthalten, zum Beispiel:

```text
frame_01_front.jpg
frame_01_side.jpg
frame_02_front.jpg
frame_02_side.jpg
```

Bestehende Einzelkamera-Reviews bleiben lesbar.

### 9.2 Metadaten

Pro Bild oder Kameraeintrag speichern:

- Kameraname und Label
- `captured_at`
- Alter beim Ollama-Aufruf
- Sequenz-/Checknummer
- Frame verfuegbar ja/nein
- redigierte Quelle oder URL-Label, keine Credentials
- Druckstatus und Statussequenz
- TaskId, Dateiname und Layer, soweit vorhanden
- Multi-View-Zeitversatz
- Rohantwort und normalisiertes Verdict

Kameraausfall, WebSocket-Ausfall und KI-Fehler muessen unterscheidbar bleiben.

## 10. Implementierungsreihenfolge fuer morgen

### Schritt 1: Bestand pruefen

Aktuelle Versionen lesen und die bereits implementierte Status-/Reconnect-Logik nicht versehentlich ueberschreiben:

- `config.yaml`
- `printguard/configuration.py`
- `printguard/camera.py`
- `printguard/printer.py`
- `printguard/monitor.py`
- `printguard/ai.py`
- `printguard/review.py`
- `requirements.txt`

### Schritt 2: Konfiguration und Redaction

- `cameras`-Struktur optional ergaenzen.
- Env-Variable aufloesen und validieren.
- `redact_url()` einfuehren.
- Tests fuer fehlende Variable, ungueltige URL und Secret-Redaktion schreiben.

### Schritt 3: Kameraobjekte

- Kamera-Rolle und Label in `CameraCapture` aufnehmen.
- Primary und Secondary getrennt oeffnen.
- Reconnect und Fehlerzaehler trennen.
- Keine Credentials loggen.

### Schritt 4: Multi-View-Monitor

- Beide Kameras pro Check aufnehmen.
- Zeitversatz und Bildalter erfassen.
- Bei aktivem, frischem Druckstatus beide Referenzen gemeinsam setzen.
- Bei Kameraausfall die Evidenz korrekt als unvollstaendig markieren.

### Schritt 5: KI und Review

- gelabelte Bilder gemeinsam an Ollama uebergeben
- Prompt um Perspektivkontext erweitern
- Multi-View-Review speichern
- bestehende Katastrophen-Allowlist und Alarmzustandsmaschine unveraendert lassen

### Schritt 6: Tests und Validierung

- Syntax-/Editorpruefung
- Konfigurations- und Secret-Tests
- Kamera-Fakes fuer beide Kameras
- Einzelkameraausfall und Reconnect
- Ollama-Mock fuer Reihenfolge und Labels
- Statuswechsel und WebSocket-Reconnect
- bestehende Alarmmatrix

### Schritt 7: Beaufsichtigter Betrieb

- zuerst Kamera-Streams ohne automatischen Druckerbefehl pruefen
- danach Dry-Run oder beobachteter Live-Lauf
- keine unbeaufsichtigte Nachtfreigabe beim ersten Multi-View-Test
- Review-Bilder und Logs manuell kontrollieren
- erst nach erfolgreicher Abnahme fuer Nachtbetrieb verwenden

## 11. Testmatrix

| Bereich | Test | Erwartung |
|---|---|---|
| Konfiguration | Primary-only mit alter `printer.camera_url` | bleibt kompatibel |
| Konfiguration | Secondary-Env fehlt | klare Konfigurationsmeldung |
| Sicherheit | RTSP-URL mit Passwort wird geloggt | Passwort maskiert |
| Kamera | beide Streams verfuegbar | beide Frames werden erfasst |
| Kamera | Secondary faellt aus | Primary bleibt aktiv, Evidenz unvollstaendig |
| Kamera | Primary faellt aus | Secondary bleibt aktiv, kein blindes `OK` |
| Kamera | Reconnect | nur betroffene Kamera wird neu verbunden |
| Frames | grosser Zeitversatz | Evidenz als unvollstaendig markiert |
| KI | Labels Front/Seite | Reihenfolge und Labels korrekt |
| KI | kleines Objekt vorne rechts | beide Perspektiven werden beruecksichtigt |
| KI | Druckkopf verdeckt Objekt | `UNSICHER`, sofern kein anderer positiver Beleg |
| KI | normale einzelne Faser | kein automatisches Katastrophenverdict |
| KI | sichtbares grossflaechiges Spaghetti | bestehendes Katastrophenverdict moeglich |
| Status | `16/20/21 -> 13` | KI startet nach Referenzreset |
| Status | WebSocket-Abbruch | Status stale, danach Cmd-0-Refresh |
| Status | Refresh scheitert | keine Analyse auf altem Status |
| Review | beide Kameras | getrennte Dateien und Metadaten |
| Alarm | bestehende Matrix | unveraendert erfolgreich |

## 12. Abschlusskriterien

Die Phase ist erst abgeschlossen, wenn:

- beide Kameras konfiguriert und mit stabilen Labels erreichbar sind
- RTSP-Credentials in keiner Ausgabe oder Datei erscheinen
- beide Kameras unabhaengig reconnecten koennen
- Bildalter und Zeitversatz gespeichert werden
- Ollama beide Perspektiven gemeinsam erhaelt
- fehlende Evidenz nicht als `OK` behandelt wird
- `Status=13` auch nach WebSocket-Reconnect erkannt wird
- kleine Randobjekte in beiden Ansichten gezielt beruecksichtigt werden
- bestehende Katastrophen-Alarmmatrix unveraendert funktioniert
- alle fokussierten Tests erfolgreich sind
- ein beaufsichtigter Testlauf ohne unerwartete Druckeraktion abgeschlossen ist

## 13. Offene Entscheidungen am Start von morgen

1. Exakte Tapo-RTSP-URL und Streamqualitaet.
2. Montageposition und Blickwinkel der C100.
3. Muss der Betrieb bei Ausfall einer Kamera auf `UNSICHER` gehen oder darf eine Einzelansicht weiter analysiert werden?
4. Soll Primary-only abwaertskompatibel dauerhaft erlaubt sein oder nur fuer Tests?
5. Welcher maximale Zeitversatz zwischen beiden Bildern ist akzeptabel?
6. Soll die erste Multi-View-Phase standardmaessig `dry_run: true` verwenden?

Empfehlung: Erst nach Beantwortung dieser sechs Punkte implementieren und danach einen beaufsichtigten Testlauf starten.
