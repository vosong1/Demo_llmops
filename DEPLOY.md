# Hướng dẫn triển khai LLMOps Workbench

Tài liệu này giúp đưa ứng dụng từ máy cá nhân lên máy chủ. Chọn **một** cách phù hợp:

| Cách | Khi nào dùng | Mục |
|---|---|---|
| **A. Windows chạy trực tiếp** | Demo, dùng cá nhân trên máy Windows (cách gốc của project) | [A](#a-windows-chạy-trực-tiếp) |
| **B. Docker Compose** | Chạy trên máy chủ/VPS Linux hoặc Windows có Docker, dễ nâng cấp và sao lưu (**khuyến nghị**) | [B](#b-docker-compose) |
| **C. Linux không Docker (systemd)** | Máy chủ Linux muốn chạy thẳng bằng Python | [C](#c-linux-không-docker-systemd) |

Cả ba cách đều dùng chung cấu hình qua file `.env` (xem [biến môi trường](#biến-môi-trường)).

> **Trạng thái kiểm thử.** Logic ứng dụng (đăng nhập, `/healthz`, chặn Host lạ, cookie `Secure`, IP qua proxy, tách `DATA_DIR`, chế độ không tự bật/tắt dịch vụ) đã được kiểm thử tự động và chạy thử trên một server thật. Còn các file `Dockerfile`, `docker-compose*.yml`, `Caddyfile`, `nginx`, `systemd` mới được kiểm tra **cú pháp**, chưa được build/chạy thật vì môi trường tạo ra chúng không có Docker và mạng. Lần đầu triển khai hãy làm theo mục [Kiểm tra sau khi chạy](#kiểm-tra-sau-khi-chạy) và đọc [Xử lý sự cố](#xử-lý-sự-cố).

---

## Biến môi trường

Đặt trong `.env` (sao chép từ `.env.example` hoặc chạy `python scripts/init_env.py`). Biến môi trường thật của hệ thống luôn thắng giá trị trong `.env`. **Không viết chú thích cuối dòng** (`KEY=value # ghi chú`), phần ghi chú sẽ bị tính vào giá trị.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LITELLM_MASTER_KEY` | tự sinh khi chạy local | Khóa admin của LiteLLM. **Bắt buộc có giá trị khi dùng Docker.** |
| `GEMINI_API_KEY` | trống | Khóa model cloud (chỉ khi dùng `gemini-flash`) |
| `DASHBOARD_HOST` | `127.0.0.1` | Địa chỉ dashboard lắng nghe. Image Docker đặt `0.0.0.0` |
| `DASHBOARD_PORT` | `8765` | Cổng dashboard |
| `OLLAMA_URL` | `http://localhost:11434` | Nơi dashboard gọi Ollama (Docker: `http://ollama:11434`) |
| `LITELLM_URL` | `http://localhost:4000` | Nơi dashboard gọi LiteLLM |
| `PHOENIX_URL` | `http://localhost:6006` | Nơi dashboard gửi trace |
| `DATA_DIR` | thư mục dự án | Nơi lưu `users.json`, `chats/`, `logs/`, `docs/`, `models.json`, `limits.json`. Docker: `/data` |
| `ALLOWED_HOSTS` | chỉ localhost | Tên miền được phép trong header `Host`, cách nhau bằng dấu phẩy |
| `COOKIE_SECURE` | `0` | `1` = cookie phiên chỉ gửi qua HTTPS |
| `TRUST_PROXY` | `0` | `1` = lấy IP người dùng từ `X-Forwarded-For` (chỉ bật khi có reverse proxy đứng trước) |
| `MANAGE_SERVICES` | `1` | `0` = nút Bật/Tắt trên dashboard không điều khiển Ollama/LiteLLM/Phoenix. Docker đặt `0` |
| `NO_BROWSER` | `0` | `1` = không tự mở trình duyệt |
| `PULL_MODELS` | `qwen2.5:3b` | (Docker) model tải bởi `ollama-pull`, cách nhau bằng dấu cách |

---

## A. Windows chạy trực tiếp

Yêu cầu: Windows 10/11, Python 3.10+, [Ollama](https://ollama.com/).

```powershell
cd Demo_llmops
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt      # tùy chọn; lõi chỉ cần thư viện chuẩn
python scripts\init_env.py                      # tạo .env + khóa LiteLLM
ollama pull qwen2.5:3b
start.bat                                       # hoặc: python dashboard.py
```

Mở `http://localhost:8765`, lần đầu tạo tài khoản quản trị. Nút **Bật/Tắt** trên dashboard khởi động Ollama, LiteLLM, Phoenix trực tiếp.

Nếu PowerShell chặn `Activate.ps1`: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Tự chạy khi mở máy (tùy chọn).** Cách đơn giản là Task Scheduler (trigger *At log on*, action chạy `start.bat`, đặt `NO_BROWSER=1` trong `.env`). Muốn chạy như dịch vụ Windows có thể dùng [NSSM](https://nssm.cc/): `nssm install LLMOpsWorkbench <đường dẫn python.exe> dashboard.py`, đặt *Startup directory* là thư mục dự án.

---

## B. Docker Compose

Yêu cầu: Docker Engine + Compose v2 (hoặc Docker Desktop), khoảng 10 GB trống cho image và model.

Kiến trúc:

```
Trình duyệt ──► (Caddy/nginx, HTTPS, tùy chọn)
                   │
                   ▼
              dashboard :8765 ──► litellm :4000 ──► ollama :11434
                   │                                   ▲
                   └──────────────────────────────────┘
                   └──► phoenix :6006 (tùy chọn)
```

Chỉ `dashboard` (và `phoenix`, `caddy` nếu bật) được publish ra máy chủ. Ollama (không có xác thực) và LiteLLM (chỉ có một khóa master dùng chung) không cần ai bên ngoài gọi tới, nên chỉ nằm trong mạng nội bộ của Compose.

### B1. Chạy lần đầu

```bash
git clone https://github.com/vosong1/Demo_llmops.git && cd Demo_llmops
python scripts/init_env.py            # tạo .env, sinh LITELLM_MASTER_KEY
docker compose up -d --build
docker compose run --rm ollama-pull   # tải model (mặc định qwen2.5:3b)
```

Muốn tải thêm model: đặt `PULL_MODELS=qwen2.5:3b qwen2.5:7b` trong `.env` rồi chạy lại lệnh `ollama-pull`. Tên model phải khớp `models.json` và alias phải có trong `deploy/litellm_config.docker.yaml`.

Mở `http://localhost:8765`, tạo tài khoản quản trị đầu tiên.

Trên Linux/macOS có thể dùng `make up`, `make pull-models`, `make logs`, `make down` (xem `make help`).

### B2. Thêm Phoenix (xem trace)

```bash
docker compose --profile observability up -d
```

Giao diện trace ở `http://localhost:6006`. Phoenix mặc định **không có đăng nhập**: compose chỉ publish nó ra `127.0.0.1`, đừng mở ra Internet. Truy cập từ xa bằng SSH tunnel: `ssh -L 6006:localhost:6006 user@server`.

### B3. Dùng GPU NVIDIA

1. Cài driver NVIDIA và [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) trên máy chủ.
2. Đăng ký runtime: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
3. Thử: `docker run --rm --gpus all ubuntu nvidia-smi`.
4. Chạy: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.

Windows + Docker Desktop: dùng backend WSL2 với driver NVIDIA mới trên Windows; thường không cần tự cài Container Toolkit, xem phần GPU trong tài liệu của Docker Desktop. Thử bằng lệnh ở bước 3 trước.

Bảng GPU/VRAM của dashboard đọc bằng `nvidia-smi` nên trong Docker mặc định hiện *không khả dụng* (chat vẫn bình thường). Xem GPU bằng `docker compose exec ollama nvidia-smi`. Muốn dashboard đọc được, bỏ comment đoạn `dashboard:` trong `docker-compose.gpu.yml`.

### B4. HTTPS với tên miền (truy cập từ xa)

Không mở cổng 8765 trực tiếp ra Internet: dashboard chỉ có HTTP thường. Hãy đặt reverse proxy có TLS phía trước.

**Cách 1 — Caddy (tự lấy và gia hạn chứng chỉ Let's Encrypt):**

1. Trỏ bản ghi DNS `A` của tên miền (ví dụ `llm.example.com`) về IP máy chủ, mở cổng 80 và 443 trên firewall.
2. Thêm vào `.env`:
   ```
   DOMAIN=llm.example.com
   ALLOWED_HOSTS=llm.example.com
   COOKIE_SECURE=1
   TRUST_PROXY=1
   ```
3. Chạy:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
   ```
4. Mở `https://llm.example.com`.

**Cách 2 — nginx sẵn có trên máy chủ:** dùng `deploy/nginx.conf.example` (thay tên miền, đường dẫn chứng chỉ, ví dụ lấy bằng `certbot`) và đặt cùng bốn biến `ALLOWED_HOSTS`, `COOKIE_SECURE`, `TRUST_PROXY` trong `.env`.

Ba biến này quan trọng:

- `ALLOWED_HOSTS`: thiếu thì mọi request qua tên miền bị trả `403 forbidden` (cơ chế chống DNS rebinding).
- `COOKIE_SECURE=1`: chỉ bật khi thật sự truy cập qua HTTPS, nếu bật mà vào bằng HTTP thì trình duyệt không lưu cookie nên không đăng nhập được.
- `TRUST_PROXY=1`: chỉ bật khi có proxy đứng trước. Nếu bật mà dashboard bị truy cập thẳng, người dùng có thể giả IP trong nhật ký hoạt động.

### B5. Dữ liệu, sao lưu, nâng cấp

Dữ liệu nằm trong các volume có tên cố định:

| Volume | Nội dung |
|---|---|
| `llmops-data` | tài khoản (`users.json`), lịch sử chat, log, kho tài liệu RAG, `models.json`, `limits.json` |
| `llmops-ollama-models` | model Ollama đã tải |
| `llmops-phoenix-data` | dữ liệu trace Phoenix |

**Sao lưu** `llmops-data` (quan trọng nhất; model có thể tải lại):

```bash
docker run --rm -v llmops-data:/data:ro -v "$PWD":/backup alpine \
  tar czf /backup/llmops-data-$(date +%Y%m%d).tgz -C /data .
```

**Khôi phục** vào volume mới/trống:

```bash
docker compose down
docker run --rm -v llmops-data:/data -v "$PWD":/backup alpine \
  sh -c "cd /data && tar xzf /backup/llmops-data-YYYYMMDD.tgz && chown -R 10001:10001 /data"
docker compose up -d
```

Bản sao lưu chứa hash mật khẩu và nội dung chat, hãy cất ở nơi an toàn.

**Nâng cấp:**

```bash
git pull
docker compose up -d --build        # dữ liệu trong volume được giữ nguyên
docker compose pull                 # (tùy chọn) cập nhật ollama/litellm/phoenix
docker compose up -d
```

Tốt hơn hết là ghim phiên bản image (ví dụ `ollama/ollama:0.x.y`, tag cụ thể của LiteLLM và Phoenix) trong `docker-compose.yml` thay vì `latest`/`main-stable`, để một lần `pull` không làm đổi hành vi bất ngờ.

**Nạp tài liệu vào kho RAG:** dùng giao diện web, hoặc copy file `.txt`/`.md` thẳng vào volume: `docker compose cp tailieu.md dashboard:/data/docs/`.

**Quên mật khẩu admin:** `docker compose exec dashboard python dashboard.py --set-password TÊN`.

**Xem log:** `docker compose logs -f dashboard litellm ollama`.

---

## C. Linux không Docker (systemd)

Ví dụ cho Ubuntu/Debian, cài vào `/opt/llmops`, dữ liệu ở `/var/lib/llmops`.

```bash
# 1. Ollama (service systemd do bộ cài của Ollama tạo sẵn)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b

# 2. Mã nguồn + môi trường Python (cần: sudo apt install -y git python3-venv)
sudo useradd --system --home /var/lib/llmops --create-home llmops
sudo git clone https://github.com/vosong1/Demo_llmops.git /opt/llmops
cd /opt/llmops
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt          # LiteLLM/Phoenix tùy chọn, có thể bỏ nếu chỉ dùng Ollama trực tiếp
sudo python3 scripts/init_env.py
sudo chown -R llmops:llmops /opt/llmops /var/lib/llmops
sudo chmod 600 /opt/llmops/.env

# 3. Dịch vụ dashboard
sudo cp deploy/llmops-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llmops-dashboard
sudo systemctl status llmops-dashboard
journalctl -u llmops-dashboard -f
```

Ở cách này dashboard (`MANAGE_SERVICES=1` mặc định) vẫn tự bật/tắt LiteLLM và Phoenix bằng nút trên giao diện, và dừng chúng khi service dừng. Nếu muốn systemd quản lý riêng từng dịch vụ, đặt `MANAGE_SERVICES=0` trong `.env`.

Muốn truy cập từ xa: đặt nginx/Caddy phía trước như mục [B4](#b4-https-với-tên-miền-truy-cập-từ-xa); dashboard vẫn bind `127.0.0.1`.

Nâng cấp: `cd /opt/llmops && sudo git pull && sudo systemctl restart llmops-dashboard`.

---

## Kiểm tra sau khi chạy

```bash
curl -fsS http://127.0.0.1:8765/healthz        # {"status": "ok", "build": "..."}
docker compose ps                               # (Docker) dashboard, ollama, litellm đều Up/healthy
docker compose exec ollama ollama list          # model đã tải
```

Rồi trong giao diện: tạo admin, vào **Tổng quan** thấy Ollama/LiteLLM "đang chạy", nạp một file `.md` ở **Hỏi đáp**, hỏi một câu và thấy nguồn trả về.

---

## Checklist bảo mật trước khi mở cho người khác dùng

- [ ] Truy cập qua HTTPS (mục B4); `ALLOWED_HOSTS`, `COOKIE_SECURE=1`, `TRUST_PROXY=1` đã đặt đúng.
- [ ] Firewall chỉ mở 80/443 (và SSH). Các cổng 8765, 11434, 4000, 6006 **không** mở ra Internet.
- [ ] `.env` quyền `600`, không commit, không gửi qua chat/zip (có thể chứa khóa API).
- [ ] Đã tạo tài khoản riêng cho từng người (mật khẩu tối thiểu 8 ký tự) và tắt quyền `allow_cloud` với người không cần model cloud.
- [ ] Đã đặt hạn mức trong **Cấu hình** (`limits.json`) và điền giá thật cho model cloud trong `models.json`, nếu không `daily_usd_total` không có tác dụng.
- [ ] Có lịch sao lưu `llmops-data` (hoặc `/var/lib/llmops`).
- [ ] Đã ghim phiên bản image/thư viện.

Giới hạn còn lại của ứng dụng (xem thêm README): chưa có tenant isolation hay phân quyền theo từng tài liệu, state là file JSON/JSONL (chưa xoay vòng log, `logs/requests.jsonl` sẽ lớn dần), guardrail dựa trên luật, chưa có `/metrics`. Đây là công cụ cho nhóm nhỏ tin cậy lẫn nhau, không phải hệ thống multi-tenant.

---

## Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp và cách xử lý |
|---|---|
| `docker compose` báo `Chạy python scripts/init_env.py hoặc đặt LITELLM_MASTER_KEY` | `.env` chưa có khóa. Chạy `python scripts/init_env.py`. |
| Mở bằng tên miền bị `{"error": "forbidden"}` | Thiếu `ALLOWED_HOSTS=<tên miền>` trong `.env`, hoặc proxy không giữ header `Host` gốc (nginx cần `proxy_set_header Host $host`). Sau khi sửa: `docker compose up -d`. |
| Đăng nhập xong vẫn bị đẩy ra trang đăng nhập | Đang để `COOKIE_SECURE=1` nhưng vào bằng HTTP. Dùng HTTPS hoặc đặt `COOKIE_SECURE=0`. |
| Dashboard báo "Ollama chưa chạy" trong Docker | `OLLAMA_URL` sai hoặc container `ollama` chưa healthy: `docker compose ps`, `docker compose logs ollama`. |
| Chọn model báo lỗi, `model not found` | Chưa tải model (`docker compose run --rm ollama-pull`), hoặc alias trong `models.json` không có trong `deploy/litellm_config.docker.yaml`. |
| Bấm Bật/Tắt dịch vụ chỉ hiện thông báo "do hạ tầng bên ngoài quản lý" | Đúng thiết kế khi `MANAGE_SERVICES=0` hoặc URL không phải localhost. Dùng `docker compose restart <dịch vụ>` / `systemctl`. |
| LiteLLM `LLM Provider NOT provided` | Dòng `model:` trong cấu hình thiếu tiền tố nhà cung cấp (ví dụ `gemini/...`, `ollama_chat/...`). |
| Model cloud (Gemini) lỗi | Chưa đặt `GEMINI_API_KEY`, hoặc tên model trong `litellm_config*.yaml` không tồn tại với tài khoản của bạn (hãy kiểm tra lại tên model hiện hành). |
| Phoenix: `Failed to bind to address [::]:4317` | Cổng gRPC 4317 đang bị chương trình khác chiếm (thường là một Phoenix cũ). Tắt chương trình đó hoặc đặt `PHOENIX_GRPC_PORT=4318`. |
| Bảng GPU báo "Không tìm thấy nvidia-smi" | Máy không có GPU NVIDIA, hoặc đang chạy trong Docker (xem B3). Không ảnh hưởng chat. |
| Cổng 8765 đã bị chiếm | Đổi `DASHBOARD_PORT` trong `.env`. Trên Windows bản mới tự thay bản cũ nếu cổng do dashboard cũ giữ. |
| Quá chậm / hết bộ nhớ | Model 7B cần nhiều VRAM/RAM hơn 3B. Dùng `qwen2.5:3b`, hoặc bật GPU (B3). |
