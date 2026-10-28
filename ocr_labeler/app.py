from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATE_DIR = APP_DIR / "state"
SOURCE_DIR = STATE_DIR / "source"
PDF_DIR = SOURCE_DIR / "pdfs"
LABELS_JSON = SOURCE_DIR / "40HD.json"
BUNDLE_PATH = PROJECT_DIR / "40HD_colab_bundle.zip"
PAGE_DIR = STATE_DIR / "pages"
CROP_DIR = STATE_DIR / "crops"
EXPORT_DIR = APP_DIR / "exports"
DB_PATH = STATE_DIR / "labeler.sqlite3"
DICT_PATH = APP_DIR / "assets" / "ppocrv6_dict.txt"
PORT = int(os.environ.get("OCR_LABELER_PORT", "8765"))
RENDER_DPI = 300
SPLITS = ("train", "val", "test")
FIELD_TYPES = {
    "item_description": "Tên mặt hàng",
    "seller_name": "Tên nhà cung cấp",
    "invoice_number": "Số hóa đơn",
    "invoice_date": "Ngày hóa đơn",
    "seller_tax_code": "MST bên bán",
    "buyer_tax_code": "MST bên mua",
    "quantity": "Số lượng",
    "unit_price": "Đơn giá",
    "tax_rate": "Thuế suất",
    "line_total": "Thành tiền dòng",
    "total_amount": "Tổng tiền",
    "other": "Khác",
}

for folder in (STATE_DIR, PAGE_DIR, CROP_DIR, EXPORT_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def normalized_group_key(invoice: dict) -> str:
    header = invoice.get("header", {})
    tax = header.get("seller_tax_code", {})
    raw_tax = str(tax.get("normalized") or tax.get("raw") or "").strip()
    digits = re.sub(r"\D", "", raw_tax)
    if digits:
        # A three-digit suffix after a hyphen is commonly a branch code.
        if len(digits) == 13 or (re.search(r"[-\s]\d{3}$", raw_tax) and len(digits) > 10):
            digits = digits[:-3]
        return "mst:" + digits
    name = str(header.get("seller_name", {}).get("raw") or "").strip()
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    name = re.sub(r"[^A-Z0-9]", "", name)
    return "name:" + (name or invoice["id"])


def default_splits(invoices: list[dict]) -> tuple[dict[str, str], str | None]:
    groups: dict[str, list[str]] = {}
    for invoice in invoices:
        groups.setdefault(normalized_group_key(invoice), []).append(invoice["id"])
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    # Dynamic programming finds two whole-supplier groups totaling five invoices each.
    states: dict[tuple[int, int], tuple[tuple[int, str], ...]] = {(0, 0): ()}
    for key, ids in ordered:
        size = len(ids)
        updated = dict(states)
        for (val_count, test_count), chosen in states.items():
            if val_count + size <= 5:
                cell = (val_count + size, test_count)
                candidate = chosen + ((0, key),)
                if cell not in updated or len(candidate) < len(updated[cell]):
                    updated[cell] = candidate
            if test_count + size <= 5:
                cell = (val_count, test_count + size)
                candidate = chosen + ((1, key),)
                if cell not in updated or len(candidate) < len(updated[cell]):
                    updated[cell] = candidate
        states = updated

    chosen = states.get((5, 5))
    result = {invoice["id"]: "train" for invoice in invoices}
    if chosen is None:
        # Keep the requested count if supplier groups cannot fit, and surface that tradeoff.
        for invoice in sorted(invoices, key=lambda item: item["id"]):
            result[invoice["id"]] = "train"
        for invoice in sorted(invoices, key=lambda item: item["id"])[-10:-5]:
            result[invoice["id"]] = "val"
        for invoice in sorted(invoices, key=lambda item: item["id"])[-5:]:
            result[invoice["id"]] = "test"
        return result, "Không thể giữ nguyên toàn bộ nhóm nhà cung cấp mà vẫn đạt 30/5/5; app đã chọn đúng số lượng và chia tách một số nhóm."

    group_to_split = {key: ("val" if which == 0 else "test") for which, key in chosen}
    for invoice in invoices:
        result[invoice["id"]] = group_to_split.get(normalized_group_key(invoice), "train")
    return result, None


def load_source_invoices() -> list[dict]:
    if not LABELS_JSON.is_file() or not PDF_DIR.is_dir():
        raise RuntimeError(f"Không tìm thấy dữ liệu nguồn chỉ đọc: {SOURCE_DIR}")
    data = json.loads(LABELS_JSON.read_text(encoding="utf-8"))
    invoices = data.get("invoices", [])
    if len(invoices) != 40:
        raise RuntimeError(f"Dữ liệu nguồn phải có 40 hóa đơn; hiện có {len(invoices)}.")
    for invoice in invoices:
        pdf_name = str(invoice.get("pdf", ""))
        if Path(pdf_name).name != pdf_name or not (PDF_DIR / pdf_name).is_file():
            raise RuntimeError(f"PDF không hợp lệ hoặc bị thiếu: {pdf_name!r}")
    return invoices


def prepare_source_data() -> None:
    if LABELS_JSON.is_file() and PDF_DIR.is_dir():
        try:
            data = json.loads(LABELS_JSON.read_text(encoding="utf-8"))
            invoices = data.get("invoices", [])
            if len(invoices) == 40 and all(
                Path(str(row.get("pdf", ""))).name == row.get("pdf")
                and (PDF_DIR / str(row.get("pdf", ""))).is_file()
                for row in invoices
            ):
                return
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            pass

    if not BUNDLE_PATH.is_file():
        raise RuntimeError(f"Không tìm thấy bundle dữ liệu {BUNDLE_PATH.name} cạnh app.")

    staging = STATE_DIR / "source_staging"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(BUNDLE_PATH) as archive:
            names = archive.namelist()
            if names.count("40HD.json") != 1:
                raise RuntimeError("Bundle phải có đúng một file 40HD.json.")
            source_bytes = archive.read("40HD.json")
            source_data = json.loads(source_bytes.decode("utf-8"))
            invoices = source_data.get("invoices", [])
            if len(invoices) != 40:
                raise RuntimeError(f"Bundle phải có 40 hóa đơn; hiện có {len(invoices)}.")
            if len(names) != len(set(names)):
                raise RuntimeError("Bundle có tên file trùng lặp.")

            pdf_entries: list[tuple[str, str]] = []
            for invoice in invoices:
                pdf_name = str(invoice.get("pdf", ""))
                if not pdf_name or Path(pdf_name).name != pdf_name or "\\" in pdf_name:
                    raise RuntimeError(f"Tên PDF không hợp lệ trong JSON: {pdf_name!r}")
                archive_name = f"pdfs/{pdf_name}"
                if names.count(archive_name) != 1:
                    raise RuntimeError(f"Bundle thiếu hoặc lặp PDF {archive_name}.")
                pdf_entries.append((archive_name, pdf_name))

            (staging / "pdfs").mkdir(parents=True)
            (staging / "40HD.json").write_bytes(source_bytes)
            for archive_name, pdf_name in pdf_entries:
                pdf_bytes = archive.read(archive_name)
                if not pdf_bytes.startswith(b"%PDF-"):
                    raise RuntimeError(f"File không hợp lệ: {archive_name}")
                (staging / "pdfs" / pdf_name).write_bytes(pdf_bytes)

        shutil.rmtree(SOURCE_DIR, ignore_errors=True)
        staging.replace(SOURCE_DIR)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def write_split_manifest(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id,pdf,supplier_name,seller_tax_code,split FROM invoices ORDER BY id"
    ).fetchall()
    target = STATE_DIR / "invoice_splits.csv"
    temp = target.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("invoice_id", "pdf", "supplier", "seller_tax_code", "split"))
        writer.writerows(tuple(row) for row in rows)
    temp.replace(target)


