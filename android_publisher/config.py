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
