from pathlib import Path
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import subprocess
import tempfile
import time
from uuid import uuid4

from .presets import VoiceLibrary
from .engine import ResidentEngine
from .output_audio import OutputAudio, output_levels
from .session import VoiceError
from .tempo import playback_blocks


def speech_paragraphs(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in re.split(r"\n(?:[^\S\n]*\n)+", normalized) if part.strip()]


class PocketVoice:
    def __init__(self, directory=None):
        self.directory = directory or Path(__file__).resolve().parents[2] / "var/pocket-tts/4.0.1"
        self.library = VoiceLibrary(self.directory)
        self.playback = None
        self.output_meter = None
        self.engine = ResidentEngine(self.directory)

    def check(self, *, cloning=False, settings=None):
        if cloning:
            if not (self.directory / "voice-clone.exe").is_file():
                raise VoiceError("Restore voice-clone.exe with scripts/build-voice-engine.ps1 -CloningOnly.")
            required = ["cloning.yaml", "cloning.safetensors"]
        else:
            config, profile = self.library.resolve(settings or self.playback or self.library.chat())
            weight = {"english.yaml": "model.safetensors", "cloning.yaml": "cloning.safetensors",
                      "english-7m.yaml": "model-7m.safetensors"}[config]
            required = [config, profile, weight]
        if not all((self.directory / f).is_file() for f in [*required, "tokenizer.model"]):
            raise VoiceError("Restore the local Pocket TTS weights and runtime under var/pocket-tts/4.0.1.")

    def _export(self, reference, profile, stop):
        if stop.is_set():
            return False
        with subprocess.Popen(
            [str(self.directory / "voice-clone.exe"), "export-voice", str(reference), str(profile)],
            cwd=self.directory, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as child:
            try:
                deadline = time.monotonic() + 180
                while not stop.is_set():
                    try:
                        _, errors = child.communicate(timeout=0.15)
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise VoiceError("Voice cloning exceeded its three-minute time limit.")
                        continue
                    if child.returncode:
                        if b"memory allocation" in errors or child.returncode in (0xC0000017, 0xC000012D):
                            raise VoiceError("Windows ran out of memory while encoding the voice. Close other heavy apps or reduce the reference duration.")
                        raise VoiceError("The voice encoder could not export this reference. Check the cloning weights and available disk space.")
                    return True
                return False
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()

    def load_voice(self, settings, stop, stage):
        self.check(settings=settings)
        config, profile = self.library.resolve(settings)
        stage("Loading Pocket TTS and voice preset")
        self.engine.load(settings, config, profile, stop)

    def prepare_playback(self):
        settings = self.playback or self.library.chat()
        if settings.speed:
            self.engine.require_on()
            if settings.synthesis_parameters != self.engine.settings.synthesis_parameters:
                raise VoiceError("The loaded voice settings differ. Open Models and load the settings for TTS or chat.")
        return settings

    def speak(self, text, stop, stage, progress=None):
        paragraphs = speech_paragraphs(text)
        if paragraphs:
            segments = iter(paragraphs)
            self.speak_segments(lambda _: next(segments, None), stop, stage, progress, total=len(paragraphs))

    def export_mp3(self, text, settings, directory, stop, stage, progress):
        import soundfile as sf

        if not sf.check_format("MP3", "MPEG_LAYER_III"):
            raise VoiceError("This voice runtime cannot encode MP3 files.")
        if stop.is_set():
            return None
        paragraphs = speech_paragraphs(text)
        if not paragraphs or not settings.speed:
            raise VoiceError("Enter text and choose a speaking speed to create an MP3.")
        rate = self.engine.rate
        output = OutputAudio(rate)
        eq = settings.bass, settings.mid, settings.treble
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".kindroid-tts-", dir=directory) as staging:
                staged = Path(staging) / "speech.mp3"
                with sf.SoundFile(staged, mode="w", samplerate=rate, channels=1,
                                  format="MP3", subtype="MPEG_LAYER_III") as mp3:
                    for index, paragraph in enumerate(paragraphs):
                        if stop.is_set():
                            return None
                        stage(f"Rendering paragraph {index + 1} of {len(paragraphs)}")
                        progress(index, len(paragraphs))
                        pcm = self.engine.render(paragraph, stop)
                        if stop.is_set():
                            return None
                        if pcm is None:
                            raise VoiceError("Speech generation ended before the MP3 was complete.")
                        for block in playback_blocks(pcm, rate, settings.speed):
                            if stop.is_set():
                                return None
                            mp3.buffer_write(output.process(block, eq), dtype="float32")
                        progress(index + 1, len(paragraphs))
                stage("Saving MP3 in Downloads")
                stem = time.strftime("Kindroid TTS %Y-%m-%d %H%M%S")
                suffix = 1
                while not stop.is_set():
                    name = stem if suffix == 1 else f"{stem} ({suffix})"
                    destination = directory / f"{name}.mp3"
                    try:
                        if os.name == "nt":
                            staged.rename(destination)
                        else:
                            os.link(staged, destination)
                        return destination
                    except FileExistsError:
                        suffix += 1
        except (OSError, RuntimeError):
            raise VoiceError("Could not save the MP3. Check that Downloads is writable and has free space.") from None

    def speak_segments(self, take_segment, stop, stage, progress=None, *, total=None):
        import sounddevice as sd
        from threading import Event

        started = time.monotonic()
        settings = self.prepare_playback()
        if stop.is_set() or settings.speed == 0:
            return
        def report(message, done):
            stage(message)
            if progress:
                progress(done, total)

        # A device failure must also cancel a paragraph already generating ahead.
        abandoned = Event()

        class Cancelled:
            def is_set(self):
                return stop.is_set() or abandoned.is_set()

        token = Cancelled()
        def get_speed():
            return (self.playback or settings).speed

        def render_next():
            segment = take_segment(token)
            if segment is None or token.is_set():
                return None
            return self.engine.render(segment, token)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pocket-render") as producer:
            if total is not None:
                report(f"Generating paragraph 1 of {total}", 0)
            pending = producer.submit(render_next)
            speaker = None
            playing = False
            completed = False
            try:
                # Open the device while the first paragraph is rendering.
                output = OutputAudio(self.engine.rate)
                speaker = sd.RawOutputStream(samplerate=self.engine.rate, channels=1,
                                             dtype="float32", latency="low")
                index = 0
                while total is None or index < total:
                    if total is not None and index and not pending.done():
                        report(f"Waiting for paragraph {index + 1} of {total}", index)
                    pcm = pending.result()
                    if stop.is_set():
                        return
                    if pcm is None:
                        break
                    if total is None or index + 1 < total:
                        pending = producer.submit(render_next)
                    if not playing:
                        speaker.start()
                        playing = True
                    report(f"Playing paragraph {index + 1} of {total}" if total is not None else
                           f"Playing Hannah’s reply · segment {index + 1}", index)
                    for block_number, block in enumerate(playback_blocks(pcm, self.engine.rate, get_speed)):
                        if stop.is_set():
                            return
                        tuning = self.playback or settings
                        block = output.process(block, (tuning.bass, tuning.mid, tuning.treble))
                        speaker.write(block)
                        if self.output_meter is not None:
                            self.output_meter(*output_levels(block))
                        if index == 0 and block_number == 0:
                            self.engine.last_timing = f"First audio queued in {time.monotonic() - started:.2f} s"
                    index += 1
                completed = True
                if progress:
                    progress(total, total)
            except (sd.PortAudioError, OSError):
                raise VoiceError("Could not play the generated reply. Check the output device.") from None
            finally:
                abandoned.set()
                if speaker is not None:
                    try:
                        if completed and not stop.is_set():
                            speaker.stop()
                        elif playing:
                            speaker.abort()
                    finally:
                        speaker.close()

    def write_reference(self, recipe, destination, stop, progress):
        import numpy as np
        import soundfile as sf

        recipe.validate()
        files = [self.library.samples / name for name in recipe.samples]
        rate = 24000
        seconds = recipe.seconds / len(files)
        blocks = []
        for index, path in enumerate(files):
            if stop.is_set():
                return
            progress(index, len(files))
            try:
                with sf.SoundFile(path) as clip:
                    if not 8000 <= clip.samplerate <= 192000 or clip.channels > 8:
                        raise VoiceError("Choose speech clips with a standard audio sample rate and up to eight channels.")
                    if recipe.offset * clip.samplerate >= clip.frames:
                        raise VoiceError("The start offset falls past the end of a selected recording.")
                    clip.seek(recipe.offset * clip.samplerate)
                    mono = clip.read(int(clip.samplerate * (seconds + 3)), dtype="float32", always_2d=True).mean(axis=1)
                    voiced = np.flatnonzero(np.abs(mono) > 0.003)
                    if not len(voiced):
                        raise VoiceError("A reference clip is silent. Choose recordings with audible speech.")
                    mono = mono[voiced[0]:min(voiced[-1] + 1, voiced[0] + int(seconds * clip.samplerate))]
                    grid = np.arange(int(len(mono) * rate / clip.samplerate)) * (clip.samplerate / rate)
                    blocks.append(np.interp(grid, np.arange(len(mono)), mono))
                    blocks.append(np.zeros(rate // 8))
            except (RuntimeError, OSError):
                raise VoiceError("A reference clip could not be decoded. Try WAV, FLAC, Ogg or MP3.") from None
        progress(len(files), len(files))
        if sum(len(part) for part in blocks[::2]) < rate * 3:
            raise VoiceError("Choose clips containing at least three seconds of speech in total.")
        sf.write(destination, np.concatenate(blocks), rate)

    def build(self, recipe, stop, stage, progress):
        if stop.is_set():
            return
        if not self.directory.is_dir():
            raise VoiceError("Install Pocket TTS with scripts/setup-pocket-tts.ps1.")
        self.check(cloning=True)
        self.library.presets.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.library.presets) as work:
            reference = Path(work) / "reference.wav"
            bundle = Path(work) / "preset"
            bundle.mkdir()
            profile = bundle / "voice.safetensors"
            self.write_reference(recipe, reference, stop, progress)
            if stop.is_set():
                return
            stage("Creating the local voice profile")
            if self._export(reference, profile, stop):
                if not stop.is_set():
                    if not profile.is_file() or not profile.stat().st_size:
                        raise VoiceError("The voice encoder finished without writing a profile.")
                    (bundle / "recipe.json").write_text(json.dumps(asdict(recipe), indent=2), encoding="utf-8")
                    ident = uuid4().hex
                    bundle.rename(self.library.presets / ident)
                    return ident
