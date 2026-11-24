"""Mono level meter. Inputs are block RMS and sample peak in dBFS."""

import time

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget


class LevelMeter(QWidget):
    floor = -80.0
    hold_seconds = 1.5
    fall_db_per_second = 18

    def __init__(self, source="Microphone"):
        super().__init__()
        self.source = source
        self.setFixedWidth(100)
        self.setMinimumHeight(220)
        self.setAccessibleName(f"{source} RMS and held sample peak")
        self.setToolTip(f"Bars show average {source.lower()} level (RMS). The white line holds the sample peak for 1.5 s.")
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(100)
        self.idle_timer.timeout.connect(self.decay_when_idle)
        self.reset()

    def reset(self):
        self.level = self.peak = self.floor
        self.last_update = self.hold_until = 0.0
        self.running = False
        self.idle_timer.stop()
        self.setAccessibleDescription(f"{self.source} off")
        self.update()

    def set_levels(self, rms, peak):
        self.last_input = time.monotonic()
        self.update_levels(rms, peak, self.last_input)
        if self.source != "Microphone" and not self.idle_timer.isActive():
            self.idle_timer.start()

    def decay_when_idle(self):
        now = time.monotonic()
        if now - self.last_input >= 0.12:
            self.update_levels(self.floor, self.floor, now)
            if self.peak <= self.floor:
                self.reset()

    def update_levels(self, rms, peak, now):
        self.running = True
        self.level = max(self.floor, min(0.0, rms))
        decay = max(0.0, now - max(self.hold_until, self.last_update)) * self.fall_db_per_second
        self.peak = max(self.floor, self.peak - decay)
        if peak >= self.peak:
            self.peak = min(0.0, peak)
            self.hold_until = now + self.hold_seconds
        self.last_update = now
        self.setAccessibleDescription(f"RMS {rms:.0f} dBFS, held peak {self.peak:.0f} dBFS")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#152027"))
        p.setPen(QColor("#d8e6eb"))
        center = Qt.AlignmentFlag.AlignCenter
        p.drawText(QRect(0, 4, self.width(), 22), center, "dBFS")
        top, bottom = 34, self.height() - 45
        height = bottom - top

        def y(db):
            return top + round(height * db / self.floor)

        for db in range(-78, 1, 2):
            color = "#45cf88" if db <= -18 else "#ffc15c" if db <= -6 else "#ff6c61"
            p.fillRect(11, y(db), 43, max(1, y(db - 2) - y(db) - 1),
                       QColor(color if self.running and self.level >= db else "#33444d"))
        for db in (0, -12, -24, -36, -48, -60, -72, -80):
            p.drawText(QRect(60, y(db) - 9, 36, 18), Qt.AlignmentFlag.AlignVCenter, str(db))
        if self.running and self.peak > self.floor:
            p.fillRect(7, y(self.peak) - 1, 50, 3, QColor("white"))
        p.drawText(QRect(0, bottom + 11, self.width(), 22), center,
                   f"Peak {self.peak:.0f}" if self.running else f"{self.source} off")
        p.end()
