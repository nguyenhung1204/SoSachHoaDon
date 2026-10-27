"""Embed local post-processors in the one-click Colab notebook."""

from __future__ import annotations

import base64
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NOTEBOOK = ROOT / "run_40HD_colab.ipynb"
MODULES = (
    "run_40hd_benchmark.py",
    "requirements_40hd.txt",
    "structured_metrics_40hd.py",
    "visual_report_40hd.py",
)


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
drive_bundle = Path('/content/drive/MyDrive/SoSachHoaDon/40HD_colab_bundle.zip')
if not drive_bundle.is_file():
    raise FileNotFoundError(
        'Không thấy 40HD_colab_bundle.zip trong My Drive/SoSachHoaDon. '
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
    required = {'40HD.json'}
    missing = required - names
    if missing:
        raise ValueError(f'ZIP thiếu file: {sorted(missing)}')
    pdf_count = sum(
        name.startswith('pdfs/') and name.lower().endswith('.pdf')
        for name in names
    )
    if pdf_count != 40:
        raise ValueError(f'ZIP có {pdf_count} PDF, cần đúng 40')
    (work / '40HD.json').write_bytes(archive.read('40HD.json'))
print(f'Đã kiểm tra: {pdf_count} PDF và dữ liệu nhãn', flush=True)

# Dùng đúng phiên bản benchmark cùng notebook; ZIP Drive chỉ cung cấp PDF và nhãn.
embedded_modules = {
__MODULES__
}
for filename, encoded in embedded_modules.items():
    (work / filename).write_bytes(base64.b64decode(encoded))

print('2/4 · Cài Tesseract, EasyOCR và PaddleOCR', flush=True)
subprocess.run(['apt-get', 'update', '-qq'], check=True)
subprocess.run(
    ['apt-get', 'install', '-y', '-qq',
     'tesseract-ocr', 'tesseract-ocr-eng', 'tesseract-ocr-vie'],
    check=True,
)
# Keep Colab's torch untouched while installing EasyOCR. PaddleOCR 3.x also
# needs PaddlePaddle >= 3.0, which is installed separately for the chosen device.
base_requirements = work / 'requirements_colab_base.txt'
base_requirements.write_text(
    ''.join(
        line for line in (work / 'requirements_40hd.txt').read_text(encoding='utf-8').splitlines(keepends=True)
        if not re.match(r'\\s*(?:paddlepaddle|easyocr)\\s*(?:[<=>!~]|$)', line, re.IGNORECASE)
    ),
    encoding='utf-8',
)
subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', '-r',
     str(base_requirements)],
    check=True,
)
# EasyOCR lists torch/torchvision as dependencies. Install its other runtime
# dependencies explicitly so pip cannot replace Colab's CUDA-enabled PyTorch.
subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q',
     'opencv-python-headless', 'scipy', 'numpy', 'scikit-image',
     'python-bidi', 'PyYAML', 'Shapely', 'pyclipper', 'ninja'],
    check=True,
)
subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', '--no-deps',
     'easyocr>=1.7.2'],
    check=True,
)

def ocr_stack_import_probe():
    return subprocess.run(
        [sys.executable, '-c',
         'import easyocr; import torch, torchvision; import paddle; '
         'from paddleocr import PaddleOCR; print("OCR_STACK_OK")'],
        capture_output=True, text=True, check=False,
    )

def ensure_torch_works():
    probe = ocr_stack_import_probe()
    if probe.returncode == 0 and 'OCR_STACK_OK' in probe.stdout:
        return
    error_text = probe.stderr or probe.stdout
    if not re.search(r'torch|nccl|cuda', error_text, re.IGNORECASE):
        raise RuntimeError('Không thể nạp bộ OCR trước khi chạy:\\n' + error_text)
    detail = error_text.strip().splitlines()
    print('PyTorch gặp lỗi thư viện CUDA/NCCL; đang cài bản CPU để EasyOCR chạy ổn định.', flush=True)
    if detail:
        print('Lỗi PyTorch:', detail[-1], flush=True)
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-q', '--force-reinstall',
         'torch', 'torchvision', '--index-url',
         'https://download.pytorch.org/whl/cpu'],
        check=True,
    )
    probe = ocr_stack_import_probe()
    if probe.returncode != 0 or 'OCR_STACK_OK' not in probe.stdout:
        raise RuntimeError(
            'Không thể nạp PyTorch cho EasyOCR kể cả sau khi chuyển sang CPU:\\n'
            + (probe.stderr or probe.stdout)
        )
    print('EasyOCR sẽ chạy CPU; PaddleOCR vẫn dùng GPU nếu khởi tạo được.', flush=True)

ensure_torch_works()
nvidia_probe = (
    subprocess.run(
        ['nvidia-smi', '-L'], capture_output=True, text=True, check=False
    ) if shutil.which('nvidia-smi') is not None else None
)
gpu_runtime = (
    nvidia_probe is not None and nvidia_probe.returncode == 0
    and bool(nvidia_probe.stdout.strip())
)

def install_cpu_paddle():
    subprocess.run(
        [sys.executable, '-m', 'pip', 'uninstall', '-y', '-q',
         'paddlepaddle', 'paddlepaddle-gpu'],
        check=False,
    )
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-q',
         'paddlepaddle==3.2.0', '-i',
         'https://www.paddlepaddle.org.cn/packages/stable/cpu/'],
        check=True,
    )

