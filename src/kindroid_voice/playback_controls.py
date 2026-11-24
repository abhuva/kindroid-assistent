"""Keyboard-accessible controls for the desktop output path."""

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QSlider

from .presets import EQ_LIMIT, TEMPO_MAX, TEMPO_MIN


class PlaybackControls(QGroupBox):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__("Playback")
        form = QFormLayout(self)
        self.speed = self.add_slider(form, "Speaking speed", TEMPO_MIN, TEMPO_MAX, 100,
                                     lambda value: "1.00× · Normal" if value == 100 else f"{value / 100:.2f}×")
        self.bands = [
            self.add_slider(form, label, -EQ_LIMIT, EQ_LIMIT, 0, lambda value: f"{value:+d} dB")
            for label in ("Bass", "Mid", "Treble")
        ]
        self.mute = QCheckBox("Mute playback")
        self.mute.toggled.connect(self.changed.emit)
        form.addRow(self.mute)

    def add_slider(self, form, name, minimum, maximum, initial, format_value):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(initial)
        slider.setTickInterval((maximum - minimum) // 2)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setAccessibleName(name)
        value = QLabel()
        value.setMinimumWidth(value.fontMetrics().horizontalAdvance("1.00× · Normal"))

        def update(current):
            text = format_value(current)
            value.setText(text)
            slider.setAccessibleDescription(text)
            self.changed.emit()

        update(initial)
        slider.valueChanged.connect(update)
        row = QHBoxLayout()
        row.addWidget(slider, 1)
        row.addWidget(value)
        form.addRow(name, row)
        return slider

    def apply_to(self, settings, *, honor_mute=True):
        bass, mid, treble = (slider.value() for slider in self.bands)
        speed = 0 if honor_mute and self.mute.isChecked() else self.speed.value() / 100
        return replace(settings, speed=speed, bass=bass, mid=mid, treble=treble)

    def load(self, settings):
        self.blockSignals(True)
        try:
            self.mute.setChecked(settings.speed == 0)
            self.speed.setValue(round((settings.speed or 1) * 100))
            for slider, gain in zip(self.bands, (settings.bass, settings.mid, settings.treble)):
                slider.setValue(gain)
        finally:
            self.blockSignals(False)
