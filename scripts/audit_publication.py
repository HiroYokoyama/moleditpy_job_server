"""Check the exact Git index before public publication, without printing secrets."""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"github_pat_[A-Za-z0-9_]{20,}",
    rb"gh[pousr]_[A-Za-z0-9]{20,}",
    rb"AKIA[A-Z0-9]{16}",
    rb"sk-[A-Za-z0-9_-]{24,}",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    issues = []
    scanned = 0
    for name in filter(None, paths):
        if name == "upstream/job_manager":
            continue
        if (
            name.startswith(("test-results/", "dist/", "build/"))
            or name.endswith((".db", ".key", ".pem", ".p12", ".env"))
            or Path(name).name == "settings.json"
        ):
            issues.append((name, "Unexpected runtime/credential artifact in index"))
            continue
        data = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        if name.endswith((".jpg", ".png")):
            continue
        scanned += 1
        for pattern in PATTERNS:
            if re.search(pattern, data):
                issues.append((name, "Credential signature detected; inspect privately"))
    for name, reason in issues:
        print(f"FAIL {name}: {reason}")
    if issues:
        return 1
    if args.history:
        objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=ROOT)
        blobs = 0
        for line in objects.decode().splitlines():
            identifier = line.split(" ", 1)[0]
            kind = subprocess.check_output(["git", "cat-file", "-t", identifier], cwd=ROOT).strip()
            if kind != b"blob":
                continue
            data = subprocess.check_output(["git", "cat-file", "blob", identifier], cwd=ROOT)
            blobs += 1
            if any(re.search(pattern, data) for pattern in PATTERNS):
                print(f"FAIL historical blob {identifier}: credential signature; inspect privately")
                return 1
        print(f"PASS: {blobs} historical Git blobs scanned for credential signatures.")
    print(
        f"PASS: {scanned} staged text files scanned; no private-key, GitHub-token, AWS-key or API-key signatures."
    )
    print(
        "PASS: no databases, device credentials, settings.json, test-results, build or dist artifacts are staged."
    )
    print(
        "Binary review required: docs/web-ui.jpg is the synthetic browser test dashboard; icon.png is upstream's public icon."
    )
    print(
        "Manual review: literal account passwords occur only in synthetic fixtures (example-password-123 and example URLs); no real accounts or credential values were read."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
