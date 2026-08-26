"""
Database models for UNI Compass.
Uses SQLite via the sqlite3 standard library — no ORM required.
"""

import csv
import sqlite3
import os
import datetime
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
                email       TEXT    NOT NULL UNIQUE,
                pincode     TEXT    NOT NULL,
                organisation TEXT   NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                country     TEXT    DEFAULT '',
                sector      TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                email    TEXT    NOT NULL,
                attempted_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS login_bans (
                email    TEXT    PRIMARY KEY,
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
                           ("annual_results", "row_mode")):
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT ''")
            except Exception:
                pass

        # Migrate existing annual results one_off_bonus variable to one_off_lump_sum
        try:
            db.execute("UPDATE annual_results SET variable = 'one_off_lump_sum' WHERE variable = 'one_off_bonus'")
        except Exception:
            pass

        # Migrate old agreements and companies one_off_bonus values to one_off_lump_sum
        try:
            db.execute("UPDATE agreements SET one_off_lump_sum = one_off_bonus WHERE (one_off_lump_sum = '' OR one_off_lump_sum IS NULL) AND one_off_bonus != '' AND one_off_bonus IS NOT NULL")
            db.execute("UPDATE companies SET one_off_lump_sum = one_off_bonus WHERE (one_off_lump_sum = '' OR one_off_lump_sum IS NULL) AND one_off_bonus != '' AND one_off_bonus IS NOT NULL")
        except Exception:
            pass


def check_login_rate_limit(email):
    """Return True if the email is allowed to attempt login, False if rate-limited."""
    email = email.strip().lower()
    with get_db() as db:
        ban = db.execute("SELECT banned_until FROM login_bans WHERE email = ?", (email,)).fetchone()
        if ban:
            from datetime import datetime
            banned_until = datetime.fromisoformat(ban["banned_until"])
            if datetime.now() < banned_until:
                return False
            db.execute("DELETE FROM login_bans WHERE email = ?", (email,))
    with get_db() as db:
        recent = db.execute(
            "SELECT COUNT(*) as cnt FROM login_attempts "
            "WHERE email = ? AND attempted_at >= datetime('now', '-15 minutes')",
            (email,)
        ).fetchone()
        if recent and recent["cnt"] >= 5:
            from datetime import datetime, timedelta
            banned_until = (datetime.now() + timedelta(minutes=30)).isoformat()
            db.execute("INSERT OR REPLACE INTO login_bans (email, banned_until) VALUES (?, ?)",
                       (email, banned_until))
            db.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
            return False
    return True


def record_failed_login(email):
    """Record a failed login attempt for this email."""
    email = email.strip().lower()
    with get_db() as db:
        db.execute("INSERT INTO login_attempts (email) VALUES (?)", (email,))


def clear_login_attempts(email):
    """Clear all failed login attempts for this email (on success)."""
    email = email.strip().lower()
    with get_db() as db:
        db.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
        db.execute("DELETE FROM login_bans WHERE email = ?", (email,))


def get_annual_results(organisation, years):
    """Return a dict: (variable, year) -> {'value': ..., 'comment': ..., 'qual_value': ..., 'row_mode': ...}"""
    qual_words = {
        "100%", "very high", "high", "average", "low", "very low", "0%",
        "strong increase", "minor increase", "stable", "minor decrease", "strong decrease",
        "very difficult", "difficult", "neutral", "favourable", "very favourable"
    }
    placeholders = ",".join("?" for _ in years)
    with get_db() as db:
        rows = db.execute(
            f"SELECT variable, year, value, comment, qual_value, row_mode FROM annual_results "
            "WHERE organisation = ? AND year IN ({})"
            .format(placeholders),
            (organisation, *years)
        ).fetchall()
    result = {}
    for r in rows:
        val = r["value"] or ""
        qual = r["qual_value"] or ""
        comment = r["comment"] or ""
        row_mode = r["row_mode"] or ""
        
        # Backward compatibility check:
        # If val is a qual word and qual is empty, then migrate it locally:
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


def save_annual_results(organisation, data):
    """data is list of dicts: [{'variable': ..., 'year': ..., 'value': ..., 'qual_value': ..., 'row_mode': ..., 'comment': ...}]"""
    with get_db() as db:
        for d in data:
            db.execute(
                "INSERT INTO annual_results (organisation, variable, year, value, comment, qual_value, row_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(organisation, variable, year) DO UPDATE SET "
                "value=excluded.value, comment=excluded.comment, qual_value=excluded.qual_value, row_mode=excluded.row_mode",
                (organisation, d["variable"], d["year"], d.get("value", ""), d.get("comment", ""),
                 d.get("qual_value", ""), d.get("row_mode", ""))
            )


