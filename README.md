# LLMOps Workbench

Ứng dụng LLMOps chạy local-first trên Windows: chat với model local hoặc qua LiteLLM gateway, retrieval-augmented generation (RAG) trên tài liệu do người dùng nạp, log request/feedback, usage & cost tracking, Phoenix tracing, GPU/VRAM monitoring và hoạt động trực tiếp. Giao diện web được tích hợp, mở tại `http://localhost:8765`.

## Kiến trúc

`Browser → Dashboard/API → Guardrails → Document chunking + lexical retrieval → Ollama hoặc LiteLLM → Answer + sources → JSONL logs / Phoenix traces`

- **Serving:** Ollama cung cấp model local (mặc định cấu hình `qwen2.5:3b` và `qwen2.5:7b`).
- **Gateway:** LiteLLM thống nhất endpoint, key, retry/fallback và giới hạn theo cấu hình.
- **RAG:** file `.txt`/`.md` trong `docs/` được chia chunk, xếp hạng theo độ trùng khớp thuật ngữ với câu hỏi; chỉ các đoạn liên quan được đưa vào prompt. Có thể nạp tài liệu trực tiếp từ web UI. Không còn tự động nạp `context.txt` mẫu.
- **Observability:** JSONL request/activity logs; Phoenix nếu bật.
- **GPU/VRAM monitoring:** dashboard đọc VRAM đã dùng/tổng, GPU utilization và nhiệt độ qua `nvidia-smi`, tự cập nhật mỗi 2 giây.
- **Governance:** tài khoản và vai trò, guardrail đầu vào, token/ngân sách theo cấu hình, ước tính chi phí từ `models.json`.

> Retrieval hiện tại là lexical retrieval không cần tải embedding model/vector database. Đây là baseline vận hành gọn nhẹ; nếu cần semantic search quy mô lớn, có thể nâng cấp sang embedding + vector store. Ứng dụng có đăng nhập (mật khẩu băm PBKDF2, phiên bằng cookie HttpOnly/SameSite, CSRF token, khóa tạm khi sai mật khẩu nhiều lần) và hai vai trò `admin`/`user`. Ứng dụng không tự nhận là production multi-tenant: chưa có phân quyền theo từng tài liệu, tenant isolation hay TLS. Dashboard mặc định bind `127.0.0.1`, LiteLLM cũng bind `127.0.0.1`; không expose trực tiếp ra Internet.

## 1. Yêu cầu