if gpu_runtime:
    print('Colab có GPU; đang cài PaddlePaddle GPU (CUDA 11.8)', flush=True)
    subprocess.run(
        [sys.executable, '-m', 'pip', 'uninstall', '-y', '-q',
         'paddlepaddle', 'paddlepaddle-gpu'],
        check=False,
    )
    gpu_install = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-q',
         'paddlepaddle-gpu==3.2.0', '-i',
         'https://www.paddlepaddle.org.cn/packages/stable/cu118/'],
        check=False,
    )
    paddle_gpu_probe = subprocess.run(
        [sys.executable, '-c',
         "import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0; print('GPU_OK')"],
        capture_output=True, text=True, check=False,
    ) if gpu_install.returncode == 0 else None
    if (paddle_gpu_probe is not None and paddle_gpu_probe.returncode == 0
            and 'GPU_OK' in paddle_gpu_probe.stdout):
        print('PaddleOCR dùng GPU; Tesseract vẫn chạy CPU', flush=True)
    else:
        print('Paddle GPU không khởi tạo được; chuyển PaddleOCR sang CPU', flush=True)
        install_cpu_paddle()
else:
    print('Colab hiện không cấp GPU; cài PaddlePaddle CPU', flush=True)
    install_cpu_paddle()

ensure_torch_works()
paddle_check = subprocess.run(
    [sys.executable, '-c',
     "import paddle; print('CUDA=' + str(paddle.is_compiled_with_cuda()))"],
    capture_output=True, text=True, check=True,
)
print('Thiết bị PaddleOCR:', 'GPU' if 'CUDA=True' in paddle_check.stdout else 'CPU', flush=True)
language_check = subprocess.run(
    ['tesseract', '--list-langs'], check=True, capture_output=True, text=True
)
languages = set((language_check.stdout + '\\n' + language_check.stderr).split())
if not {'eng', 'vie'} <= languages:
    raise RuntimeError(f'Tesseract thiếu eng/vie. Ngôn ngữ hiện có: {sorted(languages)}')
print('Đã cài xong và có đủ ngôn ngữ eng/vie', flush=True)

print('3/4 · Chạy 3 OCR và đánh giá 40 hóa đơn; hãy giữ Colab kết nối', flush=True)
drive_results_root = Path('/content/drive/MyDrive/SoSachHoaDon/ket_qua')
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
benchmark_log = drive_results / 'benchmark.log'
benchmark_command = [
    sys.executable, '-u', str(work / 'run_40hd_benchmark.py'),
    '--dataset-zip', str(local_bundle),
    '--dpi', '300',
    '--device', 'auto',
    '--output-dir', str(drive_results),
    '--resume',
]
output_tail = []
with benchmark_log.open('a', encoding='utf-8') as log:
    log.write(f'\\n--- Benchmark bắt đầu {datetime.now().isoformat()} ---\\n')
    log.flush()
    process = subprocess.Popen(
        benchmark_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    for line in process.stdout:
        print(line, end='', flush=True)
        log.write(line)
        output_tail.append(line.rstrip())
        if len(output_tail) > 80:
            del output_tail[:-80]
    return_code = process.wait()
if return_code:
    raise RuntimeError(
        f'Benchmark dừng với mã lỗi {return_code}. '
        f'Log đầy đủ được lưu tại {benchmark_log}. '
        '80 dòng cuối từ tiến trình OCR:\\n' + '\\n'.join(output_tail)
    )

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
                "Đặt `40HD_colab_bundle.zip` trong `My Drive/SoSachHoaDon`, chọn **Runtime → Change runtime type → GPU** nếu Colab cấp GPU, rồi bấm **Run all** bằng tài khoản Colab hiện tại. Cấp quyền Google Drive khi được hỏi. GPU có thể không được cấp; notebook giữ PyTorch có sẵn của Colab, và nếu thư viện CUDA/NCCL xung đột sau khi cài Paddle thì EasyOCR tự chuyển sang PyTorch CPU. PaddleOCR vẫn dùng GPU nếu khởi tạo thành công; Tesseract luôn chạy CPU.\n",
                "Notebook tự cài thư viện phù hợp, chạy cùng 40 PDF ở 300 DPI, dùng chung bộ trích trường để tính Precision, Recall, F1, tạo biểu đồ và lưu checkpoint/kết quả vào `My Drive/SoSachHoaDon/ket_qua/run_...`. Thiết bị thật của từng OCR được ghi trong `metadata.json`; giữ Colab kết nối đến khi hoàn tất.\n",
                "\n",
                "Trong thư mục `ket_qua/run_...`, mở `bao_cao_truc_quan.html` để xem biểu đồ, lọc từng kỹ thuật/hóa đơn/trạng thái và xem giá trị trường nào đúng, sai hoặc thiếu. `chi_tiet_hoa_don.csv` ghi từng giá trị và dòng OCR; `predictions.csv` giữ toàn văn OCR. Tỷ lệ tìm thấy giá trị toàn trang được báo riêng với P/R/F1 của OCR cộng bộ trích trường.\n",
            ],
        },
        code_cells[0],
    ]
    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
