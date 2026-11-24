"""Three-band output EQ and post-EQ peak/RMS measurement."""

import math


class OutputAudio:
    def __init__(self, rate):
        self.low_step = 1 - math.exp(-2 * math.pi * 250 / rate)
        self.high_step = 1 - math.exp(-2 * math.pi * 3000 / rate)
        self.low = self.upper = 0.0
        self.gains = (1.0, 1.0, 1.0)
        self.db = (0, 0, 0)

    def process(self, block, db):
        import numpy as np

        pcm = np.frombuffer(block, dtype="<f4")
        if db == self.db and db == (0, 0, 0):
            self.low = self.upper = float(pcm[-1])
            return block

        low, upper = self.low, self.upper
        bass, mid, treble = self.gains
        target = tuple(10 ** (gain / 20) for gain in db)
        bass_step, mid_step, treble_step = [(end - start) / len(pcm) for start, end in zip(self.gains, target)]
        result = pcm.copy()
        for index, sample in enumerate(pcm.tolist()):
            low += self.low_step * (sample - low)
            upper += self.high_step * (sample - upper)
            bass += bass_step
            mid += mid_step
            treble += treble_step
            result[index] = bass * low + mid * (upper - low) + treble * (sample - upper)
        self.low, self.upper = low, upper
        self.gains, self.db = target, db
        np.clip(result, -1.0, 1.0, out=result)
        return result.tobytes()


def output_levels(block):
    import numpy as np

    pcm = np.frombuffer(block, dtype="<f4")
    rms = math.sqrt(np.mean(pcm * pcm))
    peak = float(np.abs(pcm).max())
    return 20 * math.log10(max(rms, 1e-5)), 20 * math.log10(max(peak, 1e-5))
