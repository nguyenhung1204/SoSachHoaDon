# Gán nhãn crop hóa đơn

App chạy trên Windows tại localhost để cắt dòng chữ từ PDF, xác nhận bản chép và chuẩn bị dữ liệu recognition PaddleOCR. PDF, nhãn và crop được xử lý trên máy; app không gửi nội dung hóa đơn lên dịch vụ mạng.

## Chạy app

1. Clone repo `SoSachHoaDon` hoặc cập nhật nhánh được chia sẻ.
2. Mở PowerShell tại thư mục `ocr_labeler`.
3. Chạy:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\start_labeler.ps1
   ```

Script tạo môi trường Python riêng nếu cần, cài Pillow và PyPDFium2, rồi mở `http://127.0.0.1:8765/`. Nhấn **Ctrl+C** trong cửa sổ PowerShell để dừng.

Ở lần chạy đầu app đọc `40HD_colab_bundle.zip` ở thư mục gốc repo và bung 40 PDF cùng `40HD.json` vào `ocr_labeler\state\source`. Bundle gốc không bị sửa. Không cần Node.js hoặc Docker.

## Cắt và gán nhãn

1. Chọn hóa đơn ở cột trái; kiểm tra split train, validation hoặc test.
2. Kéo khung sát một dòng hoặc giá trị cần nhận diện. Cắt các cột trong bảng thành từng crop riêng; crop không quá 25 ký tự theo cấu hình PP-OCRv6.
3. Bấm **Lưu crop và nhập nhãn**.
4. Ở thẻ crop, bấm **Lấy chữ từ vùng PDF**. App hiển thị phần chữ lấy từ lớp text PDF; bấm **Chép vào ô nhãn** để điền vào ô. Đối chiếu với ảnh crop, sửa nếu cần, chọn loại nội dung và tự tích xác nhận.
5. Nếu PDF là bản scan hoặc vùng không có lớp chữ, app sẽ báo không lấy được chữ; nhập nhãn bằng tay theo ảnh crop.

JSON trong bundle chỉ là gợi ý cấp trường, không thay thế bản chép đã đối chiếu với crop. Không thêm nhãn theo suy đoán. Các split được gán ở cấp hóa đơn, mặc định 30/5/5; giữ nguyên split để không chia các crop của một hóa đơn qua nhiều tập.

## Cùng gán nhãn với người khác

- Mỗi người clone cùng nhánh/repo và chạy app trên máy riêng. Chia các hóa đơn để tránh hai người cắt trùng; nhớ có crop ở cả train, validation và test.
- Sau khi đã xác nhận nhãn, bấm **Xuất gói nhãn dở dang**. ZIP chỉ chứa ảnh crop và metadata của crop đã xác nhận, không chứa bản sao PDF.
- Gửi ZIP cho người đang tổng hợp. Trên máy tổng hợp bấm **Nhập gói nhãn** và chọn ZIP. App kiểm tra hash của `40HD.json`, PDF tương ứng và split trước khi nhập.
- Crop trùng khớp được bỏ qua. Nếu cùng crop ID hoặc cùng vùng nhưng nhãn/ảnh khác nhau, app báo xung đột và không ghi đè nhãn có sẵn. Hãy đối chiếu thủ công rồi chỉ nhập gói đã xử lý.
- Không gửi hoặc commit `state\labeler.sqlite3`, thư mục `state\crops`, `exports` hay file PDF/JSON riêng lẻ lên GitHub.

## Xuất dữ liệu recognition

Nút **Xuất ZIP recognition** chỉ bật khi dữ liệu qua kiểm tra: split hóa đơn 30/5/5, mọi crop có ảnh hợp lệ và nhãn đã xác nhận, ký tự thuộc dictionary, và mỗi split có crop. ZIP gồm `images/`, `train.txt`, `val.txt`, `test.txt`, `manifest.csv`, `invoice_splits.csv` và dictionary. Mỗi dòng list có dạng `đường_dẫn_ảnh<TAB>nội_dung_nhìn_thấy`, UTF-8.

ZIP xuất được lưu trong `ocr_labeler\exports` và tải xuống trình duyệt. Có thể mở file `manifest.csv` để xem hóa đơn, trang, tọa độ, loại trường, split và nhãn của từng crop.

## Dữ liệu local

- `state\source`: bản bung cục bộ từ ZIP; có thể xóa khi app đã dừng để giải phóng dung lượng, lần chạy sau sẽ bung lại.
- `state\labeler.sqlite3`, `state\crops`: trạng thái và crop đã gán nhãn.
- `state\pages`: cache ảnh trang PDF.
- `state\invoice_splits.csv`: split ở cấp hóa đơn.
- `exports`: các ZIP recognition và gói bàn giao đã tạo.

Toàn bộ các thư mục trên bị `.gitignore` loại khỏi commit. Sao lưu gói bàn giao hoặc cả thư mục `state` nếu muốn giữ dữ liệu khi chuyển máy.

## Giới hạn

App chuẩn bị ảnh và nhãn cho **text recognition**; nó không tìm vùng chữ tự động, không phân tích bảng và không gán nội dung vào cột hóa đơn. Chữ trích từ PDF chỉ là bản nháp; người gán phải xem crop và xác nhận. 5 hóa đơn test từng xuất hiện trong lần so sánh OCR trước, nên điểm test chỉ có tính tham khảo.
