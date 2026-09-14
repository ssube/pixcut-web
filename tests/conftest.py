from pathlib import Path
import ctypes

# Tests may load sqlite through third-party plugins before importing the service.
ctypes.CDLL(
    str(Path(__file__).resolve().parents[1] / ".runtime/libsqlite3.so.0"),
    mode=ctypes.RTLD_GLOBAL,
)
import pytest
from fastapi.testclient import TestClient
from pixcut.cli import migrate
from pixcut.api import create_app


@pytest.fixture
def client(tmp_path):
    migrate(tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client
    app.state.service.engine.dispose()
