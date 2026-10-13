"""Reuse a sibling Job Manager checkout; clone the support branch only if absent."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/HiroYokoyama/moleditpy_job_manager.git"
DEFAULT_REF = "main"


def ensure_checkout(target=None, ref=DEFAULT_REF):
    target = Path(target) if target is not None else ROOT.parent / "moleditpy_job_manager"
    if not target.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, "--", REPOSITORY, str(target)],
            check=True,
        )
    if not (target / "job_manager/server_backend.py").is_file():
        raise RuntimeError(
            "Sibling Job Manager checkout lacks Job Server support; select a support branch there. Existing checkouts are never reset or switched."
        )
    print(f"Using Job Manager checkout: {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=DEFAULT_REF)
    ensure_checkout(ref=parser.parse_args().ref)
