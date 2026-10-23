# Licensing and independently developed plugins

The first-party source code in **moleditpy_job_server** is licensed under the GNU Lesser
General Public License, version 3 or (at your option) any later version
(LGPL-3.0-or-later). `LICENSE` contains LGPLv3's additional permissions;
`COPYING` contains the incorporated GPLv3 terms. Earlier releases retain their
original licenses.

Copyright: Hiromichi Yokoyama / HiroYokoyama and the contributors identified in
the existing copyright notices. Retain those notices, `LICENSE`, `COPYING`, and
this document when redistributing the covered source or binaries.

Modifications to the covered code remain subject to the LGPL. Distribution
terms must allow users to modify the covered components and reverse engineer
for debugging their modifications as required by LGPLv3. Provide corresponding
source and allow modification or replacement of covered libraries when
distributing a combined binary, as required by the license.

Independently developed plugins that use MoleditPy's documented plugin API may
be distributed under any license, including proprietary licenses, under the
[main application's copyright-holder permission](https://github.com/HiroYokoyama/python_molecular_editor/blob/main/LICENSING.md).
Their authors choose their licenses and retain their copyright. This repository's
LGPL license does not change the licenses of other independently developed
plugins, copied MoleditPy code, or third-party components.

Third-party libraries, executables, models, and data retain their own licenses
and obligations. Preserve their notices and comply with the terms of the
versions actually redistributed, including native and transitive dependencies.
Running a program in a separate process does not by itself establish license
compatibility.

## Pinned first-party execution component

The copyright holder additionally grants LGPL-3.0-or-later for the first-party
Job Manager sources (including its embedded first-party HTTP client) taken from commit
`110e1e27797f27cd2896e0e9ec388dd940992713`, as redistributed in this migration's
`upstream/job_manager`, patched integration copy and `job_server/_execution`.
The immutable submodule's original GPL license document and source bytes are
preserved for provenance; this explicit owner permission supplies the additional
LGPL grant for these redistributed first-party copies. It does not relicense
third-party files/dependencies or change any historical release.
