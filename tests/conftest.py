from datetime import UTC, datetime

import pytest

from android_publisher.config import Settings
from android_publisher.models import Job
from android_publisher.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, api_token="test-token", poll_seconds=0.01)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir / "test.sqlite3")


@pytest.fixture
def job():
    return Job(
        video_id="v1",
        r2_object_key="v1/video.mp4",
        account_id="test_account",
        caption="Привет #reels",
        publication_mode="TRIAL_REEL",
        scheduled_at=datetime(2026, 1, 1, tzinfo=UTC),
        idempotency_key="key1",
    )


class Storage:
    def __init__(self):
        self.downloads = 0

    def download(self, job, directory):
        self.downloads += 1
        path = directory / "video.mp4"
        path.write_bytes(b"mock-mp4")
        return path


@pytest.fixture
def storage():
    return Storage()
