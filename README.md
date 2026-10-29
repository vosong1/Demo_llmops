# LLMOps Workbench

Ứng dụng LLMOps chạy local-first: chat với model local hoặc qua LiteLLM gateway, retrieval-augmented generation (RAG) trên tài liệu do người dùng nạp, log request/feedback, usage & cost tracking, Phoenix tracing, GPU/VRAM monitoring và nhật ký hoạt động trực tiếp. Giao diện web tích hợp, mở tại `http://localhost:8765`.

Chạy được trên Windows (cách gốc), Linux, hoặc Docker Compose. **Hướng dẫn triển khai đầy đủ: [DEPLOY.md](DEPLOY.md).**

## Kiến trúc

`Browser → Dashboard/API → Guardrails → Document chunking + lexical retrieval → Ollama hoặc LiteLLM → Answer + sources → JSONL logs / Phoenix traces`

- **Serving:** Ollama cung cấp model local (mặc định cấu hình `qwen2.5:3b` và `qwen2.5:7b`).
- **Gateway:** LiteLLM thống nhất endpoint, key, retry/fallback và giới hạn theo cấu hình.
- **RAG:** file `.txt`/`.md` trong `docs/` được chia chunk, xếp hạng theo độ trùng khớp thuật ngữ với câu hỏi; chỉ các đoạn liên quan được đưa vào prompt. Có thể nạp tài liệu trực tiếp từ web UI. Không còn tự động nạp `context.txt` mẫu.
- **Observability:** JSONL request/activity logs; Phoenix nếu bật.
- **GPU/VRAM monitoring:** đọc VRAM, GPU utilization và nhiệt độ qua `nvidia-smi`, tự cập nhật mỗi 2 giây.
- **Governance:** tài khoản và vai trò `admin`/`user` (mật khẩu băm PBKDF2, cookie HttpOnly/SameSite, CSRF token, khóa tạm khi sai mật khẩu nhiều lần), guardrail đầu vào, hạn mức token/ngân sách, ước tính chi phí từ `models.json`.

> Retrieval là lexical retrieval, không cần embedding model hay vector database: đây là baseline gọn nhẹ, có thể nâng cấp lên embedding + vector store khi cần semantic search quy mô lớn.

## Cấu trúc thư mục

```
dashboard.py            Máy chủ web + API + RAG + quản lý dịch vụ
settings.py             Đọc cấu hình từ biến môi trường / .env
web/                    Giao diện: page.html, login.html, i18n_runtime.js, i18n_en.json (Việt/Anh)
prompt.txt              System prompt dùng cho RAG
models.json, limits.json  Model, alias gateway, giá token, hạn mức (mẫu; bản chạy nằm ở DATA_DIR)
litellm_config.yaml     Cấu hình LiteLLM khi chạy trên máy (Docker dùng deploy/litellm_config.docker.yaml)
main.py, bench.py, trace_phoenix.py, promptfooconfig.yaml, questions.json, context.txt
                        Bộ đo serving / trace / eval (xem "Giới hạn")
scripts/init_env.py     Tạo .env và sinh khóa LiteLLM
Dockerfile, docker-compose*.yml, deploy/   Triển khai Docker, HTTPS (Caddy/nginx), systemd
tests/                  Kiểm thử đơn vị và kiểm thử HTTP
docs/                   Kho tài liệu RAG do người vận hành quản lý (nội dung được .gitignore)
```

`docs/` là **kho tri thức của RAG**: mọi file `.txt`/`.md` đặt ở đó sẽ được đưa vào truy vấn, vì vậy tài liệu hướng dẫn của project nằm ở thư mục gốc (README, DEPLOY) chứ không đặt trong `docs/`.

## Chạy nhanh

**Windows**

```powershell
ollama pull qwen2.5:3b
python -m pip install -r requirements.txt   # tùy chọn
python scripts\init_env.py
start.bat                                   # hoặc: python dashboard.py
```

**Linux/macOS**

```bash
ollama pull qwen2.5:3b
python3 scripts/init_env.py
./start.sh
```

**Docker Compose**

```bash
python scripts/init_env.py
docker compose up -d --build
docker compose run --rm ollama-pull
```

Rồi mở `http://localhost:8765`, lần đầu tạo tài khoản quản trị. Quên mật khẩu: `python dashboard.py --set-password TÊN`. Chi tiết GPU, HTTPS/tên miền, sao lưu, nâng cấp và xử lý sự cố nằm trong [DEPLOY.md](DEPLOY.md).

## Nạp tài liệu và dùng RAG

