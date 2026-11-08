import unittest
import os
import json
from app import app, compute_cell_colors, ANNUAL_VARIABLES
from auth import (
    authenticate,
    load_users,
    get_user,
    set_user_pincode,
    is_user_blocked,
    get_all_sectors,
    get_all_org_sector_combinations,
)
from models import (
    init_db, get_annual_results, save_annual_results,
    get_agreements, save_agreements, get_companies, save_companies,
    compute_progress, export_all, generate_export_zip,
    clear_login_attempts, record_failed_login,
    save_survey_response, get_user_survey_response,
    get_survey_feedback_summary, get_survey
)

class UniCompassTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        init_db()

    def test_auth_users_csv(self):
        users = load_users()
        self.assertTrue(len(users) > 0)
        # Check admin user
        u = authenticate("admin", "1984")
        self.assertIsNotNone(u)
        self.assertTrue(u["is_admin"])
        self.assertIn("Commerce", u["sectors"])

        # Check BE-ACV-Finance
        u2 = authenticate("BE-ACV-Finance", "1234")
        self.assertIsNotNone(u2)
        self.assertEqual(u2["organisation"], "ACV")
        self.assertEqual(u2["sector"], "Finance")
        self.assertEqual(u2["country"], "Belgium")

    def test_sector_scoped_data_storage(self):
        org = "ACV"
        sec1 = "Finance"
        sec2 = "Commerce"
        year = 2026

        # Save data in Finance sector
        save_annual_results(org, [{
            "variable": "bargaining_climate",
            "year": year,
            "value": "",
            "qual_value": "very favourable",
            "row_mode": "qual",
            "comment": "Good year"
        }], sector=sec1)

        # Save different data in Commerce sector
        save_annual_results(org, [{
            "variable": "bargaining_climate",
            "year": year,
            "value": "",
            "qual_value": "very unfavourable",
            "row_mode": "qual",
            "comment": "Tough year"
        }], sector=sec2)

        # Retrieve Finance
        res1 = get_annual_results(org, sector=sec1)
        self.assertEqual(res1[("bargaining_climate", year)]["qual_value"], "very favourable")
        self.assertEqual(res1[("bargaining_climate", year)]["comment"], "Good year")

        # Retrieve Commerce
        res2 = get_annual_results(org, sector=sec2)
        self.assertEqual(res2[("bargaining_climate", year)]["qual_value"], "very unfavourable")
        self.assertEqual(res2[("bargaining_climate", year)]["comment"], "Tough year")

    def test_likert_heatmap_colors_blue_to_orange(self):
        saved = {
            ("bargaining_climate", 2024): {"qual_value": "very favourable", "value": ""},
            ("bargaining_climate", 2025): {"qual_value": "neutral", "value": ""},
            ("bargaining_climate", 2026): {"qual_value": "very unfavourable", "value": ""},
            ("avg_pay_increase", 2024): {"value": "2.0", "qual_value": ""},
            ("avg_pay_increase", 2025): {"value": "4.0", "qual_value": ""},
        }
        colormap = compute_cell_colors(saved, ANNUAL_VARIABLES, [2024, 2025, 2026])
        # Very favourable -> High / Orange (249, 115, 22)
        self.assertIn("rgba(249, 115, 22", colormap[("bargaining_climate", 2024)])
        # Neutral -> Slate (148, 163, 184)
        self.assertIn("rgba(148, 163, 184", colormap[("bargaining_climate", 2025)])
        # Very unfavourable -> Low / Blue (37, 99, 235)
        self.assertIn("rgba(37, 99, 235", colormap[("bargaining_climate", 2026)])
        # Numeric min (Blue) and max (Orange)
        self.assertIn("37", colormap[("avg_pay_increase", 2024)])
        self.assertIn("249", colormap[("avg_pay_increase", 2025)])

    def test_home_page_progress_bars_and_topbar(self):
        with self.client:
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res = self.client.get("/home")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("Input progress", html)
            # Verify no numbers like '/10' in the progress table
            self.assertNotIn("/10", html)
            # Verify topbar Logout mention with hover username
            self.assertIn("Logout", html)
            self.assertIn('title="admin"', html)

    def test_admin_home_org_sector_combinations(self):
        combos = get_all_org_sector_combinations()
        self.assertTrue(len(combos) > 0)
        with self.client:
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res = self.client.get("/admin")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("ACV", html)
            self.assertIn("ABVV", html)
            self.assertIn("Finance", html)
            self.assertIn("Commerce", html)

    def test_exports_scoped_and_admin(self):
        # Save sample data
        save_annual_results("ACV", [{
            "variable": "collective_bargaining_coverage",
            "year": 2026,
            "value": "85.5",
            "qual_value": "",
            "row_mode": "num",
            "comment": "",
            "sector": "Finance"
        }], sector="Finance")

        export_all()

        with self.client:
            # Regular user export view
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            res = self.client.get("/export")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("ACV_Finance_annual_results.csv", html)

            # Regular user cannot download other files
            res_forbid = self.client.get("/export/download/ORG_Commerce_annual_results.csv")
            self.assertEqual(res_forbid.status_code, 403)

        with self.client:
            # Admin export view (shows scoped files matching active view_org/sector, same as viewing users)
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res_admin = self.client.get("/export?org=ACV&sector=Finance")
            self.assertEqual(res_admin.status_code, 200)
            html_admin = res_admin.get_data(as_text=True)
            self.assertIn("Viewing: <strong>ACV</strong>", html_admin)
            self.assertIn("ACV_Finance_annual_results.csv", html_admin)
            self.assertIn("Download all tables", html_admin)
            self.assertIn("Generate PDF Report", html_admin)

    def test_set_sector_switching(self):
        with self.client:
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res = self.client.get("/set-sector?sector=Finance", follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("sector"), "Finance")

    def test_pages_render_without_template_errors(self):
        with self.client:
            # Login as regular user
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            for path in ("/home", "/annual-results", "/major-agreements", "/major-companies", "/export"):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200, f"Failed rendering {path} for regular user")

        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            for path in ("/home", "/annual-results", "/major-agreements", "/major-companies", "/export", "/admin"):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200, f"Failed rendering {path} for admin")

    def test_admin_editing_affiliate_preserves_focus(self):
        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)

            # View annual results as affiliate
            res = self.client.get("/annual-results?org=ACV")
            self.assertEqual(res.status_code, 200)
            self.assertIn("Viewing: <strong>ACV</strong>", res.get_data(as_text=True))

            # Save annual results for affiliate
            post_res = self.client.post("/annual-results", data={
                "org": "ACV",
                "value_collective_bargaining_coverage_2026": "88",
            }, follow_redirects=False)
            self.assertEqual(post_res.status_code, 302)
            self.assertIn("org=ACV", post_res.headers.get("Location", ""))

            # Follow redirect and verify focus is preserved
            follow_res = self.client.get(post_res.headers["Location"])
            self.assertEqual(follow_res.status_code, 200)
            self.assertIn("Viewing: <strong>ACV</strong>", follow_res.get_data(as_text=True))

            # Save agreements for affiliate
            ag_post = self.client.post("/major-agreements", data={
                "org": "ACV",
                "year": "2026",
                "company_name_0": "Test CLA",
                "workers_affected_0": "500",
            }, follow_redirects=False)
            self.assertEqual(ag_post.status_code, 302)
            self.assertIn("org=ACV", ag_post.headers.get("Location", ""))

            # Save companies for affiliate
            co_post = self.client.post("/major-companies", data={
                "org": "ACV",
                "year": "2026",
                "company_name_0": "Test Company",
                "workers_0": "1000",
            }, follow_redirects=False)
            self.assertEqual(co_post.status_code, 302)
            self.assertIn("org=ACV", co_post.headers.get("Location", ""))

    def test_security_users_csv_cannot_be_downloaded(self):
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            res1 = self.client.get("/export/download/users.csv")
            self.assertIn(res1.status_code, [403, 404])
            res2 = self.client.get("/export/download/../users.csv")
            self.assertIn(res2.status_code, [403, 404, 400])
            res3 = self.client.get("/export/download/..%2Fusers.csv")
            self.assertIn(res3.status_code, [403, 404, 400])
            res4 = self.client.get("/export/download/unicompass.db")
            self.assertIn(res4.status_code, [403, 404])

    def test_admin_download_db_backup(self):
        with self.client:
            # 1. Non-admin user is denied all 4 download routes
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            for path in ("/admin/download-db", "/admin/download-csv", "/admin/download-xlsx", "/admin/download-zip"):
                res_user = self.client.get(path)
                self.assertEqual(res_user.status_code, 302)

            # 2. Admin user receives timestamped database files (.db, .csv, .xlsx, .zip)
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            
            # Check admin page has all 4 download buttons
            admin_page = self.client.get("/admin")
            self.assertEqual(admin_page.status_code, 200)
            admin_html = admin_page.get_data(as_text=True)
            self.assertIn("Download .db", admin_html)
            self.assertIn("Download .csv", admin_html)
            self.assertIn("Download .xlsx", admin_html)
            self.assertIn("Download all tables (.zip)", admin_html)
            self.assertIn("/admin/download-db", admin_html)
            self.assertIn("/admin/download-csv", admin_html)
            self.assertIn("/admin/download-xlsx", admin_html)
            self.assertIn("/admin/download-zip", admin_html)

            # Download the DB copy (.db)
            res_db = self.client.get("/admin/download-db")
            self.assertEqual(res_db.status_code, 200)
            self.assertEqual(res_db.mimetype, "application/x-sqlite3")
            self.assertIn("unicompass_", res_db.headers.get("Content-Disposition", ""))
            self.assertIn(".db", res_db.headers.get("Content-Disposition", ""))

            # Validate SQLite database integrity
            import tempfile
            import sqlite3
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp.write(res_db.data)
                tmp_path = tmp.name

            try:
                conn = sqlite3.connect(tmp_path)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                conn.close()
                self.assertNotIn("users", tables)
                self.assertIn("annual_results", tables)
                self.assertIn("agreements", tables)
                self.assertIn("companies", tables)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            # Download the CSV ZIP (.zip)
            res_csv = self.client.get("/admin/download-csv")
            self.assertEqual(res_csv.status_code, 200)
            self.assertEqual(res_csv.mimetype, "application/zip")
            self.assertIn("unicompass_csv_", res_csv.headers.get("Content-Disposition", ""))
            self.assertIn(".zip", res_csv.headers.get("Content-Disposition", ""))
            
            from zipfile import ZipFile
            from io import BytesIO
            with ZipFile(BytesIO(res_csv.data), "r") as zf:
                names = zf.namelist()
                self.assertIn("annual_results.csv", names)
                self.assertIn("agreements.csv", names)
                self.assertIn("companies.csv", names)

            # Download the Excel workbook (.xlsx)
            res_xlsx = self.client.get("/admin/download-xlsx")
            self.assertEqual(res_xlsx.status_code, 200)
            self.assertEqual(res_xlsx.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.assertIn("unicompass_", res_xlsx.headers.get("Content-Disposition", ""))
            self.assertIn(".xlsx", res_xlsx.headers.get("Content-Disposition", ""))
            
            from openpyxl import load_workbook
            wb = load_workbook(BytesIO(res_xlsx.data))
            self.assertIn("annual_results", wb.sheetnames)
            self.assertIn("agreements", wb.sheetnames)
            self.assertIn("companies", wb.sheetnames)
            self.assertNotIn("users", wb.sheetnames)

            # Download the all tables ZIP (.zip)
            res_all_zip = self.client.get("/admin/download-zip")
            self.assertEqual(res_all_zip.status_code, 200)
            self.assertEqual(res_all_zip.mimetype, "application/zip")
            self.assertIn("unicompass_all_tables_", res_all_zip.headers.get("Content-Disposition", ""))
            self.assertIn(".zip", res_all_zip.headers.get("Content-Disposition", ""))

            with ZipFile(BytesIO(res_all_zip.data), "r") as zf:
                all_names = zf.namelist()
                self.assertIn("annual_results.csv", all_names)
                self.assertIn("unicompass.xlsx", all_names)
                self.assertTrue(any("ACV" in name for name in all_names))

    def test_admin_viewing_affiliate_progress_consistency(self):
        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            
            # Switch to TK-ORG and Commerce
            res = self.client.get("/home?org=TK-ORG", follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn("Viewing: <strong>TK-ORG</strong>", res.get_data(as_text=True))
            
            # Progress table should compute for TK-ORG (0 completed), not UNI Europe
            res_turkey_home = self.client.get("/home")
            self.assertEqual(res_turkey_home.status_code, 200)
            self.assertIn("Viewing: <strong>TK-ORG</strong>", res_turkey_home.get_data(as_text=True))

    def test_pdf_report_generation_and_export(self):
        # 1. Direct model PDF generation
        from models import generate_pdf_report, save_agreements, save_companies
        
        # Save agreement and company with multi-line comment & other_changes
        save_agreements("ACV", 2026, [
            {
                "company_name": "BelgBank SA",
                "level": "Single-employer",
                "date_of_agreement": "15-06-26",
                "duration": "2 years",
                "workers_affected": 4200,
                "wage_increase": "4.2%",
                "lump_sum": "500 €",
                "other_changes": "Training & working time\nDetailed breakdown: 35h week and 5 days training.",
                "comment": "Major breakthrough\nNegotiations lasted 6 months but settled amicably."
            }
        ], sector="Finance")
        
        save_companies("ACV", 2026, [
            {
                "company_name": "BelgBank SA",
                "workers": 4500,
                "mnc": "Yes",
                "agreement": "Single-employer CLA",
                "number_of_unions": 3,
                "union_density": "75-89%",
                "worker_representation": "Yes",
                "ewc_presence": "Yes",
                "comment": "Strong union presence\nWorks council meets bi-monthly."
            }
        ], sector="Finance")

        pdf_bytes = generate_pdf_report(organisation="ACV", sector="Finance")
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1000)

        from config import EXPORT_DIR
        os.makedirs(EXPORT_DIR, exist_ok=True)
        pdf_path = os.path.join(EXPORT_DIR, "ACV_Finance_report.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # 2. Regular user endpoint access
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            
            # Export page loads and lists files (PDF is excluded from table, accessed via button)
            res_export = self.client.get("/export")
            self.assertEqual(res_export.status_code, 200)
            self.assertIn("Generate PDF Report", res_export.get_data(as_text=True))
            self.assertIn("ACV_Finance_annual_results.csv", res_export.get_data(as_text=True))
            self.assertNotIn("ACV_Finance_report.pdf", res_export.get_data(as_text=True))

            # Direct PDF generation endpoint
            res_pdf = self.client.get("/export/pdf")
            self.assertEqual(res_pdf.status_code, 200)
            self.assertEqual(res_pdf.mimetype, "application/pdf")
            self.assertIn("ACV_Finance_report.pdf", res_pdf.headers.get("Content-Disposition", ""))
            self.assertTrue(res_pdf.data.startswith(b"%PDF-"))
            res_pdf.close()

            # Download pre-generated PDF file
            res_dl = self.client.get("/export/download/ACV_Finance_report.pdf")
            self.assertEqual(res_dl.status_code, 200)
            self.assertTrue(res_dl.data.startswith(b"%PDF-"))
            res_dl.close()

            # Cannot download other affiliate's PDF
            res_forbidden = self.client.get("/export/download/ORG_Commerce_report.pdf")
            self.assertEqual(res_forbidden.status_code, 403)
            res_forbidden.close()

        # 3. Admin PDF generation endpoint
        with self.client:
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res_admin_pdf = self.client.get("/export/pdf?org=ORG&sector=Commerce")
            self.assertEqual(res_admin_pdf.status_code, 200)
            self.assertEqual(res_admin_pdf.mimetype, "application/pdf")
            self.assertIn("ORG_Commerce_report.pdf", res_admin_pdf.headers.get("Content-Disposition", ""))
            self.assertTrue(res_admin_pdf.data.startswith(b"%PDF-"))
            res_admin_pdf.close()

            # Download all zip should contain PDF reports
            res_zip = self.client.get("/export/download-all")
            self.assertEqual(res_zip.status_code, 200)
            from zipfile import ZipFile
            from io import BytesIO
            with ZipFile(BytesIO(res_zip.data), "r") as zf:
                pdf_entries = [name for name in zf.namelist() if name.endswith(".pdf")]
                self.assertGreater(len(pdf_entries), 0)
            res_zip.close()

    def test_autosave_ajax_and_title_content_separation(self):
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)

            # 1. AJAX auto-save on Annual results with distinct title and content
            res_ann = self.client.post(
                "/annual-results",
                data={
                    "value_bargaining_climate_2026": "",
                    "qual_bargaining_climate_2026": "rather favourable",
                    "title_outcome_reporting_2026": "Pensions and wage framework",
                    "content_outcome_reporting_2026": "A breakthrough was reached after multi-month tripartite discussions.",
                },
                headers={"X-Requested-With": "XMLHttpRequest"}
            )
            self.assertEqual(res_ann.status_code, 200)
            self.assertEqual(res_ann.json.get("status"), "ok")

            # Verify in GET request that title and content are distinct
            res_get = self.client.get("/annual-results")
            html = res_get.get_data(as_text=True)
            self.assertIn('value="Pensions and wage framework"', html)
            self.assertIn('A breakthrough was reached after multi-month tripartite discussions.', html)

            # 2. AJAX auto-save on Major agreements
            res_agr = self.client.post(
                "/major-agreements",
                data={
                    "year": "2026",
                    "company_name_0": "FinanceCorp SA",
                    "workers_affected_0": "3200",
                    "wage_increase_0": "3.8%",
                    "comment_title_0": "Stable deal",
                    "comment_content_0": "Concluded with high union satisfaction.",
                },
                headers={"X-Requested-With": "XMLHttpRequest"}
            )
            self.assertEqual(res_agr.status_code, 200)
            self.assertEqual(res_agr.json.get("status"), "ok")

            # 3. AJAX auto-save on Major companies
            res_comp = self.client.post(
                "/major-companies",
                data={
                    "year": "2026",
                    "company_name_0": "GlobalBank Ltd",
                    "workers_0": "5000",
                    "comment_title_0": "EWC meeting scheduled",
                    "comment_content_0": "Annual plenary scheduled for November.",
                },
                headers={"X-Requested-With": "XMLHttpRequest"}
            )
            self.assertEqual(res_comp.status_code, 200)
            self.assertEqual(res_comp.json.get("status"), "ok")

    def test_five_strikes_lockout_and_9999_pincode_blocked(self):
        test_user = "test-al-finance-1"
        orig_pin = "1234"
        try:
            # Ensure starting state
            set_user_pincode(test_user, orig_pin)
            clear_login_attempts(test_user)

            # 1. Pincode 9999 is always blocked
            res_9999 = self.client.post("/login", data={"user": test_user, "pincode": "9999"}, follow_redirects=True)
            self.assertIn("Invalid username or pincode", res_9999.get_data(as_text=True))
            clear_login_attempts(test_user)

            # 2. Attempt 4 failed logins with wrong pincode
            for i in range(4):
                res = self.client.post("/login", data={"user": test_user, "pincode": "0001"}, follow_redirects=True)
                self.assertIn("Invalid username or pincode", res.get_data(as_text=True))
                user_info = get_user(test_user)
                self.assertEqual(user_info["pincode"], orig_pin)

            # 3. Fifth failed attempt triggers lockout and sets pincode to 9999
            res_5 = self.client.post("/login", data={"user": test_user, "pincode": "0001"}, follow_redirects=True)
            html_5 = res_5.get_data(as_text=True)
            self.assertIn("contact the administrator for a new pincode", html_5)

            # Verify in users.csv that pincode is now 9999
            user_info = get_user(test_user)
            self.assertEqual(user_info["pincode"], "9999")
            self.assertTrue(is_user_blocked(test_user))

            # 4. Subsequent login attempt (even with original pin or 9999) is blocked with administrator contact message
            res_locked = self.client.post("/login", data={"user": test_user, "pincode": orig_pin}, follow_redirects=True)
            html_locked = res_locked.get_data(as_text=True)
            self.assertIn("contact the administrator for a new pincode", html_locked)

            # 5. Admin resets user pincode
            set_user_pincode(test_user, "4321")
            clear_login_attempts(test_user)
            res_restored = self.client.post("/login", data={"user": test_user, "pincode": "4321"}, follow_redirects=True)
            self.assertIn(f"Welcome, {test_user}!", res_restored.get_data(as_text=True))

        finally:
            # Restore original pin in users.csv
            set_user_pincode(test_user, orig_pin)
            clear_login_attempts(test_user)

    def test_survey_workflow_and_persistence(self):
        """Test survey saving, retrieving, and updating inputs without duplicating rows."""
        user = "BE-ACV-Finance"
        survey_id = "2026"

        # 1. Initial submission via direct model
        save_survey_response(
            survey_id=survey_id,
            username=user,
            organisation="ACV",
            country="Belgium",
            sector="Finance",
            q1_rating=5,
            q2_rating=4,
            q3_rating=5,
            q4_text="Initial feedback on navigation and ease-of-use.",
            hide_org=0,
            hide_sector=0,
            hide_country=0
        )

        resp = get_user_survey_response(survey_id, user)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["q1_rating"], 5)
        self.assertEqual(resp["q2_rating"], 4)
        self.assertEqual(resp["q3_rating"], 5)
        self.assertEqual(resp["q4_text"], "Initial feedback on navigation and ease-of-use.")
        self.assertEqual(resp["country"], "Belgium")
        self.assertEqual(resp["sector"], "Finance")

        # 2. Update submission: changing ratings and text (must update existing record)
        save_survey_response(
            survey_id=survey_id,
            username=user,
            organisation="ACV",
            country="Belgium",
            sector="Finance",
            q1_rating=4,
            q2_rating=5,
            q3_rating=5,
            q4_text="Updated feedback with more detail.",
            hide_org=1,
            hide_sector=0,
            hide_country=0
        )

        resp2 = get_user_survey_response(survey_id, user)
        self.assertEqual(resp2["q1_rating"], 4)
        self.assertEqual(resp2["q2_rating"], 5)
        self.assertEqual(resp2["q4_text"], "Updated feedback with more detail.")
        self.assertEqual(resp2["hide_org"], 1)

        # Ensure only 1 record exists for this user and survey in the database
        from models import get_db
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM survey_responses WHERE survey_id = ? AND LOWER(TRIM(user)) = LOWER(TRIM(?))",
                (survey_id, user)
            ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_survey_http_endpoints_and_ajax_autosave(self):
        """Test GET and POST on /survey including AJAX response with live summary."""
        with self.client:
            # Login as regular user
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)

            # GET /survey
            res_get = self.client.get("/survey")
            self.assertEqual(res_get.status_code, 200)
            html = res_get.get_data(as_text=True)
            self.assertIn("Ad hoc survey 2026", html)
            self.assertIn("Rate the accessibility of UNI-COMPASS", html)
            self.assertIn("Results", html)
            self.assertIn("Show organisation", html)
            self.assertIn("Show user", html)
            self.assertIn("Organisation", html)

            # POST /survey via AJAX
            res_post = self.client.post(
                "/survey?id=2026",
                data={
                    "q1_rating": "5",
                    "q2_rating": "5",
                    "q3_rating": "4",
                    "q4_text": "Live AJAX test comment from Belgium Finance.",
                    "hide_org": "0",
                    "hide_sector": "0",
                    "hide_country": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"}
            )
            self.assertEqual(res_post.status_code, 200)
            data = res_post.get_json()
            self.assertEqual(data.get("status"), "ok")
            self.assertIn("summary", data)
            summary = data["summary"]
            self.assertGreaterEqual(summary["total_responses"], 1)
            self.assertGreaterEqual(summary["avg_q1"], 1.0)
            self.assertTrue(any("Live AJAX test comment" in c["text"] for c in summary["comments"]))

    def test_survey_privacy_options_filtering(self):
        """Test that privacy checkboxes cleanly hide metadata in get_survey_feedback_summary."""
        survey_id = "2026"
        user1 = "test-be-finance-1"
        user2 = "test-be-finance-2"
        user3 = "test-be-finance-3"

        try:
            # User 1: Show all (hide_org=0, hide_sector=0, hide_country=0, hide_user=0)
            save_survey_response(
                survey_id=survey_id, username=user1, organisation="Test BE Finance 1", country="Belgium", sector="Finance",
                q1_rating=5, q4_text="All visible comment.", hide_org=0, hide_sector=0, hide_country=0, hide_user=0
            )

            # User 2: Hide organisation and user
            save_survey_response(
                survey_id=survey_id, username=user2, organisation="Test BE Finance 2", country="Belgium", sector="Finance",
                q1_rating=4, q4_text="Secret org comment.", hide_org=1, hide_sector=0, hide_country=0, hide_user=1
            )

            # User 3: Hide all (fully anonymous)
            save_survey_response(
                survey_id=survey_id, username=user3, organisation="Test BE Finance 3", country="Belgium", sector="Finance",
                q1_rating=3, q4_text="Fully private comment.", hide_org=1, hide_sector=1, hide_country=1, hide_user=1
            )

            summary = get_survey_feedback_summary(survey_id)
            comments_by_text = {c["text"]: c for c in summary["comments"]}

            # Check User 1
            c1 = comments_by_text.get("All visible comment.")
            self.assertIsNotNone(c1)
            self.assertEqual(c1["organisation"], "Test BE Finance 1")
            self.assertEqual(c1["sector"], "Finance")
            self.assertEqual(c1["country"], "Belgium")
            self.assertEqual(c1["user"], "test-be-finance-1")
            self.assertFalse(c1["is_anonymous"])

            # Check User 2
            c2 = comments_by_text.get("Secret org comment.")
            self.assertIsNotNone(c2)
            self.assertEqual(c2["organisation"], "")  # masked
            self.assertEqual(c2["sector"], "Finance")
            self.assertEqual(c2["country"], "Belgium")
            self.assertEqual(c2["user"], "")  # masked
            self.assertFalse(c2["is_anonymous"])

            # Check User 3
            c3 = comments_by_text.get("Fully private comment.")
            self.assertIsNotNone(c3)
            self.assertEqual(c3["organisation"], "")
            self.assertEqual(c3["sector"], "")
            self.assertEqual(c3["country"], "")
            self.assertEqual(c3["user"], "")
            self.assertTrue(c3["is_anonymous"])
        finally:
            from models import get_db
            with get_db() as db:
                db.execute("DELETE FROM survey_responses WHERE user IN (?, ?, ?)", (user1, user2, user3))
                db.commit()

    def test_survey_admin_table_backups(self):
        """Test that survey_responses is exported together with the tables from the admin page."""
        backup_user = "test-be-finance-1"
        try:
            # Ensure at least one response exists
            save_survey_response(
                survey_id="2026", username=backup_user, organisation="Test BE Finance 1", country="Belgium", sector="Finance",
                q1_rating=5, q2_rating=4, q3_rating=5, q4_text="Backup test comment."
            )
            export_all()

            with self.client:
                # 1. Regular user on /export should NOT see survey_responses.csv in their files list
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
                res_user = self.client.get("/export")
                self.assertEqual(res_user.status_code, 200)
                self.assertNotIn("survey_responses.csv", res_user.get_data(as_text=True))

                # Regular user cannot download survey_responses.csv directly
                res_forbidden = self.client.get("/export/download/survey_responses.csv")
                self.assertEqual(res_forbidden.status_code, 403)

            with self.client:
                # 2. Admin downloads CSV backup ZIP: survey_responses.csv must be included
                self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
                res_csv = self.client.get("/admin/download-csv")
                self.assertEqual(res_csv.status_code, 200)
                from zipfile import ZipFile
                from io import BytesIO
                with ZipFile(BytesIO(res_csv.data), "r") as zf:
                    self.assertIn("survey_responses.csv", zf.namelist())

                # 3. Admin downloads XLSX backup: survey_responses worksheet must be included
                res_xlsx = self.client.get("/admin/download-xlsx")
                self.assertEqual(res_xlsx.status_code, 200)
                from openpyxl import load_workbook
                wb = load_workbook(BytesIO(res_xlsx.data))
                self.assertIn("survey_responses", wb.sheetnames)

                # 4. Admin downloads all tables ZIP: survey_responses.csv must be included
                res_zip = self.client.get("/admin/download-zip")
                self.assertEqual(res_zip.status_code, 200)
                with ZipFile(BytesIO(res_zip.data), "r") as zf:
                    self.assertIn("survey_responses.csv", zf.namelist())
        finally:
            from models import get_db
            with get_db() as db:
                db.execute("DELETE FROM survey_responses WHERE user = ?", (backup_user,))
                db.commit()

    def test_survey_feature_toggle(self):
        """Test that setting ENABLE_SURVEY=False disables the route and hides the tab."""
        import app as app_module
        orig_val = app_module.ENABLE_SURVEY
        try:
            app_module.ENABLE_SURVEY = False
            with self.client:
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)

                # Navigation should not show 'Ad hoc survey'
                home_res = self.client.get("/home")
                self.assertNotIn("Ad hoc survey", home_res.get_data(as_text=True))

                # Visiting /survey should redirect to home
                survey_res = self.client.get("/survey", follow_redirects=False)
                self.assertEqual(survey_res.status_code, 302)
                self.assertIn("/home", survey_res.headers.get("Location", ""))
        finally:
            app_module.ENABLE_SURVEY = orig_val

    def test_global_save_button_and_banner_toggle(self):
        """Test that ENABLE_SAVE_BUTTON controls visibility of save buttons and flash banners across all pages."""
        import app as app_module
        orig_val = app_module.ENABLE_SAVE_BUTTON
        try:
            # 1. By default (toggle off), save buttons and flash banners are suppressed across all pages
            app_module.ENABLE_SAVE_BUTTON = False
            with self.client:
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)

                # Survey page
                res = self.client.get("/survey")
                self.assertNotIn("save-survey-btn", res.get_data(as_text=True))
                post_res = self.client.post("/survey?id=2026", data={"q1_rating": "4"}, follow_redirects=True)
                self.assertNotIn("Your survey response has been saved.", post_res.get_data(as_text=True))

                # Annual results page
                res_ann = self.client.get("/annual-results")
                self.assertNotIn('<button type="submit" class="btn-save">Save</button>', res_ann.get_data(as_text=True))
                post_ann = self.client.post("/annual-results", data={}, follow_redirects=True)
                self.assertNotIn("Annual results saved for", post_ann.get_data(as_text=True))

                # Major agreements page
                res_ag = self.client.get("/major-agreements")
                self.assertNotIn('<button type="submit" class="btn-save">Save</button>', res_ag.get_data(as_text=True))
                post_ag = self.client.post("/major-agreements", data={"year": "2026"}, follow_redirects=True)
                self.assertNotIn("Major agreements saved for", post_ag.get_data(as_text=True))

                # Major companies page
                res_co = self.client.get("/major-companies")
                self.assertNotIn('<button type="submit" class="btn-save">Save</button>', res_co.get_data(as_text=True))
                post_co = self.client.post("/major-companies", data={"year": "2026"}, follow_redirects=True)
                self.assertNotIn("Major companies saved for", post_co.get_data(as_text=True))

            # 2. When toggle is on, save buttons are shown without banners or explanation text
            app_module.ENABLE_SAVE_BUTTON = True
            with self.client:
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)

                # Survey page: save button present, no reassurance explanation, no flash banner on save
                res = self.client.get("/survey")
                self.assertIn("save-survey-btn", res.get_data(as_text=True))
                self.assertNotIn("Inputs are saved to your account", res.get_data(as_text=True))
                post_res = self.client.post("/survey?id=2026", data={"q1_rating": "5"}, follow_redirects=True)
                self.assertNotIn("Your survey response has been saved.", post_res.get_data(as_text=True))

                # Annual results page: save button present, no flash banner on save
                res_ann = self.client.get("/annual-results")
                self.assertIn('<button type="submit" class="btn-save">Save</button>', res_ann.get_data(as_text=True))
                post_ann = self.client.post("/annual-results", data={}, follow_redirects=True)
                self.assertNotIn("Annual results saved for", post_ann.get_data(as_text=True))
        finally:
            app_module.ENABLE_SAVE_BUTTON = orig_val

    def test_home_logos_and_conditional_survey_link(self):
        """Test that logos are served and survey link appears conditionally on home page."""
        # 1. Verify logos endpoints serve actual images
        res_hiva = self.client.get("/logos/HIVA_RGB_LOGO_ENG.png")
        self.assertEqual(res_hiva.status_code, 200)
        self.assertIn("image/png", res_hiva.mimetype)

        res_uni = self.client.get("/logos/Uni-Global-Europa-Logo-LANDSCAPE-CMYK-297x300.jpg")
        self.assertEqual(res_uni.status_code, 200)
        self.assertIn("image/jpeg", res_uni.mimetype)

        # 2. When logged in and ENABLE_SURVEY=True: logos and survey link are both present
        import app as app_module
        orig_val = app_module.ENABLE_SURVEY
        try:
            app_module.ENABLE_SURVEY = True
            with self.client:
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
                home_res = self.client.get("/home")
                self.assertEqual(home_res.status_code, 200)
                html = home_res.get_data(as_text=True)
                self.assertIn("HIVA_RGB_LOGO_ENG.png", html)
                self.assertIn("Uni-Global-Europa-Logo-LANDSCAPE-CMYK-297x300.jpg", html)
                self.assertIn("To provide general feedback", html)
                self.assertIn('href="/survey"', html)

            # 3. When ENABLE_SURVEY=False: logos remain, but survey link and sentence are hidden
            app_module.ENABLE_SURVEY = False
            with self.client:
                self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
                home_res2 = self.client.get("/home")
                self.assertEqual(home_res2.status_code, 200)
                html2 = home_res2.get_data(as_text=True)
                self.assertIn("HIVA_RGB_LOGO_ENG.png", html2)
                self.assertIn("Uni-Global-Europa-Logo-LANDSCAPE-CMYK-297x300.jpg", html2)
                self.assertNotIn("To provide general feedback", html2)
                self.assertNotIn('href="/survey"', html2)
        finally:
            app_module.ENABLE_SURVEY = orig_val

    def test_triple_test_users_auth_and_session(self):
        """Verify that test triplets for countries and sectors (Finance, Commerce, ICT) authenticate with pincode 1234."""
        from auth import authenticate, load_countries

        # 1. Verify countries.csv loading
        countries = load_countries()
        self.assertGreaterEqual(len(countries), 48)
        country_dict = {c["code"].lower(): c["country"] for c in countries}
        self.assertIn("at", country_dict)
        self.assertEqual(country_dict["at"], "Austria")
        self.assertIn("be", country_dict)
        self.assertEqual(country_dict["be"], "Belgium")

        # 2. Check representative triplets requested by user:
        # test_at_finance_1/2/3, test_be_finance_1, test_be_commerce_1, test_at_ict_1, test_uk_finance_1
        sample_users = [
            ("test-at-finance-1", "Austria", "Finance"),
            ("test-at-finance-2", "Austria", "Finance"),
            ("test-at-finance-3", "Austria", "Finance"),
            ("test-at_commerce_1", "Austria", "Commerce"),
            ("test-at-ict-1", "Austria", "ICT"),
            ("test-be-finance-1", "Belgium", "Finance"),
            ("test-be_commerce_1", "Belgium", "Commerce"),
            ("test-be-ict-1", "Belgium", "ICT"),
            ("test-de-finance-1", "Germany", "Finance"),
            ("test-fr_commerce_1", "France", "Commerce"),
            ("test-uk-finance-1", "United Kingdom", "Finance"),
        ]

        for uname, expected_country, expected_sector in sample_users:
            user_dict = authenticate(uname, "1234")
            self.assertIsNotNone(user_dict, f"Failed to authenticate {uname}")
            self.assertEqual(user_dict["pincode"], "1234")
            self.assertEqual(user_dict["country"], expected_country)
            self.assertEqual(user_dict["sector"], expected_sector)
            self.assertFalse(user_dict["is_admin"])

            # Test login via web client
            with self.client:
                res = self.client.post("/login", data={"user": uname, "pincode": "1234"}, follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                from flask import session
                self.assertEqual(session.get("user"), uname)
                self.assertEqual(session.get("country"), expected_country)
                self.assertEqual(session.get("sector"), expected_sector)

    def test_volume_and_local_data_resolution(self):
        """Verify local data resolution, CaaS deployment marker behavior, and automatic volume seeding."""
        import tempfile
        import shutil
        from config import _resolve_data_dir, is_caas_deployment, BASE_DIR
        from auth import _ensure_users_csv, _ensure_countries_csv
        import auth as auth_module

        # Clean env
        env_keys = ["CAAS_DEPLOYED", "CAAS_ENV", "DEPLOYMENT_ENV", "USE_CAAS_VOLUME", "CAAS_VOLUME_PATH", "DATA_DIR"]
        orig_env = {k: os.environ.get(k) for k in env_keys}

        try:
            for k in env_keys:
                if k in os.environ:
                    del os.environ[k]

            # 1. Without CaaS deployment marker (e.g. running in Anaconda or standalone container):
            # Must strictly use the local data directory
            self.assertFalse(is_caas_deployment())
            self.assertEqual(_resolve_data_dir(), os.path.join(BASE_DIR, "data"))

            # Even if DATA_DIR is set, without CaaS marker it stays in local directory
            os.environ["DATA_DIR"] = "/tmp/should_be_ignored_without_marker"
            self.assertFalse(is_caas_deployment())
            self.assertEqual(_resolve_data_dir(), os.path.join(BASE_DIR, "data"))

            # 2. When CaaS deployment marker is present via environment variable:
            temp_volume = tempfile.mkdtemp(prefix="unicompass_caas_vol_")
            try:
                os.environ["CAAS_DEPLOYED"] = "1"
                os.environ["DATA_DIR"] = temp_volume
                self.assertTrue(is_caas_deployment())
                self.assertEqual(_resolve_data_dir(), os.path.abspath(temp_volume))

                # 3. Test volume seed copying when CaaS volume is initially empty
                orig_users_csv = auth_module.USERS_CSV
                orig_countries_csv = auth_module.COUNTRIES_CSV
                orig_data_dir = auth_module.DATA_DIR
                try:
                    auth_module.DATA_DIR = temp_volume
                    auth_module.USERS_CSV = os.path.join(temp_volume, "users.csv")
                    auth_module.COUNTRIES_CSV = os.path.join(temp_volume, "countries.csv")
                    self.assertFalse(os.path.exists(auth_module.USERS_CSV))
                    self.assertFalse(os.path.exists(auth_module.COUNTRIES_CSV))

                    # Seeding should automatically copy from BASE_DIR/data
                    resolved_users_csv = _ensure_users_csv()
                    resolved_countries_csv = _ensure_countries_csv()
                    self.assertTrue(os.path.exists(resolved_users_csv))
                    self.assertTrue(os.path.exists(resolved_countries_csv))

                    # Confirm seeded users include test_at_finance_1
                    with open(resolved_users_csv, "r", encoding="utf-8") as f:
                        content = f.read()
                        self.assertIn("test-at-finance-1", content)
                        self.assertIn("test-be_commerce_1", content)

                    # Confirm seeded countries include Austria and Belgium
                    with open(resolved_countries_csv, "r", encoding="utf-8") as f:
                        content = f.read()
                        self.assertIn("Austria,AT", content)
                        self.assertIn("Belgium,BE", content)
                finally:
                    auth_module.USERS_CSV = orig_users_csv
                    auth_module.COUNTRIES_CSV = orig_countries_csv
                    auth_module.DATA_DIR = orig_data_dir
            finally:
                shutil.rmtree(temp_volume, ignore_errors=True)

            # 4. When CaaS deployment marker is present via marker file:
            del os.environ["CAAS_DEPLOYED"]
            if "DATA_DIR" in os.environ:
                del os.environ["DATA_DIR"]
            marker_file = os.path.join(BASE_DIR, ".caas_marker")
            try:
                with open(marker_file, "w") as f:
                    f.write("deployed")
                self.assertTrue(is_caas_deployment())
            finally:
                if os.path.exists(marker_file):
                    os.remove(marker_file)
        finally:
            for k, v in orig_env.items():
                if v is not None:
                    os.environ[k] = v
                elif k in os.environ:
                    del os.environ[k]


    def test_sectors_and_countries_filtering_and_database_integrity(self):
        """Verify sectors are restricted to Commerce, Finance, ICT, countries match countries.csv, and DB has no unlinked data."""
        from auth import get_all_sectors, load_users, load_countries, authenticate, get_all_org_sector_combinations
        from config import ALLOWED_SECTORS
        import sqlite3
        from config import DATABASE

        # 1. Allowed sectors
        self.assertEqual(get_all_sectors(), ["Commerce", "Finance", "ICT"])
        self.assertEqual(ALLOWED_SECTORS, ["Commerce", "Finance", "ICT"])

        # 2. Users validation
        countries = {c["country"] for c in load_countries()} | {"Europe"}
        users = load_users()
        valid_orgs = {u["organisation"] for u in users}
        valid_users = {u["user"] for u in users}

        for u in users:
            self.assertIn(u["country"], countries, f"User {u['user']} has invalid country {u['country']}")
            if u["is_admin"] or u["sector"].lower() in ("all", "uni"):
                pass
            else:
                for s in u["sector"].split(","):
                    self.assertIn(s.strip(), ALLOWED_SECTORS, f"User {u['user']} has non-allowed sector {s}")

        # 3. Authenticate sector assignment
        admin_auth = authenticate("admin", "1984")
        self.assertIsNotNone(admin_auth)
        self.assertEqual(admin_auth["sectors"], ["Commerce", "Finance", "ICT"])

        user_auth = authenticate("BE-ACV-Finance", "1234")
        self.assertIsNotNone(user_auth)
        self.assertEqual(user_auth["sectors"], ["Finance"])

        # 4. Combinations
        combos = get_all_org_sector_combinations()
        for c in combos:
            self.assertIn(c["sector"], ALLOWED_SECTORS)
            self.assertIn(c["organisation"], valid_orgs)

        # 5. Database data links (read-only: never modify the live database from a test)
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        for table in ["agreements", "companies", "annual_results"]:
            rows = cursor.execute(f"SELECT organisation, sector FROM {table}").fetchall()
            for org, sec in rows:
                self.assertIn(org, valid_orgs, f"Unlinked organisation {org} in {table}")
                self.assertIn(sec, ALLOWED_SECTORS + ["UNI", ""], f"Invalid sector {sec} in {table}")

        survey_rows = cursor.execute("SELECT user, organisation, country, sector FROM survey_responses").fetchall()
        for u, org, cnt, sec in survey_rows:
            self.assertIn(u, valid_users)
            self.assertIn(org, valid_orgs | {""})
            self.assertIn(sec, ALLOWED_SECTORS + ["UNI", ""])
            self.assertIn(cnt, countries | {""})

        conn.close()

    def test_survey_table_alignment_and_background(self):
        """Verify survey table styles specify top-left alignment and transparent background."""
        from config import BASE_DIR
        css_path = os.path.join(BASE_DIR, "static", "css", "survey.css")
        self.assertTrue(os.path.exists(css_path))
        with open(css_path, "r", encoding="utf-8") as f:
            css_text = f.read()

        # Check alignment and transparent background rules
        self.assertIn("text-align: left !important;", css_text)
        self.assertIn("vertical-align: top !important;", css_text)
        self.assertIn("background: transparent !important;", css_text)
        self.assertIn("background: transparent;", css_text)

        # Render survey page
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            res = self.client.get("/survey")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn('survey-table', html)
            self.assertIn('class="q-content-cell"', html)
            self.assertIn('class="q-result-cell"', html)

    def test_dual_top_bars_and_cascading_context(self):
        """Verify two top bars layout, user vs admin dropdowns, and cascading context resolution."""
        # 1. Standard user with single context (BE-ACV-Finance: Belgium, Finance, ACV)
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            res = self.client.get("/home")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)

            # Check Upper bar (.topbar-identity) and Lower bar (.nav)
            self.assertIn('class="topbar-identity"', html)
            self.assertIn('class="topbar-brand">UNI-COMPASS</a>', html)
            self.assertIn('class="nav"', html)
            self.assertIn('class="nav-inner"', html)

            # Standard user has single country, sector, organisation -> rendered as pills, not dropdowns
            self.assertIn('class="topbar-pill"', html)
            self.assertIn('BE-ACV-Finance', html)
            self.assertIn('ACV', html)
            self.assertIn('Belgium', html)
            self.assertIn('Finance', html)
            # The user button should NOT be a dropdown
            self.assertNotIn('id="userMenu"', html)

        # 2. Admin user (all countries, sectors, organisations)
        with self.client:
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)
            res_admin = self.client.get("/home")
            self.assertEqual(res_admin.status_code, 200)
            html_admin = res_admin.get_data(as_text=True)

            # User button shows admin (admin) as a pill, not a dropdown
            self.assertIn('topbar-pill', html_admin)
            self.assertIn('admin (admin)', html_admin)
            self.assertNotIn('id="userMenu"', html_admin)

            # Developper login check
            self.client.get("/logout", follow_redirects=True)
            self.client.post("/login", data={"user": "developper", "pincode": "1984"}, follow_redirects=True)
            res_dev = self.client.get("/home")
            self.assertEqual(res_dev.status_code, 200)
            html_dev = res_dev.get_data(as_text=True)
            self.assertIn('developper (admin)', html_dev)

            # Re-login as admin for next assertions
            self.client.get("/logout", follow_redirects=True)
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)

            # Country and Sector should be dropdowns
            self.assertIn('id="countryMenu"', html_admin)
            self.assertIn('id="sectorMenu"', html_admin)
            self.assertIn('HIVA KU Leuven', html_admin)

            # 3. Cascading context switching
            # Switch country to Belgium (which has multiple organizations)
            res_be = self.client.get("/set-context?country=Belgium", follow_redirects=True)
            self.assertEqual(res_be.status_code, 200)
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("country"), "Belgium")
                self.assertIn(sess.get("org") or sess.get("view_org"), ["ACV", "ABVV", "Test BE Finance 1", "Test BE Commerce 1", "Test BE ICT 1"])

            # Verify in page HTML that organisation is now a dropdown and only Belgian Commerce organisations appear
            html_be = res_be.get_data(as_text=True)
            self.assertIn('id="orgMenu"', html_be)
            self.assertIn("Test BE Commerce 1", html_be)
            # French or Italian orgs should NOT appear in the Belgian org dropdown
            self.assertNotIn(">Test FR Commerce 1<", html_be)
            self.assertNotIn(">Test IT Commerce 1<", html_be)

            # Switch sector to Finance while in Belgium
            res_be_fin = self.client.get("/set-context?sector=Finance", follow_redirects=True)
            self.assertEqual(res_be_fin.status_code, 200)
            html_be_fin = res_be_fin.get_data(as_text=True)
            # ACV is a Belgian Finance union, so it must now appear in the org dropdown
            self.assertIn("ACV", html_be_fin)
            self.assertNotIn(">Test FR Finance 1<", html_be_fin)

    def test_admin_survey_post_as_affiliate_and_unaffiliated(self):
        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin", "pincode": "1984"}, follow_redirects=True)

            # 1. Admin's own organisation (HIVA KU Leuven) is always in available_orgs even in Belgium / Finance
            res_ctx = self.client.get("/set-context?country=Belgium&sector=Finance", follow_redirects=True)
            self.assertEqual(res_ctx.status_code, 200)
            html_ctx = res_ctx.get_data(as_text=True)
            self.assertIn("HIVA KU Leuven", html_ctx)
            # "Europe" and "UNI" should be present in menus for admin
            self.assertIn(">Europe<", html_ctx)
            self.assertIn(">UNI<", html_ctx)
            # Organisation menu has None for unaffiliated
            self.assertIn(">None<", html_ctx)

            # 2. View as affiliate ACV
            res_acv = self.client.get("/set-context?org=ACV", follow_redirects=True)
            self.assertEqual(res_acv.status_code, 200)
            html_acv = res_acv.get_data(as_text=True)
            # User dropdown appears because admin is viewing affiliate org
            self.assertIn('id="userDropdown"', html_acv)
            self.assertIn('id="userMenu"', html_acv)
            self.assertIn("BE-ACV-Finance", html_acv)
            self.assertIn("admin (admin)", html_acv)

            # 3. Visit survey page as admin viewing ACV
            res_survey = self.client.get("/survey")
            self.assertEqual(res_survey.status_code, 200)
            html_survey = res_survey.get_data(as_text=True)
            self.assertIn("Viewing: <strong>ACV</strong> (Posting as: <strong>BE-ACV-Finance</strong>)", html_survey)

            # 4. Save survey response as affiliate
            post_survey = self.client.post("/survey?id=2026", data={
                "q1_rating": "5",
                "q2_rating": "4",
                "q3_rating": "5",
                "q4_text": "Saved by admin for ACV",
                "show_org": "1",
                "show_sector": "1",
                "show_country": "1",
                "show_user": "1",
            }, follow_redirects=True)
            self.assertEqual(post_survey.status_code, 200)

            # Verify response was stored under BE-ACV-Finance
            resp = get_user_survey_response("2026", "BE-ACV-Finance")
            self.assertIsNotNone(resp)
            self.assertEqual(resp["organisation"], "ACV")
            self.assertEqual(resp["q4_text"], "Saved by admin for ACV")

            # 5. Switch to post as admin: identification goes back to admin defaults in users.csv!
            res_act_admin = self.client.get("/set-context?user=admin", follow_redirects=True)
            self.assertEqual(res_act_admin.status_code, 200)
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("organisation"), "HIVA KU Leuven")
                self.assertEqual(sess.get("view_org"), "HIVA KU Leuven")
                self.assertEqual(sess.get("country"), "Europe")
                self.assertEqual(sess.get("sector"), "UNI")

            res_survey_as_admin = self.client.get("/survey")
            self.assertNotIn("Posting as:", res_survey_as_admin.get_data(as_text=True))

            post_admin = self.client.post("/survey?id=2026", data={
                "q1_rating": "3",
                "q2_rating": "3",
                "q3_rating": "3",
                "q4_text": "Admin survey note",
                "show_org": "1",
                "show_sector": "1",
                "show_country": "1",
                "show_user": "1",
            }, follow_redirects=True)
            self.assertEqual(post_admin.status_code, 200)

            resp_admin = get_user_survey_response("2026", "admin")
            self.assertIsNotNone(resp_admin)
            self.assertEqual(resp_admin["q4_text"], "Admin survey note")
            self.assertEqual(resp_admin["organisation"], "HIVA KU Leuven")
            self.assertEqual(resp_admin["country"], "Europe")
            self.assertEqual(resp_admin["sector"], "UNI")
            # Verify BE-ACV-Finance response is still intact
            resp_aff = get_user_survey_response("2026", "BE-ACV-Finance")
            self.assertEqual(resp_aff["q4_text"], "Saved by admin for ACV")

            # 6. Unaffiliated admin (Organisation: None, Country: None, Sector: None)
            res_none = self.client.get("/set-context?org=None", follow_redirects=True)
            self.assertEqual(res_none.status_code, 200)
            res_survey_none = self.client.get("/survey")
            self.assertIn("Viewing as: <strong>Unaffiliated (Admin)</strong>", res_survey_none.get_data(as_text=True))

            post_none = self.client.post("/survey?id=2026", data={
                "q1_rating": "4",
                "q2_rating": "4",
                "q3_rating": "4",
                "q4_text": "Unaffiliated admin note",
                "show_org": "1",
                "show_user": "1",
            }, follow_redirects=True)
            self.assertEqual(post_none.status_code, 200)

            resp_none = get_user_survey_response("2026", "admin")
            self.assertEqual(resp_none["q4_text"], "Unaffiliated admin note")
            self.assertEqual(resp_none["organisation"], "")

            # 7. When affiliate logs in, they see the response saved for them
            self.client.get("/logout", follow_redirects=True)
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "1234"}, follow_redirects=True)
            res_aff_survey = self.client.get("/survey")
            html_aff_survey = res_aff_survey.get_data(as_text=True)
            self.assertIn("Saved by admin for ACV", html_aff_survey)


if __name__ == "__main__":
    unittest.main()


