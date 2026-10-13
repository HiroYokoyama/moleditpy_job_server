# Public publication review

The staged publication consists of source code, tests, documentation, GPL license,
build/integration scripts, CI and service templates, a public upstream gitlink, and
two image assets. It excludes runtime configuration, SQLite databases, generated
device keys, sessions/tokens, test working directories, build trees and distributions.
`scripts/audit_publication.py` checks the actual Git index for unexpected credential
artifacts and private-key / GitHub-token / AWS-key / API-key signatures, reporting
filenames without printing suspected values.

Source/test review: the fixed password strings are synthetic fixtures. The example
TLS URLs use example.org. Account keys and bearer tokens are generated at runtime
and are never source constants. No real Job Manager job records or user credentials
were read while implementing this repository.

Binary review: `job_server/web/icon.png` is byte-identical to upstream Job Manager's
public icon (SHA-256 aeae40e896351c6f3a9354fa421d917bd82752d43f5f03bbf7dc2c04146d229a).
`docs/web-ui.jpg` was visually inspected: it shows the generated example-admin test
account and the synthetic Browser verification job, with no passwords, tokens,
private keys, personal files or real calculation records.
