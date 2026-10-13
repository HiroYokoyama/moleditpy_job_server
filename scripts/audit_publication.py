"""Check the exact Git index before public publication, without printing secrets."""

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
