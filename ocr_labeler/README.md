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

Script tạo môi trường Python riêng nếu cần, cài Pillow và PyPDFium2, rồi mở `http://127.0.0.1:8765/`. Nhấn **Ctrl+C** trong cửa sổ PowerShell để dừng. Nếu cổng 8765 đang được bản app khác dùng, chạy `$env:OCR_LABELER_PORT=8766` trước `.\start_labeler.ps1`; app sẽ mở ở `http://127.0.0.1:8766/`.

Ở lần chạy đầu app đọc `40HD_colab_bundle.zip` ở thư mục gốc repo và bung 40 PDF cùng `40HD.json` vào `ocr_labeler\state\source`. Bundle gốc không bị sửa. Không cần Node.js hoặc Docker.

## Cắt và gán nhãn

1. Chọn hóa đơn ở cột trái; kiểm tra split train, validation hoặc test.
2. Kéo khung sát một dòng hoặc giá trị cần nhận diện. Cắt các cột trong bảng thành từng crop riêng; mỗi nhãn tối đa 90 ký tự theo cấu hình fine-tune.
3. Bấm **Lưu crop và nhập nhãn**.
4. Ở thẻ crop, bấm **Lấy chữ từ vùng PDF**. App hiển thị phần chữ lấy từ lớp text PDF; bấm **Chép vào ô nhãn** để điền vào ô. Đối chiếu với ảnh crop, sửa nếu cần, chọn loại nội dung và tự tích xác nhận. Nếu khung lấy nhiều dòng, app sẽ yêu cầu crop riêng từng dòng để không nối sai nội dung.
5. Nếu PDF là bản scan hoặc vùng không có lớp chữ, app sẽ báo không lấy được chữ; nhập nhãn bằng tay theo ảnh crop.

JSON trong bundle chỉ là gợi ý cấp trường, không thay thế bản chép đã đối chiếu với crop. Không thêm nhãn theo suy đoán. Các split được gán ở cấp hóa đơn, mặc định 30/5/5; giữ nguyên split để không chia các crop của một hóa đơn qua nhiều tập.

## Cùng gán nhãn với người khác

- Cả ba máy clone/cập nhật cùng nhánh và dùng cùng `40HD_colab_bundle.zip`. Trong danh sách **Người dùng**, mỗi người chọn đúng phạm vi của mình.
- **Duy** chọn `Duy · hóa đơn 1–20`: `INV-0001`–`INV-0020`, tương ứng `HD_001.pdf`–`HD_020.pdf`.
- **Vân** chọn `Vân · hóa đơn 21–40`: `INV-0021`–`INV-0030` tương ứng `HD_021.pdf`–`HD_030.pdf`; các hóa đơn còn lại là `INV-0031/HD_173.pdf`, `INV-0032/HD_182.pdf`, `INV-0033/HD_184.pdf`, `INV-0034/HD_186.pdf`, `INV-0035/HD_189.pdf`, `INV-0036/HD_193.pdf`, `INV-0037/HD_196.pdf`, `INV-0038/HD_202.pdf`, `INV-0039/HD_208.pdf`, `INV-0040/HD_209.pdf`. App lấy cặp mã này theo đúng thứ tự trong JSON nguồn và ghi danh sách đầy đủ vào manifest.
- Với từng hóa đơn, cắt và xác nhận các crop. Khi đã xem hết PDF, bấm **Đánh dấu đã rà soát**. Nếu hóa đơn không có vùng chữ phù hợp, có thể hoàn tất với 0 crop sau khi xác nhận hộp thoại. Hóa đơn có crop chưa xác nhận sẽ không được đánh dấu hoàn tất. Thêm, sửa hoặc xóa crop sẽ mở lại trạng thái rà soát.
- Khi cả 20 hóa đơn hiện **Đã rà soát**, bấm **Xuất ZIP Duy · 20 hóa đơn** hoặc **Xuất ZIP Vân · 20 hóa đơn**. Gói chứa crop đã xác nhận và manifest (mã hóa đơn/PDF, split, tọa độ, loại trường, nhãn, người gán và checksum nguồn/ảnh); không chứa PDF đầy đủ, JSON nguồn hoặc SQLite.
- Người tổng hợp chọn `Tổng hợp · đủ 40 hóa đơn`, lần lượt bấm **Nhập gói nhãn** và chọn ZIP của Duy rồi Vân. App kiểm tra định dạng, đúng phạm vi 20 hóa đơn, `40HD.json`, từng PDF và split trước khi nhập.
- Crop trùng hoàn toàn được bỏ qua. Nếu crop hoặc người rà soát bị xung đột, cả lần nhập đó bị từ chối và nhãn hiện có không bị ghi đè. Hãy đối chiếu gói với người gán nhãn trước khi thử lại.
- Sau khi thanh tiến độ ghi `40/40 rà soát`, dùng **Xuất ZIP recognition**. App chỉ xuất khi crop hợp lệ, đã xác nhận, ký tự thuộc dictionary và có mẫu ở cả ba split. ZIP cuối giữ nguyên split 30/5/5.
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

## Kiểm thử app gán nhãn

Trong PowerShell ở `ocr_labeler`, sau khi chạy `start_labeler.ps1`:

```powershell
.\.venv\Scripts\python.exe -m unittest test_labeler -v
```

Kiểm thử tạo dữ liệu tạm và xóa riêng dữ liệu tạm đó; PDF, JSON, SQLite và crop thật không bị sửa.

## Giới hạn

App chuẩn bị ảnh và nhãn cho **text recognition**; nó không tìm vùng chữ tự động, không phân tích bảng và không gán nội dung vào cột hóa đơn. Chữ trích từ PDF chỉ là bản nháp; người gán phải xem crop và xác nhận. 5 hóa đơn test từng xuất hiện trong lần so sánh OCR trước, nên điểm test chỉ có tính tham khảo.
