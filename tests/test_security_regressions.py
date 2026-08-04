#!/usr/bin/env python3
"""Small stdlib regressions for security-sensitive pure helpers."""
import pathlib
import sys
import unittest
import contextlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import access_store
import google_api
import hub_shell
from shopify_oauth import canonical_hmac_message


class AccessPathTests(unittest.TestCase):
    def test_decodes_and_normalizes_safe_path(self):
        self.assertEqual(
            access_store.canonical_request_path("/drop/My%20Files/photo.jpg?x=1"),
            "/drop/My Files/photo.jpg",
        )

    def test_rejects_encoded_traversal_and_backslash(self):
        unsafe = (
            "/drop/%2e%2e/ops/index.html",
            "/intake/%2E%2E/ops/ledger.html",
            "/drop/%5c..%5cops/index.html",
            "//drop/files",
            "/drop/%00/file",
            "/drop/%zz/file",
        )
        for path in unsafe:
            with self.subTest(path=path):
                self.assertIsNone(access_store.canonical_request_path(path))

    def test_authorization_never_uses_unsafe_raw_prefix(self):
        with mock.patch.object(access_store, "get_role", return_value="creator"):
            with mock.patch.object(access_store, "can_use", return_value=True):
                self.assertFalse(access_store.can_open_path(
                    "creator@example.com", "/drop/%2e%2e/ops/index.html"))
                self.assertTrue(access_store.can_open_path(
                    "creator@example.com", "/drop/files/reference.jpg"))

    def test_role_path_matrix(self):
        allowed = {
            "admin": {
                "/ops/", "/ops/access.html", "/ops/product-builder.html",
                "/ops/command.html", "/ops/order-form.html", "/ops/ledger.html",
                "/ops/reddit.html", "/ops/tools.html", "/ops/supplier.html",
                "/drop/files/reference.jpg", "/intake/",
            },
            "full": {
                "/ops/", "/ops/command.html", "/ops/order-form.html",
                "/ops/ledger.html", "/ops/reddit.html", "/ops/tools.html",
                "/drop/files/reference.jpg",
            },
            "creator": {
                "/ops/command.html", "/ops/reddit.html", "/ops/tools.html",
                "/drop/files/reference.jpg",
            },
            "orders": {"/ops/order-form.html"},
            "intake": {"/intake/"},
            "supplier": {"/ops/supplier.html"},
        }
        paths = {
            "/ops/", "/ops/access.html", "/ops/product-builder.html",
            "/ops/command.html", "/ops/order-form.html", "/ops/ledger.html",
            "/ops/reddit.html", "/ops/tools.html", "/ops/supplier.html",
            "/drop/files/reference.jpg", "/intake/",
        }
        for role, expected in allowed.items():
            with self.subTest(role=role):
                with mock.patch.object(
                        access_store, "get_role", return_value=role):
                    actual = {
                        path for path in paths
                        if access_store.can_open_path(
                            f"{role}@example.com", path)
                    }
                self.assertEqual(actual, expected)


class CommandSurfaceTests(unittest.TestCase):
    def test_shared_assistant_only_launches_role_checked_command(self):
        self.assertIn("/ops/agent/api/access/me", hub_shell.ASSIST_JS)
        self.assertIn("new URL('/ops/command.html'", hub_shell.ASSIST_JS)
        self.assertIn("u.searchParams.set('session',sid)", hub_shell.ASSIST_JS)
        self.assertNotIn("/send", hub_shell.ASSIST_JS)
        self.assertNotIn("/history", hub_shell.ASSIST_JS)
        self.assertNotIn("labsAskPanel", hub_shell.ASSIST_JS)


class ShopifyHmacTests(unittest.TestCase):
    def test_whatwg_form_encoding_vector(self):
        params = {
            "hmac": "excluded",
            "signature": "also excluded",
            "shop": "snow ☃ + plus",
            "state": "*~%",
        }
        self.assertEqual(
            canonical_hmac_message(params),
            "shop=snow%20%E2%98%83%20%2B%20plus&state=*%7E%25",
        )


class GoogleMirrorBatchTests(unittest.TestCase):
    def test_incompatible_live_headers_fail_instead_of_misaligning_rows(self):
        def values(_access, _sid, rng):
            if rng == "Customers!A1:ZZ1":
                return [["Customer ID", "Name", "Phone", "Tags"]]
            if rng == "Customers!A2:A2":
                return [["7"]]
            raise AssertionError(rng)

        meta = {"sheets": [{"properties": {
            "title": "Customers", "sheetId": 2,
        }}]}
        with mock.patch.object(
                google_api, "sheet_mutation_lock",
                return_value=contextlib.nullcontext()):
            with mock.patch.object(google_api, "_meta", return_value=meta):
                with mock.patch.object(google_api, "_values", side_effect=values):
                    with mock.patch.object(google_api, "_call") as call:
                        with self.assertRaises(google_api.GoogleError):
                            google_api.ensure_tab(
                                "token", "sid", "Customers",
                                ["Customer ID", "Name", "Tags", "Phone"])
        call.assert_not_called()

    def test_reads_each_key_column_once_and_writes_one_raw_batch(self):
        order_rows = [
            ("61", ["61", "old"]),
            ("62", ["62", "first"]),
            ("62", ["62", "latest"]),
        ]
        customer_rows = [["7", "Existing"], ["8", "New"]]

        def values(_access, _sid, rng):
            if rng == "Orders!A:A":
                return [["Order #"], ["61"]]
            if rng == "Customers!A:A":
                return [["Customer ID"], ["7"]]
            raise AssertionError(rng)

        with mock.patch.object(google_api, "ensure_order_sheet", return_value="sid"):
            with mock.patch.object(google_api, "ensure_tab"):
                with mock.patch.object(
                        google_api, "sheet_mutation_lock",
                        return_value=contextlib.nullcontext()):
                    with mock.patch.object(
                            google_api, "_values", side_effect=values) as reads:
                        with mock.patch.object(google_api, "_call") as call:
                            result = google_api.batch_upsert_mirror(
                                "token", order_rows, ["Customer ID", "Name"],
                                customer_rows)

        self.assertEqual(result, {"orders": 2, "customers": 2})
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(call.call_count, 1)
        args, kwargs = call.call_args
        self.assertTrue(args[0].endswith("/values:batchUpdate"))
        self.assertEqual(args[3]["valueInputOption"], "RAW")
        self.assertTrue(kwargs["retry_server_errors"])
        data = {item["range"]: item["values"][0] for item in args[3]["data"]}
        self.assertEqual(data["Orders!A2"], ["61", "old"])
        self.assertEqual(data["Orders!A3"], ["62", "latest"])
        self.assertEqual(data["Customers!A2"], ["7", "Existing"])
        self.assertEqual(data["Customers!A3"], ["8", "New"])


if __name__ == "__main__":
    unittest.main()
