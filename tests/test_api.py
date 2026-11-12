from fastapi.testclient import TestClient

from android_publisher.android import MockAndroid
from android_publisher.api import create_app
from android_publisher.models import Execution

AUTH = {"Authorization": "Bearer test-token"}


def test_api_queue_status_health(settings, storage, job):
    app = create_app(settings, MockAndroid(), storage)
    job.scheduled_at = job.scheduled_at.replace(year=2099)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers=AUTH).json()["android"]["mock"]
        response = client.post("/v1/jobs", json=job.model_dump(mode="json"), headers=AUTH)
        assert response.status_code == 201
        jid = response.json()["id"]
        assert client.get("/v1/jobs/" + jid, headers=AUTH).json()["status"] == "QUEUED"
        assert (
            client.post("/v1/jobs", json=job.model_dump(mode="json"), headers=AUTH).json()["id"]
            == jid
        )
        job.caption = "conflicting"
        assert (
            client.post("/v1/jobs", json=job.model_dump(mode="json"), headers=AUTH).status_code
            == 409
        )
        assert client.get("/v1/jobs/missing", headers=AUTH).status_code == 404
        job.execution_mode = Execution.LIVE
        assert (
            client.post("/v1/jobs", json=job.model_dump(mode="json"), headers=AUTH).status_code
            == 403
        )
        app.state.store.pause()
        assert client.post("/v1/device/resume", headers=AUTH).json() == {"paused": False}


def test_missing_token_fails_closed(settings, storage):
    settings.api_token = ""
    app = create_app(settings, MockAndroid(), storage)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 503


def test_background_integration(settings, storage, job):
    import time

    app = create_app(settings, MockAndroid(), storage)
    with TestClient(app) as client:
        response = client.post("/v1/jobs", json=job.model_dump(mode="json"), headers=AUTH)
        jid = response.json()["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = client.get("/v1/jobs/" + jid, headers=AUTH).json()
            if row["status"] != "QUEUED" and row["status"] != "RUNNING":
                break
            time.sleep(0.01)
        assert row["status"] == "DRY_RUN_READY"
        assert app.state.worker.android.submissions == 0


def test_second_process_lock_even_with_another_database(settings, storage, tmp_path):
    import portalocker
    import pytest

    other_settings = settings.model_copy(update={"data_dir": tmp_path / "other"})
    app1 = create_app(settings, MockAndroid(), storage)
    app2 = create_app(other_settings, MockAndroid(), storage)
    with TestClient(app1):
        with pytest.raises(portalocker.exceptions.LockException):
            with TestClient(app2):
                pass


def test_api_rejects_naive_schedule_and_invalid_source(settings, storage, job):
    app = create_app(settings, MockAndroid(), storage)
    with TestClient(app) as client:
        payload = job.model_dump(mode="json")
        payload["scheduled_at"] = "2026-11-10T12:00:00"
        assert client.post("/v1/jobs", json=payload, headers=AUTH).status_code == 422
        payload = job.model_dump(mode="json")
        payload["publication_mode"] = "SHARE_TRIAL_TO_EVERYONE"
        assert client.post("/v1/jobs", json=payload, headers=AUTH).status_code == 422
