from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PUBLISHER_", env_file=None)
    data_dir: Path = Path("data")
    device_serial: str = ""
    adb_path: str = "adb"
    android_backend: str = "uiautomator2"
    selector_profile: Path = Path("profiles/instagram-en.example.json")
    allow_live: bool = False
    api_token: str = ""
    max_attempts: int = Field(default=3, ge=1, le=5)
    retry_seconds: int = Field(default=30, ge=1)
    poll_seconds: float = Field(default=2, gt=0)
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "content-factory-videos"


# Only nonsecret settings may be persisted by setup. LIVE is ENV-ONLY.
LOCAL_FIELDS = {
    "data_dir",
    "device_serial",
    "adb_path",
    "selector_profile",
    "r2_endpoint_url",
    "r2_bucket",
}


def load_settings(path=Path("publisher.local.json")):
    import json
    import os

    raw = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    values = {
        key: value
        for key, value in raw.items()
        if key in LOCAL_FIELDS and "PUBLISHER_" + key.upper() not in os.environ
    }
    # Match the producer's ENV names, but keep PUBLISHER_* as the highest priority.
    aliases = {
        "r2_access_key_id": "R2_ACCESS_KEY_ID",
        "r2_secret_access_key": "R2_SECRET_ACCESS_KEY",
        "r2_bucket": "R2_BUCKET_NAME",
        "r2_endpoint_url": "R2_ENDPOINT",
    }
    for key, env in aliases.items():
        if "PUBLISHER_" + key.upper() not in os.environ and os.environ.get(env):
            values[key] = os.environ[env]
    if (
        "r2_endpoint_url" not in values
        and not os.environ.get("PUBLISHER_R2_ENDPOINT_URL")
        and os.environ.get("R2_ACCOUNT_ID")
    ):
        values["r2_endpoint_url"] = (
            "https://" + os.environ["R2_ACCOUNT_ID"] + ".r2.cloudflarestorage.com"
        )
    return Settings(**values)
