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

if __name__ == "__main__":
    unittest.main()
