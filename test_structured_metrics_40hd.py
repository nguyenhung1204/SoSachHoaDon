"""Behavioral tests for independent OCR field extraction and slot scoring."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from structured_metrics_40hd import evaluate_run, extract_fields


def _region(text: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"text": text, "confidence": 0.9, "bbox": [x0, y0, x1, y1]}


class ExtractionTests(unittest.TestCase):
    def test_extracts_header_and_table_row_without_ground_truth(self):
        # A parser that returns a whole-page substring instead of a labeled value
        # or that confuses the buyer's tax code with the seller's fails here.
        text = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số (No.): 000123
Ngày (Date): 09/10/2022
Đơn vị bán hàng: Công ty TNHH Ánh Sao
Mã số thuế (Tax code): 0108075931
Người mua hàng: Nguyễn Văn A
Mã số thuế: 0312345678
STT | Tên hàng hóa | ĐVT | Số lượng | Đơn giá | Thuế suất | Thành tiền
1 | Chuột không dây | Cái | 2 | 300.000 | 10% | 660.000
Tổng cộng tiền thanh toán: 660.000 đồng
"""
        rows = extract_fields(text)
        slots = {(row["field"], row["line"]): row for row in rows}

        self.assertEqual(slots[("invoice_number", "")]["predicted_value"], "000123")
        self.assertEqual(slots[("invoice_date", "")]["predicted_value"], "2022-10-09")
        self.assertEqual(slots[("seller_name", "")]["predicted_value"], "Công ty TNHH Ánh Sao")
        self.assertEqual(slots[("seller_tax_code", "")]["predicted_value"], "0108075931")
        self.assertEqual(slots[("total_amount", "")]["predicted_value"], "660.000")
        self.assertEqual(slots[("item_description", "1")]["predicted_value"], "Chuột không dây")
        self.assertEqual(slots[("quantity", "1")]["predicted_value"], "2")
        self.assertEqual(slots[("unit_price", "1")]["predicted_value"], "300.000")
        self.assertEqual(slots[("tax_rate", "1")]["predicted_value"], "10%")
        self.assertEqual(slots[("line_total", "1")]["predicted_value"], "660.000")
        self.assertIn("1 | Chuột", slots[("quantity", "1")]["source_ocr_line"])

    def test_abstains_without_field_context_or_clear_item_row(self):
        # An arbitrary numeric string or OCR line without a row structure
        # must not become a confident field prediction.
        rows = extract_fields("Ký hiệu: 1C22THT\n000123\nChuột không dây\n2\n300.000")
        self.assertEqual(rows, [])

    def test_layout_parser_assigns_unit_price_to_invoice_line_two(self):
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng hóa, dịch vụ", 50, 20, 170, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
            ("1", 15, 60, 25, 70), ("Mặt hàng một", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("2", 265, 60, 275, 70),
            ("100", 355, 60, 390, 70), ("10%", 450, 60, 480, 70),
            ("110", 550, 60, 585, 70),
            ("2", 15, 100, 25, 110), ("Mặt hàng hai", 55, 100, 150, 110),
            ("Thùng", 205, 100, 235, 110), ("5", 265, 100, 275, 110),
            ("445,454.55", 345, 100, 410, 110), ("8%", 450, 100, 480, 110),
            ("490,000", 540, 100, 595, 110),
        ]
        page = {
            "page": 1, "width": 640, "height": 160,
            "regions": [_region(*item) for item in regions],
        }

        rows = extract_fields("", [page])
        slots = {(row["field"], row["line"]): row for row in rows}

        self.assertEqual(slots[("unit_price", "2")]["predicted_value"], "445,454.55")
        self.assertEqual(slots[("line_total", "2")]["predicted_value"], "490,000")
        self.assertEqual(slots[("unit_price", "2")]["source_page"], 1)
        self.assertEqual(slots[("unit_price", "2")]["bbox"], [345, 100, 410, 110])

    def test_layout_parser_joins_wrapped_description_and_continues_after_stt_restart(self):
        page1 = {
            "page": 1, "width": 640, "height": 220,
            "regions": [
                _region("STT", 10, 20, 30, 30), _region("Tên hàng", 50, 20, 160, 30),
                _region("ĐVT", 205, 20, 235, 30), _region("Số lượng", 250, 20, 300, 30),
                _region("Đơn giá", 340, 20, 400, 30), _region("Thuế suất", 435, 20, 495, 30),
                _region("Thành tiền", 530, 20, 600, 30),
                _region("1", 15, 60, 25, 70), _region("Hạt hướng", 55, 60, 140, 70),
                _region("dương", 55, 73, 105, 83), _region("Thùng", 205, 60, 235, 70),
                _region("5", 265, 60, 275, 70), _region("400", 350, 60, 390, 70),
                _region("8%", 450, 60, 480, 70), _region("2.160", 540, 60, 590, 70),
            ],
        }
        page2 = {
            "page": 2, "width": 640, "height": 220,
            "regions": [
                _region("1", 15, 60, 25, 70), _region("Nước", 55, 60, 140, 70),
                _region("Chai", 205, 60, 235, 70), _region("2", 265, 60, 275, 70),
                _region("100", 350, 60, 390, 70), _region("8%", 450, 60, 480, 70),
                _region("216", 540, 60, 590, 70),
            ],
        }

        rows = extract_fields("", [page1, page2])
        slots = {(row["field"], row["line"]): row for row in rows}

        self.assertEqual(slots[("item_description", "1")]["predicted_value"], "Hạt hướng dương")
        self.assertEqual(slots[("item_description", "2")]["predicted_value"], "Nước")
        self.assertEqual(slots[("unit_price", "2")]["predicted_value"], "100")

    def test_layout_parser_abstains_when_a_column_header_is_not_found(self):
        page = {
            "page": 1, "width": 640, "height": 160,
            "regions": [
                _region("STT", 10, 20, 30, 30), _region("Tên hàng", 50, 20, 160, 30),
                _region("ĐVT", 205, 20, 235, 30), _region("Số lượng", 250, 20, 300, 30),
                _region("Thuế suất", 435, 20, 495, 30), _region("Thành tiền", 530, 20, 600, 30),
                _region("1", 15, 60, 25, 70), _region("Mặt hàng", 55, 60, 150, 70),
                _region("Cái", 210, 60, 230, 70), _region("2", 265, 60, 275, 70),
                # Its center is near the quantity column, but the intervening
                # unit-price header is missing; do not merge it into quantity.
                _region("445,454.55", 325, 60, 359, 70), _region("8%", 450, 60, 480, 70),
                _region("490,000", 540, 60, 595, 70),
            ],
        }
        rows = extract_fields("", [page])
        self.assertFalse(any(row["field"] == "unit_price" for row in rows))
        quantity = next(row for row in rows if row["field"] == "quantity")
        self.assertEqual(quantity["predicted_value"], "2")

    def test_layout_parser_stops_at_invoice_total_and_ignores_footer_numbers(self):
        page = {
            "page": 1, "width": 640, "height": 180,
            "regions": [
                _region("STT", 10, 20, 30, 30), _region("Tên hàng", 50, 20, 160, 30),
                _region("ĐVT", 205, 20, 235, 30), _region("Số lượng", 250, 20, 300, 30),
                _region("Đơn giá", 340, 20, 400, 30), _region("Thuế suất", 435, 20, 495, 30),
                _region("Thành tiền", 530, 20, 600, 30),
                _region("1", 15, 60, 25, 70), _region("Widget", 55, 60, 150, 70),
                _region("Cái", 210, 60, 230, 70), _region("2", 265, 60, 275, 70),
                _region("100", 355, 60, 390, 70), _region("10%", 450, 60, 480, 70),
                _region("220", 550, 60, 585, 70),
                _region("Tổng cộng tiền thanh toán", 55, 100, 230, 110),
                _region("220", 355, 100, 390, 110), _region("10%", 450, 100, 480, 110),
                _region("Footer", 50, 140, 100, 150), _region("2", 15, 140, 25, 150),
                _region("220", 355, 140, 390, 150), _region("10%", 450, 140, 480, 150),
            ],
        }

        rows = extract_fields("", [page])

        self.assertEqual({row["line"] for row in rows if row["field"] == "unit_price"}, {"1"})

    def test_layout_parser_marks_unanchored_row_ambiguous_when_stt_gap_suggests_ocr_missed_a_row(self):
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
            ("1", 15, 60, 25, 70), ("Hàng một", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("1", 265, 60, 275, 70),
            ("100", 355, 60, 390, 70), ("10%", 450, 60, 480, 70),
            ("110", 550, 60, 585, 70),
            # OCR sees a complete row but misses its printed STT. The later
            # anchor jumps from 1 to 4, so one or more rows may be missing.
            ("Hàng ba", 55, 100, 150, 110), ("Cái", 210, 100, 230, 110),
            ("1", 265, 100, 275, 110), ("300", 355, 100, 390, 110),
            ("10%", 450, 100, 480, 110), ("330", 550, 100, 585, 110),
            ("4", 15, 140, 25, 150), ("Hàng bốn", 55, 140, 150, 150),
            ("Cái", 210, 140, 230, 150), ("1", 265, 140, 275, 150),
            ("400", 355, 140, 390, 150), ("10%", 450, 140, 480, 150),
            ("440", 550, 140, 585, 150),
        ]
        page = {"page": 1, "width": 640, "height": 180,
                "regions": [_region(*item) for item in regions]}

        rows = extract_fields("", [page])
        unanchored_price = next(row for row in rows
                                if row["field"] == "unit_price"
                                and row["predicted_value"] == "300")
        anchored_price = next(row for row in rows
                              if row["field"] == "unit_price"
                              and row["predicted_value"] == "400")

        self.assertEqual(unanchored_price["line"], "")
        self.assertEqual(unanchored_price["assignment_status"], "ambiguous")
        self.assertEqual(unanchored_price["assignment_reason"],
                         "row_number_not_confident_missing_or_unanchored_rows")
        self.assertEqual(anchored_price["line"], "4")
        self.assertEqual(anchored_price["assignment_status"], "assigned")

    def test_layout_parser_infers_missing_stt_only_between_exact_sequential_anchors(self):
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
            ("1", 15, 60, 25, 70), ("Hàng một", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("1", 265, 60, 275, 70),
            ("100", 355, 60, 390, 70), ("10%", 450, 60, 480, 70),
            ("110", 550, 60, 585, 70),
            ("Hàng hai", 55, 100, 150, 110), ("Cái", 210, 100, 230, 110),
            ("1", 265, 100, 275, 110), ("200", 355, 100, 390, 110),
            ("10%", 450, 100, 480, 110), ("220", 550, 100, 585, 110),
            ("3", 15, 140, 25, 150), ("Hàng ba", 55, 140, 150, 150),
            ("Cái", 210, 140, 230, 150), ("1", 265, 140, 275, 150),
            ("300", 355, 140, 390, 150), ("10%", 450, 140, 480, 150),
            ("330", 550, 140, 585, 150),
        ]
        page = {"page": 1, "width": 640, "height": 180,
                "regions": [_region(*item) for item in regions]}

        rows = extract_fields("", [page])
        inferred_price = next(row for row in rows
                              if row["field"] == "unit_price"
                              and row["predicted_value"] == "200")

        self.assertEqual(inferred_price["line"], "2")
        self.assertEqual(inferred_price["assignment_status"], "assigned")

    def test_ambiguous_row_does_not_shift_a_later_readable_stt_anchor(self):
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
            ("1", 15, 60, 25, 70), ("Hàng một", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("1", 265, 60, 275, 70),
            ("100", 355, 60, 390, 70), ("10%", 450, 60, 480, 70),
            ("110", 550, 60, 585, 70),
            ("Hàng chưa rõ STT", 55, 100, 160, 110), ("Cái", 210, 100, 230, 110),
            ("1", 265, 100, 275, 110), ("200", 355, 100, 390, 110),
            ("10%", 450, 100, 480, 110), ("220", 550, 100, 585, 110),
            ("2", 15, 140, 25, 150), ("Hàng hai", 55, 140, 150, 150),
            ("Cái", 210, 140, 230, 150), ("1", 265, 140, 275, 150),
            ("300", 355, 140, 390, 150), ("10%", 450, 140, 480, 150),
            ("330", 550, 140, 585, 150),
        ]
        page = {"page": 1, "width": 640, "height": 180,
                "regions": [_region(*item) for item in regions]}

        rows = extract_fields("", [page])
        uncertain_price = next(row for row in rows
                               if row["field"] == "unit_price"
                               and row["predicted_value"] == "200")
        anchored_price = next(row for row in rows
                              if row["field"] == "unit_price"
                              and row["predicted_value"] == "300")

        self.assertEqual(uncertain_price["assignment_status"], "ambiguous")
        self.assertEqual(anchored_price["line"], "2")
        self.assertEqual(anchored_price["assignment_status"], "assigned")


