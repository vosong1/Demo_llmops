"""Điểm vào duy nhất của Demo 1.
  python main.py                  -> đo hiệu năng (serving) cho các model đã tải
  python main.py --trace          -> thêm tracing (cần phoenix serve đang chạy)
  python main.py --eval           -> thêm eval bằng promptfoo
  python main.py --gateway        -> đo thêm qua LiteLLM (cần litellm đang chạy ở cổng 4000)
  python main.py --all            -> chạy cả bốn
  python main.py --models qwen2.5:3b
"""
import argparse, json, os, shutil, subprocess, sys, urllib.request

import settings

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = f"http://localhost:{settings.OLLAMA_PORT}"

def installed_models():
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None

def run(cmd):
    print("\n$", " ".join(cmd)); return subprocess.run(cmd, cwd=HERE).returncode

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["qwen2.5:3b", "qwen2.5:7b"])
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--gateway", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    a.trace, a.eval, a.gateway = a.trace or a.all, a.eval or a.all, a.gateway or a.all

    have = installed_models()
    if have is None:
        sys.exit(f"Không kết nối được Ollama tại localhost:{settings.OLLAMA_PORT}. Hãy mở Ollama trước.")
    todo = [m for m in a.models if any(h == m or h.startswith(m + ":") for h in have)]
    for m in set(a.models) - set(todo):
        print(f"[bỏ qua] chưa có model {m}. Tải bằng: ollama pull {m}")
    if not todo:
        sys.exit("Chưa có model nào để đo.")

    print("== 1. SERVING: đo hiệu năng ==")
    for m in todo:
        run([sys.executable, "bench.py", "--model", m])

    gw_up = False
    if a.gateway:
        print("\n== 1b. GATEWAY: đo qua LiteLLM (so với trực tiếp để thấy overhead) ==")
        try:
            urllib.request.urlopen(f"http://localhost:{settings.GW_PORT}/health/liveliness", timeout=5); gw_up = True
        except Exception:
            print("LiteLLM chưa chạy. Mở cửa sổ khác: litellm --config litellm_config.yaml --host 127.0.0.1 --port 4000")
        alias = {"qwen2.5:3b": "qwen-3b", "qwen2.5:7b": "qwen-7b"}
        for mname in (todo if gw_up else []):
            if mname in alias:
                run([sys.executable, "bench.py", "--model", alias[mname], "--gateway"])

    if a.trace:
        print("\n== 2. TRACING: gửi trace sang Phoenix ==")
        run([sys.executable, "trace_phoenix.py"] + (["--gateway"] if gw_up else []))

    if a.eval:
        print("\n== 3. EVAL: promptfoo ==")
        pf = shutil.which("promptfoo")
        cmd = [pf] if pf else ([shutil.which("npx"), "promptfoo@latest"] if shutil.which("npx") else None)
        if cmd: run(cmd + ["eval", "-c", "promptfooconfig.yaml"])
        else: print("Chưa cài promptfoo/Node.js, bỏ qua bước eval.")

    print("\nXong. Xem results.md (bảng cho báo cáo) và các file results_*.csv.")

if __name__ == "__main__": main()
