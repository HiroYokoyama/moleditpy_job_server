"""Memory and storage are stored in MB (API and settings) but shown in GB."""

from PySide6.QtWidgets import QDoubleSpinBox

MB_PER_GB = 1024


def gb(mb):
    """Return an MB amount in GB to one decimal place, such as "16", "0.5" or "—"."""
    if mb is None or isinstance(mb, bool) or not isinstance(mb, (int, float)):
        return "—"
    return f"{mb / MB_PER_GB:.1f}".removesuffix(".0")


class GBSpinBox(QDoubleSpinBox):
    """Edits an MB setting in GB, rounding back to whole MB."""

    def __init__(self, maximum_mb, parent=None):
        super().__init__(parent)
        self.setDecimals(1)
        self.setSingleStep(0.5)
        self.setSuffix(" GB")
        self.setRange(0.1, maximum_mb / MB_PER_GB)

    def mb(self):
        return max(1, round(self.value() * MB_PER_GB))

    def set_mb(self, value):
        self.setValue(value / MB_PER_GB)
