"""
UNI Compass — Flask application for tracking collective bargaining progress.
"""

from functools import wraps

from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file

import os
from auth import authenticate, get_all_organisations as get_all_orgs_csv
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
    check_login_rate_limit,
    record_failed_login,
    clear_login_attempts,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY


@app.context_processor
def inject_globals():
    if "email" not in session:
        return {}
    view_org = request.args.get("org") or session.get("organisation", "")
    return {
        "current_email": session["email"],
        "session_org": session.get("organisation", ""),
        "view_org": view_org,
        "is_admin": bool(session.get("is_admin")),
        "all_orgs": get_all_orgs_csv() if session.get("is_admin") else None,
        "current_page": request.path,
    }

ANNUAL_VARIABLES = [
    ("bargaining_climate", "Bargaining climate", "climate"),
    ("collective_bargaining_coverage", "Collective bargaining coverage (number of workers)", "level"),
    ("avg_wage_increase", "Collectively agreed wage increase (%)", "rate"),
    ("one_off_lump_sum", "One-off lump sum", "level"),
    ("multi_employer_agreements", "Number of multi-employer agreements", "level"),
    ("single_employer_agreements", "Number of single-employer agreements", "level"),
    ("avg_monthly_wage", "Average full-time monthly wage", "level"),
    ("inflation_rate", "National inflation rate", "rate"),
    ("sectoral_productivity", "Sectoral productivity evolution", "rate"),
    ("outcome_reporting", "Qualitative reporting", "report"),
]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
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
            if v and is_numeric(v):
                numeric_vals.append((y, float(v)))
        if len(numeric_vals) > 1:
            vals = [v for _, v in numeric_vals]
            mn, mx = min(vals), max(vals)
            rng = mx - mn
            if rng > 0:
                for y, v in numeric_vals:
                    ratio = (v - mn) / rng
                    red = int(ratio * 200)
                    blue = int((1 - ratio) * 200)
                    colormap[(var_key, y)] = f"background-color:rgba({red}, 0, {blue}, 0.2);"
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
    if "email" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "")
        pincode = request.form.get("pincode", "")

        if not check_login_rate_limit(email):
            flash("Too many failed attempts. Please try again later.", "error")
            return render_template("login.html")

        user = authenticate(email, pincode)
        if user:
            clear_login_attempts(email)
            session["email"] = user["email"]
            session["organisation"] = user["organisation"]
            session["is_admin"] = user["is_admin"]
            flash(f"Welcome, {user['email']}!")
            return redirect(url_for("dashboard"))
        record_failed_login(email)
        flash("Invalid username or pincode.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    years = current_years()
    org = session["organisation"]
    per_year = {}
    for y in years:
        annual = get_annual_results(org, [y])
        annual_filled = 0
        for item in ANNUAL_VARIABLES:
            vk = item[0]
            cell = annual.get((vk, y), {})
            if vk == "outcome_reporting":
                if cell.get("comment", ""):
                    annual_filled += 1
            else:
                if cell.get("value", ""):
                    annual_filled += 1
        annual_total = len(ANNUAL_VARIABLES)

        ag_rows = get_agreements(org, y)
        ag_filled = sum(1 for r in ag_rows if r.get("company_name", ""))
        ag_total = 10

        co_rows = get_companies(org, y)
        co_filled = sum(1 for r in co_rows if r.get("company_name", ""))
        co_total = 10

        per_year[y] = {
            "annual_filled": annual_filled, "annual_total": annual_total,
            "ag_filled": ag_filled, "ag_total": ag_total,
            "co_filled": co_filled, "co_total": co_total,
        }
    return render_template("dashboard.html", progress_years=per_year)


@app.route("/admin")
@login_required
@admin_required
def admin():
    years = current_years()
    selected_year = request.args.get("year", type=int) or None
    orgs = get_all_organisations()
    if selected_year:
        progress = {org: compute_progress(org, [selected_year]) for org in orgs}
    else:
        progress = {org: compute_progress(org, years) for org in orgs}
    return render_template("admin.html", orgs=orgs, progress=progress, years=years, selected_year=selected_year)


@app.route("/annual-results", methods=["GET", "POST"])
@login_required
def annual_results():
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or request.form.get("org") or org
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
                    if len(words) > 100:
                        cmt = " ".join(words[:100])
                if val or qual or cmt:
                    data.append({
                        "variable": var_key,
                        "year": y,
                        "value": val,
                        "qual_value": qual,
                        "row_mode": row_mode,
                        "comment": cmt
                    })
        save_annual_results(org, data)
        export_all()
        flash("Annual results saved.")
        return redirect(url_for("annual_results"))

    saved = get_annual_results(org, years)
    forced_qual = {"bargaining_climate"}
    row_modes = {}
    for item in ANNUAL_VARIABLES:
        var_key = item[0]
        if var_key in forced_qual:
            row_modes[var_key] = "qual"
        else:
            saved_mode = None
            for y in years:
                cell = saved.get((var_key, y), {})
                if cell.get("row_mode"):
                    saved_mode = cell["row_mode"]
                    break
            if saved_mode:
                row_modes[var_key] = saved_mode
            else:
                row_has_qual = False
                for y in years:
                    cell = saved.get((var_key, y), {})
                    if cell.get("qual_value"):
                        row_has_qual = True
                        break
                row_modes[var_key] = "qual" if row_has_qual else "num"

    colormap = compute_cell_colors(saved, ANNUAL_VARIABLES, years)
    active = active_years(saved, ANNUAL_VARIABLES, years)
    return render_template(
        "annual_results.html",
        variables=ANNUAL_VARIABLES,
        years=years,
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
                "working_time_change": _combine_tc(request.form.get(f"working_time_change_title_{i}", "").strip(),
                                                   request.form.get(f"working_time_change_content_{i}", "").strip()),
                "other_changes": _combine_tc(request.form.get(f"other_changes_title_{i}", "").strip(),
                                             request.form.get(f"other_changes_content_{i}", "").strip()),
            })
        save_agreements(org, selected_year, rows)
        export_all()
        flash("Major agreements saved.")
        return redirect(url_for("agreements", year=selected_year))

    saved = get_agreements(org, selected_year)
    return render_template("agreements.html", years=years, selected_year=selected_year, saved=saved, view_org=org, orgs=orgs)


