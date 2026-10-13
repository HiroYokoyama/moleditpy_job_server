"""Job Manager integration with the optional Job Server plugin."""


def backend_for(host):
    try:
        from job_server.server_backend import JobServerBackend
    except ImportError as exc:
        raise ValueError(
            "Install the Job Server plugin/client dependencies before using this host"
        ) from exc
    return JobServerBackend(host)


def show_enrollment(parent, url, key_field, ca_file):
    from pathlib import Path
    import uuid
    from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
    from job_server.client import JobServerClient
    from job_server.settings import Settings
    from .tasks import run_async
    from PyQt6.QtCore import QThreadPool

    username, ok = QInputDialog.getText(parent, "Job Server", "Username")
    if not ok:
        return
    password, ok = QInputDialog.getText(
        parent, "Register device", "Initial password", QLineEdit.EchoMode.Password
    )
    if not ok:
        return
    path = key_field.text().strip() or str(
        Settings().root / "identity" / (str(uuid.uuid4()) + ".key")
    )

    def work():
        client = JobServerClient(url, path, ca_file or None)
        if Path(path).exists():
            client.authenticate()
        else:
            client.enroll(username, password)
        return client.request("GET", "/capabilities")

    def done(capabilities):
        key_field.setText(path)
        QMessageBox.information(
            parent,
            "Job Server",
            f"Device registered. Execution OS: {capabilities['execution_os']}. Select the matching native scheduler.",
        )

    run_async(
        QThreadPool.globalInstance(),
        work,
        on_success=done,
        on_error=lambda message: QMessageBox.warning(parent, "Job Server", message),
        owner=parent,
    )
