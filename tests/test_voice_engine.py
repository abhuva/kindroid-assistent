from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

try:
    import sounddevice
except ImportError:
    sounddevice = None

from kindroid_voice.pocket import PocketVoice
from kindroid_voice.presets import Playback
from kindroid_voice.session import VoiceError


@unittest.skipIf(sounddevice is None, "requires the local voice runtime")
class ParagraphPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.voice = PocketVoice(Path("unused"))
        self.voice.engine = Mock(rate=24000)
        self.voice.engine.settings = self.voice.playback = Playback("18m", "alba", 0.3)
        self.stop = Event()
        self.stage = Mock()

    def test_next_paragraph_generates_during_playback_on_one_stream(self):
        second_started, playing_first = Event(), Event()
        def render(text, cancel):
            if text == "Second":
                second_started.set()
                self.assertTrue(playing_first.wait(2))
            return bytes([1 if text.startswith("First") else 2]) * 400
        def write(pcm):
            if pcm[0] == 1:
                self.assertTrue(second_started.wait(2))
                playing_first.set()
        self.voice.engine.render.side_effect = render
        with patch.object(sounddevice, "RawOutputStream") as device:
            device.return_value.write.side_effect = write
            progress = Mock()
            self.voice.speak("First\r\nline\r\n \r\n\r\nSecond", self.stop, self.stage, progress)
            self.assertEqual([call.args[0] for call in self.voice.engine.render.call_args_list],
                             ["First\nline", "Second"])
            device.assert_called_once()
            self.assertEqual([call.args[0][0] for call in device.return_value.write.call_args_list], [1, 2])
            self.assertEqual(progress.call_args.args, (2, 2))
            device.return_value.stop.assert_called_once()
            device.return_value.close.assert_called_once()

    def test_stop_discards_buffer_and_aborts_device(self):
        self.voice.engine.render.return_value = bytes(12000)
        with patch.object(sounddevice, "RawOutputStream") as device:
            device.return_value.write.side_effect = lambda _: self.stop.set()
            self.voice.speak("first\n\nsecond\n\nthird", self.stop, self.stage)
            device.return_value.write.assert_called_once()
            device.return_value.abort.assert_called_once()
            self.assertLessEqual(self.voice.engine.render.call_count, 2)

    def test_later_synthesis_failure_surfaces_without_replay(self):
        self.voice.engine.render.side_effect = [bytes(40), VoiceError("engine exited")]
        with patch.object(sounddevice, "RawOutputStream") as device:
            with self.assertRaisesRegex(VoiceError, "engine exited"):
                self.voice.speak("first\n\nsecond", self.stop, self.stage)
            self.assertEqual(self.voice.engine.render.call_count, 2)
            device.return_value.write.assert_called_once()
            device.return_value.close.assert_called_once()

    def test_off_engine_never_autostarts(self):
        voice = PocketVoice(Path("unused"))
        with self.assertRaisesRegex(VoiceError, "Models"):
            voice.prepare_playback()

    def test_speed_changes_duration_and_preserves_pitch_at_the_native_device_rate(self):
        import numpy as np
        from kindroid_voice.presets import SPEECH_SPEEDS

        rate = self.voice.engine.rate
        tone = np.sin(2 * np.pi * 165 * np.arange(rate) / rate).astype("<f4").tobytes()
        self.voice.engine.render.return_value = tone
        for speed in (*SPEECH_SPEEDS, 0.80, 0.97, 1.01, 1.20):
            with self.subTest(speed=speed), patch.object(sounddevice, "RawOutputStream") as device:
                self.voice.playback = Playback("18m", "alba", 0.3, speed)
                self.voice.engine.render.reset_mock()
                self.voice.speak("One paragraph", self.stop, self.stage)
                if not speed:
                    device.assert_not_called()
                    self.voice.engine.render.assert_not_called()
                    continue
                self.assertEqual(device.call_args.kwargs["samplerate"], rate)
                pcm = b"".join(call.args[0] for call in device.return_value.write.call_args_list)
                self.assertEqual(len(pcm), round(rate / speed) * 4)
                if speed == 1:
                    self.assertEqual(pcm, tone)
                audio = np.frombuffer(pcm, dtype="<f4")
                spectrum = abs(np.fft.rfft(audio * np.hanning(len(audio))))
                self.assertAlmostEqual(np.argmax(spectrum) * rate / len(audio), 165, delta=2)
                self.voice.engine.load.assert_not_called()

    def test_playback_rejects_a_mismatched_resident_voice(self):
        self.voice.engine.settings = Playback("7m", "alba", 0.3)
        self.voice.playback = Playback("18m", "alba", 0.3)
        with self.assertRaisesRegex(VoiceError, "Open Models"):
            self.voice.prepare_playback()
        self.voice.engine.load.assert_not_called()

    def test_speed_and_eq_take_effect_during_the_same_output_stream(self):
        import numpy as np

        rate = self.voice.engine.rate
        tone = (0.1 * np.sin(2 * np.pi * 120 * np.arange(rate) / rate)).astype("<f4")
        self.voice.engine.render.return_value = tone.tobytes()
        self.voice.output_meter = Mock()
        blocks = []

        def write(block):
            blocks.append(np.frombuffer(block, dtype="<f4"))
            if len(blocks) == 1:
                self.voice.playback = replace(self.voice.playback, speed=1.20, bass=9)

        with patch.object(sounddevice, "RawOutputStream") as device:
            device.return_value.write.side_effect = write
            self.voice.speak("One continuous segment.", self.stop, self.stage)

        device.assert_called_once()
        self.voice.engine.render.assert_called_once()
        self.voice.engine.load.assert_not_called()
        np.testing.assert_array_equal(blocks[0], tone[:len(blocks[0])])
        self.assertLess(sum(map(len, blocks)), rate * 0.87)
        self.assertGreater(float(np.abs(blocks[8]).max()), 0.17)
        for block, call in zip(blocks, self.voice.output_meter.call_args_list, strict=True):
            rms, peak = call.args
            self.assertAlmostEqual(rms, 10 * np.log10(float(np.mean(block * block))), places=4)
            self.assertAlmostEqual(peak, 20 * np.log10(float(np.abs(block).max())), places=4)

    def test_mp3_export_uses_both_paragraphs_and_preserves_an_existing_file(self):
        import numpy as np
        import soundfile as sf

        rate = self.voice.engine.rate
        tone = (0.1 * np.sin(2 * np.pi * 120 * np.arange(rate // 2) / rate)).astype("<f4")
        self.voice.engine.render.return_value = tone.tobytes()
        settings = replace(self.voice.playback, speed=1.20, bass=6)
        with TemporaryDirectory() as directory, patch("kindroid_voice.pocket.time.strftime", return_value="Kindroid TTS"), \
                patch.object(sounddevice, "RawOutputStream") as device:
            downloads = Path(directory)
            existing = downloads / "Kindroid TTS.mp3"
            existing.write_bytes(b"previous file")
            progress = Mock()
            result = self.voice.export_mp3("First.\n\nSecond.", settings, downloads, self.stop, self.stage, progress)
            self.assertEqual(result.name, "Kindroid TTS (2).mp3")
            self.assertEqual(existing.read_bytes(), b"previous file")
            self.assertEqual(sorted(downloads.iterdir()), sorted([existing, result]))
            self.assertEqual(sf.info(result).format, "MP3")
            samples, sample_rate = sf.read(result, dtype="float32")
            self.assertEqual(sample_rate, rate)
            self.assertEqual(len(samples), 20000)
            self.assertGreater(float(np.abs(samples).max()), 0.12)
            self.assertEqual(progress.call_args.args, (2, 2))
            device.assert_not_called()
        self.assertEqual([call.args[0] for call in self.voice.engine.render.call_args_list], ["First.", "Second."])

    def test_stopping_mp3_export_cleans_up_partial_audio(self):
        def render(text, stop):
            if text == "Second.":
                stop.set()
            return bytes(8000)

        self.voice.engine.render.side_effect = render
        with TemporaryDirectory() as directory:
            downloads = Path(directory)
            result = self.voice.export_mp3("First.\n\nSecond.", self.voice.playback, downloads,
                                           self.stop, self.stage, Mock())
            self.assertIsNone(result)
            self.assertEqual(list(downloads.iterdir()), [])
