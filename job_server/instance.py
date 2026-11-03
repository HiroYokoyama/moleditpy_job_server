import os

from .files import open_regular


class DaemonAlreadyRunning(RuntimeError):
    """Another process holds the instance lock for this data directory."""


class InstanceLock:
    def __init__(self, root, *, port=None):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root
        self.path = root / "daemon.lock"
        self.port = port
        self.stream = None
        self._descriptor_written = False

    def acquire(self):
        self.stream = open_regular(self.path, "r+b")
        if os.fstat(self.stream.fileno()).st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise DaemonAlreadyRunning("Daemon already running for this data directory") from exc
        if self.port is not None:
            from .daemon_ownership import write_owner_descriptor

            try:
                write_owner_descriptor(self.root, self.port)
                self._descriptor_written = True
            except Exception:
                self.release()
                raise
        return self

    def release(self):
        if self.stream:
            try:
                if self._descriptor_written:
                    from .daemon_ownership import descriptor_path

                    descriptor_path(self.root).unlink(missing_ok=True)
            finally:
                self._descriptor_written = False
                try:
                    self.stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
                finally:
                    self.stream.close()
                    self.stream = None
