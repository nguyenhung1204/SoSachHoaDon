"""Behavior checks for the downloadable 40HD OCR review report."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from visual_report_40hd import generate_visual_report


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class VisualReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_rows(self.root / "summary.csv", [
            {"engine": "tesseract", "confirmed_values": 2, "values_found": 1,
             "overall_value_presence_rate": 0.5, "inference_seconds_per_invoice": 1.25},
            {"engine": "easyocr", "confirmed_values": 2, "values_found": 2,
             "overall_value_presence_rate": 1, "inference_seconds_per_invoice": 2.5},
            {"engine": "paddleocr", "confirmed_values": 2, "values_found": 1,
             "overall_value_presence_rate": 0.5, "inference_seconds_per_invoice": 3},
        ])
        write_rows(self.root / "field_summary.csv", [
            {"engine": "tesseract", "field": "invoice_number", "field_label": "Số hóa đơn",
             "confirmed_values": 1, "values_found": 1, "value_presence_rate": 1},
            {"engine": "tesseract", "field": "item_description", "field_label": "Tên mặt hàng",
             "confirmed_values": 1, "values_found": 0, "value_presence_rate": 0},
        ])
        write_rows(self.root / "field_scores.csv", [
            {"engine": "tesseract", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
             "field": "invoice_number", "field_label": "Số hóa đơn", "line": "",
             "normalized": "123", "exact_value_found": 1},
            {"engine": "tesseract", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
             "field": "item_description", "field_label": "Tên mặt hàng", "line": 2,
             "normalized": "Bàn ghế", "exact_value_found": 0},
        ])
        write_rows(self.root / "predictions.csv", [
            {"engine": "tesseract", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
             "ocr_text": "Số: 123\nBàn hàng\n<script>alert(1)</script>"},
        ])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_item_keeps_invoice_line_and_nearby_ocr_text(self) -> None:
        generate_visual_report(self.root)
        with (self.root / "chi_tiet_hoa_don.csv").open(
            newline="", encoding="utf-8-sig"
        ) as stream:
            rows = list(csv.DictReader(stream))
        missing = next(row for row in rows if row["field"] == "item_description")
        self.assertEqual(missing["invoice_id"], "INV-0001")
        self.assertEqual(missing["line"], "2")
        self.assertEqual(missing["status"], "Không tìm thấy giá trị chuẩn")
        self.assertIn("Bàn hàng", missing["ocr_excerpt"])

    def test_precision_and_f1_are_not_fabricated_from_presence_hits(self) -> None:
        generate_visual_report(self.root)
        with (self.root / "bang_danh_gia.csv").open(
            newline="", encoding="utf-8-sig"
        ) as stream:
            rows = list(csv.DictReader(stream))
        tesseract = next(row for row in rows if row["engine"] == "tesseract")
        self.assertEqual(tesseract["value_presence_rate"], "0.5")
        self.assertEqual(tesseract["average_seconds_per_invoice"], "1.25")
        self.assertEqual(tesseract["precision"], "")
        self.assertEqual(tesseract["recall"], "")
        self.assertEqual(tesseract["f1_score"], "")

    def test_report_has_charts_filters_and_escapes_ocr_content(self) -> None:
        generate_visual_report(self.root)
        html = (self.root / "bao_cao_truc_quan.html").read_text(encoding="utf-8")
        self.assertIn("INV-0001", html)
        self.assertIn("HD_001.pdf", html)
        self.assertIn("Lọc kỹ thuật", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertTrue((self.root / "bieu_do_ty_le_nhan_dien.svg").is_file())
        self.assertTrue((self.root / "bieu_do_thoi_gian.svg").is_file())

    def test_structured_predictions_populate_precision_and_wrong_value(self) -> None:
        write_rows(self.root / "structured_summary.csv", [
            {"engine": "tesseract", "tp": 1, "fp": 1, "fn": 1,
             "precision": 0.5, "recall": 0.5, "f1": 0.5},
        ])
        write_rows(self.root / "structured_field_scores.csv", [
            {"engine": "tesseract", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
             "field": "item_description", "field_label": "Tên mặt hàng", "line": 2,
             "expected_value": "Bàn ghế", "predicted_value": "Bàn hàng",
             "outcome": "FP+FN", "tp": 0, "fp": 1, "fn": 1,
             "source_ocr_line": "Bàn hàng"},
        ])
        generate_visual_report(self.root)
        with (self.root / "bang_danh_gia.csv").open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        tesseract = next(row for row in rows if row["engine"] == "tesseract")
        self.assertEqual(tesseract["precision"], "0.5")
        self.assertEqual(tesseract["f1_score"], "0.5")
        with (self.root / "chi_tiet_hoa_don.csv").open(newline="", encoding="utf-8-sig") as stream:
            details = list(csv.DictReader(stream))
        self.assertEqual(details[0]["predicted_value"], "Bàn hàng")
        self.assertEqual(details[0]["status"], "Sai")

    def test_report_distinguishes_read_value_from_wrong_field_assignment(self) -> None:
        write_rows(self.root / "structured_summary.csv", [
            {"engine": "paddleocr", "tp": 2, "fp": 1, "fn": 1,
             "precision": 2 / 3, "recall": 2 / 3, "f1": 2 / 3},
        ])
        write_rows(self.root / "structured_field_scores.csv", [
            {"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
             "field": "unit_price", "field_label": "Đơn giá", "line": 2,
             "expected_value": "445454.55", "predicted_value": "",
             "source_ocr_line": "445,454.55", "assignment_status": "unassigned",
             "assignment_reason": "outside_known_columns", "source_page": 1,
             "bbox": "[345, 100, 410, 110]", "diagnostic_status": "read_but_unassigned",
             "raw_value_found": 1, "linked_prediction_field": "",
             "linked_prediction_line": "", "outcome": "FN", "tp": 0, "fp": 0, "fn": 1},
            {"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
             "field": "line_total", "field_label": "Thành tiền dòng", "line": 2,
             "expected_value": "490000", "predicted_value": "445,454.55",
             "source_ocr_line": "445,454.55", "assignment_status": "assigned",
             "assignment_reason": "header_columns_and_row_geometry", "source_page": 1,
             "bbox": "[345, 100, 410, 110]", "diagnostic_status": "wrong_assignment",
             "raw_value_found": 1, "linked_prediction_field": "unit_price",
             "linked_prediction_line": "2", "outcome": "FP+FN", "tp": 0, "fp": 1, "fn": 1},
        ])
        generate_visual_report(self.root)

        with (self.root / "chi_tiet_hoa_don.csv").open(
            newline="", encoding="utf-8-sig"
        ) as stream:
            details = list(csv.DictReader(stream))
        report = (self.root / "bao_cao_truc_quan.html").read_text(encoding="utf-8")

        self.assertEqual(details[0]["diagnostic_status"], "read_but_unassigned")
        self.assertEqual(details[0]["raw_value_found"], "1")
        self.assertEqual(details[0]["status"], "Đọc được nhưng chưa gán")
        self.assertEqual(details[1]["diagnostic_status"], "wrong_assignment")
        self.assertEqual(details[1]["linked_prediction_field"], "unit_price")
        self.assertEqual(details[1]["status"], "Gán nhầm trường/dòng")
        self.assertIn("Đọc được nhưng chưa gán", report)
        self.assertIn("Gán nhầm trường/dòng", report)
        self.assertIn("unit_price", report)


if __name__ == "__main__":
    unittest.main()
