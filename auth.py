import csv
import os
import shutil
from config import USERS_CSV, COUNTRIES_CSV, BASE_DIR, DATA_DIR, ALLOWED_SECTORS


BLOCKED_PINCODE = "9999"


def _ensure_users_csv():
    """
    Ensure USERS_CSV exists in DATA_DIR. If running on a newly mounted persistent volume
    (where DATA_DIR has no users.csv yet), seed it from the protected seeds directory
    or repository/bundled template.
    """
    if os.path.exists(USERS_CSV):
        return USERS_CSV

    os.makedirs(DATA_DIR, exist_ok=True)
    candidate_seeds = [
        os.path.join(BASE_DIR, "seeds", "users.csv"),
        "/app/seeds/users.csv",
        os.path.join(BASE_DIR, "data", "users.csv"),
        os.path.join(BASE_DIR, "users.csv"),
    ]
    for seed in candidate_seeds:
        if os.path.exists(seed) and os.path.abspath(seed) != os.path.abspath(USERS_CSV):
            try:
                shutil.copyfile(seed, USERS_CSV)
                return USERS_CSV
            except Exception:
                return seed
    return USERS_CSV


def _ensure_countries_csv():
    """
    Ensure COUNTRIES_CSV exists in DATA_DIR. If running on a newly mounted persistent volume
    (where DATA_DIR has no countries.csv yet), seed it from the protected seeds directory
    or repository template.
    """
    if os.path.exists(COUNTRIES_CSV):
        return COUNTRIES_CSV

    os.makedirs(DATA_DIR, exist_ok=True)
    candidate_seeds = [
        os.path.join(BASE_DIR, "seeds", "countries.csv"),
        "/app/seeds/countries.csv",
        os.path.join(BASE_DIR, "data", "countries.csv"),
        os.path.join(BASE_DIR, "countries.csv"),
    ]
    for seed in candidate_seeds:
        if os.path.exists(seed) and os.path.abspath(seed) != os.path.abspath(COUNTRIES_CSV):
            try:
                shutil.copyfile(seed, COUNTRIES_CSV)
                return COUNTRIES_CSV
            except Exception:
                return seed
    return COUNTRIES_CSV


