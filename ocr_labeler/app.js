const $ = (selector) => document.querySelector(selector);
const invoiceListEl = $("#invoice-list");
const invoiceTotalEl = $("#invoice-total");
const invoiceProgressEl = $("#invoice-progress");
const saveIndicatorEl = $("#save-indicator");
const pageImageEl = $("#page-image");
const pageStageEl = $("#page-stage");
const selectionLayerEl = $("#selection-layer");
const selectionPreviewEl = $("#selection-preview");
const cropListEl = $("#crop-list");
const toastEl = $("#toast");

let allInvoices = [];
let visibleInvoices = [];
let activeInvoice = null;
let activeIndex = -1;
let pageIndex = 0;
let pageCount = 1;
let pageWidth = 0;
let pageHeight = 0;
let zoom = 0.5;
let selection = null;
let drawing = false;
let imageUrl = null;
let fieldTypes = {};
let saveTimers = new Map();
let savePromises = new Map();
let pendingDeleteId = null;
let toastTimer = null;
let readinessTimer = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `Lỗi máy chủ (${response.status})`;
    try { message = (await response.json()).error || message; } catch { /* response was not JSON */ }
    throw new Error(message);
  }
  return response;
}

function toast(message, isError = false) {
  toastEl.textContent = message;
  toastEl.classList.toggle("error", isError);
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3000);
}

function setSaveStatus(message, error = false) {
  saveIndicatorEl.innerHTML = `<i></i>${escapeHtml(message)}`;
  saveIndicatorEl.classList.toggle("error", error);
}

function statusText(invoice) {
  if (!invoice.crop_count) return "Chưa cắt crop";
  if (invoice.confirmed_count === invoice.crop_count) return "Đã xác nhận";
  return "Cần gán nhãn";
}

function renderInvoiceList() {
  const q = $("#search").value.trim().toLocaleLowerCase("vi");
  const split = $("#split-filter").value;
  const status = $("#status-filter").value;
  visibleInvoices = allInvoices.filter((invoice) => {
    const matchText = !q || `${invoice.id} ${invoice.pdf} ${invoice.supplier_name}`.toLocaleLowerCase("vi").includes(q);
    const matchSplit = split === "all" || invoice.split === split;
    const matchStatus = status === "all" || (status === "empty" && invoice.crop_count === 0)
      || (status === "review" && (!invoice.crop_count || invoice.confirmed_count < invoice.crop_count))
      || (status === "done" && invoice.crop_count > 0 && invoice.crop_count === invoice.confirmed_count);
    return matchText && matchSplit && matchStatus;
  });
  invoiceListEl.innerHTML = visibleInvoices.map((invoice) => `
    <button class="invoice-item ${activeInvoice?.id === invoice.id ? "selected" : ""}" data-invoice="${escapeHtml(invoice.id)}" role="listitem" type="button" aria-current="${activeInvoice?.id === invoice.id ? "true" : "false"}">
      <span><span class="invoice-id">${escapeHtml(invoice.id)}</span><span class="invoice-pdf">${escapeHtml(invoice.pdf)}</span></span>
      <span class="invoice-count">${invoice.confirmed_count}/${invoice.crop_count} nhãn</span>
      <span class="invoice-vendor" title="${escapeHtml(invoice.supplier_name)}">${escapeHtml(invoice.supplier_name || "Chưa có tên nhà cung cấp")}</span>
    </button>`).join("") || `<div class="empty-state compact"><h3>Không tìm thấy</h3><p>Thử đổi từ khóa hoặc bộ lọc.</p></div>`;
  invoiceListEl.querySelectorAll("[data-invoice]").forEach((button) => {
    button.addEventListener("click", () => selectInvoice(button.dataset.invoice));
  });
  const done = allInvoices.reduce((total, invoice) => total + Number(invoice.confirmed_count || 0), 0);
  const cropTotal = allInvoices.reduce((total, invoice) => total + Number(invoice.crop_count || 0), 0);
  invoiceTotalEl.textContent = allInvoices.length;
  invoiceProgressEl.textContent = `${done}/${cropTotal} nhãn`;
}

