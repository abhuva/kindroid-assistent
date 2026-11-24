from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


BADGES = {
    "TK": ("#ffe6a1", "#513800", "Text in Kindroid"),
    "VK": ("#e7d7ff", "#492476", "Voice call in Kindroid"),
    "API": ("#ccebdc", "#164a33", "Text through the API"),
    "?": ("#e2e6eb", "#374151", "History source not reported"),
}


class ChatView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setAccessibleName("Kindroid conversation")
        page = QWidget()
        self.rows = QVBoxLayout(page)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(page)
        self.entries = {}
        self.follow = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self.follow_range)
        bar.valueChanged.connect(lambda value: setattr(self, "follow", value >= bar.maximum() - 2))

    def follow_range(self, minimum, maximum):
        if self.follow:
            self.verticalScrollBar().setValue(maximum)

    def put(self, ident, speaker, origin, text, status=""):
        if ident not in self.entries:
            if len(self.entries) >= 500:
                oldest = next(iter(self.entries))
                row, _, _ = self.entries.pop(oldest)
                self.rows.removeWidget(row)
                row.deleteLater()
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 1, 0, 1)
            card = QFrame()
            card.setObjectName("chatCard")
            outgoing = speaker in ("Marc", "Codex")
            bg = "#dfefe7" if outgoing else "#eef0f6"
            card.setStyleSheet(f"QFrame#chatCard {{ background: {bg}; border-radius: 9px; }} QLabel {{ color: #24303a; }}")
            stack = QVBoxLayout(card)
            header = QHBoxLayout()
            stack.addLayout(header)
            color, ink, description = BADGES[origin]
            badge = QLabel(origin)
            badge.setToolTip(description)
            badge.setAccessibleName(description)
            badge.setStyleSheet(f"background: {color}; color: {ink}; padding: 1px 5px; border-radius: 4px; font-weight: bold")
            header.addWidget(badge)
            heading = QLabel()
            heading.setTextFormat(Qt.TextFormat.PlainText)
            header.addWidget(heading)
            header.addStretch()
            body = QLabel()
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            stack.addWidget(body)
            if outgoing:
                layout.addStretch(1)
            layout.addWidget(card, 4)
            if not outgoing:
                layout.addStretch(1)
            self.rows.addWidget(row)
            self.entries[ident] = row, heading, body
        _, heading, body = self.entries[ident]
        heading.setText(f"{speaker} · {status}" if status else speaker)
        body.setText(text)