- Windows 10/11, Python 3.10+.
- [Ollama](https://ollama.com/) và tối thiểu một model tương thích.
- Tùy chọn: LiteLLM proxy, Phoenix, Node.js/promptfoo.

Mở PowerShell trong thư mục project:

```powershell
ollama pull qwen2.5:3b
# Tùy VRAM, có thể tải thêm:
ollama pull qwen2.5:7b
python -m pip install -r requirements.txt
copy .env.example .env
```

File `.env` chứa khóa và cổng (không commit). `LITELLM_MASTER_KEY` để trống thì lần chạy đầu tự sinh khóa ngẫu nhiên và ghi vào `.env`. Muốn dùng model cloud, điền khóa tương ứng (ví dụ `GEMINI_API_KEY`) vào `.env`; **không ghi khóa trực tiếp vào `litellm_config.yaml`**, file đó chỉ tham chiếu `os.environ/TÊN_BIẾN`.

Nếu chưa có `requirements.txt` hoặc chỉ muốn dùng model local với dashboard, các tính năng lõi dùng thư viện chuẩn Python. Các package tích hợp tùy chọn:

```powershell
python -m pip install "litellm[proxy]" arize-phoenix openinference-instrumentation-openai openai
```

## 2. Khởi động

1. Mở Ollama (ứng dụng thường chạy nền trên Windows).
2. Chạy `start.bat` hoặc:

```powershell
python dashboard.py
```

3. Truy cập `http://localhost:8765`.
4. Lần đầu mở trang, tạo tài khoản quản trị. Quên mật khẩu: `python dashboard.py --set-password TÊN`.
5. Chọn model và hỏi thử.

## 3. Nạp tài liệu và sử dụng RAG

- Trong phần **Knowledge & RAG Chat**, chọn file `.txt` hoặc `.md`, bấm **Thêm tài liệu vào Knowledge Base**.
- Hoặc đặt file vào thư mục `docs/` rồi bấm **Làm mới kho tài liệu**.
- Hỏi câu cụ thể về tài liệu. Kết quả hiển thị tên nguồn được truy xuất; lịch sử lưu trong `logs/requests.jsonl`.
- Nên chia tài liệu lớn thành file theo chủ đề. Giới hạn upload qua giao diện là 2 MB/file.
- Nếu kho rỗng hoặc không tìm thấy chunk liên quan, hệ thống hướng dẫn thêm tài liệu thay vì ngầm dùng nội dung mẫu.

## 4. Dịch vụ LLMOps tùy chọn

- **LiteLLM:** bật từ bảng điều khiển hoặc chạy `litellm --config litellm_config.yaml --host 127.0.0.1 --port 4000` (biến `LITELLM_MASTER_KEY` phải có trong môi trường, bảng điều khiển tự nạp từ `.env`). Cấu hình alias ở `litellm_config.yaml`; cập nhật alias tương ứng trong `models.json`. Khi thêm model cloud, `model:` phải có tiền tố nhà cung cấp hợp lệ (ví dụ `gemini/...`), nếu không LiteLLM báo `LLM Provider NOT provided`.
- **Phoenix:** bật từ dashboard, giao diện trace mặc định `http://localhost:6006`.

## 5. Cấu hình và dữ liệu

- `.env`: khóa và cổng (xem `.env.example`). `settings.py` nạp file này.
- `models.json`: model, alias gateway, cờ cloud, giá token USD/1 triệu token. Giá cloud đang để 0: hãy điền giá thật thì hạn mức `daily_usd_total` mới có tác dụng.
- `users.json`, `chats/`: tài khoản và lịch sử chat riêng từng người (đã nằm trong `.gitignore`).
- `limits.json`: hạn mức token/ngân sách.
- `prompt.txt`: system prompt dùng cho RAG.
- `docs/`: knowledge base do người vận hành quản lý.
- `logs/requests.jsonl`: request, token usage, độ trễ, nguồn và feedback.
- `logs/activity.jsonl`: nhật ký hoạt động.

`.gitignore` đã loại `.env`, `users.json`, `chats/`, `logs/` và nội dung `docs/`. Vẫn không commit API key, tài liệu nội bộ hoặc log nhạy cảm lên Git. Với model cloud, prompt và các đoạn tài liệu truy xuất có thể được gửi tới nhà cung cấp cloud.

## 6. Giới hạn vận hành

- Hạn mức token phụ thuộc usage do provider trả về; model local có thể báo usage bằng 0 tùy runtime.
- Chi phí là ước tính dựa trên giá nhập trong `models.json`, không thay thế hóa đơn nhà cung cấp.
- Guardrail hiện là rule-based và không thay thế hệ thống bảo mật chuyên dụng.
- Chưa có tenant isolation, TLS, durable database (state đang là JSON/JSONL, chưa xoay vòng log), semantic vector index, `/healthz`/`/metrics` chuẩn hay kiểm thử tải. Giữ dashboard trên localhost cho đến khi bổ sung các thành phần này.
- `promptfooconfig.yaml`, `bench.py`, `trace_phoenix.py` đo bằng `context.txt` ghép thẳng vào prompt, **không đi qua pipeline RAG của dashboard**, nên kết quả chưa phản ánh chất lượng RAG thật.


### GPU/VRAM
Dashboard cần NVIDIA GPU driver và `nvidia-smi` có trong PATH để đọc thông tin GPU. Nếu chạy CPU-only hoặc không có NVIDIA driver, mục GPU sẽ báo không khả dụng; chat vẫn có thể hoạt động bình thường. VRAM hiển thị là mức sử dụng của toàn GPU, không riêng tiến trình Ollama.

## 7. Kiểm thử và CI

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q      # hoặc: python -m unittest discover -s tests
ruff check .
```

`tests/test_core.py` kiểm tra guardrail, băm mật khẩu, parser netstat, cấu hình `.env` và quét khóa ghi cứng trong repo. Workflow `.github/workflows/ci.yml` chạy lint, test và `gitleaks` trên mỗi push/PR.
