"""
UNI Compass — Flask application for tracking collective bargaining progress.
"""

from functools import wraps
from io import BytesIO
import datetime
import os
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory, abort, jsonify

from auth import (
    authenticate,
    get_user,
    get_all_organisations as get_all_orgs_csv,
    get_all_org_sector_combinations,
    get_user_scope,
    resolve_cascading_context,
    get_affiliate_user_for_context,
    _ensure_users_csv,
    _ensure_countries_csv,
)
from config import SECRET_KEY, DATA_DIR, EXPORT_DIR, ENABLE_SURVEY, ENABLE_SAVE_BUTTON, LOGOS_DIR, ALLOWED_SECTORS
from models import (
    init_db,
    get_annual_results,
    save_annual_results,
    get_agreements,
    save_agreements,
    get_companies,
    save_companies,
    compute_all_progress_batch,
    export_all,
    generate_export_zip,
    generate_all_exports_zip,
    backup_database,
    backup_database_csv_zip,
    backup_database_xlsx,
    generate_pdf_report,
    org_file_prefix,
    org_sector_file_prefix,
    parse_optional_number,
    parse_optional_int,
    check_login_rate_limit,
    record_failed_login,
    clear_login_attempts,
    get_survey,
    get_user_survey_response,
    save_survey_response,
    get_survey_feedback_summary,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY


def create_app():
    # Ensure active data volume directories exist
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    # Seed baseline users.csv and countries.csv into volume if fresh mount
    _ensure_users_csv()
    _ensure_countries_csv()
    # Initialize database tables
    init_db()
    return app


@app.context_processor
def inject_globals():
    if "user" not in session:
        return {}

    current_user = session.get("user", "")
    is_admin = bool(session.get("is_admin"))
    home_org = session.get("home_org") or session.get("organisation", "")
    if "home_org" not in session and home_org:
        session["home_org"] = home_org
    scope = get_user_scope(current_user, is_admin=is_admin)

    req_org = request.args.get("org") or request.form.get("org")
    req_country = request.args.get("country")
    req_sector = request.args.get("sector")

    cur_country = req_country or session.get("view_country") or session.get("country")
    cur_sector = req_sector or session.get("sector")
    cur_org = req_org or (session.get("view_org") if is_admin else session.get("organisation"))

    resolved = resolve_cascading_context(
        scope,
        country=cur_country,
        sector=cur_sector,
        org=cur_org,
        is_admin=is_admin,
        home_org=home_org,
    )

    session["sector"] = resolved["active_sector"]
    session["view_country"] = resolved["active_country"]
    session["country"] = resolved["active_country"]
    session["view_org"] = resolved["active_org"]
    if not is_admin:
        session["organisation"] = resolved["active_org"]

    if is_admin:
        view_org = resolved["active_org"]
        if view_org and view_org != "None" and view_org != home_org:
            affiliate_user = get_affiliate_user_for_context(
                view_org,
                resolved["active_country"],
                resolved["active_sector"]
            )
            act_as = session.get("act_as_user")
            active_user = act_as if act_as in (affiliate_user, current_user) else affiliate_user
            available_users = [affiliate_user, current_user] if affiliate_user and affiliate_user != current_user else [current_user]
            user_button_label = active_user
        else:
            active_user = current_user
            available_users = [current_user]
            user_button_label = f"{current_user} (admin)"
    else:
        active_user = current_user
        available_users = [current_user]
        user_button_label = current_user

    # Prepare user dropdown items and active user label with (admin) suffix
    available_user_items = []
    for u in available_users:
        u_info = get_user(u)
        u_is_adm = bool(u_info.get("is_admin")) if u_info else (u == current_user and is_admin)
        if u_is_adm:
            available_user_items.append({"username": u, "label": f"{u} (admin)"})
        else:
            available_user_items.append({"username": u, "label": u})

    active_user_info = get_user(active_user) if active_user else None
    act_is_adm = bool(active_user_info.get("is_admin")) if active_user_info else (active_user == current_user and is_admin)
    active_user_label = f"{active_user} (admin)" if act_is_adm else active_user

    return {
        "current_user": current_user,
        "active_user": active_user,
        "active_user_label": active_user_label,
        "available_users": available_users,
        "available_user_items": available_user_items,
        "has_multiple_users": len(available_users) > 1,
        "user_label": active_user_label,
        "current_sector": resolved["active_sector"],
        "current_country": resolved["active_country"],
        "current_org": resolved["active_org"],
        "session_org": home_org,
        "view_org": resolved["active_org"],
        "available_countries": resolved["available_countries"],
        "available_sectors": resolved["available_sectors"],
        "available_orgs": resolved["available_orgs"],
        "has_multiple_countries": len(resolved["available_countries"]) > 1,
        "has_multiple_sectors": len(resolved["available_sectors"]) > 1,
        "has_multiple_orgs": len(resolved["available_orgs"]) > 1,
        "is_admin": is_admin,
        "all_orgs": resolved["available_orgs"] if is_admin else None,
        "all_sectors": resolved["available_sectors"],
        "user_sectors": resolved["available_sectors"],
        "enable_survey": ENABLE_SURVEY,
        "enable_save_button": ENABLE_SAVE_BUTTON,
        "current_page": request.path,
    }


ANNUAL_VARIABLES = [
    ("bargaining_climate", "Bargaining climate", "climate"),
    ("collective_bargaining_coverage", "Collective bargaining coverage", "coverage"),
    ("avg_pay_increase", "Collectively agreed pay increase", "pay_increase"),
    ("lump_sum", "Lump sum", "lump_sum"),
    ("multi_employer_agreements", "Number of multi-employer agreements", "multi_agreements"),
    ("single_employer_agreements", "Number of single-employer agreements", "single_agreements"),
    ("avg_monthly_wage", "Average full-time monthly wage", "wage"),
    ("inflation_rate", "National inflation rate", "inflation"),
    ("sectoral_productivity", "Sectoral productivity evolution", "productivity"),
    ("outcome_reporting", "Qualitative reporting", "report"),
]

LIKERT_SCORES = {
    # 5: High / Favourable / Strong (Red)
    "very favourable": 5, "80-100%": 5, "80–100%": 5, "strong increase": 5, "very high": 5,
    "many agreements / many employers": 5, "many agreements/many employers": 5,
    "very common": 5, "strong inflation (+3%)": 5, "strong inflation": 5, "strong growth": 5,
    
    # 4: Rather favourable / High / Common / Increasing
    "rather favourable": 4, "60-79%": 4, "60–79%": 4, "moderate increase": 4, "high": 4,
    "many agreements / few employers": 4, "many agreements/few employers": 4,
    "common": 4, "minor inflation (+1/+3%)": 4, "minor inflation": 4, "moderate growth": 4,
    
    # 3: Neutral / Moderate / Rare / Stable
    "neutral": 3, "40-59%": 3, "40–59%": 3, "minimal increase": 3, "moderate": 3, "average": 3,
    "few agreements / many employers": 3, "few agreements/many employers": 3,
    "rare": 3, "normal": 3, "stable prices (+1/-1%)": 3, "stable prices": 3, "stable": 3, "no growth": 3,
    
    # 2: Rather unfavourable / Low / Very rare / Decreasing
    "rather unfavourable": 2, "20-39%": 2, "20–39%": 2, "no increase": 2, "low": 2,
    "few agreements / few employers": 2, "few agreements/few employers": 2,
    "very rare": 2, "minor deflation (-1/-3%)": 2, "minor deflation": 2, "moderate decline": 2,
    
    # 1: Very unfavourable / Low / None / Strongly decreasing (Blue)
    "very unfavourable": 1, "0-19%": 1, "0–19%": 1, "decrease": 1, "none": 1, "very low": 1,
    "no agreements": 1, "strong deflation (-3%)": 1, "strong deflation": 1, "strong decline": 1
}

LIKERT_COLORS = {
    5: "background-color:rgba(249, 115, 22, 0.40);",  # High / Orange
    4: "background-color:rgba(251, 146, 60, 0.30);",  # Rather favourable / Light Orange
    3: "background-color:rgba(148, 163, 184, 0.22);", # Neutral / Slate
    2: "background-color:rgba(56, 189, 248, 0.25);",  # Rather unfavourable / Sky Blue
    1: "background-color:rgba(37, 99, 235, 0.35);",   # Low / Blue
}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if not session.get("is_admin"):
            flash("Access denied.", "error")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated


def current_years():
    return list(range(2023, 2028))


def is_numeric(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _combine_tc(title, content):
    if not title and not content:
        return ""
    return title + "\n" + content


def compute_cell_colors(saved, variables, years):
    colormap = {}
    for var_key, *rest in variables:
        numeric_vals = []
        for y in years:
            cell = saved.get((var_key, y), {})
            v = cell.get("value")
            v_str = str(v).strip() if v is not None else ""
            qual = (cell.get("qual_value") or "").strip().lower()
            if qual and qual in LIKERT_SCORES:
                score = LIKERT_SCORES[qual]
                colormap[(var_key, y)] = LIKERT_COLORS[score]
            elif v_str and v_str.lower() in LIKERT_SCORES:
                score = LIKERT_SCORES[v_str.lower()]
                colormap[(var_key, y)] = LIKERT_COLORS[score]
            elif v_str and is_numeric(v_str):
                numeric_vals.append((y, float(v_str)))
        if len(numeric_vals) > 1:
            vals = [v for _, v in numeric_vals]
            mn, mx = min(vals), max(vals)
            rng = mx - mn
            if rng > 0:
                for y, v in numeric_vals:
                    if (var_key, y) not in colormap:
                        ratio = (v - mn) / rng
                        # Low (0.0): Blue (37, 99, 235) -> High (1.0): Orange (249, 115, 22)
                        red = int(37 + (249 - 37) * ratio)
                        green = int(99 + (115 - 99) * ratio)
                        blue = int(235 + (22 - 235) * ratio)
                        alpha = 0.15 + 0.25 * abs(ratio - 0.5) * 2
                        colormap[(var_key, y)] = f"background-color:rgba({red}, {green}, {blue}, {alpha:.2f});"
    return colormap


def active_years(saved, variables, years):
    active = set()
    for var_key, *rest in variables:
        for y in years:
            cell = saved.get((var_key, y), {})
            v = cell.get("value")
            has_val = v is not None and str(v).strip() != ""
            if has_val or cell.get("qual_value") or cell.get("comment"):
                active.add(y)
    return active


@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("user", "").strip()
        pincode = request.form.get("pincode", "").strip()

        if not username or not pincode:
            flash("Please enter both username and pincode.", "error")
            return render_template("login.html")

        # 1. Check if account is already locked (pincode is 9999)
        existing_user = get_user(username)
        if existing_user and existing_user.get("pincode", "").strip() == "9999":
            flash("This account has been locked. Please contact the administrator for a new pincode.", "error")
            return render_template("login.html")

        # 2. Pincode 9999 is always blocked
        if pincode == "9999":
            strikes = record_failed_login(username)
            if strikes >= 5:
                flash("You have exceeded 5 failed login attempts. Please contact the administrator for a new pincode.", "error")
            else:
                flash("Invalid username or pincode.", "error")
            return render_template("login.html")

        # 3. Check rate limit
        if not check_login_rate_limit(username):
            flash("Too many failed attempts. Please contact the administrator for a new pincode.", "error")
            return render_template("login.html")

        # 4. Attempt authentication
        user = authenticate(username, pincode)
        if user:
            clear_login_attempts(username)
            session.clear()
            session["user"] = user["user"]
            session["organisation"] = user["organisation"]
            session["home_org"] = user["organisation"]
            session["country"] = user.get("country", "")
            session["is_admin"] = user["is_admin"]
            scope = get_user_scope(user, is_admin=user["is_admin"])
            resolved = resolve_cascading_context(
                scope,
                country=user.get("country", ""),
                sector=user.get("sector") or (user.get("sectors", [None])[0] if user.get("sectors") else None),
                org=user.get("organisation", ""),
                is_admin=user["is_admin"],
                home_org=user["organisation"],
            )
            session["sector"] = resolved["active_sector"]
            session["view_org"] = resolved["active_org"]
            session["view_country"] = resolved["active_country"]
            session["country"] = resolved["active_country"]
            flash(f"Welcome, {user['user']}!")
            return redirect(url_for("home"))

        # 5. Failed attempt: record strike
        strikes = record_failed_login(username)
        if strikes >= 5:
            flash("You have exceeded 5 failed login attempts. Please contact the administrator for a new pincode.", "error")
        else:
            flash("Invalid username or pincode.", "error")

    return render_template("login.html")


@app.route("/set-context")
@login_required
def set_context():
    req_country = request.args.get("country", "").strip()
    req_sector = request.args.get("sector", "").strip()
    req_org = request.args.get("org", "").strip()
    req_user = request.args.get("user", "").strip()
    next_url = request.args.get("next") or request.referrer or url_for("home")

    if "/set-context" in next_url:
        next_url = url_for("home")

    is_admin = bool(session.get("is_admin"))
    home_org = session.get("home_org") or session.get("organisation", "")
    if "home_org" not in session and home_org:
        session["home_org"] = home_org
    scope = get_user_scope(session.get("user"), is_admin=is_admin)

    cur_country = session.get("view_country") or session.get("country")
    cur_sector = session.get("sector")
    cur_org = session.get("view_org") if is_admin else session.get("organisation")

    current_username = session.get("user", "")
    if req_user and is_admin and req_user.strip().lower() == current_username.strip().lower():
        # Admin selects himself to act: identification goes back to his defaults in users.csv!
        user_info = get_user(current_username)
        session["act_as_user"] = None
        default_org = user_info.get("organisation", "") if user_info else home_org
        default_country = user_info.get("country", "Europe") if user_info else "Europe"
        default_sector = user_info.get("sector", "UNI") if user_info else "UNI"
        resolved = resolve_cascading_context(
            scope,
            country=default_country,
            sector=default_sector,
            org=default_org,
            is_admin=True,
            home_org=default_org,
        )
        session["sector"] = resolved["active_sector"]
        session["view_country"] = resolved["active_country"]
        session["country"] = resolved["active_country"]
        session["view_org"] = resolved["active_org"]
        session["org"] = resolved["active_org"]
        session["organisation"] = resolved["active_org"]
    elif req_org:
        session["act_as_user"] = None
        resolved = resolve_cascading_context(scope, org=req_org, is_admin=is_admin, home_org=home_org)
        session["sector"] = resolved["active_sector"]
        session["view_country"] = resolved["active_country"]
        session["country"] = resolved["active_country"]
        session["view_org"] = resolved["active_org"]
        session["org"] = resolved["active_org"]
        if not is_admin:
            session["organisation"] = resolved["active_org"]
    elif req_country:
        resolved = resolve_cascading_context(scope, country=req_country, sector=cur_sector, is_admin=is_admin, home_org=home_org)
        session["sector"] = resolved["active_sector"]
        session["view_country"] = resolved["active_country"]
        session["country"] = resolved["active_country"]
        session["view_org"] = resolved["active_org"]
        session["org"] = resolved["active_org"]
        if not is_admin:
            session["organisation"] = resolved["active_org"]
    elif req_sector:
        resolved = resolve_cascading_context(scope, country=cur_country, sector=req_sector, is_admin=is_admin, home_org=home_org)
        session["sector"] = resolved["active_sector"]
        session["view_country"] = resolved["active_country"]
        session["country"] = resolved["active_country"]
        session["view_org"] = resolved["active_org"]
        session["org"] = resolved["active_org"]
        if not is_admin:
            session["organisation"] = resolved["active_org"]
    else:
        if req_user and is_admin:
            session["act_as_user"] = req_user
        resolved = resolve_cascading_context(scope, country=cur_country, sector=cur_sector, org=cur_org, is_admin=is_admin, home_org=home_org)
        session["sector"] = resolved["active_sector"]
        session["view_country"] = resolved["active_country"]
        session["country"] = resolved["active_country"]
        session["view_org"] = resolved["active_org"]
        session["org"] = resolved["active_org"]
        if not is_admin:
            session["organisation"] = resolved["active_org"]

    # Strip stale query params (org, country, sector, user) from next_url if present
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(next_url)
    if parsed.query:
        qs = parse_qs(parsed.query)
        qs.pop("org", None)
        qs.pop("country", None)
        qs.pop("sector", None)
        qs.pop("user", None)
        new_query = urlencode(qs, doseq=True)
        next_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    return redirect(next_url)


@app.route("/set-sector")
@login_required
def set_sector():
    sector = request.args.get("sector", "").strip()
    next_url = request.args.get("next") or url_for("home")
    return redirect(url_for("set_context", sector=sector, next=next_url))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    years = current_years()
    if session.get("is_admin"):
        req_org = request.args.get("org") or session.get("view_org") or session.get("organisation", "")
        session["view_org"] = req_org
        org = req_org
        if request.args.get("sector") and (request.args.get("sector").strip() in ALLOWED_SECTORS or request.args.get("sector").strip() == "UNI"):
            session["sector"] = request.args.get("sector").strip()
    else:
        org = session.get("organisation", "")
    sector = session.get("sector", "")
    if sector not in ALLOWED_SECTORS and sector != "UNI":
        sector = "UNI" if session.get("is_admin") else "Commerce"
        session["sector"] = sector
    per_year = {}
    for y in years:
        ann_saved = get_annual_results(org, [y], sector=sector)
        annual_filled = 0
        for item in ANNUAL_VARIABLES:
            var_key = item[0]
            cell = ann_saved.get((var_key, y), {})
            v = cell.get("value")
            has_val = v is not None and str(v).strip() != ""
            if has_val or cell.get("qual_value") or cell.get("comment"):
                annual_filled += 1
        annual_total = len(ANNUAL_VARIABLES)

        ag_rows = get_agreements(org, y, sector=sector)
        ag_filled = sum(1 for r in ag_rows if any(r.get(k) is not None and str(r.get(k)).strip() != "" for k in ("company_name", "wage_increase", "workers_affected", "lump_sum", "other_changes", "comment")))

        co_rows = get_companies(org, y, sector=sector)
        co_filled = sum(1 for r in co_rows if any(r.get(k) is not None and str(r.get(k)).strip() != "" for k in ("company_name", "workers", "agreement", "union_density", "comment")))

        per_year[y] = {
            "annual_filled": annual_filled, "annual_total": annual_total,
            "ag_filled": ag_filled,
            "co_filled": co_filled,
        }
    return render_template("home.html", progress_years=per_year, view_org=org)


@app.route("/admin")
@login_required
@admin_required
def admin():
    years = current_years()
    year_param = request.args.get("year")
    if year_param is None:
        selected_year = 2026 if 2026 in years else years[-1]
    elif year_param == "" or year_param.lower() in ("all", "none"):
        selected_year = None
    else:
        try:
            selected_year = int(year_param)
        except ValueError:
            selected_year = 2026 if 2026 in years else years[-1]

    combos = get_all_org_sector_combinations()
    progress_map = compute_all_progress_batch(combos, years, selected_year=selected_year)

    rows_data = []
    target_count = 1 if selected_year else len(years)
    for c in combos:
        org = c["organisation"]
        sec = c["sector"]
        p = progress_map.get((org, sec), {
            "annual": {"filled": 0, "total": 10 * target_count},
            "agreements": {"filled": 0, "total": 0},
            "companies": {"filled": 0, "total": 0},
        })
        rows_data.append({
            "organisation": org,
            "sector": sec,
            "country": c.get("country", ""),
            "users": c.get("users", []),
            "is_admin": c.get("is_admin", False),
            "progress": p,
        })

    return render_template(
        "admin.html",
        combos=rows_data,
        years=years,
        selected_year=selected_year,
    )


@app.route("/admin/download-db")
@login_required
@admin_required
def admin_download_db():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unicompass_{timestamp}.db"
    db_bytes = backup_database()
    return send_file(
        BytesIO(db_bytes),
        mimetype="application/x-sqlite3",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/admin/download-csv")
@login_required
@admin_required
def admin_download_csv():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unicompass_csv_{timestamp}.zip"
    zip_bytes = backup_database_csv_zip()
    return send_file(
        BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/admin/download-xlsx")
@login_required
@admin_required
def admin_download_xlsx():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unicompass_{timestamp}.xlsx"
    xlsx_bytes = backup_database_xlsx()
    return send_file(
        BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/admin/download-zip")
@login_required
@admin_required
def admin_download_zip():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unicompass_all_tables_{timestamp}.zip"
    zip_bytes = generate_all_exports_zip()
    return send_file(
        BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/annual-results", methods=["GET", "POST"])
@login_required
def annual_results():
    org = session.get("organisation", "")
    if session.get("is_admin"):
        req_org = request.args.get("org") or request.form.get("org") or session.get("view_org") or org
        if req_org and req_org != "None":
            org = req_org
            session["view_org"] = req_org
        else:
            org = session.get("organisation", "") or "HIVA KU Leuven"
            session["view_org"] = org
        if request.args.get("sector") and (request.args.get("sector").strip() in ALLOWED_SECTORS or request.args.get("sector").strip() == "UNI"):
            session["sector"] = request.args.get("sector").strip()
    if not org or org == "None":
        org = session.get("organisation", "") or "HIVA KU Leuven"
    sector = session.get("sector", "")
    if sector not in ALLOWED_SECTORS and sector != "UNI":
        sector = "Commerce"
        session["sector"] = sector
    years = current_years()
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        data = []
        for item in ANNUAL_VARIABLES:
            var_key = item[0]
            var_type = item[2]
            row_mode = request.form.get(f"row_mode_{var_key}", "num").strip()
            for y in years:
                val = request.form.get(f"value_{var_key}_{y}", "").strip()
                qual = request.form.get(f"qual_{var_key}_{y}", "").strip()
                if var_type == "report":
                    rep_title = request.form.get(f"title_{var_key}_{y}", "").strip()
                    rep_content = request.form.get(f"content_{var_key}_{y}", "").strip()
                    if rep_title or rep_content:
                        cmt = _combine_tc(rep_title, rep_content)
                    else:
                        cmt = request.form.get(f"comment_{var_key}_{y}", "").strip()
                    if cmt:
                        words = cmt.strip().split()
                        if len(words) > 80:
                            cmt = " ".join(words[:80])
                else:
                    cmt = request.form.get(f"comment_{var_key}_{y}", "").strip()
                if var_type in ("coverage", "pay_increase", "lump_sum", "multi_agreements", "single_agreements", "wage", "inflation", "productivity"):
                    if val != "":
                        num = parse_optional_number(val, max_decimals=2)
                        val = str(num) if num is not None else ""
                if (val is not None and str(val).strip() != "") or qual or cmt:
                    data.append({
                        "variable": var_key,
                        "year": y,
                        "value": val,
                        "qual_value": qual,
                        "row_mode": row_mode,
                        "comment": cmt,
                        "sector": sector,
                    })
        save_annual_results(org, data, sector=sector)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "ok", "message": f"Annual results saved for {org}."})
        if session.get("is_admin") and org != session.get("organisation"):
            return redirect(url_for("annual_results", org=org))
        return redirect(url_for("annual_results"))

    saved = get_annual_results(org, sector=sector)
    colormap = compute_cell_colors(saved, ANNUAL_VARIABLES, years)
    active = active_years(saved, ANNUAL_VARIABLES, years)
    default_year = 2026 if 2026 in years else years[-1]
    row_modes = {item[0]: saved.get((item[0], default_year), {}).get("row_mode", "num") for item in ANNUAL_VARIABLES}

    return render_template(
        "annual_results.html",
        years=years,
        variables=ANNUAL_VARIABLES,
        saved=saved,
        colormap=colormap,
        active=active,
        view_org=org,
        row_modes=row_modes,
        orgs=orgs,
    )


@app.route("/major-agreements", methods=["GET", "POST"])
@login_required
def agreements():
    org = session.get("organisation", "")
    if session.get("is_admin"):
        req_org = request.args.get("org") or request.form.get("org") or session.get("view_org") or org
        if req_org and req_org != "None":
            org = req_org
            session["view_org"] = req_org
        else:
            org = session.get("organisation", "") or "HIVA KU Leuven"
            session["view_org"] = org
        if request.args.get("sector") and (request.args.get("sector").strip() in ALLOWED_SECTORS or request.args.get("sector").strip() == "UNI"):
            session["sector"] = request.args.get("sector").strip()
    if not org or org == "None":
        org = session.get("organisation", "") or "HIVA KU Leuven"
    sector = session.get("sector", "")
    if sector not in ALLOWED_SECTORS and sector != "UNI":
        sector = "Commerce"
        session["sector"] = sector
    years = current_years()
    default_year = 2026 if 2026 in years else years[-1]
    selected_year = request.args.get("year", type=int) or default_year
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        selected_year = int(request.form.get("year", default_year))
        rows = []
        for i in range(10):
            rows.append({
                "company_name": request.form.get(f"company_name_{i}", "").strip(),
                "wage_increase": parse_optional_number(request.form.get(f"wage_increase_{i}")),
                "workers_affected": parse_optional_int(request.form.get(f"workers_affected_{i}")),
                "level": request.form.get(f"level_{i}", "").strip(),
                "date_of_agreement": request.form.get(f"date_of_agreement_{i}", "").strip(),
                "lump_sum": parse_optional_number(request.form.get(f"lump_sum_{i}")),
                "bargaining_climate": request.form.get(f"bargaining_climate_{i}", "").strip(),
                "comment": _combine_tc(request.form.get(f"comment_title_{i}", "").strip(),
                                       request.form.get(f"comment_content_{i}", "").strip()),
                "duration": request.form.get(f"duration_{i}", "").strip(),
                "working_time_change": request.form.get(f"working_time_change_{i}", "").strip(),
                "other_changes": _combine_tc(request.form.get(f"other_changes_title_{i}", "").strip(),
                                             request.form.get(f"other_changes_content_{i}", "").strip()),
                "sector": sector,
            })
        save_agreements(org, selected_year, rows, sector=sector)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "ok", "message": f"Major agreements saved for {org}."})
        if session.get("is_admin") and org != session.get("organisation"):
            return redirect(url_for("agreements", year=selected_year, org=org))
        return redirect(url_for("agreements", year=selected_year))

    saved = get_agreements(org, selected_year, sector=sector)
    return render_template("agreements.html", years=years, selected_year=selected_year, saved=saved, view_org=org, orgs=orgs)


@app.route("/major-companies", methods=["GET", "POST"])
@login_required
def companies():
    org = session.get("organisation", "")
    if session.get("is_admin"):
        req_org = request.args.get("org") or request.form.get("org") or session.get("view_org") or org
        if req_org and req_org != "None":
            org = req_org
            session["view_org"] = req_org
        else:
            org = session.get("organisation", "") or "HIVA KU Leuven"
            session["view_org"] = org
        if request.args.get("sector") and (request.args.get("sector").strip() in ALLOWED_SECTORS or request.args.get("sector").strip() == "UNI"):
            session["sector"] = request.args.get("sector").strip()
    if not org or org == "None":
        org = session.get("organisation", "") or "HIVA KU Leuven"
    sector = session.get("sector", "")
    if sector not in ALLOWED_SECTORS and sector != "UNI":
        sector = "Commerce"
        session["sector"] = sector
    years = current_years()
    default_year = 2026 if 2026 in years else years[-1]
    selected_year = request.args.get("year", type=int) or default_year
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        selected_year = int(request.form.get("year", default_year))
        rows = []
        for i in range(10):
            rows.append({
                "company_name": request.form.get(f"company_name_{i}", "").strip(),
                "agreement": request.form.get(f"agreement_{i}", "").strip(),
                "workers": parse_optional_int(request.form.get(f"workers_{i}")),
                "number_of_unions": parse_optional_int(request.form.get(f"number_of_unions_{i}")),
                "union_members": None,
                "ewc_presence": request.form.get(f"ewc_presence_{i}", "").strip(),
                "mnc": request.form.get(f"mnc_{i}", "").strip(),
                "bargaining_climate": "",
                "lump_sum": None,
                "comment": _combine_tc(request.form.get(f"comment_title_{i}", "").strip(),
                                       request.form.get(f"comment_content_{i}", "").strip()),
                "union_density": request.form.get(f"union_density_{i}", "").strip(),
                "worker_representation": request.form.get(f"worker_representation_{i}", "").strip(),
                "sector": sector,
            })
        save_companies(org, selected_year, rows, sector=sector)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"status": "ok", "message": f"Major companies saved for {org}."})
        if session.get("is_admin") and org != session.get("organisation"):
            return redirect(url_for("companies", year=selected_year, org=org))
        return redirect(url_for("companies", year=selected_year))

    saved = get_companies(org, selected_year, sector=sector)
    return render_template("companies.html", years=years, selected_year=selected_year, saved=saved, view_org=org, orgs=orgs)


