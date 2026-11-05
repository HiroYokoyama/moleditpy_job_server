import socket

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from ..connection import is_mdns_name
from ..listeners import available_lan_addresses


def this_pc_local_name():
    """The `.local` name this PC announces, when its name can be one."""
    name = f"{socket.gethostname().split('.')[0].lower()}.local"
    return name if is_mdns_name(name) else ""


class LanAddressList(QListWidget):
    """Each way another PC can reach this one, ticked to use it."""

    changed = Signal()

    def __init__(self, addresses=(), hostname=""):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(
            "Listen on each ticked private address as well as localhost. "
            "No firewall settings are changed automatically."
        )
        for address in available_lan_addresses():
            self.add_entry(address)
        if this_pc_local_name():
            self.add_entry(this_pc_local_name())
        self.select(addresses, hostname)
        self.itemChanged.connect(self._item_changed)

    def add_entry(self, value, label=""):
        for row in range(self.count()):
            if self.item(row).data(Qt.ItemDataRole.UserRole) == value:
                return self.item(row)
        item = QListWidgetItem(label or value)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.addItem(item)
        self._fit()
        return item

    def select(self, addresses=(), hostname=""):
        wanted = [*addresses, *([hostname] if hostname else [])]
        for value in wanted:
            self.add_entry(value, f"{value}  (not currently available)")
        with_signals = self.signalsBlocked()
        self.blockSignals(True)
        for row in range(self.count()):
            item = self.item(row)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in wanted
                else Qt.CheckState.Unchecked
            )
        self.blockSignals(with_signals)
        self.changed.emit()

    def addresses(self):
        return [value for value in self._ticked() if not is_mdns_name(value)]

    def hostname(self):
        return next((value for value in self._ticked() if is_mdns_name(value)), "")

    def _ticked(self):
        items = (self.item(row) for row in range(self.count()))
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in items
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _item_changed(self, item):
        value = item.data(Qt.ItemDataRole.UserRole)
        if is_mdns_name(value) and item.checkState() == Qt.CheckState.Checked:
            self.blockSignals(True)
            for row in range(self.count()):
                other = self.item(row)
                if other is not item and is_mdns_name(other.data(Qt.ItemDataRole.UserRole)):
                    other.setCheckState(Qt.CheckState.Unchecked)
            self.blockSignals(False)
        self.changed.emit()

    def _fit(self):
        rows = max(1, min(self.count(), 5))
        self.setFixedHeight(self.sizeHintForRow(0) * rows + 2 * self.frameWidth() + 2)
