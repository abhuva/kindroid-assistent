from collections import deque
from dataclasses import dataclass
import importlib.util
import json
import math
from pathlib import Path
import subprocess
from threading import Thread
import time

from .session import VoiceError
from .pocket import PocketVoice


STT_MODELS = {
    "multilingual": "Parakeet multilingual",
    "german": "Parakeet German",
    "fluid-speech-pro": "Fluid Speech Pro",
}
FLUID_PROFILE = Path(__file__).with_name("fluid-speech-pro.json")


@dataclass(frozen=True)
class Recording:
    samples: object
    ended_by: str
    streamed_blocks: int = 0

    @property
    def seconds(self):
        return self.streamed_blocks / 10 if self.samples is None else len(self.samples) / 16000


def wait_for_wake(stop, on_ready):
    script = Path(__file__).with_name("speech.ps1")
    with subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as child:
        def read_ready():
            if child.stdout.readline().strip() == b"READY" and not stop.is_set():
                on_ready()

        reader = Thread(target=read_ready)
        reader.start()
        try:
            while not stop.is_set():
                try:
                    child.wait(timeout=0.15)
                except subprocess.TimeoutExpired:
                    continue
                if child.returncode:
                    raise VoiceError("Wake listener failed. Check Windows speech recognition and the default microphone.")
                return True
            return False
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            reader.join()


class LocalAudio:
    def __init__(self, dictate_dir, model_dir):
        self.dictate_dir = dictate_dir
        self.model_dir = model_dir
        self.chat_model = model_dir.name if model_dir else "multilingual"
        self.recognizer = None
        self.threads = 4
        self.voice = PocketVoice()

    def select_model(self, model, threads=4):
        if model != self.model_dir.name or threads != self.threads:
            self.recognizer = None
            self.model_dir = self.model_dir.with_name(model)
            self.threads = threads

    def wake(self, stop, on_ready):
        return wait_for_wake(stop, on_ready)

    def prepare(self, stop):
        if self.recognizer is None:
            source = self.dictate_dir / "upstream" / "local_stt.py"
            registry_path = self.dictate_dir / "model-registry.json"
            if not source.is_file():
                raise VoiceError("Restore the local Dictate files under var/voice/dictate.")
            if self.model_dir.name == "fluid-speech-pro":
                files = json.loads(FLUID_PROFILE.read_text("utf-8"))["files"]
                missing = "Fluid Speech Pro is incomplete. Run scripts/setup-fluid-speech-pro.ps1."
            else:
                if not registry_path.is_file():
                    raise VoiceError("Restore the local Dictate files under var/voice/dictate.")
                registry = json.loads(registry_path.read_text("utf-8"))
                files = next(m["files"] for m in registry["models"] if m["id"] == self.model_dir.name)
                missing = "Dictate model is incomplete. Run NICA Dictate setup."
            for entry in files:
                path = self.model_dir / entry["name"]
                if not path.is_file() or path.stat().st_size != entry["bytes"]:
                    raise VoiceError(missing)
            spec = importlib.util.spec_from_file_location("voice_local_stt", source)
            stt = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(stt)
            stt.MODEL_DIR = self.model_dir
            self.recognizer = stt.LocalRecognizer(num_threads=self.threads)
        while not stop.is_set():
            if self.recognizer.wait_ready(0.1):
                if not self.recognizer.is_ready():
                    self.recognizer = None
                    raise VoiceError("Could not load the local Dictate model.")
                return

    def transcribe(self, samples):
        return self.recognizer.transcribe(samples)

    def capture(self, deadline, stop, stage, *, pause_seconds=0.9, threshold_dbfs=None,
                finish=None, on_level=None, on_block=None, on_boundary=None,
                listening_threshold_dbfs=-60, awaiting_trigger=None, on_audio=None, on_meter=None):
        import numpy as np
        import sounddevice as sd

        # The microphone supplies 100 ms of 16-bit PCM per block.
        pause_blocks = round(pause_seconds * 10) if pause_seconds is not None else None
        threshold = 240 if threshold_dbfs is None else 32768 * 10 ** (threshold_dbfs / 20)
        listening_threshold = 32768 * 10 ** (listening_threshold_dbfs / 20)
        recent = deque(maxlen=15 if on_audio else 3)
        frames = []
        blocks = 0
        quiet = 0

        def end(reason):
            return Recording(None if on_block else np.concatenate(frames), reason, blocks)

        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1600) as mic:
            if on_boundary:
                stage("Listening for a start phrase")
            else:
                stage("Recording · speak now" if pause_blocks is None else "Listening · waiting for speech")
            while not stop.is_set():
                if blocks and finish is not None and finish.is_set():
                    return end("Finished manually")
                if not blocks and deadline is not None and time.monotonic() >= deadline:
                    return None
                block, overflow = mic.read(1600)
                if overflow:
                    raise VoiceError("Microphone audio was interrupted. Start again to record a complete turn.")
                rms = math.sqrt(np.mean(block.astype("float32") ** 2))
                heard_name = on_audio(block) if on_audio else False
                active = heard_name or rms >= (listening_threshold if awaiting_trigger and awaiting_trigger() else threshold)
                if on_level or on_meter:
                    level = 20 * math.log10(max(rms / 32768, 1e-5))
                if on_meter:
                    peak = max(abs(int(block.min())), abs(int(block.max())))
                    on_meter(level, 20 * math.log10(max(peak / 32768, 1e-5)))
                if not blocks:
                    if pause_blocks is not None and not active:
                        if on_level:
                            on_level(level, 0, 0)
                        recent.append(block)
                        continue
                    blocks = len(recent)
                    if on_block:
                        for buffered in recent:
                            on_block(buffered, False)
                    else:
                        frames.extend(recent)
                    if pause_blocks is not None and not on_boundary:
                        stage("Recording · pause to finish")
                blocks += 1
                if on_block:
                    on_block(block, active)
                else:
                    frames.append(block)
                quiet = 0 if active else quiet + 1
                if on_level:
                    on_level(level, blocks, quiet)
                if pause_blocks is not None and quiet >= pause_blocks:
                    if on_boundary:
                        on_boundary()
                        recent.clear()
                        blocks = quiet = 0
                        continue
                    return end(f"{pause_seconds:g} s pause detected")
                if not on_block and blocks >= 600:
                    if finish is not None:
                        return end("60 s recording limit reached")
                    raise VoiceError("Recording reached one minute. Start again with a shorter turn.")
        return None
