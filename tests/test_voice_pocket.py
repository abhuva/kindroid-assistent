from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    import soundfile as sf
except ImportError:
    sf = None

from kindroid_voice.pocket import PocketVoice
from kindroid_voice.presets import Playback, Recipe, VoiceLibrary
from kindroid_voice.session import VoiceError


@unittest.skipIf(sf is None, "requires voice runtime")
class PocketTests(unittest.TestCase):
    def test_import_preserves_colliding_names_reuses_duplicates_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = VoiceLibrary(root / "library")
            clip = root / "speaker.wav"
            stop = Event()
            sf.write(clip, np.full(16000, 0.1), 16000)
            self.assertEqual(library.import_recording(clip, stop), clip.name)
            original = (library.samples / clip.name).read_bytes()
            sf.write(clip, np.full(16000, 0.5), 16000)
            self.assertEqual(library.import_recording(clip, stop), "speaker (2).wav")
            self.assertEqual(library.import_recording(clip, stop), "speaker (2).wav")
            self.assertEqual(library.import_recording(library.samples / clip.name, stop), clip.name)
            self.assertEqual((library.samples / clip.name).read_bytes(), original)
            bad = root / "broken.mp3"
            bad.write_bytes(b"invalid audio")
            with self.assertRaises(VoiceError):
                library.import_recording(bad, stop)
            stop.set()
            self.assertIsNone(library.import_recording(clip, stop))
            self.assertEqual(sorted(p.name for p in library.samples.iterdir()), ["speaker (2).wav", "speaker.wav"])

    def test_reclone_saves_independent_exports_and_restores_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voice = PocketVoice(root)
            voice.library.samples.mkdir()
            clip = voice.library.samples / "speech.wav"
            sf.write(clip, np.repeat([0.1, 0.5], 16000 * 4), 16000)
            (root / "custom.safetensors").write_bytes(b"original")
            first = Recipe("Hannah", (clip.name,), 0, 3, 0.3)
            second = Recipe("Hannah", (clip.name,), 4, 3, 0.55)

            def encode(reference, profile, stop):
                data, rate = sf.read(reference)
                self.assertEqual(rate, 24000)
                profile.write_text(str(round(data.max(), 1)))
                return True

            with patch.object(voice, "check"), patch.object(voice, "_export", side_effect=encode):
                a, b = [voice.build(r, Event(), Mock(), Mock()) for r in (first, second)]
            library = VoiceLibrary(root)
            self.assertEqual(library.chat().preset, "original")
            self.assertNotEqual(a, b)
            self.assertEqual(library.recipes()[b].offset, 4)
            for ident, expected in [(a, "0.1"), (b, "0.5")]:
                _, profile = library.resolve(Playback("18m", ident, 0.3))
                self.assertEqual((root / profile).read_text(), expected)
            settings = Playback("18m", b, 0.55)
            library.save_chat(settings)
            self.assertEqual(VoiceLibrary(root).chat(), settings)
            self.assertEqual((root / "custom.safetensors").read_bytes(), b"original")
            with patch.object(voice, "check"), patch.object(voice, "_export", side_effect=VoiceError("failed")):
                with self.assertRaises(VoiceError):
                    voice.build(first, Event(), Mock(), Mock())
            self.assertEqual(set(library.recipes()), {a, b})
            self.assertEqual(VoiceLibrary(root).chat(), settings)

    def test_stop_silence_and_reference_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            voice = PocketVoice(Path(directory))
            voice.library.samples.mkdir()
            sf.write(voice.library.samples / "quiet.wav", np.zeros(24000), 24000)
            stop = Event()
            stop.set()
            recipe = Recipe("sample", ("quiet.wav",))
            with patch.object(voice, "check"), patch.object(voice, "_export") as run:
                voice.build(recipe, stop, Mock(), Mock())
                with self.assertRaisesRegex(VoiceError, "silent"):
                    voice.build(recipe, Event(), Mock(), Mock())
                with self.assertRaises(VoiceError):
                    voice.build(Recipe("escape", ("../quiet.wav",)), Event(), Mock(), Mock())
                run.assert_not_called()

    def test_model_specific_voices(self):
        with tempfile.TemporaryDirectory() as directory:
            library = VoiceLibrary(Path(directory))
            self.assertEqual(library.resolve(Playback("7m", "alba", 0.2)), ("english-7m.yaml", "alba-7m.safetensors"))
            with self.assertRaises(VoiceError):
                library.save_chat(Playback("7m", "original", 0.3))
            self.assertFalse(library.preference.exists())
            library.preference.write_text('{"model":"7m","preset":"alba","temperature":0.3}', encoding="utf-8")
            self.assertEqual(library.chat().speed, 1.0)
            with self.assertRaisesRegex(VoiceError, "speaking speed"):
                library.save_chat(Playback("7m", "alba", 0.3, 2.25))
            self.assertEqual(library.chat().speed, 1.0)

    def test_native_allocation_failure_is_reported_without_a_partial_preset(self):
        with tempfile.TemporaryDirectory() as directory:
            voice = PocketVoice(Path(directory))
            voice.library.samples.mkdir()
            sf.write(voice.library.samples / "fixture.wav", np.ones(24000 * 4) * 0.2, 24000)

            def failed_export(args, **kwargs):
                self.assertEqual(Path(args[0]).name, "voice-clone.exe")
                Path(args[-1]).write_bytes(b"partial")
                child = Mock(returncode=0xC0000409)
                child.communicate.return_value = (None, b"memory allocation of 100 bytes failed")
                return Mock(__enter__=Mock(return_value=child), __exit__=Mock(return_value=False))

            with patch.object(voice, "check"), patch("subprocess.Popen", side_effect=failed_export):
                with self.assertRaisesRegex(VoiceError, "ran out of memory"):
                    voice.build(Recipe("fixture", ("fixture.wav",), seconds=3), Event(), Mock(), Mock())
            self.assertEqual(list(voice.library.presets.iterdir()), [])

    def test_installed_encoder_exports_maximum_duration_for_playback(self):
        voice = PocketVoice()
        if not all((voice.directory / name).is_file() for name in
                   ("voice-clone.exe", "voice-engine.exe", "cloning.safetensors")):
            self.skipTest("requires the installed cloning bundle")
        with tempfile.TemporaryDirectory(dir=voice.directory) as directory:
            root = Path(directory)
            voice.library = VoiceLibrary(root)
            t = np.arange(31 * 24000) / 24000
            source = root / "fixture.wav"
            sf.write(source, np.sin(2 * np.pi * 180 * t) * 0.2, 24000)
            name = voice.library.import_recording(source, Event())
            ident = voice.build(Recipe("fixture", (name,), seconds=30), Event(), Mock(), Mock())
            profile = voice.library.presets / ident / "voice.safetensors"
            try:
                voice.engine.load(Playback("18m", ident, 0.3), "cloning.yaml", str(profile), Event())
                pcm = voice.engine.render("Good morning.", Event())
                self.assertGreater(np.abs(np.frombuffer(pcm, dtype="<f4")).max(), 0.001)
            finally:
                voice.engine.unload()
