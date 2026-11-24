"""Streaming English keyword detector for the STT comparison."""

from pathlib import Path

from .session import VoiceError


MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"


class ConversationSpotter:
    def __init__(self, threshold):
        import sherpa_onnx

        directory = Path(__file__).resolve().parents[2] / "var/voice/kws" / MODEL
        files = {part: directory / f"{part}-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
                 for part in ("encoder", "decoder", "joiner")}
        files.update(tokens=directory / "tokens.txt", keywords_file=Path(__file__).with_name("conversation-keywords.txt"))
        if not all(path.is_file() and path.stat().st_size for path in files.values()):
            raise VoiceError("Restore the local acoustic detector with scripts/setup-voice-kws.ps1.")
        self.model = sherpa_onnx.KeywordSpotter(**{key: str(path) for key, path in files.items()},
                                              num_threads=1, keywords_score=2.0, keywords_threshold=threshold)
        self.stream = self.model.create_stream()

    def accept(self, pcm):
        self.stream.accept_waveform(16000, pcm.ravel().astype("float32") / 32768.0)
        match = ""
        while self.model.is_ready(self.stream):
            self.model.decode_stream(self.stream)
            result = self.model.get_result(self.stream)
            if result:
                match = result.replace("_", " ")
                self.model.reset_stream(self.stream)
        return match

    def new_segment(self):
        self.stream = self.model.create_stream()
