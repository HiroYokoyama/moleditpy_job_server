from PySide6.QtWidgets import QComboBox

from ..listeners import available_lan_addresses


def lan_address_selector(selected=""):
    field = QComboBox()
    field.addItem("Local only (default)", "")
    for address in available_lan_addresses():
        field.addItem(address, address)
    if selected and field.findData(selected) < 0:
        field.addItem(f"{selected} (not currently available)", selected)
    field.setCurrentIndex(max(0, field.findData(selected)))
    field.setToolTip(
        "Listen on this private interface as well as localhost. "
        "Enable local HTTPS or allow LAN HTTP. Allow the server port in the OS firewall "
        "for trusted LAN clients. No firewall settings are changed automatically."
    )
    return field
