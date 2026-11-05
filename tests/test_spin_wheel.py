import pytest


@pytest.mark.parametrize("kind", ["integer", "gb"])
def test_spin_wheel_changes_values_only_when_focused(kind, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from job_server.ui.units import FocusSpinBox, GBSpinBox

    app = QApplication.instance() or QApplication([])
    field = FocusSpinBox() if kind == "integer" else GBSpinBox(102400)
    field.setValue(1)

    def wheel():
        return QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )

    assert field.focusPolicy() == Qt.FocusPolicy.StrongFocus
    monkeypatch.setattr(field, "hasFocus", lambda: False)
    event = wheel()
    field.wheelEvent(event)
    assert field.value() == 1
    assert not event.isAccepted()
    monkeypatch.setattr(field, "hasFocus", lambda: True)
    field.wheelEvent(wheel())
    assert field.value() > 1
    field.close()
    app.processEvents()