def load_countries():
    """Read all countries and their 2-character ISO codes from countries.csv."""
    _ensure_countries_csv()
    countries = []
    if os.path.exists(COUNTRIES_CSV):
        try:
            with open(COUNTRIES_CSV, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cname = (row.get("country") or "").strip()
                    code = (row.get("code") or "").strip()
                    if cname:
                        countries.append({"country": cname, "code": code})
        except Exception:
            pass
    return countries


def load_users():
    """
    Read all users from the CSV file and return a list of dicts.
    Expected CSV columns: user, pincode, organisation, is_admin, country, sector
    """
    users = []
    csv_path = _ensure_users_csv()

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
                row["sector"] = row.get("sector", "").strip() or "UNI"
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
    csv_path = _ensure_users_csv()
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

    return found


def get_valid_countries():
    """Return list of valid country names from countries.csv plus 'Europe'."""
    countries = [c["country"] for c in load_countries()]
    if "Europe" not in countries:
        countries.append("Europe")
    return countries


def get_all_sectors():
    """Return a sorted list of all allowed sectors (Commerce, Finance, ICT)."""
    return list(ALLOWED_SECTORS)


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
                if user["is_admin"] or user["sector"].strip().lower() in ("all", "uni"):
                    user_sectors = list(all_known_sectors)
                else:
                    user_sectors = [s.strip() for s in user["sector"].split(",") if s.strip() in ALLOWED_SECTORS]
                user["sectors"] = user_sectors or ["Commerce"]
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
    present in users.csv, restricted to allowed sectors.
    """
    combos = []
    seen = set()
    for user in load_users():
        org = user.get("organisation", "").strip()
        sec = user.get("sector", "").strip() or "UNI"
        country = user.get("country", "").strip()
        uname = user.get("user", "").strip()
        is_admin = user.get("is_admin", False)
        if not org:
            continue

        if sec.lower() in ("all", "uni") or is_admin:
            sectors = list(ALLOWED_SECTORS)
        else:
            sectors = [s.strip() for s in sec.split(",") if s.strip() in ALLOWED_SECTORS]
            if not sectors:
                continue

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


def get_all_context_triplets():
    """
    Return sorted list of distinct (country, sector, organisation) tuples
    across all entries in users.csv, restricted to ALLOWED_SECTORS.
    """
    triplets = set()
    for user in load_users():
        org = user.get("organisation", "").strip()
        sec = user.get("sector", "").strip() or "UNI"
        country = user.get("country", "").strip()
        is_admin = user.get("is_admin", False)
        if not org or not country:
            continue

        if sec.lower() in ("all", "uni") or is_admin:
            sectors = ["UNI"] + list(ALLOWED_SECTORS)
        else:
            sectors = [s.strip() for s in sec.split(",") if s.strip() in ALLOWED_SECTORS]

        for s in sectors:
            triplets.add((country, s, org))

    return sorted(triplets, key=lambda x: (x[0].lower(), x[1].lower(), x[2].lower()))


def get_user_scope(user_dict_or_name, is_admin=False):
    """
    Return list of (country, sector, organisation) tuples allowed for this user.
    If is_admin is True, returns all system triplets.
    """
    all_triplets = get_all_context_triplets()
    if is_admin:
        return all_triplets

    if isinstance(user_dict_or_name, dict):
        user = user_dict_or_name
    else:
        user = get_user(str(user_dict_or_name))

    if not user:
        return all_triplets if is_admin else []

    if user.get("is_admin"):
        return all_triplets

    # Standard user: look for their matching tuples
    uname = user.get("user", "").strip().lower()
    user_org = user.get("organisation", "").strip()
    user_cnt = user.get("country", "").strip()
    sec = user.get("sector", "").strip() or "UNI"

    if sec.lower() in ("all", "uni"):
        user_secs = {"UNI"} | set(ALLOWED_SECTORS)
    else:
        user_secs = {s.strip() for s in sec.split(",") if s.strip() in ALLOWED_SECTORS}

    scope = []
    # Match against all triplets that match user's org, country, and sectors
    for c, s, o in all_triplets:
        if user_org and o.lower() != user_org.lower():
            continue
        if user_cnt and c.lower() != user_cnt.lower():
            continue
        if s not in user_secs:
            continue
        scope.append((c, s, o))

    if not scope and user_org and user_cnt:
        for s in (user_secs or ALLOWED_SECTORS):
            scope.append((user_cnt, s, user_org))

    return scope


def get_affiliate_user_for_context(org, country=None, sector=None):
    """
    Find matching affiliate user from users.csv for an organisation, country, and sector.
    """
    if not org or org == "None":
        return None
    users = load_users()
    # 1. Match non-admin user by org, country, sector
    for u in users:
        if u.get("is_admin"):
            continue
        u_org = u.get("organisation", "").strip().lower()
        if u_org == org.strip().lower():
            u_cnt = u.get("country", "").strip().lower()
            u_sec = u.get("sector", "").strip().lower()
            if country and country != "None" and u_cnt == country.strip().lower():
                if sector and sector not in ("None", "UNI") and (u_sec == sector.strip().lower() or u_sec in ("all", "uni")):
                    return u.get("user")

    # 2. Match non-admin user by org and country
    for u in users:
        if u.get("is_admin"):
            continue
        u_org = u.get("organisation", "").strip().lower()
        if u_org == org.strip().lower():
            u_cnt = u.get("country", "").strip().lower()
            if country and country != "None" and u_cnt == country.strip().lower():
                return u.get("user")

    # 3. Match non-admin user by org
    for u in users:
        if u.get("is_admin"):
            continue
        u_org = u.get("organisation", "").strip().lower()
        if u_org == org.strip().lower():
            return u.get("user")

    # 4. Any user matching org
    for u in users:
        u_org = u.get("organisation", "").strip().lower()
        if u_org == org.strip().lower():
            return u.get("user")

    return org


def resolve_cascading_context(scope, country=None, sector=None, org=None, is_admin=False, home_org=None):
    """
    Given a user's scope of (country, sector, organisation) triplets and optional
    current selections, resolve consistent (active_country, active_sector, active_org)
    and return available options for all three dimensions.

    Cascading rules:
    - If org is chosen: country becomes that org's country, and sectors are restricted
      to sectors where that org is present.
    - If country is chosen: organisations are restricted to that country.
    - If sector is chosen: options are filtered to those where the sector is active.
    - For admin users:
      * Their own organisation (home_org) is always available in available_orgs.
      * "None" is available as an option across Country, Sector, and Organisation.
    """
    if not scope:
        all_trips = get_all_context_triplets()
        scope = all_trips if all_trips else [("Europe", "Commerce", "UNI Europe")]

    # 1. Resolve active organisation if specified
    active_org = None
    if org:
        if org == "None" and is_admin:
            active_org = "None"
        elif home_org and org.strip().lower() == home_org.strip().lower():
            active_org = home_org
        else:
            matching_orgs = [o for (c, s, o) in scope if o.lower() == org.strip().lower()]
            if matching_orgs:
                active_org = matching_orgs[0]

    # 2. Resolve active country
    active_country = None
    if active_org and active_org != "None":
        org_countries = [c for (c, s, o) in scope if o == active_org]
        if org_countries:
            active_country = org_countries[0]
    elif country:
        if country == "None" and is_admin:
            active_country = "None"
        elif country.lower() == "europe":
            active_country = "Europe"
        else:
            matching_countries = [c for (c, s, o) in scope if c.lower() == country.strip().lower()]
            if matching_countries:
                active_country = matching_countries[0]

    if not active_country and (not is_admin or country != "None"):
        active_country = "Europe" if is_admin else scope[0][0]

    # 3. Filter scope by active country (Europe represents all European scope)
    if active_country and active_country not in ("None", "Europe"):
        country_scope = [t for t in scope if t[0] == active_country]
        if not country_scope:
            country_scope = scope
            active_country = scope[0][0]
    else:
        country_scope = scope

    # 4. Resolve active sector (UNI represents cross-sectoral scope)
    active_sector = None
    if sector and sector.upper() == "UNI":
        active_sector = "UNI"
    elif sector == "None" and is_admin:
        active_sector = "None"
    elif active_org and active_org != "None":
        org_sectors = [s for (c, s, o) in country_scope if o == active_org]
        if sector and sector in org_sectors:
            active_sector = sector
        elif "UNI" in org_sectors and (not sector or sector == "UNI"):
            active_sector = "UNI"
        elif org_sectors:
            active_sector = org_sectors[0]

    if not active_sector and sector and sector not in ("None",):
        country_sectors = [s for (c, s, o) in country_scope if s == sector]
        if country_sectors:
            active_sector = country_sectors[0]

    if not active_sector and (not is_admin or sector != "None"):
        active_sector = "UNI" if is_admin else country_scope[0][1]

    # 5. Resolve active organisation if not yet set
    if not active_org and (not is_admin or org != "None"):
        orgs_in_country = [o for (c, s, o) in country_scope]
        if home_org and (home_org in orgs_in_country or active_country in ("None", "Europe")):
            active_org = home_org
        else:
            cs_orgs = [o for (c, s, o) in country_scope if s == active_sector]
            if cs_orgs:
                active_org = cs_orgs[0]
            elif country_scope:
                active_org = country_scope[0][2]
            else:
                active_org = home_org

    # Re-verify organisation's country and sector consistency when active_org is not "None"
    if active_org and active_org != "None":
        org_countries = [c for (c, s, o) in scope if o == active_org]
        if org_countries and active_country not in org_countries and active_country not in ("None", "Europe"):
            if org:
                active_country = org_countries[0]
                country_scope = [t for t in scope if t[0] == active_country]
            else:
                cs_orgs = [o for (c, s, o) in country_scope if s == active_sector]
                if cs_orgs:
                    active_org = cs_orgs[0]
                elif country_scope:
                    active_org = country_scope[0][2]

        org_sectors = [s for (c, s, o) in country_scope if o == active_org]
        if org_sectors and active_sector not in org_sectors and active_sector not in ("None", "UNI"):
            active_sector = org_sectors[0]

    # 6. Compute available options for each dimension based on cascading rules
    available_countries = sorted(list({c for (c, s, o) in scope if c != "Europe"}), key=lambda x: x.lower())
    if "Europe" in {c for (c, s, o) in scope}:
        available_countries.insert(0, "Europe")

    if active_country and active_country not in ("None", "Europe"):
        if active_sector and active_sector not in ("None", "UNI"):
            orgs_in_country_and_sector = sorted(list({o for (c, s, o) in country_scope if s == active_sector}), key=lambda x: x.lower())
        else:
            orgs_in_country_and_sector = sorted(list({o for (c, s, o) in country_scope}), key=lambda x: x.lower())
        if orgs_in_country_and_sector:
            available_orgs = orgs_in_country_and_sector
        else:
            available_orgs = sorted(list({o for (c, s, o) in country_scope}), key=lambda x: x.lower())
        available_sectors = [s for s in ALLOWED_SECTORS if any(c == active_country and s == item_sec for (c, item_sec, o) in country_scope)]
    else:
        if active_sector and active_sector not in ("None", "UNI"):
            available_orgs = sorted(list({o for (c, s, o) in scope if s == active_sector}), key=lambda x: x.lower())
        else:
            available_orgs = sorted(list({o for (c, s, o) in scope}), key=lambda x: x.lower())
        available_sectors = list(ALLOWED_SECTORS)

    if not available_sectors:
        available_sectors = list(ALLOWED_SECTORS)

    if is_admin:
        # 1. Admin's own organisation should always be an option you can select, regardless of country and sector
        if home_org and home_org != "None" and home_org not in available_orgs:
            available_orgs.append(home_org)
        if "None" not in available_orgs:
            available_orgs.insert(0, "None")
        # 2. Replace All and None for sectors to UNI
        if "UNI" not in available_sectors:
            available_sectors.insert(0, "UNI")
        available_sectors = [s for s in available_sectors if s not in ("None", "All")]
        # 3. Replace All and None for countries to Europe
        if "Europe" in available_countries:
            available_countries.remove("Europe")
        available_countries.insert(0, "Europe")
        available_countries = [c for c in available_countries if c not in ("None", "All")]

    return {
        "active_country": active_country,
        "active_sector": active_sector,
        "active_org": active_org,
        "available_countries": available_countries,
        "available_sectors": available_sectors,
        "available_orgs": available_orgs,
        "has_multiple_countries": len(available_countries) > 1,
        "has_multiple_sectors": len(available_sectors) > 1,
        "has_multiple_orgs": len(available_orgs) > 1,
    }
