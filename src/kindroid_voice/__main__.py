import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import sys
from threading import Event
import time

from PyQt6.QtCore import QLockFile, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtNetwork import QNetworkAccessManager
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QSpinBox,
    QPlainTextEdit, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)
import requests

from .audio import LocalAudio, STT_MODELS
from .chat_view import ChatView
from .keyword_spotter import ConversationSpotter
from .level_meter import LevelMeter
from .playback_controls import PlaybackControls
from .presets import Playback, Recipe, RECORDING_EXTENSIONS, write_settings
from .provider import HISTORY_LOOKBACK_HOURS, read_history, stream_reply
from .session import CallInput, VoiceError, conversation
from .stt_chunks import ConversationTurns, PauseChunks, TriggerGate


def read_credentials(path):
    values = {}
    try:
        with path.open(encoding="utf-8-sig") as config:
            for line in config:
                name, separator, raw = line.partition("=")
                name = name.strip()
                if separator and name in ("KINDROID_API_KEY", "KINDROID_AI_ID"):
                    parts = shlex.split(raw, comments=True)
                    if len(parts) > 1:
                        raise ValueError
                    values[name] = parts[0] if parts else ""
    except (OSError, ValueError):
        raise VoiceError("Check the .env file next to Start Kindroid Voice.bat.") from None
    if not all(values.get(name, "").strip() for name in ("KINDROID_API_KEY", "KINDROID_AI_ID")):
        raise VoiceError("Fill in KINDROID_API_KEY and KINDROID_AI_ID in .env, then click Call.")
    return values["KINDROID_API_KEY"], values["KINDROID_AI_ID"]


