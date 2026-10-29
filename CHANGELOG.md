# Changelog

## 2026-10-29-v4.4
Triển khai và dọn dẹp mã. Không đổi hành vi khi chạy local trên Windows (mọi giá trị mặc định giữ nguyên).

**Triển khai (mới)**
- `DEPLOY.md`: hướng dẫn Windows, Docker Compose (GPU, Phoenix, HTTPS), Linux systemd, sao lưu/nâng cấp, checklist bảo mật, xử lý sự cố.
- `Dockerfile`, `docker-compose.yml` (+ `.gpu.yml`, `.proxy.yml`), `deploy/` (cấu hình LiteLLM cho Docker, Caddyfile, nginx, systemd unit), `.dockerignore`.
- `scripts/init_env.py` tạo `.env` và sinh `LITELLM_MASTER_KEY`; `start.sh`, `Makefile`; `start.bat` dùng `.venv` nếu có.
- `GET /healthz` không cần đăng nhập (dùng cho Docker HEALTHCHECK, load balancer).
- CI thêm job build image Docker, kiểm tra `/healthz` và cú pháp compose.

**Cấu hình qua biến môi trường** (xem `.env.example`)
- `OLLAMA_URL`, `LITELLM_URL`, `PHOENIX_URL`: dashboard không còn hardcode `localhost`, gọi được dịch vụ ở container/máy khác (kể cả `bench.py`, `main.py`, `trace_phoenix.py`).
- `DATA_DIR`: tách dữ liệu chạy (users, chats, logs, docs, models, limits) khỏi mã nguồn; tự lấy `models.json`/`limits.json` mẫu trong repo làm giá trị khởi tạo.
- `DASHBOARD_HOST`, `ALLOWED_HOSTS` (header `Host` được phép), `COOKIE_SECURE`, `TRUST_PROXY` (IP thật từ `X-Forwarded-For`), `MANAGE_SERVICES`, `NO_BROWSER`.
- Dashboard thoát êm khi nhận SIGTERM (`docker stop`, `systemctl stop`) và tắt các dịch vụ con do nó bật; cảnh báo khi bind khác loopback.

**Dọn dẹp**
- Tách HTML/JS/từ điển dịch khỏi `dashboard.py` sang `web/` (`page.html`, `login.html`, `i18n_runtime.js`, `i18n_en.json`); `dashboard.py` từ 1705 xuống khoảng 1140 dòng. Nội dung trang sinh ra giống hệt bản trước (đã đối chiếu từng byte). Chú thích nhóm trong từ điển dịch không còn vì JSON không hỗ trợ.
- Xóa `ui_i18n.js`: bản dịch trùng lặp không được mã nào tham chiếu (từ điển đang dùng là `web/i18n_en.json`). Nếu bạn còn dùng ở nơi khác, khôi phục bằng `git checkout -- ui_i18n.js`.
- Bỏ tham số thừa của `load_knowledge`, bỏ mã đọc/ghi cứng đường dẫn.
- `.gitattributes` chuẩn hóa xuống dòng (LF, `.bat` giữ CRLF), hết cảnh báo thay đổi giả ở `limits.json`, `ui_i18n.js`.
- `.gitignore` thêm `.env.*`, `*.zip` (tránh vô tình đóng gói `.env`/`users.json`); `requirements.txt` ghi chú rõ phần tùy chọn, thêm `requirements-docker.txt`.
- Thêm 17 test (tổng 32): host/URL, biến triển khai, `DATA_DIR`, HTTP, file Docker/compose, định dạng `.env.example`. Test không còn ghi dữ liệu vào repo.

## 2026-10-28-v4.3
- Bỏ khóa ghi cứng: `LITELLM_MASTER_KEY` và khóa cloud đọc từ `.env` / biến môi trường (`settings.py`); lần chạy đầu tự sinh khóa gateway.
- `litellm_config.yaml`: `master_key` và `api_key` dùng `os.environ/...`; bỏ block `customlmm` gây lỗi `LLM Provider NOT provided` (để lại mẫu đã comment); xóa mục tương ứng trong `models.json`.
- LiteLLM bind `127.0.0.1` thay vì `0.0.0.0`.
- Cổng Ollama/LiteLLM/Phoenix/dashboard cấu hình qua env, không còn rải trong mã.
- Thêm `.gitignore`, `.env.example`, `pyproject.toml`, `requirements-dev.txt`.
- Thêm `tests/` (15 test) và CI GitHub Actions (ruff, pytest, gitleaks).
- README cập nhật khớp code (đã có đăng nhập/RBAC) và ghi rõ giới hạn còn lại.
