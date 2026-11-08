import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def is_caas_deployment():
    """
    Check if the container is deployed on CaaS (Container-as-a-Service).
    Detection markers:
    1. Environment variable marker:
       - CAAS_DEPLOYED ("1", "true", "yes")
       - CAAS_ENV ("1", "true", "yes", "production")
       - DEPLOYMENT_ENV ("caas")
       - USE_CAAS_VOLUME ("1", "true", "yes")
    2. File markers (e.g. marker file placed inside the CaaS volume or in container):
       - .caas_marker, .caas_volume, or caas.marker in volume mount points or BASE_DIR
    """
    for var in ("CAAS_DEPLOYED", "CAAS_ENV", "USE_CAAS_VOLUME"):
        if os.environ.get(var, "").strip().lower() in ("1", "true", "yes", "production"):
            return True
    if os.environ.get("DEPLOYMENT_ENV", "").strip().lower() == "caas":
        return True

    marker_candidates = [
        os.path.join(BASE_DIR, ".caas_marker"),
        os.path.join(BASE_DIR, ".caas_volume"),
        os.path.join(BASE_DIR, ".caas_deployed"),
        "/app/data/.caas_marker",
        "/app/data/.caas_volume",
        "/app/data/caas.marker",
        "/app/.caas_marker",
        "/app/.caas_deployed",
        "/volume/.caas_marker",
        "/volume/.caas_volume",
    ]
    for marker in marker_candidates:
        if os.path.exists(marker):
            return True

    return False


def _resolve_data_dir():
    """
    Determine the active data directory:
    1. If CAAS_VOLUME_PATH or DATA_DIR is explicitly set in environment: use it.
    2. If running in a container with /volume mounted and accessible: use /volume.
    3. If deployed to CaaS (marker detected): use /app/data.
    4. Default: os.path.join(BASE_DIR, 'data') (which resolves to /app/data inside the container).
    """
    if is_caas_deployment():
        if os.environ.get("CAAS_VOLUME_PATH"):
            return os.path.abspath(os.environ["CAAS_VOLUME_PATH"])
        if os.environ.get("DATA_DIR"):
            return os.path.abspath(os.environ["DATA_DIR"])
        if os.path.isdir("/volume") and os.access("/volume", os.W_OK):
            return "/volume"
        return "/app/data"

    return os.path.abspath(os.path.join(BASE_DIR, "data"))


DATA_DIR = _resolve_data_dir()
DATABASE = os.path.join(DATA_DIR, "unicompass.db")
USERS_CSV = os.path.join(DATA_DIR, "users.csv")
COUNTRIES_CSV = os.path.join(DATA_DIR, "countries.csv")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")

# Ensure required directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)


def _resolve_secret_key():
    """
    Use SECRET_KEY from the environment if it is a real value. Otherwise generate a random
    key once and keep it in the data volume, so sessions survive restarts and the
    placeholder keys can never be used to forge a login.
    """
    key = os.environ.get("SECRET_KEY", "").strip()
    if key and not key.startswith(("change-me", "replace-with")):
        return key
    path = os.path.join(DATA_DIR, ".secret_key")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    with open(path) as f:
        return f.read().strip()


SECRET_KEY = _resolve_secret_key()

ENABLE_SURVEY = os.environ.get("ENABLE_SURVEY", "true").lower() in ("1", "true", "yes")
ENABLE_SAVE_BUTTON = os.environ.get("ENABLE_SAVE_BUTTON", "true").lower() in ("1", "true", "yes")
LOGOS_DIR = os.environ.get("LOGOS_DIR", os.path.join(BASE_DIR, "logos"))
ALLOWED_SECTORS = ["Commerce", "Finance", "ICT"]

