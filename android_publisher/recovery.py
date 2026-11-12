"""Record an owner's verified outcome without reopening Instagram or retrying submission."""

from datetime import UTC, datetime

from .android import canonical_permalink
from .locking import device_session
from .models import Stop
from .store import Store


def record_owner_published(settings, jid, permalink):
    permalink = canonical_permalink(permalink)
    if not permalink:
        raise Stop("INVALID_PERMALINK")
    with device_session(settings):
        store = Store(settings.data_dir / "publisher.sqlite3")
        row = store.get(jid)
        if row is None:
            raise Stop("JOB_NOT_FOUND")
        if (
            row["status"] == "PUBLISHED"
            and row.get("result")
            and row["result"].get("permalink") == permalink
        ):
            return row
        if row["status"] != "UNKNOWN_RESULT" or not row["submitted"]:
            raise Stop("RECONCILIATION_REQUIRES_UNKNOWN_SUBMITTED")
        result = {
            "verified": True,
            "verification": "owner-confirmed",
            "permalink": permalink,
            "reconciled_at": datetime.now(UTC).isoformat(),
            "previous_error": row["error"],
        }
        store.finish(jid, "PUBLISHED", result=result, artifacts=row["artifacts"])
        # Keep the submission reservations and device pause. No automatic daily approval.
        return store.get(jid)