- Trong **Knowledge & RAG Chat**, chọn file `.txt` hoặc `.md`, bấm **Thêm tài liệu vào Knowledge Base**; hoặc đặt file vào `docs/` (Docker: `/data/docs`) rồi bấm **Làm mới kho tài liệu**.
- Hỏi câu cụ thể về tài liệu. Kết quả hiển thị tên nguồn được truy xuất; lịch sử lưu trong `logs/requests.jsonl`.
- Nên chia tài liệu lớn thành file theo chủ đề. Giới hạn upload qua giao diện là 2 MB/file.
- Nếu kho rỗng hoặc không tìm thấy chunk liên quan, hệ thống hướng dẫn thêm tài liệu thay vì ngầm dùng nội dung mẫu.

## Dịch vụ LLMOps tùy chọn

- **LiteLLM:** bật từ bảng điều khiển hoặc `litellm --config litellm_config.yaml --host 127.0.0.1 --port 4000` (`LITELLM_MASTER_KEY` phải có trong môi trường, bảng điều khiển tự nạp từ `.env`). Alias cấu hình ở `litellm_config.yaml` phải khớp `models.json`. Khi thêm model cloud, `model:` phải có tiền tố nhà cung cấp hợp lệ (ví dụ `gemini/...`), nếu không LiteLLM báo `LLM Provider NOT provided`.
- **Phoenix:** bật từ dashboard, giao diện trace mặc định `http://localhost:6006`.

## Cấu hình và dữ liệu

- `.env`: khóa và cổng (xem `.env.example`, danh sách biến đầy đủ ở [DEPLOY.md](DEPLOY.md#biến-môi-trường)). `settings.py` nạp file này. **Không ghi khóa trực tiếp vào `litellm_config.yaml`**, file đó chỉ tham chiếu `os.environ/TÊN_BIẾN`.
- `DATA_DIR` (mặc định là thư mục dự án): `users.json`, `chats/` (lịch sử chat riêng từng người), `logs/`, `docs/`, `models.json`, `limits.json`. Tất cả đã nằm trong `.gitignore`.
- `models.json`: model, alias gateway, cờ cloud, giá token USD/1 triệu token. Giá cloud đang để 0: hãy điền giá thật thì hạn mức `daily_usd_total` mới có tác dụng.
- `logs/requests.jsonl`: request, token usage, độ trễ, nguồn và feedback. `logs/activity.jsonl`: nhật ký hoạt động.

Không commit API key, tài liệu nội bộ hoặc log nhạy cảm lên Git, và đừng gửi zip của thư mục dự án khi nó còn `.env`/`users.json`. Với model cloud, prompt và các đoạn tài liệu truy xuất có thể được gửi tới nhà cung cấp cloud.

## Giới hạn vận hành

- Hạn mức token phụ thuộc usage do provider trả về; model local có thể báo usage bằng 0 tùy runtime.
- Chi phí là ước tính từ giá nhập trong `models.json`, không thay thế hóa đơn nhà cung cấp.
- Guardrail là rule-based, không thay thế hệ thống bảo mật chuyên dụng.
- Chưa có tenant isolation, phân quyền theo từng tài liệu, durable database (state là JSON/JSONL, chưa xoay vòng log), semantic vector index, `/metrics` hay kiểm thử tải. Dashboard không tự phục vụ HTTPS: khi mở ra ngoài localhost hãy đặt sau reverse proxy có TLS (DEPLOY.md, mục B4). Có `GET /healthz` (liveness, không cần đăng nhập).
- `promptfooconfig.yaml`, `bench.py`, `trace_phoenix.py` đo bằng `context.txt` ghép thẳng vào prompt, **không đi qua pipeline RAG của dashboard**, nên kết quả chưa phản ánh chất lượng RAG thật.
- GPU/VRAM cần NVIDIA driver và `nvidia-smi` trong PATH; không có thì mục GPU báo không khả dụng, chat vẫn chạy. VRAM là mức dùng của toàn GPU, không riêng Ollama.

## Kiểm thử và CI

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q      # hoặc: python -m unittest discover -s tests
ruff check .
```

`tests/test_core.py` kiểm tra guardrail, băm mật khẩu, parser netstat, cấu hình `.env` và biến triển khai, đọc/ghi `DATA_DIR`, các endpoint HTTP (`/healthz`, chặn Host lạ, yêu cầu đăng nhập), file Docker/compose và quét khóa ghi cứng trong repo. Workflow `.github/workflows/ci.yml` chạy lint, test, build image Docker và `gitleaks` trên mỗi push/PR.