def initialize_database() -> None:
    prepare_source_data()
    source = load_source_invoices()
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                pdf TEXT NOT NULL,
                supplier_name TEXT NOT NULL DEFAULT '',
                seller_tax_code TEXT NOT NULL DEFAULT '',
                split TEXT NOT NULL CHECK(split IN ('train','val','test'))
            );
            CREATE TABLE IF NOT EXISTS crops (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                page INTEGER NOT NULL CHECK(page >= 0),
                x0 INTEGER NOT NULL,
                y0 INTEGER NOT NULL,
                x1 INTEGER NOT NULL,
                y1 INTEGER NOT NULL,
                pdf_x0 REAL NOT NULL,
                pdf_y0 REAL NOT NULL,
                pdf_x1 REAL NOT NULL,
                pdf_y1 REAL NOT NULL,
                field_type TEXT NOT NULL DEFAULT 'other',
                label TEXT NOT NULL DEFAULT '',
                confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)),
                image_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS crops_invoice_idx ON crops(invoice_id, created_at);
            """
        )
        existing = {row[0] for row in connection.execute("SELECT id FROM invoices")}
        if not existing:
            splits, _ = default_splits(source)
        else:
            splits = {}
        for invoice in source:
            header = invoice.get("header", {})
            supplier = header.get("seller_name", {}).get("raw") or header.get("seller_name", {}).get("normalized") or ""
            tax = header.get("seller_tax_code", {}).get("normalized") or header.get("seller_tax_code", {}).get("raw") or ""
            split = splits.get(invoice["id"], "train")
            connection.execute(
                """INSERT INTO invoices(id,pdf,supplier_name,seller_tax_code,split) VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET pdf=excluded.pdf,supplier_name=excluded.supplier_name,
                   seller_tax_code=excluded.seller_tax_code""",
                (invoice["id"], invoice["pdf"], supplier, tax, split),
            )
        write_split_manifest(connection)


def invoice_rows() -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT i.*,COUNT(c.id) crop_count,
                      SUM(CASE WHEN c.confirmed=1 AND c.label<>'' THEN 1 ELSE 0 END) confirmed_count
               FROM invoices i LEFT JOIN crops c ON c.invoice_id=i.id
               GROUP BY i.id ORDER BY i.id"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_invoice(invoice_id: str) -> dict | None:
    with connect() as connection:
        invoice = connection.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if invoice is None:
            return None
        crops = connection.execute(
            "SELECT * FROM crops WHERE invoice_id=? ORDER BY created_at,id", (invoice_id,)
        ).fetchall()
    result = dict(invoice)
    result["crops"] = [dict(crop) for crop in crops]
    return result


def page_image(invoice_id: str, page_index: int) -> tuple[Path, int, int, float, float, int]:
    with connect() as connection:
        invoice = connection.execute("SELECT pdf FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if invoice is None:
        raise ValueError("Không tìm thấy hóa đơn.")
    pdf_path = PDF_DIR / invoice["pdf"]
    stat = pdf_path.stat()
    cache_path = PAGE_DIR / f"{invoice_id}_{stat.st_mtime_ns}_{page_index}_{RENDER_DPI}.png"
    try:
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Thiếu PyPDFium2/Pillow. Chạy start_labeler.ps1 để cài phần phụ thuộc local.") from exc

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index < 0 or page_index >= len(document):
            raise ValueError("Số trang PDF không hợp lệ.")
        page = document[page_index]
        width_pt, height_pt = page.get_size()
        if not cache_path.is_file():
            bitmap = page.render(scale=RENDER_DPI / 72)
            image = bitmap.to_pil().convert("RGB")
            image.save(cache_path, format="PNG", optimize=True)
            image.close()
            bitmap.close()
        with Image.open(cache_path) as image:
            width_px, height_px = image.size
        return cache_path, width_px, height_px, width_pt, height_pt, len(document)
    finally:
        document.close()


def extract_crop_text(crop_id: str) -> str:
    with connect() as connection:
        crop = connection.execute(
            """SELECT c.*,i.pdf FROM crops c JOIN invoices i ON i.id=c.invoice_id WHERE c.id=?""",
            (crop_id,),
        ).fetchone()
    if crop is None:
        raise ValueError("Không tìm thấy crop.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("Thiếu PyPDFium2; chạy start_labeler.ps1 để cài thư viện.") from exc

    image_path, pixel_width, pixel_height, _, _, _ = page_image(crop["invoice_id"], crop["page"])
    pdf_path = PDF_DIR / crop["pdf"]
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[crop["page"]]
        page_width, page_height = page.get_size()
        left_bound, bottom_bound, right_bound, top_bound = page.get_bbox()
        scale_x = (right_bound - left_bound) / pixel_width
        scale_y = (top_bound - bottom_bound) / pixel_height
        left = left_bound + crop["x0"] * scale_x
        right = left_bound + crop["x1"] * scale_x
        top = top_bound - crop["y0"] * scale_y
        bottom = top_bound - crop["y1"] * scale_y
        if page_width <= 0 or page_height <= 0 or right <= left or top <= bottom:
            raise ValueError("Không chuyển được tọa độ crop sang vùng chữ PDF.")
        text_page = page.get_textpage()
        try:
            extracted = text_page.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
        finally:
            text_page.close()
    finally:
        document.close()

    extracted = unicodedata.normalize("NFC", extracted)
    extracted = re.sub(r"\s+", " ", extracted).strip()
    if not extracted:
        raise ValueError("Vùng này không có lớp chữ PDF để chép. Nếu đây là PDF scan, hãy nhập nhãn theo ảnh crop.")
    return extracted


def dictionary_chars() -> set[str] | None:
    if not DICT_PATH.is_file():
        return None
    return {line.rstrip("\r\n") for line in DICT_PATH.read_text(encoding="utf-8").splitlines()}


def system_resources() -> dict:
    result = {"cpu_threads": os.cpu_count(), "ram_total_gb": None, "ram_available_gb": None}
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                result["ram_total_gb"] = round(status.total / 1024**3, 1)
                result["ram_available_gb"] = round(status.available / 1024**3, 1)
        except Exception:
            pass
    result["disk_free_gb"] = round(shutil.disk_usage(APP_DIR).free / 1024**3, 1)
    return result


def readiness(full_check: bool = False) -> dict:
    invoices = invoice_rows()
    counts = {split: sum(1 for row in invoices if row["split"] == split) for split in SPLITS}
    all_crops: list[dict] = []
    with connect() as connection:
        all_crops = [dict(row) for row in connection.execute(
            "SELECT c.*,i.split FROM crops c JOIN invoices i ON i.id=c.invoice_id ORDER BY c.id"
        )]

    errors: list[str] = []
    warnings: list[str] = []
    if counts != {"train": 30, "val": 5, "test": 5}:
        errors.append(f"Split hiện tại là train/val/test {counts['train']}/{counts['val']}/{counts['test']}; cần 30/5/5.")
    if not all_crops:
        errors.append("Chưa có crop nào được gán nhãn.")
    dictionary = dictionary_chars()
    if dictionary is None:
        errors.append("Không tìm thấy character dictionary PP-OCRv6 trong assets.")

    hashes: dict[str, str] = {}
    missing = 0
    unconfirmed = 0
    unsupported: list[tuple[str, str]] = []
    for crop in all_crops:
        path = CROP_DIR / crop["image_name"]
        if not path.is_file():
            missing += 1
            continue
        if full_check:
            try:
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
            except Exception:
                missing += 1
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in hashes:
                errors.append(f"Ảnh crop trùng nội dung: {hashes[digest]} và {crop['id']}.")
            else:
                hashes[digest] = crop["id"]
        if not crop["label"].strip() or not crop["confirmed"]:
            unconfirmed += 1
        if "\n" in crop["label"] or "\r" in crop["label"] or "\t" in crop["label"]:
            errors.append(f"Nhãn crop {crop['id']} có tab/xuống dòng, không thể biểu diễn an toàn trong file PaddleOCR.")
        if len(crop["label"]) > 25:
            errors.append(f"Nhãn crop {crop['id']} dài hơn giới hạn 25 ký tự của cấu hình PP-OCRv6; hãy cắt thành vùng nhỏ hơn.")
        if dictionary is not None:
            bad = sorted({char for char in crop["label"] if char not in dictionary and not char.isspace()})
            if bad:
                unsupported.append((crop["id"], "".join(bad)))

    if missing:
        errors.append(f"Có {missing} ảnh crop bị thiếu hoặc lỗi.")
    if unconfirmed:
        errors.append(f"Có {unconfirmed} crop thiếu nhãn hoặc chưa được người dùng xác nhận.")
    if unsupported:
        examples = "; ".join(f"{crop_id}: {chars!r}" for crop_id, chars in unsupported[:5])
        errors.append(f"Có ký tự ngoài dictionary PP-OCRv6 ở {len(unsupported)} crop: {examples}")
    for split in SPLITS:
        if not any(crop["split"] == split for crop in all_crops):
            errors.append(f"Tập {split} chưa có crop; cần có nhãn để huấn luyện và đánh giá.")

    if len(all_crops) < 5000:
        warnings.append(f"Có {len(all_crops)} crop, dưới mức khuyến nghị khoảng 5.000 mẫu; chỉ nên xem kết quả là pilot có nguy cơ overfit.")
    training_root = APP_DIR / "training"
    training_repo = training_root / "PaddleOCR-v3.7.0"
    pretrained = training_root / "PP-OCRv6_medium_rec_pretrained.pdparams"
    if not training_repo.is_dir():
        warnings.append("Chưa có source PaddleOCR v3.7.0 phục vụ huấn luyện; runner sẽ tải source chính thức khi dữ liệu sẵn sàng.")
    if not pretrained.is_file():
        warnings.append("Chưa có checkpoint huấn luyện .pdparams; model inference trong cache không thay thế được checkpoint này.")
    warnings.append("5 hóa đơn test thuộc bộ 40 hóa đơn đã được xem trong lần đánh giá trước; điểm test hiện tại chỉ có tính tham khảo.")
    resources = system_resources()
    if resources.get("ram_available_gb") is not None and resources["ram_available_gb"] < 4:
        warnings.append(f"RAM khả dụng hiện khoảng {resources['ram_available_gb']} GB; nên đóng ứng dụng khác trước khi thử train CPU.")
    data_ready = not errors
    return {
        "split_counts": counts,
        "invoice_count": len(invoices),
        "crop_count": len(all_crops),
        "confirmed_count": sum(1 for crop in all_crops if crop["confirmed"] and crop["label"].strip()),
        "data_ready": data_ready,
        "training_assets_ready": training_repo.is_dir() and pretrained.is_file(),
        "recommended_sample_count_reached": len(all_crops) >= 5000,
        "errors": errors,
        "warnings": warnings,
        "resources": resources,
    }


def validate_export() -> tuple[dict, list[dict]]:
    report = readiness(full_check=True)
    if report["errors"]:
        raise ValueError("Không thể xuất: " + " | ".join(report["errors"]))
    with connect() as connection:
        crops = [dict(row) for row in connection.execute(
            """SELECT c.*,i.pdf,i.split,i.supplier_name,i.seller_tax_code
               FROM crops c JOIN invoices i ON i.id=c.invoice_id ORDER BY i.id,c.created_at,c.id"""
        )]
    return report, crops


def export_dataset() -> Path:
    _, crops = validate_export()
    invoice_map = {row["id"]: row for row in invoice_rows()}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    zip_path = EXPORT_DIR / f"paddleocr_recognition_{stamp}.zip"
    manifest_rows = []
    split_lines: dict[str, list[str]] = {split: [] for split in SPLITS}
    for crop in crops:
        if "\t" in crop["label"] or "\n" in crop["label"] or "\r" in crop["label"]:
            raise ValueError(f"Nhãn {crop['id']} có tab/xuống dòng; hãy tách thành crop riêng.")
        relative_image = f"images/{crop['image_name']}"
        split_lines[crop["split"]].append(f"{relative_image}\t{crop['label']}")
        manifest_rows.append({
            "crop_id": crop["id"], "invoice_id": crop["invoice_id"], "pdf": crop["pdf"],
            "page": crop["page"] + 1,
            "x0_pdf_pt": crop["pdf_x0"], "y0_pdf_pt": crop["pdf_y0"],
            "x1_pdf_pt": crop["pdf_x1"], "y1_pdf_pt": crop["pdf_y1"],
            "x0_px": crop["x0"], "y0_px": crop["y0"], "x1_px": crop["x1"], "y1_px": crop["y1"],
            "field_type": crop["field_type"], "split": crop["split"],
            "image": relative_image, "label": crop["label"], "supplier": crop["supplier_name"],
            "seller_tax_code": crop["seller_tax_code"],
        })
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for crop in crops:
            archive.write(CROP_DIR / crop["image_name"], f"images/{crop['image_name']}")
        for split in SPLITS:
            text = "\n".join(split_lines[split]) + "\n"
            archive.writestr(f"{split}.txt", text.encode("utf-8"))
            if split in ("train", "val"):
                archive.writestr(f"{split}_list.txt", text.encode("utf-8"))
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)
        archive.writestr("manifest.csv", csv_buffer.getvalue().encode("utf-8-sig"))
        invoice_buffer = io.StringIO(newline="")
        writer = csv.writer(invoice_buffer)
        writer.writerow(("invoice_id", "pdf", "supplier", "seller_tax_code", "split"))
        for invoice in invoice_map.values():
            writer.writerow((invoice["id"], invoice["pdf"], invoice["supplier_name"], invoice["seller_tax_code"], invoice["split"]))
        archive.writestr("invoice_splits.csv", invoice_buffer.getvalue().encode("utf-8-sig"))
        archive.write(DICT_PATH, "ppocrv6_dict.txt")
        archive.writestr("DATASET_INFO.md", (
            "PaddleOCR recognition dataset created locally.\n\n"
            "Lists are UTF-8 and use image-path<TAB>transcription. Images are 300-DPI crops rendered from source PDFs.\n"
            "Splits are by invoice, with 30 train / 5 val / 5 test invoices. Related supplier branches are grouped where identified by tax-code suffix.\n"
            "All text labels were explicitly confirmed against the crop by a human annotator.\n"
            "The five test invoices were already included in an earlier OCR review, so test scores are exploratory, not independent.\n"
        ).encode("utf-8"))
    return zip_path


def export_handoff() -> tuple[Path, int]:
    with connect() as connection:
        crops = [dict(row) for row in connection.execute(
            """SELECT c.*,i.pdf,i.split FROM crops c JOIN invoices i ON i.id=c.invoice_id
               WHERE c.confirmed=1 AND TRIM(c.label)<>'' ORDER BY i.id,c.created_at,c.id"""
        )]
        unconfirmed = connection.execute(
            "SELECT COUNT(*) FROM crops WHERE confirmed<>1 OR TRIM(label)=''"
        ).fetchone()[0]
    if not crops:
        raise ValueError("Chưa có nhãn đã xác nhận để đưa vào gói bàn giao.")

    invoices: dict[str, dict] = {}
    for crop in crops:
        invoice_id = crop["invoice_id"]
        if invoice_id not in invoices:
            pdf_path = PDF_DIR / crop["pdf"]
            if not pdf_path.is_file():
                raise ValueError(f"Không tìm thấy PDF nguồn cho {invoice_id}: {crop['pdf']}")
            digest = sha256_file(pdf_path)
            invoices[invoice_id] = {
                "pdf": crop["pdf"], "pdf_sha256": digest, "split": crop["split"],
            }

    manifest = {
        "schema_version": 1,
        "dataset": "40HD",
        "source_labels_sha256": hashlib.sha256(LABELS_JSON.read_bytes()).hexdigest(),
        "exported_at": now_iso(),
        "invoices": invoices,
        "crops": [],
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    zip_path = EXPORT_DIR / f"ocr_labels_handoff_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for crop in crops:
            image_name = f"{crop['id']}.png"
            image_path = CROP_DIR / crop["image_name"]
            if not image_path.is_file():
                raise ValueError(f"Thiếu ảnh crop {crop['id']}.")
            manifest["crops"].append({
                "id": crop["id"], "invoice_id": crop["invoice_id"], "pdf": crop["pdf"],
                "page": crop["page"], "x0": crop["x0"], "y0": crop["y0"],
                "x1": crop["x1"], "y1": crop["y1"], "pdf_x0": crop["pdf_x0"],
                "pdf_y0": crop["pdf_y0"], "pdf_x1": crop["pdf_x1"], "pdf_y1": crop["pdf_y1"],
                "field_type": crop["field_type"], "label": crop["label"],
                "confirmed": True, "image": f"crops/{image_name}",
            })
            archive.write(image_path, f"crops/{image_name}")
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        archive.writestr("README.txt", (
            "Goi nhan dang nhan hoa don. Chi nhap vao app dung bo 40HD; PDF nguon duoc doi chieu bang SHA-256.\n"
            "Cac crop trong goi da duoc nguoi gan nhan xac nhan.\n"
        ).encode("utf-8"))
    return zip_path, int(unconfirmed)


def import_handoff(zip_path: Path) -> dict[str, int]:
    try:
        import PIL.Image as Image
    except ImportError as exc:
        raise RuntimeError("Thiếu Pillow; chạy start_labeler.ps1 để cài thư viện.") from exc

    max_entries = 20_000
    max_uncompressed = 512 * 1024 * 1024
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > max_entries or sum(item.file_size for item in infos) > max_uncompressed:
            raise ValueError("Gói nhãn vượt giới hạn dung lượng cho phép.")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError("Gói nhãn có tên file trùng lặp.")
        for name in names:
            member = Path(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise ValueError("Gói nhãn có đường dẫn file không an toàn.")
        if "manifest.json" not in names or any(
            name not in {"manifest.json", "README.txt"} and not name.startswith("crops/")
            for name in names
        ):
            raise ValueError("Cấu trúc gói nhãn không hợp lệ.")
        if archive.getinfo("manifest.json").file_size > 20 * 1024 * 1024:
            raise ValueError("Manifest trong gói nhãn quá lớn.")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Không đọc được manifest.json trong gói nhãn.") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("dataset") != "40HD":
            raise ValueError("Gói nhãn không thuộc định dạng 40HD được hỗ trợ.")
        if manifest.get("source_labels_sha256") != hashlib.sha256(LABELS_JSON.read_bytes()).hexdigest():
            raise ValueError("40HD.json khác bộ nguồn của gói nhãn; không nhập để tránh ghép nhầm hóa đơn.")
        invoice_manifest = manifest.get("invoices")
        crops = manifest.get("crops")
        if not isinstance(invoice_manifest, dict) or not isinstance(crops, list) or not crops:
            raise ValueError("Manifest thiếu hóa đơn hoặc crop.")

        local_invoices = {row["id"]: row for row in invoice_rows()}
        for invoice_id, item in invoice_manifest.items():
            if not isinstance(item, dict):
                raise ValueError(f"Metadata hóa đơn không hợp lệ: {invoice_id}.")
            local = local_invoices.get(invoice_id)
            if local is None or local["pdf"] != item.get("pdf") or local["split"] != item.get("split"):
                raise ValueError(f"Hóa đơn/split không khớp bộ local: {invoice_id}.")
            pdf_path = PDF_DIR / local["pdf"]
            if sha256_file(pdf_path) != item.get("pdf_sha256"):
                raise ValueError(f"PDF nguồn không khớp hóa đơn {invoice_id}: {local['pdf']}.")

        dictionary = dictionary_chars()
        if dictionary is None:
            raise ValueError("Không tìm thấy character dictionary PP-OCRv6.")
        incoming: list[dict] = []
        crop_ids: set[str] = set()
        page_sizes: dict[tuple[str, int], tuple[int, int]] = {}
        for crop in crops:
            if not isinstance(crop, dict):
                raise ValueError("Metadata crop trong manifest không hợp lệ.")
            crop_id = str(crop.get("id", ""))
            invoice_id = str(crop.get("invoice_id", ""))
            image_name = f"crops/{crop_id}.png"
            if not re.fullmatch(r"[0-9a-f]{32}", crop_id) or crop_id in crop_ids:
                raise ValueError(f"ID crop không hợp lệ hoặc trùng: {crop_id!r}.")
            crop_ids.add(crop_id)
            if crop.get("image") != image_name or image_name not in names:
                raise ValueError(f"Thiếu ảnh tương ứng crop {crop_id}.")
            invoice_info = invoice_manifest.get(invoice_id)
            if not invoice_info or crop.get("pdf") != invoice_info.get("pdf"):
                raise ValueError(f"Crop {crop_id} không khớp PDF trong manifest.")
            if crop.get("confirmed") is not True:
                raise ValueError(f"Crop {crop_id} chưa được xác nhận; hãy chỉ xuất nhãn đã đối chiếu.")
            field_type = crop.get("field_type")
            label = crop.get("label")
            if field_type not in FIELD_TYPES or not isinstance(label, str) or not label.strip():
                raise ValueError(f"Crop {crop_id} thiếu loại trường hoặc nhãn.")
            if len(label) > 25 or any(char in label for char in "\r\n\t"):
                raise ValueError(f"Nhãn crop {crop_id} vượt giới hạn hoặc chứa tab/xuống dòng.")
            unsupported = sorted({char for char in label if char not in dictionary and not char.isspace()})
            if unsupported:
                raise ValueError(f"Nhãn crop {crop_id} có ký tự ngoài dictionary: {''.join(unsupported)!r}.")
            try:
                page = int(crop["page"])
                coords = tuple(int(crop[key]) for key in ("x0", "y0", "x1", "y1"))
                pdf_coords = tuple(float(crop[key]) for key in ("pdf_x0", "pdf_y0", "pdf_x1", "pdf_y1"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Tọa độ crop {crop_id} không hợp lệ.") from exc
            if page < 0 or len(pdf_coords) != 4 or coords[2] <= coords[0] or coords[3] <= coords[1]:
                raise ValueError(f"Tọa độ crop {crop_id} không hợp lệ.")
            size_key = (invoice_id, page)
            if size_key not in page_sizes:
                _, width, height, _, _, _ = page_image(invoice_id, page)
                page_sizes[size_key] = (width, height)
            width, height = page_sizes[size_key]
            if coords[0] < 0 or coords[1] < 0 or coords[2] > width or coords[3] > height:
                raise ValueError(f"Crop {crop_id} vượt ra ngoài trang PDF.")
            data = archive.read(image_name)
            try:
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    if image.size != (coords[2] - coords[0], coords[3] - coords[1]):
                        raise ValueError(f"Kích thước ảnh crop {crop_id} không khớp tọa độ.")
            except Exception as exc:
                raise ValueError(f"Ảnh crop {crop_id} bị lỗi.") from exc
            item = {
                "id": crop_id, "invoice_id": invoice_id, "page": page,
                "x0": coords[0], "y0": coords[1], "x1": coords[2], "y1": coords[3],
                "pdf_x0": pdf_coords[0], "pdf_y0": pdf_coords[1],
                "pdf_x1": pdf_coords[2], "pdf_y1": pdf_coords[3],
                "field_type": field_type, "label": label, "image_name": f"{crop_id}.png",
                "image_sha256": hashlib.sha256(data).hexdigest(), "image_bytes": data,
            }
            incoming.append(item)

    with connect() as connection:
        existing_rows = [dict(row) for row in connection.execute("SELECT * FROM crops")]
    existing_by_id = {row["id"]: row for row in existing_rows}
    existing_by_region = {
        (row["invoice_id"], row["page"], row["x0"], row["y0"], row["x1"], row["y1"]): row
        for row in existing_rows
    }
    accepted: list[dict] = []
    skipped = 0
    conflicts: list[str] = []
    for item in incoming:
        same_id = existing_by_id.get(item["id"])
        same_region = existing_by_region.get((item["invoice_id"], item["page"], item["x0"], item["y0"], item["x1"], item["y1"]))
        current = same_id or same_region
        if current is None:
            accepted.append(item)
            continue
        existing_image = CROP_DIR / current["image_name"]
        same_content = (
            current["invoice_id"] == item["invoice_id"] and current["page"] == item["page"]
            and current["x0"] == item["x0"] and current["y0"] == item["y0"]
            and current["x1"] == item["x1"] and current["y1"] == item["y1"]
            and current["field_type"] == item["field_type"] and current["label"] == item["label"]
            and existing_image.is_file()
            and hashlib.sha256(existing_image.read_bytes()).hexdigest() == item["image_sha256"]
        )
        if same_content:
            skipped += 1
        else:
            conflicts.append(item["id"])
    if conflicts:
        raise ValueError("Có xung đột: crop trùng vùng/ID nhưng nội dung khác, không nhập để tránh ghi đè: " + ", ".join(conflicts[:10]))

    import_dir = CROP_DIR / f".import_{uuid.uuid4().hex}"
    import_dir.mkdir()
    moved: list[Path] = []
    try:
        for item in accepted:
            (import_dir / item["image_name"]).write_bytes(item["image_bytes"])
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in accepted:
                destination = CROP_DIR / item["image_name"]
                if destination.exists():
                    raise ValueError(f"Ảnh crop đích đã tồn tại; không ghi đè: {item['id']}.")
                (import_dir / item["image_name"]).replace(destination)
                moved.append(destination)
                timestamp = now_iso()
                connection.execute(
                    """INSERT INTO crops(id,invoice_id,page,x0,y0,x1,y1,pdf_x0,pdf_y0,pdf_x1,pdf_y1,
                       field_type,label,confirmed,image_name,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["id"], item["invoice_id"], item["page"], item["x0"], item["y0"],
                     item["x1"], item["y1"], item["pdf_x0"], item["pdf_y0"], item["pdf_x1"],
                     item["pdf_y1"], item["field_type"], item["label"], 1, item["image_name"],
                     timestamp, timestamp),
                )
        with connect() as connection:
            write_split_manifest(connection)
    except Exception:
        for path in moved:
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(import_dir, ignore_errors=True)
    return {"imported": len(accepted), "skipped_duplicates": skipped}


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalOCRLabeler/1.0"

    def log_message(self, _format: str, *args: object) -> None:
        print("[labeler] " + (_format % args), flush=True)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "").lower()
        return host in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self' data: blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str, filename: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        with path.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def _json(self, value: object, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("Kích thước yêu cầu không hợp lệ.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Nội dung yêu cầu không phải JSON hợp lệ.") from exc

    def _error(self, exc: Exception) -> None:
        self._json({"error": str(exc)}, 400 if isinstance(exc, (ValueError, KeyError)) else 500)

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json({"error": "Chỉ cho phép truy cập qua localhost."}, 403)
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/", "/index.html", "/styles.css", "/app.js"):
            name = "index.html" if path == "/" else path.lstrip("/")
            file_path = APP_DIR / name
            mime = "text/html; charset=utf-8" if name.endswith(".html") else "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
            self._send(200, file_path.read_bytes(), mime)
            return
        try:
            if path == "/api/meta":
                report = readiness()
                self._json({"field_types": FIELD_TYPES, **report})
            elif path == "/api/invoices":
                query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
                search = unquote(query.get("q", "")).casefold()
                split = query.get("split", "all")
                status = query.get("status", "all")
                rows = invoice_rows()
                rows = [row for row in rows if (split == "all" or row["split"] == split)]
                rows = [row for row in rows if not search or search in row["id"].casefold() or search in row["pdf"].casefold() or search in row["supplier_name"].casefold()]
                if status == "empty":
                    rows = [row for row in rows if row["crop_count"] == 0]
                elif status == "review":
                    rows = [row for row in rows if row["confirmed_count"] < row["crop_count"] or row["crop_count"] == 0]
                elif status == "done":
                    rows = [row for row in rows if row["crop_count"] > 0 and row["confirmed_count"] == row["crop_count"]]
                self._json({"invoices": rows})
            elif path.startswith("/api/invoice/"):
                invoice_id = path.removeprefix("/api/invoice/")
                invoice = get_invoice(invoice_id)
                if invoice is None:
                    raise ValueError("Không tìm thấy hóa đơn.")
                self._json(invoice)
            elif path.startswith("/api/page/"):
                parts = path.split("/")
                if len(parts) != 5:
                    raise ValueError("Đường dẫn trang PDF không hợp lệ.")
                image_path, width, height, width_pt, height_pt, page_count = page_image(parts[3], int(parts[4]))
                self._send(200, image_path.read_bytes(), "image/png", {"X-Page-Width": str(width), "X-Page-Height": str(height), "X-Page-Width-Pt": str(width_pt), "X-Page-Height-Pt": str(height_pt), "X-Page-Count": str(page_count)})
            elif path.startswith("/api/crop-image/"):
                crop_id = path.removeprefix("/api/crop-image/")
                with connect() as connection:
                    crop = connection.execute("SELECT image_name FROM crops WHERE id=?", (crop_id,)).fetchone()
                if crop is None:
                    raise ValueError("Không tìm thấy crop.")
                image_path = CROP_DIR / crop["image_name"]
                self._send(200, image_path.read_bytes(), "image/png")
            elif path.startswith("/api/crop-text/"):
                crop_id = path.removeprefix("/api/crop-text/")
                self._json({"text": extract_crop_text(crop_id)})
            elif path == "/api/handoff/export":
                archive_path, unconfirmed = export_handoff()
                self._send_file(archive_path, "application/zip", archive_path.name, {
                    "X-Unconfirmed-Crops": str(unconfirmed),
                })
            elif path == "/api/readiness":
                self._json(readiness())
            else:
                self._json({"error": "Không tìm thấy đường dẫn."}, 404)
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json({"error": "Chỉ cho phép truy cập qua localhost."}, 403)
            return
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/api/handoff/import":
                self._import_handoff()
                return
            body = self._body()
            if path == "/api/crops":
                self._create_crop(body)
            elif path.startswith("/api/crops/") and path.endswith("/delete"):
                crop_id = path.split("/")[-2]
                self._delete_crop(crop_id, body)
            elif path.startswith("/api/crops/"):
                self._update_crop(path.removeprefix("/api/crops/"), body)
            elif path.startswith("/api/invoice/") and path.endswith("/split"):
                invoice_id = path.removeprefix("/api/invoice/").removesuffix("/split")
                self._update_split(invoice_id, body)
            elif path == "/api/export":
                self._export()
            else:
                self._json({"error": "Không tìm thấy đường dẫn."}, 404)
        except Exception as exc:
            self._error(exc)

    def _import_handoff(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Kích thước gói nhãn không hợp lệ.") from exc
        if length <= 0 or length > 512 * 1024 * 1024:
            raise ValueError("Gói nhãn phải có dung lượng từ 1 byte đến 512 MB.")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/zip":
            raise ValueError("Hãy chọn file ZIP gói nhãn.")

        fd, temporary_name = tempfile.mkstemp(prefix="handoff_", suffix=".zip", dir=STATE_DIR)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Gói nhãn tải lên chưa đầy đủ.")
                    stream.write(chunk)
                    remaining -= len(chunk)
            self._json(import_handoff(temporary_path))
        finally:
            temporary_path.unlink(missing_ok=True)

    def _create_crop(self, body: dict) -> None:
        invoice_id = str(body.get("invoice_id", ""))
        page_index = int(body.get("page", -1))
        image_path, page_width, page_height, width_pt, height_pt, _ = page_image(invoice_id, page_index)
        x0, y0, x1, y1 = (int(body[key]) for key in ("x0", "y0", "x1", "y1"))
        x0, x1 = max(0, min(x0, page_width)), max(0, min(x1, page_width))
        y0, y1 = max(0, min(y0, page_height)), max(0, min(y1, page_height))
        if x1 <= x0 or y1 <= y0 or x1 - x0 < 12 or y1 - y0 < 10:
            raise ValueError("Vùng chọn quá nhỏ hoặc không hợp lệ.")
        if (x1 - x0) * (y1 - y0) > page_width * page_height * 0.8:
            raise ValueError("Vùng chọn quá lớn; hãy cắt riêng dòng chữ, không chọn cả trang.")
        from PIL import Image
        with Image.open(image_path) as page_image_file:
            crop_image = page_image_file.crop((x0, y0, x1, y1)).convert("RGB")
            crop_bytes = io.BytesIO()
            crop_image.save(crop_bytes, format="PNG", optimize=True)
            crop_image.close()
        crop_id = uuid.uuid4().hex
        image_name = f"{crop_id}.png"
        crop_path = CROP_DIR / image_name
        crop_path.write_bytes(crop_bytes.getvalue())
        scale_x, scale_y = width_pt / page_width, height_pt / page_height
        timestamp = now_iso()
        try:
            with connect() as connection:
                connection.execute(
                    """INSERT INTO crops(id,invoice_id,page,x0,y0,x1,y1,pdf_x0,pdf_y0,pdf_x1,pdf_y1,
                       field_type,label,confirmed,image_name,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (crop_id, invoice_id, page_index, x0, y0, x1, y1,
                     x0*scale_x, y0*scale_y, x1*scale_x, y1*scale_y,
                     "other", "", 0, image_name, timestamp, timestamp),
                )
        except Exception:
            crop_path.unlink(missing_ok=True)
            raise
        self._json({"crop_id": crop_id})

    def _update_crop(self, crop_id: str, body: dict) -> None:
        label = str(body.get("label", ""))
        field_type = str(body.get("field_type", "other"))
        confirmed = bool(body.get("confirmed", False))
        if field_type not in FIELD_TYPES:
            raise ValueError("Loại trường không hợp lệ.")
        if confirmed and not label.strip():
            raise ValueError("Nhập nội dung nhìn thấy trước khi xác nhận nhãn.")
        with connect() as connection:
            cursor = connection.execute(
                "UPDATE crops SET label=?,field_type=?,confirmed=?,updated_at=? WHERE id=?",
                (label, field_type, int(confirmed), now_iso(), crop_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Không tìm thấy crop.")
        self._json({"saved": True, "confirmed": confirmed})

    def _delete_crop(self, crop_id: str, body: dict) -> None:
        if body.get("confirm") is not True:
            raise ValueError("Cần xác nhận trước khi xóa crop.")
        with connect() as connection:
            row = connection.execute("SELECT image_name FROM crops WHERE id=?", (crop_id,)).fetchone()
            if row is None:
                raise ValueError("Không tìm thấy crop.")
            connection.execute("DELETE FROM crops WHERE id=?", (crop_id,))
        (CROP_DIR / row["image_name"]).unlink(missing_ok=True)
        self._json({"deleted": True})

    def _update_split(self, invoice_id: str, body: dict) -> None:
        split = str(body.get("split", ""))
        if split not in SPLITS:
            raise ValueError("Split chỉ được là train, val hoặc test.")
        with connect() as connection:
            row = connection.execute("SELECT id FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            if row is None:
                raise ValueError("Không tìm thấy hóa đơn.")
            old = connection.execute("SELECT split FROM invoices WHERE id=?", (invoice_id,)).fetchone()["split"]
            connection.execute("UPDATE invoices SET split=? WHERE id=?", (split, invoice_id))
            write_split_manifest(connection)
            counts = {name: connection.execute("SELECT COUNT(*) FROM invoices WHERE split=?", (name,)).fetchone()[0] for name in SPLITS}
            current = connection.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            current_key = normalized_group_key({"id": invoice_id, "header": {
                "seller_tax_code": {"raw": current["seller_tax_code"]},
                "seller_name": {"raw": current["supplier_name"]},
            }})
            group_splits = []
            for member in connection.execute("SELECT * FROM invoices"):
                member_key = normalized_group_key({"id": member["id"], "header": {
                    "seller_tax_code": {"raw": member["seller_tax_code"]},
                    "seller_name": {"raw": member["supplier_name"]},
                }})
                if member_key == current_key:
                    group_splits.append(member["split"])
        warnings = []
        if counts != {"train": 30, "val": 5, "test": 5}:
            warnings.append(f"Sau thay đổi split, số hóa đơn là {counts['train']}/{counts['val']}/{counts['test']} thay vì 30/5/5.")
        if len(set(group_splits)) > 1:
            warnings.append("Nhóm nhà cung cấp này hiện bị chia qua nhiều split.")
        if old != split:
            warnings.append(f"Hóa đơn đã chuyển từ {old} sang {split}; manifest được lưu lại.")
        self._json({"split": split, "counts": counts, "warnings": warnings})

    def _export(self) -> None:
        zip_path = export_dataset()
        self._send_file(zip_path, "application/zip", zip_path.name)


def main() -> None:
    initialize_database()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        raise SystemExit(f"Không mở được cổng {PORT}; đóng app OCR Labeler đang chạy hoặc đặt OCR_LABELER_PORT khác.") from exc
    print(f"Rà soát crop hóa đơn đang chạy tại http://127.0.0.1:{PORT}", flush=True)
    print(f"PDF/nhãn local từ bundle chỉ đọc: {SOURCE_DIR}", flush=True)
    print(f"Crop và nhãn được lưu local: {STATE_DIR}", flush=True)
    threading.Timer(0.8, webbrowser.open, args=(f"http://127.0.0.1:{PORT}",)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Đã dừng app.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