async function refreshInvoices() {
  const response = await api("/api/invoices");
  allInvoices = (await response.json()).invoices;
  renderInvoiceList();
}

async function refreshReadiness() {
  clearTimeout(readinessTimer);
  return new Promise((resolve) => {
    readinessTimer = setTimeout(async () => {
      try { resolve(await refreshReadinessNow()); }
      catch (error) { resolve(null); }
    }, 250);
  });
}

async function refreshReadinessNow() {
  const response = await api("/api/readiness");
  const data = await response.json();
  const card = $("#readiness-card");
  const counts = data.split_counts;
  const title = data.data_ready ? "Dữ liệu recognition đã qua kiểm tra" : "Chưa đủ điều kiện xuất / huấn luyện";
  const issues = [...data.errors.slice(0, 3), ...data.warnings.slice(0, 3)];
  card.className = `readiness-card ${data.data_ready ? "ready" : "blocked"}`;
  card.innerHTML = `<strong>${escapeHtml(title)}</strong>
    <div>Hóa đơn train / val / test: ${counts.train}/${counts.val}/${counts.test} · Crop đã xác nhận: ${data.confirmed_count}/${data.crop_count}</div>
    <div>PP-OCRv6 dictionary: ${data.errors.some((item) => item.includes("dictionary")) ? "chưa sẵn sàng" : "đã có"} · Source/checkpoint train: ${data.training_assets_ready ? "đã có" : "chưa tải"}</div>
    ${issues.length ? `<ul>${issues.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}`;
  $("#export-button").disabled = data.errors.length > 0;
}

async function selectInvoice(invoiceId) {
  if (activeInvoice?.id === invoiceId) return;
  await flushSaves();
  activeIndex = visibleInvoices.findIndex((invoice) => invoice.id === invoiceId);
  if (activeIndex < 0) activeIndex = allInvoices.findIndex((invoice) => invoice.id === invoiceId);
  pageIndex = 0;
  activeInvoice = { id: invoiceId };
  renderInvoiceList();
  await loadActiveInvoice();
}

async function loadActiveInvoice(options = {}) {
  if (!activeInvoice?.id) return;
  const wantedPage = options.keepPage ? pageIndex : 0;
  try {
    const response = await api(`/api/invoice/${encodeURIComponent(activeInvoice.id)}`);
    activeInvoice = await response.json();
    const found = allInvoices.findIndex((item) => item.id === activeInvoice.id);
    if (found >= 0) activeIndex = found;
    $("#pdf-name").textContent = activeInvoice.pdf;
    $("#pdf-meta").textContent = `${activeInvoice.id} · ${activeInvoice.supplier_name || "Chưa có nhà cung cấp"}`;
    $("#invoice-position").textContent = `${activeIndex + 1} / ${allInvoices.length}`;
    $("#invoice-split").disabled = false;
    $("#invoice-split").value = activeInvoice.split;
    $("#prev-invoice").disabled = activeIndex <= 0;
    $("#next-invoice").disabled = activeIndex >= allInvoices.length - 1;
    pageIndex = Math.min(wantedPage, Math.max(0, pageCount - 1));
    renderCrops(options.focusCropId);
    renderInvoiceList();
    await loadPage(pageIndex);
    await refreshInvoices();
    await refreshReadiness();
    setSaveStatus("Đã lưu trên máy này");
  } catch (error) {
    setSaveStatus("Lỗi tải dữ liệu", true);
    toast(error.message, true);
  }
}

async function loadPage(nextPage) {
  if (!activeInvoice?.id) return;
  pageIndex = nextPage;
  try {
    const response = await api(`/api/page/${encodeURIComponent(activeInvoice.id)}/${pageIndex}`);
    pageCount = Number(response.headers.get("X-Page-Count") || 1);
    pageWidth = Number(response.headers.get("X-Page-Width") || 0);
    pageHeight = Number(response.headers.get("X-Page-Height") || 0);
    const blob = await response.blob();
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = URL.createObjectURL(blob);
    pageImageEl.onload = () => { applyZoom(); };
    pageImageEl.src = imageUrl;
    pageStageEl.hidden = false;
    $("#empty-pdf").hidden = true;
    $("#page-position").textContent = `${pageIndex + 1} / ${pageCount}`;
    $("#prev-page").disabled = pageIndex <= 0;
    $("#next-page").disabled = pageIndex >= pageCount - 1;
    clearSelection();
  } catch (error) {
    toast(error.message, true);
  }
}

function applyZoom() {
  if (!pageImageEl.naturalWidth) return;
  const width = Math.round(pageImageEl.naturalWidth * zoom);
  const height = Math.round(pageImageEl.naturalHeight * zoom);
  pageImageEl.style.width = `${width}px`;
  pageImageEl.style.height = `${height}px`;
  pageStageEl.style.width = `${width}px`;
  pageStageEl.style.height = `${height}px`;
  $("#zoom-value").textContent = `${Math.round(zoom * 100)}%`;
}

function clearSelection() {
  selection = null;
  selectionLayerEl.innerHTML = "";
  selectionPreviewEl.hidden = true;
  selectionPreviewEl.removeAttribute("src");
  $("#selection-status").textContent = "Chưa chọn vùng chữ";
  $("#save-crop").disabled = true;
}

function previewSelection() {
  const width = selection.x1 - selection.x0;
  const height = selection.y1 - selection.y0;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(pageImageEl, selection.x0, selection.y0, width, height, 0, 0, width, height);
  selectionPreviewEl.src = canvas.toDataURL("image/png");
  selectionPreviewEl.hidden = false;
}

function drawSelection(event) {
  const bounds = pageImageEl.getBoundingClientRect();
  const current = {
    x: Math.max(0, Math.min(pageImageEl.naturalWidth, (event.clientX - bounds.left) * pageImageEl.naturalWidth / bounds.width)),
    y: Math.max(0, Math.min(pageImageEl.naturalHeight, (event.clientY - bounds.top) * pageImageEl.naturalHeight / bounds.height)),
  };
  if (!selection) return;
  const x0 = Math.min(selection.startX, current.x);
  const y0 = Math.min(selection.startY, current.y);
  const x1 = Math.max(selection.startX, current.x);
  const y1 = Math.max(selection.startY, current.y);
  const cssScaleX = bounds.width / pageImageEl.naturalWidth;
  const cssScaleY = bounds.height / pageImageEl.naturalHeight;
  const box = selectionLayerEl.querySelector(".selection-box") || document.createElement("div");
  box.className = "selection-box";
  box.style.left = `${x0 * cssScaleX}px`;
  box.style.top = `${y0 * cssScaleY}px`;
  box.style.width = `${(x1 - x0) * cssScaleX}px`;
  box.style.height = `${(y1 - y0) * cssScaleY}px`;
  if (!box.isConnected) selectionLayerEl.append(box);
  selection.x0 = Math.round(x0);
  selection.y0 = Math.round(y0);
  selection.x1 = Math.round(x1);
  selection.y1 = Math.round(y1);
}

selectionLayerEl.addEventListener("pointerdown", (event) => {
  if (!activeInvoice || !pageImageEl.naturalWidth) return;
  event.preventDefault();
  selectionLayerEl.setPointerCapture(event.pointerId);
  const bounds = pageImageEl.getBoundingClientRect();
  selection = {
    startX: (event.clientX - bounds.left) * pageImageEl.naturalWidth / bounds.width,
    startY: (event.clientY - bounds.top) * pageImageEl.naturalHeight / bounds.height,
  };
  selectionLayerEl.innerHTML = "";
  drawSelection(event);
  drawing = true;
});
selectionLayerEl.addEventListener("pointermove", (event) => { if (drawing) drawSelection(event); });
selectionLayerEl.addEventListener("pointerup", (event) => {
  if (!drawing) return;
  drawSelection(event);
  drawing = false;
  const width = selection.x1 - selection.x0;
  const height = selection.y1 - selection.y0;
  const valid = width >= 12 && height >= 10;
  $("#selection-status").textContent = valid ? `Đã chọn ${width} × ${height} px · ${pageIndex + 1} DPI 300` : "Vùng chọn quá nhỏ; kéo lại khung chữ.";
  $("#save-crop").disabled = !valid;
  if (valid) previewSelection();
});

async function saveSelectedCrop() {
  if (!selection || !activeInvoice || $("#save-crop").disabled) return;
  const button = $("#save-crop");
  button.disabled = true;
  button.textContent = "Đang lưu crop…";
  try {
    const response = await api("/api/crops", {
      method: "POST",
      body: JSON.stringify({ invoice_id: activeInvoice.id, page: pageIndex, x0: selection.x0, y0: selection.y0, x1: selection.x1, y1: selection.y1 }),
    });
    const data = await response.json();
    clearSelection();
    await loadActiveInvoice({ keepPage: true, focusCropId: data.crop_id });
    toast("Đã lưu ảnh crop cục bộ. Nhập nội dung đúng như trên PDF.");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  } finally {
    button.textContent = "Lưu crop và nhập nhãn";
  }
}

function renderCrops(focusCropId = null) {
  const crops = activeInvoice?.crops || [];
  const confirmed = crops.filter((crop) => crop.confirmed && crop.label.trim()).length;
  $("#crop-count").textContent = `${crops.length} crop · ${confirmed} xác nhận`;
  if (!crops.length) {
    cropListEl.innerHTML = `<div class="empty-state compact"><h3>Chưa có crop</h3><p>Kéo chọn một dòng trên PDF rồi bấm “Lưu crop”.</p></div>`;
    return;
  }
  cropListEl.innerHTML = crops.map((crop, index) => `
    <article class="crop-card" data-crop-card="${escapeHtml(crop.id)}">
      <div class="crop-card-head"><strong>Crop ${index + 1} · Trang ${crop.page + 1}</strong><button class="text-button" data-delete-crop="${escapeHtml(crop.id)}" type="button">Xóa</button></div>
      <div class="crop-card-body">
        <img class="crop-preview" src="/api/crop-image/${encodeURIComponent(crop.id)}" alt="Crop ${index + 1} từ ${escapeHtml(activeInvoice.pdf)}">
        <div class="crop-fields">
          <label for="type-${escapeHtml(crop.id)}">Loại nội dung</label>
          <select id="type-${escapeHtml(crop.id)}" data-crop-type="${escapeHtml(crop.id)}">${Object.entries(fieldTypes).map(([key, title]) => `<option value="${escapeHtml(key)}" ${crop.field_type === key ? "selected" : ""}>${escapeHtml(title)}</option>`).join("")}</select>
          <label for="label-${escapeHtml(crop.id)}">Nội dung nhìn thấy chính xác</label>
          <button class="button button-soft extract-pdf-text" data-extract-text="${escapeHtml(crop.id)}" type="button">Lấy chữ từ vùng PDF</button>
          <div class="pdf-text-result" data-pdf-text-result="${escapeHtml(crop.id)}" hidden>
            <p data-pdf-text-value></p>
            <button class="text-button apply-pdf-text" data-apply-pdf-text="${escapeHtml(crop.id)}" type="button">Chép vào ô nhãn</button>
          </div>
          <textarea id="label-${escapeHtml(crop.id)}" data-crop-label="${escapeHtml(crop.id)}" maxlength="90" placeholder="Chép nguyên văn đúng như crop/PDF · tối đa 90 ký tự">${escapeHtml(crop.label)}</textarea>
          <label class="crop-confirm"><input type="checkbox" data-crop-confirm="${escapeHtml(crop.id)}" ${crop.confirmed ? "checked" : ""}><span>Đã đối chiếu crop với PDF và xác nhận bản chép</span></label>
          <div class="crop-save-state" data-save-state="${escapeHtml(crop.id)}">${crop.confirmed ? "Đã lưu và xác nhận" : "Lưu tự động · chưa xác nhận"}</div>
        </div>
      </div>
    </article>`).join("");
  cropListEl.querySelectorAll("[data-crop-label]").forEach((textarea) => {
    textarea.addEventListener("input", () => updateLocalCrop(textarea.dataset.cropLabel, { label: textarea.value, confirmed: false }));
    textarea.addEventListener("keydown", (event) => {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        const checkbox = cropListEl.querySelector(`[data-crop-confirm="${CSS.escape(textarea.dataset.cropLabel)}"]`);
        if (checkbox) { checkbox.checked = true; checkbox.dispatchEvent(new Event("change")); }
      }
    });
  });
  cropListEl.querySelectorAll("[data-extract-text]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Đang lấy chữ…";
      try {
        const response = await api(`/api/crop-text/${encodeURIComponent(button.dataset.extractText)}`);
        const data = await response.json();
        const result = cropListEl.querySelector(`[data-pdf-text-result="${CSS.escape(button.dataset.extractText)}"]`);
        const textValue = result?.querySelector("[data-pdf-text-value]");
        const applyButton = result?.querySelector("[data-apply-pdf-text]");
        if (!result || !textValue || !applyButton) throw new Error("Không tìm thấy vùng xem trước chữ PDF.");
        textValue.textContent = data.text;
        applyButton.dataset.extractedText = data.text;
        applyButton.disabled = data.text.length > 90;
        applyButton.textContent = data.text.length > 90 ? "Dài hơn 90 ký tự · hãy crop ngắn hơn" : "Chép vào ô nhãn";
        result.hidden = false;
        if (data.text.length > 90) toast("Vùng chữ dài hơn 90 ký tự; hãy crop sát hơn hoặc chia theo dòng.", true);
        else toast("Đã lấy chữ từ lớp PDF. Đối chiếu với crop rồi bấm “Chép vào ô nhãn”.");
      } catch (error) {
        toast(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = "Lấy chữ từ vùng PDF";
      }
    });
  });
  cropListEl.querySelectorAll("[data-apply-pdf-text]").forEach((button) => {
    button.addEventListener("click", () => {
      const textarea = cropListEl.querySelector(`[data-crop-label="${CSS.escape(button.dataset.applyPdfText)}"]`);
      const text = button.dataset.extractedText;
      if (!textarea || text === undefined || text.length > 90) return;
      textarea.value = text;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
      toast("Đã chép vào ô nhãn và lưu nháp. Hãy đối chiếu crop rồi tự xác nhận.");
    });
  });
  cropListEl.querySelectorAll("[data-crop-type]").forEach((select) => {
    select.addEventListener("change", () => updateLocalCrop(select.dataset.cropType, { field_type: select.value }));
  });
  cropListEl.querySelectorAll("[data-crop-confirm]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => updateLocalCrop(checkbox.dataset.cropConfirm, { confirmed: checkbox.checked }));
  });
  cropListEl.querySelectorAll("[data-delete-crop]").forEach((button) => {
    button.addEventListener("click", () => {
      pendingDeleteId = button.dataset.deleteCrop;
      $("#confirm-dialog").showModal();
    });
  });
  if (focusCropId) {
    const textarea = cropListEl.querySelector(`[data-crop-label="${CSS.escape(focusCropId)}"]`);
    textarea?.focus();
  }
}

function updateLocalCrop(cropId, changes) {
  const crop = activeInvoice?.crops.find((item) => item.id === cropId);
  if (!crop) return;
  Object.assign(crop, changes);
  if (Object.prototype.hasOwnProperty.call(changes, "label")) {
    const checkbox = cropListEl.querySelector(`[data-crop-confirm="${CSS.escape(cropId)}"]`);
    if (checkbox) checkbox.checked = false;
  }
  crop.dirty = true;
  const status = cropListEl.querySelector(`[data-save-state="${CSS.escape(cropId)}"]`);
  if (status) { status.textContent = "Lưu thay đổi…"; status.classList.remove("error"); }
  if (changes.confirmed && !String(crop.label || "").trim()) {
    crop.confirmed = 0;
    const checkbox = cropListEl.querySelector(`[data-crop-confirm="${CSS.escape(cropId)}"]`);
    if (checkbox) checkbox.checked = false;
    toast("Nhập nội dung trước khi xác nhận nhãn.", true);
  }
  clearTimeout(saveTimers.get(cropId));
  saveTimers.set(cropId, setTimeout(() => saveCropFields(cropId).catch(() => {}), 500));
  $("#crop-count").textContent = `${activeInvoice.crops.length} crop · ${activeInvoice.crops.filter((item) => item.confirmed && item.label.trim()).length} xác nhận`;
  refreshReadiness().catch(() => {});
}

async function saveCropFields(cropId) {
  const existing = savePromises.get(cropId);
  if (existing) return existing;
  clearTimeout(saveTimers.get(cropId));
  const promise = (async () => {
    const crop = activeInvoice?.crops.find((item) => item.id === cropId);
    while (crop?.dirty) {
      crop.dirty = false;
      const confirmed = Boolean(crop.confirmed && String(crop.label || "").trim());
      const status = cropListEl.querySelector(`[data-save-state="${CSS.escape(cropId)}"]`);
      try {
        await api(`/api/crops/${encodeURIComponent(cropId)}`, {
          method: "POST", body: JSON.stringify({ label: crop.label, field_type: crop.field_type, confirmed }),
        });
        crop.confirmed = confirmed ? 1 : 0;
        if (status) { status.textContent = confirmed ? "Đã lưu và xác nhận" : "Đã lưu · chưa xác nhận"; status.classList.remove("error"); }
        setSaveStatus("Đã lưu trên máy này");
      } catch (error) {
        crop.dirty = true;
        if (status) { status.textContent = `Lỗi lưu: ${error.message}`; status.classList.add("error"); }
        setSaveStatus("Lỗi lưu nhãn", true);
        throw error;
      }
    }
    await refreshInvoices();
    await refreshReadiness();
  })().finally(() => savePromises.delete(cropId));
  savePromises.set(cropId, promise);
  return promise;
}

async function flushSaves() {
  for (const timer of saveTimers.values()) clearTimeout(timer);
  const ids = [...saveTimers.keys()];
  saveTimers.clear();
  await Promise.all(ids.map((id) => saveCropFields(id)));
  await Promise.all([...savePromises.values()]);
}

async function deleteCrop(cropId) {
  try {
    await api(`/api/crops/${encodeURIComponent(cropId)}/delete`, { method: "POST", body: JSON.stringify({ confirm: true }) });
    await loadActiveInvoice({ keepPage: true });
    toast("Đã xóa crop và nhãn khỏi máy này.");
  } catch (error) { toast(error.message, true); }
}

async function updateSplit() {
  if (!activeInvoice) return;
  const select = $("#invoice-split");
  const previous = activeInvoice.split;
  try {
    const response = await api(`/api/invoice/${encodeURIComponent(activeInvoice.id)}/split`, {
      method: "POST", body: JSON.stringify({ split: select.value }),
    });
    const result = await response.json();
    activeInvoice.split = result.split;
    $("#split-warning").textContent = result.warnings.join(" ");
    if (result.warnings.length) toast(result.warnings.join(" "), true);
    await refreshInvoices();
    await refreshReadiness();
  } catch (error) {
    select.value = previous;
    toast(error.message, true);
  }
}

async function navigateInvoice(step) {
  await flushSaves();
  const target = Math.max(0, Math.min(allInvoices.length - 1, activeIndex + step));
  if (target !== activeIndex && allInvoices[target]) await selectInvoice(allInvoices[target].id);
}

async function exportDataset() {
  await flushSaves();
  const button = $("#export-button");
  button.disabled = true;
  button.textContent = "Đang kiểm tra…";
  try {
    const response = await api("/api/export", { method: "POST", body: JSON.stringify({}) });
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "paddleocr_recognition.zip";
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
    toast(`Đã tải ${filename}; bản ZIP cũng được lưu trong thư mục exports.`);
  } catch (error) {
    toast(error.message, true);
    await refreshReadiness();
  } finally {
    button.textContent = "Xuất ZIP recognition";
  }
}

async function exportHandoff() {
  await flushSaves();
  const button = $("#handoff-export-button");
  button.disabled = true;
  button.textContent = "Đang đóng gói…";
  try {
    const response = await api("/api/handoff/export");
    const blob = await response.blob();
    const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "ocr_labels_handoff.zip";
    const unconfirmed = Number(response.headers.get("X-Unconfirmed-Crops") || 0);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
    toast(unconfirmed ? `Đã tải ${filename}; bỏ qua ${unconfirmed} crop chưa xác nhận.` : `Đã tải ${filename}.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Xuất gói nhãn dở dang";
  }
}

