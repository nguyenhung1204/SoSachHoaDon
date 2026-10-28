import json
import math
import shutil
import sqlite3
import tempfile
import threading
import unittest
import uuid
import urllib.request
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

import app


class HandlerResult:
    def _json(self, value, status=200):
        self.result = value


class LabelerTests(unittest.TestCase):
    def setUp(self):
        self.original_paths = {
            name: getattr(app, name)
            for name in ("STATE_DIR", "SOURCE_DIR", "PDF_DIR", "LABELS_JSON", "PAGE_DIR", "CROP_DIR", "EXPORT_DIR", "DB_PATH", "PORT")
        }
        self.temp_root = Path(tempfile.mkdtemp(prefix="test-", dir=app.APP_DIR / "state"))
        self.addCleanup(self._restore)
        self.use_state(self.temp_root / "state")
        app.initialize_database()

    def _restore(self):
        for name, value in self.original_paths.items():
            setattr(app, name, value)
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def use_state(self, state_dir):
        app.STATE_DIR = Path(state_dir)
        app.SOURCE_DIR = app.STATE_DIR / "source"
        app.PDF_DIR = app.SOURCE_DIR / "pdfs"
        app.LABELS_JSON = app.SOURCE_DIR / "40HD.json"
        app.PAGE_DIR = app.STATE_DIR / "pages"
        app.CROP_DIR = app.STATE_DIR / "crops"
        app.EXPORT_DIR = app.STATE_DIR.parent / "exports"
        app.DB_PATH = app.STATE_DIR / "labeler.sqlite3"
        for path in (app.STATE_DIR, app.PAGE_DIR, app.CROP_DIR, app.EXPORT_DIR):
            path.mkdir(parents=True, exist_ok=True)

    def create_crop(self, invoice, page_index, coords):
        result = HandlerResult()
        x0, y0, x1, y1 = coords
        app.Handler._create_crop(result, {
            "invoice_id": invoice["id"], "page": page_index,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        })
        return result.result["crop_id"]

    def set_label(self, crop_id, label):
        app.Handler._update_crop(HandlerResult(), crop_id, {
            "label": label, "field_type": "other", "confirmed": True,
        })

    def add_fixture_crop(self, invoice, label="A", crop_id=None):
        crop_id = crop_id or uuid.uuid4().hex
        image_name = f"{crop_id}.png"
        color = int(crop_id, 16) & 0xFFFFFF
        Image.new("RGB", (30, 12), (color >> 16, color >> 8 & 255, color & 255)).save(app.CROP_DIR / image_name)
        with app.connect() as connection:
            connection.execute(
                """INSERT INTO crops(id,invoice_id,page,x0,y0,x1,y1,pdf_x0,pdf_y0,pdf_x1,pdf_y1,
                   field_type,label,confirmed,image_name,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (crop_id, invoice["id"], 0, 10, 10, 40, 22, 1, 1, 2, 2,
                 "other", label, 1, image_name, app.now_iso(), app.now_iso()),
            )
        return crop_id

    def mark_scope_reviewed(self, scope, reviewer=None):
        reviewer = reviewer or {"duy": "Duy", "van": "Vân", "all": "Người tổng hợp"}[scope]
        for invoice_id in app.scope_invoice_ids(scope):
            app.set_invoice_review(invoice_id, True, reviewer)

    def set_test_splits(self):
        rows = app.invoice_rows()
        for index, invoice in enumerate(rows):
            split = "test" if index < 5 else "val" if index < 10 else "train"
            with app.connect() as connection:
                connection.execute("UPDATE invoices SET split=? WHERE id=?", (split, invoice["id"]))
        with app.connect() as connection:
            app.write_split_manifest(connection)

    def make_handoff_packages(self):
        self.set_test_splits()
        rows = app.invoice_rows()
        for index in (0, 6, 10, 20, 21, 22):
            self.add_fixture_crop(rows[index])
        self.mark_scope_reviewed("duy")
        duy_zip, _ = app.export_handoff("duy")
        self.mark_scope_reviewed("van")
        van_zip, _ = app.export_handoff("van")
        return duy_zip, van_zip

    def test_text_layer_extracts_only_selected_text_and_scan_falls_back(self):
        invoice = next(row for row in app.invoice_rows() if row["pdf"] == "HD_001.pdf")
        document = pdfium.PdfDocument(str(app.PDF_DIR / invoice["pdf"]))
        page = document[0]
        bbox = page.get_bbox()
        text_page = page.get_textpage()
        full_text = text_page.get_text_range()
        index = full_text.find("506572")
        self.assertGreaterEqual(index, 0)
        boxes = [text_page.get_charbox(index + offset) for offset in range(6)]
        text_page.close()
        document.close()

        _, pixel_width, pixel_height, _, _, _ = app.page_image(invoice["id"], 0)
        left = min(box[0] for box in boxes)
        right = max(box[2] for box in boxes)
        bottom = min(box[1] for box in boxes)
        top = max(box[3] for box in boxes)
        x0 = max(0, math.floor(left / bbox[2] * pixel_width) - 3)
        x1 = min(pixel_width, math.ceil(right / bbox[2] * pixel_width) + 3)
        y0 = max(0, math.floor((bbox[3] - top) / bbox[3] * pixel_height) - 3)
        y1 = min(pixel_height, math.ceil((bbox[3] - bottom) / bbox[3] * pixel_height) + 3)
        crop_id = self.create_crop(invoice, 0, (x0, y0, x1, y1))
        self.assertEqual(app.extract_crop_text(crop_id), "506572")

        scanned = next(row for row in app.invoice_rows() if row["pdf"] == "HD_173.pdf")
        _, width, height, _, _, _ = app.page_image(scanned["id"], 0)
        scan_crop_id = self.create_crop(scanned, 0, (10, 10, min(width, 200), min(height, 100)))
        with self.assertRaisesRegex(ValueError, "PDF scan"):
            app.extract_crop_text(scan_crop_id)

    def test_label_length_limit_is_90_characters(self):
        invoice = app.invoice_rows()[0]
        crop_id = self.create_crop(invoice, 0, (0, 0, 24, 16))
        for length in (25, 26, 65, 90):
            self.set_label(crop_id, "A" * length)
        label = "Công ty " * 11 + "AB"
        self.set_label(crop_id, label)
        with app.connect() as connection:
            saved = connection.execute("SELECT label,confirmed FROM crops WHERE id=?", (crop_id,)).fetchone()
        self.assertEqual(saved["label"], label)
        self.assertEqual(saved["confirmed"], 1)
        with self.assertRaisesRegex(ValueError, "90"):
            self.set_label(crop_id, "A" * 91)
        with app.connect() as connection:
            self.assertEqual(connection.execute("SELECT label FROM crops WHERE id=?", (crop_id,)).fetchone()["label"], label)

    def test_ui_has_pdf_text_copy_and_90_character_input(self):
        source = (app.APP_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('maxlength="90"', source)
        self.assertIn("/api/crop-text/", source)
        self.assertIn("Chép vào ô nhãn", source)

    def test_invoice_scopes_follow_source_order_and_verified_pdf_mapping(self):
        source = app.load_source_invoices()
        self.assertEqual(app.scope_invoice_ids("duy"), [row["id"] for row in source[:20]])
        self.assertEqual(app.scope_invoice_ids("van"), [row["id"] for row in source[20:]])
        self.assertEqual(app.scope_invoice_ids("all"), [row["id"] for row in source])
        self.assertEqual([(row["id"], row["pdf"]) for row in source[:2]], [
            ("INV-0001", "HD_001.pdf"), ("INV-0002", "HD_002.pdf"),
        ])
        self.assertEqual([(row["id"], row["pdf"]) for row in source[-2:]], [
            ("INV-0039", "HD_208.pdf"), ("INV-0040", "HD_209.pdf"),
        ])

    def test_invoice_review_allows_no_crops_and_requires_confirmed_crops(self):
        rows = app.invoice_rows()
        empty = rows[0]
        app.set_invoice_review(empty["id"], True, "Duy")
        self.assertEqual(app.get_invoice(empty["id"])["review_complete"], 1)
        self.assertEqual(app.get_invoice(empty["id"])["reviewed_by"], "Duy")

        invoice = rows[1]
        crop_id = self.create_crop(invoice, 0, (10, 10, 40, 22))
        with self.assertRaisesRegex(ValueError, "chưa xác nhận"):
            app.set_invoice_review(invoice["id"], True, "Duy")
        self.assertEqual(app.get_invoice(invoice["id"])["review_complete"], 0)
        self.set_label(crop_id, "A")
        app.set_invoice_review(invoice["id"], True, "Duy")
        self.assertEqual(app.get_invoice(invoice["id"])["review_complete"], 1)

    def test_crop_creation_invalidates_invoice_review(self):
        invoice = app.invoice_rows()[0]
        app.set_invoice_review(invoice["id"], True, "Duy")
        self.create_crop(invoice, 0, (10, 10, 40, 22))
        updated = app.get_invoice(invoice["id"])
        self.assertEqual(updated["review_complete"], 0)
        self.assertEqual(updated["reviewed_by"], "")

    def test_existing_database_migrates_review_columns_without_losing_labels_or_splits(self):
        source = app.load_source_invoices()
        splits, warning = app.default_splits(source)
        self.assertIsNone(warning)
        self.use_state(self.temp_root / "legacy" / "state")
        with sqlite3.connect(app.DB_PATH) as connection:
            connection.execute("CREATE TABLE invoices (id TEXT PRIMARY KEY,pdf TEXT NOT NULL,supplier_name TEXT NOT NULL DEFAULT '',seller_tax_code TEXT NOT NULL DEFAULT '',split TEXT NOT NULL CHECK(split IN ('train','val','test'))) ")
            connection.execute(
                "CREATE TABLE crops (id TEXT PRIMARY KEY,invoice_id TEXT NOT NULL,page INTEGER NOT NULL,x0 INTEGER NOT NULL,y0 INTEGER NOT NULL,x1 INTEGER NOT NULL,y1 INTEGER NOT NULL,pdf_x0 REAL NOT NULL,pdf_y0 REAL NOT NULL,pdf_x1 REAL NOT NULL,pdf_y1 REAL NOT NULL,field_type TEXT NOT NULL DEFAULT 'other',label TEXT NOT NULL DEFAULT '',confirmed INTEGER NOT NULL DEFAULT 0,image_name TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
            )
            for invoice in source:
                connection.execute(
                    "INSERT INTO invoices(id,pdf,split) VALUES(?,?,?)",
                    (invoice["id"], invoice["pdf"], splits[invoice["id"]]),
                )
            connection.execute(
                "INSERT INTO crops VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("a" * 32, source[0]["id"], 0, 10, 10, 40, 22, 1, 1, 2, 2, "other", "A", 1, "legacy.png", app.now_iso(), app.now_iso()),
            )
        Image.new("RGB", (30, 12), (1, 2, 3)).save(app.CROP_DIR / "legacy.png")

        app.initialize_database()
        with app.connect() as connection:
            invoice = connection.execute("SELECT review_complete,reviewed_by FROM invoices WHERE id=?", (source[0]["id"],)).fetchone()
            self.assertEqual(connection.execute("SELECT label FROM crops WHERE id=?", ("a" * 32,)).fetchone()[0], "A")
            split_counts = [tuple(row) for row in connection.execute("SELECT split,COUNT(*) FROM invoices GROUP BY split ORDER BY split")]
            self.assertEqual(split_counts, [
                ("test", 5), ("train", 30), ("val", 5),
            ])
        self.assertEqual(tuple(invoice), (0, ""))

    def test_handoff_export_includes_exact_scope_and_reviewed_empty_invoices(self):
        rows = app.invoice_rows()
        self.mark_scope_reviewed("duy")
        crop_id = self.create_crop(rows[1], 0, (10, 10, 40, 22))
        with self.assertRaisesRegex(ValueError, "Chưa rà soát"):
            app.export_handoff("duy")
        self.set_label(crop_id, "A")
        app.set_invoice_review(rows[1]["id"], True, "Duy")
        archive_path, unconfirmed = app.export_handoff("duy")
        self.assertEqual(unconfirmed, 0)
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["scope"], "duy")
            self.assertEqual(manifest["annotator"], "Duy")
            self.assertEqual(manifest["invoice_ids"], app.scope_invoice_ids("duy"))
            self.assertEqual(len(manifest["invoices"]), 20)
            self.assertEqual(len(manifest["crops"]), 1)
            self.assertTrue(all(row["review_complete"] for row in manifest["invoices"].values()))
            self.assertTrue(manifest["crops"][0]["image_sha256"])
            self.assertTrue(all(name == "manifest.json" or name == "README.txt" or name.startswith("crops/") for name in archive.namelist()))
            self.assertFalse(any(name.lower().endswith((".pdf", ".sqlite", ".json")) and name != "manifest.json" for name in archive.namelist()))

    def test_import_two_scopes_exports_recognition_and_duplicate_import_is_idempotent(self):
        duy_zip, van_zip = self.make_handoff_packages()
        self.use_state(self.temp_root / "aggregator" / "state")
        app.initialize_database()
        self.set_test_splits()
        self.assertEqual(app.import_handoff(duy_zip), {"imported": 3, "skipped_duplicates": 0, "reviewed_invoices": 20})
        self.assertEqual(app.import_handoff(duy_zip), {"imported": 0, "skipped_duplicates": 3, "reviewed_invoices": 20})
        self.assertEqual(app.import_handoff(van_zip), {"imported": 3, "skipped_duplicates": 0, "reviewed_invoices": 20})
        self.assertEqual(app.readiness()["reviewed_invoice_count"], 40)
        output = app.export_dataset()
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(len(archive.read("train.txt").decode("utf-8").splitlines()), 4)
            self.assertEqual(len(archive.read("val.txt").decode("utf-8").splitlines()), 1)
            self.assertEqual(len(archive.read("test.txt").decode("utf-8").splitlines()), 1)
            self.assertIn("manifest.csv", archive.namelist())
            self.assertFalse(any(name.lower().endswith((".pdf", ".sqlite")) for name in archive.namelist()))

    def test_handoff_import_rejects_wrong_scope_and_conflict_without_partial_writes(self):
        rows = app.invoice_rows()
        crop_id = self.add_fixture_crop(rows[1], crop_id="1" * 32)
        self.mark_scope_reviewed("duy")
        package, _ = app.export_handoff("duy")
        self.use_state(self.temp_root / "target" / "state")
        app.initialize_database()

        wrong_scope = self.temp_root / "wrong_scope.zip"
        with zipfile.ZipFile(package) as source, zipfile.ZipFile(wrong_scope, "w", zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                contents = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(contents.decode("utf-8"))
                    manifest["scope"] = "van"
                    contents = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                target.writestr(name, contents)
        with self.assertRaisesRegex(ValueError, "Phạm vi"):
            app.import_handoff(wrong_scope)
        with app.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM crops").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoices WHERE review_complete=1").fetchone()[0], 0)

        self.add_fixture_crop(app.invoice_rows()[1], label="B", crop_id=crop_id)
        with self.assertRaisesRegex(ValueError, "xung đột"):
            app.import_handoff(package)
        with app.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM crops").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoices WHERE review_complete=1").fetchone()[0], 0)

    def test_handoff_import_rejects_bad_source_split_and_crop_hashes_before_writing(self):
        rows = app.invoice_rows()
        self.add_fixture_crop(rows[0])
        self.mark_scope_reviewed("duy")
        package, _ = app.export_handoff("duy")
        self.use_state(self.temp_root / "hash-target" / "state")
        app.initialize_database()

        for filename, edit in (
            ("bad_source.zip", lambda manifest: manifest.update(source_labels_sha256="0" * 64)),
            ("bad_split.zip", lambda manifest: manifest["invoices"][manifest["invoice_ids"][0]].update(split="tampered")),
            ("bad_crop_hash.zip", lambda manifest: manifest["crops"][0].update(image_sha256="0" * 64)),
        ):
            damaged = self.temp_root / filename
            with zipfile.ZipFile(package) as source, zipfile.ZipFile(damaged, "w", zipfile.ZIP_DEFLATED) as target:
                for name in source.namelist():
                    contents = source.read(name)
                    if name == "manifest.json":
                        manifest = json.loads(contents.decode("utf-8"))
                        edit(manifest)
                        contents = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                    target.writestr(name, contents)
            with self.assertRaises(ValueError):
                app.import_handoff(damaged)
            with app.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM crops").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoices WHERE review_complete=1").fetchone()[0], 0)

    def test_recognition_export_requires_all_40_invoices_reviewed(self):
        with self.assertRaisesRegex(ValueError, "chưa rà soát"):
            app.export_dataset()

    def test_ui_exposes_annotator_scopes_review_and_merge_actions(self):
        html = (app.APP_DIR / "index.html").read_text(encoding="utf-8")
        script = (app.APP_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="scope-selector"', html)
        self.assertIn('id="invoice-review-button"', html)
        self.assertIn('id="handoff-import-file"', html)
        self.assertIn("Đã rà soát", script)
        self.assertIn("/review", script)
        self.assertIn("scope=", script)

    def test_http_scope_filter_and_invoice_review_route(self):
        from http.server import ThreadingHTTPServer

        previous_port = app.PORT
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        app.PORT = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{app.PORT}"
            with urllib.request.urlopen(base_url + "/api/invoices?scope=van") as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(len(result["invoices"]), 20)
            self.assertEqual(result["invoices"][0]["id"], app.scope_invoice_ids("van")[0])
            invoice_id = app.scope_invoice_ids("duy")[0]
            request = urllib.request.Request(
                base_url + f"/api/invoice/{invoice_id}/review",
                data=json.dumps({"review_complete": True, "reviewer": "Duy"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["reviewed_by"], "Duy")
            self.assertEqual(app.get_invoice(invoice_id)["review_complete"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            app.PORT = previous_port

    def test_text_layer_does_not_flatten_multiple_selected_lines(self):
        invoice = next(row for row in app.invoice_rows() if row["pdf"] == "HD_001.pdf")
        document = pdfium.PdfDocument(str(app.PDF_DIR / invoice["pdf"]))
        page = document[0]
        bbox = page.get_bbox()
        text_page = page.get_textpage()
        full_text = text_page.get_text_range()
        boxes = []
        for value in ("Công ty TNHH Thương Mại Tổng hợp HTV", "0108075931"):
            index = full_text.find(value)
            self.assertGreaterEqual(index, 0, f"Không tìm thấy {value!r} trong PDF.")
            boxes.extend(text_page.get_charbox(index + offset) for offset in range(len(value)))
        text_page.close()
        document.close()

        _, pixel_width, pixel_height, _, _, _ = app.page_image(invoice["id"], 0)
        x0 = max(0, math.floor(min(box[0] for box in boxes) / bbox[2] * pixel_width) - 3)
        x1 = min(pixel_width, math.ceil(max(box[2] for box in boxes) / bbox[2] * pixel_width) + 3)
        y0 = max(0, math.floor((bbox[3] - max(box[3] for box in boxes)) / bbox[3] * pixel_height) - 3)
        y1 = min(pixel_height, math.ceil((bbox[3] - min(box[1] for box in boxes)) / bbox[3] * pixel_height) + 3)
        crop_id = self.create_crop(invoice, 0, (x0, y0, x1, y1))
        with self.assertRaisesRegex(ValueError, "nhiều dòng"):
            app.extract_crop_text(crop_id)


if __name__ == "__main__":
    unittest.main()
