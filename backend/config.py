"""Configuration loader — reads .env / env.config / OS environment.

Precedence (low to high):
  1. env.config (v1 legacy format)
  2. .env (docker-compose format)
  3. OS environment variables
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # agent_company/


def _load_env_file(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        # Skip empty values so they don't wipe out earlier-loaded values
        if not v:
            continue
        out[k.strip()] = v
    return out


_cfg: dict = {}
_cfg.update(_load_env_file(BASE_DIR / "env.config"))
_cfg.update(_load_env_file(BASE_DIR / ".env"))
# OS env overrides
for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD", "SECRET_KEY",
            "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "AWS_REGION", "PORT"):
    if os.environ.get(key):
        _cfg[key] = os.environ[key]

CFG = _cfg


def _build_database_url() -> str:
    if CFG.get("DATABASE_URL"):
        return CFG["DATABASE_URL"]
    user = CFG.get("POSTGRES_USER", "agent_company")
    pw = CFG.get("POSTGRES_PASSWORD", "devpassword")
    host = CFG.get("POSTGRES_HOST", "localhost")
    port = CFG.get("POSTGRES_PORT", "5432")
    db = CFG.get("POSTGRES_DB", "agent_company")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


DATABASE_URL = _build_database_url()

# Flask session signing key. In personal / desktop mode we auto-generate a
# stable per-install key so OSS users don't need to configure anything. In
# server / enterprise mode the operator MUST set SECRET_KEY — we refuse to
# start with the obviously-insecure default.
_DEV_SECRET_PLACEHOLDER = "dev-secret-change-me"
_raw_secret = CFG.get("SECRET_KEY", _DEV_SECRET_PLACEHOLDER)
if _raw_secret == _DEV_SECRET_PLACEHOLDER:
    _backend = (CFG.get("DB_BACKEND") or os.environ.get("DB_BACKEND") or "").lower()
    if _backend == "sqlite":
        # Personal mode — derive a stable key from the SQLite path so it's
        # unique per install and survives restarts without shipping any
        # secret in the source tree.
        import hashlib
        _sqlite_path = CFG.get("SQLITE_PATH") or os.environ.get("SQLITE_PATH") or str(Path.home() / ".agent_company" / "data.db")
        _raw_secret = hashlib.sha256(("holons:" + _sqlite_path).encode()).hexdigest()
    elif (CFG.get("FLASK_ENV") or os.environ.get("FLASK_ENV") or "").lower() == "production":
        raise RuntimeError(
            "SECRET_KEY is unset or still the dev default. Set SECRET_KEY in "
            "env.config or the environment before starting in production."
        )
SECRET_KEY = _raw_secret
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

AWS_ACCESS_KEY = CFG.get("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = CFG.get("AWS_SECRET_KEY", "")
AWS_REGION = CFG.get("AWS_REGION", "ap-northeast-1")

# Phase 2 — asset library credential encryption.
# Fernet symmetric key used to encrypt MCP / RAG connection secrets stored
# in asset_items.credential_encrypted. If absent, backend.services.asset_crypto
# auto-generates one on first use and appends it to env.config, with a loud
# startup warning so the operator knows the key is now in that file and
# shouldn't be checked into git.
ASSET_ENCRYPTION_KEY = CFG.get("ASSET_ENCRYPTION_KEY", "")
ENV_CONFIG_PATH = BASE_DIR / "env.config"

PORT = int(CFG.get("PORT", "8087"))