async function importHandoff(file) {
  if (!file) return;
  if (file.size > 512 * 1024 * 1024) {
    toast("Gói nhãn vượt giới hạn 512 MB.", true);
    return;
  }
  if (!window.confirm(`Nhập gói “${file.name}”? Nhãn trùng nội dung sẽ được bỏ qua; xung đột sẽ báo lỗi và không ghi đè.`)) return;
  const input = $("#handoff-import-file");
  input.disabled = true;
  setSaveStatus("Đang nhập gói nhãn");
  try {
    await flushSaves();
    const response = await api("/api/handoff/import", {
      method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
    });
    const result = await response.json();
    await refreshInvoices();
    if (activeInvoice) await loadActiveInvoice({ keepPage: true });
    else await refreshReadiness();
    toast(`Đã nhập ${result.imported} crop; bỏ qua ${result.skipped_duplicates} crop trùng khớp.`);
  } catch (error) {
    toast(error.message, true);
    setSaveStatus("Nhập gói bị lỗi", true);
  } finally {
    input.value = "";
    input.disabled = false;
  }
}

async function boot() {
  try {
    const metaResponse = await api("/api/meta");
    const meta = await metaResponse.json();
    fieldTypes = meta.field_types;
    await refreshInvoices();
    await refreshReadiness();
    setSaveStatus("Đã lưu trên máy này");
    if (allInvoices.length) await selectInvoice(allInvoices[0].id);
  } catch (error) {
    setSaveStatus("Không tải được dữ liệu", true);
    toast(error.message, true);
  }
}

