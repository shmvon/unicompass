"""
CSV-based authentication for UNI Compass.
Users are stored in a CSV file with columns: email,pincode,organisation,is_admin
"""

import csv
from config import USERS_CSV


def load_users():
    """
    Read all users from the CSV file and return a list of dicts.
    Expected CSV columns: email, pincode, organisation, is_admin
    """
    users = []
    with open(USERS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["is_admin"] = row.get("is_admin", "0").strip().lower() in ("1", "true", "yes")
            users.append(row)
    return users


def authenticate(email, pincode):
    """
    Verify email + pincode against the CSV.
    Returns the user dict on success, or None on failure.
    """
    email = email.strip().lower()
    for user in load_users():
        if user["email"].strip().lower() == email and user["pincode"].strip() == pincode.strip():
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
