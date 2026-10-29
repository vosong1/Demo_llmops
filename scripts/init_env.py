"""Tạo .env từ .env.example và sinh LITELLM_MASTER_KEY nếu còn trống. Chỉ dùng thư viện chuẩn, chạy được trên Windows/Linux/macOS.

    python scripts/init_env.py

Chạy lại nhiều lần vẫn an toàn: không ghi đè .env có sẵn và không đổi khóa đã có.
"""
import os
import re
import secrets
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV, EXAMPLE = os.path.join(ROOT, ".env"), os.path.join(ROOT, ".env.example")


def main():
    if not os.path.exists(ENV):
        if not os.path.exists(EXAMPLE):
            sys.exit("Không tìm thấy .env.example.")
        shutil.copyfile(EXAMPLE, ENV)
        print("Đã tạo .env từ .env.example")
    with open(ENV, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^LITELLM_MASTER_KEY=(.*)$", text, re.M)
    if m and m.group(1).strip():
        print("LITELLM_MASTER_KEY đã có, giữ nguyên.")
        return
    key = "sk-" + secrets.token_urlsafe(24)
    if m:
        text = text[:m.start()] + f"LITELLM_MASTER_KEY={key}" + text[m.end():]
    else:
        text = text.rstrip("\n") + f"\nLITELLM_MASTER_KEY={key}\n"
    with open(ENV, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        os.chmod(ENV, 0o600)       # Linux/macOS; Windows bỏ qua
    except OSError:
        pass
    print("Đã sinh LITELLM_MASTER_KEY mới trong .env (không commit file này).")


if __name__ == "__main__":
    main()
