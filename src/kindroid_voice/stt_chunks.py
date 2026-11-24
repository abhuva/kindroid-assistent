"""Pause-based segmentation for the STT test microphone."""

from collections import deque
from dataclasses import dataclass
import re
import time


class PauseChunks:
    # 100 ms microphone blocks. Bound the work of one decode even without pauses.
    max_blocks = 80

    def __init__(self, pause_seconds, paragraph_seconds, submit, *, manual=False, preroll_blocks=3):
        self.pause_blocks = round(pause_seconds * 10)
        self.paragraph_blocks = round(paragraph_seconds * 10)
        self.paragraph = 0
        self.submit = submit
        self.preroll = deque(maxlen=preroll_blocks)
        self.frames = []
        self.quiet = 0
        self.quiet_reference = [] if manual else None

    def accept(self, block, active):
        if active:
            self.quiet_reference = None
            if self.quiet >= self.paragraph_blocks:
                self.flush()
                self.paragraph += 1
            self.quiet = 0
        else:
            self.quiet += 1
        if not self.frames:
            if not active:
                self.preroll.append(block)
                if self.quiet_reference is not None:
                    self.quiet_reference.append(block)
                    if len(self.quiet_reference) >= self.max_blocks:
                        self.flush()
                return
            self.frames.extend(self.preroll)
            self.preroll.clear()
        self.frames.append(block)
        if self.quiet >= self.pause_blocks or len(self.frames) >= self.max_blocks:
            self.flush()

    def flush(self):
        import numpy as np

        if self.frames:
            # Keep the ends of words, but spare the decoder a long silence.
            end = len(self.frames) - max(0, self.quiet - 2)
            self.submit(np.concatenate(self.frames[:end]), self.paragraph)
            self.preroll.extend(self.frames[end:])
            self.frames.clear()
        elif self.quiet_reference:
            samples = np.concatenate(self.quiet_reference)
            self.quiet_reference.clear()
            if samples.any():
                self.submit(samples, self.paragraph)
                self.preroll.clear()


@dataclass
class OpeningWait:
    speech_run: int
    shown_at: float | None = None

    def acknowledge(self):
        self.shown_at = time.monotonic()


class TriggerGate:
    """Find command spans in recording order, including phrases split across chunks."""

    start_phrase = "conversation on"
    stop_phrase = "conversation off"

    def __init__(self, words):
        phrases = [" ".join(re.findall(r"\w+", word)) for word in words]
        alternatives = "|".join(re.escape(phrase) for phrase in phrases if phrase)
        pattern = rf"(?P<off>{self.stop_phrase})"
        if alternatives:
            pattern += rf"|(?P<on>{alternatives})"
        self.pattern = re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
        self.lookback = max(len(phrase.split()) for phrase in [*phrases, self.stop_phrase]) - 1
        self.tail = []

    def commands(self, text):
        """Match whole words across adjacent chunks within one silence-delimited take."""
        tokens = list(re.finditer(r"\w+", text))
        if not tokens:
            return
        combined = self.tail + [token.group() for token in tokens]
        prefix = len(self.tail)
        self.tail = combined[-self.lookback:]
        phrase = " ".join(combined)
        for match in self.pattern.finditer(phrase):
            last = phrase.count(" ", 0, match.end()) - prefix
            if last >= 0:
                yield match.lastgroup, match.group(), tokens[last].end()


class ConversationTurns:
    """Own conversation and turn boundaries on the ordered transcription thread."""

    def __init__(self, phrases, activity, turn_finished, transcript, start_match):
        self.gate = TriggerGate(phrases)
        self.activity = activity
        self.turn_finished = turn_finished
        self.transcript = transcript
        self.start_match = start_match
        self.active = False
        self.opening = None
        self.preview = deque(maxlen=2)
        self.parts = []

    def open(self, speech_run):
        if not self.active:
            self.opening = OpeningWait(speech_run)
            self.active = True
            self.parts.extend(self.preview)
            self.preview.clear()
            self.activity(self.opening)

    def append(self, text, paragraph):
        if text.strip():
            self.transcript(text.strip(), paragraph)
            parts = self.parts if self.active else self.preview
            parts.append((text.strip(), paragraph))

    def receive(self, text, paragraph, speech_run):
        offset = 0
        for command, phrase, end in self.gate.commands(text):
            if command == "on":
                self.start_match(phrase)
                self.open(speech_run)
                if re.search(r"\w", text[end:]):
                    self.opening = None
            else:
                while end < len(text) and not text[end].isalnum() and not text[end].isspace():
                    end += 1
                self.append(text[offset:end], paragraph)
                # Closing preserves the displayed words. Only a turn timeout
                # publishes text to consumers of the completed-turn signal.
                self.parts.clear()
                self.active = False
                self.opening = None
                self.preview.clear()
                self.activity(None)
                offset = end
        if self.opening and speech_run > self.opening.speech_run:
            self.opening = None
        self.append(text[offset:], paragraph)

    def finish_turn(self):
        self.opening = None
        if self.parts:
            text = []
            previous = self.parts[0][1]
            for part, paragraph in self.parts:
                if text:
                    text.append(" " if paragraph == previous else "\n\n")
                text.append(part)
                previous = paragraph
            self.turn_finished("".join(text))
            self.parts.clear()

    def pause(self, speech_run):
        self.gate.tail.clear()
        if not self.active:
            self.preview.clear()
            self.activity(None)
        elif self.opening is None or speech_run > self.opening.speech_run:
            self.finish_turn()

    def expire(self, opening):
        if self.opening is opening:
            self.finish_turn()
