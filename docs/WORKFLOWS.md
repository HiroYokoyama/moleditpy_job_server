# GitHub workflows

This repository follows the Job Manager/plugin template release pattern. It has no
Zenodo or PyPI publication workflow.

| Workflow | Trigger | Checks / outcome |
|---|---|---|
| Tests | Main/dev pushes, pull requests, manual, reusable | Python 3.11/3.12/3.13 on Linux/Windows; Qt, native execution, coverage/JUnit reports, wheel/sdist and both plugin ZIPs with checksums; Linux/Windows patched Job Manager regression |
| Security | Main/dev pushes, pull requests, weekly, manual, reusable | Dependency advisories, Bandit source analysis, credential signatures in tracked source and Git history |
| Release | `vX.Y.Z` tag push or manual on main | Validate both version declarations, rerun Tests and Security, build/validate four assets, create a GitHub prerelease, request registry refresh |
| Update Plugin Registry | After Release, or manual with an existing release tag | Verify the release includes `job_server_X.Y.Z.zip`, send the existing `plugin_release` registry dispatch |

To prepare a release, update `PLUGIN_VERSION` in `job_server/__init__.py` and
`project.version` in `pyproject.toml` together. Update README version text/badge as
well. Commit the change, then run **Actions → Release → Run workflow** on main with
the matching version (without `v`), or push its `vX.Y.Z` tag. Publication occurs only
after Tests and Security succeed. An existing tag must point to the selected commit;
existing release assets are never overwritten by this workflow. Releases remain
marked **Trusted Lab Preview / prerelease**.

Release assets: `job_server_X.Y.Z.zip`, `job_manager_job_server_preview.zip`, the
Python wheel, source distribution, and `SHA256SUMS`. The registry receives the
server plugin ZIP; the companion does not replace the upstream Job Manager release.

Set repository Actions secret **REGISTRY_PAT** to a token authorized to dispatch
`HiroYokoyama/moleditpy-plugins`. No token value belongs in source. At setup time
(2026-10-13), this repository has no configured Actions secrets. Without this
secret, registry dispatch is explicitly skipped and the workflow summary gives the
manual registration route. After configuring it, rerun Update Plugin Registry for
the published tag. Creating these workflows does not itself release or register
the plugin.

CI jobs have read-only repository tokens and no publication secrets. Only the
publication job gets `contents: write`; the registry token is passed only to the
registry job, which never checks out or executes repository code. Action versions
are pinned to verified full commit hashes; Dependabot proposes updates weekly.
Event inputs enter scripts through environment variables and are validated before
use. No `pull_request_target` or privileged `workflow_run` path executes fork code.

Coverage is published as a report, not an unverified percentage badge. Initial
Release/download badges can show no releases until the first release is requested.

Workflow security guidance: [GitHub secure use reference](https://docs.github.com/en/actions/reference/security/secure-use).
