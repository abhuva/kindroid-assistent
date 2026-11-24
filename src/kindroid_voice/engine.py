"""One resident Pocket TTS process, serialized by the desktop audio worker."""

import json
from queue import Empty, Queue
import struct
import subprocess
from threading import Thread
import time

from .session import VoiceError


def _read_packets(pipe, packets):
    try:
        while header := pipe.read(5):
            if len(header) != 5:
                break
            tag, size = struct.unpack("<cI", header)
            if size > 128 * 1024 * 1024:
                break
            payload = pipe.read(size)
            if len(payload) != size:
                break
            packets.put((tag, payload))
    except (ValueError, OSError):
        pass
    finally:
        packets.put((b"X", b""))


class ResidentEngine:
    def __init__(self, directory):
        self.directory = directory
        self.child = None
        self.reader = None
        self.settings = None
        self.phase = "Off"
        self.rate = 0
        self.last_timing = ""

    @property
    def status(self):
        child = self.child
        if child is not None and child.poll() is not None:
            return "Interrupted · load the model again"
        return self.phase

    def require_on(self):
        if self.child is None or self.child.poll() is not None:
            raise VoiceError("Open Models and load a Pocket TTS voice first.")

    def unload(self, phase="Off"):
        child = self.child
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdin.close()
            if self.reader:
                self.reader.join()
            child.stdout.close()
        self.reader = self.child = self.settings = None
        self.phase = phase

    def _receive(self, stop, *, loading):
        deadline = time.monotonic() + (45 if loading else 180)
        cancelled = False
        while time.monotonic() < deadline:
            if stop.is_set() and not cancelled:
                if loading:
                    self.unload()
                    return None
                self.child.stdin.write(b"\n")
                self.child.stdin.flush()
                cancelled = True
                deadline = time.monotonic() + 4
            try:
                tag, payload = self.packets.get(timeout=0.04)
            except Empty:
                continue
            if tag == b"X":
                raise VoiceError("The Pocket TTS process exited. Reload it in Models.")
            if cancelled or tag == b"C":
                return None
            if tag != (b"R" if loading else b"A") or not payload or len(payload) % 4:
                raise VoiceError("Pocket TTS returned an invalid audio packet.")
            return payload
        raise VoiceError("Pocket TTS timed out. Reload it in Models.")

    def load(self, settings, config, profile, stop):
        if (self.settings and self.settings.synthesis_parameters == settings.synthesis_parameters
                and self.child and self.child.poll() is None):
            return
        self.unload()
        if stop.is_set():
            return
        self.phase = "Loading"
        try:
            self.child = subprocess.Popen(
                [str(self.directory / "voice-engine.exe"), config, profile, str(settings.temperature)],
                cwd=self.directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.packets = Queue()
            self.reader = Thread(target=_read_packets, args=(self.child.stdout, self.packets), daemon=True)
            self.reader.start()
            packet = self._receive(stop, loading=True)
            if packet is None:
                return
            if len(packet) != 4 or int.from_bytes(packet, "little") != 24000:
                raise VoiceError("Unexpected Pocket TTS sample rate.")
            self.rate = 24000
            self.settings = settings
            self.phase = "Ready"
        except (OSError, VoiceError) as exc:
            self.unload("Load failed")
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError("Could not launch voice-engine.exe. Restore the local voice bundle.") from None

    def render(self, paragraph, stop):
        """One request at a time. Drain cancellation acknowledgement before reusing the pipe."""
        self.require_on()
        if stop.is_set():
            return None
        try:
            self.phase = "Generating"
            self.child.stdin.write(json.dumps(paragraph, ensure_ascii=False).encode("utf-8") + b"\n")
            self.child.stdin.flush()
            payload = self._receive(stop, loading=False)
            self.phase = "Ready"
            return payload
        except (OSError, VoiceError) as exc:
            self.unload("Interrupted")
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError("Lost the Pocket TTS process. Reload it in Models.") from None
