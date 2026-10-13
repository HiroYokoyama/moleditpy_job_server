# Development guidance

Job Server is a GPL-3.0-only Trusted Lab Preview. Keep the default listener off until
explicit start, bind loopback, and report resource enforcement as advisory.

Run `python -m pytest -q` and `python -m ruff check job_server scripts tests integration setup.py`.
For integration changes run `python scripts/run_regression.py` against the patched copy.
Build both distributions and plugin ZIPs before publishing source changes.

Do not modify `upstream/job_manager`: it is an immutable submodule dependency. Source
integration belongs in `scripts/build_job_manager.py` and `integration/server_backend.py`.
Do not read or publish real user job records or credentials. Keep fixture accounts and
job data in temporary directories. No listener or process starts during plugin loading.

Do not tag a release or update the public plugin registry unless the user requests it.
