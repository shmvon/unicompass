import unittest
import os
import json
from app import app, compute_cell_colors, ANNUAL_VARIABLES
from auth import authenticate, load_users, get_all_sectors, get_all_org_sector_combinations
from models import (
    init_db, get_annual_results, save_annual_results,
    get_agreements, save_agreements, get_companies, save_companies,
    compute_progress, export_all, generate_export_zip
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
        # Check system user
        u = authenticate("system@example.com", "0000")
        self.assertIsNotNone(u)
        self.assertTrue(u["is_admin"])
        self.assertIn("Commerce", u["sectors"])

        # Check BE-ACV-Finance
        u2 = authenticate("BE-ACV-Finance", "3456")
        self.assertIsNotNone(u2)
        self.assertEqual(u2["organisation"], "UNI-Belgium")
        self.assertEqual(u2["sector"], "Finance")

    def test_sector_scoped_data_storage(self):
        org = "TestOrg"
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

    def test_likert_heatmap_colors_blue_to_yellow(self):
        saved = {
            ("bargaining_climate", 2024): {"qual_value": "very favourable", "value": ""},
            ("bargaining_climate", 2025): {"qual_value": "neutral", "value": ""},
            ("bargaining_climate", 2026): {"qual_value": "very unfavourable", "value": ""},
            ("avg_pay_increase", 2024): {"value": "2.0", "qual_value": ""},
            ("avg_pay_increase", 2025): {"value": "4.0", "qual_value": ""},
        }
        colormap = compute_cell_colors(saved, ANNUAL_VARIABLES, [2024, 2025, 2026])
        # Very favourable -> High / Yellow (250, 204, 21)
        self.assertIn("rgba(250, 204, 21", colormap[("bargaining_climate", 2024)])
        # Neutral -> Interval 3 (132, 204, 22)
        self.assertIn("rgba(132, 204, 22", colormap[("bargaining_climate", 2025)])
        # Very unfavourable -> Low / Blue (37, 99, 235)
        self.assertIn("rgba(37, 99, 235", colormap[("bargaining_climate", 2026)])
        # Numeric min (Blue) and max (Yellow)
        self.assertIn("37", colormap[("avg_pay_increase", 2024)])
        self.assertIn("250", colormap[("avg_pay_increase", 2025)])

    def test_home_page_progress_bars_and_topbar(self):
        with self.client:
            self.client.post("/login", data={"user": "system@example.com", "pincode": "0000"}, follow_redirects=True)
            res = self.client.get("/home")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("Input progress", html)
            # Verify no numbers like '/10' in the progress table
            self.assertNotIn("/10", html)
            # Verify topbar Logout mention with hover username
            self.assertIn("Logout", html)
            self.assertIn('title="system@example.com"', html)

    def test_admin_dashboard_org_sector_combinations(self):
        combos = get_all_org_sector_combinations()
        self.assertTrue(len(combos) > 0)
        with self.client:
            self.client.post("/login", data={"user": "admin@example.com", "pincode": "1234"}, follow_redirects=True)
            res = self.client.get("/admin")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("UNI-Belgium", html)
            self.assertIn("ABVV", html)
            self.assertIn("Finance", html)
            self.assertIn("Commerce", html)

    def test_exports_scoped_and_admin(self):
        # Save sample data
        save_annual_results("UNI-Belgium", [{
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
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "3456"}, follow_redirects=True)
            res = self.client.get("/export")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("UNI-Belgium_Finance_annual_results.csv", html)

            # Regular user cannot download other files
            res_forbid = self.client.get("/export/download/ABVV_Finance_annual_results.csv")
            self.assertEqual(res_forbid.status_code, 403)

        with self.client:
            # Admin export view
            self.client.post("/login", data={"user": "admin@example.com", "pincode": "1234"}, follow_redirects=True)
            res_admin = self.client.get("/export")
            self.assertEqual(res_admin.status_code, 200)
            html_admin = res_admin.get_data(as_text=True)
            self.assertIn("Full Database (All Affiliates & Sectors)", html_admin)
            self.assertIn("unicompass.xlsx", html_admin)
            self.assertIn("annual_results.csv", html_admin)

    def test_set_sector_switching(self):
        with self.client:
            self.client.post("/login", data={"user": "system@example.com", "pincode": "0000"}, follow_redirects=True)
            res = self.client.get("/set-sector?sector=Finance", follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("sector"), "Finance")

    def test_pages_render_without_template_errors(self):
        with self.client:
            # Login as regular user
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "3456"}, follow_redirects=True)
            for path in ("/home", "/annual-results", "/major-agreements", "/major-companies", "/export"):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200, f"Failed rendering {path} for regular user")

        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin@example.com", "pincode": "1234"}, follow_redirects=True)
            for path in ("/home", "/annual-results", "/major-agreements", "/major-companies", "/export", "/admin"):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200, f"Failed rendering {path} for admin")

    def test_admin_editing_affiliate_preserves_focus(self):
        with self.client:
            # Login as admin
            self.client.post("/login", data={"user": "admin@example.com", "pincode": "1234"}, follow_redirects=True)

            # View annual results as affiliate
            res = self.client.get("/annual-results?org=UNI-Belgium")
            self.assertEqual(res.status_code, 200)
            self.assertIn("Viewing: <strong>UNI-Belgium</strong>", res.get_data(as_text=True))

            # Save annual results for affiliate
            post_res = self.client.post("/annual-results", data={
                "org": "UNI-Belgium",
                "value_collective_bargaining_coverage_2026": "88",
            }, follow_redirects=False)
            self.assertEqual(post_res.status_code, 302)
            self.assertIn("org=UNI-Belgium", post_res.headers.get("Location", ""))

            # Follow redirect and verify focus is preserved
            follow_res = self.client.get(post_res.headers["Location"])
            self.assertEqual(follow_res.status_code, 200)
            self.assertIn("Viewing: <strong>UNI-Belgium</strong>", follow_res.get_data(as_text=True))

            # Save agreements for affiliate
            ag_post = self.client.post("/major-agreements", data={
                "org": "UNI-Belgium",
                "year": "2026",
                "company_name_0": "Test CLA",
                "workers_affected_0": "500",
            }, follow_redirects=False)
            self.assertEqual(ag_post.status_code, 302)
            self.assertIn("org=UNI-Belgium", ag_post.headers.get("Location", ""))

            # Save companies for affiliate
            co_post = self.client.post("/major-companies", data={
                "org": "UNI-Belgium",
                "year": "2026",
                "company_name_0": "Test Company",
                "workers_0": "1000",
            }, follow_redirects=False)
            self.assertEqual(co_post.status_code, 302)
            self.assertIn("org=UNI-Belgium", co_post.headers.get("Location", ""))

    def test_security_users_csv_cannot_be_downloaded(self):
        with self.client:
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "3456"}, follow_redirects=True)
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
            # 1. Non-admin user is denied
            self.client.post("/login", data={"user": "BE-ACV-Finance", "pincode": "3456"}, follow_redirects=True)
            res_user = self.client.get("/admin/download-db")
            self.assertEqual(res_user.status_code, 302)

            # 2. Admin user receives timestamped database file
            self.client.post("/login", data={"user": "admin@example.com", "pincode": "1234"}, follow_redirects=True)
            
            # Check admin page has download button
            admin_page = self.client.get("/admin")
            self.assertEqual(admin_page.status_code, 200)
            self.assertIn("Download Database (unicompass.db)", admin_page.get_data(as_text=True))
            self.assertIn("/admin/download-db", admin_page.get_data(as_text=True))

            # Download the DB copy
            res_admin = self.client.get("/admin/download-db")
            self.assertEqual(res_admin.status_code, 200)
            self.assertEqual(res_admin.mimetype, "application/x-sqlite3")

            disposition = res_admin.headers.get("Content-Disposition", "")
            self.assertIn("unicompass_", disposition)
            self.assertIn(".db", disposition)

            # Validate that downloaded file is a consistent SQLite database with all tables
            import tempfile
            import sqlite3
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp.write(res_admin.data)
                tmp_path = tmp.name

            try:
                conn = sqlite3.connect(tmp_path)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cur.fetchall()]
                conn.close()
                self.assertIn("users", tables)
                self.assertIn("annual_results", tables)
                self.assertIn("agreements", tables)
                self.assertIn("companies", tables)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

if __name__ == "__main__":
    unittest.main()
