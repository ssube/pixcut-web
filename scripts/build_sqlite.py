"""Build the design-required SQLite runtime from a hash-pinned official amalgamation.

Run with Python 3.12. Requires a C compiler and internet, or pass a cached zip path.
"""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SHA256 = "acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4"
URL = "https://sqlite.org/2026/sqlite-amalgamation-3510300.zip"
with tempfile.TemporaryDirectory(prefix="pixcut-sqlite-") as tmp:
    archive = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tmp) / "sqlite.zip"
    if len(sys.argv) == 1:
        urllib.request.urlretrieve(URL, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("SQLite source checksum mismatch")
    with zipfile.ZipFile(archive) as source:
        # Extract only the known compilation unit after checking the whole archive hash.
        code = Path(tmp) / "sqlite3.c"
        code.write_bytes(source.read("sqlite-amalgamation-3510300/sqlite3.c"))
    output = ROOT / ".runtime" / "libsqlite3.so.0"
    output.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            "cc",
            "-O2",
            "-fPIC",
            "-shared",
            "-DSQLITE_THREADSAFE=1",
            "-DSQLITE_ENABLE_FTS5",
            "-Wl,-soname,libsqlite3.so.0",
            str(code),
            "-o",
            str(output),
            "-lm",
            "-ldl",
            "-lpthread",
        ],
        check=True,
    )
    print(output)
