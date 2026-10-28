# Changelog

## 2026-10-28-v4.3
- Bỏ khóa ghi cứng: `LITELLM_MASTER_KEY` và khóa cloud đọc từ `.env` / biến môi trường (`settings.py`); lần chạy đầu tự sinh khóa gateway.
- `litellm_config.yaml`: `master_key` và `api_key` dùng `os.environ/...`; bỏ block `customlmm` gây lỗi `LLM Provider NOT provided` (để lại mẫu đã comment); xóa mục tương ứng trong `models.json`.
- LiteLLM bind `127.0.0.1` thay vì `0.0.0.0`.
- Cổng Ollama/LiteLLM/Phoenix/dashboard cấu hình qua env, không còn rải trong mã.
- Thêm `.gitignore`, `.env.example`, `pyproject.toml`, `requirements-dev.txt`.
- Thêm `tests/` (15 test) và CI GitHub Actions (ruff, pytest, gitleaks).
- README cập nhật khớp code (đã có đăng nhập/RBAC) và ghi rõ giới hạn còn lại.
