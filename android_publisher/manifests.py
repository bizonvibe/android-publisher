"""Adapters for formats actually inspected in insta-reels-agent on main and feature/ru-reels-r2.

Do not interpret Library delivery manifests or asset-capture manifests as MP4 QA.
"""

from pathlib import PurePosixPath

from pydantic import ValidationError

from .models import Manifest, Stop


def normalize_manifest(raw, job):
    if isinstance(raw, dict) and ("assets" in raw or raw.get("provider") == "cloudflare-r2"):
        prefix = str(PurePosixPath(job.r2_object_key).parent)
        if (
            raw.get("version") != 1
            or raw.get("status") != "ready"
            or raw.get("provider") != "cloudflare-r2"
            or raw.get("prefix") != prefix
        ):
            raise Stop("MANIFEST_NOT_READY_OR_MISMATCH")
        assets = raw.get("assets")
        if not isinstance(assets, dict) or not isinstance(assets.get("video"), dict):
            raise Stop("INVALID_MANIFEST")
        video = assets["video"]
        if video.get("key") != job.r2_object_key or video.get("key") != prefix + "/video.mp4":
            raise Stop("MANIFEST_MISMATCH")
        if video.get("content_type") != "video/mp4":
            raise Stop("VIDEO_MIME_MISMATCH")
        try:
            manifest = Manifest(
                video_id=job.video_id,
                object_key=video["key"],
                sha256=video.get("sha256"),
                size_bytes=video.get("bytes"),
            )
        except ValidationError:
            raise Stop("INVALID_MANIFEST") from None
        return manifest, "factory-r2-v1"
    if isinstance(raw, dict) and "object_key" in raw:
        try:
            manifest = Manifest.model_validate(raw)
        except ValidationError:
            raise Stop("INVALID_MANIFEST") from None
        if manifest.video_id != job.video_id or manifest.object_key != job.r2_object_key:
            raise Stop("MANIFEST_MISMATCH")
        return manifest, "publisher-v1"
    # Real final-manifest.json is an array of {file, size_bytes, sha256, ...}.
    # Real fbo-fbs-misha2/qa.json is {file, bytes, sha256, ...}.
    entries = raw if isinstance(raw, list) else [raw]
    if not entries or not all(isinstance(entry, dict) for entry in entries):
        raise Stop("UNSUPPORTED_MANIFEST")
    basename = PurePosixPath(job.r2_object_key).name
    matches = [entry for entry in entries if entry.get("file") == basename]
    if len(matches) != 1:
        raise Stop("MANIFEST_AMBIGUOUS" if len(matches) > 1 else "MANIFEST_FILE_NOT_FOUND")
    entry = matches[0]
    if "size_bytes" in entry and "bytes" in entry and entry["size_bytes"] != entry["bytes"]:
        raise Stop("INVALID_MANIFEST")
    try:
        manifest = Manifest(
            video_id=job.video_id,
            object_key=job.r2_object_key,
            sha256=entry.get("sha256"),
            size_bytes=entry.get("size_bytes", entry.get("bytes")),
        )
    except ValidationError:
        raise Stop("INVALID_MANIFEST") from None
    return manifest, "agent-final-list" if isinstance(raw, list) else "agent-file-qa"