def get_agreements(organisation, year):
    """Return rows sorted by workers_affected descending."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM agreements WHERE organisation = ? AND year = ? "
            "ORDER BY workers_affected DESC, row_idx",
            (organisation, year)
        ).fetchall()
    return [dict(r) for r in rows]


def save_agreements(organisation, year, rows):
    """Replace all agreement rows for this org+year."""
    with get_db() as db:
        db.execute("DELETE FROM agreements WHERE organisation = ? AND year = ?", (organisation, year))
        for i, r in enumerate(rows):
            db.execute(
                "INSERT INTO agreements (organisation, year, row_idx, company_name, wage_increase, workers_affected, level, date_of_agreement, one_off_lump_sum, bargaining_climate, comment, duration, working_time_change, other_changes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (organisation, year, i, r.get("company_name", ""), r.get("wage_increase", ""),
                 r.get("workers_affected", 0), r.get("level", ""), r.get("date_of_agreement", ""),
                 r.get("one_off_lump_sum", ""), r.get("bargaining_climate", ""), r.get("comment", ""),
                 r.get("duration", ""), r.get("working_time_change", ""), r.get("other_changes", ""))
            )


def get_companies(organisation, year):
    """Return rows sorted by workers descending."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM companies WHERE organisation = ? AND year = ? "
            "ORDER BY workers DESC, row_idx",
            (organisation, year)
        ).fetchall()
    return [dict(r) for r in rows]


def save_companies(organisation, year, rows):
    """Replace all company rows for this org+year."""
    with get_db() as db:
        db.execute("DELETE FROM companies WHERE organisation = ? AND year = ?", (organisation, year))
        for i, r in enumerate(rows):
            db.execute(
                "INSERT INTO companies (organisation, year, row_idx, company_name, agreement, workers, number_of_unions, union_members, ewc_presence, mnc, bargaining_climate, one_off_lump_sum, comment, union_density, worker_representation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (organisation, year, i, r.get("company_name", ""), r.get("agreement", ""),
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


def compute_progress(org, years):
    """Return dict with completion counts for an organisation."""
    annual_total = 0
    annual_filled = 0
    for y in years:
        data = get_annual_results(org, [y])
        for var_key in ("bargaining_climate", "collective_bargaining_coverage", "avg_wage_increase",
                        "one_off_lump_sum", "multi_employer_agreements", "single_employer_agreements",
                        "avg_monthly_wage", "inflation_rate", "sectoral_productivity", "outcome_reporting"):
            annual_total += 1
            cell = data.get((var_key, y), {})
            if var_key == "outcome_reporting":
                if cell.get("comment", ""):
                    annual_filled += 1
            else:
                if cell.get("value", ""):
                    annual_filled += 1

    agreements_total = 0
    agreements_filled = 0
    for y in years:
        rows = get_agreements(org, y)
        agreements_total += 10
        for r in rows:
            if r.get("company_name", ""):
                agreements_filled += 1

    companies_total = 0
    companies_filled = 0
    for y in years:
        rows = get_companies(org, y)
        companies_total += 10
        for r in rows:
            if r.get("company_name", ""):
                companies_filled += 1

    return {
        "annual": {"filled": annual_filled, "total": annual_total},
        "agreements": {"filled": agreements_filled, "total": agreements_total},
        "companies": {"filled": companies_filled, "total": companies_total},
    }


def _export_xlsx(organisation=None):
    """Write tables to a single XLSX workbook. If organisation given, only that org's data."""
    from openpyxl import Workbook
    wb = Workbook()
    default = wb.active
    sheet_count = 0
    with get_db() as db:
        for table in ("annual_results", "agreements", "companies"):
            if organisation:
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE organisation = ? ORDER BY organisation, year",
                    (organisation,)
                ).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {table} ORDER BY organisation, year").fetchall()
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
    return org.replace(" ", "_")


def export_all():
    """Write per-affiliate CSVs and a combined XLSX to EXPORT_DIR. No date suffixes."""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    orgs_set = set()

    with get_db() as db:
        for table in ("annual_results", "agreements", "companies"):
            rows = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY organisation, year").fetchall()]
            if rows:
                all_path = os.path.join(EXPORT_DIR, f"{table}.csv")
                with open(all_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
                orgs_in_table = set(r.get("organisation", "") for r in rows)
                orgs_set.update(orgs_in_table)
                for org in sorted(orgs_in_table):
                    org_rows = [r for r in rows if r.get("organisation") == org]
                    safe_org = org.replace(" ", "_")
                    org_path = os.path.join(EXPORT_DIR, f"{safe_org}_{table}.csv")
                    with open(org_path, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                        writer.writeheader()
                        writer.writerows(org_rows)

    xlsx_bytes = _export_xlsx()
    if xlsx_bytes:
        xlsx_path = os.path.join(EXPORT_DIR, "unicompass.xlsx")
        with open(xlsx_path, "wb") as f:
            f.write(xlsx_bytes)

    for org in sorted(orgs_set):
        safe = org_file_prefix(org)
        org_xlsx = _export_xlsx(organisation=org)
        if org_xlsx:
            org_xlsx_path = os.path.join(EXPORT_DIR, f"{safe}_unicompass.xlsx")
            with open(org_xlsx_path, "wb") as f:
                f.write(org_xlsx)

    return sorted(orgs_set)


def generate_export_zip(org_filter=None):
    """Create a zip archive of export files. If org_filter is a string prefix, only include files starting with that prefix."""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        for fname in os.listdir(EXPORT_DIR):
            if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
                continue
            if org_filter is not None and not fname.startswith(org_filter):
                continue
            fpath = os.path.join(EXPORT_DIR, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, arcname=fname)
    return buf.getvalue()