class VoiceWorker(QThread):
    stage = pyqtSignal(str)
    chat_message = pyqtSignal(str, str, str, str)
    history_page = pyqtSignal(object)
    transcript = pyqtSignal(str, int)
    progress = pyqtSignal(int, int)
    playback_progress = pyqtSignal(int, int)
    mic_level = pyqtSignal(float, int, int, float)
    meter_levels = pyqtSignal(float, float)
    output_levels = pyqtSignal(float, float)
    detector_result = pyqtSignal(str, str, bool)
    chunk_progress = pyqtSignal(int, int)
    recording_ended = pyqtSignal(str)
    trigger_activity = pyqtSignal(object)
    turn_finished = pyqtSignal(str)

    def __init__(self, audio, action, *, key="", kin_id="", text="", recipe=None, settings=None,
                 pause_seconds=4.0, threshold_dbfs=-50, chunk_seconds=1.0, paragraph_seconds=2.0, triggers=(),
                 listening_threshold_dbfs=-60, acoustic_threshold=None, initial_pause_seconds=4.0, sources=(),
                 call_input=None, history_guard=None, destination=None):
        super().__init__()
        self.audio = audio
        self.key = key
        self.kin_id = kin_id
        self.stop = Event()
        self.finish_recording = Event()
        self.recording_summary = ""
        self.pause_seconds = pause_seconds
        self.initial_pause_seconds = initial_pause_seconds
        self.threshold_dbfs = threshold_dbfs
        self.chunk_seconds = chunk_seconds
        self.paragraph_seconds = paragraph_seconds
        self.triggers = triggers
        self.listening_threshold_dbfs = listening_threshold_dbfs
        self.acoustic_threshold = acoustic_threshold
        self.outcome = "Stopped"
        self.action = action
        self.text = text
        self.recipe = recipe
        self.settings = settings
        self.destination = destination
        self.created_voice = None
        self.sources = sources
        self.call_input = call_input
        self.history_guard = history_guard
        self.imported = []

    def run(self):
        try:
            if self.action == "load":
                self.audio.voice.load_voice(self.settings, self.stop, self.stage.emit)
                if not self.stop.is_set():
                    self.outcome = "Pocket TTS ready"
                return
            if self.action == "unload":
                self.audio.voice.engine.unload()
                self.outcome = "Pocket TTS off"
                return
            if self.action == "import":
                total = len(self.sources)
                self.progress.emit(0, total)
                for source in self.sources:
                    if self.stop.is_set():
                        break
                    try:
                        name = self.audio.voice.library.import_recording(source, self.stop)
                    except VoiceError as exc:
                        self.outcome = f"Import stopped at {Path(source).name} · {len(self.imported)} of {total} ready. {exc}"
                        return
                    if name is None:
                        break
                    self.imported.append(name)
                    self.progress.emit(len(self.imported), total)
                count = len(self.imported)
                self.outcome = (f"{count} of {total} recordings ready" if count == total
                                else f"Import stopped · {count} of {total} recordings ready")
                return
            if self.action == "clone":
                self.created_voice = self.audio.voice.build(self.recipe, self.stop, self.stage.emit, self.progress.emit)
                if self.created_voice:
                    self.outcome = f"Saved preset · {self.recipe.name}"
                return
            if self.action == "STT":
                self.run_dictation_test()
                return
            if self.action in ("TTS", "MP3"):
                self.audio.voice.load_voice(self.settings, self.stop, self.stage.emit)
                if self.stop.is_set():
                    return
                if self.action == "MP3":
                    saved = self.audio.voice.export_mp3(self.text, self.settings, self.destination, self.stop,
                                                        self.stage.emit, self.playback_progress.emit)
                    if saved is not None:
                        self.outcome = f"Saved MP3 · {saved}"
                    return
                self.audio.voice.output_meter = self.output_levels.emit
                self.audio.voice.speak(self.text, self.stop, self.stage.emit, self.playback_progress.emit)
                if not self.stop.is_set():
                    self.outcome = "Playback finished"
                return
            if self.action == "history":
                with requests.Session() as http:
                    rows, more = read_history(http.get, self.key, self.kin_id, self.history_guard, self.stop)
                if not self.stop.is_set():
                    self.history_page.emit(rows)
                    if more:
                        self.outcome = f"Recent history · {len(rows)} shown · newer messages outside this page"
                    elif rows:
                        self.outcome = f"Recent history · {len(rows)} shown"
                    else:
                        self.outcome = f"No displayable messages in the last {HISTORY_LOOKBACK_HOURS} hours"
                return
            if self.action != "chat":
                raise VoiceError("Unknown voice operation.")
            if self.stop.is_set():
                return
            self.audio.voice.output_meter = self.output_levels.emit
            manager = QNetworkAccessManager()
            def send(message, cancel):
                return stream_reply(manager, self.key, self.kin_id, message, cancel)

            conversation(self.audio, send, self.stop, self.stage.emit, self.chat_message.emit,
                         lambda: self.call_input)
        except VoiceError as exc:
            self.outcome = str(exc)
        except Exception:
            self.outcome = "Voice operation interrupted. Check the local audio devices and model files."
        finally:
            if self.action in ("TTS", "chat"):
                self.audio.voice.output_meter = None
            self.key = ""

    def run_dictation_test(self):
        self.stage.emit("Preparing local dictation")
        self.audio.prepare(self.stop)
        if self.stop.is_set():
            return

        abandoned = Event()
        pending = deque()
        speech_run = 0
        was_speaking = False
        spotter = None
        matches = {}
        if self.acoustic_threshold is not None:
            self.stage.emit("Loading the local conversation detector")
            spotter = ConversationSpotter(self.acoustic_threshold)
        words = empty_chunks = submitted = 0
        elapsed = 0.0

        def matched(method, word):
            if method in matches or self.stop.is_set() or abandoned.is_set():
                return
            matches[method] = word
            self.detector_result.emit(matches.get("acoustic", ""), matches.get("text", ""), False)

        turns = None
        if self.triggers:
            turns = ConversationTurns(self.triggers, self.trigger_activity.emit, self.turn_finished.emit,
                                      self.transcript.emit, lambda word: matched("text", word) if spotter else None)

        def decode(samples, paragraph, run):
            nonlocal words, empty_chunks, elapsed
            if abandoned.is_set() or self.stop.is_set():
                return
            started = time.monotonic()
            try:
                text = self.audio.transcribe(samples).strip()
            except Exception:
                abandoned.set()
                raise VoiceError("A speech chunk could not be transcribed. Try recording the phrase again.") from None
            if self.stop.is_set() or abandoned.is_set():
                return
            if turns:
                turns.receive(text, paragraph, run)
            else:
                words += len(text.split())
                empty_chunks += not text
                elapsed += time.monotonic() - started
                if text:
                    self.transcript.emit(text, paragraph)

        def finish_interval(run):
            if self.stop.is_set():
                return
            if spotter:
                self.detector_result.emit(matches.get("acoustic", ""), matches.get("text", ""), True)
                matches.clear()
            turns.pause(run)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt-chunk") as decoder:
            def enqueue(fn, *args):
                while pending and pending[0].done():
                    pending.popleft().result()
                if len(pending) >= 32:
                    raise VoiceError("Transcription fell behind the microphone. Partial text kept. Restart listening to continue.")
                pending.append(decoder.submit(fn, *args))

            def submit(samples, paragraph):
                nonlocal submitted
                submitted += 1
                enqueue(decode, samples, paragraph, speech_run)

            def new_chunks():
                return PauseChunks(self.chunk_seconds, self.paragraph_seconds, submit,
                                   manual=self.pause_seconds is None, preroll_blocks=15 if spotter else 3)

            chunks = new_chunks()
            acoustic_heard = False
            expiry_queued = None

            def accept(block, active):
                nonlocal was_speaking, speech_run
                if active and not was_speaking:
                    speech_run += 1
                was_speaking = active
                chunks.accept(block, active)

            def acoustic_match(word, run):
                matched("acoustic", word)
                turns.open(run)

            def scan(block):
                nonlocal acoustic_heard
                if acoustic_heard:
                    return False
                word = spotter.accept(block)
                if word:
                    acoustic_heard = True
                    enqueue(acoustic_match, word, speech_run + int(not was_speaking))
                return bool(word)

            def meter(level, blocks, quiet):
                nonlocal expiry_queued
                if abandoned.is_set():
                    raise VoiceError("A speech chunk could not be transcribed. Try recording the phrase again.")
                opening = turns.opening if turns else None
                elapsed = -1.0
                if opening and speech_run <= opening.speech_run:
                    elapsed = 0.0 if opening.shown_at is None else time.monotonic() - opening.shown_at
                    if elapsed >= self.initial_pause_seconds and not was_speaking and expiry_queued is not opening:
                        chunks.flush()
                        enqueue(turns.expire, opening)
                        expiry_queued = opening
                self.mic_level.emit(level, blocks, quiet, elapsed)

            def boundary():
                nonlocal chunks, acoustic_heard
                # Order the silence behind earlier audio, even if detection is still pending.
                chunks.flush()
                enqueue(finish_interval, speech_run)
                if spotter:
                    spotter.new_segment()
                    acoustic_heard = False
                    chunks = new_chunks()

            try:
                self.stage.emit("Opening microphone")
                recording = self.audio.capture(None if turns else time.monotonic() + 30, self.stop, self.stage.emit,
                                               pause_seconds=self.pause_seconds, threshold_dbfs=self.threshold_dbfs,
                                               finish=self.finish_recording, on_level=meter,
                                               on_block=accept,
                                               on_boundary=boundary if turns else None,
                                               on_audio=scan if spotter else None, on_meter=self.meter_levels.emit,
                                               awaiting_trigger=(lambda: not turns.active) if turns else None,
                                               listening_threshold_dbfs=self.listening_threshold_dbfs)
                if self.stop.is_set():
                    return
                if recording is None:
                    self.outcome = "No speech detected in 30 s. Check the input level or lower the speech threshold."
                    return
                summary = f"{recording.ended_by} · {recording.seconds:.1f} s recorded"
                self.recording_ended.emit(summary)
                chunks.flush()
                if not submitted and recording.samples is not None:
                    submit(recording.samples, 0)
                self.stage.emit("Finishing transcription")
                while pending:
                    self.chunk_progress.emit(submitted - len(pending), submitted)
                    pending.popleft().result()
                if not self.stop.is_set():
                    self.chunk_progress.emit(submitted, submitted)
                    unit = "chunk" if submitted == 1 else "chunks"
                    word_unit = "word" if words == 1 else "words"
                    self.recording_summary = f"{summary} · {submitted} {unit} · {words} {word_unit} · {elapsed:.2f} s total decode"
                    if empty_chunks:
                        unit = "chunk" if empty_chunks == 1 else "chunks"
                        self.recording_summary += f" · {empty_chunks} {unit} returned no text"
                    self.outcome = "Dictation complete" if words else "No speech recognized. Record a phrase to try again."
            finally:
                abandoned.set()
                for job in pending:
                    job.cancel()


