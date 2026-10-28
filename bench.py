"""Đo độ trễ, tốc độ token, VRAM và kiểm tra thô đáp án cho Ollama.
Chỉ dùng thư viện chuẩn. Chạy: python bench.py --model qwen2.5:3b
"""
import argparse, csv, json, statistics, subprocess, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

import settings

def read(p): return open(p, encoding="utf-8").read()

def vram_mb():
    try:
        o = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                                     "--format=csv,noheader,nounits"], text=True, timeout=5)
        return int(o.strip().splitlines()[0])
    except Exception:
        return None

class VramSampler(threading.Thread):
    def __init__(self): super().__init__(daemon=True); self.peak = 0; self.stop = False
    def run(self):
        while not self.stop:
            v = vram_mb()
            if v: self.peak = max(self.peak, v)
            time.sleep(0.5)

def ask(url, model, prompt, num_predict):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    lat = time.perf_counter() - t0
    n, dur = d.get("eval_count", 0), d.get("eval_duration", 0)
    return {"latency_s": lat, "tokens": n, "tok_per_s": n / (dur / 1e9) if dur else 0.0,
            "answer": d.get("response", "").strip()}

def ask_gw(url, key, model, prompt, num_predict):
    """Gọi qua LiteLLM (chuẩn OpenAI). tok/s ở đây tính end-to-end (gồm cả xử lý prompt)."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": num_predict}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                             "Authorization": "Bearer " + key})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    lat = time.perf_counter() - t0
    n = d.get("usage", {}).get("completion_tokens", 0)
    return {"latency_s": lat, "tokens": n, "tok_per_s": n / lat if lat else 0.0,
            "answer": d["choices"][0]["message"]["content"].strip()}

def check(ans, q):
    a = ans.lower()
    if any(f in a for f in q["forbid"]): return "FAIL"
    return "PASS" if (not q["expect_any"] or any(k in a for k in q["expect_any"])) else "FAIL"

def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default=f"http://localhost:{settings.OLLAMA_PORT}")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 5])
    ap.add_argument("--num-predict", type=int, default=256)
    ap.add_argument("--gateway", action="store_true", help="gọi qua LiteLLM; --model dùng alias (qwen-3b, qwen-7b)")
    ap.add_argument("--gateway-url", default=f"http://localhost:{settings.GW_PORT}")
    ap.add_argument("--api-key", default=settings.GW_KEY)
    a = ap.parse_args()
    url = a.host + "/api/generate"
    if a.gateway:
        gurl = a.gateway_url + "/v1/chat/completions"
        call = lambda prompt, n: ask_gw(gurl, a.api_key, a.model, prompt, n)
    else:
        call = lambda prompt, n: ask(url, a.model, prompt, n)
    ctx, tpl = read("context.txt"), read("prompt.txt")
    qs = json.loads(read("questions.json"))
    mk = lambda q: tpl.replace("{{context}}", ctx).replace("{{question}}", q["question"])
    base = vram_mb()
    print(f"Warm-up (nạp model)... VRAM trước: {base} MB")
    call("Xin chào", 8)
    rows = []
    tag = " (qua LiteLLM)" if a.gateway else " (trực tiếp Ollama)"
    md = [f"\n### {a.model}{tag}\n", "| Concurrency | p50 (s) | p95 (s) | Tok/s TB | Req/s | VRAM đỉnh (MB) | Đạt/Tổng |",
          "|---|---|---|---|---|---|---|"]
    for c in a.concurrency:
        s = VramSampler(); s.start()
        t0 = time.perf_counter()
        with ThreadPoolExecutor(c) as ex:
            res = list(ex.map(lambda q: call(mk(q), a.num_predict), qs))
        wall = time.perf_counter() - t0
        s.stop = True; s.join()
        lats = [r["latency_s"] for r in res]
        ok = 0
        for q, r in zip(qs, res):
            v = check(r["answer"], q); ok += v == "PASS"
            rows.append({"model": a.model, "concurrency": c, "id": q["id"], "type": q["type"],
                         "latency_s": round(r["latency_s"], 2), "tok_per_s": round(r["tok_per_s"], 1),
                         "check": v, "answer": r["answer"].replace("\n", " ")})
        line = (f"| {c} | {pct(lats,50):.2f} | {pct(lats,95):.2f} | "
                f"{statistics.mean(r['tok_per_s'] for r in res):.1f} | {len(qs)/wall:.2f} | {s.peak} | {ok}/{len(qs)} |")
        md.append(line); print(line)
    fn = f"results_{a.model.replace(':','_')}{'_gw' if a.gateway else ''}.csv"
    with open(fn, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    open("results.md", "a", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"\nĐã ghi {fn} và results.md (dán bảng vào báo cáo).")
    print("Lưu ý: 'check' chỉ là kiểm tra từ khóa thô, cần đọc lại câu trả lời trong CSV.")

if __name__ == "__main__": main()
