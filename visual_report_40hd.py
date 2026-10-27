"""Build an offline, Drive-friendly review report for the 40-invoice benchmark.

The original benchmark's `exact_value_found` is a *presence* check over all OCR
text. It must never be presented as a structured prediction or as precision.
"""

from __future__ import annotations

import base64
import csv
import html
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


ENGINES = ("tesseract", "easyocr", "paddleocr")
ENGINE_NAMES = {"tesseract": "Tesseract", "easyocr": "EasyOCR", "paddleocr": "PaddleOCR"}
DETAIL_COLUMNS = (
    "engine", "invoice_id", "pdf", "field", "field_label", "line", "expected_value",
    "predicted_value", "status", "diagnostic_status", "raw_value_found", "assignment_status",
    "assignment_reason", "linked_prediction_field", "linked_prediction_line", "source_page", "bbox",
    "tp", "fp", "fn", "ocr_excerpt",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, rows: list[dict], columns: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value)).casefold().replace("đ", "d")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _excerpt(text: str, reference: str) -> str:
    """Best *hint* from OCR lines, never treated as a field prediction."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    target = _fold(reference)
    if not target:
        return lines[0][:240]
    tokens = set(target.split())

    def score(line: str) -> float:
        candidate = _fold(line)
        if not candidate:
            return 0.0
        if target in candidate:
            return 2.0
        words = candidate.split()
        overlap = len(tokens & set(words)) / max(1, len(tokens))
        similarity = max(
            (SequenceMatcher(None, target, " ".join(words[i:i + len(tokens)])).ratio()
             for i in range(max(1, len(words) - len(tokens) + 1))),
            default=0.0,
        )
        return max(overlap, similarity)

    best = max(lines, key=score)
    return best[:240]


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _raw_details(scores: list[dict[str, str]], predictions: list[dict[str, str]]) -> list[dict[str, str]]:
    ocr = {(row["engine"], row["invoice_id"]): row.get("ocr_text", "") for row in predictions}
    details: list[dict[str, str]] = []
    for row in scores:
        found = row.get("exact_value_found", "") == "1"
        expected = row.get("normalized", "")
        details.append({
            "engine": row["engine"], "invoice_id": row["invoice_id"], "pdf": row["pdf"],
            "field": row["field"], "field_label": row.get("field_label", row["field"]),
            "line": row.get("line", ""), "expected_value": expected, "predicted_value": "",
            "status": "Giá trị xuất hiện trong OCR" if found else "Không tìm thấy giá trị chuẩn",
            "diagnostic_status": "value_present" if found else "value_not_found",
            "tp": "", "fp": "", "fn": "",
            "ocr_excerpt": _excerpt(ocr.get((row["engine"], row["invoice_id"]), ""), expected),
            "raw_value_found": "1" if found else "0",
            "assignment_status": "", "assignment_reason": "",
            "linked_prediction_field": "", "linked_prediction_line": "",
            "source_page": "", "bbox": "",
        })
    return details


def _structured_details(
    rows: list[dict[str, str]], raw_details: list[dict[str, str]],
    predictions: list[dict[str, str]],
) -> list[dict[str, str]]:
    raw_lookup = {
        (r["engine"], r["invoice_id"], r["field"], r["line"]): r for r in raw_details
    }
    ocr = {(r["engine"], r["invoice_id"]): r.get("ocr_text", "") for r in predictions}
    details = []
    for row in rows:
        engine = row.get("engine", "")
        invoice_id = row.get("invoice_id", "")
        field = row.get("field", "")
        line = str(row.get("line", ""))
        raw = raw_lookup.get((engine, invoice_id, field, line), {})
        expected = _first(row, "expected_value", "ground_truth", "normalized", "reference")
        predicted = _first(row, "predicted_value", "prediction", "extracted_value")
        outcome = _first(row, "outcome")
        diagnostic = _first(row, "diagnostic_status").lower()
        diagnostic_statuses = {
            "correct": "Đúng", "wrong_value": "Sai giá trị",
            "wrong_assignment": "Gán nhầm trường/dòng",
            "read_but_unassigned": "Đọc được nhưng chưa gán",
            "not_read": "OCR chưa đọc được", "ambiguous": "Gán mơ hồ",
            "unscored": "Chưa chấm",
        }
        status = diagnostic_statuses.get(diagnostic) or {
            "TP": "Đúng", "FP+FN": "Sai", "FN": "Thiếu", "FP": "Thừa",
            "TN": "Không có trên hóa đơn", "UNSCORED": "Chưa chấm",
        }.get(outcome, _first(row, "status", "result"))
        if not status:
            status = "Đúng" if row.get("tp") == "1" else "Sai hoặc thiếu"
        details.append({
            "engine": engine, "invoice_id": invoice_id,
            "pdf": _first(row, "pdf") or raw.get("pdf", ""),
            "field": field,
            "field_label": _first(row, "field_label") or raw.get("field_label", field),
            "line": line,
            "expected_value": expected,
            "predicted_value": predicted,
            "status": status,
            "tp": row.get("tp", ""), "fp": row.get("fp", ""), "fn": row.get("fn", ""),
            "diagnostic_status": _first(row, "diagnostic_status") or outcome.lower(),
            "raw_value_found": _first(row, "raw_value_found") or raw.get("raw_value_found", ""),
            "assignment_status": _first(row, "assignment_status"),
            "assignment_reason": _first(row, "assignment_reason"),
            "linked_prediction_field": _first(row, "linked_prediction_field"),
            "linked_prediction_line": _first(row, "linked_prediction_line"),
            "source_page": _first(row, "source_page"), "bbox": _first(row, "bbox"),
            "ocr_excerpt": _first(row, "source_ocr_line", "ocr_excerpt", "source_line", "ocr_line")
            or _excerpt(ocr.get((engine, invoice_id), ""), predicted or expected),
        })
    return details


def _as_float(value: str | float | int) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _bar_chart(path: Path, title: str, rows: list[dict], series: list[tuple[str, str, str]],
               percent: bool = False) -> None:
    width = 900
    left = 175
    plot_width = 620
    top = 85
    row_gap = 46
    height = top + len(rows) * row_gap * max(1, len(series)) + 45
    values = [_as_float(row.get(key, "")) for row in rows for key, _, _ in series]
    maximum = 1.0 if percent else max(values + [1.0])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(title, quote=True)}">',
        f'<rect width="{width}" height="{height}" fill="#fff"/>',
        f'<text x="24" y="40" fill="#16263e" font-size="24" font-family="Arial" font-weight="bold">'
        f'{html.escape(title)}</text>',
    ]
    for idx, row in enumerate(rows):
        for sub, (key, label, color) in enumerate(series):
            y = top + (idx * len(series) + sub) * row_gap
            name = ENGINE_NAMES.get(row.get("engine", ""), row.get("engine", ""))
            if len(series) > 1:
                name += f" · {label}"
            value = _as_float(row.get(key, ""))
            bar_width = max(0.0, min(plot_width, plot_width * value / maximum))
            shown = f"{value:.1%}" if percent else f"{value:.2f} s"
            parts += [
                f'<text x="24" y="{y + 23}" fill="#243954" font-size="16" font-family="Arial">'
                f'{html.escape(name)}</text>',
                f'<rect x="{left}" y="{y + 4}" width="{plot_width}" height="28" rx="5" fill="#eef2f8"/>',
                f'<rect x="{left}" y="{y + 4}" width="{bar_width:.1f}" height="28" rx="5" fill="{color}"/>',
                f'<text x="{left + plot_width + 12}" y="{y + 24}" fill="#17233a" font-size="15" '
                f'font-family="Arial">{shown}</text>',
            ]
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _render_html(result_dir: Path, assessment: list[dict], field_summary: list[dict],
                 details: list[dict], predictions: list[dict], has_structured: bool) -> str:
    esc = html.escape
    chart_sources = {
        name: "data:image/svg+xml;base64," + base64.b64encode(
            (result_dir / name).read_bytes()
        ).decode("ascii")
        for name in ("bieu_do_ty_le_nhan_dien.svg", "bieu_do_thoi_gian.svg", "bieu_do_prf.svg")
    }
    assessment_rows = "\n".join(
        "<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (
            ENGINE_NAMES.get(row.get("engine", ""), row.get("engine", "")),
            f"{_as_float(row.get('value_presence_rate', '')):.1%}",
            f"{_as_float(row.get('precision', '')):.1%}" if row.get("precision", "") != "" else "",
            f"{_as_float(row.get('recall', '')):.1%}" if row.get("recall", "") != "" else "",
            f"{_as_float(row.get('f1_score', '')):.1%}" if row.get("f1_score", "") != "" else "",
            str(row.get("tp", "")), str(row.get("fp", "")), str(row.get("fn", "")),
            f"{_as_float(row.get('average_seconds_per_invoice', '')):.2f}",
        )) + "</tr>"
        for row in assessment
    )
    field_rows = "\n".join(
        "<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (
            ENGINE_NAMES.get(row.get("engine", ""), row.get("engine", "")),
            row.get("field_label", row.get("field", "")),
            str(row.get("tp", "")), str(row.get("fp", "")), str(row.get("fn", "")),
            f"{_as_float(row.get('precision', '')):.1%}" if row.get("precision", "") != "" else "",
            f"{_as_float(row.get('recall', '')):.1%}" if row.get("recall", "") != "" else "",
            f"{_as_float(row.get('f1', row.get('f1_score', ''))):.1%}"
            if row.get("f1", row.get("f1_score", "")) != "" else "",
        )) + "</tr>"
        for row in field_summary
    )
    detail_rows = []
    for row in details:
        attrs = " ".join(
            f'data-{key}="{esc(str(row.get(key, "")), quote=True)}"'
            for key in ("engine", "invoice_id", "status")
        )
        columns = ("engine", "invoice_id", "pdf", "field_label", "line", "expected_value",
                   "predicted_value", "status", "raw_value_found", "linked_prediction_field",
                   "linked_prediction_line", "source_page", "bbox", "ocr_excerpt")
        detail_rows.append(
            f"<tr {attrs}>" + "".join(f"<td>{esc(str(row.get(key, '')))}</td>" for key in columns) + "</tr>"
        )
    ocr_sections = []
    for row in predictions:
        label = f"{row.get('engine', '')} · {row.get('invoice_id', '')} · {row.get('pdf', '')}"
        ocr_sections.append(
            f'<details><summary>{esc(label)}</summary><pre>{esc(row.get("ocr_text", ""))}</pre></details>'
        )
    methodology = (
        "Precision, Recall, F1 được tính trên giá trị do cùng bộ trích trường gán từ đầu ra "
        "mỗi OCR; đây là đánh giá OCR + bộ trích trường chung. Nhãn chưa xác minh không được chấm. "
        "Dòng hàng được gán theo vị trí các vùng OCR so với tiêu đề cột và STT; trang tiếp theo có thể "
        "kế thừa bố cục bảng đã nhận diện ở trang trước. "
        "Tỷ lệ tìm thấy chỉ cho biết giá trị chuẩn xuất hiện đâu đó trong OCR, không chứng minh gán đúng trường."
        if has_structured else
        "Chưa có dữ liệu trích trường độc lập nên Precision, Recall, F1 để trống. "
        "Tỷ lệ tìm thấy chỉ cho biết giá trị chuẩn xuất hiện đâu đó trong OCR, không chứng minh gán đúng trường."
    )
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Báo cáo OCR 40 hóa đơn</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, Arial, sans-serif; color: #19263a; background: #f3f6fa; }}
body {{ margin: 0; }} header {{ background: #102744; color: #fff; padding: 24px max(24px, calc((100% - 1300px)/2)); }}
main {{ max-width: 1300px; margin: auto; padding: 24px; }} h1 {{ margin: 0 0 8px; }} h2 {{ margin-top: 34px; }}
.card {{ background: #fff; border: 1px solid #dce5ee; border-radius: 12px; padding: 20px; margin-bottom: 18px; }}
.charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; }}
.charts img {{ width: 100%; max-height: 430px; object-fit: contain; }}
.tablewrap {{ overflow: auto; max-height: 670px; }} table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border-bottom: 1px solid #e0e7ef; padding: 10px; text-align: left; vertical-align: top; }}
th {{ position: sticky; top: 0; background: #eaf0f9; white-space: nowrap; }}
td:nth-child(6),td:nth-child(7),td:nth-child(9) {{ min-width: 130px; max-width: 320px; overflow-wrap: anywhere; }}
.filters {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }}
label {{ display: grid; gap: 5px; font-weight: 600; }} input,select {{ font: inherit; padding: 8px; border: 1px solid #a9b8c8; border-radius: 6px; min-width: 160px; }}
details {{ border-top: 1px solid #dde5ee; padding: 10px 0; }} summary {{ cursor: pointer; font-weight: 600; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f2f5f9; padding: 14px; border-radius: 8px; }}
.note {{ color: #46566b; line-height: 1.5; }}
</style></head><body>
<header><h1>So sánh OCR trên 40 hóa đơn</h1><div>Tesseract · EasyOCR · PaddleOCR</div></header>
<main><section class="card"><h2>Cách đọc kết quả</h2><p class="note">{esc(methodology)}</p>
<p class="note">Cột “Dòng OCR gần nhất” là gợi ý để mở PDF hoặc văn bản OCR kiểm tra, không phải giá trị tự gán cho trường. Dữ liệu gốc và toàn văn OCR nằm trong các CSV cùng thư mục.</p></section>
<section class="card"><h2>Bảng đánh giá</h2><div class="tablewrap"><table><thead><tr>
<th>Kỹ thuật</th><th>Tỷ lệ tìm thấy</th><th>Precision</th><th>Recall</th><th>F1-score</th><th>TP</th><th>FP</th><th>FN</th><th>Giây OCR/hóa đơn</th>
</tr></thead><tbody>{assessment_rows}</tbody></table></div></section>
<section class="card"><h2>Kết quả theo trường</h2><div class="tablewrap"><table><thead><tr>
<th>Kỹ thuật</th><th>Trường</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1-score</th>
</tr></thead><tbody>{field_rows}</tbody></table></div></section>
<section class="charts"><div class="card"><img src="{chart_sources['bieu_do_ty_le_nhan_dien.svg']}" alt="Biểu đồ tỷ lệ nhận diện"></div>
<div class="card"><img src="{chart_sources['bieu_do_thoi_gian.svg']}" alt="Biểu đồ thời gian trung bình"></div>
<div class="card"><img src="{chart_sources['bieu_do_prf.svg']}" alt="Biểu đồ Precision Recall F1"></div></section>
<section class="card"><h2>Đúng, sai và thiếu theo từng hóa đơn</h2>
<div class="filters"><label>Lọc kỹ thuật<select id="engine"><option value="">Tất cả</option>
{''.join(f'<option value="{esc(engine)}">{esc(ENGINE_NAMES.get(engine, engine))}</option>' for engine in ENGINES)}</select></label>
<label>Lọc hóa đơn<input id="invoice" placeholder="INV-0001 hoặc HD_001.pdf"></label>
<label>Lọc trạng thái<select id="status"><option value="">Tất cả</option>
{''.join(f'<option value="{esc(s, quote=True)}">{esc(s)}</option>' for s in sorted({r['status'] for r in details}))}</select></label></div>
<p id="count" class="note"></p><div class="tablewrap"><table id="details"><thead><tr>
<th>Kỹ thuật</th><th>Hồ sơ</th><th>PDF</th><th>Trường</th><th>Dòng hàng</th><th>Giá trị chuẩn</th>
<th>Giá trị dự đoán</th><th>Chẩn đoán</th><th>Có trong OCR</th><th>Gán nhầm tới trường</th>
<th>Dòng dự đoán</th><th>Trang nguồn</th><th>Vùng nguồn (bbox)</th><th>Dòng OCR</th>
</tr></thead><tbody>{''.join(detail_rows)}</tbody></table></div></section>
<section class="card"><h2>Văn bản OCR đầy đủ</h2>{''.join(ocr_sections)}</section></main>
<script>
const engine=document.getElementById('engine'), invoice=document.getElementById('invoice'), status=document.getElementById('status');
function applyFilters() {{ let count=0; for (const row of document.querySelectorAll('#details tbody tr')) {{
const match=(!engine.value||row.dataset.engine===engine.value)&&(!status.value||row.dataset.status===status.value)
&&(!invoice.value||row.textContent.toLocaleLowerCase().includes(invoice.value.toLocaleLowerCase()));
row.hidden=!match; if(match) count++; }} document.getElementById('count').textContent=count+' dòng đang hiển thị'; }}
for(const control of [engine,invoice,status]) control.addEventListener('input',applyFilters); applyFilters();
</script></body></html>"""