@app.route("/example")
def example():
    example_data = {
        "annual": [
            ("Bargaining climate", [("2024", "Neutral"), ("2025", "Rather favourable"), ("2026", "Rather favourable")]),
            ("Collective bargaining coverage (%)", [("2024", "78%"), ("2025", "81%"), ("2026", "83%")]),
            ("Collectively agreed pay increase (%)", [("2024", "3.2%"), ("2025", "4.0%"), ("2026", "3.8%")]),
            ("Lump sum (€)", [("2024", "500 €"), ("2025", "550 €"), ("2026", "600 €")]),
            ("Number of multi-employer agreements", [("2024", "12"), ("2025", "14"), ("2026", "15")]),
            ("Number of single-employer agreements", [("2024", "45"), ("2025", "42"), ("2026", "40")]),
            ("Average full-time monthly wage (€)", [("2024", "3200"), ("2025", "3350"), ("2026", "3480")]),
            ("National inflation rate (%)", [("2024", "2.8%"), ("2025", "3.1%"), ("2026", "2.5%")]),
            ("Sectoral productivity evolution (%)", [("2024", "1.5%"), ("2025", "1.8%"), ("2026", "1.6%")]),
            ("Qualitative reporting", [("2024", "Wage accord"), ("2025", "Pension framework"), ("2026", "Sector renewal")]),
        ],
        "agreements": [
            ("Megacorp GmbH", "Entire sector", "15-06-26", "2 years", 12000, "5.2%", "600 €", "Training & working time", "Major breakthrough"),
            ("IndustryServices AG", "Multi-employer", "01-04-26", "1 year", 8500, "4.5%", "500 €", "Health subsidy", "Stable bargaining"),
            ("LogiStar SE", "Single-employer", "20-07-26", "3-5 years", 5200, "3.8%", "400 €", "Overtime rules", "Tense negotiations"),
            ("CarePlus e.V.", "Single-employer", "10-05-26", "2 years", 3100, "4.0%", "350 €", "Extra holidays", "Favourable outcome"),
        ],
        "companies": [
            ("MegaIndustry plc", 15000, "Yes", "Yes", 12, "75-89%", "Yes", "Yes", "Strong union presence"),
            ("ServiceFirst Ltd", 9200, "Yes", "Yes", 8, "50-74%", "Yes", "Yes", "Constructive dialogue"),
            ("TransLog GmbH", 7400, "Yes", "No", 1, "< 10%", "No", "No", "Challenging environment"),
            ("HealthCare Corp", 4100, "No", "Yes", 5, "25-49%", "Yes", "No", "Stable representation"),
        ],
    }
    return render_template("example.html", data=example_data)


