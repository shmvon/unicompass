import csv
import os
import shutil
from config import USERS_CSV, BASE_DIR, DATA_DIR


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
                row["is_admin"] = row.get("is_admin", "0").strip().lower() in ("1", "true", "yes")
                row["organisation"] = row.get("organisation", "").strip()
                row["country"] = row.get("country", "").strip()
                row["sector"] = row.get("sector", "").strip() or "All"
                users.append(row)
    except FileNotFoundError:
        pass
    return users


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
    """
    uname = username.strip().lower()
    pcode = pincode.strip()
    all_known_sectors = get_all_sectors()

    for user in load_users():
        if user["user"].strip().lower() == uname and user["pincode"].strip() == pcode:
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
