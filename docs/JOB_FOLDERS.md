# Job folders

**On the server PC:** in the desktop window, right-click a job and choose **Open job folder
in File Explorer**, or use the button in its details. It opens the job's real folder in the
system file manager, and never opens or runs a file inside it.

**From a browser:** a job's details page has a **Job folder** section. Open folders one at
a time, download a file, or download a folder as a ZIP. Downloads are checked against the
server's SHA-256, and names are only ever shown as text.

**From Job Manager:** see the end of this page.

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
Traversal, absolute/Windows paths, alternate streams, wrapper/control names,
symlinks, junctions, special files and hardlinks are excluded or rejected.
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