@app.route("/export")
@login_required
def export():
    export_all()
    org = session.get("organisation", "")
    if session.get("is_admin"):
        org = request.args.get("org") or session.get("view_org") or org
        session["view_org"] = org
        if request.args.get("sector") and (request.args.get("sector").strip() in ALLOWED_SECTORS or request.args.get("sector").strip() == "UNI"):
            session["sector"] = request.args.get("sector").strip()
    sector = (session.get("sector") or "").strip()
    if sector not in ALLOWED_SECTORS and sector != "UNI":
        sector = "UNI" if session.get("is_admin") else "Commerce"
        session["sector"] = sector
    is_adm = session.get("is_admin", False)

    affiliate_files = []
    prefix = org_sector_file_prefix(org, sector) + "_"
    fallback_prefix = org_file_prefix(org) + "_"
    for fname in sorted(os.listdir(EXPORT_DIR), reverse=True):
        if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
            continue
        if fname in ("unicompass.xlsx", "unicompass_report.pdf", "annual_results.csv", "agreements.csv", "companies.csv", "survey_responses.csv"):
            continue
        if fname.startswith(prefix) or (sector and sector.lower() == "all" and fname.startswith(fallback_prefix)):
            fpath = os.path.join(EXPORT_DIR, fname)
            if os.path.isfile(fpath):
                affiliate_files.append({"name": fname, "size": f"{os.path.getsize(fpath) / 1024:.1f} KB"})

    return render_template(
        "export.html",
        files=affiliate_files,
        is_admin=is_adm,
        view_org=org,
        selected_org=org,
        selected_sector=sector,
    )


