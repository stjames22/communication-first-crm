import os
from pathlib import Path
from functools import lru_cache


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_local_env(path: Path = ENV_PATH, *, override: bool = False) -> None:
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if not override and key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ[key] = value


_load_local_env()


def runtime_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _railway_public_origin(value: str | None) -> str:
    domain = str(value or "").strip()
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    cleaned = str(value).strip().lower()
    if not cleaned:
        return default
    return cleaned in {"1", "true", "yes", "y", "on"}


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv(
            "GS_DATABASE_URL",
            os.getenv("DATABASE_URL", "sqlite:///./communication_first_crm.db"),
        )
        self.storage_backend = (
            os.getenv("GS_STORAGE_BACKEND", "").strip().lower()
            or ("s3" if os.getenv("GS_S3_BUCKET", "").strip() else "local")
        )
        self.uploads_path = os.getenv("GS_UPLOADS_PATH", "").strip()
        self.uploads_prefix = os.getenv("GS_UPLOADS_PREFIX", "").strip().strip("/")
        self.s3_bucket = os.getenv("GS_S3_BUCKET", "").strip()
        self.s3_region = os.getenv("GS_S3_REGION", os.getenv("AWS_DEFAULT_REGION", "")).strip()
        self.s3_endpoint_url = os.getenv("GS_S3_ENDPOINT_URL", "").strip()
        self.s3_access_key_id = os.getenv("GS_S3_ACCESS_KEY_ID", os.getenv("AWS_ACCESS_KEY_ID", "")).strip()
        self.s3_secret_access_key = os.getenv(
            "GS_S3_SECRET_ACCESS_KEY",
            os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        ).strip()
        self.s3_session_token = os.getenv("GS_S3_SESSION_TOKEN", os.getenv("AWS_SESSION_TOKEN", "")).strip()
        self.s3_force_path_style = _to_bool(os.getenv("GS_S3_FORCE_PATH_STYLE"), False)
        self.api_key = os.getenv("GS_API_KEY", "").strip()
        cors_source = os.getenv("GS_CORS_ORIGINS", "").strip()
        if not cors_source:
            cors_source = _railway_public_origin(os.getenv("RAILWAY_PUBLIC_DOMAIN"))
        self.cors_origins = _split_csv(cors_source)
        self.estimator_user = os.getenv("GS_ESTIMATOR_USER", "").strip()
        self.estimator_password = os.getenv("GS_ESTIMATOR_PASSWORD", "").strip()
        self.openai_api_key = runtime_openai_api_key()
        self.openai_vision_model = os.getenv("GS_OPENAI_VISION_MODEL", "gpt-4.1").strip() or "gpt-4.1"
        self.openai_ca_bundle = os.getenv("GS_OPENAI_CA_BUNDLE", "").strip()
        self.openai_allow_insecure_ssl = _to_bool(
            os.getenv("GS_OPENAI_ALLOW_INSECURE_SSL"),
            False,
        )
        self.allow_fallback_handwritten_measurement_ocr = _to_bool(
            os.getenv("GS_ALLOW_FALLBACK_HANDWRITTEN_MEASUREMENT_OCR"),
            False,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def refresh_settings() -> Settings:
    _load_local_env(override=False)
    get_settings.cache_clear()
    return get_settings()
