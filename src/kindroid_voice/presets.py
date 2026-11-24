from dataclasses import asdict, dataclass
import filecmp
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .session import VoiceError


RECORDING_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg")
SPEECH_SPEEDS = tuple(step / 4 for step in range(9))
TEMPO_MIN, TEMPO_MAX = 80, 120
EQ_LIMIT = 12


def write_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}-{uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(asdict(settings)), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class Playback:
    model: str
    preset: str
    temperature: float
    speed: float = 1.0
    bass: int = 0
    mid: int = 0
    treble: int = 0

    @property
    def synthesis_parameters(self):
        return self.model, self.preset, self.temperature


@dataclass(frozen=True)
class Recipe:
    name: str
    samples: tuple[str, ...]
    offset: int = 0
    seconds: int = 24
    temperature: float = 0.3

    def validate(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise VoiceError("Enter a preset name of up to 80 characters.")
        if not isinstance(self.samples, (list, tuple)) or not 1 <= len(self.samples) <= 8:
            raise VoiceError("Select one to eight source recordings.")
        if not 0 <= self.offset <= 3600 or not 3 <= self.seconds <= 30 or not 0 <= self.temperature <= 1:
            raise VoiceError("Voice preset settings are outside the supported range.")
        for name in self.samples:
            if name != Path(name).name or Path(name).suffix.lower() not in RECORDING_EXTENSIONS:
                raise VoiceError("Select source recordings from the repository sample folder.")


class VoiceLibrary:
    def __init__(self, directory):
        self.root = Path(directory)
        self.samples = self.root / "samples"
        self.presets = self.root / "presets"
        self.preference = self.root / "chat-voice.json"

    def recordings(self):
        return sorted(p.name for p in self.samples.glob("*") if p.is_file()
                      and p.suffix.lower() in RECORDING_EXTENSIONS)

    def import_recording(self, source, stop):
        """Copy one complete, readable recording into the library without replacing existing audio."""
        import soundfile as sf

        source = Path(source)
        if source.suffix.lower() not in RECORDING_EXTENSIONS:
            raise VoiceError("Choose a WAV, MP3, FLAC or Ogg recording.")
        try:
            self.samples.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.samples) as staging:
                copy = Path(staging) / source.name
                with source.open("rb") as reader, copy.open("xb") as writer:
                    while not stop.is_set():
                        block = reader.read(1024 * 1024)
                        if not block:
                            break
                        writer.write(block)
                if stop.is_set():
                    return None
                if sf.info(copy).frames < 1:
                    raise VoiceError("The selected recording contains no audio.")
                target = self.samples / source.name
                suffix = 2
                while not stop.is_set():
                    try:
                        # Windows rename and POSIX hard-link creation both fail
                        # if another import has already published this filename.
                        if os.name == "nt":
                            copy.rename(target)
                        else:
                            os.link(copy, target)
                        return target.name
                    except FileExistsError:
                        if target.is_file() and not target.is_symlink() and filecmp.cmp(copy, target, shallow=False):
                            return target.resolve().name
                        target = self.samples / f"{source.stem} ({suffix}){source.suffix}"
                        suffix += 1
        except (OSError, RuntimeError):
            raise VoiceError("Could not import the recording. Check the file is readable audio and the samples folder is writable.") from None

    def recipes(self):
        result = {}
        for path in sorted(self.presets.glob("*/recipe.json")):
            if re.fullmatch("[0-9a-f]{32}", path.parent.name) and path.with_name("voice.safetensors").is_file():
                try:
                    recipe = Recipe(**json.loads(path.read_text("utf-8")))
                    recipe.validate()
                except (OSError, ValueError, TypeError, VoiceError):
                    continue
                result[path.parent.name] = recipe
        return result

    def chat(self):
        if not self.preference.is_file():
            return Playback("18m", "original" if (self.root / "custom.safetensors").is_file() else "alba", 0.3)
        try:
            settings = Playback(**json.loads(self.preference.read_text("utf-8")))
            self.resolve(settings)
            return settings
        except (TypeError, ValueError, OSError):
            raise VoiceError("Choose a voice in TTS and save it for chat again.") from None

    def save_chat(self, settings):
        self.resolve(settings)
        write_settings(self.preference, settings)

    def resolve(self, settings):
        speed = settings.speed
        # Accept older voice settings, including mute, when opening a saved voice.
        if speed not in SPEECH_SPEEDS and not (TEMPO_MIN / 100 <= speed <= TEMPO_MAX / 100
                                               and speed == round(speed, 2)):
            raise VoiceError("Choose a speaking speed from 0.80× to 1.20×.")
        for gain in (settings.bass, settings.mid, settings.treble):
            if type(gain) is not int or not -EQ_LIMIT <= gain <= EQ_LIMIT:
                raise VoiceError("Choose EQ gains between −12 and +12 dB.")
        if settings.model not in ("7m", "18m") or not 0 <= settings.temperature <= 1:
            raise VoiceError("Choose a supported Pocket TTS model and temperature.")
        if settings.preset == "alba":
            suffix = "-7m" if settings.model == "7m" else ""
            return f"english{suffix}.yaml", f"alba{suffix}.safetensors"
        if settings.model != "18m":
            raise VoiceError("This clone uses the English 18M REFL model.")
        if settings.preset == "original":
            voice = Path("custom.safetensors")
        elif re.fullmatch("[0-9a-f]{32}", settings.preset):
            voice = Path("presets") / settings.preset / "voice.safetensors"
        else:
            raise VoiceError("Choose an installed voice preset.")
        if not (self.root / voice).is_file():
            raise VoiceError("The saved voice file is missing. Choose another preset.")
        return "cloning.yaml", str(voice)
