import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from .models import Job, Stop


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    due TEXT NOT NULL, submitted INTEGER NOT NULL DEFAULT 0,
                    result TEXT, error TEXT, artifacts TEXT, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO device_state VALUES(1,0);
                CREATE TABLE IF NOT EXISTS publication_hashes (
                    account TEXT NOT NULL, sha256 TEXT NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY(account, sha256)
                );
                CREATE TABLE IF NOT EXISTS publications (
                    account TEXT NOT NULL, video TEXT NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY(account, video)
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, job: Job):
        payload = job.model_dump_json()
        canonical = job.model_dump(mode="json")
        if "scheduled_at" not in job.model_fields_set:
            canonical.pop("scheduled_at")
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM jobs WHERE idem=?", (job.idempotency_key,)).fetchone()
            if old:
                previous = Job.model_validate_json(old["payload"]).model_dump(mode="json")
                if "scheduled_at" not in job.model_fields_set:
                    previous.pop("scheduled_at")
                if previous != canonical:
                    raise Stop("IDEMPOTENCY_CONFLICT")
                return self.decode(old)
            jid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO jobs(id,idem,fingerprint,payload,status,due,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    jid,
                    job.idempotency_key,
                    fingerprint,
                    payload,
                    "QUEUED",
                    job.scheduled_at.isoformat(),
                    now(),
                ),
            )
        return self.get(jid)

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        value["job"] = json.loads(value.pop("payload"))
        for key in ("result", "artifacts"):
            value[key] = json.loads(value[key]) if value[key] else None
        for key in ("fingerprint", "idem"):
            value.pop(key)
        return value

    def get(self, jid):
        with self.db() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())

    def recover(self, max_attempts=3):
        # Called only after acquiring the OS worker lock. No other worker may be alive.
        with self.db() as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE status IN ('RUNNING','SUBMITTING') AND submitted=1"
            ).fetchone():
                db.execute("UPDATE device_state SET paused=1")
            db.execute(
                "UPDATE jobs SET status=CASE WHEN submitted=1 THEN 'UNKNOWN_RESULT' "
                "ELSE 'QUEUED' END, error='WORKER_RESTARTED', updated=? "
                "WHERE status IN ('RUNNING','SUBMITTING')",
                (now(),),
            )
            db.execute(
                "UPDATE jobs SET status='FAILED',error='ATTEMPTS_EXHAUSTED',updated=? "
                "WHERE status='QUEUED' AND attempts>=?",
                (now(), max_attempts),
            )

    def claim(self, jid=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT paused FROM device_state WHERE id=1").fetchone()[0]:
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE status='QUEUED' AND due<=? AND (? IS NULL OR id=?) ORDER BY due LIMIT 1",
                (now(), jid, jid),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='RUNNING', attempts=attempts+1, updated=? WHERE id=?",
                (now(), row["id"]),
            )
        return self.get(row["id"])

    def barrier(self, jid, job, sha256=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if job.publication_mode != "SHARE_TRIAL_TO_EVERYONE":
                try:
                    db.execute(
                        "INSERT INTO publications VALUES(?,?,?)",
                        (job.account_id.lower(), job.video_id, jid),
                    )
                except sqlite3.IntegrityError:
                    raise Stop("DUPLICATE_PUBLICATION") from None
                if sha256:
                    try:
                        db.execute(
                            "INSERT INTO publication_hashes VALUES(?,?,?)",
                            (job.account_id.lower(), sha256, jid),
                        )
                    except sqlite3.IntegrityError:
                        raise Stop("DUPLICATE_PUBLICATION") from None
            else:
                # Only one promotion of the same trial, even across idempotency keys.
                try:
                    db.execute(
                        "INSERT INTO publications VALUES(?,?,?)",
                        (job.account_id.lower(), "promote:" + job.source_job_id, jid),
                    )
                except sqlite3.IntegrityError:
                    raise Stop("DUPLICATE_PUBLICATION") from None
            db.execute(
                "UPDATE jobs SET submitted=1,status='SUBMITTING',updated=? WHERE id=?", (now(), jid)
            )

    def finish(self, jid, status, *, result=None, error=None, artifacts=None, due=None):
        with self.db() as db:
            db.execute(
                "UPDATE jobs SET status=?, result=?, error=?, artifacts=?, "
                "due=COALESCE(?,due),updated=? WHERE id=?",
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    json.dumps(artifacts) if artifacts else None,
                    due,
                    now(),
                    jid,
                ),
            )

    def pause(self, paused=True):
        with self.db() as db:
            db.execute("UPDATE device_state SET paused=? WHERE id=1", (int(paused),))

    def paused(self):
        with self.db() as db:
            return bool(db.execute("SELECT paused FROM device_state WHERE id=1").fetchone()[0])

    def duplicate(self, job, sha256=None):
        if job.publication_mode == "SHARE_TRIAL_TO_EVERYONE":
            return False
        with self.db() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM publications WHERE account=? AND video=?",
                    (job.account_id.lower(), job.video_id),
                ).fetchone()
                or (
                    sha256
                    and db.execute(
                        "SELECT 1 FROM publication_hashes WHERE account=? AND sha256=?",
                        (job.account_id.lower(), sha256),
                    ).fetchone()
                )
            )
