"""
Database models for UNI Compass.
Uses SQLite via the sqlite3 standard library — no ORM required.
"""

import csv
import sqlite3
import os
import datetime
import tempfile
from zipfile import ZipFile
from io import BytesIO
from contextlib import contextmanager
from config import DATABASE, EXPORT_DIR


@contextmanager
def get_db():
    """Open a connection to the SQLite database, yielding a transaction context and closing it on exit."""
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    """Create all tables if they do not exist."""
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user        TEXT    NOT NULL UNIQUE,
                pincode     TEXT    NOT NULL,
                organisation TEXT   NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                country     TEXT    DEFAULT '',
                sector      TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user     TEXT    NOT NULL,
                attempted_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS login_bans (
                user     TEXT    PRIMARY KEY,
                banned_until TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS annual_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                organisation TEXT    NOT NULL,
                variable     TEXT    NOT NULL,
                year         INTEGER NOT NULL,
                value        TEXT    DEFAULT '',
                comment      TEXT    DEFAULT '',
                qual_value   TEXT    DEFAULT '',
                row_mode     TEXT    DEFAULT '',
                UNIQUE(organisation, variable, year)
            );

            CREATE TABLE IF NOT EXISTS agreements (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                organisation    TEXT    NOT NULL,
                year            INTEGER NOT NULL,
                row_idx         INTEGER NOT NULL,
                company_name    TEXT    DEFAULT '',
                wage_increase   TEXT    DEFAULT '',
                workers_affected INTEGER DEFAULT 0,
                level           TEXT    DEFAULT '',
                date_of_agreement TEXT   DEFAULT '',
                one_off_lump_sum TEXT    DEFAULT '',
                bargaining_climate TEXT DEFAULT '',
                comment         TEXT    DEFAULT '',
                duration        TEXT    DEFAULT '',
                working_time_change TEXT DEFAULT '',
                other_changes   TEXT    DEFAULT '',
                one_off_bonus   TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS companies (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                organisation TEXT    NOT NULL,
                year         INTEGER NOT NULL,
                row_idx      INTEGER NOT NULL,
                company_name TEXT    DEFAULT '',
                agreement    TEXT    DEFAULT '',
                workers      INTEGER DEFAULT 0,
                number_of_unions INTEGER DEFAULT 0,
                union_members    INTEGER DEFAULT 0,
                ewc_presence  TEXT   DEFAULT '',
                mnc           TEXT   DEFAULT '',
                bargaining_climate TEXT DEFAULT '',
                one_off_lump_sum TEXT   DEFAULT '',
                comment      TEXT    DEFAULT '',
                union_density TEXT   DEFAULT '',
                worker_representation TEXT DEFAULT '',
                one_off_bonus TEXT   DEFAULT ''
            );
        """)
        # Migrate existing tables that may lack new columns
        for table, col in (("agreements", "bargaining_climate"),
                           ("agreements", "duration"),
                           ("agreements", "working_time_change"),
                           ("agreements", "other_changes"),
                           ("agreements", "one_off_lump_sum"),
                           ("agreements", "one_off_bonus"),
                           ("companies", "ewc_presence"),
                           ("companies", "mnc"),
                           ("companies", "bargaining_climate"),
                           ("companies", "union_density"),
                           ("companies", "worker_representation"),
                           ("companies", "one_off_lump_sum"),
                           ("companies", "one_off_bonus"),
                           ("users", "country"),
                           ("users", "sector"),
                           ("annual_results", "qual_value"),
                           ("annual_results", "row_mode"),
                           ("annual_results", "sector"),
                           ("agreements", "sector"),
                           ("companies", "sector")):
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT ''")
            except Exception:
                pass

        # Migrate existing annual results avg_wage_increase variable to avg_pay_increase
        try:
            db.execute("UPDATE annual_results SET variable = 'avg_pay_increase' WHERE variable = 'avg_wage_increase'")
        except Exception:
            pass

        # Migrate existing annual results one_off_bonus variable to one_off_lump_sum
        try:
            db.execute("UPDATE annual_results SET variable = 'one_off_lump_sum' WHERE variable = 'one_off_bonus'")
        except Exception:
            pass        # Migrate annual_results table if it lacks sector in UNIQUE constraint
        try:
            # Check table definition
            tbl_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='annual_results'").fetchone()
            if tbl_sql and "UNIQUE(organisation, variable, year)" in tbl_sql[0]:
                db.executescript("""
                    CREATE TABLE annual_results_migrated (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        organisation TEXT    NOT NULL,
                        sector       TEXT    DEFAULT '',
                        variable     TEXT    NOT NULL,
                        year         INTEGER NOT NULL,
                        value        TEXT    DEFAULT '',
                        comment      TEXT    DEFAULT '',
                        qual_value   TEXT    DEFAULT '',
                        row_mode     TEXT    DEFAULT '',
                        UNIQUE(organisation, sector, variable, year)
                    );
                    INSERT OR IGNORE INTO annual_results_migrated (id, organisation, sector, variable, year, value, comment, qual_value, row_mode)
                    SELECT id, organisation, COALESCE(sector, ''), variable, year, value, comment, COALESCE(qual_value, ''), COALESCE(row_mode, '') FROM annual_results;
                    DROP TABLE annual_results;
                    ALTER TABLE annual_results_migrated RENAME TO annual_results;
                """)
        except Exception:
            pass

        # Migrate old agreements and companies one_off_bonus values to one_off_lump_sum
        try:
            db.execute("UPDATE agreements SET one_off_lump_sum = one_off_bonus WHERE (one_off_lump_sum = '' OR one_off_lump_sum IS NULL) AND one_off_bonus != '' AND one_off_bonus IS NOT NULL")
            db.execute("UPDATE companies SET one_off_lump_sum = one_off_bonus WHERE (one_off_lump_sum = '' OR one_off_lump_sum IS NULL) AND one_off_bonus != '' AND one_off_bonus IS NOT NULL")
        except Exception:
            pass

        # Migrate transient login_bans and login_attempts tables to use user
        try:
            tbl_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='login_bans'").fetchone()
            if tbl_sql and "email" in tbl_sql[0]:
                db.execute("DROP TABLE IF EXISTS login_bans")
                db.execute("CREATE TABLE login_bans (user TEXT PRIMARY KEY, banned_until TEXT NOT NULL)")
        except Exception:
            pass

        try:
            tbl_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='login_attempts'").fetchone()
            if tbl_sql and "email" in tbl_sql[0]:
                db.execute("DROP TABLE IF EXISTS login_attempts")
                db.execute("CREATE TABLE login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, attempted_at TEXT NOT NULL DEFAULT (datetime('now')))")
        except Exception:
            pass


def check_login_rate_limit(user):
    """Return True if the user is allowed to attempt login, False if rate-limited."""
    user = user.strip().lower()
    with get_db() as db:
        ban = db.execute("SELECT banned_until FROM login_bans WHERE user = ?", (user,)).fetchone()
        if ban:
            from datetime import datetime
            banned_until = datetime.fromisoformat(ban["banned_until"])
            if datetime.utcnow() < banned_until:
                return False
            db.execute("DELETE FROM login_bans WHERE user = ?", (user,))
        return True


def record_failed_login(user):
    """Record a failed login attempt; ban for 15 minutes after 5 failures within 15 minutes."""
    user = user.strip().lower()
    with get_db() as db:
        db.execute("INSERT INTO login_attempts (user) VALUES (?)", (user,))
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(minutes=15)).isoformat()
        count = db.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE user = ? AND attempted_at >= ?",
            (user, cutoff)
        ).fetchone()[0]
        if count >= 5:
            banned_until = (datetime.datetime.utcnow() + datetime.timedelta(minutes=15)).isoformat()
            db.execute(
                "INSERT OR REPLACE INTO login_bans (user, banned_until) VALUES (?, ?)",
                (user, banned_until)
            )


def clear_login_attempts(user):
    """Clear failed login attempts for a user after a successful login."""
    user = user.strip().lower()
    with get_db() as db:
        db.execute("DELETE FROM login_attempts WHERE user = ?", (user,))
        db.execute("DELETE FROM login_bans WHERE user = ?", (user,))


def get_annual_results(organisation, years=None, sector=None):
    """Return a dict: (variable, year) -> {'value': ..., 'comment': ..., 'qual_value': ..., 'row_mode': ...}"""
    if isinstance(years, int):
        years = [years]

    qual_words = {
        "100%", "very high", "high", "average", "normal", "moderate", "low", "very low", "0%", "none",
        "80-100%", "60-79%", "40-59%", "20-39%", "0-19%",
        "strong increase", "moderate increase", "minimal increase", "no increase", "decrease",
        "minor increase", "stable", "minor decrease", "strong decrease",
        "many agreements / many employers", "many agreements / few employers",
        "few agreements / many employers", "few agreements / few employers", "no agreements",
        "many agreements/many employers", "many agreements/few employers",
        "few agreements/many employers", "few agreements/few employers",
        "very many agreements", "many agreements", "few agreements", "very few agreements",
        "very common", "common", "rare", "very rare",
        "strong inflation (+3%)", "minor inflation (+1/+3%)", "stable prices (+1/-1%)",
        "minor deflation (-1/-3%)", "strong deflation (-3%)",
        "strong inflation", "minor inflation", "stable prices", "minor deflation", "strong deflation",
        "strong growth", "moderate growth", "no growth", "moderate decline", "strong decline",
        "very unfavourable", "rather unfavourable", "neutral", "rather favourable", "very favourable",
        "very difficult", "difficult", "favourable"
    }

    with get_db() as db:
        if years:
            placeholders = ",".join("?" for _ in years)
            if sector and sector.lower() != "all":
                rows = db.execute(
                    f"SELECT variable, year, value, comment, qual_value, row_mode FROM annual_results "
                    f"WHERE organisation = ? AND sector = ? AND year IN ({placeholders})",
                    (organisation, sector, *years)
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT variable, year, value, comment, qual_value, row_mode FROM annual_results "
                    f"WHERE organisation = ? AND year IN ({placeholders})",
                    (organisation, *years)
                ).fetchall()
        else:
            if sector and sector.lower() != "all":
                rows = db.execute(
                    "SELECT variable, year, value, comment, qual_value, row_mode FROM annual_results "
                    "WHERE organisation = ? AND sector = ?",
                    (organisation, sector)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT variable, year, value, comment, qual_value, row_mode FROM annual_results "
                    "WHERE organisation = ?",
                    (organisation,)
                ).fetchall()
    result = {}
    for r in rows:
        val = r["value"] or ""
        qual = r["qual_value"] or ""
        comment = r["comment"] or ""
        row_mode = r["row_mode"] or ""
        
        # Backward compatibility check:
        if val in qual_words and not qual:
            qual = val
            val = ""
            
        result[(r["variable"], r["year"])] = {
            "value": val,
            "qual_value": qual,
            "row_mode": row_mode,
            "comment": comment
        }
    return result


def save_annual_results(organisation, data, sector=""):
    """data is list of dicts: [{'variable': ..., 'year': ..., 'value': ..., 'qual_value': ..., 'row_mode': ..., 'comment': ...}]"""
    sec = sector if (sector and sector.lower() != "all") else ""
    with get_db() as db:
        for d in data:
            item_sec = sec or d.get("sector", "")
            db.execute(
                "DELETE FROM annual_results WHERE organisation = ? AND sector = ? AND variable = ? AND year = ?",
                (organisation, item_sec, d["variable"], d["year"])
            )
            db.execute(
                "INSERT INTO annual_results (organisation, sector, variable, year, value, comment, qual_value, row_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (organisation, item_sec, d["variable"], d["year"], d.get("value", ""), d.get("comment", ""),
                 d.get("qual_value", ""), d.get("row_mode", ""))
            )


def get_agreements(organisation, year, sector=None):
    """Return rows sorted by workers_affected descending."""
    with get_db() as db:
        if sector and sector.lower() != "all":
            rows = db.execute(
                "SELECT * FROM agreements WHERE organisation = ? AND sector = ? AND year = ? "
                "ORDER BY workers_affected DESC, row_idx",
                (organisation, sector, year)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM agreements WHERE organisation = ? AND year = ? "
                "ORDER BY workers_affected DESC, row_idx",
                (organisation, year)
            ).fetchall()
    return [dict(r) for r in rows]


def save_agreements(organisation, year, rows, sector=""):
    """Replace all agreement rows for this org+sector+year."""
    sec = sector if (sector and sector.lower() != "all") else ""
    with get_db() as db:
        db.execute("DELETE FROM agreements WHERE organisation = ? AND sector = ? AND year = ?", (organisation, sec, year))
        for i, r in enumerate(rows):
            db.execute(
                "INSERT INTO agreements (organisation, sector, year, row_idx, company_name, wage_increase, workers_affected, level, date_of_agreement, one_off_lump_sum, bargaining_climate, comment, duration, working_time_change, other_changes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (organisation, sec, year, i, r.get("company_name", ""), r.get("wage_increase", ""),
                 r.get("workers_affected", 0), r.get("level", ""), r.get("date_of_agreement", ""),
                 r.get("one_off_lump_sum", ""), r.get("bargaining_climate", ""), r.get("comment", ""),
                 r.get("duration", ""), r.get("working_time_change", ""), r.get("other_changes", ""))
            )


def get_companies(organisation, year, sector=None):
    """Return rows sorted by workers descending."""
    with get_db() as db:
        if sector and sector.lower() != "all":
            rows = db.execute(
                "SELECT * FROM companies WHERE organisation = ? AND sector = ? AND year = ? "
                "ORDER BY workers DESC, row_idx",
                (organisation, sector, year)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM companies WHERE organisation = ? AND year = ? "
                "ORDER BY workers DESC, row_idx",
                (organisation, year)
            ).fetchall()
    return [dict(r) for r in rows]


def save_companies(organisation, year, rows, sector=""):
    """Replace all company rows for this org+sector+year."""
    sec = sector if (sector and sector.lower() != "all") else ""
    with get_db() as db:
        db.execute("DELETE FROM companies WHERE organisation = ? AND sector = ? AND year = ?", (organisation, sec, year))
        for i, r in enumerate(rows):
            db.execute(
                "INSERT INTO companies (organisation, sector, year, row_idx, company_name, agreement, workers, number_of_unions, union_members, ewc_presence, mnc, bargaining_climate, one_off_lump_sum, comment, union_density, worker_representation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (organisation, sec, year, i, r.get("company_name", ""), r.get("agreement", ""),
                 r.get("workers", 0), r.get("number_of_unions", 0), r.get("union_members", 0),
                 r.get("ewc_presence", ""), r.get("mnc", ""), r.get("bargaining_climate", ""),
                 r.get("one_off_lump_sum", ""), r.get("comment", ""), r.get("union_density", ""), r.get("worker_representation", ""))
            )


def get_all_organisations():
    """Return sorted list of distinct organisation names from results tables."""
    orgs = set()
    with get_db() as db:
        for table in ("annual_results", "agreements", "companies"):
            rows = db.execute(f"SELECT DISTINCT organisation FROM {table}").fetchall()
            orgs.update(r["organisation"] for r in rows)
    return sorted(orgs)


def compute_progress(org, years, sector=None):
    """Return dict with completion counts for an organisation."""
    annual_total = 0
    annual_filled = 0
    for y in years:
        data = get_annual_results(org, [y], sector=sector)
        for var_key in ("bargaining_climate", "collective_bargaining_coverage", "avg_pay_increase",
                        "one_off_lump_sum", "multi_employer_agreements", "single_employer_agreements",
                        "avg_monthly_wage", "inflation_rate", "sectoral_productivity", "outcome_reporting"):
            annual_total += 1
            cell = data.get((var_key, y), {})
            if cell.get("value", "") or cell.get("qual_value", "") or cell.get("comment", ""):
                annual_filled += 1

    agreements_filled = 0
    for y in years:
        rows = get_agreements(org, y, sector=sector)
        for r in rows:
            if any(r.get(k) for k in ("company_name", "wage_increase", "workers_affected", "one_off_lump_sum", "other_changes", "comment")):
                agreements_filled += 1

    companies_filled = 0
    for y in years:
        rows = get_companies(org, y, sector=sector)
        for r in rows:
            if any(r.get(k) for k in ("company_name", "workers", "agreement", "union_density", "comment")):
                companies_filled += 1

    return {
        "annual": {"filled": annual_filled, "total": annual_total},
        "agreements": {"filled": agreements_filled, "total": agreements_filled},
        "companies": {"filled": companies_filled, "total": companies_filled},
    }


def _export_xlsx(organisation=None, sector=None):
    """Write tables to a single XLSX workbook. If organisation and sector are given, filter by both."""
    from openpyxl import Workbook
    wb = Workbook()
    default = wb.active
    sheet_count = 0
    with get_db() as db:
        for table in ("annual_results", "agreements", "companies"):
            if organisation and sector and sector.lower() != "all":
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE organisation = ? AND sector = ? ORDER BY organisation, year",
                    (organisation, sector)
                ).fetchall()
            elif organisation:
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE organisation = ? ORDER BY organisation, year",
                    (organisation,)
                ).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {table} ORDER BY organisation, sector, year").fetchall()
            if not rows:
                continue
            ws = wb.create_sheet(title=table)
            sheet_count += 1
            headers = list(rows[0].keys())
            ws.append(headers)
            for r in rows:
                ws.append([r[h] for h in headers])
    if sheet_count == 0:
        return b""
    wb.remove(default)
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def org_file_prefix(org):
    """Safe filename prefix for an organisation (spaces → underscores)."""
    return org.replace(" ", "_") if org else ""


def org_sector_file_prefix(org, sector=""):
    """Safe filename prefix for an organisation and sector combination."""
    safe_org = (org or "").replace(" ", "_")
    if sector and sector.lower() != "all":
        safe_sec = sector.replace(" ", "_")
        return f"{safe_org}_{safe_sec}"
    return safe_org


def export_all():
    """Write full database files and per-affiliate/sector CSVs and XLSX to EXPORT_DIR."""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    pairs_set = set()
    orgs_set = set()

    with get_db() as db:
        for table in ("annual_results", "agreements", "companies"):
            rows = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY organisation, sector, year").fetchall()]
            if rows:
                # 1. Full database CSV (all affiliates and sectors)
                all_path = os.path.join(EXPORT_DIR, f"{table}.csv")
                with open(all_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                
                # 2. Collect unique (org, sector) pairs
                for r in rows:
                    o = r.get("organisation", "").strip()
                    s = r.get("sector", "").strip()
                    if o:
                        orgs_set.add(o)
                        pairs_set.add((o, s))

                # 3. Per-(org, sector) and per-org CSV files
                for org, sec in pairs_set:
                    prefix = org_sector_file_prefix(org, sec)
                    filtered_rows = [
                        r for r in rows
                        if r.get("organisation") == org and (not sec or r.get("sector", "") == sec or sec.lower() == "all")
                    ]
                    if filtered_rows:
                        pair_path = os.path.join(EXPORT_DIR, f"{prefix}_{table}.csv")
                        with open(pair_path, "w", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=list(filtered_rows[0].keys()))
                            writer.writeheader()
                            writer.writerows(filtered_rows)

    # 4. Full database XLSX (all affiliates and sectors)
    full_xlsx = _export_xlsx()
    if full_xlsx:
        with open(os.path.join(EXPORT_DIR, "unicompass.xlsx"), "wb") as f:
            f.write(full_xlsx)

    # 5. Per-(org, sector) and per-org XLSX workbooks
    for org, sec in pairs_set:
        prefix = org_sector_file_prefix(org, sec)
        pair_xlsx = _export_xlsx(organisation=org, sector=sec)
        if pair_xlsx:
            with open(os.path.join(EXPORT_DIR, f"{prefix}_unicompass.xlsx"), "wb") as f:
                f.write(pair_xlsx)

    for org in orgs_set:
        safe = org_file_prefix(org)
        org_xlsx = _export_xlsx(organisation=org)
        if org_xlsx:
            with open(os.path.join(EXPORT_DIR, f"{safe}_unicompass.xlsx"), "wb") as f:
                f.write(org_xlsx)

    return sorted(orgs_set)


def generate_export_zip(org_filter=None, sector_filter=None, is_admin=False):
    """Create a zip archive of export files. For admin without filter, exports full DB tables."""
    export_all()
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        if is_admin and not org_filter:
            # Full database dump for administrator
            for fname in ("unicompass.xlsx", "annual_results.csv", "agreements.csv", "companies.csv"):
                fpath = os.path.join(EXPORT_DIR, fname)
                if os.path.isfile(fpath) and fname not in zf.namelist():
                    zf.write(fpath, arcname=fname)
        else:
            prefix = org_sector_file_prefix(org_filter, sector_filter) + "_" if org_filter else None
            for fname in os.listdir(EXPORT_DIR):
                if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
                    continue
                if prefix and not fname.startswith(prefix):
                    continue
                fpath = os.path.join(EXPORT_DIR, fname)
                if os.path.isfile(fpath) and fname not in zf.namelist():
                    zf.write(fpath, arcname=fname)
    return buf.getvalue()


def backup_database():
    """Create a consistent copy of the SQLite database using SQLite's backup API and return its bytes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        dest = sqlite3.connect(tmp_path)
        with get_db() as src:
            src.backup(dest)
        dest.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
        return data
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

