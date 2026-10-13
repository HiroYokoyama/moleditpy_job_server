from pathlib import Path
from shutil import copy2, copytree

CORE_FILES = ("__init__.py", "models.py", "remote_paths.py", "remote_runner.py")


def copy_core(destination):
    source = Path(__file__).parent / "upstream" / "job_manager" / "job_manager"
    if not (source / "models.py").exists():
        raise RuntimeError("git submodule update --init is required before building")
    destination.mkdir(parents=True, exist_ok=True)
    for name in CORE_FILES:
        copy2(source / name, destination / name)
    copytree(
        source / "schedulers",
        destination / "schedulers",
        dirs_exist_ok=True,
        ignore=lambda _p, names: [name for name in names if name == "__pycache__"],
    )


if __name__ == "__main__":
    from setuptools import setup
    from setuptools.command.build_py import build_py

    class Build(build_py):
        def run(self):
            super().run()
            copy_core(Path(self.build_lib) / "job_server" / "_execution")

    setup(cmdclass={"build_py": Build})
