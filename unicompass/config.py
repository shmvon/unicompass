import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DATABASE = os.path.join(DATA_DIR, "unicompass.db")
USERS_CSV = os.path.join(DATA_DIR, "users.csv")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
