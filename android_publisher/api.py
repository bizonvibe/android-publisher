import asyncio
import hashlib
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest

import portalocker
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .android import Android, MockAndroid
from .config import Settings
from .models import Execution, Job, Stop
from .storage import R2
from .store import Store
from .worker import Worker


def create_app(settings=None, android=None, storage=None):
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / "publisher.sqlite3")
    if android is None:
        if settings.android_backend == "mock":
            android = MockAndroid()
        elif settings.android_backend == "uiautomator2":
            android = Android(settings)
        else:
            raise ValueError("Unknown Android backend")
    if settings.android_backend == "mock" and settings.allow_live:
        raise ValueError("Mock backend cannot enable LIVE")
    worker = Worker(store, android, storage or R2(settings), settings)

    @asynccontextmanager
    async def lifespan(app):
        # Cross-process lock for the whole lifetime, including recovery. On Windows
        # use one data directory per device, and never run another UI automation tool.
        lock = portalocker.Lock(str(settings.data_dir / "device.lock"), timeout=0)
        device_key = hashlib.sha256(settings.device_serial.encode()).hexdigest()
        device_lock = portalocker.Lock(
            str(Path(tempfile.gettempdir()) / ("android-publisher-" + device_key + ".lock")),
            timeout=0,
        )
        lock.acquire()
        try:
            device_lock.acquire()
        except Exception:
            lock.release()
            raise
        try:
            store.recover(settings.max_attempts)
            app.state.worker_running = True

            async def run():
                while app.state.worker_running:
                    try:
                        await asyncio.to_thread(worker.tick)
                    except Exception:
                        logging.getLogger(__name__).error("WORKER_FAILURE")
                        app.state.worker_running = False
                        break
                    await asyncio.sleep(settings.poll_seconds)

            task = asyncio.create_task(run())
            try:
                yield
            finally:
                app.state.worker_running = False
                # Do not cancel to_thread: release device lock only after it stops.
                await task
        finally:
            device_lock.release()
            lock.release()

    app = FastAPI(title="Android Instagram Publisher", lifespan=lifespan)
    app.state.store, app.state.worker = store, worker
    app.state.worker_running = False
    security = HTTPBearer(auto_error=False)

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if not settings.api_token:
            raise HTTPException(503, "PUBLISHER_API_TOKEN is required")
        if credentials is None or not compare_digest(credentials.credentials, settings.api_token):
            raise HTTPException(401, "Invalid bearer token")

    @app.post("/v1/jobs", status_code=201, dependencies=[Depends(auth)])
    def create(job: Job):
        if job.execution_mode == Execution.LIVE and not settings.allow_live:
            raise HTTPException(403, "LIVE is disabled")
        try:
            return store.create(job)
        except Stop as exc:
            raise HTTPException(409, exc.code) from None

    @app.get("/v1/jobs/{jid}", dependencies=[Depends(auth)])
    def get(jid: str):
        row = store.get(jid)
        if row is None:
            raise HTTPException(404, "Job not found")
        return row

    @app.post("/v1/device/resume", dependencies=[Depends(auth)])
    def resume():
        # Operator has resolved the challenge/inspected uncertain publication on phone.
        # Terminal jobs stay terminal and are never retried by this action.
        store.pause(False)
        return {"paused": False}

    @app.get("/health", dependencies=[Depends(auth)])
    def health():
        # ADB availability only; never touches Instagram while worker is running.
        with store.db() as db:
            db.execute("SELECT 1")
        return {
            "service": "ok" if app.state.worker_running else "worker_stopped",
            "android": android.health(),
            "live_enabled": settings.allow_live,
            "backend": settings.android_backend,
            "paused": store.paused(),
        }

    return app
