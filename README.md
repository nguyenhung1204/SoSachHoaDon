# SoSánh OCR hóa đơn

Bộ mã so sánh Tesseract, EasyOCR và PaddleOCR trên cùng 40 hóa đơn. Quy trình dùng ảnh 300 DPI và cùng bộ trích trường để đo khả năng đọc, gán đúng trường/dòng, Precision, Recall, F1 và thời gian OCR.

## Chạy nhanh bằng Google Colab

Dữ liệu hóa đơn và nhãn không nằm trên GitHub. Giữ `40HD_colab_bundle.zip` trong Google Drive riêng của bạn.

1. Trên Drive, tạo `My Drive/SoSachHoaDon` và tải `40HD_colab_bundle.zip` vào thư mục đó.
2. Mở [notebook chạy 40 hóa đơn trên Colab](https://colab.research.google.com/github/nguyenhung1204/SoSachHoaDon/blob/main/run_40HD_colab.ipynb).
3. Bấm **Run all** và cấp quyền Google Drive khi được hỏi. Lần đầu Colab sẽ cài OCR và tải model; hãy giữ phiên Colab kết nối đến khi hoàn tất.
4. Mở `My Drive/SoSachHoaDon/ket_qua/run_.../bao_cao_truc_quan.html` để xem biểu đồ và lọc từng hóa đơn/trường. File CSV chi tiết và ZIP kết quả cũng nằm trong cùng thư mục run.

Notebook ghi checkpoint trực tiếp vào Drive. Nếu Colab bị ngắt giữa chừng, chạy lại notebook để tiếp tục run chưa hoàn tất. Run hoàn chỉnh được đánh dấu bằng file `COMPLETE`.

**Không có dữ liệu hóa đơn trong repo này.** ZIP chứa PDF và nhãn được giữ trên Drive để tránh công khai hóa đơn và thông tin doanh nghiệp. Nếu cần lấy lại ZIP, dùng bộ dữ liệu mà chủ repo đã chia sẻ riêng.

## Chạy kiểm thử

Cần Python 3.10+:

```powershell
python -m unittest test_40hd_benchmark test_structured_metrics_40hd test_visual_report_40hd test_build_colab_notebook -v
```

Bài kiểm thử xác nhận bộ dữ liệu 40 hóa đơn sẽ tự bỏ qua nếu bạn chưa có dữ liệu riêng.

## Tài liệu chi tiết

Xem [README_40HD.md](README_40HD.md) để biết định nghĩa chỉ số, đầu ra và cách chạy OCR trên máy cá nhân.
