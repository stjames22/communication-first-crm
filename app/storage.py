import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .settings import Settings, get_settings


def _default_local_upload_root() -> Path:
    return Path(__file__).resolve().parent.parent / "uploads"


def _normalize_key(key: str) -> str:
    return "/".join(part for part in str(key or "").replace("\\", "/").split("/") if part)


class StorageManager:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._s3_client = None

    def is_s3(self) -> bool:
        return str(self.settings.storage_backend or "").strip().lower() == "s3"

    def local_root(self) -> Path:
        configured = str(getattr(self.settings, "uploads_path", "") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return _default_local_upload_root()

    def prefixed_key(self, key: str) -> str:
        normalized = _normalize_key(key)
        prefix = str(getattr(self.settings, "uploads_prefix", "") or "").strip().strip("/")
        if prefix and normalized:
            return f"{prefix}/{normalized}"
        if prefix:
            return prefix
        return normalized

    def _s3_ref(self, key: str) -> str:
        return f"s3://{self.settings.s3_bucket}/{key}"

    def _parse_s3_ref(self, storage_path: str) -> Optional[tuple[str, str]]:
        text = str(storage_path or "").strip()
        if not text.startswith("s3://"):
            return None
        parsed = urlparse(text)
        bucket = parsed.netloc.strip()
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            return None
        return bucket, key

    def _client(self):
        if self._s3_client is not None:
            return self._s3_client
        if not self.is_s3():
            return None
        if not self.settings.s3_bucket:
            raise RuntimeError("GS_S3_BUCKET is required when GS_STORAGE_BACKEND=s3.")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("boto3 is required for S3-compatible storage.") from exc

        session = boto3.session.Session(
            aws_access_key_id=self.settings.s3_access_key_id or None,
            aws_secret_access_key=self.settings.s3_secret_access_key or None,
            aws_session_token=self.settings.s3_session_token or None,
            region_name=self.settings.s3_region or None,
        )
        config = Config(
            s3={"addressing_style": "path" if self.settings.s3_force_path_style else "auto"}
        )
        self._s3_client = session.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url or None,
            region_name=self.settings.s3_region or None,
            config=config,
        )
        return self._s3_client

    def save_bytes(self, key: str, raw: bytes, content_type: str = "") -> str:
        final_key = self.prefixed_key(key)
        if self.is_s3():
            params = {
                "Bucket": self.settings.s3_bucket,
                "Key": final_key,
                "Body": raw,
            }
            if content_type:
                params["ContentType"] = content_type
            self._client().put_object(**params)
            return self._s3_ref(final_key)

        destination = self.local_root() / final_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return str(destination)

    def read_bytes(self, storage_path: str) -> bytes:
        s3_ref = self._parse_s3_ref(storage_path)
        if s3_ref:
            bucket, key = s3_ref
            response = self._client().get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        return Path(storage_path).expanduser().read_bytes()

    def copy_into(self, source_path: str, destination_key: str, content_type: str = "") -> str:
        s3_ref = self._parse_s3_ref(source_path)
        final_key = self.prefixed_key(destination_key)
        if self.is_s3() and s3_ref:
            source_bucket, source_key = s3_ref
            if source_bucket == self.settings.s3_bucket:
                params = {
                    "Bucket": self.settings.s3_bucket,
                    "Key": final_key,
                    "CopySource": {"Bucket": source_bucket, "Key": source_key},
                }
                if content_type:
                    params["ContentType"] = content_type
                    params["MetadataDirective"] = "REPLACE"
                self._client().copy_object(**params)
                return self._s3_ref(final_key)
        return self.save_bytes(destination_key, self.read_bytes(source_path), content_type=content_type)

    def ensure_local_path(self, storage_path: str, temp_dir: Path, file_name: str = "upload.bin") -> Optional[Path]:
        s3_ref = self._parse_s3_ref(storage_path)
        if not s3_ref:
            path = Path(storage_path).expanduser()
            if path.exists() and path.is_file():
                return path
            return None

        suffix = Path(file_name).suffix or Path(s3_ref[1]).suffix or ".bin"
        stem = Path(file_name).stem or "upload"
        target = temp_dir / f"{stem}{suffix}"
        target.write_bytes(self.read_bytes(storage_path))
        return target

    def copy_local_path(self, source_path: Path, destination_key: str) -> str:
        if self.is_s3():
            return self.save_bytes(destination_key, source_path.read_bytes())

        destination = self.local_root() / self.prefixed_key(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        return str(destination)


storage = StorageManager()
