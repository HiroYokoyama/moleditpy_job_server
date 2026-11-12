"""A dial for a share of capacity, and a trend line of the last few minutes."""

import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

# The spans the dashboard offers, as (seconds, label); the server keeps the longest.
SPANS = ((5 * 60, "5 min"), (15 * 60, "15 min"), (60 * 60, "1 h"), (6 * 60 * 60, "6 h"))
DEFAULT_SPAN = SPANS[1]
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


def round_top(largest):
    """A round number above the largest count (1, 2, 5, 10, 20, 50...), so the line
    never lies on the top of the plot and the scale changes rarely."""
    step = 1.0
    while True:
        for factor in (1, 2, 5):
            if factor * step > largest:
                return factor * step
        step *= 10


class Trend(QWidget):
    """One measure over the chosen span, with its value under the pointer."""

    def __init__(self, unit="", maximum=None, parent=None, clock=time.time):
        super().__init__(parent)
        self.unit = unit
        self.maximum = maximum
        self.clock = clock
        self.samples = []
        self.span, self.span_label = DEFAULT_SPAN
        self.gap = 30.0
        self._hover = None
        self.setMouseTracking(True)
        self.setMinimumHeight(76)
        self.setMaximumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_samples(self, samples, gap=None):
        """``(time, value)`` pairs, oldest first; None or a missing stretch breaks the line."""
        self.samples = [(when, None if value is None else float(value)) for when, value in samples]
        if gap is not None:
            self.gap = gap
        self._hover = None
        self.update()

    def set_span(self, seconds, label):
        self.span, self.span_label = seconds, label
        self._hover = None
        self.update()

    def _visible(self):
        horizon = self.clock() - self.span
        return [(when, value) for when, value in self.samples if when >= horizon]

    def _scale(self):
        values = [value for _, value in self._visible() if value is not None]
        if self.maximum is not None:
            return self.maximum or 1.0
        return round_top(max(values + [0.0]))

    def top_label(self):
        """What the line at the top of the plot stands for."""
        return f"{self._scale():g}{self.unit}"

    def _axis_font(self):
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
        return font

    def _ceiling(self):
        """Where the top-of-scale line sits: low enough for its label to sit above it."""
        return QFontMetricsF(self._axis_font()).height() + 2

    def _plot_height(self):
        """The line's own height, above the small time axis."""
        return self.height() - QFontMetricsF(self._axis_font()).height() - 3

    def _points(self):
        """(time, value, point) per sample; a None point where the line must break."""
        now, top = self.clock(), self._scale()
        ceiling = self._ceiling()
        width, height = self.width() - 2, self._plot_height() - ceiling - 2
        points, previous = [], None
        for when, value in self._visible():
            # The server samples every few seconds; a longer silence is time it was not running.
            if previous is not None and when - previous > self.gap:
                points.append((previous, None, None))
            previous = when
            points.append((when, value, None if value is None else QPointF(
                1 + width * (1 - (now - when) / self.span),
                ceiling + height * (1 - min(value, top) / top),
            )))
        return points

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(_ink(self, 0.12), 1))
        base = self._plot_height() - 1.5
        painter.drawLine(QPointF(0, base), QPointF(self.width(), base))
        # The top of the scale: without it a line's height says nothing.
        painter.setPen(QPen(_ink(self, 0.22), 1))
        painter.drawLine(QPointF(0, self._ceiling()), QPointF(self.width(), self._ceiling()))
        # Says what the line spans; without it the width means nothing.
        painter.setFont(self._axis_font())
        painter.setPen(_ink(self, 0.55))
        axis = QRectF(0, self._plot_height(), self.width(), self.height() - self._plot_height())
        painter.drawText(axis, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"{self.span_label} ago")
        painter.drawText(axis, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "now")
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
        # A faint fill under the line gives the plot weight without hiding the figure over it.
        shade = QColor(color)
        shade.setAlphaF(0.10)
        run = []
        for _when, _value, point in [*self._points(), (None, None, None)]:
            if point is not None:
                run.append(point)
                continue
            if len(run) > 1:
                area = QPainterPath(QPointF(run[0].x(), base))
                for corner in run:
                    area.lineTo(corner)
                area.lineTo(QPointF(run[-1].x(), base))
                painter.fillPath(area, shade)
            run = []
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        # What the top line stands for, just above its right end.
        height = QFontMetricsF(painter.font()).height()
        corner = QRectF(0, self._ceiling() - height - 1, self.width(), height)
        painter.setPen(_ink(self, 0.55))
        painter.drawText(corner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self.top_label())
        if self._hover is not None:
            _when, _value, point = self._hover
            painter.setPen(QPen(_ink(self, 0.35), 1))
            painter.drawLine(QPointF(point.x(), 0), QPointF(point.x(), self._plot_height()))
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

