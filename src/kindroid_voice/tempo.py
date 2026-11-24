"""Pitch-preserving playback tempo for mono float32 speech."""


def playback_blocks(pcm, rate, speed):
    """Yield 20 ms blocks, reading a new tempo for each overlap-add step."""
    import numpy as np

    get_speed = speed if callable(speed) else lambda: speed
    samples = np.frombuffer(pcm, dtype="<f4")
    hop = max(1, min(rate // 50, len(samples)))  # 20 ms overlap for speech
    search = hop // 2
    padded = np.pad(samples, (0, 2 * hop))
    fade = np.arange(hop, dtype="float32") / hop
    cursor = 0.0
    previous_end = None
    while cursor < len(samples):
        tempo = get_speed()
        if not tempo:
            return
        size = min(hop, round((len(samples) - cursor) / tempo))
        if size <= 0:
            break
        expected = min(len(samples) - 1, round(cursor))
        start = expected
        if previous_end is None or (tempo == 1 and expected == previous_end):
            block = padded[start:start + size]
            yield block.tobytes()
            previous_end = start + hop
            cursor += hop * tempo
            continue
        tail = padded[previous_end:previous_end + hop]
        if tempo != 1 and np.dot(tail, tail) > 1e-10:
            # Choose the nearby waveform best aligned with the preceding tail.
            # Normalizing each candidate keeps louder syllables from winning
            # simply because of their amplitude.
            low = max(0, expected - search)
            high = min(len(samples) - 1, expected + search)
            region = padded[low:high + hop]
            cross = np.correlate(region, tail, mode="valid")
            energy = np.empty(len(region) + 1, dtype="float64")
            energy[0] = 0
            np.cumsum(region * region, dtype="float64", out=energy[1:])
            scores = cross / np.sqrt(np.maximum(energy[hop:] - energy[:-hop], 1e-10))
            start = low + int(np.argmax(scores))
        head = padded[start:start + hop]
        blended = tail + fade * (head - tail)
        yield blended[:size].tobytes()
        previous_end = start + hop
        cursor += hop * tempo