@app.route("/export/download/<path:filename>")
@login_required
def export_download(filename):
    safe_name = secure_filename(filename)
    if not (safe_name.endswith(".csv") or safe_name.endswith(".xlsx") or safe_name.endswith(".pdf")):
        abort(404)
    org = session.get("organisation", "")
    sector = (session.get("sector") or "").strip()
    if not session.get("is_admin"):
        prefix = org_sector_file_prefix(org, sector) + "_"
        fallback_prefix = org_file_prefix(org) + "_"
        if not (safe_name.startswith(prefix) or (sector and sector.lower() == "all" and safe_name.startswith(fallback_prefix))):
            abort(403)
    return send_from_directory(EXPORT_DIR, safe_name, as_attachment=True)


@app.route("/export/pdf")
@login_required
def export_pdf():
    is_adm = session.get("is_admin", False)
    if is_adm:
        org = request.args.get("org") or session.get("view_org") or session.get("organisation", "")
        sector = (request.args.get("sector") or session.get("sector") or "").strip()
    else:
        org = session.get("organisation", "")
        sector = (session.get("sector") or "").strip()
    
    pdf_bytes = generate_pdf_report(organisation=org, sector=sector, is_admin=is_adm)
    prefix = org_sector_file_prefix(org, sector) if (org and org != "UNI Europe") else "unicompass"
    filename = f"{prefix}_report.pdf" if prefix else "report.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/export/download-all")