def _label(value: str, status: str = "Đã kiểm tra PDF") -> dict:
    return {"raw": value, "normalized": value, "status": status}


class ScoringTests(unittest.TestCase):
    def test_formatted_money_is_scored_correct_for_coordinate_assigned_price(self):
        fields = {
            "item_description": "Widget", "unit": "Cái", "quantity": "1",
            "unit_price": "445454.55", "tax_rate": "8%", "line_total": "490000",
        }
        dataset = {"invoices": [{
            "id": "INV-0019", "pdf": "HD_019.pdf", "header": {},
            "items": [{"line": 2, "fields": {
                field: _label(value) for field, value in fields.items()
            }}],
        }]}
        header = [
            ("STT", 10, 20, 30, 30), ("Tên hàng hóa", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
        ]
        content = [
            ("2", 15, 60, 25, 70), ("Widget", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("1", 265, 60, 275, 70),
            ("445,454.55", 345, 60, 410, 70), ("8%", 450, 60, 480, 70),
            ("490,000", 540, 60, 595, 70),
        ]
        pages = [{"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
                  "page": 1, "width": 640, "height": 120,
                  "regions": [_region(*item) for item in header + content]}]
        prediction = [{"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
                       "inference_seconds": "1", "ocr_text": "445,454.55"}]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(prediction[0]))
                writer.writeheader()
                writer.writerows(prediction)
            with (root / "ocr_regions.jsonl").open("w", encoding="utf-8") as stream:
                stream.write(json.dumps(pages[0]) + "\n")
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out",
                         regions_jsonl=root / "ocr_regions.jsonl")
            with (root / "out" / "structured_field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                scores = list(csv.DictReader(stream))

        price = next(row for row in scores if row["field"] == "unit_price")
        self.assertEqual(price["line"], "2")
        self.assertEqual(price["expected_value"], "445454.55")
        self.assertEqual(price["predicted_value"], "445,454.55")
        self.assertEqual(price["outcome"], "TP")
        self.assertEqual(price["diagnostic_status"], "correct")

    def test_scores_read_but_unassigned_separately_from_not_read(self):
        dataset = {"invoices": [{
            "id": "INV-0001", "pdf": "HD_001.pdf", "header": {},
            "items": [
                {"line": 1, "fields": {"unit_price": _label("445454.55")}},
                {"line": 2, "fields": {"unit_price": _label("700000")}},
            ],
        }]}
        source = [{
            "engine": "paddleocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
            "inference_seconds": "1", "ocr_text": "OCR line: 445,454.55",
        }]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(source[0]))
                writer.writeheader()
                writer.writerows(source)
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out")
            with (root / "out" / "structured_field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                detail = {row["line"]: row for row in csv.DictReader(stream)}

        self.assertEqual(detail["1"]["diagnostic_status"], "read_but_unassigned")
        self.assertEqual(detail["1"]["raw_value_found"], "1")
        self.assertEqual(detail["1"]["outcome"], "FN")
        self.assertEqual(detail["2"]["diagnostic_status"], "not_read")
        self.assertEqual(detail["2"]["raw_value_found"], "0")

    def test_read_but_unassigned_retains_matching_source_page_and_bbox(self):
        dataset = {"invoices": [{
            "id": "INV-0019", "pdf": "HD_019.pdf", "header": {},
            "items": [{"line": 2, "fields": {
                "unit_price": _label("445454.55"),
            }}],
        }]}
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Thuế suất", 435, 20, 495, 30), ("Thành tiền", 530, 20, 600, 30),
            ("2", 15, 60, 25, 70), ("Widget", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("2", 265, 60, 275, 70),
            ("445,454.55", 325, 60, 359, 70), ("8%", 450, 60, 480, 70),
            ("490,000", 540, 60, 595, 70),
        ]
        page = {"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
                "page": 1, "width": 640, "height": 120,
                "regions": [_region(*item) for item in regions]}
        prediction = [{"engine": "paddleocr", "invoice_id": "INV-0019", "pdf": "HD_019.pdf",
                       "inference_seconds": "1", "ocr_text": "445,454.55"}]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(prediction[0]))
                writer.writeheader()
                writer.writerows(prediction)
            (root / "ocr_regions.jsonl").write_text(json.dumps(page) + "\n", encoding="utf-8")
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out",
                         regions_jsonl=root / "ocr_regions.jsonl")
            with (root / "out" / "structured_field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                detail = next(row for row in csv.DictReader(stream) if row["field"] == "unit_price")

        self.assertEqual(detail["diagnostic_status"], "read_but_unassigned")
        self.assertEqual(detail["raw_value_found"], "1")
        self.assertEqual(detail["source_page"], "1")
        self.assertEqual(json.loads(detail["bbox"]), [325, 60, 359, 70])
        self.assertEqual(detail["assignment_reason"], "raw_value_found_but_no_confident_cell")

    def test_ambiguous_unassigned_row_is_diagnostic_only_not_a_false_positive(self):
        dataset = {"invoices": [{
            "id": "INV-0001", "pdf": "HD_001.pdf", "header": {},
            "items": [{"line": line, "fields": {
                "unit_price": _label(value),
            }} for line, value in [(1, "100"), (2, "200"), (3, "300"), (4, "400")]],
        }]}
        regions = [
            ("STT", 10, 20, 30, 30), ("Tên hàng", 50, 20, 160, 30),
            ("ĐVT", 205, 20, 235, 30), ("Số lượng", 250, 20, 300, 30),
            ("Đơn giá", 340, 20, 400, 30), ("Thuế suất", 435, 20, 495, 30),
            ("Thành tiền", 530, 20, 600, 30),
            ("1", 15, 60, 25, 70), ("Hàng một", 55, 60, 150, 70),
            ("Cái", 210, 60, 230, 70), ("1", 265, 60, 275, 70),
            ("100", 355, 60, 390, 70), ("10%", 450, 60, 480, 70),
            ("110", 550, 60, 585, 70),
            ("Hàng chưa rõ STT", 55, 100, 160, 110), ("Cái", 210, 100, 230, 110),
            ("1", 265, 100, 275, 110), ("200", 355, 100, 390, 110),
            ("10%", 450, 100, 480, 110), ("220", 550, 100, 585, 110),
            ("4", 15, 140, 25, 150), ("Hàng bốn", 55, 140, 150, 150),
            ("Cái", 210, 140, 230, 150), ("1", 265, 140, 275, 150),
            ("400", 355, 140, 390, 150), ("10%", 450, 140, 480, 150),
            ("440", 550, 140, 585, 150),
        ]
        page = {"engine": "paddleocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
                "page": 1, "width": 640, "height": 180,
                "regions": [_region(*item) for item in regions]}
        prediction = [{"engine": "paddleocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
                       "inference_seconds": "1", "ocr_text": "100 200 400"}]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(prediction[0]))
                writer.writeheader()
                writer.writerows(prediction)
            (root / "ocr_regions.jsonl").write_text(json.dumps(page) + "\n", encoding="utf-8")
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out",
                         regions_jsonl=root / "ocr_regions.jsonl")
            with (root / "out" / "structured_field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                scores = {row["line"]: row for row in csv.DictReader(stream)}
            with (root / "out" / "structured_field_predictions.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                extracted = list(csv.DictReader(stream))

        self.assertEqual(scores["2"]["diagnostic_status"], "ambiguous")
        self.assertEqual(scores["2"]["assignment_status"], "ambiguous")
        self.assertEqual(scores["2"]["outcome"], "FN")
        self.assertEqual(scores["2"]["fp"], "0")
        candidate = next(row for row in extracted if row["predicted_value"] == "200")
        self.assertEqual(candidate["line"], "")
        self.assertEqual(candidate["assignment_status"], "ambiguous")

    def test_wrong_cell_assignment_links_source_and_destination_slots(self):
        dataset = {"invoices": [{
            "id": "INV-0001", "pdf": "HD_001.pdf", "header": {},
            "items": [{"line": 1, "fields": {
                "item_description": _label("Widget"),
                "unit": _label("Cái"),
                "quantity": _label("1"),
                "unit_price": _label("100"), "line_total": _label("110"),
                "tax_rate": _label("10%"),
            }}],
        }]}
        source = [{
            "engine": "paddleocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
            "inference_seconds": "1",
            "ocr_text": "STT | Tên hàng | ĐVT | Số lượng | Đơn giá | Thuế suất | Thành tiền\n"
                        "1 | Widget | Cái | 1 | 110 | 10% | 100",
        }]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(source[0]))
                writer.writeheader()
                writer.writerows(source)
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out")
            with (root / "out" / "structured_field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                details = {row["field"]: row for row in csv.DictReader(stream)}
            with (root / "out" / "structured_summary.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                summary = next(csv.DictReader(stream))

        self.assertEqual(details["unit_price"]["diagnostic_status"], "wrong_assignment")
        self.assertEqual(details["unit_price"]["linked_prediction_field"], "line_total")
        self.assertEqual(details["line_total"]["diagnostic_status"], "wrong_assignment")
        self.assertEqual([summary[key] for key in ("tp", "fp", "fn")], ["4", "2", "2"])

    def test_scores_correct_wrong_missing_and_explicit_absence(self):
        # Wrong value costs one FP and one FN; a value for a documented
        # absent field costs FP. These are hand-derived expected counts.
        dataset = {
            "invoices": [{
                "id": "INV-0001", "pdf": "HD_001.pdf",
                "header": {
                    "invoice_number": _label("000123"),
                    "seller_tax_code": _label("", "Không có trên hóa đơn"),
                },
                "items": [{"line": 1, "fields": {
                    "item_description": _label("", "Cần đọc từ PDF"),
                    "unit": _label("Cái"),
                    "quantity": _label("2"),
                    "unit_price": _label("", "Cần đọc từ PDF"),
                    "tax_rate": _label("", "Cần đọc từ PDF"),
                    "line_total": _label("", "Cần đọc từ PDF"),
                }}],
            }],
        }
        rows = [
            {
                "engine": "tesseract", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
                "inference_seconds": "1.25",
                "ocr_text": "Số (No.): 000123\nMã số thuế: 0108075931\n1 | Chuột | Cái | 3 | 300.000 | 10% | 990.000",
            },
            {
                "engine": "easyocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
                "inference_seconds": "2.50", "ocr_text": "blurred text",
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset_path = root / "40HD.json"
            prediction_path = root / "predictions.csv"
            dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
            with prediction_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            evaluate_run(prediction_path, dataset_path, root / "out")
            with (root / "out" / "structured_summary.csv").open(encoding="utf-8-sig", newline="") as stream:
                summary = {row["engine"]: row for row in csv.DictReader(stream)}
            with (root / "out" / "structured_field_scores.csv").open(encoding="utf-8-sig", newline="") as stream:
                detail = list(csv.DictReader(stream))
            with (root / "out" / "structured_field_predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
                predictions = list(csv.DictReader(stream))

        self.assertEqual(
            [int(summary["tesseract"][key]) for key in ("tp", "fp", "fn")],
            [2, 2, 1],
        )
        self.assertAlmostEqual(float(summary["tesseract"]["precision"]), 1 / 2)
        self.assertAlmostEqual(float(summary["tesseract"]["recall"]), 2 / 3)
        self.assertAlmostEqual(float(summary["tesseract"]["f1"]), 4 / 7)
        self.assertEqual(
            [int(summary["easyocr"][key]) for key in ("tp", "fp", "fn")],
            [0, 0, 3],
        )
        self.assertEqual(float(summary["tesseract"]["avg_ocr_seconds_per_invoice"]), 1.25)
        self.assertTrue(any(row["outcome"] == "FP+FN" and row["field"] == "quantity" for row in detail))
        self.assertTrue(any(row["outcome"] == "FP" and row["field"] == "seller_tax_code" for row in detail))
        self.assertTrue(any(row["predicted_value"] == "000123" for row in predictions))

    def test_does_not_score_unverified_label_or_guess_its_value(self):
        dataset = {"invoices": [{
            "id": "INV-0001", "pdf": "HD_001.pdf",
            "header": {"invoice_number": _label("", "Cần đọc từ PDF")},
            "items": [],
        }]}
        rows = [{
            "engine": "paddleocr", "invoice_id": "INV-0001", "pdf": "HD_001.pdf",
            "inference_seconds": "1", "ocr_text": "Số hóa đơn: 000123",
        }]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "40HD.json").write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
            with (root / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            evaluate_run(root / "predictions.csv", root / "40HD.json", root / "out")
            with (root / "out" / "structured_field_scores.csv").open(encoding="utf-8-sig", newline="") as stream:
                detail = list(csv.DictReader(stream))
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["outcome"], "UNSCORED")
        self.assertEqual((detail[0]["tp"], detail[0]["fp"], detail[0]["fn"]), ("0", "0", "0"))


if __name__ == "__main__":
    unittest.main()
