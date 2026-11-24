"""Half-duplex direct call. Capture, provider delivery and playback have distinct lifetimes."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty, Queue
import re
from threading import Event
import time
from uuid import uuid4

from .stt_chunks import PauseChunks


class VoiceError(Exception):
    """A message suitable for the local operator."""


@dataclass(frozen=True)
class CallInput:
    model: str
    threads: int
    threshold: int
    chunk: float
    paragraph: float
    finish: float


def capture_turn(audio, settings, stop, stage, draft):
    parts = []
    previous = None
    pending = deque()

    def decode(samples, paragraph):
        nonlocal previous
        if stop.is_set():
            return
        text = audio.transcribe(samples).strip()
        if text and not stop.is_set():
            if parts:
                parts.append("\n\n" if previous != paragraph else " ")
            previous = paragraph
            parts.append(text)
            message = "".join(parts)
            draft(message)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="call-dictation") as decoder:
        def submit(samples, paragraph):
            pending.append(decoder.submit(decode, samples, paragraph))

        chunks = PauseChunks(settings.chunk, settings.paragraph, submit)
        def accept(block, active):
            while pending and pending[0].done():
                pending.popleft().result()
            if len(pending) >= 32:
                raise VoiceError("Dictation fell behind. This turn was not sent.")
            chunks.accept(block, active)
        try:
            recording = audio.capture(None, stop, stage, pause_seconds=settings.finish,
                                      threshold_dbfs=settings.threshold, on_block=accept)
            if recording is None or stop.is_set():
                return ""
            stage("Finishing your transcript")
            chunks.flush()
            while pending:
                pending.popleft().result()
        finally:
            for job in pending:
                job.cancel()
    return "".join(parts)


# Confirm sentence punctuation with following whitespace; a period in 3.14
# or a word split across network packets is not itself a playback boundary.
_BREAK = re.compile(r"""[.!?…]+["'”’)]*(?=\s)|[\r\n]+""")


def receive_reply(audio, open_stream, stop, stage, update):
    speech = Queue()
    failed = Event()

    class PlaybackStop:
        def is_set(self):
            return failed.is_set() or stop.is_set()

    def take_segment(cancel):
        while not cancel.is_set():
            try:
                return speech.get(timeout=0.075)
            except Empty:
                pass

    def play():
        try:
            audio.voice.speak_segments(take_segment, token, stage)
        except Exception:
            failed.set()
            raise

    reply = pending = ""
    last_update = 0.0
    token = PlaybackStop()
    speak = bool(audio.voice.playback.speed)
    playing = None
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="call-playback") as player:
        def enqueue(text):
            nonlocal playing
            speech.put(text)
            if playing is None:
                stage("Generating Hannah’s first audio")
                playing = player.submit(play)

        stream = open_stream(token)
        try:
            for delta in stream:
                if token.is_set():
                    break
                if not reply:
                    stage("Receiving Hannah’s reply")
                reply += delta
                now = time.monotonic()
                if now - last_update >= 0.05:
                    update(reply, "Receiving")
                    last_update = now
                if speak:
                    pending += delta
                    while match := _BREAK.search(pending):
                        piece = pending[:match.end()].strip()
                        pending = pending[match.end():]
                        if piece:
                            enqueue(piece)
            if not token.is_set() and pending.strip():
                enqueue(pending.strip())
            if reply:
                status = "Stopped" if stop.is_set() else "Incomplete reply" if failed.is_set() else ""
                update(reply, status)
            if playing is not None and not token.is_set():
                stage("Finishing Hannah’s playback")
        except Exception:
            failed.set()
            if reply:
                update(reply, "Incomplete reply")
            raise
        finally:
            speech.put(None)
            stream.close()
        if playing is not None:
            playing.result()
    if stop.is_set() and reply:
        update(reply, "Stopped")


def conversation(audio, send, stop, stage, update, get_settings):
    """Apply STT edits between turns; reload weights only on a recognizer change."""
    loaded = None
    while not stop.is_set():
        settings = get_settings()
        recognizer = (settings.model, settings.threads)
        if loaded != recognizer:
            stage("Preparing local speech recognition")
            audio.select_model(*recognizer)
            audio.prepare(stop)
            if loaded is None and not stop.is_set() and audio.voice.playback.speed:
                stage("Loading saved Pocket TTS voice")
                audio.voice.load_voice(audio.voice.playback, stop, stage)
            loaded = recognizer
        if stop.is_set():
            return
        turn = uuid4().hex
        message = ""
        def draft(text):
            nonlocal message
            message = text
            update(turn, "Marc", text, "Dictating")
        try:
            capture_turn(audio, settings, stop, stage, draft)
        except Exception:
            if message:
                update(turn, "Marc", message, "Not sent")
            raise
        if stop.is_set():
            if message:
                update(turn, "Marc", message, "Not sent")
            return
        if not message:
            stage("No speech transcribed · listening again")
            continue
        update(turn, "Marc", message, "Sending")
        stage("Waiting for Hannah’s reply")
        answer = uuid4().hex
        confirmed = False
        def reply(text, status):
            nonlocal confirmed
            if text and not confirmed:
                update(turn, "Marc", message, "Sent")
                confirmed = True
            if text:
                update(answer, "Hannah", text, status)
        try:
            receive_reply(audio, lambda cancel: send(message, cancel), stop, stage, reply)
        finally:
            if not confirmed:
                update(turn, "Marc", message, "Check delivery")