@login_required
def export_download_all():
    org = session.get("organisation", "")
    if session.get("is_admin"):
        org = request.args.get("org") or session.get("view_org") or org
    sector = (session.get("sector") or "").strip()
    zip_bytes = generate_export_zip(org_filter=org, sector_filter=sector, is_admin=False)
    prefix = org_sector_file_prefix(org, sector)
    return send_file(
        BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{prefix}_tables.zip" if prefix else "unicompass_export.zip",
    )


@app.route("/survey", methods=["GET", "POST"])
@login_required
def survey():
    if not ENABLE_SURVEY:
        return redirect(url_for("home"))

    survey_id = request.args.get("id") or "2026"
    survey_def = get_survey(survey_id)
    if not survey_def:
        abort(404)

    is_admin = bool(session.get("is_admin"))
    current_user = session.get("user", "")
    home_org = session.get("home_org") or session.get("organisation", "")

    if is_admin:
        view_org = session.get("view_org")
        target_country = session.get("view_country") or session.get("country") or "Europe"
        target_sector = session.get("sector") or "UNI"
        if target_sector not in ALLOWED_SECTORS and target_sector != "UNI":
            target_sector = "UNI"
        if target_country == "None":
            target_country = "Europe"
        if target_sector == "None":
            target_sector = "UNI"

        if view_org and view_org != "None" and view_org != home_org:
            affiliate_user = get_affiliate_user_for_context(view_org, target_country, target_sector)
            target_user = session.get("act_as_user") or affiliate_user
            target_org = view_org
        elif view_org == "None":
            target_org = ""
            target_country = ""
            target_sector = ""
            target_user = current_user
        else:
            target_org = home_org
            target_user = current_user
    else:
        user_info = get_user(current_user) if current_user else None
        target_org = session.get("organisation", "")
        target_sector = session.get("sector", "Commerce")
        if target_sector not in ALLOWED_SECTORS and target_sector != "UNI":
            target_sector = "Commerce"
            session["sector"] = target_sector
        target_country = session.get("country") or (user_info.get("country") if user_info else "")
        target_user = current_user

    if request.method == "POST":
        q1 = request.form.get("q1_rating")
        q2 = request.form.get("q2_rating")
        q3 = request.form.get("q3_rating")
        q4 = request.form.get("q4_text", "")
        if "show_org" in request.form:
            hide_org = 0 if request.form.get("show_org") in ("1", "true", "on", "yes") else 1
        elif "hide_org" in request.form:
            hide_org = 1 if request.form.get("hide_org") in ("1", "true", "on", "yes") else 0
        else:
            hide_org = 1

        if "show_sector" in request.form:
            hide_sector = 0 if request.form.get("show_sector") in ("1", "true", "on", "yes") else 1
        elif "hide_sector" in request.form:
            hide_sector = 1 if request.form.get("hide_sector") in ("1", "true", "on", "yes") else 0
        else:
            hide_sector = 1

        if "show_country" in request.form:
            hide_country = 0 if request.form.get("show_country") in ("1", "true", "on", "yes") else 1
        elif "hide_country" in request.form:
            hide_country = 1 if request.form.get("hide_country") in ("1", "true", "on", "yes") else 0
        else:
            hide_country = 1

        if "show_user" in request.form:
            hide_user = 0 if request.form.get("show_user") in ("1", "true", "on", "yes") else 1
        elif "hide_user" in request.form:
            hide_user = 1 if request.form.get("hide_user") in ("1", "true", "on", "yes") else 0
        else:
            hide_user = 1

        save_survey_response(
            survey_id=survey_id,
            username=target_user,
            organisation=target_org,
            country=target_country,
            sector=target_sector,
            q1_rating=q1,
            q2_rating=q2,
            q3_rating=q3,
            q4_text=q4,
            hide_org=hide_org,
            hide_sector=hide_sector,
            hide_country=hide_country,
            hide_user=hide_user,
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            summary = get_survey_feedback_summary(survey_id)
            return jsonify({
                "status": "ok",
                "message": "Survey response saved.",
                "summary": summary,
            })

        return redirect(url_for("survey", id=survey_id))

    saved_resp = get_user_survey_response(survey_id, target_user)
    saved = {}
    if saved_resp:
        saved = dict(saved_resp)
        saved["show_org"] = 0 if str(saved_resp.get("hide_org", 0)).strip().lower() in ("1", "true") else 1
        saved["show_sector"] = 0 if str(saved_resp.get("hide_sector", 0)).strip().lower() in ("1", "true") else 1
        saved["show_country"] = 0 if str(saved_resp.get("hide_country", 0)).strip().lower() in ("1", "true") else 1
        saved["show_user"] = 0 if str(saved_resp.get("hide_user", 0)).strip().lower() in ("1", "true") else 1
    else:
        saved = {
            "show_org": 1,
            "show_sector": 1,
            "show_country": 1,
            "show_user": 1,
        }
    summary = get_survey_feedback_summary(survey_id)

    return render_template(
        "survey.html",
        survey=survey_def,
        saved=saved,
        summary=summary,
        user_country=target_country,
        user_sector=target_sector,
        user_org=target_org,
        target_user=target_user,
        enable_save_button=ENABLE_SAVE_BUTTON,
    )


@app.route("/logos/<path:filename>")
def serve_logo(filename):
    safe_name = secure_filename(filename)
    return send_from_directory(LOGOS_DIR, safe_name)
