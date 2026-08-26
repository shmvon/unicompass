"""
UNI Compass — Flask application for tracking collective bargaining progress.
"""

from functools import wraps
from io import BytesIO
import os
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory, abort

from auth import (
    authenticate,
    get_all_organisations as get_all_orgs_csv,
    get_all_sectors,
    get_all_org_sector_combinations,
)
from config import SECRET_KEY, EXPORT_DIR
from models import (
    init_db,
    get_annual_results,
    save_annual_results,
    get_agreements,
    save_agreements,
    get_companies,
    save_companies,
    get_all_organisations,
    compute_progress,
    export_all,
    generate_export_zip,
    org_file_prefix,
    org_sector_file_prefix,
    check_login_rate_limit,
    record_failed_login,
    clear_login_attempts,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY


def create_app():
    init_db()
    return app


@app.context_processor
def inject_globals():
    if "user" not in session:
        return {}
    current_user = session.get("user", "")
    current_sector = session.get("sector", "")
    user_sectors = session.get("user_sectors", [])
    view_org = request.args.get("org") or session.get("organisation", "")
    return {
        "current_user": current_user,
        "current_sector": current_sector,
        "user_sectors": user_sectors,
        "session_org": session.get("organisation", ""),
        "view_org": view_org,
        "is_admin": bool(session.get("is_admin")),
        "all_orgs": get_all_orgs_csv() if session.get("is_admin") else None,
        "all_sectors": get_all_sectors() if session.get("is_admin") else user_sectors,
        "current_page": request.path,
    }


ANNUAL_VARIABLES = [
    ("bargaining_climate", "Bargaining climate", "climate"),
    ("collective_bargaining_coverage", "Collective bargaining coverage (%)", "coverage"),
    ("avg_pay_increase", "Collectively agreed pay increase (%)", "pay_increase"),
    ("one_off_lump_sum", "Additional lump sum (€)", "lump_sum"),
    ("multi_employer_agreements", "Number of multi-employer agreements", "multi_agreements"),
    ("single_employer_agreements", "Number of single-employer agreements", "single_agreements"),
    ("avg_monthly_wage", "Average full-time monthly wage (€)", "wage"),
    ("inflation_rate", "National inflation rate (%)", "inflation"),
    ("sectoral_productivity", "Sectoral productivity evolution (%)", "productivity"),
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
    5: "background-color:rgba(250, 204, 21, 0.45);",  # High / Yellow
    4: "background-color:rgba(234, 179, 8, 0.30);",
    3: "background-color:rgba(132, 204, 22, 0.22);",   # Neutral / Yellow-Green
    2: "background-color:rgba(14, 165, 233, 0.25);",
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
            return redirect(url_for("dashboard"))
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


def safe_int(val, default=0):
    if not val:
        return default
    val_str = str(val).strip()
    digits = "".join(c for c in val_str if c.isdigit())
    if not digits:
        return default
    try:
        return int(digits)
    except ValueError:
        return default


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
            v = cell.get("value", "")
            qual = (cell.get("qual_value") or "").strip().lower()
            if qual and qual in LIKERT_SCORES:
                score = LIKERT_SCORES[qual]
                colormap[(var_key, y)] = LIKERT_COLORS[score]
            elif v and v.strip().lower() in LIKERT_SCORES:
                score = LIKERT_SCORES[v.strip().lower()]
                colormap[(var_key, y)] = LIKERT_COLORS[score]
            elif v and is_numeric(v):
                numeric_vals.append((y, float(v)))
        if len(numeric_vals) > 1:
            vals = [v for _, v in numeric_vals]
            mn, mx = min(vals), max(vals)
            rng = mx - mn
            if rng > 0:
                for y, v in numeric_vals:
                    if (var_key, y) not in colormap:
                        ratio = (v - mn) / rng
                        # Low (0.0): Blue (37, 99, 235) -> High (1.0): Yellow (250, 204, 21)
                        red = int(37 + (250 - 37) * ratio)
                        green = int(99 + (204 - 99) * ratio)
                        blue = int(235 + (21 - 235) * ratio)
                        alpha = 0.15 + 0.25 * abs(ratio - 0.5) * 2
                        colormap[(var_key, y)] = f"background-color:rgba({red}, {green}, {blue}, {alpha:.2f});"
    return colormap


def active_years(saved, variables, years):
    active = set()
    for var_key, *rest in variables:
        for y in years:
            cell = saved.get((var_key, y), {})
            if cell.get("value", "") or cell.get("qual_value", "") or cell.get("comment", ""):
                active.add(y)
    return active


@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("user", "").strip()
        pincode = request.form.get("pincode", "").strip()

        if not check_login_rate_limit(username):
            flash("Too many failed attempts. Please try again later.", "error")
            return render_template("login.html")

        user = authenticate(username, pincode)
        if user:
            clear_login_attempts(username)
            session["user"] = user["user"]
            session["organisation"] = user["organisation"]
            session["is_admin"] = user["is_admin"]
            user_sectors = user.get("sectors") or ["General"]
            session["user_sectors"] = user_sectors
            session["sector"] = user_sectors[0] if user_sectors else "General"
            flash(f"Welcome, {user['user']}!")
            return redirect(url_for("dashboard"))
        record_failed_login(username)
        flash("Invalid username or pincode.", "error")
    return render_template("login.html")


@app.route("/set-sector")
@login_required
def set_sector():
    sector = request.args.get("sector", "").strip()
    if sector:
        user_sectors = session.get("user_sectors", [])
        if session.get("is_admin") or sector in user_sectors:
            session["sector"] = sector
            flash(f"Active sector: {sector}")
    next_url = request.args.get("next") or url_for("dashboard")
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/dashboard")
@app.route("/home")
@login_required
def dashboard():
    years = current_years()
    org = session["organisation"]
    sector = session.get("sector", "")
    per_year = {}
    for y in years:
        annual = get_annual_results(org, [y], sector=sector)
        annual_filled = 0
        for item in ANNUAL_VARIABLES:
            vk = item[0]
            cell = annual.get((vk, y), {})
            if cell.get("value", "") or cell.get("qual_value", "") or cell.get("comment", ""):
                annual_filled += 1
        annual_total = len(ANNUAL_VARIABLES)

        ag_rows = get_agreements(org, y, sector=sector)
        ag_filled = sum(1 for r in ag_rows if any(r.get(k) for k in ("company_name", "wage_increase", "workers_affected", "one_off_lump_sum", "other_changes", "comment")))

        co_rows = get_companies(org, y, sector=sector)
        co_filled = sum(1 for r in co_rows if any(r.get(k) for k in ("company_name", "workers", "agreement", "union_density", "comment")))

        per_year[y] = {
            "annual_filled": annual_filled, "annual_total": annual_total,
            "ag_filled": ag_filled,
            "co_filled": co_filled,
        }
    return render_template("home.html", progress_years=per_year)


@app.route("/admin")
@login_required
@admin_required
def admin():
    years = current_years()
    selected_year = request.args.get("year", type=int) or None
    combos = get_all_org_sector_combinations()

    rows_data = []
    for c in combos:
        org = c["organisation"]
        sec = c["sector"]
        if selected_year:
            p = compute_progress(org, [selected_year], sector=sec)
        else:
            p = compute_progress(org, years, sector=sec)
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


@app.route("/annual-results", methods=["GET", "POST"])
@login_required
def annual_results():
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or request.form.get("org") or org
        if request.args.get("sector"):
            session["sector"] = request.args.get("sector")
    sector = session.get("sector", "")
    years = current_years()
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        data = []
        for item in ANNUAL_VARIABLES:
            var_key = item[0]
            row_mode = request.form.get(f"row_mode_{var_key}", "num").strip()
            for y in years:
                val = request.form.get(f"value_{var_key}_{y}", "").strip()
                qual = request.form.get(f"qual_{var_key}_{y}", "").strip()
                cmt = request.form.get(f"comment_{var_key}_{y}", "").strip()
                if var_key == "outcome_reporting" and cmt:
                    words = cmt.strip().split()
                    if len(words) > 80:
                        cmt = " ".join(words[:80])
                if val or qual or cmt:
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
        export_all()
        flash("Annual results saved.")
        return redirect(url_for("annual_results"))

    saved = get_annual_results(org, sector=sector)
    colormap = compute_cell_colors(saved, ANNUAL_VARIABLES, years)
    active = active_years(saved, ANNUAL_VARIABLES, years)
    row_modes = {item[0]: saved.get((item[0], years[-1]), {}).get("row_mode", "num") for item in ANNUAL_VARIABLES}

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
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or request.form.get("org") or org
        if request.args.get("sector"):
            session["sector"] = request.args.get("sector")
    sector = session.get("sector", "")
    years = current_years()
    selected_year = request.args.get("year", type=int) or years[-1]
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        selected_year = int(request.form.get("year", years[-1]))
        rows = []
        for i in range(10):
            rows.append({
                "company_name": request.form.get(f"company_name_{i}", "").strip(),
                "wage_increase": request.form.get(f"wage_increase_{i}", "").strip(),
                "workers_affected": safe_int(request.form.get(f"workers_affected_{i}", 0)),
                "level": request.form.get(f"level_{i}", "").strip(),
                "date_of_agreement": request.form.get(f"date_of_agreement_{i}", "").strip(),
                "one_off_lump_sum": request.form.get(f"one_off_lump_sum_{i}", "").strip(),
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
        export_all()
        flash("Major agreements saved.")
        return redirect(url_for("agreements", year=selected_year))

    saved = get_agreements(org, selected_year, sector=sector)
    return render_template("agreements.html", years=years, selected_year=selected_year, saved=saved, view_org=org, orgs=orgs)


@app.route("/major-companies", methods=["GET", "POST"])
@login_required
def companies():
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or request.form.get("org") or org
        if request.args.get("sector"):
            session["sector"] = request.args.get("sector")
    sector = session.get("sector", "")
    years = current_years()
    selected_year = request.args.get("year", type=int) or years[-1]
    orgs = get_all_orgs_csv() if session.get("is_admin") else None

    if request.method == "POST":
        selected_year = int(request.form.get("year", years[-1]))
        rows = []
        for i in range(10):
            rows.append({
                "company_name": request.form.get(f"company_name_{i}", "").strip(),
                "agreement": request.form.get(f"agreement_{i}", "").strip(),
                "workers": safe_int(request.form.get(f"workers_{i}", 0)),
                "number_of_unions": safe_int(request.form.get(f"number_of_unions_{i}", 0)),
                "union_members": 0,
                "ewc_presence": request.form.get(f"ewc_presence_{i}", "").strip(),
                "mnc": request.form.get(f"mnc_{i}", "").strip(),
                "bargaining_climate": "",
                "one_off_lump_sum": "",
                "comment": _combine_tc(request.form.get(f"comment_title_{i}", "").strip(),
                                       request.form.get(f"comment_content_{i}", "").strip()),
                "union_density": request.form.get(f"union_density_{i}", "").strip(),
                "worker_representation": request.form.get(f"worker_representation_{i}", "").strip(),
                "sector": sector,
            })
        save_companies(org, selected_year, rows, sector=sector)
        export_all()
        flash("Major companies saved.")
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
            ("Additional lump sum (€)", [("2024", "500 €"), ("2025", "550 €"), ("2026", "600 €")]),
            ("Number of multi-employer agreements", [("2024", "12"), ("2025", "14"), ("2026", "15")]),
            ("Number of single-employer agreements", [("2024", "45"), ("2025", "42"), ("2026", "40")]),
            ("Average full-time monthly wage (€)", [("2024", "3200"), ("2025", "3350"), ("2026", "3480")]),
            ("National inflation rate (%)", [("2024", "2.8%"), ("2025", "3.1%"), ("2026", "2.5%")]),
            ("Sectoral productivity evolution (%)", [("2024", "1.5%"), ("2025", "1.8%"), ("2026", "1.6%")]),
            ("Qualitative reporting", [("2024", "Wage accord"), ("2025", "Pension framework"), ("2026", "Sector renewal")]),
        ],
        "agreements": [
            ("Megacorp GmbH", "Entire sector", "2026-06", "2 years", 12000, "5.2%", "600 €", "Training & working time", "Major breakthrough"),
            ("IndustryServices AG", "Multi-employer", "2026-04", "1 year", 8500, "4.5%", "500 €", "Health subsidy", "Stable bargaining"),
            ("LogiStar SE", "Single-employer", "2026-07", "3-5 years", 5200, "3.8%", "400 €", "Overtime rules", "Tense negotiations"),
            ("CarePlus e.V.", "Single-employer", "2026-05", "2 years", 3100, "4.0%", "350 €", "Extra holidays", "Favourable outcome"),
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
    org = session["organisation"]
    sector = session.get("sector", "")
    is_adm = session.get("is_admin", False)

    db_files = []
    affiliate_files = []

    if is_adm:
        # Full database tables for admin
        for fname in ("unicompass.xlsx", "annual_results.csv", "agreements.csv", "companies.csv"):
            fpath = os.path.join(EXPORT_DIR, fname)
            if os.path.isfile(fpath):
                db_files.append({"name": fname, "size": f"{os.path.getsize(fpath) / 1024:.1f} KB"})

        # Per affiliate & sector files
        for fname in sorted(os.listdir(EXPORT_DIR), reverse=True):
            if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
                continue
            if fname in ("unicompass.xlsx", "annual_results.csv", "agreements.csv", "companies.csv"):
                continue
            fpath = os.path.join(EXPORT_DIR, fname)
            if os.path.isfile(fpath):
                affiliate_files.append({"name": fname, "size": f"{os.path.getsize(fpath) / 1024:.1f} KB"})
    else:
        # Regular user: strictly for selected organisation and sector
        prefix = org_sector_file_prefix(org, sector) + "_"
        fallback_prefix = org_file_prefix(org) + "_"
        for fname in sorted(os.listdir(EXPORT_DIR), reverse=True):
            if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
                continue
            if fname.startswith(prefix) or (sector.lower() == "all" and fname.startswith(fallback_prefix)):
                fpath = os.path.join(EXPORT_DIR, fname)
                if os.path.isfile(fpath):
                    affiliate_files.append({"name": fname, "size": f"{os.path.getsize(fpath) / 1024:.1f} KB"})

    return render_template(
        "export.html",
        db_files=db_files,
        files=affiliate_files,
        is_admin=is_adm,
        selected_org=org,
        selected_sector=sector,
    )


@app.route("/export/download/<path:filename>")
@login_required
def export_download(filename):
    safe_name = secure_filename(filename)
    if not (safe_name.endswith(".csv") or safe_name.endswith(".xlsx")):
        abort(404)
    org = session["organisation"]
    sector = session.get("sector", "")
    if not session.get("is_admin"):
        prefix = org_sector_file_prefix(org, sector) + "_"
        fallback_prefix = org_file_prefix(org) + "_"
        if not (safe_name.startswith(prefix) or (sector.lower() == "all" and safe_name.startswith(fallback_prefix))):
            abort(403)
    return send_from_directory(EXPORT_DIR, safe_name, as_attachment=True)


@app.route("/export/download-all")
@login_required
def export_download_all():
    org = session["organisation"]
    sector = session.get("sector", "")
    is_adm = session.get("is_admin", False)
    if is_adm:
        zip_bytes = generate_export_zip(is_admin=True)
    else:
        zip_bytes = generate_export_zip(org_filter=org, sector_filter=sector, is_admin=False)
    return send_file(
        BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name="unicompass_export.zip",
    )
