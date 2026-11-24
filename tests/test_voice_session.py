import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch

from kindroid_voice.audio import FLUID_PROFILE, LocalAudio
from kindroid_voice.provider import decode_reply, history_rows, read_history
from kindroid_voice.session import CallInput, VoiceError, conversation, receive_reply


class VoiceSessionTests(unittest.TestCase):
    def test_fluid_requires_external_weights_before_loading_dictate(self):
        with TemporaryDirectory() as directory:
            dictate = Path(directory)
            upstream = dictate / "upstream"
            upstream.mkdir()
            (upstream / "local_stt.py").write_text("raise AssertionError('Incomplete model loaded')", encoding="utf-8")
            model = dictate / "models" / "fluid-speech-pro"
            model.mkdir(parents=True)
            # Keep the small files at their declared sizes and omit the external weights.
            files = json.loads(FLUID_PROFILE.read_text(encoding="utf-8"))["files"]
            for entry in files:
                if not entry["name"].endswith(".data"):
                    (model / entry["name"]).write_bytes(bytes(entry["bytes"]))
            audio = LocalAudio(dictate, model)
            with self.assertRaisesRegex(VoiceError, "setup-fluid-speech-pro"):
                audio.prepare(Event())
            self.assertIsNone(audio.recognizer)

    def test_stream_decoder_keeps_unicode_and_hides_in_band_errors(self):
        data = "   \nHi, it’s Hannah.".encode()
        self.assertEqual("".join(decode_reply((bytes([b]) for b in data), Event())), data.decode().strip())
        for marker in (b"<<HTTP_ERROR>>", b"<<ERROR>>"):
            for cut in range(1, len(marker)):
                stream = decode_reply([b"Hello. " + marker[:cut], marker[cut:] + b"private"], Event())
                self.assertEqual(next(stream), "Hello. ")
                with self.assertRaises(VoiceError) as caught:
                    next(stream)
                self.assertNotIn("private", str(caught.exception))
        with self.assertRaises(VoiceError):
            list(decode_reply([b"  "], Event()))

    def test_history_one_page_sources_dedup_spacing_and_429(self):
        try:
            import requests
        except ImportError:
            self.skipTest("requires requests")
        now = 1_800_000_000
        start_after = (now - 24 * 3600) * 1000
        items = [{"id": str(i), "sender": "user", "message": "<b>test</b>",
                  "timestamp": start_after + i + 1, **extra}
                 for i, extra in enumerate([{}, {"source": "kindroid_text"}, {"type": "call_message"},
                                            {"source": "api", "sender_type": "ai"}])]
        payload = {"messages": items + items[:1], "pagination": {"hasMore": True}}
        rows, more = history_rows(payload, start_after)
        self.assertTrue(more)
        self.assertEqual([r[1] for r in rows], ["?", "TK", "VK", "API"])
        self.assertEqual(rows[-1][0], "Hannah")
        with TemporaryDirectory() as d, patch("kindroid_voice.provider.time.time", return_value=now):
            guard = Path(d) / "next"
            response = MagicMock(status_code=200)
            response.__enter__.return_value = response
            response.iter_content.return_value = [json.dumps(payload).encode()]
            get = Mock(return_value=response)
            self.assertEqual(read_history(get, "test", "fixture", guard, Event()), (rows, True))
            self.assertEqual(get.call_args.kwargs["params"],
                             {"ai_id": "fixture", "limit": 100, "start_after_timestamp": start_after})
            with self.assertRaises(VoiceError):
                read_history(get, "test", "fixture", guard, Event())
            get.assert_called_once()
            guard.unlink()
            response.status_code = 429
            response.headers = {"Retry-After": "600"}
            with self.assertRaisesRegex(VoiceError, "429"):
                read_history(get, "test", "fixture", guard, Event())
            with self.assertRaisesRegex(VoiceError, "cooldown"):
                read_history(get, "test", "fixture", guard, Event())
            self.assertEqual(get.call_count, 2)

    def test_recent_history_displays_only_newest_ten_nonempty_messages(self):
        start_after = 1_800_000_000_000
        recent = [{"timestamp": start_after + i, "message": str(i)} for i in range(1, 13)]
        payload = {"messages": [
            {"timestamp": 1_700_000_000_000, "message": "Years ago"},
            {"timestamp": start_after, "message": "Cursor is exclusive"},
            {"message": "No timestamp"},
        ]}
        self.assertEqual(history_rows(payload, start_after), ([], False))
        payload["messages"].extend(recent + [{"timestamp": start_after + 13, "message": "  "}])
        rows, more = history_rows(payload, start_after)
        self.assertFalse(more)
        self.assertEqual([body for _, _, body in rows], [str(i) for i in range(3, 13)])

    def test_two_direct_turns_overlap_audio_and_stream_and_apply_stt_edits(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("requires voice runtime")
        audio = Mock()
        stop = Event()
        played = []
        first_played = Event()
        stages, messages, captures, sent = [], [], [], []
        settings = CallInput("multilingual", 4, -50, 0.2, 0.4, 4)
        def capture(deadline, cancel, stage, *, on_block, **opts):
            self.assertIsNone(deadline)
            if captures:
                self.assertEqual(played[-1], "Last fragment")
            captures.append(opts)
            if len(captures) == 3:
                stop.set()
                return None
            block = np.ones((1600, 1), dtype="int16")
            for gap in (4, 2):
                on_block(block, True)
                for _ in range(gap):
                    on_block(block * 0, False)
            return object()
        audio.capture.side_effect = capture
        audio.transcribe.side_effect = ["First chunk.", "Second paragraph.", "Hello", "again"]
        def speak(take_segment, token, stage):
            while piece := take_segment(token):
                played.append(piece)
                first_played.set()
        audio.voice.speak_segments.side_effect = speak
        def send(text, cancel):
            nonlocal settings
            sent.append(text)
            first_played.clear()
            yield "First sentence. "
            self.assertTrue(first_played.wait(2))
            yield "Next line\nLast fragment"
            settings = CallInput("german", 2, -55, 0.2, 0.4, 3.5)
        conversation(audio, send, stop, stages.append, lambda *args: messages.append(args), lambda: settings)
        self.assertEqual(sent, ["First chunk.\n\nSecond paragraph.", "Hello\n\nagain"])
        self.assertEqual(played, ["First sentence.", "Next line", "Last fragment"] * 2)
        self.assertEqual([c["pause_seconds"] for c in captures], [4, 3.5, 3.5])
        self.assertEqual([call.args for call in audio.select_model.call_args_list], [("multilingual", 4), ("german", 2)])
        audio.voice.load_voice.assert_called_once()
        audio.wake.assert_not_called()
        self.assertEqual([(r[2], r[3]) for r in messages if r[1] == "Marc"][:2],
                         [("First chunk.", "Dictating"), (sent[0], "Dictating")])

    def test_uncertain_request_stops_without_resending(self):
        audio = Mock()
        send = Mock(side_effect=VoiceError("Delivery is uncertain."))
        updates = []
        def capture(audio, settings, stop, stage, draft):
            draft("Hello")
            return "Hello"
        with patch("kindroid_voice.session.capture_turn", side_effect=capture):
            with self.assertRaisesRegex(VoiceError, "uncertain"):
                conversation(audio, send, Event(), Mock(), lambda *args: updates.append(args),
                             lambda: CallInput("multilingual", 4, -50, 1, 2, 4))
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "Hello")
        self.assertEqual(updates[-1][-1], "Check delivery")

    def test_audio_failure_cancels_receiving_and_keeps_partial_reply(self):
        audio = Mock()
        audio.voice.speak_segments.side_effect = VoiceError("Output device failed.")
        stop = Event()
        updates = []
        closed = Event()

        def stream(cancel):
            try:
                yield "First sentence. "
                for _ in range(200):
                    if cancel.is_set():
                        return
                    stop.wait(0.01)
                self.fail("Playback failure did not cancel the stream")
            finally:
                closed.set()

        with self.assertRaisesRegex(VoiceError, "Output device"):
            receive_reply(audio, stream, stop, Mock(), lambda *args: updates.append(args))
        self.assertTrue(closed.is_set())
        self.assertEqual(updates[-1], ("First sentence. ", "Incomplete reply"))
