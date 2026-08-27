import csv
import os
import shutil
from config import USERS_CSV, BASE_DIR, DATA_DIR


BLOCKED_PINCODE = "9999"


def load_users():
    """
    Read all users from the CSV file and return a list of dicts.
    Expected CSV columns: user, pincode, organisation, is_admin, country, sector
    """
    users = []
    csv_path = USERS_CSV
    if not os.path.exists(csv_path):
        fallback_csv = os.path.join(BASE_DIR, "users.csv")
        if os.path.exists(fallback_csv):
            os.makedirs(DATA_DIR, exist_ok=True)
            try:
                shutil.copyfile(fallback_csv, csv_path)
            except Exception:
                csv_path = fallback_csv

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uname = (row.get("user") or row.get("username") or "").strip()
                if not uname:
                    continue
                row["user"] = uname
                row["pincode"] = (row.get("pincode") or "").strip()
                row["is_admin"] = row.get("is_admin", "0").strip().lower() in ("1", "true", "yes")
                row["organisation"] = row.get("organisation", "").strip()
                row["country"] = row.get("country", "").strip()
                row["sector"] = row.get("sector", "").strip() or "All"
                users.append(row)
    except FileNotFoundError:
        pass
    return users


def get_user(username):
    """Return user dict if username matches, None otherwise."""
    uname = username.strip().lower()
    for user in load_users():
        if user["user"].strip().lower() == uname:
            return user
    return None


def is_user_blocked(username):
    """Return True if user's pincode is 9999, False otherwise."""
    user = get_user(username)
    if not user:
        return False
    return user.get("pincode", "").strip() == BLOCKED_PINCODE


def set_user_pincode(username, new_pincode):
    """
    Update the pincode for a user in USERS_CSV and sync to SQLite DB.
    Returns True if user was found and updated, False otherwise.
    """
    csv_path = USERS_CSV
    if not os.path.exists(csv_path):
        fallback_csv = os.path.join(BASE_DIR, "users.csv")
        if os.path.exists(fallback_csv):
            os.makedirs(DATA_DIR, exist_ok=True)
            try:
                shutil.copyfile(fallback_csv, csv_path)
            except Exception:
                csv_path = fallback_csv

    users = []
    fieldnames = ["user", "pincode", "organisation", "is_admin", "country", "sector"]
    found = False
    uname = username.strip().lower()

    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = reader.fieldnames
            for row in reader:
                if (row.get("user") or row.get("username") or "").strip().lower() == uname:
                    row["pincode"] = str(new_pincode)
                    found = True
                users.append(row)

    if found:
        temp_csv = csv_path + ".tmp"
        with open(temp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(users)
        os.replace(temp_csv, csv_path)

        # Also update sqlite users table if present
        try:
            from models import get_db
            with get_db() as db:
                db.execute("UPDATE users SET pincode = ? WHERE LOWER(TRIM(user)) = ?", (str(new_pincode), uname))
        except Exception:
            pass

    return found


def get_all_sectors():
    """Return a sorted list of all distinct sectors from users.csv, excluding 'All'."""
    sectors = set()
    for u in load_users():
        sec = u.get("sector", "")
        if sec and sec.lower() != "all":
            for s in sec.split(","):
                if s.strip():
                    sectors.add(s.strip())
    if not sectors:
        sectors = {"Commerce", "Finance", "ICT", "Services"}
    return sorted(sectors)


def authenticate(username, pincode):
    """
    Verify user + pincode against the CSV.
    Returns the user dict on success with available sectors, or None on failure.
    Pincode 9999 is always blocked.
    """
    uname = username.strip().lower()
    pcode = pincode.strip()

    # Pincode 9999 is always blocked
    if pcode == BLOCKED_PINCODE:
        return None

    all_known_sectors = get_all_sectors()

    for user in load_users():
        if user["user"].strip().lower() == uname:
            # Block users whose assigned pincode is 9999
            if user["pincode"].strip() == BLOCKED_PINCODE:
                return None
            if user["pincode"].strip() == pcode:
                user_sectors = []
                if user["is_admin"] or user["sector"].strip().lower() == "all":
                    user_sectors = list(all_known_sectors)
                else:
                    user_sectors = [s.strip() for s in user["sector"].split(",") if s.strip()]
                user["sectors"] = user_sectors or ["General"]
                return user
    return None


def get_all_organisations():
    """Return sorted list of distinct organisation names from the CSV."""
    orgs = set()
    for user in load_users():
        org = user.get("organisation", "").strip()
        if org:
            orgs.add(org)
    return sorted(orgs)


def get_all_org_sector_combinations():
    """
    Return sorted list of dicts for every distinct (organisation, sector) combination
    present in users.csv.
    """
    combos = []
    seen = set()
    for user in load_users():
        org = user.get("organisation", "").strip()
        sec = user.get("sector", "").strip() or "All"
        country = user.get("country", "").strip()
        uname = user.get("user", "").strip()
        is_admin = user.get("is_admin", False)
        if not org:
            continue

        sectors = [s.strip() for s in sec.split(",") if s.strip()] if sec.lower() != "all" else ["All"]
        for s in sectors:
            key = (org, s)
            if key not in seen:
                seen.add(key)
                combos.append({
                    "organisation": org,
                    "sector": s,
                    "country": country,
                    "users": [uname] if uname else [],
                    "is_admin": is_admin,
                })
            else:
                for c in combos:
                    if c["organisation"] == org and c["sector"] == s:
                        if uname and uname not in c["users"]:
                            c["users"].append(uname)
    return sorted(combos, key=lambda x: (x["organisation"].lower(), x["sector"].lower()))
