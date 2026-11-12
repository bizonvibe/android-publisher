"""Same locks for the API, local wizard, calibration and publication commands."""

import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path

import portalocker


@contextmanager
def device_session(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(settings.device_serial.encode()).hexdigest()
    with portalocker.Lock(str(settings.data_dir / "device.lock"), timeout=0):
        with portalocker.Lock(
            str(Path(tempfile.gettempdir()) / f"android-publisher-{key}.lock"), timeout=0
        ):
            yield
