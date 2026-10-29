"""Cấu hình dùng chung (đọc từ biến môi trường / file .env). Chỉ dùng thư viện chuẩn.

Thứ tự ưu tiên: biến môi trường thật > file .env > giá trị mặc định.
Lần chạy đầu, nếu chưa có LITELLM_MASTER_KEY thì tự sinh khóa ngẫu nhiên và ghi vào .env
(không còn khóa cố định trong mã nguồn).

Mặc định mọi thứ chạy trên một máy (localhost). Khi triển khai (Docker, VPS, sau reverse proxy)
đổi qua biến môi trường, xem .env.example và DEPLOY.md.
"""
import os, secrets

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")


def load_env(path=ENV_FILE):
    """Nạp KEY=VALUE từ .env vào os.environ (không ghi đè biến đã có). Trả về dict các khóa đã đọc."""
    found = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("\"'")
                if k:
                    found[k] = v
                    os.environ.setdefault(k, v)
    except OSError:
        pass
    return found


def ensure_master_key(path=ENV_FILE):
    """Bảo đảm có LITELLM_MASTER_KEY; nếu thiếu thì sinh mới và lưu vào .env."""
    if os.environ.get("LITELLM_MASTER_KEY"):
        return os.environ["LITELLM_MASTER_KEY"]
    key = "sk-" + secrets.token_urlsafe(24)
    os.environ["LITELLM_MASTER_KEY"] = key
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n# Tự sinh lần chạy đầu. Đổi tùy ý, nhưng phải khớp với LiteLLM đang chạy.\nLITELLM_MASTER_KEY={key}\n")
    except OSError:
        pass   # không ghi được thì khóa chỉ sống trong tiến trình này
    return key


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name):
    return [x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()]


def _url(name, default):
    return (os.environ.get(name) or default).strip().rstrip("/")


load_env()
GW_KEY = ensure_master_key()

# ---- cổng (giữ nguyên tên cũ để mã cũ vẫn chạy) ----
PORT = _int("DASHBOARD_PORT", 8765)
OLLAMA_PORT = _int("OLLAMA_PORT", 11434)
GW_PORT = _int("LITELLM_PORT", 4000)
PHOENIX_PORT = _int("PHOENIX_PORT", 6006)
GW_HOST = os.environ.get("LITELLM_BIND_HOST", "127.0.0.1")   # địa chỉ LiteLLM bind khi do bảng điều khiển bật; không mở ra LAN theo mặc định

# ---- địa chỉ các dịch vụ mà dashboard gọi tới (Docker: http://ollama:11434, http://litellm:4000 ...) ----
OLLAMA_URL = _url("OLLAMA_URL", f"http://localhost:{OLLAMA_PORT}")
GW_URL = _url("LITELLM_URL", f"http://localhost:{GW_PORT}")
PHOENIX_URL = _url("PHOENIX_URL", f"http://localhost:{PHOENIX_PORT}")

# ---- dashboard ----
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")           # trong Docker đặt 0.0.0.0 (và chỉ publish cổng ra 127.0.0.1)
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR") or HERE)  # users.json, chats/, logs/, docs/, models.json, limits.json
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", *_list("ALLOWED_HOSTS")}   # tên miền được phép trong header Host (chống DNS rebinding)
COOKIE_SECURE = _bool("COOKIE_SECURE")      # bật khi phục vụ qua HTTPS (reverse proxy)
TRUST_PROXY = _bool("TRUST_PROXY")          # tin X-Forwarded-For từ reverse proxy để ghi đúng IP người dùng
MANAGE_SERVICES = _bool("MANAGE_SERVICES", True)   # false: dashboard không bật/tắt Ollama/LiteLLM/Phoenix (do Docker/systemd quản lý)
NO_BROWSER = _bool("NO_BROWSER")            # true: không tự mở trình duyệt khi khởi động
