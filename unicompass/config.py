import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
DATABASE = os.path.join(BASE_DIR, "data", "unicompass.db")
USERS_CSV = os.path.join(BASE_DIR, "users.csv")
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORT_DIR = os.path.join(BASE_DIR, "data", "exports")
