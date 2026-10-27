# SoSánh OCR hóa đơn

Bộ mã so sánh Tesseract, EasyOCR và PaddleOCR trên cùng 40 hóa đơn. Quy trình dùng ảnh 300 DPI và cùng bộ trích trường để đo khả năng đọc, gán đúng trường/dòng, Precision, Recall, F1 và thời gian OCR.

## Chạy nhanh bằng Google Colab

Repo công khai này có kèm `40HD_colab_bundle.zip` ở thư mục gốc, gồm 40 PDF hóa đơn thật và nhãn tương ứng. Ai có quyền truy cập repo đều có thể tải các tài liệu này; chỉ sử dụng nếu bạn được phép xử lý và chia sẻ dữ liệu hóa đơn.

1. Tải `40HD_colab_bundle.zip` từ repo về máy, rồi tải lên thư mục `My Drive/SoSachHoaDon` trên Google Drive.
2. Mở [notebook chạy 40 hóa đơn trên Colab](https://colab.research.google.com/github/nguyenhung1204/SoSachHoaDon/blob/main/run_40HD_colab.ipynb).
3. Trong Colab, chọn **Runtime → Change runtime type → GPU** nếu GPU đang khả dụng, rồi bấm **Run all** bằng tài khoản Colab hiện tại và cấp quyền Google Drive. Colab có thể không cấp GPU; notebook tự cài bản Paddle phù hợp và chạy CPU khi thiếu GPU hoặc GPU không khởi tạo được. Tesseract luôn chạy CPU. Lần đầu notebook cài OCR và tải model; hãy giữ phiên Colab kết nối đến khi hoàn tất.
4. Mở `My Drive/SoSachHoaDon/ket_qua/run_.../bao_cao_truc_quan.html` để xem biểu đồ và lọc từng hóa đơn/trường. File CSV chi tiết và ZIP kết quả cũng nằm trong cùng thư mục run.

Notebook ghi checkpoint trực tiếp vào Drive. Nếu Colab bị ngắt giữa chừng, chạy lại notebook để tiếp tục run chưa hoàn tất. Run hoàn chỉnh được đánh dấu bằng file `COMPLETE`.

Thiết bị thực tế của từng OCR được ghi trong `metadata.json`. Thời gian benchmark chỉ so sánh được trong cấu hình đã ghi; EasyOCR/PaddleOCR có thể dùng GPU, còn Tesseract dùng CPU.

Khi clone repo để chạy trên máy cá nhân, ZIP dữ liệu đã nằm sẵn ở thư mục gốc; không cần tải riêng từ Drive.

## Chạy kiểm thử

Cần Python 3.10+:

```powershell
python -m unittest test_40hd_benchmark test_structured_metrics_40hd test_visual_report_40hd test_build_colab_notebook -v
```

Bài kiểm thử xác nhận bộ dữ liệu 40 hóa đơn sẽ tự bỏ qua nếu bạn chưa có dữ liệu riêng.

## Tài liệu chi tiết

Xem [README_40HD.md](README_40HD.md) để biết định nghĩa chỉ số, đầu ra và cách chạy OCR trên máy cá nhân.
