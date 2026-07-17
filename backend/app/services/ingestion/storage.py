"""
Object storage abstraction.

Existing implementation (MVP): rasters and previews written to a local folder and
served via `StaticFiles`. With more than one replica this breaks immediately - a
preview written on pod A is a 404 on pod B, and pod-local disk is ephemeral.

Enterprise solution: a `Storage` protocol with two implementations - `LocalStorage`
for dev and `S3Storage` for cloud. The service layer depends only on the protocol,
so moving to S3 is a config switch (`DMRV_STORAGE_BACKEND=s3`), not a code change.
Previews are served via short-lived presigned URLs in the S3 backend instead of a
public static mount.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("dmrv.storage")


class Storage(Protocol):
    def save(self, key: str, src_path: str) -> str: ...
    def local_path_for_processing(self, key: str) -> str: ...
    def url_for(self, key: str) -> str: ...
    def open_stream(self, key: str) -> BinaryIO: ...


class LocalStorage:
    """Dev/local backend. Files live under `root`; URLs are served by the API's
    /previews mount (dev only)."""

    def __init__(self, root: str, public_prefix: str = "/previews") -> None:
        self.root = Path(root)
        self.public_prefix = public_prefix
        self.root.mkdir(parents=True, exist_ok=True)

    def _abs(self, key: str) -> Path:
        p = (self.root / key).resolve()
        # Guard against path traversal via crafted keys.
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Illegal storage key (path traversal).")
        return p

    def save(self, key: str, src_path: str) -> str:
        dst = self._abs(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src_path, dst)
        return key

    def local_path_for_processing(self, key: str) -> str:
        return str(self._abs(key))

    def url_for(self, key: str) -> str:
        return f"{self.public_prefix}/{key}"

    def open_stream(self, key: str) -> BinaryIO:
        return open(self._abs(key), "rb")


class S3Storage:
    """Cloud backend. Kept dependency-light: boto3 is imported lazily so dev/test
    environments need not install it. Wired for use in staging/production."""

    def __init__(self, bucket: str, region: str, endpoint_url: str | None = None) -> None:
        import boto3  # lazy import

        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def save(self, key: str, src_path: str) -> str:
        self._client.upload_file(src_path, self.bucket, key)
        os.unlink(src_path)
        return key

    def local_path_for_processing(self, key: str) -> str:
        # Rasters are processed by streaming from S3 via GDAL's /vsis3/ driver.
        return f"/vsis3/{self.bucket}/{key}"

    def url_for(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
        )

    def open_stream(self, key: str):
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"]


def build_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        log.info("storage.backend", kind="s3", bucket=settings.s3_bucket)
        return S3Storage(settings.s3_bucket, settings.s3_region, settings.s3_endpoint_url)
    log.info("storage.backend", kind="local", root=settings.local_data_dir)
    return LocalStorage(settings.local_data_dir)
