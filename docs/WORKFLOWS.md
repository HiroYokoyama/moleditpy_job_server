# GitHub workflows

This repository follows the Job Manager/plugin template release pattern.
PyPI publication reuses the distributions already attached to a GitHub Release.

| Workflow | Trigger | Checks / outcome |
|---|---|---|
| Tests | Main/dev pushes, pull requests, manual, reusable | Python 3.11/3.12/3.13 on Linux/Windows; Qt, native execution, coverage/JUnit reports, wheel/sdist and server plugin ZIP with checksums; test-only companion build/validation, Linux/Windows patched Job Manager regression and real HTTP client compatibility |
| Security | Main/dev pushes, pull requests, weekly, manual, reusable | Dependency advisories, Bandit source analysis, credential signatures in tracked source and Git history |
| Release | `vX.Y.Z` tag push or manual on main | Validate both version declarations, rerun Tests and Security, build/validate three assets, create a GitHub release; registry registration requires explicit opt-in |
| Publish to PyPI | Published release or manual on main | Download the released wheel/sdist, verify release checksums, exercise the installed wheel, publish with OIDC/PEP 740 attestations, attach attestations to the GitHub Release |
| Installed Job Manager integration | Manual only | Install Job Manager 3.4.0 from PyPI alongside a built server wheel or a selected PyPI server version; test GUI/CLI/daemon integration on Ubuntu Python 3.11–3.14 and Windows/macOS 3.12 |
| Update Plugin Registry | After Release, or manual with an existing release tag | Verify the release includes `job_server_X.Y.Z.zip`, send the existing `plugin_release` registry dispatch |

To prepare a release, update `PLUGIN_VERSION` in `job_server/__init__.py` and
`project.version` in `pyproject.toml` together. Update README version text/badge as
well. Commit the change, then run **Actions → Release → Run workflow** on main with
the matching version (without `v`), or push its `vX.Y.Z` tag. Publication occurs only
after Tests and Security succeed. An existing tag must point to the selected commit;
existing release assets are never overwritten by this workflow. Releases are published as ordinary GitHub Releases.

Release assets: `job_server_X.Y.Z.zip`, the Python wheel, source distribution,
and `SHA256SUMS` covering those three files. The registry receives the server
plugin ZIP. The pinned companion `job_manager_job_server.zip` is built and
validated only for testing; it is not uploaded with release/distribution assets
or included in the release checksums. Use the normal Job Manager release.

Registration is disabled by default. Leave the manual Release input **register**
unchecked (`false`). Tag releases register only when the repository variable
**REGISTER_PLUGIN** is explicitly `true`. Publishing Job Server 0.3.0 does not
register it in the public plugin catalog.

If registration is authorized later, set repository Actions secret **REGISTRY_PAT**
to a token authorized to dispatch `HiroYokoyama/moleditpy-plugins`, then explicitly
run Update Plugin Registry for the published tag. No token value belongs in source.
Configuring the secret alone does not opt tag releases into registration.

## PyPI publication

Configure a PyPI Trusted Publisher for project **moleditpy-job-server**, GitHub
owner **HiroYokoyama**, repository **moleditpy_job_server**, workflow
**publish-pypi.yml**, environment **pypi**. For the initial publication, add it as
a pending publisher in PyPI account publishing settings. No PyPI API token is needed.

Run **Actions → Publish to PyPI → Run workflow** on **main**, supplying an existing
GitHub Release version such as **0.3.2** without `v`. The workflow uploads the exact
released wheel and sdist, after checksum verification and an isolated wheel check
of CLI startup, daemon/API, web UI, authentication, native execution and verified
output download. The published Job Manager 3.4.0 is also exercised through its
standalone window and `moleditpy-job submit --wait`, including input upload,
automatic/manual download, file preview, failure exit codes and cancellation.
After publishing, manually run **Installed Job Manager integration** with
**server-version = 0.3.2** to test the PyPI server package in six environments,
with bounded install retries for PyPI index propagation. Leave the version blank
to test a wheel built from the selected checkout. This workflow is dispatch-only;
pushes, pull requests and publication do not start it automatically.
It leaves the plugin registry unchanged.

A release published with GitHub's default Actions token does not trigger other
release-event workflows. After the Release workflow finishes, manually run
Publish to PyPI. Releases published outside that token also trigger it automatically.

Install the headless daemon with `python -m pip install moleditpy-job-server`.
Add `[gui]` only for the optional MoleditPy administration dialogs. Python 3.11+
is required; the CLI entry point is `moleditpy-job-server`.

CI jobs have read-only repository tokens and no publication secrets. Only the
publication job gets `contents: write`; the registry token is passed only to the
registry job, which never checks out or executes repository code. Action versions
are pinned to verified full commit hashes; Dependabot proposes updates weekly.
Event inputs enter scripts through environment variables and are validated before
use. No `pull_request_target` or privileged `workflow_run` path executes fork code.

Job Server's Tests workflow also runs Job Manager HTTP compatibility on Linux and
Windows. `scripts/ensure_job_manager.py` reuses `../moleditpy_job_manager` when it
exists, or clones that repository's `main` into the sibling location. It never
resets, switches, pulls or deletes an existing checkout. Set the optional repository
variable `JOB_MANAGER_REF` to test another branch when cloning into a fresh runner.
The job tests optional-dependency-free imports, GUI routing, and real enrollment,
native execution, polling, logs, verified results, cancellation and owner ACLs.

Coverage is published as a report, not an unverified percentage badge. Initial
Release/download badges can show no releases until the first release is requested.

Workflow security guidance: [GitHub secure use reference](https://docs.github.com/en/actions/reference/security/secure-use).
