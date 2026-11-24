import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from threading import Event
from unittest.mock import Mock, patch

try:
    import numpy as np
    import sounddevice
    from PyQt6.QtWidgets import QApplication, QLineEdit
    from kindroid_voice.__main__ import Window
except ImportError:
    QApplication = None

from kindroid_voice.audio import LocalAudio, Recording
from kindroid_voice.presets import VoiceLibrary
from kindroid_voice.session import VoiceError
from kindroid_voice.stt_chunks import PauseChunks, TriggerGate


@unittest.skipIf(QApplication is None, "run in the NICA Dictate runtime")
class VoiceDesktopTests(unittest.TestCase):
    def test_single_startup_history_and_live_bubbles(self):
        from PyQt6.QtWidgets import QLabel
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as temp, patch("kindroid_voice.__main__.read_credentials",
                return_value=("fixture", "fixture")), patch("kindroid_voice.__main__.read_history") as get:
            audio = Mock(chat_model="multilingual")
            audio.voice.library = VoiceLibrary(Path(temp))
            window = Window(audio, audio.voice.library.root / ".env")
            window.tabs.setCurrentWidget(window.start_button.parentWidget())
            window.show()
            get.return_value = ([("Hannah", "VK", "<hello>"), ("Marc", "TK", "Hi")], True)
            window.load_history()
            self.assertTrue(window.progress.isVisible())
            self.assertFalse(window.start_button.isEnabled())
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            window.load_history()
            get.assert_called_once()
            self.assertIn("newer messages outside this page", window.history_status.text())
            for ident, side, source in (("history-0", 0, "VK"), ("history-1", 1, "TK")):
                row, heading, body = window.history.entries[ident]
                card = row.layout().itemAt(side).widget()
                self.assertTrue(card.isAncestorOf(body))
                self.assertIn(source, [label.text() for label in card.findChildren(QLabel)])
            window.update_chat_message("live", "Marc", "Part", "Dictating")
            window.update_chat_message("live", "Marc", "Part two.", "Sent")
            _, label, body = window.history.entries["live"]
            self.assertEqual((label.text(), body.text()), ("Marc · Sent", "Part two."))
            self.assertEqual(len(window.history.entries), 3)
            self.assertTrue(window.start_button.isEnabled())
            self.assertFalse(window.progress.isVisible())
            window.close()

    def test_provider_streams_plain_http_and_stop_aborts_before_headers(self):
        import json
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from PyQt6.QtNetwork import QNetworkAccessManager
        from kindroid_voice.provider import stream_reply
        app = QApplication.instance() or QApplication([])
        manager = QNetworkAccessManager()
        posts = []
        release = Event()
        stop = Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                post = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append(post)
                if post["message"] == "Second":
                    stop.set()
                    release.wait(3)
                    return
                if post["message"] == "Disconnect":
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"   First. ")
                self.wfile.flush()
                release.wait(3)
                self.wfile.write("And voilà.".encode())

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("kindroid_voice.provider.BASE", f"http://127.0.0.1:{server.server_port}/"):
                    stream = stream_reply(manager, "fixture", "fixture", "Hello", stop)
                    self.assertEqual(next(stream), "First. ")
                    release.set()
                    self.assertEqual("".join(stream), "And voilà.")
                    release.clear()
                    started = time.monotonic()
                    self.assertEqual(list(stream_reply(manager, "fixture", "fixture", "Second", stop)), [])
                    self.assertLess(time.monotonic() - started, 2)
                    stop.clear()
                    with self.assertRaisesRegex(VoiceError, "Delivery is uncertain"):
                        list(stream_reply(manager, "fixture", "fixture", "Disconnect", stop))
                self.assertEqual([p["message"] for p in posts], ["Hello", "Second", "Disconnect"])
                self.assertTrue(all(p["stream"] is True for p in posts))
            finally:
                release.set()
                server.shutdown()
                thread.join(2)

    def test_call_button_reports_progress_across_tabs_and_stops(self):
        app = QApplication.instance() or QApplication([])
        entered = Event()
        selected = []

        def call(audio, send, stop, stage, update, get_settings):
            selected.append(get_settings().model)
            stage("Listening · waiting for speech")
            update("draft", "Marc", "Hello", "Dictating")
            audio.voice.output_meter(-24.0, -10.0)
            entered.set()
            if stop.wait(3):
                update("draft", "Marc", "Hello", "Not sent")

        with TemporaryDirectory() as temp, patch("kindroid_voice.__main__.read_credentials",
                return_value=("fixture", "fixture")), patch("kindroid_voice.__main__.conversation", side_effect=call):
            audio = Mock(chat_model="multilingual")
            audio.voice.library = VoiceLibrary(Path(temp))
            (Path(temp) / "custom.safetensors").touch()
            window = Window(audio, Path(temp) / ".env")
            chat = window.start_button.parentWidget()
            window.stt_model.setCurrentIndex(window.stt_model.findData("fluid-speech-pro"))
            self.assertEqual(window.stt_model.currentText(), "Fluid Speech Pro")
            self.assertEqual(window.saved_call_input.model, "multilingual")
            window.save_stt_button.click()
            window.tabs.setCurrentWidget(chat)
            window.show()
            window.start_button.click()
            self.assertEqual(window.start_button.text(), "Stop")
            self.assertTrue(window.progress.isVisible())
            self.assertTrue(entered.wait(2))
            self.assertEqual(selected, ["fluid-speech-pro"])
            app.processEvents()
            self.assertEqual(window.chat_meter.level, -24.0)
            self.assertFalse(window.tts_meter.running)
            window.chat_playback.speed.setValue(107)
            window.chat_playback.bands[0].setValue(4)
            self.assertEqual((audio.voice.playback.speed, audio.voice.playback.bass), (1.07, 4))
            window.stt_model.setCurrentIndex(window.stt_model.findData("german"))
            self.assertEqual(window.worker.call_input.model, "fluid-speech-pro")
            window.save_stt_button.click()
            self.assertEqual(window.worker.call_input.model, "german")
            self.assertIn("Listening", window.status.text())
            window.tabs.setCurrentIndex(0)
            self.assertTrue(window.stop_button.isVisible())
            self.assertTrue(window.progress.isVisible())
            window.tabs.setCurrentWidget(chat)
            window.start_button.click()
            self.assertIn("Stopping", window.status.text())
            self.assertFalse(window.start_button.isEnabled())
            self.assertTrue(window.worker.wait(2000))
            app.processEvents()
            self.assertEqual(window.start_button.text(), "Call")
            self.assertEqual(window.status.text(), "Stopped")
            self.assertIn("Not sent", window.history.entries["draft"][1].text())
            self.assertTrue(window.start_button.isEnabled())
            self.assertFalse(window.progress.isVisible())
            self.assertFalse(window.chat_meter.running)
            window.close()
            restored = Window(audio, Path(temp) / ".env")
            self.assertEqual(restored.stt_model.currentData(), "german")
            self.assertEqual(restored.saved_call_input.model, "german")
            restored.close()

    def test_import_from_tts_selects_one_speaker_and_exposes_cloned_preset(self):
        import soundfile as sf

        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as temp, patch("kindroid_voice.__main__.QFileDialog.getOpenFileNames") as pick:
            root = Path(temp)
            audio = LocalAudio(None, Path("multilingual"))
            library = VoiceLibrary(root / "voice")
            audio.voice.library = library
            audio.voice.directory = library.root
            library.samples.mkdir(parents=True)
            (library.samples / "older.wav").touch()
            sources = [root / "Robin 1.wav", root / "Robin 2.wav"]
            for path in sources:
                sf.write(path, np.full(4 * 16000, 0.2), 16000)
            window = Window(audio, audio.voice.library.root / ".env")
            window.show()
            tts = window.preview_button.parentWidget()
            window.tabs.setCurrentWidget(tts)
            pick.return_value = ([], "")
            window.new_voice_button.click()
            self.assertIsNone(window.worker)
            self.assertIs(window.tabs.currentWidget(), tts)
            pick.return_value = ([str(p) for p in sources], "")
            proceed = Event()
            actual_import = library.import_recording
            def delayed_import(source, stop):
                self.assertTrue(proceed.wait(2))
                return actual_import(source, stop)
            with patch.object(library, "import_recording", side_effect=delayed_import):
                window.new_voice_button.click()
                try:
                    self.assertIs(window.tabs.currentWidget(), window.clone_button.parentWidget())
                    self.assertTrue(window.progress.isVisible())
                    self.assertFalse(window.import_button.isEnabled())
                    self.assertFalse(window.new_voice_button.isEnabled())
                    end = time.monotonic() + 1
                    while window.progress.maximum() != 2 and time.monotonic() < end:
                        app.processEvents()
                        time.sleep(0.003)
                    self.assertEqual(window.progress.maximum(), 2)
                    self.assertIn("0 of 2", window.status.text())
                finally:
                    proceed.set()
                    self.assertTrue(window.worker.wait(3000))
                    app.processEvents()
            self.assertEqual(window.status.text(), "2 of 2 recordings ready")
            self.assertFalse(window.progress.isVisible())
            self.assertEqual(window.name_edit.text(), "Robin 1")
            for path in sources:
                path.unlink()  # Cloning must use the local library copies.
            window.name_edit.setText("Robin")
            with patch.object(audio.voice, "check"), patch.object(audio.voice, "_export") as encode:
                def export(reference, profile, stop):
                    profile.write_bytes(b"fixture")
                    return True
                encode.side_effect = export
                window.clone_button.click()
                self.assertTrue(window.worker.wait(3000))
                app.processEvents()
            preset = window.voices.currentData()
            self.assertEqual(list(library.recipes()[preset].samples), [p.name for p in sources])
            self.assertIn("Robin", window.voices.currentText())
            self.assertEqual(library.chat().preset, "alba")
            # A failed second file keeps the completed import usable. Retrying
            # the batch after fixing that file reuses the existing local copy.
            good = root / "next.wav"
            bad = root / "broken.wav"
            sf.write(good, np.full(16000, 0.3), 16000)
            bad.write_bytes(b"broken")
            pick.return_value = ([str(good), str(bad)], "")
            window.import_button.click()
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertIn("broken.wav · 1 of 2 ready", window.status.text())
            self.assertTrue(window.import_button.isEnabled())
            self.assertFalse((library.samples / bad.name).exists())
            sf.write(bad, np.full(16000, 0.4), 16000)
            window.import_button.click()
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertEqual(window.status.text(), "2 of 2 recordings ready")
            self.assertFalse((library.samples / "next (2).wav").exists())
            window.close()

    def test_model_panel_load_stop_during_playback_and_release(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        audio = Mock(chat_model="multilingual")
        engine = audio.voice.engine
        engine.status, engine.settings, engine.last_timing = "Off", None, ""
        loading_test_voice, finish_load = Event(), Event()
        with TemporaryDirectory() as directory:
            audio.voice.library = VoiceLibrary(Path(directory))
            (Path(directory) / "english-7m.yaml").touch()

            def load(settings, stop, stage):
                if settings.model == "7m":
                    stage("Loading Pocket TTS and voice preset")
                    loading_test_voice.set()
                    finish_load.wait(3)
                engine.status, engine.settings = "Ready", settings

            def unload():
                engine.status, engine.settings = "Off", None

            def speak(text, stop, stage, progress):
                audio.voice.output_meter(-20.0, -12.0)
                stage("Playing paragraph 1 of 2")
                progress(0, 2)
                stop.wait(3)

            audio.voice.load_voice.side_effect = load
            engine.unload.side_effect = unload
            audio.voice.speak.side_effect = speak
            window = Window(audio, audio.voice.library.root / ".env")
            window.show()
            self.assertEqual(window.tabs.tabText(window.tabs.currentIndex()), "Models")
            self.assertFalse(window.off_button.isEnabled())
            window.load_chat_button.click()
            self.assertTrue(window.progress.isVisible())
            self.assertFalse(window.load_test_button.isEnabled())
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertIn("Ready", window.engine_status.text())
            self.assertTrue(window.off_button.isEnabled())
            window.models.setCurrentIndex(window.models.findData("7m"))
            window.temperature.setValue(0.55)
            window.preview_button.click()
            self.assertTrue(loading_test_voice.wait(2))
            app.processEvents()
            self.assertIn("Loading Pocket TTS", window.status.text())
            self.assertTrue(window.progress.isVisible())
            self.assertFalse(window.preview_button.isEnabled())
            audio.voice.speak.assert_not_called()
            finish_load.set()
            deadline = time.monotonic() + 2
            while window.progress.maximum() != 2 and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertEqual(window.progress.maximum(), 2)
            self.assertEqual(audio.voice.load_voice.call_count, 2)
            loaded = audio.voice.load_voice.call_args.args[0]
            self.assertEqual((loaded.model, loaded.preset, loaded.temperature), ("7m", "alba", 0.55))
            self.assertEqual(window.tts_meter.level, -20.0)
            self.assertFalse(window.chat_meter.running)
            self.assertTrue(window.tts_playback.speed.isEnabled())
            window.tts_playback.speed.setValue(97)
            self.assertEqual(audio.voice.playback.speed, 0.97)
            window.off_button.click()
            self.assertTrue(window.worker.stop.is_set())
            deadline = time.monotonic() + 3
            while window.worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertIsNone(window.worker)
            self.assertEqual(window.status.text(), "Pocket TTS off")
            self.assertFalse(window.off_button.isEnabled())
            self.assertFalse(window.progress.isVisible())
            engine.unload.assert_called_once()
            window.close()

    def test_mp3_export_shows_progress_across_tabs_and_reports_the_file(self):
        from PyQt6.QtCore import QStandardPaths

        app = QApplication.instance() or QApplication([])
        rendering, finish = Event(), Event()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio = Mock(chat_model="multilingual")
            audio.voice.library = VoiceLibrary(root)
            downloads = root / "User Downloads"
            target = downloads / "Kindroid TTS.mp3"

            def export(text, settings, destination, stop, stage, progress):
                stage("Rendering paragraph 2 of 3")
                progress(1, 3)
                rendering.set()
                return target if finish.wait(2) else None

            audio.voice.export_mp3.side_effect = export
            window = Window(audio, root / ".env")
            window.tts_playback.mute.setChecked(True)
            window.tts_playback.speed.setValue(111)
            window.tts_playback.bands[2].setValue(3)
            window.tabs.setCurrentWidget(window.export_mp3_button.parentWidget())
            window.show()
            with patch.object(QStandardPaths, "writableLocation", return_value=str(downloads)) as location:
                window.export_mp3_button.click()
            location.assert_called_once_with(QStandardPaths.StandardLocation.DownloadLocation)
            self.assertTrue(rendering.wait(2))
            app.processEvents()
            self.assertFalse(window.export_mp3_button.isEnabled())
            self.assertFalse(window.preview_button.isEnabled())
            self.assertIn("Rendering paragraph 2 of 3", window.status.text())
            self.assertEqual((window.progress.value(), window.progress.maximum()), (1, 3))
            window.tabs.setCurrentIndex(0)
            self.assertTrue(window.progress.isVisible())
            _, settings, destination, *_ = audio.voice.export_mp3.call_args.args
            self.assertEqual((settings.speed, settings.treble, destination), (1.11, 3, downloads))
            self.assertEqual(audio.voice.load_voice.call_args.args[0], settings)
            finish.set()
            self.assertTrue(window.worker.wait(2000))
            app.processEvents()
            self.assertEqual(window.status.text(), f"Saved MP3 · {target}")
            self.assertTrue(window.export_mp3_button.isEnabled())
            self.assertFalse(window.progress.isVisible())
            audio.voice.speak.assert_not_called()
            window.close()

    def test_tts_experiments_only_change_chat_on_explicit_save(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "custom.safetensors").touch()
            (root / "english-7m.yaml").touch()
            audio = Mock()
            audio.chat_model = "multilingual"
            audio.voice.library = VoiceLibrary(root)
            window = Window(audio, root / ".env")
            self.assertEqual(window.tabs.count(), 5)
            speed = window.tts_playback.speed
            self.assertEqual((speed.minimum(), speed.maximum(), speed.value(), speed.singleStep()), (80, 120, 100, 1))
            window.tts_playback.mute.setChecked(True)
            window.preview_button.click()
            self.assertIsNone(window.worker)
            self.assertIn("Playback off", window.status.text())
            audio.voice.speak.assert_not_called()
            self.assertEqual(window.tabs.tabText(0), "Models")
            self.assertEqual(window.voices.currentData(), "original")
            window.models.setCurrentIndex(window.models.findData("7m"))
            self.assertEqual(window.voices.currentData(), "alba")
            window.temperature.setValue(0.55)
            window.tts_playback.mute.setChecked(False)
            speed.setValue(113)
            window.tts_playback.bands[1].setValue(-3)
            self.assertEqual(audio.voice.library.chat().speed, 1.0)
            self.assertEqual(audio.voice.library.chat().preset, "original")
            window.save_voice_button.click()
            window.close()
            restored = Window(audio, root / ".env")
            self.assertEqual(restored.models.currentData(), "7m")
            self.assertEqual(restored.temperature.value(), 0.55)
            self.assertEqual(restored.tts_playback.speed.value(), 113)
            self.assertEqual(restored.chat_playback.speed.value(), 113)
            self.assertEqual(restored.chat_playback.bands[1].value(), -3)
            self.assertIn("Alba", restored.chat_voice_label.text())
            restored.close()
            app.processEvents()

    def test_independent_stt_and_tts_tabs_without_credentials_or_provider(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        audio = Mock()
        audio.capture.side_effect = [Recording(np.zeros(16000), "3.5 s pause detected"), None]
        audio.transcribe.return_value = "First phrase."
        with TemporaryDirectory() as temp, patch("kindroid_voice.__main__.read_credentials") as credentials, \
                patch("kindroid_voice.__main__.requests.Session") as http, \
                patch("kindroid_voice.__main__.stream_reply") as send:
            audio.voice.library = VoiceLibrary(Path(temp))
            audio.chat_model = "multilingual"
            window = Window(audio, Path(temp) / ".env")
            window.show()
            stt, tts = window.tabs.indexOf(window.record_button.parentWidget()), window.tabs.indexOf(window.tts_text.parentWidget())
            self.assertEqual(window.tabs.tabText(stt), "STT")
            self.assertEqual((window.stt_pause.value(), window.chunk_pause.value(), window.stt_threshold.value(),
                              window.stt_threads.value()), (4, 1, -50, 4))
            self.assertEqual(window.tabs.tabText(tts), "TTS")
            window.tabs.setCurrentIndex(stt)
            self.assertEqual(window.stt_model.currentData(), "multilingual")
            window.stt_model.setCurrentIndex(window.stt_model.findData("fluid-speech-pro"))
            window.stt_pause.setValue(3.5)
            window.stt_threshold.setValue(-50)
            window.stt_threads.setValue(2)
            window.record_button.click()
            self.assertIn("STT", window.status.text())
            self.assertTrue(window.progress.isVisible())
            self.assertFalse(window.start_button.isEnabled())
            window.tabs.setCurrentIndex(tts)
            self.assertFalse(window.preview_button.isEnabled())
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertIsNone(window.worker)
            self.assertEqual(window.status.text(), "Dictation complete")
            self.assertIn("3.5 s pause detected · 1.0 s recorded", window.mic_status.text())
            self.assertEqual(audio.capture.call_args.kwargs["pause_seconds"], 3.5)
            self.assertEqual(audio.capture.call_args.kwargs["threshold_dbfs"], -50)
            audio.select_model.assert_called_once_with("fluid-speech-pro", 2)
            self.assertEqual(window.transcript.toPlainText(), "First phrase.")
            audio.voice.check.assert_not_called()
            audio.voice.speak.assert_not_called()
            window.tabs.setCurrentIndex(stt)
            window.record_button.click()
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertIn("No speech detected in 30 s", window.status.text())
            window.tabs.setCurrentIndex(tts)
            window.tts_text.setPlainText("  ")
            window.preview_button.click()
            self.assertIsNone(window.worker)
            window.tts_text.setPlainText("Read this typed sentence.")
            window.preview_button.click()
            self.assertIn("TTS", window.status.text())
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertEqual(window.status.text(), "Playback finished")
            self.assertEqual(audio.voice.speak.call_args.args[0], "Read this typed sentence.")
            audio.voice.speak.assert_called_once()
            self.assertEqual(audio.prepare.call_count, 2)
            self.assertEqual(audio.capture.call_count, 2)
            audio.transcribe.assert_called_once()
            self.assertFalse(window.history.entries)
            self.assertFalse(window.progress.isVisible())
            audio.wake.assert_not_called()
            credentials.assert_not_called()
            http.assert_not_called()
            send.assert_not_called()
            window.close()

    def test_transcript_retention_after_final_chunk_error_empty_result_or_cancel(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        # Hold the microphone open until the preview is visible in the real widget.
        for ending in ("success", "cancel", "empty", "decode error", "capture error", "clear", "clear and cancel"):
            with self.subTest(ending=ending), TemporaryDirectory() as directory:
                audio = Mock(chat_model="multilingual")
                audio.voice.library = VoiceLibrary(Path(directory))
                final = {"empty": "", "decode error": RuntimeError("fixture")}.get(ending, "Final phrase.")
                audio.transcribe.side_effect = ["First phrase.", final]
                resume = Event()
                def capture(deadline, stop, stage, *, on_block, **options):
                    block = np.full((1600, 1), 1000, dtype="int16")
                    on_block(block, True)
                    for _ in range(13):
                        on_block(block * 0, False)
                    self.assertTrue(resume.wait(3))
                    if ending == "capture error":
                        raise VoiceError("Microphone audio was interrupted.")
                    on_block(block * 2, True)
                    return Recording(block, "Finished manually")
                audio.capture.side_effect = capture
                window = Window(audio, audio.voice.library.root / ".env")
                window.transcript.setPlainText("Previous take.")
                window.chunk_pause.setValue(1.2)
                window.record_button.click()
                deadline = time.monotonic() + 2
                while "First phrase." not in window.transcript.toPlainText() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.005)
                try:
                    self.assertEqual(window.transcript.toPlainText(), "Previous take.\nFirst phrase.")
                    self.assertTrue(window.worker.isRunning())
                    if ending.startswith("clear"):
                        window.clear_log_button.click()
                        self.assertEqual(window.transcript.toPlainText(), "")
                        self.assertTrue(window.worker.isRunning())
                    if ending in ("cancel", "clear and cancel"):
                        window.stop_button.click()
                finally:
                    resume.set()
                    self.assertTrue(window.worker.wait(3000))
                    app.processEvents()
                if ending in ("cancel", "clear and cancel"):
                    expected = "Previous take." if ending == "cancel" else ""
                    self.assertEqual(window.transcript.toPlainText(), expected)
                    audio.transcribe.assert_called_once()
                elif ending in ("success", "clear"):
                    expected = "Previous take.\nFirst phrase. Final phrase." if ending == "success" else "Final phrase."
                    self.assertEqual(window.transcript.toPlainText(), expected)
                    self.assertEqual(audio.transcribe.call_count, 2)
                    self.assertIn("2 chunks · 4 words", window.mic_status.text())
                else:
                    self.assertEqual(window.transcript.toPlainText(), "Previous take.\nFirst phrase.")
                    if ending == "empty":
                        self.assertIn("1 chunk returned no text", window.mic_status.text())
                        self.assertIn("2 words", window.mic_status.text())
                    else:
                        self.assertIn("partial transcript kept", window.mic_status.text())
                window.close()

    def test_paragraph_gaps_follow_recorded_audio_even_with_pending_decodes(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory:
            audio = Mock(chat_model="multilingual")
            audio.voice.library = VoiceLibrary(Path(directory))
            captured = Event()
            results = iter(["One.", "Two.", "Three.", "", "Four."])

            def decode(samples):
                self.assertTrue(captured.wait(2))
                return next(results)

            def capture(deadline, stop, stage, *, on_block, **options):
                block = np.ones((1600, 1), dtype="int16")
                silence = np.zeros_like(block)
                for gap in (10, 22, 23, 10, 40):
                    on_block(block, True)
                    for _ in range(gap):
                        on_block(silence, False)
                captured.set()
                return Recording(block, "4 s pause detected")

            audio.transcribe.side_effect = decode
            audio.capture.side_effect = capture
            window = Window(audio, audio.voice.library.root / ".env")
            self.assertEqual(window.paragraph_pause.value(), 2.0)
            window.paragraph_pause.setValue(2.3)
            window.record_button.click()
            self.assertTrue(window.worker.wait(3000))
            app.processEvents()
            self.assertEqual(window.transcript.toPlainText(), "One. Two. Three.\n\nFour.")
            self.assertEqual(audio.transcribe.call_count, 5)
            window.clear_log_button.click()
            self.assertEqual(window.transcript.toPlainText(), "")
            self.assertEqual(window.paragraph_pause.value(), 2.3)
            window.close()

    def test_trigger_listener_promotes_current_preview_and_expires_ambient_text_in_audio_order(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        audio = LocalAudio(None, Path("multilingual"))
        first_batch = Event()
        # Recognition lags capture across the first four-second boundary.
        phrases = iter(["Johannah is here", "rolled out", "the current topic", "Oh hey,", "HENNAH, tell me", "more Hanna please",
                        "next turn", "Hena.", "hello again, conversation off.", "another ignored phrase"])
        def decode(samples):
            self.assertTrue(first_batch.wait(3))
            return next(phrases)

        rms = []
        for pause in (40, 10, 10, 10, 10, 40, 40, 10, 40, 40):
            rms.extend([1000] * 3 + [0] * pause)
        stream = Mock()
        def read(count):
            if rms:
                return np.full((count, 1), rms.pop(0), dtype="int16"), False
            first_batch.set()
            time.sleep(0.005)
            return np.zeros((count, 1), dtype="int16"), False
        stream.read.side_effect = read
        with TemporaryDirectory() as directory, patch.object(audio, "prepare"), \
                patch.object(audio, "transcribe", side_effect=decode) as stt, \
                patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value = stream
            audio.voice.library = VoiceLibrary(Path(directory))
            window = Window(audio, audio.voice.library.root / ".env")
            window.show()
            window.stt_mode.setCurrentIndex(2)
            window.trigger_words.setText(" , ")
            window.record_button.click()
            self.assertIsNone(window.worker)
            window.trigger_words.setText("Hennah, Hena")
            snapshots = []
            window.transcript.textChanged.connect(lambda: snapshots.append(window.transcript.toPlainText()))
            window.record_button.click()
            expected = ("the current topic Oh hey, HENNAH, tell me more Hanna please\nnext turn\n"
                        "Hena. hello again, conversation off.")
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    app.processEvents()
                    if snapshots and snapshots[-1] == expected and any("another ignored phrase" in s for s in snapshots):
                        break
                    time.sleep(0.005)
                self.assertEqual(window.transcript.toPlainText(), expected)
                self.assertIn("[Listening] Johannah is here", snapshots)
                self.assertIn("[Listening] the current topic Oh hey,", snapshots)
                self.assertFalse(window.triggered)
                self.assertIn("Listening for a start phrase", window.status.text())
                self.assertFalse(window.finish_button.isEnabled())
                self.assertTrue(window.worker.isRunning())
            finally:
                window.stop()
                self.assertTrue(window.worker.wait(3000))
                app.processEvents()
            self.assertEqual(window.transcript.toPlainText(), expected)
            mic.assert_called_once()
            window.close()

    def test_conversation_commands_span_chunks_but_never_the_finish_pause(self):
        gate = TriggerGate(("conversation on", "start here please"))
        self.assertEqual(list(gate.commands("Hannah, conversations on, conversation office.")), [])
        self.assertEqual(list(gate.commands("Conversation.")), [])
        self.assertEqual(list(gate.commands("ON, tell me more")), [("on", "Conversation ON", 2)])
        self.assertEqual(list(gate.commands("Start here")), [])
        self.assertEqual(list(gate.commands("please continue")), [("on", "Start here please", 6)])
        self.assertEqual(list(gate.commands("Conversation")), [])
        gate.tail.clear()
        self.assertEqual(list(gate.commands("off")), [])
        self.assertEqual(list(gate.commands("Conversation")), [])
        self.assertEqual(list(gate.commands("OFF!")), [("off", "Conversation OFF", 3)])
        self.assertEqual([cmd for cmd, _, _ in gate.commands("Conversation on. Conversation off.")], ["on", "off"])

    def test_spoken_off_returns_to_standby_and_next_on_reuses_the_microphone(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch.object(sounddevice, "InputStream") as mic, \
                patch("kindroid_voice.__main__.ConversationSpotter") as spotter:
            spotter.return_value.accept.return_value = ""
            audio = LocalAudio(None, Path("multilingual"))
            audio.voice.library = VoiceLibrary(Path(directory))
            audio.prepare = Mock()
            window = Window(audio, audio.voice.library.root / ".env")
            self.assertEqual(window.trigger_words.text(), "conversation on")
            for mode, addressed in ((2, True), (3, False)):
                with self.subTest(mode=mode):
                    window.clear_transcript()
                    window.stt_mode.setCurrentIndex(mode)
                    # Delay all recognition until capture has queued four seconds
                    # of silence and another start after the spoken hang-up.
                    phrases = [("Conversation", 10), ("on, keep this.", 40)]
                    if addressed:
                        phrases.append(("Conversation on, unfinished take.", 10))
                    phrases.extend([("Conversation", 10), ("off.", 40), ("Ambient only.", 40),
                                    ("Conversation on again.", 40)])
                    drained = Event()
                    values = iter(v for _, gap in phrases for v in [1000] + [0] * gap)
                    def read(count):
                        level = next(values, None)
                        if level is None:
                            drained.set()
                            time.sleep(0.002)
                        return np.full((count, 1), level or 0, dtype="int16"), False
                    lines = iter(line for line, _ in phrases)
                    def decode(samples):
                        self.assertTrue(drained.wait(2))
                        return next(lines)
                    audio.transcribe = Mock(side_effect=decode)
                    mic.return_value.__enter__.return_value.read.side_effect = read
                    window.record_button.click()
                    finished_turns = []
                    window.worker.turn_finished.connect(finished_turns.append)
                    try:
                        deadline = time.monotonic() + 3
                        while "Conversation on again." not in finished_turns and time.monotonic() < deadline:
                            app.processEvents()
                            time.sleep(0.005)
                        self.assertEqual(finished_turns, ["Conversation on, keep this.", "Conversation on again."])
                        closing = ("Conversation on, unfinished take. " if addressed else "") + "Conversation off."
                        self.assertEqual(window.transcript.toPlainText(),
                                         "\n".join([finished_turns[0], closing, finished_turns[1]]))
                        self.assertTrue(window.triggered)
                        self.assertTrue(window.worker.isRunning())
                        self.assertTrue(window.stop_button.isEnabled())
                    finally:
                        window.stop()
                        self.assertTrue(window.worker.wait(1500))
                        app.processEvents()
                    self.assertIsNone(window.worker)
                    self.assertTrue(window.record_button.isEnabled())
                    self.assertFalse(window.stop_button.isEnabled())
                    self.assertFalse(window.level_meter.running)
                    self.assertEqual(audio.transcribe.call_count, len(phrases))
            self.assertEqual(mic.return_value.__exit__.call_count, 2)
            window.close()

    def test_opening_wait_starts_at_ui_confirmation_and_subsequent_turn_uses_finish_pause(self):
        app = QApplication.instance() or QApplication([])
        audio = LocalAudio(None, Path("multilingual"))
        audio.prepare = Mock()
        decoded = Event()
        phrases = iter(["Conversation on.", "First reply.", "Second reply."])
        levels = [1000] + [0] * 8
        def read(count):
            time.sleep(0.003)
            return np.full((count, 1), levels.pop(0) if levels else 0, dtype="int16"), False
        def decode(samples):
            decoded.set()
            return next(phrases)
        def until(predicate):
            end = time.monotonic() + 2
            while not predicate() and time.monotonic() < end:
                app.processEvents()
                time.sleep(0.003)
            self.assertTrue(predicate())
        with TemporaryDirectory() as directory, patch.object(sounddevice, "InputStream") as mic:
            audio.voice.library = VoiceLibrary(Path(directory))
            audio.transcribe = Mock(side_effect=decode)
            mic.return_value.__enter__.return_value.read.side_effect = read
            window = Window(audio, audio.voice.library.root / ".env")
            window.stt_mode.setCurrentIndex(2)
            window.initial_pause.setValue(0.4)
            window.stt_pause.setValue(0.2)
            window.record_button.click()
            turns = []
            window.worker.turn_finished.connect(turns.append)
            try:
                self.assertTrue(decoded.wait(1))
                time.sleep(0.45)  # Deliberately delay the visual confirmation beyond both pauses.
                until(lambda: window.triggered)
                until(lambda: "initial wait" in window.mic_status.text())
                self.assertEqual(turns, [])
                until(lambda: turns == ["Conversation on."])
                self.assertTrue(window.triggered)
                levels.extend([1000, 0, 0, 1000, 0, 0])
                until(lambda: len(turns) == 3)
                self.assertEqual(turns[1:], ["First reply.", "Second reply."])
                self.assertTrue(window.worker.isRunning())
            finally:
                window.stop()
                self.assertTrue(window.worker.wait(1500))
                app.processEvents()
                window.close()

    def test_compare_start_phrase_in_quiet_audio_and_lagged_transcripts(self):
        app = QApplication.instance() or QApplication([])
        audio = LocalAudio(None, Path("multilingual"))
        captured = Event()
        decoded = []
        lines = iter(["Conservation on.", "Conversation on, hello.", "Conversation on again.", "Hannah."])

        def transcribe(samples):
            self.assertTrue(captured.wait(3))
            decoded.append(samples.ravel())
            return next(lines)

        # 1 and 2 are below both volume thresholds. The first acoustic hit must
        # carry its preceding quiet audio into the take. Later intervals cover
        # transcript-only, both detectors, and neither.
        levels = iter([1] * 10 + [2] + [0] * 40 + [1000] + [0] * 40 +
                      [1000, 2] + [0] * 40 + [1000] + [0] * 40)

        def read(count):
            level = next(levels, None)
            if level is None:
                captured.set()
                time.sleep(0.005)
            return np.full((count, 1), level or 0, dtype="int16"), False

        with TemporaryDirectory() as directory, patch.object(audio, "prepare"), \
                patch.object(audio, "transcribe", side_effect=transcribe), \
                patch("kindroid_voice.__main__.ConversationSpotter") as spotter, \
                patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value.read.side_effect = read
            spotter.return_value.accept.side_effect = lambda b: "Conversation on" if b[0, 0] == 2 else ""
            audio.voice.library = VoiceLibrary(Path(directory))
            window = Window(audio, audio.voice.library.root / ".env")
            window.stt_mode.setCurrentText("Compare conversation detectors")
            window.acoustic_threshold.setValue(0.15)
            window.record_button.click()
            try:
                deadline = time.monotonic() + 4
                while sum(window.detector_counts.values()) < 4 and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.005)
                self.assertEqual(window.detector_counts, {(True, False): 1, (False, True): 1,
                                                         (True, True): 1, (False, False): 1})
                self.assertIn("Completed phrase 4", window.detector_status.text())
                self.assertEqual(window.transcript.toPlainText(),
                                 "Conservation on. Conversation on, hello.\nConversation on again.\nHannah.")
                self.assertEqual(np.count_nonzero(decoded[0] == 1), 16000)
                self.assertTrue(window.level_meter.running)
                spotter.assert_called_once_with(0.15)
                mic.assert_called_once()
            finally:
                window.stop()
                self.assertTrue(window.worker.wait(3000))
                app.processEvents()
                self.assertFalse(window.level_meter.running)
                window.close()

    def test_meter_uses_sample_peak_and_holds_then_falls_independently(self):
        from kindroid_voice.level_meter import LevelMeter

        app = QApplication.instance() or QApplication([])
        meter = LevelMeter()
        impulse = np.zeros((1600, 1), dtype="int16")
        impulse[0, 0] = -32768
        finish = Event()
        with patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value.read.return_value = impulse, False
            LocalAudio(None, None).capture(None, Event(), Mock(), pause_seconds=None, finish=finish,
                                           on_meter=meter.set_levels, on_level=lambda *_: finish.set())
        self.assertAlmostEqual(meter.level, -32.04, places=2)
        self.assertEqual(meter.peak, 0.0)
        end_hold = meter.hold_until
        with patch("kindroid_voice.level_meter.time.monotonic", side_effect=[end_hold - 0.1, end_hold + 0.5]):
            meter.set_levels(-60, -42)
            self.assertEqual((meter.level, meter.peak), (-60, 0))
            meter.set_levels(-70, -50)
            self.assertEqual((meter.level, meter.peak), (-70, -9))
        self.assertFalse(meter.grab().isNull())
        meter.reset()
        self.assertFalse(meter.running)

    def test_idle_preview_cleanup_after_clear_stop_or_capture_failure(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        for ending in ("clear", "stop", "failure"):
            with self.subTest(ending=ending), TemporaryDirectory() as directory:
                audio = Mock(chat_model="multilingual")
                audio.voice.library = VoiceLibrary(Path(directory))
                proceed = Event()
                def capture(deadline, stop, stage, *, on_block, **options):
                    block = np.ones((1600, 1), dtype="int16")
                    on_block(block, True)
                    for _ in range(10):
                        on_block(block, False)
                    if not proceed.wait(3) or ending == "failure":
                        raise VoiceError("Microphone interrupted.")
                    return None
                audio.capture.side_effect = capture
                audio.transcribe.return_value = "ambient fixture"
                window = Window(audio, audio.voice.library.root / ".env")
                window.transcript.setPlainText("Completed take.")
                window.stt_mode.setCurrentIndex(2)
                window.record_button.click()
                deadline = time.monotonic() + 2
                try:
                    while not window.listening_preview and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.005)
                    self.assertIn("[Listening] ambient fixture", window.transcript.toPlainText())
                    if ending == "clear":
                        window.clear_log_button.click()
                    if ending != "failure":
                        window.stop_button.click()
                finally:
                    proceed.set()
                    self.assertTrue(window.worker.wait(3000))
                    app.processEvents()
                self.assertEqual(window.transcript.toPlainText(), "" if ending == "clear" else "Completed take.")
                self.assertFalse(window.listening_preview)
                window.close()

    def test_quiet_trigger_sensitivity_switches_back_for_addressed_speech(self):
        waiting, stop = Event(), Event()
        waiting.set()
        activity = []
        def accept(block, active):
            activity.append(active)
            waiting.clear()
            if len(activity) == 2:
                stop.set()
        # About -56 dBFS, between the idle and addressed thresholds.
        with patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value.read.return_value = (np.full((1600, 1), 52, dtype="int16"), False)
            LocalAudio(None, None).capture(time.monotonic() + 1, stop, Mock(), pause_seconds=4,
                                           threshold_dbfs=-50, awaiting_trigger=waiting.is_set,
                                           listening_threshold_dbfs=-60, on_block=accept)
        self.assertEqual(activity, [True, False])

    def test_one_hour_of_continuous_audio_has_bounded_chunks_and_no_whole_take_buffer(self):
        sizes = []
        chunks = PauseChunks(1, 2, lambda samples, paragraph: sizes.append(len(samples)))
        loud = np.full((1600, 1), 1000, dtype="int16")
        quiet = np.zeros_like(loud)
        remaining = 36000
        def read(count):
            nonlocal remaining
            remaining -= 1
            return (loud if remaining >= 0 else quiet), False
        with patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value.read.side_effect = read
            recording = LocalAudio(None, None).capture(None, Event(), Mock(), pause_seconds=4,
                                                       on_block=chunks.accept)
        chunks.flush()
        self.assertEqual(recording.seconds, 3604)
        self.assertIsNone(recording.samples)
        self.assertEqual(sum(sizes), 3600 * 16000)
        self.assertLessEqual(max(sizes), 8 * 16000)

    def test_quiet_preroll_and_endpoint(self):
        levels = [0] * 8 + [1000] * 4 + [0] * 9
        stream = Mock()
        stream.read.side_effect = [(np.full((1600, 1), v, dtype="int16"), False) for v in levels]
        stage = Mock()
        with patch.object(sounddevice, "InputStream") as open_mic:
            open_mic.return_value.__enter__.return_value = stream
            recording = LocalAudio(None, None).capture(time.monotonic() + 3, Event(), stage)
            self.assertEqual(len(recording.samples), (3 + 4 + 9) * 1600)
            self.assertEqual(recording.ended_by, "0.9 s pause detected")
        open_mic.return_value.__exit__.assert_called_once()
        self.assertEqual([call.args[0] for call in stage.call_args_list],
                         ["Listening · waiting for speech", "Recording · pause to finish"])

    def test_longer_pause_resets_on_quiet_speech(self):
        levels = [0] * 5 + [150] + [0] * 34 + [150] + [0] * 35
        stream = Mock()
        stream.read.side_effect = [(np.full((1600, 1), v, dtype="int16"), False) for v in levels]
        meter = Mock()
        with patch.object(sounddevice, "InputStream") as mic:
            mic.return_value.__enter__.return_value = stream
            recording = LocalAudio(None, None).capture(time.monotonic() + 30, Event(), Mock(),
                                                       pause_seconds=3.5, threshold_dbfs=-50, on_level=meter)
        self.assertEqual(recording.ended_by, "3.5 s pause detected")
        self.assertEqual(len(recording.samples), (3 + 1 + 34 + 1 + 35) * 1600)
        self.assertEqual(meter.call_args.args[-1], 35)
        self.assertAlmostEqual(meter.call_args_list[5].args[0], -46.8, places=1)

    def test_manual_recording_keeps_silence_until_finish_or_cancel(self):
        for cancel in (False, True):
            stop, finish = Event(), Event()
            def meter(level, blocks, quiet):
                if blocks == 40:
                    (stop if cancel else finish).set()
            with patch.object(sounddevice, "InputStream") as mic:
                mic.return_value.__enter__.return_value.read.return_value = (np.zeros((1600, 1), dtype="int16"), False)
                recording = LocalAudio(None, None).capture(time.monotonic() + 30, stop, Mock(),
                                                           pause_seconds=None, finish=finish, on_level=meter)
            if cancel:
                self.assertIsNone(recording)
            else:
                self.assertEqual(len(recording.samples), 64000)
                self.assertEqual(recording.ended_by, "Finished manually")

    def test_deadline_returns_to_wake_without_transcribing(self):
        with patch.object(sounddevice, "InputStream") as open_mic:
            self.assertIsNone(LocalAudio(None, None).capture(time.monotonic(), Event(), Mock()))
        open_mic.return_value.__enter__.return_value.read.assert_not_called()

    def test_worker_progress_stop_failure_and_restart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        def until(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertTrue(predicate())

        audio = Mock()
        def capture(deadline, stop, stage, **options):
            stage("Listening · waiting for speech")
            stop.wait(3)
            return None
        audio.capture.side_effect = capture
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env_file = Path(temp.name) / ".env"
        audio.voice.library = VoiceLibrary(Path(temp.name))
        audio.chat_model = "multilingual"
        window = Window(audio, env_file)
        window.show()
        self.assertFalse(window.history.parentWidget().findChildren(QLineEdit))
        window.start_button.click()
        self.assertIn(".env", window.status.text())
        self.assertIsNone(window.worker)
        env_file.write_text("KINDROID_API_KEY=test\nKINDROID_AI_ID=\n", encoding="utf-8")
        window.start_button.click()
        self.assertIsNone(window.worker)
        env_file.write_text('KINDROID_API_KEY="test" # local\nKINDROID_AI_ID=character\n', encoding="utf-8-sig")
        window.start_button.click()
        self.assertEqual((window.worker.key, window.worker.kin_id), ("test", "character"))
        self.assertTrue(window.progress.isVisible())
        self.assertTrue(window.start_button.isEnabled())
        self.assertEqual(window.start_button.text(), "Stop")
        until(lambda: "waiting for speech" in window.status.text())
        window.start_button.click()
        self.assertIn("Stopping", window.status.text())
        until(lambda: window.worker is None)
        self.assertEqual(window.status.text(), "Stopped")
        audio.capture.side_effect = VoiceError("Microphone unavailable")
        window.start_button.click()
        until(lambda: window.worker is None)
        self.assertEqual(window.status.text(), "Microphone unavailable")
        self.assertFalse(window.progress.isVisible())
        self.assertTrue(window.start_button.isEnabled())
        env_file.unlink()
        window.preview_button.click()
        self.assertTrue(window.progress.isVisible())
        self.assertFalse(window.clone_button.isEnabled())
        until(lambda: window.worker is None)
        self.assertEqual(window.status.text(), "Playback finished")
        audio.voice.speak.assert_called_once()
        self.assertTrue(window.clone_button.isEnabled())
        window.close()
