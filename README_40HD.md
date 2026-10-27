# So sánh 3 kỹ thuật OCR trên 40 hóa đơn

Bộ chạy này dùng `40HD.json` và đúng 40 PDF được gán nhãn. Nó so sánh Tesseract (`vie+eng`), EasyOCR (`vi`, `en`) và PaddleOCR (`vi`) trên cùng ảnh raster hóa ở 300 DPI. Mặc định chạy CPU để kết quả cũ không đổi; chạy local có thể chọn tự nhận GPU cho EasyOCR/PaddleOCR bằng `--device auto`. Tesseract luôn chạy CPU.

Hiện bộ nhãn có 860 trường: 833 giá trị đã được xác nhận trên PDF, 16 trường đánh dấu không có trên hóa đơn, 10 trường không áp dụng và 1 trường còn chờ đọc. Chỉ 833 giá trị đã xác nhận được đưa vào mẫu số; giá trị `raw` được dùng làm dự phòng khi `normalized` để trống.

## Chạy nhanh trên Google Colab

1. Tải `40HD_colab_bundle.zip` ở thư mục gốc repo này lên thư mục `My Drive/SoSachHoaDon` trên Google Drive. Nếu đã clone repo trên máy, ZIP nằm sẵn ở đó; nếu cần tạo lại từ PDF và nhãn cục bộ, chạy `python package_40hd_dataset.py`.
2. Mở [Google Colab](https://colab.research.google.com/), chọn **File → Upload notebook** và chọn `run_40HD_colab.ipynb`. Nếu bạn đã tải notebook bản cũ lên Drive, hãy thay bằng bản mới; ZIP dữ liệu vẫn dùng nguyên file hiện tại.
3. Chọn **Runtime → Change runtime type → GPU** nếu Colab cấp GPU. Bấm **Run all** hoặc chạy ô code duy nhất bằng tài khoản Colab hiện tại, rồi cấp quyền Google Drive khi được hỏi. Colab không đảm bảo GPU luôn sẵn sàng; nếu không có GPU hoặc GPU không khởi tạo được, notebook tự cài PaddlePaddle CPU và tiếp tục.
4. Notebook cài Tesseract, EasyOCR và PaddleOCR, rồi chạy 40 hóa đơn ở 300 DPI với `--device auto`. Tesseract luôn chạy CPU; EasyOCR và PaddleOCR tự dùng GPU nếu nhận được, từng engine tự fallback CPU nếu không khởi tạo được. Cùng một bộ quy tắc trích trường được dùng để đánh giá cả ba. Kết quả và checkpoint được ghi trực tiếp tại `My Drive/SoSachHoaDon/ket_qua/run_...`; nếu Colab bị ngắt, mở notebook mới nhất và chạy lại để tiếp tục run chưa hoàn tất. Run chỉ được đánh dấu `COMPLETE` sau khi đã tạo báo cáo và ZIP. Khi run đã hoàn tất, lần chạy sau sẽ tạo thư mục run mới.

Notebook cài PaddlePaddle GPU 3.2.0 theo gói CUDA 11.8 chính thức; nếu bản GPU không cài hoặc không khởi tạo được, nó gỡ bản lỗi và cài PaddlePaddle CPU 3.2.0. Gói chạy thực tế và thiết bị của mỗi engine được ghi trong `metadata.json`; khi so sánh thời gian, cần báo cáo cùng cấu hình vì Tesseract vẫn chạy CPU trong khi hai engine còn lại có thể chạy GPU.

Các thư mục kết quả tạo bởi notebook cũ không có `ocr_regions.jsonl`, nên không thể khôi phục tọa độ từ văn bản OCR đã lưu. Hãy tải `run_40HD_colab.ipynb` mới nhất trong thư mục này lên Colab rồi chạy lại để lấy dữ liệu tọa độ và đánh giá gán trường.

Colab chạy notebook `.ipynb` bằng nút Run; mã `.py` bên trong ZIP được notebook gọi tự động. ZIP chứa hóa đơn và nhãn, nên hãy dùng tài khoản Drive mà bạn được phép lưu các tài liệu này.

## Chạy trên máy đã cài đủ công cụ

Trước tiên cài Tesseract binary và dữ liệu ngôn ngữ `vie`/`eng`, rồi cài:

```powershell
python -m pip install -r requirements_40hd.txt
python run_40hd_benchmark.py --limit 3 --output-dir results/40hd_smoke
python run_40hd_benchmark.py --output-dir results/40hd_full
```

Nếu Tesseract không có trong `PATH`, truyền `--tesseract-cmd C:\path\to\tesseract.exe`.

### Chạy trên Windows với NVIDIA GTX 1050

1. Dùng `40HD_colab_bundle.zip` ở thư mục gốc repo (đã có sau khi clone) và đặt cạnh `run_40hd_benchmark.py`. Cài Python 3.10 64-bit, Tesseract OCR kèm ngôn ngữ `vie` và `eng`, cùng driver NVIDIA đủ mới; kiểm tra GPU bằng `nvidia-smi`.
2. Tạo môi trường riêng và cài thư viện nền:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements_40hd.txt
```

3. Để dùng GPU, thay PyTorch/Paddle CPU bằng bản CUDA 11.8 (làm trong môi trường `.venv`):

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu118
.\.venv\Scripts\python.exe -m pip uninstall -y paddlepaddle
.\.venv\Scripts\python.exe -m pip install paddlepaddle-gpu==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
.\.venv\Scripts\python.exe -c "import torch,paddle; print('PyTorch CUDA:', torch.cuda.is_available()); print('Paddle CUDA:', paddle.is_compiled_with_cuda(), paddle.device.cuda.device_count())"
```

4. Chạy thử một hóa đơn trước:

```powershell
.\.venv\Scripts\python.exe run_40hd_benchmark.py --dataset-zip 40HD_colab_bundle.zip --limit 1 --device auto --output-dir ket_qua\kiem_tra
```

Nếu chạy thử xong, chạy đủ 40 hóa đơn:

```powershell
.\.venv\Scripts\python.exe run_40hd_benchmark.py --dataset-zip 40HD_colab_bundle.zip --device auto --resume --output-dir ket_qua\gtx1050
```

`auto` bật GPU riêng cho từng engine nếu PyTorch/Paddle nhận CUDA; nếu không nhận được hoặc model không khởi tạo được vì thiếu VRAM, engine đó tự chuyển sang CPU và ghi thiết bị thực tế vào `metadata.json`, `summary.csv` và `BAO_CAO.md`. Mỗi engine được giải phóng trước khi nạp engine kế tiếp để giảm áp lực VRAM. Nếu hết bộ nhớ GPU giữa lúc nhận dạng, chạy lại với `--device cpu --resume`; checkpoint GPU sẽ tự được tính lại theo cấu hình CPU. Kết quả mặc định nằm trong `ket_qua\gtx1050`; để lưu vào Google Drive, đặt `--output-dir` trỏ tới thư mục Google Drive for Desktop đã đồng bộ trên PC.

## Cách đọc điểm

Có **hai phép đo khác nhau**:

- `field_scores.csv` và cột `value_presence_rate` trong `bang_danh_gia.csv`: tỷ lệ giá trị chuẩn hóa xuất hiện đâu đó trong văn bản OCR toàn trang. Đây là phép đo *tìm thấy giá trị*, chưa chứng minh OCR gán đúng tên trường hoặc dòng hàng.
- Khi so khớp số, dấu phẩy/dấu chấm được chuẩn hóa; văn bản OCR thuần xem khoảng trắng là ranh giới token để không ghép dính hai ô liền nhau như số lượng `2` và đơn giá `445,454.55`. Khi có bbox, phép đo nhận dạng còn khôi phục nhóm số dùng dấu cách nếu các token nằm sát nhau trên cùng một dòng; các ô cách xa nhau không bị ghép. Vì vậy bộ kết quả cũ chỉ có text không thể phân biệt an toàn số dùng dấu cách hàng nghìn với các ô liền kề.
- `structured_field_scores.csv`, `structured_summary.csv` và các cột Precision, Recall, F1 trong `bang_danh_gia.csv`: kết quả sau khi cả ba OCR đi qua **cùng một bộ trích trường cố định**. Trường đầu hóa đơn lấy từ nhãn văn bản; bảng hàng lấy theo vị trí vùng OCR so với tiêu đề cột và hàng STT. Nếu trang tiếp theo bỏ tiêu đề, parser dùng bố cục đã nhận diện ở trang trước. Hàng không có STT chỉ được suy ra khi STT rõ ràng ở hai bên tạo đúng khoảng liên tục; nếu có thể đã mất hàng thì giữ là `ambiguous`. Mỗi dự đoán được đối chiếu đúng trường và dòng hàng với nhãn đã xác minh. Dự đoán sai giá trị tính một FP và một FN; giá trị bị bỏ sót tính FN; dự đoán ở trường được xác nhận không có trên PDF tính FP. Nhãn chưa xác minh không được chấm. Precision = TP/(TP+FP), Recall = TP/(TP+FN), F1 = 2PR/(P+R). Đây là kết quả **OCR cộng bộ trích trường chung**, không phải chỉ riêng chất lượng nhận dạng ký tự. Nếu không nhận ra tiêu đề/cột hoặc vị trí không đủ rõ thì parser không đoán.
- `structured_field_scores.csv`, `structured_summary.csv` và các cột Precision, Recall, F1 trong `bang_danh_gia.csv`: kết quả sau khi cả ba OCR đi qua **cùng một bộ trích trường cố định**. Trường đầu hóa đơn lấy từ nhãn văn bản; bảng hàng lấy theo vị trí vùng OCR so với tiêu đề cột và hàng STT. Nếu trang tiếp theo bỏ tiêu đề, parser dùng bố cục đã nhận diện ở trang trước. Hàng không có STT chỉ được suy ra khi STT rõ ràng ở hai bên tạo đúng khoảng liên tục; nếu có thể đã mất hàng thì giá trị OCR vẫn được lưu nhưng không nhận số dòng và không được tính FP như một dự đoán chắc chắn. Mẫu số chuẩn tương ứng vẫn tính là FN, với chẩn đoán `ambiguous` khi giá trị OCR khớp nhưng vị trí dòng không chắc. Mỗi dự đoán sai giá trị tính một FP và một FN; giá trị bị bỏ sót tính FN; dự đoán ở trường được xác nhận không có trên PDF tính FP. Nhãn chưa xác minh không được chấm. Precision = TP/(TP+FP), Recall = TP/(TP+FN), F1 = 2PR/(P+R). Đây là kết quả **OCR cộng bộ trích trường chung**, không phải chỉ riêng chất lượng nhận dạng ký tự. Nếu không nhận ra tiêu đề/cột hoặc vị trí không đủ rõ thì parser không đoán.

Trong `structured_field_scores.csv` và `chi_tiet_hoa_don.csv`, `diagnostic_status` giải thích nguyên nhân: `correct` (đúng giá trị, trường, dòng), `wrong_value` (đã gán nhưng sai giá trị), `wrong_assignment` (giá trị khớp được gán sang trường/dòng khác), `read_but_unassigned` (giá trị có trong văn bản OCR nhưng parser chưa gán được), `not_read` (không thấy giá trị trong văn bản OCR), `ambiguous` (có ứng viên nhưng vị trí chưa đủ chắc), `unscored` (nhãn chưa xác nhận hoặc không chấm). Chẩn đoán này giải thích lỗi, không cộng thêm TP/FP/FN ngoài cách tính slot đã nêu.

Mở `bao_cao_truc_quan.html` trong thư mục kết quả để xem biểu đồ và lọc theo kỹ thuật, hóa đơn, trạng thái. Nếu Drive không xem trực tiếp HTML, tải file này về rồi mở bằng Chrome; biểu đồ đã được nhúng trong file. `chi_tiet_hoa_don.csv` cho biết từng trường/dòng hàng, giá trị nhãn, giá trị dự đoán, chẩn đoán, vùng tọa độ nguồn và trường/dòng mà giá trị có thể bị gán nhầm; `predictions.csv` giữ nguyên văn bản OCR, còn `ocr_regions.jsonl` giữ hộp tọa độ từng trang. `structured_field_summary.csv` và `structured_invoice_summary.csv` tổng hợp theo trường và hóa đơn. `structured_evaluation.json` ghi quy tắc chấm; `BAO_CAO.md` tóm tắt cả hai phép đo.

Thời gian trung bình là thời gian **chạy OCR mỗi hóa đơn**, không tính bước raster hóa PDF chung cho ba kỹ thuật hay tải model khởi động. Vì cả ba nhận cùng ảnh 300 DPI, số này phù hợp để so sánh tốc độ suy luận OCR; khi báo cáo thời gian từ lúc tải PDF đến kết quả, cần cộng thời gian tiền xử lý và hậu xử lý riêng.

Kết quả Colab có thể chạy GPU hoặc CPU tùy thiết bị được cấp và khởi tạo thành công; không nhất thiết có cùng tốc độ với máy cá nhân. Lần đầu có thể tải các model; hãy ghi lại môi trường và thiết bị trong `metadata.json` khi trích số liệu vào báo cáo.