class Window(QWidget):
    def __init__(self, audio, env_file=Path(__file__).resolve().parents[2] / ".env", *, initial_model=None):
        super().__init__()
        self.audio = audio
        self.env_file = env_file
        self.stt_preference = env_file.parent / "var/voice/chat-stt.json"
        self.worker = None
        self.history_attempted = False
        self.closing = False
        self.turn_off_requested = False
        self.listening_preview = deque(maxlen=2)
        self.setWindowTitle("Kindroid voice")
        self.resize(630, 540)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        chat_page, stt_page, tts_page = QWidget(), QWidget(), QWidget()
        clone_page = QWidget()
        engine_page = QWidget()
        self.tabs.addTab(engine_page, "Models")
        self.tabs.addTab(clone_page, "Voice cloning")
        self.tabs.addTab(tts_page, "TTS")
        self.tabs.addTab(stt_page, "STT")
        self.tabs.addTab(chat_page, "Kindroid chat")
        chat_row = QHBoxLayout(chat_page)
        chat_layout = QVBoxLayout()
        chat_row.addLayout(chat_layout, 1)
        self.chat_meter = LevelMeter("Output")
        chat_row.addWidget(self.chat_meter)
        stt_row = QHBoxLayout(stt_page)
        stt_layout = QVBoxLayout()
        stt_row.addLayout(stt_layout, 1)
        self.level_meter = LevelMeter()
        stt_row.addWidget(self.level_meter)
        tts_row = QHBoxLayout(tts_page)
        tts_layout = QVBoxLayout()
        tts_row.addLayout(tts_layout, 1)
        self.tts_meter = LevelMeter("Output")
        tts_row.addWidget(self.tts_meter)
        engine_layout = QVBoxLayout(engine_page)
        self.engine_status = QLabel()
        self.engine_status.setTextFormat(Qt.TextFormat.PlainText)
        self.engine_status.setAccessibleName("Pocket TTS model status")
        self.engine_status.setWordWrap(True)
        engine_layout.addWidget(self.engine_status)
        self.load_chat_button = QPushButton("Load saved chat voice")
        self.load_chat_button.clicked.connect(lambda: self.load_engine(chat=True))
        engine_layout.addWidget(self.load_chat_button)
        self.load_test_button = QPushButton("Load current TTS selection")
        self.load_test_button.clicked.connect(lambda: self.load_engine(chat=False))
        engine_layout.addWidget(self.load_test_button)
        self.off_button = QPushButton("Turn model off")
        self.off_button.clicked.connect(self.unload_engine)
        engine_layout.addWidget(self.off_button)
        engine_layout.addStretch()
        self.library = audio.voice.library
        self.recipes = self.library.recipes()
        self.clone_form = QWidget()
        clone_layout = QVBoxLayout(clone_page)
        clone_layout.addWidget(self.clone_form)
        cf = QFormLayout(self.clone_form)
        self.templates = QComboBox()
        cf.addRow("Load clone settings", self.templates)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Voice preset name")
        self.name_edit.setMaxLength(80)
        cf.addRow("New preset name", self.name_edit)
        self.clips = QListWidget()
        self.refresh_clips(())
        cf.addRow("Source recordings", self.clips)
        self.import_button = QPushButton("Import audio files…")
        self.import_button.clicked.connect(self.import_audio)
        cf.addRow(self.import_button)
        self.offset = QSpinBox()
        self.offset.setRange(0, 3600)
        self.offset.setSuffix(" s")
        cf.addRow("Start within each recording", self.offset)
        self.duration = QSpinBox()
        self.duration.setRange(3, 30)
        self.duration.setValue(24)
        self.duration.setSuffix(" s")
        cf.addRow("Total reference duration", self.duration)
        self.clone_temperature = self.temperature_input()
        cf.addRow("Default TTS temperature", self.clone_temperature)
        self.clone_button = QPushButton("Clone and save new preset")
        self.clone_button.clicked.connect(self.clone_voice)
        clone_layout.addWidget(self.clone_button)
        self.templates.currentIndexChanged.connect(self.load_recipe)
        form = QFormLayout()
        self.models = QComboBox()
        self.models.addItem("Pocket TTS English 18M REFL", "18m")
        if (self.library.root / "english-7m.yaml").is_file():
            self.models.addItem("Pocket TTS English 7M REFL", "7m")
        form.addRow("TTS model", self.models)
        self.voices = QComboBox()
        self.voices.setAccessibleName("Reply voice")
        form.addRow("Voice preset", self.voices)
        self.new_voice_button = QPushButton("Create voice from audio…")
        self.new_voice_button.clicked.connect(self.import_audio)
        form.addRow(self.new_voice_button)
        self.temperature = self.temperature_input()
        form.addRow("Temperature", self.temperature)
        self.models.currentIndexChanged.connect(self.refresh_voices)
        self.voices.currentIndexChanged.connect(self.preset_temperature)
        tts_layout.addLayout(form)
        self.tts_playback = PlaybackControls()
        self.tts_playback.changed.connect(lambda: self.adjust_playback("TTS", self.tts_playback))
        tts_layout.addWidget(self.tts_playback)
        self.tts_text = QPlainTextEdit("Hey, it's good to hear from you. How has your day been?")
        self.tts_text.setAccessibleName("Text to speak")
        tts_layout.addWidget(self.tts_text)
        tts_actions = QHBoxLayout()
        self.preview_button = QPushButton("Speak text")
        self.preview_button.clicked.connect(lambda: self.start_tts())
        tts_actions.addWidget(self.preview_button)
        self.export_mp3_button = QPushButton("Create MP3")
        self.export_mp3_button.clicked.connect(lambda: self.start_tts(export=True))
        tts_actions.addWidget(self.export_mp3_button)
        tts_layout.addLayout(tts_actions)
        self.save_voice_button = QPushButton("Use these settings for Kindroid chat")
        self.save_voice_button.clicked.connect(self.save_chat_voice)
        tts_layout.addWidget(self.save_voice_button)
        self.stt_model = QComboBox()
        for model, label in STT_MODELS.items():
            self.stt_model.addItem(label, model)
        self.stt_model.setCurrentIndex(max(0, self.stt_model.findData(self.audio.chat_model)))
        self.stt_controls = QWidget()
        stt_form = QFormLayout(self.stt_controls)
        stt_form.addRow("Recognizer", self.stt_model)
        self.stt_threads = QSpinBox()
        self.stt_threads.setRange(1, 16)
        self.stt_threads.setValue(4)
        stt_form.addRow("CPU threads", self.stt_threads)
        self.stt_mode = QComboBox()
        self.stt_mode.addItems(["Finish after a pause", "Finish manually", "Conversation on / off",
                                "Compare conversation detectors"])
        stt_form.addRow("Recording mode", self.stt_mode)
        self.trigger_words = QLineEdit(TriggerGate.start_phrase)
        self.trigger_words.setMaxLength(512)
        self.trigger_words.setToolTip("Separate alternative start phrases with commas.")
        stt_form.addRow("Start phrase", self.trigger_words)
        stt_form.setRowVisible(self.trigger_words, False)
        self.stop_phrase = QLabel(f"“{TriggerGate.stop_phrase}” returns to standby")
        stt_form.addRow("Stop phrase", self.stop_phrase)
        stt_form.setRowVisible(self.stop_phrase, False)
        self.acoustic_threshold = QDoubleSpinBox()
        self.acoustic_threshold.setRange(0.01, 1.0)
        self.acoustic_threshold.setSingleStep(0.05)
        self.acoustic_threshold.setValue(0.25)
        self.acoustic_threshold.setToolTip("Lower values accept weaker acoustic matches for “conversation on”.")
        stt_form.addRow("Acoustic match threshold", self.acoustic_threshold)
        stt_form.setRowVisible(self.acoustic_threshold, False)
        self.listening_threshold = QSpinBox()
        self.listening_threshold.setRange(-80, -5)
        self.listening_threshold.setValue(-60)
        self.listening_threshold.setSuffix(" dBFS")
        self.listening_threshold.setToolTip("While waiting for a command, lower this for a quieter voice or raise it above room noise.")
        stt_form.addRow("Listening threshold", self.listening_threshold)
        stt_form.setRowVisible(self.listening_threshold, False)
        self.chunk_pause = self.pause_input(1)
        self.chunk_pause.setToolTip("A pause this long queues the preceding speech for transcription while recording continues.")
        stt_form.addRow("Pause to transcribe a chunk", self.chunk_pause)
        self.paragraph_pause = self.pause_input(2)
        stt_form.addRow("Pause for a new paragraph", self.paragraph_pause)
        self.initial_pause = self.pause_input(4)
        self.initial_pause.setMaximum(60)
        self.initial_pause.setToolTip("Time to start speaking after the STT tab confirms the start phrase.")
        stt_form.addRow("Wait after conversation on", self.initial_pause)
        stt_form.setRowVisible(self.initial_pause, False)
        self.stt_pause = self.pause_input(4)
        stt_form.addRow("Pause to finish", self.stt_pause)
        self.stt_threshold = QSpinBox()
        self.stt_threshold.setRange(-80, -5)
        self.stt_threshold.setValue(-50)
        self.stt_threshold.setSuffix(" dBFS")
        self.stt_threshold.setToolTip("Lower the threshold for a quieter voice. Raise it if room noise starts a recording.")
        stt_form.addRow("Speech threshold", self.stt_threshold)
        self.stt_mode.currentIndexChanged.connect(self.change_recording_mode)
        stt_layout.addWidget(self.stt_controls)
        self.mic_status = QLabel("Microphone off")
        self.mic_status.setAccessibleName("Microphone level and recording details")
        self.mic_status.setWordWrap(True)
        stt_layout.addWidget(self.mic_status)
        self.detector_status = QLabel("Compare audio and transcript matches for “conversation on”")
        self.detector_status.setTextFormat(Qt.TextFormat.PlainText)
        self.detector_status.setWordWrap(True)
        self.detector_status.setAccessibleName("Conversation start detector comparison")
        stt_layout.addWidget(self.detector_status)
        self.detector_status.hide()
        self.detector_counts = Counter()
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setUndoRedoEnabled(False)
        self.transcript.setAccessibleName("Dictation transcript")
        self.transcript.setPlaceholderText("Record a phrase, then pause to see the transcript here.")
        stt_layout.addWidget(self.transcript)
        self.record_button = QPushButton("Record a phrase")
        self.record_button.clicked.connect(self.record_phrase)
        stt_actions = QHBoxLayout()
        stt_layout.addLayout(stt_actions)
        stt_actions.addWidget(self.record_button)
        self.finish_button = QPushButton("Finish and transcribe")
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self.finish_recording)
        stt_actions.addWidget(self.finish_button)
        self.clear_log_button = QPushButton("Clear log")
        self.clear_log_button.clicked.connect(self.clear_transcript)
        stt_actions.addWidget(self.clear_log_button)
        self.save_stt_button = QPushButton("Use these settings for Kindroid chat")
        self.save_stt_button.clicked.connect(self.save_chat_input)
        stt_layout.addWidget(self.save_stt_button)
        self.history_status = QLabel("Kindroid history")
        self.history_status.setAccessibleName("Startup history")
        self.history_status.setWordWrap(True)
        chat_layout.addWidget(self.history_status)
        legend = QLabel("TK · Kindroid text     VK · Kindroid voice call     API · API text     ? · Source unavailable")
        legend.setWordWrap(True)
        chat_layout.addWidget(legend)
        self.history = ChatView()
        chat_layout.addWidget(self.history)
        self.chat_voice_label = QLabel()
        self.chat_voice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_voice_label.setWordWrap(True)
        chat_layout.addWidget(self.chat_voice_label)
        self.chat_playback = PlaybackControls()
        self.chat_playback.changed.connect(lambda: self.adjust_playback("chat", self.chat_playback))
        chat_layout.addWidget(self.chat_playback)
        self.status = QLabel("Microphone off")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.start_button = QPushButton("Call")
        self.start_button.setMinimumHeight(48)
        self.start_button.setStyleSheet("font-size: 17px; font-weight: bold")
        self.start_button.clicked.connect(self.start)
        chat_layout.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)
        self.tabs.currentChanged.connect(self.update_stop_visibility)
        self.saved_call_input = self.current_call_input()
        self.restore_chat_input(initial_model)
        self.refresh_templates()
        self.refresh_voices()
        try:
            saved = self.library.chat()
            self.models.setCurrentIndex(max(0, self.models.findData(saved.model)))
            self.voices.setCurrentIndex(max(0, self.voices.findData(saved.preset)))
            self.temperature.setValue(saved.temperature)
            self.tts_playback.load(saved)
            self.chat_playback.load(saved)
            self.show_chat_voice(saved)
        except (VoiceError, TypeError, ValueError):
            self.chat_voice_label.setText("Select chat settings on the TTS tab.")
        self.model_timer = QTimer(self)
        self.model_timer.timeout.connect(self.refresh_engine)
        self.model_timer.start(250)
        self.refresh_engine()

    def refresh_engine(self):
        engine = self.audio.voice.engine
        state = str(engine.status)
        settings = engine.settings
        details = ""
        if isinstance(settings, Playback):
            recipe = self.recipes.get(settings.preset)
            name = recipe.name if recipe else {"original": "Hannah", "alba": "Alba"}.get(settings.preset, "")
            details = f"\nEnglish {settings.model.upper()} REFL · {name} · temperature {settings.temperature:g}"
        timing = engine.last_timing
        label = f"Pocket TTS · {state}{details}" + (f"\n{timing}" if timing else "")
        if self.engine_status.text() != label:
            self.engine_status.setText(label)
        self.off_button.setEnabled(not self.turn_off_requested and state != "Off" and
                                   (self.worker is None or self.worker.action in ("chat", "load", "TTS", "MP3")))

    def load_engine(self, *, chat):
        if self.worker is not None:
            return
        try:
            settings = self.library.chat() if chat else self.tts_settings()
        except VoiceError as exc:
            self.status.setText(str(exc))
            return
        self.begin(VoiceWorker(self.audio, "load", settings=settings), "Loading Pocket TTS")

    def unload_engine(self):
        if self.worker is not None:
            self.turn_off_requested = True
            self.stop()
        else:
            self.begin(VoiceWorker(self.audio, "unload"), "Releasing Pocket TTS")
        self.refresh_engine()

    @staticmethod
    def pause_input(seconds):
        box = QDoubleSpinBox()
        box.setDecimals(1)
        box.setRange(0.1, 10)
        box.setSingleStep(0.1)
        box.setSuffix(" s")
        box.setValue(seconds)
        return box

    @staticmethod
    def temperature_input():
        box = QDoubleSpinBox()
        box.setRange(0, 1)
        box.setSingleStep(0.05)
        box.setValue(0.3)
        return box

    def refresh_templates(self):
        self.templates.clear()
        self.templates.addItem("New settings", None)
        for ident, recipe in self.recipes.items():
            self.templates.addItem(f"{recipe.name} · {ident[:6]}", ident)

    def load_recipe(self):
        recipe = self.recipes.get(self.templates.currentData())
        self.name_edit.setText(recipe.name if recipe else "")
        self.offset.setValue(recipe.offset if recipe else 0)
        self.duration.setValue(recipe.seconds if recipe else 24)
        self.clone_temperature.setValue(recipe.temperature if recipe else 0.3)
        for i in range(self.clips.count()):
            item = self.clips.item(i)
            checked = recipe and item.text() in recipe.samples
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def refresh_voices(self):
        self.voices.clear()
        self.voices.addItem("Alba", "alba")
        if self.models.currentData() == "18m":
            if (self.library.root / "custom.safetensors").is_file():
                self.voices.addItem("Hannah · original clone", "original")
            for ident, recipe in self.recipes.items():
                self.voices.addItem(f"{recipe.name} · {ident[:6]}", ident)

    def preset_temperature(self):
        recipe = self.recipes.get(self.voices.currentData())
        self.temperature.setValue(recipe.temperature if recipe else 0.3)

    def tts_settings(self, *, honor_mute=True):
        settings = Playback(self.models.currentData(), self.voices.currentData(), self.temperature.value())
        return self.tts_playback.apply_to(settings, honor_mute=honor_mute)

    def adjust_playback(self, action, controls):
        if self.worker is not None and self.worker.action == action:
            self.audio.voice.playback = controls.apply_to(self.audio.voice.playback)

    def show_chat_voice(self, settings):
        recipe = self.recipes.get(settings.preset)
        name = (f"{recipe.name} · {settings.preset[:6]}" if recipe else
                {"original": "Hannah · original clone", "alba": "Alba"}.get(settings.preset, "Unavailable preset"))
        self.chat_voice_label.setText(f"Chat voice · {name} · {settings.model.upper()} REFL · "
                                     f"temperature {settings.temperature:g}")

    def save_chat_voice(self):
        try:
            settings = self.tts_settings()
            self.library.save_chat(settings)
            self.chat_playback.load(settings)
            self.show_chat_voice(settings)
            self.status.setText("Chat voice saved")
        except (OSError, VoiceError):
            self.status.setText("Could not save the selected chat voice.")

    def load_history(self):
        if self.history_attempted or self.worker is not None:
            return
        self.history_attempted = True
        try:
            key, kin_id = read_credentials(self.env_file)
        except VoiceError as exc:
            self.history_status.setText(f"History unavailable · {exc}")
            return
        self.history_status.setText("Loading recent Kindroid messages…")
        guard = Path(__file__).resolve().parents[2] / "var/voice/history-next-read"
        self.begin(VoiceWorker(self.audio, "history", key=key, kin_id=kin_id, history_guard=guard),
                   "Loading recent Kindroid messages")

    def show_history(self, rows):
        for index, (speaker, origin, body) in enumerate(rows):
            self.history.put(f"history-{index}", speaker, origin, body)

    def current_call_input(self):
        return CallInput(self.stt_model.currentData(), self.stt_threads.value(), self.stt_threshold.value(),
                         self.chunk_pause.value(), self.paragraph_pause.value(), self.stt_pause.value())

    def restore_chat_input(self, initial_model):
        try:
            settings = CallInput(**json.loads(self.stt_preference.read_text("utf-8")))
        except FileNotFoundError:
            return
        except (OSError, TypeError, ValueError):
            self.status.setText("Could not load saved chat STT settings.")
            return
        fields = (
            (self.stt_threads, settings.threads),
            (self.stt_threshold, settings.threshold),
            (self.chunk_pause, settings.chunk),
            (self.paragraph_pause, settings.paragraph),
            (self.stt_pause, settings.finish),
        )
        if (not isinstance(settings.model, str) or settings.model not in STT_MODELS or
                any(type(value) not in (int, float) or not field.minimum() <= value <= field.maximum()
                    for field, value in fields) or
                type(settings.threads) is not int or type(settings.threshold) is not int):
            self.status.setText("Saved chat STT settings are outside the supported range.")
            return
        self.stt_model.setCurrentIndex(self.stt_model.findData(initial_model or settings.model))
        for field, value in fields:
            field.setValue(value)
        self.saved_call_input = settings

    def save_chat_input(self):
        settings = self.current_call_input()
        try:
            write_settings(self.stt_preference, settings)
        except OSError:
            self.mic_status.setText("Could not save chat STT settings.")
            return
        self.saved_call_input = settings
        self.mic_status.setText(f"Chat STT saved · {STT_MODELS[settings.model]} · applies on next turn")
        if self.worker is not None and self.worker.action == "chat":
            self.worker.call_input = settings

    def update_stop_visibility(self):
        in_call_tab = self.tabs.currentWidget() is self.start_button.parentWidget()
        self.stop_button.setVisible(not in_call_tab or (self.worker is not None and self.worker.action != "chat"))

    def start(self):
        if self.worker is not None:
            if self.worker.action == "chat":
                self.stop()
            return
        try:
            key, kin_id = read_credentials(self.env_file)
            self.audio.voice.playback = self.chat_playback.apply_to(self.library.chat())
        except VoiceError as exc:
            self.status.setText(str(exc))
            return
        self.begin(VoiceWorker(self.audio, "chat", key=key, kin_id=kin_id, call_input=self.saved_call_input),
                   "Preparing call models")

    def record_phrase(self):
        if self.worker is None:
            mode = self.stt_mode.currentIndex()
            triggers = ()
            if mode >= 2:
                triggers = tuple(word.strip() for word in self.trigger_words.text().split(",") if word.strip())
                if not triggers:
                    self.status.setText("Enter a start phrase before starting the listener.")
                    return
            self.triggered = False
            self.audio.select_model(self.stt_model.currentData(), self.stt_threads.value())
            pause = None if mode == 1 else self.stt_pause.value()
            self.mic_status.setText("Microphone off · preparing recognizer")
            self.preview_start = self.transcript.document().characterCount() - 1
            self.last_paragraph = None
            if mode == 3:
                self.detector_counts.clear()
                self.detector_status.setText("Waiting for speech")
            self.begin(VoiceWorker(self.audio, "STT", pause_seconds=pause,
                                   initial_pause_seconds=self.initial_pause.value(),
                                   threshold_dbfs=self.stt_threshold.value(),
                                   chunk_seconds=self.chunk_pause.value(),
                                   paragraph_seconds=self.paragraph_pause.value(), triggers=triggers,
                                   acoustic_threshold=self.acoustic_threshold.value() if mode == 3 else None,
                                   listening_threshold_dbfs=self.listening_threshold.value()), "Preparing dictation")

    def change_recording_mode(self, index):
        self.stt_pause.setEnabled(index != 1)
        self.stt_controls.layout().setRowVisible(self.trigger_words, index >= 2)
        self.stt_controls.layout().setRowVisible(self.stop_phrase, index >= 2)
        self.stt_controls.layout().setRowVisible(self.initial_pause, index >= 2)
        self.stt_controls.layout().setRowVisible(self.listening_threshold, index >= 2)
        self.stt_controls.layout().setRowVisible(self.acoustic_threshold, index == 3)
        self.detector_status.setVisible(index == 3)
        self.record_button.setText("Start listening" if index >= 2 else "Record a phrase")

    def show_detector_result(self, acoustic, transcript, completed):
        waiting = "no match" if completed else "no match yet"
        counts = self.detector_counts
        interval = sum(counts.values()) + 1
        if completed:
            counts[bool(acoustic), bool(transcript)] += 1
        self.detector_status.setText(
            f"{'Completed' if completed else 'Current'} phrase {interval}"
            f"  ·  Acoustic: {acoustic or waiting}  ·  Transcript: {transcript or waiting}\n"
            f"Both {counts[True, True]}  ·  Acoustic only {counts[True, False]}  ·  "
            f"Transcript only {counts[False, True]}  ·  Neither {counts[False, False]}")

    def set_trigger_activity(self, opening):
        self.triggered = opening is not None
        if self.listening_preview:
            self.erase_current_suffix()
            if self.triggered:
                for text, paragraph in self.listening_preview:
                    self.append_log_text(text, paragraph)
            self.listening_preview.clear()
        if self.triggered:
            self.set_stage("Conversation on · recording")
            opening.acknowledge()
        else:
            self.preview_start = self.transcript.document().characterCount() - 1
            self.last_paragraph = None
            self.set_stage("Listening for a start phrase")

    def finish_conversation_turn(self):
        self.preview_start = self.transcript.document().characterCount() - 1
        self.last_paragraph = None
        self.set_stage("Conversation on · ready for the next turn")

    def clear_transcript(self):
        self.listening_preview.clear()
        self.transcript.clear()
        self.preview_start = 0
        self.last_paragraph = None

    def append_transcript_chunk(self, text, paragraph):
        if self.worker is None:
            return
        if self.worker.triggers and not self.triggered:
            if self.listening_preview:
                self.erase_current_suffix()
            self.listening_preview.append((text, paragraph))
            for index, (part, number) in enumerate(self.listening_preview):
                self.append_log_text(f"[Listening] {part}" if index == 0 else part, number)
        else:
            if self.worker.triggers and self.last_paragraph is None:
                self.set_stage("Conversation on · recording")
            self.append_log_text(text, paragraph)

    def erase_current_suffix(self):
        cursor = QTextCursor(self.transcript.document())
        cursor.setPosition(self.preview_start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.last_paragraph = None

    def append_log_text(self, text, paragraph):
        scroll = self.transcript.verticalScrollBar()
        following = scroll.value() == scroll.maximum()
        cursor = QTextCursor(self.transcript.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.last_paragraph is not None and paragraph != self.last_paragraph:
            cursor.insertBlock()
            cursor.insertBlock()
        elif self.last_paragraph is not None:
            cursor.insertText(" ")
        elif cursor.position():
            cursor.insertBlock()
        cursor.insertText(text)
        self.last_paragraph = paragraph
        if following:
            scroll.setValue(scroll.maximum())

    def show_mic_level(self, level, blocks, quiet, opening_elapsed):
        worker = self.worker
        if worker is None or worker.stop.is_set() or worker.finish_recording.is_set():
            return
        text = f"Mic {level:.0f} dBFS"
        if worker.triggers:
            if self.triggered:
                if opening_elapsed >= 0:
                    text += f" · initial wait {opening_elapsed:.1f} / {worker.initial_pause_seconds:g} s"
                elif blocks:
                    text += f" · turn pause {quiet / 10:.1f} / {worker.pause_seconds:g} s"
                else:
                    text += " · ready for speech"
            self.mic_status.setText(text)
            return
        if blocks:
            text += f" · {blocks / 10:.1f} s recorded"
            if worker.pause_seconds is not None:
                text += f" · pause {quiet / 10:.1f} / {worker.pause_seconds:g} s"
            self.finish_button.setEnabled(True)
        self.mic_status.setText(text)

    def finish_recording(self):
        if self.worker is not None and self.worker.action == "STT":
            self.worker.finish_recording.set()
            self.finish_button.setEnabled(False)
            self.set_stage("Finishing recording")

    def recording_ended(self, summary):
        self.level_meter.reset()
        self.finish_button.setEnabled(False)
        self.mic_status.setText(summary)

    def start_tts(self, *, export=False):
        if self.worker is not None:
            return
        settings = self.tts_settings(honor_mute=not export)
        if settings.speed == 0:
            self.status.setText("Playback off. Unmute to hear the preview.")
            return
        text = self.tts_text.toPlainText().strip()
        if not 1 <= len(text) <= 4000:
            self.status.setText("Enter 1 to 4,000 characters for speech synthesis.")
            return
        if export:
            downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
            if not downloads:
                self.status.setText("Could not locate your Downloads folder.")
                return
            self.begin(VoiceWorker(self.audio, "MP3", text=text, settings=settings, destination=Path(downloads)),
                       "Preparing MP3 export")
            return
        self.audio.voice.playback = settings
        self.begin(VoiceWorker(self.audio, "TTS", text=text, settings=settings), "Preparing speech")

    def refresh_clips(self, selected):
        self.clips.clear()
        for name in self.library.recordings():
            item = QListWidgetItem(name, self.clips)
            item.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)

    def import_audio(self):
        if self.worker is not None:
            return
        patterns = " ".join(f"*{ext}" for ext in RECORDING_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(self, "Import voice recordings", "", f"Audio files ({patterns})")
        if files:
            self.tabs.setCurrentWidget(self.clone_button.parentWidget())
            self.begin(VoiceWorker(self.audio, "import", sources=files), "Importing audio files")

    def clone_voice(self):
        if self.worker is not None:
            return
        samples = tuple(self.clips.item(i).text() for i in range(self.clips.count())
                        if self.clips.item(i).checkState() == Qt.CheckState.Checked)
        recipe = Recipe(self.name_edit.text().strip(), samples, self.offset.value(),
                        self.duration.value(), self.clone_temperature.value())
        try:
            recipe.validate()
        except VoiceError as exc:
            self.status.setText(str(exc))
            return
        self.begin(VoiceWorker(self.audio, "clone", recipe=recipe), "Preparing reference clips")

    def begin(self, worker, stage):
        for control in (self.models, self.temperature, self.clone_form, self.stt_controls, self.save_voice_button,
                        self.load_chat_button, self.load_test_button, self.new_voice_button):
            control.setEnabled(False)
        self.voices.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.export_mp3_button.setEnabled(False)
        self.clone_button.setEnabled(False)
        self.start_button.setEnabled(worker.action == "chat")
        if worker.action == "chat":
            self.start_button.setText("Stop")
            self.stt_controls.setEnabled(True)
        self.save_stt_button.setEnabled(worker.action == "chat")
        self.tts_playback.mute.setEnabled(worker.action != "TTS")
        self.chat_playback.mute.setEnabled(worker.action != "chat")
        self.record_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker = worker
        self.update_stop_visibility()
        self.worker.stage.connect(self.set_stage)
        self.worker.progress.connect(self.set_clip_progress)
        self.worker.playback_progress.connect(self.set_playback_progress)
        self.worker.mic_level.connect(self.show_mic_level)
        self.worker.meter_levels.connect(self.level_meter.set_levels)
        if worker.action in ("TTS", "chat"):
            meter = self.tts_meter if worker.action == "TTS" else self.chat_meter
            self.worker.output_levels.connect(meter.set_levels)
        self.worker.detector_result.connect(self.show_detector_result)
        self.worker.recording_ended.connect(self.recording_ended)
        self.worker.trigger_activity.connect(self.set_trigger_activity)
        self.worker.turn_finished.connect(self.finish_conversation_turn)
        self.worker.chat_message.connect(self.update_chat_message)
        self.worker.history_page.connect(self.show_history)
        self.worker.transcript.connect(self.append_transcript_chunk)
        self.worker.chunk_progress.connect(self.set_chunk_progress)
        self.worker.finished.connect(self.finished)
        self.set_stage(stage)
        self.progress.show()
        self.worker.start()

    def set_stage(self, text):
        if self.worker is not None and not self.worker.stop.is_set():
            if self.worker.action in ("STT", "TTS", "MP3"):
                text = f"{self.worker.action} · {text}"
            self.status.setText(text)
            self.progress.setAccessibleName(text)
            self.progress.setRange(0, 0)

    def set_clip_progress(self, done, total):
        if self.worker is not None and not self.worker.stop.is_set():
            action = "Importing audio files" if self.worker.action == "import" else "Reading reference clips"
            text = f"{action} · {done} of {total}"
            self.status.setText(text)
            self.progress.setAccessibleName(text)
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def set_chunk_progress(self, done, total):
        if self.worker is not None and not self.worker.stop.is_set():
            self.progress.setRange(0, total)
            self.progress.setFormat("%v of %m chunks")
            self.progress.setValue(done)

    def set_playback_progress(self, done, total):
        if self.worker is not None and not self.worker.stop.is_set():
            self.progress.setRange(0, total)
            self.progress.setFormat("%v of %m paragraphs")
            self.progress.setValue(done)

    def update_chat_message(self, ident, speaker, text, status):
        self.history.put(ident, speaker, "API", text, status)

    def stop(self):
        if self.worker is not None:
            self.worker.stop.set()
            self.start_button.setEnabled(False)
            self.finish_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.progress.setRange(0, 0)
            self.status.setText("Stopping · waiting for the current operation")
            self.progress.setAccessibleName(self.status.text())

    def finished(self):
        self.status.setText(self.worker.outcome)
        if self.worker.action == "TTS":
            self.tts_meter.reset()
        if self.worker.action == "chat":
            self.chat_meter.reset()
        if self.worker.action == "history":
            self.history_status.setText(self.worker.outcome)
        if self.worker.action == "STT":
            self.level_meter.reset()
            if self.worker.acoustic_threshold is not None:
                result = self.detector_status.text()
                if result.startswith("Current phrase"):
                    self.detector_status.setText(result.replace("Current phrase", "Interrupted phrase", 1))
                elif result == "Waiting for speech":
                    self.detector_status.setText("No completed phrases")
            summary = self.worker.recording_summary or "Microphone off"
            if self.listening_preview:
                self.erase_current_suffix()
                self.listening_preview.clear()
            if self.last_paragraph is not None and self.worker.stop.is_set():
                self.erase_current_suffix()
                summary = "Recording canceled"
            elif self.last_paragraph is not None and not self.worker.recording_summary:
                summary = "Recording interrupted · partial transcript kept"
            self.finish_button.setEnabled(False)
            self.mic_status.setText(summary)
        if self.worker.imported:
            # Start a separate selection for this speaker. Preserve the timing
            # settings the operator was experimenting with.
            self.templates.blockSignals(True)
            self.templates.setCurrentIndex(0)
            self.templates.blockSignals(False)
            self.refresh_clips(set(self.worker.imported))
            if not self.name_edit.isModified() or not self.name_edit.text().strip():
                self.name_edit.setText(Path(self.worker.imported[0]).stem)
        if self.worker.created_voice:
            self.recipes = self.library.recipes()
            self.refresh_templates()
            self.templates.setCurrentIndex(self.templates.findData(self.worker.created_voice))
            self.models.setCurrentIndex(0)
            self.refresh_voices()
            self.voices.setCurrentIndex(self.voices.findData(self.worker.created_voice))
        for control in (self.models, self.temperature, self.clone_form, self.stt_controls, self.save_voice_button,
                        self.load_chat_button, self.load_test_button, self.new_voice_button):
            control.setEnabled(True)
        self.save_stt_button.setEnabled(True)
        self.tts_playback.mute.setEnabled(True)
        self.chat_playback.mute.setEnabled(True)
        self.voices.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.export_mp3_button.setEnabled(True)
        self.clone_button.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        self.start_button.setText("Call")
        self.update_stop_visibility()
        self.progress.hide()
        self.progress.resetFormat()
        self.start_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.closing:
            self.close()
        elif self.turn_off_requested:
            self.turn_off_requested = False
            self.unload_engine()
        self.refresh_engine()

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.stop()
            event.ignore()
        else:
            self.model_timer.stop()
            self.audio.voice.engine.unload()
            event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=STT_MODELS)
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    local = Path(__file__).resolve().parents[2] / "var/voice"
    lock = QLockFile(str(local / "listener.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock():
        QMessageBox.information(None, "Kindroid voice", "The voice frontend is already open.")
        return 1
    audio = LocalAudio(local / "dictate", local / "dictate/models" / (args.model or "multilingual"))
    window = Window(audio, initial_model=args.model)
    window.tabs.setCurrentWidget(window.start_button.parentWidget())
    window.show()
    window.load_history()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
