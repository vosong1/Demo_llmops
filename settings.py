"""Cấu hình dùng chung (đọc từ biến môi trường / file .env). Chỉ dùng thư viện chuẩn.

Thứ tự ưu tiên: biến môi trường thật > file .env > giá trị mặc định.
Lần chạy đầu, nếu chưa có LITELLM_MASTER_KEY thì tự sinh khóa ngẫu nhiên và ghi vào .env
(không còn khóa cố định trong mã nguồn).
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


load_env()
GW_KEY = ensure_master_key()
PORT = _int("DASHBOARD_PORT", 8765)
OLLAMA_PORT = _int("OLLAMA_PORT", 11434)
GW_PORT = _int("LITELLM_PORT", 4000)
PHOENIX_PORT = _int("PHOENIX_PORT", 6006)
GW_HOST = os.environ.get("LITELLM_BIND_HOST", "127.0.0.1")   # không mở ra LAN theo mặc định
