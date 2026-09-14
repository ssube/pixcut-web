import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def space_check(self, needed=0):
        minimum = int(os.environ.get("PIXCUT_MIN_FREE_BYTES", 128 * 1024 * 1024))
        if shutil.disk_usage(self.root).free < minimum + needed:
            raise ValueError(
                "STORAGE_LOW: free disk space is below the configured reserve"
            )

    def put(self, data: bytes, mime: str):
        self.space_check(len(data))
        key = digest(data)
        target = self.root / key
        if target.exists():
            self.read(key)
        else:
            fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                # Hard-link publication never replaces an existing immutable artifact.
                try:
                    os.link(tmp, target)
                except FileExistsError:
                    self.read(key)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                os.unlink(tmp)
        return {"hash": key, "size": len(data), "mime": mime}

    def read(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid artifact key")
        try:
            data = (self.root / key).read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(
                "HISTORY_ARTIFACT_UNAVAILABLE: retained artifact is missing"
            ) from exc
        if digest(data) != key:
            raise ValueError(
                "ARTIFACT_INTEGRITY: retained artifact hash does not match"
            )
        return data
