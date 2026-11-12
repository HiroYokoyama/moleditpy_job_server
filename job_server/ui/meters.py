"""A dial for a share of capacity, and a trend line of the last few minutes."""

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

HISTORY_SECONDS = 15 * 60
TREND_LIGHT, TREND_DARK = "#2a78d6", "#3987e5"


def _dark(widget):
    return widget.palette().window().color().lightness() < 128


def _ink(widget, alpha):
    color = QColor(widget.palette().windowText().color())
    color.setAlphaF(alpha)
    return color


class Gauge(QWidget):
    """A 270° dial with the percentage in its middle; keeps QProgressBar's value()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._known = False
        self._color = QColor("#2e7d32")
        self.setFixedSize(QSize(68, 68))

    def setValue(self, value):
        self._value = max(0, min(100, int(value)))
        self.update()

    def value(self):
        return self._value

    def setKnown(self, known):
        self._known = bool(known)
        self.update()

    def setColor(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        # Qt angles are in 1/16 degree, counter-clockwise from 3 o'clock: start at
        # 7:30 and sweep clockwise through 270 degrees.
        start, span = 225 * 16, -270 * 16
        track = QPen(_ink(self, 0.15), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, start, span)
        if self._known and self._value > 0:
            painter.setPen(QPen(self._color, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, start, int(span * self._value / 100))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(7.0, font.pointSizeF()))
        painter.setFont(font)
        painter.setPen(_ink(self, 0.9 if self._known else 0.45))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self._value}%" if self._known else "—")


class Trend(QWidget):
    """The last fifteen minutes of one measure, with its value under the pointer."""

    def __init__(self, unit="", maximum=None, parent=None):
        super().__init__(parent)
        self.unit = unit
        self.maximum = maximum
        self.samples = deque()
        self._hover = None
        self.setMouseTracking(True)
        self.setMinimumHeight(30)
        self.setMaximumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add(self, value, when=None):
        """Record a sample; None marks a gap, such as the server being off."""
        when = time.time() if when is None else when
        self.samples.append((when, None if value is None else float(value)))
        while self.samples and self.samples[0][0] < when - HISTORY_SECONDS:
            self.samples.popleft()
        self.update()

    def _scale(self):
        values = [value for _, value in self.samples if value is not None]
        top = self.maximum if self.maximum is not None else max(values + [1.0])
        return top or 1.0

    def _points(self):
        if not self.samples:
            return []
        now = self.samples[-1][0]
        top = self._scale()
        width, height = self.width() - 2, self.height() - 4
        return [
            (when, value, None if value is None else QPointF(
                1 + width * (1 - (now - when) / HISTORY_SECONDS),
                2 + height * (1 - min(value, top) / top),
            ))
            for when, value in self.samples
        ]

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_ink(self, 0.12), 1))
        painter.drawLine(QPointF(0, self.height() - 1.5), QPointF(self.width(), self.height() - 1.5))
        color = QColor(TREND_DARK if _dark(self) else TREND_LIGHT)
        path, started = QPainterPath(), False
        for _when, _value, point in self._points():
            if point is None:
                started = False
            elif started:
                path.lineTo(point)
            else:
                path.moveTo(point)
                started = True
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        if self._hover is not None:
            _when, _value, point = self._hover
            painter.setPen(QPen(_ink(self, 0.35), 1))
            painter.drawLine(QPointF(point.x(), 0), QPointF(point.x(), self.height()))
            painter.setPen(QPen(self.palette().window().color(), 2))
            painter.setBrush(color)
            painter.drawEllipse(point, 4, 4)

    def describe(self, when, value):
        stamp = time.strftime("%H:%M:%S", time.localtime(when))
        amount = f"{value:.0f}" if self.unit == "%" or value == int(value) else f"{value:.1f}"
        return f"{stamp} — {amount}{self.unit}"

    def mouseMoveEvent(self, event):
        x = event.position().x()
        nearest = min(
            ((when, value, point) for when, value, point in self._points() if point is not None),
            key=lambda sample: abs(sample[2].x() - x), default=None,
        )
        # Only a sample close to the pointer is described: across a gap there is nothing to read.
        if nearest is None or abs(nearest[2].x() - x) > 12:
            self._hover = None
            QToolTip.hideText()
        else:
            self._hover = nearest
            QToolTip.showText(event.globalPosition().toPoint(), self.describe(nearest[0], nearest[1]), self)
        self.update()

    def leaveEvent(self, event):
        self._hover = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)


def percent(used, budget):
    try:
        return max(0.0, min(100.0, 100.0 * used / budget))
    except (TypeError, ZeroDivisionError):
        return None