def generate_visual_report(result_dir: Path) -> None:
    """Write CSV + offline HTML/SVG artifacts in the benchmark result directory."""
    result_dir = Path(result_dir)
    summary = _read_csv(result_dir / "summary.csv")
    raw_scores = _read_csv(result_dir / "field_scores.csv")
    predictions = _read_csv(result_dir / "predictions.csv")
    if not summary or not raw_scores or not predictions:
        raise ValueError("Thiếu summary.csv, field_scores.csv hoặc predictions.csv")

    structured_scores = _read_csv(result_dir / "structured_field_scores.csv")
    structured_summary = {
        row["engine"]: row for row in _read_csv(result_dir / "structured_summary.csv")
    }
    raw_details = _raw_details(raw_scores, predictions)
    details = (_structured_details(structured_scores, raw_details, predictions)
               if structured_scores else raw_details)
    _write_csv(result_dir / "chi_tiet_hoa_don.csv", details, DETAIL_COLUMNS)

    assessment = []
    for row in summary:
        structured = structured_summary.get(row["engine"], {})
        assessment.append({
            "engine": row["engine"],
            "confirmed_values": row.get("confirmed_values", ""),
            "values_found": row.get("values_found", ""),
            "value_presence_rate": row.get("overall_value_presence_rate", ""),
            "precision": _first(structured, "precision"),
            "recall": _first(structured, "recall"),
            "f1_score": _first(structured, "f1_score", "f1"),
            "average_seconds_per_invoice": row.get("inference_seconds_per_invoice", ""),
            "tp": _first(structured, "tp"),
            "fp": _first(structured, "fp"),
            "fn": _first(structured, "fn"),
        })
    _write_csv(result_dir / "bang_danh_gia.csv", assessment, (
        "engine", "confirmed_values", "values_found", "value_presence_rate", "precision", "recall",
        "f1_score", "average_seconds_per_invoice", "tp", "fp", "fn",
    ))
    _bar_chart(result_dir / "bieu_do_ty_le_nhan_dien.svg", "Tỷ lệ tìm thấy giá trị chuẩn", assessment,
               [("value_presence_rate", "", "#2865bb")], percent=True)
    _bar_chart(result_dir / "bieu_do_thoi_gian.svg", "Thời gian OCR trung bình mỗi hóa đơn", assessment,
               [("average_seconds_per_invoice", "", "#148a72")])
    prf = [(key, label, color) for key, label, color in (
        ("precision", "Precision", "#7446bb"),
        ("recall", "Recall", "#dc7a25"),
        ("f1_score", "F1", "#bd3e64"),
    ) if structured_summary]
    if not prf:
        (result_dir / "bieu_do_prf.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 110">'
            '<rect width="900" height="110" fill="white"/>'
            '<text x="24" y="55" font-family="Arial" font-size="20">'
            'Chưa có dự đoán trường để tính Precision, Recall và F1.</text></svg>',
            encoding="utf-8",
        )
    else:
        _bar_chart(result_dir / "bieu_do_prf.svg", "Precision, Recall và F1 theo kỹ thuật", assessment,
                   prf, percent=True)
    (result_dir / "bao_cao_truc_quan.html").write_text(
        _render_html(result_dir, assessment, _read_csv(result_dir / "structured_field_summary.csv"),
                     details, predictions, bool(structured_summary)),
        encoding="utf-8",
    )
    markdown_path = result_dir / "BAO_CAO.md"
    if markdown_path.is_file():
        current = markdown_path.read_text(encoding="utf-8")
    else:
        current = "# Kết quả so sánh OCR — bộ 40 hóa đơn\n"
    marker = "## Kết quả trích trường chung"
    current = current.split(marker, 1)[0].rstrip()
    if structured_summary:
        lines = [marker, "", "Các chỉ số này đánh giá đầu ra OCR sau khi áp dụng cùng bộ trích trường.", "",
                 "| Kỹ thuật | TP | FP | FN | Precision | Recall | F1 | Giây OCR/hóa đơn |",
                 "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for row in assessment:
            structured = structured_summary.get(row["engine"], {})
            lines.append(
                f"| {row['engine']} | {_first(structured, 'tp')} | {_first(structured, 'fp')} | "
                f"{_first(structured, 'fn')} | {_first(structured, 'precision')} | "
                f"{_first(structured, 'recall')} | {_first(structured, 'f1', 'f1_score')} | "
                f"{row['average_seconds_per_invoice']} |"
            )
    else:
        lines = [marker, "", "Chưa có dự đoán trường; Precision, Recall và F1 được để trống."]
    lines += ["", "Mở `bao_cao_truc_quan.html` để lọc kết quả theo kỹ thuật, hóa đơn và trạng thái.",
              "`chi_tiet_hoa_don.csv` hiển thị từng trường, giá trị chuẩn, dự đoán và dòng OCR.",
              "`bang_danh_gia.csv` và `structured_field_summary.csv` giữ các chỉ số ở định dạng CSV."]
    markdown_path.write_text(current + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
