import json
import math
import shutil
import tempfile
import unittest
import uuid
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
            for name in ("STATE_DIR", "SOURCE_DIR", "PDF_DIR", "LABELS_JSON", "PAGE_DIR", "CROP_DIR", "EXPORT_DIR", "DB_PATH")
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

    def test_handoff_import_merge_duplicate_and_conflict(self):
        rows = app.invoice_rows()
        chosen = {split: next(row for row in rows if row["split"] == split) for split in app.SPLITS}
        for index, split in enumerate(app.SPLITS):
            invoice = chosen[split]
            name = f"test_{index}.png"
            Image.new("RGB", (24, 16), (index * 40, 0, 0)).save(app.CROP_DIR / name, format="PNG")
            with app.connect() as connection:
                connection.execute(
                    """INSERT INTO crops(id,invoice_id,page,x0,y0,x1,y1,pdf_x0,pdf_y0,pdf_x1,pdf_y1,
                       field_type,label,confirmed,image_name,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"{index + 1:032x}", invoice["id"], 0, 0, 0, 24, 16, 0, 0, 1, 1,
                     "other", "1", 1, name, app.now_iso(), app.now_iso()),
                )

        archive_path, skipped_unconfirmed = app.export_handoff()
        self.assertEqual(skipped_unconfirmed, 0)
        friend_root = self.temp_root / "friend"
        self.use_state(friend_root / "state")
        app.initialize_database()
        imported = app.import_handoff(archive_path)
        self.assertEqual(imported, {"imported": 3, "skipped_duplicates": 0})
        duplicate = app.import_handoff(archive_path)
        self.assertEqual(duplicate, {"imported": 0, "skipped_duplicates": 3})

        conflict_path = friend_root / "conflict.zip"
        with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(conflict_path, "w", zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                contents = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(contents.decode("utf-8"))
                    manifest["crops"][0]["label"] = "11"
                    contents = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                target.writestr(name, contents)
        with self.assertRaisesRegex(ValueError, "xung đột"):
            app.import_handoff(conflict_path)
        with app.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM crops").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
