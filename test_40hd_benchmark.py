import base64
import csv
import inspect
import json
import tempfile
import unittest
from types import SimpleNamespace
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import sys

import run_40hd_benchmark as benchmark
from run_40hd_benchmark import (
    default_paths,
    iter_confirmed_values,
    load_engines,
    load_dataset,
    normalize_engine_regions,
    regions_to_text,
    run_benchmark,
    value_found,
    value_found_in_regions,
)


class FortyHdBenchmarkTests(unittest.TestCase):
    def test_engine_adapters_normalize_text_confidence_and_xyxy_boxes(self):
        cases = [
            (
                "tesseract",
                {
                    "text": ["445,454.55", "   "],
                    "conf": ["92.5", "-1"],
                    "left": [10, 0], "top": [20, 0],
                    "width": [80, 0], "height": [15, 0],
                },
                [{"text": "445,454.55", "confidence": 92.5, "bbox": [10, 20, 90, 35]}],
            ),
            (
                "easyocr",
                [([[3, 4], [19, 4], [19, 14], [3, 14]], "HÓA ĐƠN", 0.87)],
                [{"text": "HÓA ĐƠN", "confidence": 0.87, "bbox": [3, 4, 19, 14]}],
            ),
            (
                "paddleocr",
                {"rec_texts": ["Đơn giá"], "rec_scores": [0.91],
                 "rec_boxes": [[31, 44, 79, 57]]},
                [{"text": "Đơn giá", "confidence": 0.91, "bbox": [31, 44, 79, 57]}],
            ),
        ]

        for engine, raw, expected in cases:
            with self.subTest(engine=engine):
                self.assertEqual(normalize_engine_regions(engine, raw), expected)

    def test_run_benchmark_saves_page_regions_and_resumes_completed_engine_invoice(self):
        dataset = {"invoices": [{"id": "INV-0001", "pdf": "HD_001.pdf"}]}
        values = [{
            "invoice_id": "INV-0001", "pdf": "HD_001.pdf", "field": "invoice_number",
            "field_label": "Số hóa đơn", "line": "", "normalized": "123",
        }]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "page.png"
            image_path.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7WQAAAAASUVORK5CYII="
            ))
            (root / "HD_001.pdf").write_bytes(b"test PDF contents")
            calls = Counter()

            def fake_engine(engine_name):
                def recognize(path):
                    calls[engine_name] += 1
                    return [{"text": "Số hóa đơn: 123", "confidence": 0.95,
                             "bbox": [1, 2, 35, 14]}]
                return recognize

            engines = {name: fake_engine(name) for name in ("tesseract", "easyocr", "paddleocr")}
            with patch("run_40hd_benchmark.load_engines", return_value=engines), \
                 patch("run_40hd_benchmark.render_pdf", return_value=[image_path]):
                output = root / "run"
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=False)
                first_calls = calls.copy()
                (output / "checkpoints" / "easyocr" / "INV-0001.json").write_text(
                    "{truncated", encoding="utf-8"
                )
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=True)

            with (output / "ocr_regions.jsonl").open(encoding="utf-8") as stream:
                pages = [json.loads(line) for line in stream]
            self.assertEqual(len(pages), 3)
            self.assertEqual(pages[0]["page"], 1)
            self.assertEqual((pages[0]["width"], pages[0]["height"]), (1, 1))
            self.assertEqual(pages[0]["regions"][0]["bbox"], [1, 2, 35, 14])
            with (output / "predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
                predictions = list(csv.DictReader(stream))
            self.assertEqual(len(predictions), 3)
            self.assertEqual(predictions[0]["ocr_text"], "Số hóa đơn: 123")
            self.assertTrue(all(
                calls[name] == first_calls[name] + 1 + (name == "easyocr")
                for name in engines
            ))

    def test_run_benchmark_uses_close_ocr_boxes_for_space_grouped_money(self):
        dataset = {"invoices": [{"id": "INV-0001", "pdf": "HD_001.pdf"}]}
        values = [{
            "invoice_id": "INV-0001", "pdf": "HD_001.pdf", "field": "total_amount",
            "field_label": "Tổng tiền hóa đơn", "line": "", "normalized": "1234567.89",
        }]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "page.png"
            image_path.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7WQAAAAASUVORK5CYII="
            ))
            (root / "HD_001.pdf").write_bytes(b"test PDF contents")
            regions = [
                {"text": "1", "confidence": 0.9, "bbox": [100, 20, 105, 32]},
                {"text": "234", "confidence": 0.9, "bbox": [109, 20, 124, 32]},
                {"text": "567,89", "confidence": 0.9, "bbox": [128, 20, 160, 32]},
            ]
            engines = {name: (lambda _path: regions) for name in ("tesseract", "easyocr", "paddleocr")}
            output = root / "run"
            with patch("run_40hd_benchmark.load_engines", return_value=engines), \
                 patch("run_40hd_benchmark.render_pdf", return_value=[image_path]):
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=False)
            with (output / "field_scores.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                scores = list(csv.DictReader(stream))

        self.assertEqual(len(scores), 3)
        self.assertTrue(all(row["exact_value_found"] == "1" for row in scores))

    def test_resume_invalidates_checkpoint_when_same_name_pdf_content_changes(self):
        dataset = {"invoices": [{"id": "INV-0001", "pdf": "HD_001.pdf"}]}
        values = [{"invoice_id": "INV-0001", "pdf": "HD_001.pdf", "field": "invoice_number",
                   "field_label": "Số hóa đơn", "line": "", "normalized": "123"}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "page.png"
            pdf = root / "HD_001.pdf"
            image.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7WQAAAAASUVORK5CYII="
            ))
            pdf.write_bytes(b"first pdf content")
            calls = Counter()
            engines = {
                name: (lambda engine_name: lambda _path: calls.update([engine_name]) or [
                    {"text": "123", "confidence": 0.9, "bbox": [1, 1, 10, 8]}
                ])(name)
                for name in ("tesseract", "easyocr", "paddleocr")
            }
            output = root / "run"
            with patch("run_40hd_benchmark.load_engines", return_value=engines), \
                 patch("run_40hd_benchmark.render_pdf", return_value=[image]):
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=False)
                before_resume = calls.copy()
                pdf.write_bytes(b"changed content, same filename and page count")
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=True)

            self.assertTrue(all(calls[name] == before_resume[name] + 2 for name in engines))

    def test_resume_invalidates_only_engine_whose_runtime_fingerprint_changes(self):
        dataset = {"invoices": [{"id": "INV-0001", "pdf": "HD_001.pdf"}]}
        values = [{"invoice_id": "INV-0001", "pdf": "HD_001.pdf", "field": "invoice_number",
                   "field_label": "Số hóa đơn", "line": "", "normalized": "123"}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "page.png"
            (root / "HD_001.pdf").write_bytes(b"stable PDF")
            image.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7WQAAAAASUVORK5CYII="
            ))
            calls = Counter()
            engines = {
                name: (lambda engine_name: lambda _path: calls.update([engine_name]) or [
                    {"text": "123", "confidence": 0.9, "bbox": [1, 1, 10, 8]}
                ])(name)
                for name in ("tesseract", "easyocr", "paddleocr")
            }
            version = {"easyocr": "1.0"}
            def package(name):
                return version.get(name, "1.0")

            output = root / "run"
            with patch("run_40hd_benchmark.load_engines", return_value=engines), \
                 patch("run_40hd_benchmark.render_pdf", return_value=[image]), \
                 patch("run_40hd_benchmark.package_version", side_effect=package), \
                 patch("run_40hd_benchmark.tesseract_runtime_version", return_value="5.0"):
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=False)
                before_resume = calls.copy()
                version["easyocr"] = "2.0"
                run_benchmark(dataset, values, root, output, 300, "vie+eng", resume=True)

            self.assertEqual(calls["tesseract"], before_resume["tesseract"] + 1)
            self.assertEqual(calls["easyocr"], before_resume["easyocr"] + 2)
            self.assertEqual(calls["paddleocr"], before_resume["paddleocr"] + 1)

    def test_regions_to_text_keeps_words_on_the_same_ocr_line(self):
        regions = [
            {"text": "445,454.55", "confidence": 0.9, "bbox": [140, 30, 210, 45]},
            {"text": "Đơn", "confidence": 0.9, "bbox": [20, 30, 45, 45]},
            {"text": "giá", "confidence": 0.9, "bbox": [48, 30, 72, 45]},
            {"text": "Ngày", "confidence": 0.9, "bbox": [20, 80, 55, 95]},
        ]
        self.assertEqual(regions_to_text(regions), "Đơn giá 445,454.55\nNgày")

    def test_only_pdf_confirmed_nonempty_values_are_scored(self):
        dataset = {
            "invoices": [
                {
                    "id": "INV-0001",
                    "pdf": "HD_001.pdf",
                    "header": {
                        "invoice_number": {
                            "normalized": "506572",
                            "status": "Đã kiểm tra PDF",
                        },
                        "seller_name": {
                            "normalized": "Công ty Demo",
                            "status": "Cần đọc từ PDF",
                        },
                        "total_amount": {
                            "normalized": "0",
                            "status": "Không áp dụng",
                        },
                    },
                    "items": [
                        {
                            "line": 1,
                            "fields": {
                                "quantity": {
                                    "normalized": "2",
                                    "status": "Đã kiểm tra PDF",
                                },
                                "unit_price": {
                                    "normalized": "",
                                    "status": "Đã kiểm tra PDF",
                                },
                                "unit": {
                                    "normalized": "",
                                    "raw": "Dĩa",
                                    "status": "Đã kiểm tra PDF",
                                },
                            },
                        }
                    ],
                }
            ]
        }

        values = list(iter_confirmed_values(dataset))

        self.assertEqual(
            [(value["field"], value["normalized"]) for value in values],
            [("invoice_number", "506572"), ("quantity", "2"), ("unit", "Dĩa")],
        )
        self.assertEqual(values[1]["line"], 1)

    def test_date_and_formatted_money_match_the_printed_value(self):
        self.assertTrue(value_found("invoice_date", "2022-10-09", "Ngày 09/10/2022"))
        self.assertTrue(value_found("total_amount", "780000", "Tổng thanh toán: 780.000 đ"))

    def test_auto_device_uses_available_gpu_per_engine_and_cpu_for_tesseract(self):
        resolver = getattr(benchmark, "resolve_ocr_devices", None)
        self.assertTrue(callable(resolver), "runner must expose GPU/CPU device selection")
        self.assertEqual(
            resolver("auto", easyocr_gpu_available=True, paddle_gpu_available=False),
            {"tesseract": "cpu", "easyocr": "cuda", "paddleocr": "cpu"},
        )

    def test_gpu_device_request_requires_both_gpu_ocr_backends(self):
        resolver = getattr(benchmark, "resolve_ocr_devices", None)
        self.assertTrue(callable(resolver), "runner must expose GPU/CPU device selection")
        with self.assertRaisesRegex(RuntimeError, "EasyOCR and PaddleOCR"):
            resolver("gpu", easyocr_gpu_available=True, paddle_gpu_available=False)

    def test_cli_accepts_auto_device_mode(self):
        parser_factory = getattr(benchmark, "build_arg_parser", None)
        self.assertTrue(callable(parser_factory), "CLI must expose its supported device modes")
        self.assertEqual(parser_factory().parse_args(["--device", "auto"]).device, "auto")

    def test_load_engines_lazily_releases_one_gpu_model_before_reloading(self):
        self.assertIn("device", inspect.signature(load_engines).parameters)
        constructed = []

        class FakeEasyReader:
            def __init__(self, languages, gpu, verbose):
                constructed.append(("easyocr", gpu))

            def readtext(self, path, detail, paragraph):
                return []

        class FakePaddleReader:
            def __init__(self, **kwargs):
                constructed.append(("paddleocr", kwargs["device"]))

            def predict(self, path):
                return []

        fake_modules = {
            "pytesseract": SimpleNamespace(
                pytesseract=SimpleNamespace(tesseract_cmd=None),
                get_languages=lambda config: ["eng", "vie"],
                Output=SimpleNamespace(DICT="dict"),
            ),
            "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
            "paddle": SimpleNamespace(
                is_compiled_with_cuda=lambda: True,
                device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1)),
            ),
            "easyocr": SimpleNamespace(Reader=FakeEasyReader),
            "paddleocr": SimpleNamespace(PaddleOCR=FakePaddleReader),
        }
        with patch.dict(sys.modules, fake_modules):
            engines = load_engines("vie+eng", device="auto")
            self.assertEqual(engines["easyocr"].device, "cuda")
            self.assertEqual(engines["paddleocr"].device, "gpu:0")
            self.assertEqual(constructed, [])
            engines["easyocr"](Path("unused.png"))
            self.assertEqual(constructed, [("easyocr", True)])
            engines["easyocr"].release()
            engines["easyocr"](Path("unused.png"))
            self.assertEqual(constructed, [("easyocr", True), ("easyocr", True)])
            engines["paddleocr"](Path("unused.png"))
            self.assertEqual(constructed[-1], ("paddleocr", "gpu:0"))

    def test_auto_device_falls_back_to_cpu_when_gpu_model_initialization_fails(self):
        self.assertIn("device", inspect.signature(load_engines).parameters)
        constructed = []

        class FakeEasyReader:
            def __init__(self, languages, gpu, verbose):
                constructed.append(gpu)
                if gpu:
                    raise RuntimeError("out of memory")

            def readtext(self, path, detail, paragraph):
                return []

        fake_modules = {
            "pytesseract": SimpleNamespace(
                pytesseract=SimpleNamespace(tesseract_cmd=None),
                get_languages=lambda config: ["eng", "vie"],
                Output=SimpleNamespace(DICT="dict"),
            ),
            "torch": SimpleNamespace(cuda=SimpleNamespace(
                is_available=lambda: True, empty_cache=lambda: None,
            )),
            "paddle": SimpleNamespace(
                is_compiled_with_cuda=lambda: True,
                device=SimpleNamespace(cuda=SimpleNamespace(
                    device_count=lambda: 1, empty_cache=lambda: None,
                )),
            ),
            "easyocr": SimpleNamespace(Reader=FakeEasyReader),
            "paddleocr": SimpleNamespace(PaddleOCR=lambda **kwargs: None),
        }
        with patch.dict(sys.modules, fake_modules):
            engines = load_engines("vie+eng", device="auto")
            with self.assertWarnsRegex(RuntimeWarning, "falling back to CPU"):
                engines["easyocr"](Path("unused.png"))
        self.assertEqual(constructed, [True, False])
        self.assertEqual(engines["easyocr"].device, "cpu")

    def test_benchmark_releases_each_engine_after_processing_to_free_gpu_memory(self):
        dataset = {"invoices": [{"id": "INV-0001", "pdf": "HD_001.pdf"}]}
        values = [{"invoice_id": "INV-0001", "pdf": "HD_001.pdf", "field": "invoice_number",
                   "field_label": "Số hóa đơn", "line": "", "normalized": "123"}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "page.png"
            image.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7WQAAAAASUVORK5CYII="
            ))
            (root / "HD_001.pdf").write_bytes(b"test PDF contents")
            released = Counter()
            engines = {}
            for name in ("tesseract", "easyocr", "paddleocr"):
                def recognize(_path):
                    return [{"text": "123", "confidence": 0.9, "bbox": [1, 1, 10, 8]}]
                recognize.device = "cpu"
                recognize.release = lambda engine=name: released.update([engine])
                engines[name] = recognize
            with patch("run_40hd_benchmark.load_engines", return_value=engines), \
                 patch("run_40hd_benchmark.render_pdf", return_value=[image]):
                run_benchmark(dataset, values, root, root / "run", 300, "vie+eng")
        self.assertEqual(released, Counter({"tesseract": 1, "easyocr": 1, "paddleocr": 1}))

    def test_numeric_match_uses_whole_number_tokens(self):
        self.assertTrue(value_found("quantity", "5", "Số lượng 5,00"))
        self.assertFalse(value_found("quantity", "5", "Số lượng 15.000"))
        self.assertFalse(value_found("quantity", "331", "Tên hàng: LOGITECH M331"))

    def test_numeric_match_does_not_join_adjacent_table_cells(self):
        row_text = "Số lượng 2 445,454.55 8%"
        self.assertTrue(value_found("unit_price", "445454.55", row_text))
        self.assertFalse(value_found("total_amount", "2445454.55", row_text))

    def test_space_grouped_amount_uses_region_geometry_without_joining_adjacent_cells(self):
        whole_region = [{"regions": [
            {"text": "1 234 567,89", "bbox": [100, 20, 165, 32]},
        ]}]
        close_fragments = [{"regions": [
            {"text": "1", "bbox": [100, 20, 105, 32]},
            {"text": "234", "bbox": [109, 20, 124, 32]},
            {"text": "567,89", "bbox": [128, 20, 160, 32]},
        ]}]
        adjacent_cells = [{"regions": [
            {"text": "2", "bbox": [265, 60, 275, 70]},
            {"text": "445,454.55", "bbox": [325, 60, 359, 70]},
        ]}]

        self.assertTrue(value_found_in_regions("total_amount", "1234567.89", whole_region))
        self.assertTrue(value_found_in_regions("total_amount", "1234567.89", close_fragments))
        self.assertFalse(value_found_in_regions("total_amount", "2445454.55", adjacent_cells))

    def test_text_match_ignores_case_and_layout_whitespace(self):
        self.assertTrue(
            value_found(
                "item_description",
                "CHUỘT KHÔNG DÂY LOGITECH",
                "CHUỘT KHÔNG DÂY\nLOGITECH",
            )
        )

    def test_invoice_number_with_spaced_slash_is_matched_as_one_identifier(self):
        self.assertTrue(
            value_found("invoice_number", "1C22THT/506572", "Số: 1C22THT / 506572")
        )

    def test_current_40hd_dataset_has_40_pdfs_and_833_confirmed_values(self):
        json_path, pdf_root = default_paths()
        if not json_path.is_file() or not pdf_root.is_dir():
            self.skipTest("The private 40-invoice dataset is not present in this checkout")
        dataset, values = load_dataset(json_path, pdf_root)
        self.assertEqual(len(dataset["invoices"]), 40)
        self.assertEqual(len(values), 833)


if __name__ == "__main__":
    unittest.main()
