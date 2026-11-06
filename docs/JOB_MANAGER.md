# Job Manager integration

Job Manager 2.6.0 or later adds the server as a normal destination for submission, monitoring and downloading. Job Manager 3.9.4 or later can also browse job folders.

Install [Job Manager 2.6.0 or later](https://github.com/HiroYokoyama/moleditpy_job_manager/releases)
through its normal Plugin Installer/registry entry. Install optional
`cryptography>=50.0.2` in Job Manager's Python environment to enable **Job Server (HTTPS)**.
Existing SSH and local backends do not require it. In **Job Manager → Hosts**, add a
Job Server host, configure the HTTPS origin (HTTP is allowed only on localhost),
and click **Sign in / register device**. The server's execution OS selects the native
scheduler. Submit, monitor, tail, cancel, and download then use the normal Job Manager UI.

For development and regression checks, build a reproducible companion from this
server's pinned older Job Manager source:

```sh
python scripts/build_job_manager.py
```

The test-only companion is `dist/job_manager_job_server.zip`. CI builds and validates
it, but it is excluded from release downloads, distribution artifacts and `SHA256SUMS`.
Install the normal Job Manager 2.6.0+ release for everyday use; installing this test
companion would replace it with the pinned older version plus the integration overlay.

The companion is generated from the audited upstream commit and an explicit integration
overlay. It does not change your sibling Job Manager repository or its upstream branch.
`integration/job_manager.patch` records changes for upstream review;
`integration/server_backend.py` is the additional bridge module.
