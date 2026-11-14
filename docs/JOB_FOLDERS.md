# Job folders

**On the server PC:** in the desktop window, right-click a job and choose **Open job folder
in File Explorer**, or use the button in its details. It opens the job's real folder in the
system file manager, and never opens or runs a file inside it.

**From a browser:** a job's details page has a **Job folder** section. Open folders one at
a time, download a file, or download a folder as a ZIP. Downloads are checked against the
server's SHA-256, and names are only ever shown as text.

**From Job Manager:** see the end of this page.

## Finished jobs folder

In the desktop window's settings, **Finished jobs folder** (`finished_dir` in
`settings.json`) moves each job that ends (done, failed, cancelled or lost) out of the
working directory to `<finished folder>/<username>/<submitted>_<job name>`, for example
`alice/20261114-1738_water opt`. The time is when the job was submitted, in local time to
the minute, so folders sort by name in submission order. Turn off **Start folder names with
the submission time** (`finished_date_prefix`, on by default since Server 1.2.0) to use the
job name alone. Folders moved earlier keep their names. A name already used by that user
gets `_2`, `_3`, and so on. Characters a folder name cannot hold become `_`.
Jobs of deleted accounts go to `deleted-users`.

- The move happens on a background thread, a few seconds after the job's supervisor exits.
  A file still held open is retried every minute, up to ten times. A move to another drive
  copies first and removes the original only after the copy is complete.
- The new location is recorded. The desktop window, the web job folder and Job Manager
  still open, browse and download the moved results through the same job ID.
- The setting applies after a restart, to jobs that end from then on. **Move existing
  finished jobs now** (or `POST /api/v1/admin/jobs/move-finished`, administrators only)
  also moves jobs that ended earlier. Running and waiting jobs are never moved.
- The folder must not be, or be inside, the data folder's job, control, log, TLS, activity archive or backup
  folders, and must not contain the data or job working directory.

## Database backups

The server copies `server.db` (accounts, jobs and activity) with SQLite's backup API, so
each copy is consistent even while jobs are running. Defaults: on, every 24 hours
(`db_backup_hours`), keeping the newest 7 copies (`db_backup_keep`), in
`<data folder>/backups` unless `db_backup_dir` names another folder. A backup is taken
at start-up when the newest copy is older than the interval. Copies are named
`server-YYYYMMDD-HHMMSS.db`. Copies contain password hashes, so keep the folder private.
A folder on another disk also protects against a disk failure.

To restore, stop the server and replace `server.db` with a copy. Also delete
`server.db-wal` and `server.db-shm` if they exist.

## Activity log archive (Server 1.1.0)

The activity log (`events` in `server.db`, shown in the dashboard and by
`GET /api/v1/admin/events`) keeps the last 3 months (`event_keep_months`). Older rows are
moved to one SQLite file per calendar month, `<data folder>/event-archive/events-YYYY-MM.db`
(for example `events-2026-10.db`), and deleted from `server.db`. The month is the row's local
time. Each pass copies and commits the rows to the archive before deleting them, so an
interrupted pass never loses or duplicates a row; later passes append to the same month's file.
Event numbers (`seq`) are kept, so clients reading with `after=` see no gaps or repeats among
recent rows. Archived rows are not served by the API; open the files with any SQLite tool.

## API (Server 0.7.0)

Authenticated clients can browse a virtual root containing committed job UUIDs.
Ordinary users see their own jobs; administrators can see all jobs. No endpoint
accepts a host filesystem path or exposes the data/control/TLS directories.

- `GET /api/v1/browse?path=&offset=0&limit=1000`: page through job folders.
- `GET /api/v1/browse?path=<job-uuid>/results`: immediate children only, including
  empty directories. Entries contain `name`, `path`, `type`, and file `size`.
  The response contains `entries` and `next_offset` (null on the last page).
  Root entries also carry `job_name` and `state`.
- `GET /api/v1/browse/stat?path=<job-uuid>/results/output.txt&digest=true`:
  file metadata, optionally SHA-256. Listing and size-only stat do not hash files.
- `GET /api/v1/jobs/<job-uuid>/files/results/output.txt`: existing verified,
  resumable file download; continue using the file manifest for checksums.
- `GET /api/v1/jobs/<job-uuid>/archive?path=results`: download a ZIP of a folder,
  preserving its relative paths and empty subdirectories. Omit `path` for the
  whole job work folder. ZIP member names are relative to the selected folder.

Paths are canonical lowercase UUIDs followed by safe relative components.
Traversal, absolute/Windows paths, alternate streams, control names
(`.moleditpy_rc`, `.server_process`), symlinks, junctions, special files and
hardlinks are excluded or rejected. The job's own log (`job.log`) and the script
that ran it (`moleditpy_run.sh` or `moleditpy_run.ps1`, with `payload.ps1`) are
listed, downloadable and included in a ZIP; nothing can be uploaded over them.
Each directory and archive permits at most 50,000 entries; archive source bytes
are capped by the configured per-job storage quota. ZIPs are prepared in private
temporary storage and closed after transfer or failure. Live files may change;
archives are not atomic snapshots. The existing trusted-user OS-account model
still applies. Uploading jobs must be committed before browsing.

The `job_folders` capability advertises support. Job Manager 3.9.4 adds these
servers to File Browser, reads folders lazily, downloads files using SHA-256
verification, and can download a selected folder recursively. It does not
extract server-provided ZIPs. Existing local files are skipped unless overwrite
was explicitly requested; linked destination components are rejected.
