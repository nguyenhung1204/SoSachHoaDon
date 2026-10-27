"""Embed local post-processors in the one-click Colab notebook."""

from __future__ import annotations

import base64
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NOTEBOOK = ROOT / "run_40HD_colab.ipynb"
MODULES = ("structured_metrics_40hd.py", "visual_report_40hd.py")


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    if len(code_cells) != 1:
        raise ValueError("Notebook phải có đúng một ô code chạy toàn bộ quy trình")
    code = "".join(code_cells[0]["source"])

    source = """from google.colab import drive
from IPython.display import Markdown, display
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from zipfile import ZipFile
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys

# Chỉ cần để ZIP trong thư mục này trên Google Drive.
drive.mount('/content/drive')
drive_bundle = Path('/content/drive/MyDrive/SoSanhOCR_40HD/40HD_colab_bundle.zip')
if not drive_bundle.is_file():
    raise FileNotFoundError(
        'Không thấy 40HD_colab_bundle.zip trong My Drive/SoSanhOCR_40HD. '
        f'Đường dẫn đã kiểm tra: {drive_bundle}'
    )

print('1/4 · Đọc bộ 40 hóa đơn từ Google Drive', flush=True)
local_bundle = Path('/content/40HD_colab_bundle.zip')
shutil.copy2(drive_bundle, local_bundle)
bundle_sha256 = hashlib.sha256(local_bundle.read_bytes()).hexdigest()
work = Path('/content/40hd-benchmark')
work.mkdir(parents=True, exist_ok=True)
with ZipFile(local_bundle) as archive:
    names = set(archive.namelist())
    required = {'40HD.json', 'run_40hd_benchmark.py', 'requirements_40hd.txt'}
    missing = required - names
    if missing:
        raise ValueError(f'ZIP thiếu file: {sorted(missing)}')
    pdf_count = sum(
        name.startswith('pdfs/') and name.lower().endswith('.pdf')
        for name in names
    )
    if pdf_count != 40:
        raise ValueError(f'ZIP có {pdf_count} PDF, cần đúng 40')
    for name in ('run_40hd_benchmark.py', 'requirements_40hd.txt', '40HD.json'):
        (work / name).write_bytes(archive.read(name))
print(f'Đã kiểm tra: {pdf_count} PDF và dữ liệu nhãn', flush=True)

print('2/4 · Cài Tesseract, EasyOCR và PaddleOCR', flush=True)
subprocess.run(['apt-get', 'update', '-qq'], check=True)
subprocess.run(
    ['apt-get', 'install', '-y', '-qq',
     'tesseract-ocr', 'tesseract-ocr-eng', 'tesseract-ocr-vie'],
    check=True,
)
subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', '-r',
     str(work / 'requirements_40hd.txt')],
    check=True,
)
language_check = subprocess.run(
    ['tesseract', '--list-langs'], check=True, capture_output=True, text=True
)
languages = set((language_check.stdout + '\\n' + language_check.stderr).split())
if not {'eng', 'vie'} <= languages:
    raise RuntimeError(f'Tesseract thiếu eng/vie. Ngôn ngữ hiện có: {sorted(languages)}')
print('Đã cài xong và có đủ ngôn ngữ eng/vie', flush=True)

print('3/4 · Chạy 3 OCR và đánh giá 40 hóa đơn; hãy giữ Colab kết nối', flush=True)
drive_results_root = Path('/content/drive/MyDrive/SoSanhOCR_40HD/ket_qua')
drive_results_root.mkdir(parents=True, exist_ok=True)
active_run_file = drive_results_root / '.active_run'
try:
    active_state = json.loads(active_run_file.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError):
    active_state = {}
if not isinstance(active_state, dict):
    active_state = {}
run_id = active_state.get('run_id', '')
if (not isinstance(run_id, str)
        or not re.fullmatch(r'run_[A-Za-z0-9_-]{1,64}', run_id)
        or active_state.get('bundle_sha256') != bundle_sha256):
    run_id = ''
drive_results = drive_results_root / run_id if run_id else None
if drive_results is None or (drive_results / 'COMPLETE').is_file():
    run_id = datetime.now().strftime('run_%Y%m%d_%H%M%S') + '_' + uuid4().hex[:6]
    drive_results = drive_results_root / run_id
active_run_file.write_text(
    json.dumps({'run_id': run_id, 'bundle_sha256': bundle_sha256}), encoding='utf-8'
)
drive_results.mkdir(parents=True, exist_ok=True)
print(f'Kết quả và checkpoint được ghi trực tiếp lên Drive: {drive_results}', flush=True)
subprocess.run(
    [sys.executable, '-u', str(work / 'run_40hd_benchmark.py'),
     '--dataset-zip', str(local_bundle),
     '--dpi', '300',
     '--output-dir', str(drive_results),
     '--resume'],
    check=True,
)

# Hai bộ hậu xử lý được nhúng trong notebook để không cần tải thêm file.
embedded_modules = {
__MODULES__
}
for filename, encoded in embedded_modules.items():
    (work / filename).write_bytes(base64.b64decode(encoded))
sys.path.insert(0, str(work))
from structured_metrics_40hd import evaluate_run
from visual_report_40hd import generate_visual_report
evaluate_run(drive_results / 'predictions.csv', work / '40HD.json', drive_results,
             regions_jsonl=drive_results / 'ocr_regions.jsonl')
generate_visual_report(drive_results)

print('4/4 · Đóng gói kết quả đã lưu trên Google Drive', flush=True)
archive_path = shutil.make_archive(
    str(drive_results), 'zip', root_dir=drive_results
)
(drive_results / 'COMPLETE').write_text('OCR, đánh giá, báo cáo và ZIP đã hoàn tất.', encoding='utf-8')
print(f'Thư mục kết quả trên Drive: {drive_results}')
print(f'ZIP kết quả trên Drive: {archive_path}')
print('Mở bao_cao_truc_quan.html trong thư mục kết quả để xem biểu đồ và lọc từng trường.')
display(Markdown((drive_results / 'BAO_CAO.md').read_text(encoding='utf-8')))
"""
    embedded = "\n".join(
        f"    {json.dumps(name)}: {json.dumps(base64.b64encode((ROOT / name).read_bytes()).decode('ascii'))},"
        for name in MODULES
    )
    source = source.replace("__MODULES__", embedded)
    code_cells[0]["source"] = source.splitlines(keepends=True)
    notebook["cells"] = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# So sánh 3 kỹ thuật OCR trên 40 hóa đơn\n",
                "\n",
                "Đặt `40HD_colab_bundle.zip` trong `My Drive/SoSanhOCR_40HD`, rồi bấm **Run all**. Cấp quyền Google Drive khi Colab hỏi. Notebook tự cài các thư viện, chạy Tesseract, EasyOCR, PaddleOCR trên cùng 40 PDF ở 300 DPI, dùng chung bộ trích trường để tính Precision, Recall, F1, tạo biểu đồ và lưu toàn bộ kết quả vào Drive.\n",
                "\n",
                "Trong thư mục `ket_qua/run_...`, mở `bao_cao_truc_quan.html` để xem biểu đồ, lọc từng kỹ thuật/hóa đơn/trạng thái và xem giá trị trường nào đúng, sai hoặc thiếu. `chi_tiet_hoa_don.csv` ghi từng giá trị và dòng OCR; `predictions.csv` giữ toàn văn OCR. Tỷ lệ tìm thấy giá trị toàn trang được báo riêng với P/R/F1 của OCR cộng bộ trích trường.\n",
            ],
        },
        code_cells[0],
    ]
    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