@app.route("/major-companies", methods=["GET", "POST"])
@login_required
def companies():
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or request.form.get("org") or org
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
            })
        save_companies(org, selected_year, rows)
        export_all()
        flash("Major companies saved.")
        return redirect(url_for("companies", year=selected_year))

    saved = get_companies(org, selected_year)
    return render_template("companies.html", years=years, selected_year=selected_year, saved=saved, view_org=org, orgs=orgs)


@app.route("/example")
def example():
    example_data = {
        "annual": [
            ("Bargaining climate", [("2024", "Neutral"), ("2025", "Favourable"), ("2026", "Favourable")]),
            ("Collective bargaining coverage (%)", [("2024", "78"), ("2025", "81"), ("2026", "83")]),
            ("Collectively agreed wage increase (%)", [("2024", "3.2"), ("2025", "4.0"), ("2026", "3.8")]),
            ("One-off lump sum", [("2024", "500"), ("2025", "550"), ("2026", "600")]),
            ("Number of multi-employer agreements", [("2024", "12"), ("2025", "14"), ("2026", "15")]),
            ("Number of single-employer agreements", [("2024", "45"), ("2025", "42"), ("2026", "40")]),
            ("Average full-time monthly wage", [("2024", "3200"), ("2025", "3350"), ("2026", "3480")]),
            ("National inflation rate", [("2024", "2.8"), ("2025", "3.1"), ("2026", "2.5")]),
            ("Sectoral productivity evolution", [("2024", "1.5"), ("2025", "1.8"), ("2026", "1.6")]),
        ],
        "agreements": [
            ("Megacorp GmbH", "Multi-employer", "2024-06", "Two year", 12000, "5.2%", "600", "38 to 37 hours", "Training funds increase", "Major breakthrough"),
            ("IndustryServices AG", "Multi-employer", "2024-04", "One year", 8500, "4.5%", "500", "None", "Health insurance subsidy", "Stable bargaining"),
            ("LogiStar SE", "Single-employer", "2024-07", "Three year", 5200, "3.8%", "400", "None", "None", "Tense negotiations"),
            ("CarePlus e.V.", "Single-employer", "2024-05", "Two year", 3100, "4.0%", "350", "39 to 38 hours", "Extra holiday day", "Favourable outcome"),
        ],
        "companies": [
            ("MegaIndustry plc", "Yes", "Yes", 15000, 12, "75-99%", "Yes", "Yes", "Strong union presence"),
            ("ServiceFirst Ltd", "Yes", "Yes", 9200, 8, "50-74%", "Yes", "Yes", "Constructive dialogue"),
            ("TransLog GmbH", "Yes", "No", 7400, 1, "0%", "No", "No", "Challenging environment"),
            ("HealthCare Corp", "No", "Yes", 4100, 5, "25-49%", "Yes", "No", "Stable representation"),
        ],
    }
    return render_template("example.html", data=example_data)


@app.route("/export")
@login_required
def export():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or ""
    prefix = org_file_prefix(org) + "_" if org else None
    files = []
    for fname in sorted(os.listdir(EXPORT_DIR), reverse=True):
        if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
            continue
        if prefix is not None and not fname.startswith(prefix):
            continue
        fpath = os.path.join(EXPORT_DIR, fname)
        if os.path.isfile(fpath):
            files.append({"name": fname, "size": f"{os.path.getsize(fpath) / 1024:.1f} KB"})
    return render_template("export.html", files=files)


@app.route("/export/download/<filename>")
@login_required
def export_download(filename):
    return send_file(os.path.join(EXPORT_DIR, filename), as_attachment=True)


@app.route("/export/download-all")
@login_required
def export_download_all():
    org = session["organisation"]
    if session.get("is_admin"):
        org = request.args.get("org") or ""
    prefix = org_file_prefix(org) + "_" if org else None
    zip_bytes = generate_export_zip(org_filter=prefix)
    return send_file(
        BytesIO(zip_bytes),
        as_attachment=True,
        download_name="unicompass_export.zip",
    )


def create_app():
    init_db()
    export_all()

    # Force Jinja2 to never cache templates — required because gunicorn runs with debug=False
    app.jinja_env.auto_reload = True
    app.jinja_env.cache_size = 0

    @app.after_request
    def add_no_cache(response):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    return app