$("#search").addEventListener("input", renderInvoiceList);
$("#split-filter").addEventListener("change", renderInvoiceList);
$("#status-filter").addEventListener("change", renderInvoiceList);
$("#save-crop").addEventListener("click", saveSelectedCrop);
$("#handoff-export-button").addEventListener("click", exportHandoff);
$("#handoff-import-file").addEventListener("change", (event) => importHandoff(event.target.files[0]));
$("#prev-invoice").addEventListener("click", () => navigateInvoice(-1));
$("#next-invoice").addEventListener("click", () => navigateInvoice(1));
$("#prev-page").addEventListener("click", () => { if (pageIndex > 0) loadPage(pageIndex - 1); });
$("#next-page").addEventListener("click", () => { if (pageIndex + 1 < pageCount) loadPage(pageIndex + 1); });
$("#zoom-in").addEventListener("click", () => { zoom = Math.min(2, zoom + 0.1); applyZoom(); });
$("#zoom-out").addEventListener("click", () => { zoom = Math.max(0.2, zoom - 0.1); applyZoom(); });
$("#fit-width").addEventListener("click", () => {
  if (!pageImageEl.naturalWidth) return;
  zoom = Math.max(0.2, Math.min(1.4, ( $("#pdf-viewport").clientWidth - 48) / pageImageEl.naturalWidth));
  applyZoom();
});
$("#invoice-split").addEventListener("change", updateSplit);
$("#export-button").addEventListener("click", exportDataset);
$("#confirm-dialog").addEventListener("close", () => {
  if ($( "#confirm-dialog").returnValue === "delete" && pendingDeleteId) deleteCrop(pendingDeleteId);
  pendingDeleteId = null;
});
document.addEventListener("keydown", (event) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  if (event.altKey && event.key === "ArrowLeft") { event.preventDefault(); navigateInvoice(-1); }
  else if (event.altKey && event.key === "ArrowRight") { event.preventDefault(); navigateInvoice(1); }
  else if (!typing && event.key.toLowerCase() === "s" && selection && !$("#save-crop").disabled) { event.preventDefault(); saveSelectedCrop(); }
});

boot();
