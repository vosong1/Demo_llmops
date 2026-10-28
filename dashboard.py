"""LLMOps workbench: quản lý serving, RAG, GPU/VRAM và observability.
Chạy: python dashboard.py   (hoặc bấm đúp start.bat). Chỉ dùng thư viện chuẩn.
Bật/tắt dịch vụ, hỏi đáp RAG, theo dõi GPU/VRAM và hoạt động hệ thống.
"""
import atexit, csv, glob, hashlib, hmac, io, json, os, re, secrets, shutil, signal, socket, subprocess, sys, threading, time, urllib.request, webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import settings   # nạp .env, khóa gateway và cổng từ biến môi trường
from settings import GW_KEY, GW_HOST, GW_PORT, OLLAMA_PORT, PHOENIX_PORT, PORT

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs"); os.makedirs(LOGS, exist_ok=True)
BUILD = "2026-10-28-v4.3"   # hiện ở đầu trang: thấy dòng này nghĩa là đang chạy đúng bản mới
SERVER = None
_CHAT_STATUS, _CHAT_STATUS_LOCK = {}, threading.Lock()   # theo tên người dùng
IDLE = {"active": False, "stage": "idle", "message": "Sẵn sàng"}
def set_chat_status(user, stage, message, active=True):
    with _CHAT_STATUS_LOCK:
        _CHAT_STATUS[user] = {"active": active, "stage": stage, "message": message, "updated": time.time()}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

ACT_LOG = os.path.join(LOGS, "activity.jsonl")
_alock = threading.Lock()
events = []
try:
    with open(ACT_LOG, encoding="utf-8") as _f:
        for _line in _f.readlines()[-300:]:
            try: events.append(json.loads(_line))
            except ValueError: pass
except OSError:
    pass
_seq = [max([e.get("seq", 0) for e in events] or [0])]

def act(user, ip, action, detail="", status="ok"):
    """Ghi một dòng 'ai làm gì' vào bộ nhớ (hiển thị trực tiếp) và logs/activity.jsonl."""
    with _alock:
        _seq[0] += 1
        e = {"seq": _seq[0], "ts": datetime.now().isoformat(timespec="seconds"), "user": user, "ip": ip,
             "action": action, "detail": str(detail)[:300], "status": str(status)[:200]}
        events.append(e); del events[:-500]
        try:
            with open(ACT_LOG, "a", encoding="utf-8") as f: f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            pass

def ollama_exe():
    p = shutil.which("ollama")
    if p: return p
    for c in (r"D:\Ollama\ollama.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")):
        if os.path.exists(c): return c
    return "ollama"

SERVICES = {
    "ollama":  {"label": "Ollama", "note": "chạy model 3B, 7B", "port": OLLAMA_PORT,
                "cmd": lambda: [ollama_exe(), "serve"]},
    "litellm": {"label": "LiteLLM", "note": "gateway: key, giới hạn, fallback", "port": GW_PORT,
                "cmd": lambda: [sys.executable, "-m", "litellm.proxy.proxy_cli",
                                "--config", "litellm_config.yaml", "--host", GW_HOST, "--port", str(GW_PORT)]},
    "phoenix": {"label": "Phoenix", "note": "xem trace tại localhost:6006", "port": PHOENIX_PORT,
                "cmd": lambda: [sys.executable, "-m", "phoenix.server.main", "serve"]},
}
procs = {}

def port_open(p):
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=0.4): return True
    except OSError:
        return False

def kill(proc):
    if proc.poll() is not None: return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()

def start_service(name):
    s = SERVICES[name]
    if port_open(s["port"]): return "Đã chạy sẵn."
    log = open(os.path.join(LOGS, name + ".log"), "w", encoding="utf-8")
    try:
        procs[name] = subprocess.Popen(s["cmd"](), cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                       env=ENV, creationflags=NOWIN)
    except Exception as e:
        return f"Không bật được {s['label']}: {e}"
    return "Đang khởi động, chờ vài giây."

def parse_listen_pids(netstat_text, port):
    """Đọc kết quả `netstat -ano -p TCP` của Windows, trả các PID đang lắng nghe cổng."""
    pids = []
    for line in netstat_text.splitlines():
        p = line.split()
        if len(p) >= 5 and p[0].upper().startswith("TCP") and p[1].endswith(f":{port}") and p[2].endswith(":0") and p[-1] not in pids:
            pids.append(p[-1])
    return pids

def parse_tasklist_name(text):
    text = text.strip()
    return text.split('","')[0].strip('"') if text.startswith('"') else "?"

def listening_pids(port):
    """PID các tiến trình đang lắng nghe cổng (Windows: netstat, Linux: /proc, macOS: lsof)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=8, creationflags=NOWIN).stdout
            return parse_listen_pids(out, port)
        if os.path.isdir("/proc/net"):
            inodes = set()
            for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
                try: rows = open(fn).read().splitlines()[1:]
                except OSError: continue
                for ln in rows:
                    f = ln.split()
                    if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port: inodes.add(f[9])
            pids = []
            for d in os.listdir("/proc"):
                if not d.isdigit(): continue
                try:
                    for fd in os.listdir(f"/proc/{d}/fd"):
                        try: link = os.readlink(f"/proc/{d}/fd/{fd}")
                        except OSError: continue
                        if link.startswith("socket:[") and link[8:-1] in inodes: pids.append(d); break
                except OSError:
                    pass
            return pids
        if shutil.which("lsof"):
            return subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=8).stdout.split()
    except Exception:
        pass
    return []

def proc_name(pid):
    try:
        if os.name == "nt":
            return parse_tasklist_name(subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                                      text=True, timeout=8, creationflags=NOWIN).stdout)
        return open(f"/proc/{pid}/comm").read().strip()
    except Exception:
        return "?"

def kill_pid(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
    else:
        try: os.kill(int(pid), signal.SIGTERM)
        except OSError: pass

def wait_closed(port, secs=6):
    for _ in range(int(secs / 0.25)):
        if not port_open(port): return True
        time.sleep(0.25)
    return False

def stop_service(name, force=False, then_start=False):
    s = SERVICES[name]; p = procs.get(name)
    if p and p.poll() is None:                      # do bảng điều khiển bật: tắt thẳng
        kill(p); wait_closed(s["port"]); return {"msg": "Đã dừng."}
    if not port_open(s["port"]): return {"msg": "Dịch vụ đang tắt."}
    # Dịch vụ do nơi khác bật (PowerShell, khay hệ thống...): tìm tiến trình đang giữ đúng cổng của dịch vụ này.
    pids = [x for x in listening_pids(s["port"]) if x not in ("0", "4", str(os.getpid()))]
    if not pids: return {"msg": f"Không xác định được tiến trình đang giữ cổng {s['port']}. Hãy tắt thủ công."}
    desc = ", ".join(f"{proc_name(x)} (PID {x})" for x in pids)
    if not force:
        note = " Ollama chạy từ khay hệ thống có thể tự bật lại; muốn tắt hẳn hãy chọn Quit ở khay." if name == "ollama" else ""
        return {"needs_confirm": True, "msg": f"{s['label']} không do bảng điều khiển bật. Tắt tiến trình đang giữ cổng {s['port']}: {desc}"
                                              + (" rồi bật lại?" if then_start else "?") + note}
    for x in pids: kill_pid(x)
    if wait_closed(s["port"]): return {"msg": "Đã dừng.", "killed": desc}
    return {"msg": "Đã gửi lệnh tắt nhưng cổng vẫn mở" + (" (Ollama có thể đã tự khởi động lại từ khay hệ thống)." if name == "ollama" else ".")}

def service_action(name, action, force=False):
    if action == "start": return {"msg": start_service(name)}
    if action == "stop": return stop_service(name, force)
    if port_open(SERVICES[name]["port"]):           # restart: tắt rồi bật lại để nạp lại cấu hình
        r = stop_service(name, force, then_start=True)
        if r.get("needs_confirm") or r["msg"] != "Đã dừng.": return r
        r2 = {"msg": start_service(name)}
        if r.get("killed"): r2["killed"] = r["killed"]
        return r2
    return {"msg": start_service(name)}

atexit.register(lambda: [kill(p) for p in procs.values()])

# ---- tác vụ chạy một lần ----
def eval_cmd():
    pf = shutil.which("promptfoo")
    if pf: return [pf, "eval", "-c", "promptfooconfig.yaml"]
    npx = shutil.which("npx")
    return [npx, "promptfoo@latest", "eval", "-c", "promptfooconfig.yaml"] if npx else None

JOBS = {
    "serving": ("Đo serving", lambda: [sys.executable, "main.py", "--models", "qwen2.5:3b", "qwen2.5:7b"]),
    "gateway": ("Đo qua LiteLLM", lambda: [sys.executable, "main.py", "--gateway"]),
    "trace":   ("Gửi trace sang Phoenix", lambda: [sys.executable, "trace_phoenix.py"] + (["--gateway"] if port_open(GW_PORT) else [])),
    "eval":    ("Chấm eval bằng promptfoo", eval_cmd),
    "all":     ("Chạy tất cả", lambda: [sys.executable, "main.py", "--all"]),
}
job = {"name": None, "running": False, "log": [], "code": None, "proc": None}

def run_job(key, user="ẩn danh", ip="-"):
    if job["running"]: return "Đang có tác vụ chạy, hãy chờ hoặc bấm Dừng."
    if not port_open(OLLAMA_PORT): return "Ollama chưa chạy. Bấm Bật ở dòng Ollama trước."
    label, mk = JOBS[key]
    cmd = mk()
    job.update(name=label, running=True, log=[], code=None, proc=None, user=user, ip=ip, t0=time.time())
    if not cmd:
        job["log"].append("Chưa cài promptfoo hoặc Node.js nên không chạy được bước này.")
        job.update(running=False, code=-1); return "ok"
    def work():
        try:
            p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", env=ENV, creationflags=NOWIN, bufsize=1)
            job["proc"] = p
            for line in p.stdout: job["log"].append(ANSI.sub("", line.rstrip()))
            p.wait(); job["code"] = p.returncode
        except Exception as e:
            job["log"].append(f"Lỗi: {e}"); job["code"] = -1
        finally:
            job["running"] = False
            ok = job["code"] == 0
            act(user, ip, "kết thúc bài đo", f"{label} ({int(time.time() - job['t0'])}s)", "ok" if ok else f"lỗi, mã {job['code']}")
    threading.Thread(target=work, daemon=True).start()
    return "ok"

def read_csv(name):
    if not re.fullmatch(r"results_[\w.\-]+\.csv", name): return None
    p = os.path.join(HERE, name)
    if not os.path.exists(p): return None
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))

# ---- hỏi đáp: guardrail -> LiteLLM/Ollama -> trace -> log ----
DOCS = os.path.join(HERE, "docs"); os.makedirs(DOCS, exist_ok=True)
REQ_LOG = os.path.join(LOGS, "requests.jsonl")
MODELS_FILE = os.path.join(HERE, "models.json")     # model, alias LiteLLM, giá (USD / 1 triệu token)
LIMITS_FILE = os.path.join(HERE, "limits.json")     # hạn mức token mỗi ngày
DEFAULT_MODELS = {"qwen2.5:3b": {"alias": "qwen-3b", "cloud": False, "in_per_m": 0, "out_per_m": 0},
                  "qwen2.5:7b": {"alias": "qwen-7b", "cloud": False, "in_per_m": 0, "out_per_m": 0}}
DEFAULT_LIMITS = {"daily_tokens_per_user": 50000, "daily_usd_total": 1.0, "users": {}}

def load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f: json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except ValueError:
        return default

def models(): return load_json(MODELS_FILE, DEFAULT_MODELS)
def limits(): return load_json(LIMITS_FILE, DEFAULT_LIMITS)

_jl = {}
def read_jsonl(path):
    """Đọc file .jsonl, chỉ đọc lại khi file thay đổi (trước đây đọc lại cả file mỗi 5 giây)."""
    try: st = os.stat(path)
    except OSError: return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _jl.get(path)
    if hit and hit[0] == key: return hit[1]
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try: rows.append(json.loads(line))
            except ValueError: pass
    _jl[path] = (key, rows)
    return rows

def iter_requests():
    return (d for d in read_jsonl(REQ_LOG) if "vote" not in d)

def usage_today():
    today, rows = datetime.now().date().isoformat(), {}
    for d in iter_requests():
        if d.get("ts", "")[:10] != today or not d.get("route"): continue   # chỉ tính lượt đã gọi model
        k = (d.get("user", "ẩn danh"), d["model"])
        r = rows.setdefault(k, {"user": k[0], "model": k[1], "requests": 0, "in": 0, "out": 0, "cost": 0.0})
        r["requests"] += 1; r["in"] += d.get("prompt_tokens", 0); r["out"] += d.get("tokens", 0)
        r["cost"] += d.get("cost_usd", 0.0)
    return list(rows.values())

def user_tokens(user, rows):
    return sum(r["in"] + r["out"] for r in rows if r["user"] == user)

def over_budget(user, cfg):
    lim, rows = limits(), usage_today()
    cap = lim.get("users", {}).get(user, lim.get("daily_tokens_per_user", 0))
    if cap and user_tokens(user, rows) >= cap: return f"hết hạn mức {cap} token hôm nay"
    if cfg.get("cloud") and lim.get("daily_usd_total") and sum(r["cost"] for r in rows) >= lim["daily_usd_total"]:
        return "hết ngân sách đám mây hôm nay"
    return None
BAD_IN = [(r"bỏ qua (mọi |tất cả )?(chỉ dẫn|hướng dẫn|quy tắc)", "yêu cầu bỏ qua quy tắc"),
          (r"ignore (all |previous |the )?(instructions|rules)", "yêu cầu bỏ qua quy tắc"),
          (r"(chỉ dẫn|prompt) hệ thống|system prompt", "hỏi về chỉ dẫn hệ thống"),
          (r"\b\d{12}\b", "chứa dãy 12 số giống số giấy tờ")]
_tracing = {"on": False}
_lock = threading.Lock()

DOC_NAME = r"[\w .()\-]{1,120}\.(txt|md)"
_kb, _kb_lock = {"key": None, "docs": [], "chunks": []}, threading.Lock()

def _kb_refresh():
    files = sorted(glob.glob(os.path.join(DOCS, "*.txt")) + glob.glob(os.path.join(DOCS, "*.md")))
    try: key = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in files)
    except OSError: key = None
    with _kb_lock:
        if key is not None and key == _kb["key"]: return
        docs = []
        for path in files:
            try: txt = open(path, encoding="utf-8", errors="replace").read().strip()
            except OSError: continue
            if txt: docs.append((os.path.basename(path), txt))
        chunks, size, overlap = [], 900, 120
        for name, text in docs:
            start = 0
            while start < len(text):
                piece = text[start:start + size].strip()
                if piece: chunks.append((name, piece, set(re.findall(r"[\w-]+", piece.lower(), re.UNICODE))))
                if start + size >= len(text): break
                start += size - overlap
        _kb.update(key=key, docs=docs, chunks=chunks)

def load_knowledge(limit=6000):
    _kb_refresh()
    docs = list(_kb["docs"])
    return docs, [n for n, _ in docs]

def retrieve_chunks(question, top_k=4):
    """RAG từ vựng đơn giản: xếp hạng đoạn theo mức trùng từ với câu hỏi."""
    _kb_refresh()
    q_terms = set(re.findall(r"[\w-]+", question.lower(), re.UNICODE))
    if not _kb["chunks"] or not q_terms: return []
    out = []
    for name, piece, terms in _kb["chunks"]:
        common = len(q_terms & terms)
        if common: out.append({"source": name, "text": piece,
                               "score": common / len(q_terms) + common / max(1, len(terms)) * 0.25})
    return sorted(out, key=lambda x: x["score"], reverse=True)[:top_k]

def guard_in(q):
    if len(q) > 1000: return "câu hỏi quá dài"
    for pat, why in BAD_IN:
        if re.search(pat, q, re.I): return why
    return None

def ensure_tracing():
    if _tracing["on"] or not port_open(PHOENIX_PORT): return
    try:
        from phoenix.otel import register
        register(project_name="llmops-chat", endpoint=f"http://localhost:{PHOENIX_PORT}/v1/traces", auto_instrument=True)
        _tracing["on"] = True
    except Exception:
        pass

def http_chat(base, key, model, prompt, user="ẩn danh"):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 400, "user": user}).encode()
    req = urllib.request.Request(base + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=300) as r: d = json.loads(r.read())
    u = d.get("usage", {})
    return d["choices"][0]["message"]["content"].strip(), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

def call_llm(model, prompt, use_gw, user="ẩn danh"):
    base = f"http://localhost:{GW_PORT}" if use_gw else f"http://localhost:{OLLAMA_PORT}"
    key, name = (GW_KEY, models()[model]["alias"]) if use_gw else ("ollama", model)
    ensure_tracing()
    try:
        from openai import OpenAI
    except ImportError:
        return http_chat(base, key, name, prompt, user)
    try:
        from openinference.instrumentation import using_user   # gắn tên người dùng vào trace Phoenix
    except Exception:
        from contextlib import nullcontext as using_user
    with using_user(user):
        r = OpenAI(base_url=base + "/v1", api_key=key, timeout=300).chat.completions.create(
            model=name, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=400, user=user)
    u = r.usage
    return r.choices[0].message.content.strip(), (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)

def _chat(q, model, who, lang="vi"):
    user = who["username"]
    q = (q or "").strip()
    if not q: return {"error": "Hãy nhập câu hỏi."}
    cfg = models().get(model)
    if not cfg: return {"error": "Model không hợp lệ."}
    if cfg.get("cloud") and not who.get("allow_cloud"):
        return {"error": "Tài khoản của bạn chưa được phép dùng model đám mây. Hãy nhờ quản trị viên cấp quyền."}
    rec = {"id": int(time.time() * 1000) * 10 + secrets.randbelow(10), "ts": datetime.now().isoformat(timespec="seconds"), "model": model,
           "user": user, "question": q, "route": None, "blocked": None, "answer": "", "tokens": 0, "prompt_tokens": 0, "cost_usd": 0.0, "latency_s": 0}
    t0 = time.perf_counter()
    set_chat_status(user, "checking", "Đang kiểm tra câu hỏi và hạn mức sử dụng…")
    why = guard_in(q) or over_budget(user, cfg)
    if why:
        rec.update(blocked=why, answer="Câu hỏi bị chặn: " + why + ".")
    else:
        if cfg.get("cloud"):
            if not port_open(GW_PORT): return {"error": "Model đám mây cần LiteLLM đang chạy (khóa API nằm ở LiteLLM). Bấm Bật LiteLLM."}
        elif not port_open(OLLAMA_PORT): return {"error": "Ollama chưa chạy. Bấm Bật ở dòng Ollama."}
        set_chat_status(user, "retrieval", "Đang tìm các đoạn tài liệu liên quan trong Knowledge Base…")
        chunks = retrieve_chunks(q)
        ctx = "\n\n".join(f"[Nguồn: {c['source']}]\n{c['text']}" for c in chunks)
        if not ctx:
            ctx = "Không tìm thấy đoạn tài liệu phù hợp. Hãy nói rõ rằng chưa có bằng chứng trong kho tài liệu; không tự bịa chính sách hay trích dẫn."
        prompt = open(os.path.join(HERE, "prompt.txt"), encoding="utf-8").read() \
            .replace("{{context}}", ctx).replace("{{question}}", q)
        if lang == "en":      # giao diện tiếng Anh: yêu cầu trả lời bằng tiếng Anh
            prompt += "\n\nIMPORTANT: Write the answer in English. If the documents contain no relevant information, reply exactly: \"No relevant information found in the documents.\""
        use_gw = port_open(GW_PORT)
        set_chat_status(user, "generating", f"Đã chuẩn bị {len(chunks)} đoạn tài liệu. Đang gửi prompt tới {model} và chờ model sinh câu trả lời…")
        try:
            ans, pt, ct = call_llm(model, prompt, use_gw, user)
            rec["route"] = "LiteLLM" if use_gw else "Ollama trực tiếp"
        except Exception as e:
            if not use_gw or cfg.get("cloud"): return {"error": f"Lỗi khi gọi model: {e}"}
            try:
                ans, pt, ct = call_llm(model, prompt, False, user)
                rec["route"] = "Ollama trực tiếp (LiteLLM lỗi)"
            except Exception as e2:
                return {"error": f"Lỗi khi gọi model: {e2}"}
        if "TÀI LIỆU:" in ans or "Chỉ trả lời dựa trên" in ans:
            rec.update(blocked="lộ chỉ dẫn hệ thống", answer="Câu trả lời bị chặn vì có dấu hiệu lộ chỉ dẫn hệ thống.")
        else:
            rec.update(answer=ans)
        rec.update(tokens=ct, prompt_tokens=pt, sources=list(dict.fromkeys(c["source"] for c in chunks)),
                   retrieval_chunks=len(chunks),
                   cost_usd=round((pt * cfg.get("in_per_m", 0) + ct * cfg.get("out_per_m", 0)) / 1e6, 6))
    rec["latency_s"] = round(time.perf_counter() - t0, 2)
    set_chat_status(user, "done", "Đã hoàn tất câu trả lời.", active=False)
    rec["traced"] = _tracing["on"]
    meta = {k: rec.get(k) for k in META_KEYS}; meta["sources_n"] = len(rec.get("sources", []))
    with _lock:
        with open(REQ_LOG, "a", encoding="utf-8") as f: f.write(json.dumps(meta, ensure_ascii=False) + "\n")     # số liệu chung, KHÔNG có nội dung chat
        with open(chat_path(who["uid"]), "a", encoding="utf-8") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")   # nội dung chỉ nằm ở lịch sử riêng
    return rec

def chat(q, model, who, ip="-", lang="vi"):
    user = who["username"]
    try:
        r = _chat(q, model, who, lang)
    finally:
        if _CHAT_STATUS.get(user, IDLE).get("active"): set_chat_status(user, "idle", "Sẵn sàng", active=False)
    # Nhật ký hoạt động chỉ ghi số liệu, không ghi nội dung câu hỏi (nội dung là riêng tư của người dùng).
    if r.get("error"):
        act(user, ip, "hỏi đáp", f"[{model}]", "lỗi: " + r["error"])
    else:
        act(user, ip, "hỏi đáp", f"[{model}]",
            ("bị chặn: " + r["blocked"]) if r["blocked"] else f"ok · {r['latency_s']}s · {r['route']} · {r.get('prompt_tokens', 0)}+{r.get('tokens', 0)} token"
            + (f" · ${r['cost_usd']}" if r.get("cost_usd") else ""))
    return r

META_KEYS = ("id", "ts", "user", "model", "route", "blocked", "tokens", "prompt_tokens", "cost_usd", "latency_s", "retrieval_chunks", "traced")

def chat_path(uid):
    return os.path.join(CHATS, uid + ".jsonl")      # uid là chuỗi hex do hệ thống sinh, không lấy từ người dùng

def history_for(uid, n=30):
    """Lịch sử chat RIÊNG của một tài khoản, mới nhất trước."""
    rows = read_jsonl(chat_path(uid))
    votes = {d["id"]: d["vote"] for d in rows if "vote" in d}
    items = [dict(d, feedback=votes.get(d["id"])) for d in rows if "vote" not in d]
    return items[-n:][::-1], len(items)

def own_record(uid, rid):
    return any(d.get("id") == rid and "vote" not in d for d in read_jsonl(chat_path(uid)))

def migrate_legacy():
    """Bản cũ ghi cả nội dung chat vào log chung. Chuyển nội dung sang file lưu trữ riêng, log chung chỉ giữ số liệu."""
    rows = read_jsonl(REQ_LOG)
    if not any("question" in d for d in rows): return 0
    old = [d for d in rows if "question" in d]
    with open(os.path.join(LOGS, "legacy_chat_history.jsonl"), "a", encoding="utf-8") as f:
        for d in old: f.write(json.dumps(d, ensure_ascii=False) + "\n")
    tmp = REQ_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for d in rows:
            if "question" in d:
                d = {k: d.get(k) for k in META_KEYS} | {"sources_n": len(d.get("sources", []))}
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, REQ_LOG)
    return len(old)

def pct(xs, p):
    if not xs: return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

def metrics():
    """Chỉ số vận hành: hôm nay và 7 ngày gần đây, lấy từ logs/requests.jsonl."""
    rows = read_jsonl(REQ_LOG)
    votes = {d["id"]: d["vote"] for d in rows if "vote" in d}
    today = datetime.now().date()
    days = {(today - timedelta(days=i)).isoformat(): {"date": (today - timedelta(days=i)).isoformat(), "requests": 0, "tokens": 0}
            for i in range(6, -1, -1)}
    t = {"requests": 0, "answered": 0, "blocked": 0, "tokens": 0, "up": 0, "down": 0}
    lat, users = [], set()
    for d in rows:
        if "vote" in d or not d.get("ts"): continue
        day = d["ts"][:10]
        tok = d.get("tokens", 0) + d.get("prompt_tokens", 0)
        if day in days: days[day]["requests"] += 1; days[day]["tokens"] += tok
        if day != today.isoformat(): continue
        t["requests"] += 1; users.add(d.get("user", "ẩn danh"))
        if d.get("blocked"): t["blocked"] += 1
        if d.get("route"): t["answered"] += 1; t["tokens"] += tok; lat.append(d.get("latency_s", 0))
        v = votes.get(d.get("id"))
        if v in ("up", "down"): t[v] += 1
    t["p50"], t["p95"] = round(pct(lat, 50), 2), round(pct(lat, 95), 2)
    return {"today": t, "days": list(days.values()), "users": len(users)}

def ollama_get(path, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://localhost:{OLLAMA_PORT}" + path, timeout=timeout) as r: return json.loads(r.read())
    except Exception:
        return None

def ollama_info():
    tags, ps = ollama_get("/api/tags"), ollama_get("/api/ps")
    if tags is None: return {"up": False, "installed": [], "loaded": []}
    installed = [{"name": m["name"], "size": m.get("size", 0), "params": m.get("details", {}).get("parameter_size", ""),
                  "quant": m.get("details", {}).get("quantization_level", "")} for m in tags.get("models", [])]
    loaded = []
    for m in (ps or {}).get("models", []):
        size, vram = m.get("size", 0), m.get("size_vram", 0)
        loaded.append({"name": m.get("name", "?"), "size": size, "gpu_pct": round(100 * vram / size) if size else 0,
                       "expires": str(m.get("expires_at", ""))[11:19]})
    return {"up": True, "installed": installed, "loaded": loaded}

_cpu_prev = [None]
def system_stats():
    """CPU và RAM của máy (Windows: ctypes, Linux: /proc). Lỗi thì trả None, không làm hỏng trang."""
    out = {"cpu": None, "ram_used": None, "ram_total": None}
    try:
        if os.name == "nt":
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            out.update(ram_total=m.ullTotalPhys, ram_used=m.ullTotalPhys - m.ullAvailPhys)
            i, k, u = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
            cur = (i.value, k.value + u.value)          # thời gian kernel đã gồm cả thời gian rảnh
        else:
            mem = dict(l.split(":", 1) for l in open("/proc/meminfo").read().splitlines())
            tot, av = int(mem["MemTotal"].split()[0]) * 1024, int(mem["MemAvailable"].split()[0]) * 1024
            out.update(ram_total=tot, ram_used=tot - av)
            v = list(map(int, open("/proc/stat").readline().split()[1:8])); cur = (v[3] + v[4], sum(v))
        prev, _cpu_prev[0] = _cpu_prev[0], cur
        if prev and cur[1] > prev[1]: out["cpu"] = round(100 * (1 - (cur[0] - prev[0]) / (cur[1] - prev[1])), 1)
    except Exception:
        pass
    return out

_gpu = {"t": 0, "v": None}
def gpu_info():
    if time.time() - _gpu["t"] < 1.5 and _gpu["v"]: return _gpu["v"]   # nhiều tab mở cùng lúc chỉ gọi nvidia-smi 1 lần
    exe = shutil.which("nvidia-smi")
    if not exe:
        v = {"available": False, "message": "Không tìm thấy nvidia-smi. Cần GPU NVIDIA và driver đã cài; nếu dùng CPU-only thì mục này không có dữ liệu."}
    else:
        try:
            out = subprocess.check_output([exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                                          text=True, encoding="utf-8", errors="replace", timeout=2)
            name, used, total, util, temp = [x.strip() for x in out.strip().splitlines()[0].split(",", 4)]
            v = {"available": True, "name": name, "used_mib": int(used), "total_mib": int(total), "utilization": int(util),
                 "temperature": int(temp) if temp not in ("N/A", "[N/A]") else None}
        except Exception as e:
            v = {"available": False, "message": "Không đọc được GPU: " + str(e)[:160]}
    _gpu.update(t=time.time(), v=v)
    return v

def svc_status():
    def one(item):
        k, sv = item
        return k, {"label": sv["label"], "note": sv["note"], "port": sv["port"], "up": port_open(sv["port"])}
    with ThreadPoolExecutor(len(SERVICES)) as ex: return dict(ex.map(one, SERVICES.items()))

def save_json(path, data):
    tmp = path + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

def save_limits(body):
    try:
        tok, usd = int(body.get("daily_tokens_per_user", 0)), float(body.get("daily_usd_total", 0))
        users = {str(k).strip()[:40]: int(v) for k, v in dict(body.get("users") or {}).items() if str(k).strip()}
    except (TypeError, ValueError):
        return "Dữ liệu hạn mức không hợp lệ."
    if tok < 0 or usd < 0 or any(v < 0 for v in users.values()): return "Hạn mức không được âm."
    save_json(LIMITS_FILE, {"daily_tokens_per_user": tok, "daily_usd_total": round(usd, 4), "users": users})
    return None

def save_model(body):
    name = str(body.get("name", "")).strip()
    alias = str(body.get("alias") or name).strip()
    if not (re.fullmatch(r"[\w.:\-]{1,60}", name) and re.fullmatch(r"[\w.:\-]{1,60}", alias)):
        return "Tên model và alias chỉ gồm chữ, số, dấu . : - _ (tối đa 60 ký tự)."
    try: pin, pout = float(body.get("in_per_m", 0)), float(body.get("out_per_m", 0))
    except (TypeError, ValueError): return "Giá không hợp lệ."
    if pin < 0 or pout < 0: return "Giá không được âm."
    m = dict(models()); m[name] = {"alias": alias, "cloud": bool(body.get("cloud")), "in_per_m": pin, "out_per_m": pout}
    save_json(MODELS_FILE, m)
    return None

# ================= Đa ngôn ngữ (Việt / Anh) =================
# Chuỗi gốc trong giao diện là tiếng Việt. Khi chọn English, trình duyệt dịch chữ hiển thị bằng từ điển và các luật bên dưới;
# chuỗi chưa có bản dịch được giữ nguyên tiếng Việt. Thêm bản dịch mới: bổ sung vào I18N_EN.
I18N_EN = {
# ---- Giao diện tĩnh (tab, tiêu đề, nhãn) ----
"Tổng quan": "Overview", "Hỏi đáp": "Q&A", "Sử dụng": "Usage", "Hoạt động": "Activity", "Cấu hình": "Settings",
"Người dùng": "Users", "Tài khoản": "Account", "Đăng xuất": "Sign out",
"Dịch vụ": "Services", "Máy chủ": "Host machine", "Model Ollama": "Ollama models",
"Lượt hỏi hôm nay": "Questions today", "Độ trễ p50 / p95 (giây)": "Latency p50 / p95 (seconds)",
"các lượt đã gọi model": "requests that called a model", "Bị chặn bởi guardrail / hạn mức": "Blocked by guardrail / quota",
"Người dùng đánh giá": "User ratings", "Chưa có lượt hỏi nào": "No questions yet", "Chưa có đánh giá": "No ratings yet",
"GPU chưa xác định": "GPU not detected", "Đang chờ dữ liệu từ NVIDIA": "Waiting for data from NVIDIA", "Đang đọc GPU…": "Reading GPU…",
"VRAM đang dùng": "VRAM in use", "Phần trăm tải GPU": "GPU load percentage", "Nhiệt độ": "Temperature",
"Live · cập nhật 2s": "Live · updates every 2s",
"3 phút gần nhất, chỉ ghi khi tab Tổng quan đang mở": "Last 3 minutes, recorded only while the Overview tab is open",
"Tự cập nhật mỗi 2 giây bằng nvidia-smi. VRAM phản ánh toàn GPU, không chỉ riêng Ollama.": "Refreshes every 2 seconds via nvidia-smi. VRAM reflects the whole GPU, not just Ollama.",
"Cần NVIDIA driver và nvidia-smi": "Requires the NVIDIA driver and nvidia-smi", "Không khả dụng": "Unavailable",
"Còn trống": "Free",
"Model lớn hơn VRAM sẽ dùng thêm RAM và chạy chậm hơn.": "Models larger than VRAM spill into RAM and run slower.",
"Đang nạp trong bộ nhớ": "Loaded in memory", "Đã tải về máy": "Downloaded to this machine", "Đang đọc…": "Reading…",
"Đang chạy": "Running", "Chưa chạy": "Not running",
"Bật Ollama để xem model.": "Start Ollama to see models.",
"Chưa nạp model nào (sẽ nạp khi có câu hỏi đầu tiên).": "No model loaded yet (it loads on the first question).",
"Chưa tải model nào. Dùng: ollama pull qwen2.5:3b": "No models downloaded. Use: ollama pull qwen2.5:3b",
"Hoạt động trực tiếp": "Live activity", "Đang kết nối...": "Connecting...", "Tải log (CSV)": "Download log (CSV)",
"Mất kết nối": "Disconnected", "Mất kết nối bảng điều khiển": "Lost connection to the dashboard",
"Giờ": "Time", "Ai": "Who", "Làm gì": "Action", "Chi tiết": "Details", "Kết quả": "Result",
"Token và chi phí hôm nay": "Tokens and cost today", "Lượt": "Requests", "Token vào": "Input tokens", "Token ra": "Output tokens",
"Chi phí (USD)": "Cost (USD)", "Chưa có lượt hỏi nào hôm nay.": "No questions today yet.",
"Hạn mức chỉnh trong limits.json, giá model trong models.json (USD cho 1 triệu token).": "Edit quotas in limits.json and model prices in models.json (USD per 1 million tokens).",
"7 ngày gần đây": "Last 7 days",
"Chiều cao cột là số lượt hỏi mỗi ngày. Rê chuột lên cột để xem số token.": "Bar height is the number of questions per day. Hover a bar to see token counts.",
"Hỏi": "Ask", "Đang hỏi...": "Asking...", "Đang xử lý…": "Processing…",
"Ví dụ: Nhân viên chính thức được nghỉ phép mấy ngày?": "Example: How many days of annual leave do full-time employees get?",
"1. Kiểm tra": "1. Checking", "2. Tìm tài liệu": "2. Finding documents", "3. Gọi LLM": "3. Calling the LLM", "4. Hoàn tất": "4. Done",
"Thêm tài liệu vào Knowledge Base": "Add document to the Knowledge Base", "Làm mới kho tài liệu": "Refresh document store",
"Tài liệu được chia chunk và truy xuất theo mức độ liên quan trước khi đưa vào prompt. Chỉ nhận .txt/.md.": "Documents are split into chunks and retrieved by relevance before being added to the prompt. Only .txt/.md accepted.",
"Knowledge Base đang trống. Hãy nhờ quản trị viên thêm tài liệu.": "The Knowledge Base is empty. Ask an administrator to add documents.",
"Knowledge Base đang trống. Thêm file .txt/.md để bật RAG có nguồn.": "The Knowledge Base is empty. Add .txt/.md files to enable RAG with sources.",
"Lịch sử chat của tôi": "My chat history", "Xóa lịch sử": "Clear history", "Xem thêm": "Show more", "Chưa có lịch sử": "No history yet",
"Xóa tài liệu": "Delete document",
"Bạn đánh giá: đúng": "You rated: correct", "Bạn đánh giá: sai": "You rated: wrong", "Đúng": "Correct", "Sai": "Wrong",
"không gọi model": "no model call",
"Chọn file .txt hoặc .md trước.": "Choose a .txt or .md file first.", "Chỉ hỗ trợ .txt và .md": "Only .txt and .md are supported",
"Xóa toàn bộ lịch sử chat của bạn? Không thể khôi phục.": "Delete your entire chat history? This cannot be undone.",
"Hạn mức sử dụng": "Usage quotas", "Lưu là áp dụng ngay": "Saved changes apply immediately",
"Token mỗi người mỗi ngày (0 = không giới hạn)": "Tokens per user per day (0 = unlimited)",
"Ngân sách model đám mây mỗi ngày (USD, 0 = không giới hạn)": "Daily cloud-model budget (USD, 0 = unlimited)",
"Hạn mức riêng từng người (mỗi dòng một người, dạng: tên=số token)": "Per-user quotas (one user per line, format: name=token count)",
"Lưu hạn mức": "Save quotas", "Model": "Model", "Giá tính theo USD cho 1 triệu token": "Prices in USD per 1 million tokens",
"Tên": "Name", "Alias LiteLLM": "LiteLLM alias", "Loại": "Type", "Giá vào": "Input price", "Giá ra": "Output price",
"Thêm hoặc sửa model": "Add or edit a model", "Tên model": "Model name", "Alias trong LiteLLM": "Alias in LiteLLM",
"Model đám mây (gọi qua LiteLLM)": "Cloud model (called via LiteLLM)", "Lưu model": "Save model",
"Model đám mây còn cần khối cấu hình tương ứng trong litellm_config.yaml và khóa API trong biến môi trường. Xem README mục G.": "A cloud model also needs a matching block in litellm_config.yaml and its API key in an environment variable. See README section G.",
"đám mây": "cloud", "Sửa": "Edit", "Xóa": "Delete", "(bạn)": "(you)", "chưa": "never",
"Tên đăng nhập": "Username", "Vai trò": "Role", "Trạng thái": "Status", "Model đám mây": "Cloud model", "Đăng nhập gần nhất": "Last sign-in",
"Lịch sử chat là riêng của từng người. Quản trị viên không đọc được nội dung chat của người khác, chỉ thấy số liệu sử dụng.": "Chat history is private to each user. Administrators cannot read other people's chats, only usage figures.",
"Thêm người dùng": "Add user", "Mật khẩu tạm (tối thiểu 8 ký tự)": "Temporary password (at least 8 characters)",
"Người dùng thường": "User", "Quản trị viên": "Administrator", "Quản trị": "Admin",
"Cho phép dùng model đám mây": "Allow cloud models", "Tạo tài khoản": "Create account",
"Đặt lại mật khẩu": "Reset password", "Người đó sẽ bị đăng xuất": "That person will be signed out", "Tài khoản của tôi": "My account",
"Mật khẩu hiện tại": "Current password", "Mật khẩu mới": "New password", "Mật khẩu mới (tối thiểu 8 ký tự)": "New password (at least 8 characters)",
"Nhập lại mật khẩu mới": "Repeat new password", "Đổi mật khẩu": "Change password",
"Đổi mật khẩu sẽ đăng xuất các thiết bị khác của bạn.": "Changing your password signs you out on your other devices.",
"Mã bản đang chạy": "Running build",
"Tắt rồi bật lại, dùng khi vừa đổi cấu hình": "Stop, then start again; use after changing the configuration",
# ---- Nút, trạng thái, nhãn động trong JS ----
"Bật": "Start", "Tắt": "Stop", "Khởi động lại": "Restart", "Đang bật…": "Turning on…", "Đang tắt…": "Turning off…", "Đang khởi động lại…": "Restarting…",
"Đang khởi động": "Starting", "Đã dừng.": "Stopped.", "Đã hủy.": "Cancelled.", "Khóa": "Lock", "Mở khóa": "Unlock",
"Nâng lên quản trị": "Promote to admin", "Hạ xuống người dùng": "Demote to user", "Cho phép đám mây": "Allow cloud", "Cấm đám mây": "Block cloud",
"Đã khóa": "Locked", "Đang hoạt động": "Active", "Được phép": "Allowed", "Không được phép": "Not allowed", "Không": "No",
"Không kết nối được bảng điều khiển.": "Could not reach the dashboard.",
"AI đang suy luận và tạo câu trả lời": "The AI is reasoning and writing the answer", "Đang kiểm tra yêu cầu": "Checking the request",
"Đang truy xuất tài liệu": "Retrieving documents", "Hoàn tất": "Done", "Sẵn sàng": "Ready",
# ---- Màn hình đăng nhập ----
"Đăng nhập": "Sign in", "Đăng nhập · LLMOps Workbench": "Sign in · LLMOps Workbench", "Đăng nhập để tiếp tục.": "Sign in to continue.",
"Mật khẩu": "Password", "Nhập lại mật khẩu": "Repeat password", "Hiện mật khẩu": "Show password",
"Chưa có tài khoản nào. Tạo tài khoản quản trị đầu tiên để bắt đầu.": "No accounts yet. Create the first administrator account to get started.",
"Tạo tài khoản quản trị": "Create administrator account",
"Tài khoản này có toàn quyền. Hãy nhớ mật khẩu. Nếu quên, chạy: python dashboard.py --set-password TÊN": "This account has full control. Remember the password. If you forget it, run: python dashboard.py --set-password NAME",
"Mật khẩu cần tối thiểu 8 ký tự.": "Password must be at least 8 characters.", "Hai mật khẩu không khớp.": "The two passwords do not match.",
"Hai mật khẩu mới không khớp.": "The two new passwords do not match.",
"Không đăng nhập được.": "Could not sign in.", "Không kết nối được máy chủ.": "Could not reach the server.",
# ---- Thông báo từ máy chủ ----
"Sai tên đăng nhập hoặc mật khẩu.": "Wrong username or password.", "Cần quyền quản trị.": "Administrator permission required.",
"Dịch vụ đang tắt.": "The service is already stopped.", "Dữ liệu hạn mức không hợp lệ.": "Invalid quota data.", "Dữ liệu quá lớn.": "Data too large.",
"File quá lớn (giới hạn 2 MB).": "File too large (2 MB limit).", "Giá không hợp lệ.": "Invalid price.", "Giá không được âm.": "Price cannot be negative.",
"Hãy dùng mục Tài khoản để đổi mật khẩu của chính bạn.": "Use the Account tab to change your own password.",
"Hãy nhập câu hỏi.": "Please enter a question.", "Hạn mức không được âm.": "Quotas cannot be negative.",
"Không thể tự xóa chính mình.": "You cannot delete yourself.", "Không thể tự đổi vai trò hoặc tự khóa chính mình.": "You cannot change your own role or lock yourself.",
"Không tìm thấy câu hỏi.": "Question not found.", "Không tìm thấy model.": "Model not found.", "Không tìm thấy người dùng.": "User not found.",
"Không tìm thấy tài liệu.": "Document not found.",
"Không tìm thấy nvidia-smi. Cần GPU NVIDIA và driver đã cài; nếu dùng CPU-only thì mục này không có dữ liệu.": "nvidia-smi not found. An NVIDIA GPU and driver are required; with CPU only this section has no data.",
"Model không hợp lệ.": "Invalid model.",
"Model đám mây cần LiteLLM đang chạy (khóa API nằm ở LiteLLM). Bấm Bật LiteLLM.": "Cloud models need LiteLLM running (the API key lives in LiteLLM). Start LiteLLM.",
"Mật khẩu hiện tại không đúng.": "Current password is incorrect.", "Mật khẩu tối đa 128 ký tự.": "Password can be at most 128 characters.",
"Ollama chưa chạy. Bấm Bật ở dòng Ollama trước.": "Ollama is not running. Start it from the Ollama row first.",
"Ollama chưa chạy. Bấm Bật ở dòng Ollama.": "Ollama is not running. Start it from the Ollama row.",
"Ollama trực tiếp": "Ollama (direct)", "Ollama trực tiếp (LiteLLM lỗi)": "Ollama (direct, LiteLLM failed)",
"Phiên không hợp lệ. Hãy tải lại trang.": "Invalid session. Please reload the page.",
"Phải còn ít nhất một model.": "At least one model must remain.", "Phải còn ít nhất một quản trị viên đang hoạt động.": "At least one active administrator must remain.",
"Tài khoản của bạn chưa được phép dùng model đám mây. Hãy nhờ quản trị viên cấp quyền.": "Your account is not allowed to use cloud models. Ask an administrator for permission.",
"Tài liệu trống hoặc không đọc được.": "The document is empty or unreadable.", "Tên file không hợp lệ; chỉ nhận .txt/.md.": "Invalid file name; only .txt/.md accepted.",
"Tên model và alias chỉ gồm chữ, số, dấu . : - _ (tối đa 60 ký tự).": "Model name and alias may only contain letters, digits and . : - _ (max 60 characters).",
"Tên đăng nhập gồm 3-32 ký tự: chữ, số, dấu . _ -": "Username must be 3-32 characters: letters, digits, . _ -", "Tên đăng nhập đã tồn tại.": "Username already exists.",
"Vai trò không hợp lệ.": "Invalid role.", "Đang có tác vụ chạy, hãy chờ hoặc bấm Dừng.": "A task is already running. Wait or press Stop.",
"Đang khởi động, chờ vài giây.": "Starting, wait a few seconds.", "Đã chạy sẵn.": "Already running.",
"Đã có tài khoản. Hãy đăng nhập.": "An account already exists. Please sign in.", "Đã cập nhật tài khoản.": "Account updated.",
"Đã lưu hạn mức, áp dụng ngay.": "Quotas saved and applied immediately.", "Đã đặt lại mật khẩu; người này bị đăng xuất.": "Password reset; that user has been signed out.",
"Đã xóa lịch sử chat của bạn.": "Your chat history was deleted.", "Đã đổi mật khẩu. Các thiết bị khác đã bị đăng xuất.": "Password changed. Your other devices were signed out.",
"Chưa cài promptfoo hoặc Node.js nên không chạy được bước này.": "promptfoo or Node.js is not installed, so this step cannot run.",
"Câu trả lời bị chặn vì có dấu hiệu lộ chỉ dẫn hệ thống.": "The answer was blocked because it looked like it leaked the system instructions.",
"Đang kiểm tra câu hỏi và hạn mức sử dụng…": "Checking the question and usage quota…",
"Đang tìm các đoạn tài liệu liên quan trong Knowledge Base…": "Looking for relevant passages in the Knowledge Base…",
"Đã hoàn tất câu trả lời.": "Answer completed.",
# ---- Dịch vụ, tác vụ, lý do chặn ----
"chạy model 3B, 7B": "runs 3B and 7B models", "gateway: key, giới hạn, fallback": "gateway: keys, limits, fallback", "xem trace tại localhost:6006": "view traces at localhost:6006",
"Đo serving": "Benchmark serving", "Đo qua LiteLLM": "Benchmark via LiteLLM", "Gửi trace sang Phoenix": "Send traces to Phoenix",
"Chấm eval bằng promptfoo": "Run eval with promptfoo", "Chạy tất cả": "Run all",
"yêu cầu bỏ qua quy tắc": "request to ignore the rules", "hỏi về chỉ dẫn hệ thống": "asks about the system instructions",
"chứa dãy 12 số giống số giấy tờ": "contains a 12-digit number that looks like an ID", "câu hỏi quá dài": "question too long",
"hết ngân sách đám mây hôm nay": "daily cloud budget used up", "lộ chỉ dẫn hệ thống": "leaked system instructions",
# ---- Nhật ký hoạt động: hành động, kết quả, người ----
"bật dịch vụ": "start service", "tắt dịch vụ": "stop service", "khởi động lại dịch vụ": "restart service", "chạy bài đo": "run benchmark",
"kết thúc bài đo": "benchmark finished", "dừng bài đo": "stop benchmark", "hỏi đáp": "Q&A", "đăng nhập": "sign-in", "đăng xuất": "sign-out",
"đăng nhập thất bại": "failed sign-in", "tạo tài khoản": "create account", "tạo tài khoản quản trị đầu tiên": "create first admin account",
"sửa tài khoản": "edit account", "xóa tài khoản": "delete account", "đặt lại mật khẩu": "reset password", "đổi mật khẩu": "change password",
"xóa lịch sử chat của mình": "delete own chat history", "đánh giá câu trả lời": "rate answer", "nạp tài liệu": "upload document",
"xóa tài liệu": "delete document", "đổi hạn mức": "change quotas", "lưu model": "save model", "xóa model": "delete model", "khởi động": "startup",
"đã bắt đầu": "started", "đã dừng": "stopped", "đúng": "correct", "sai": "wrong", "đã đăng xuất các thiết bị khác": "signed out other devices",
"đang bị khóa tạm thời": "temporarily locked", "sai mật khẩu": "wrong password", "không có tài khoản này": "no such account",
"tài khoản bị khóa": "account is locked", "lỗi: sai mật khẩu hiện tại": "error: wrong current password",
"(chưa đăng nhập)": "(not signed in)", "hệ thống": "system", "ẩn danh": "anonymous",
"Sao chép": "Copy", "Đã sao chép": "Copied", "Xem tin nhắn cũ hơn": "Show older messages", "Gửi": "Send",
"Hỏi về tài liệu của bạn": "Ask about your documents", "Chọn một câu gợi ý hoặc gõ câu hỏi của bạn bên dưới.": "Pick a suggestion or type your own question below.",
"Nhập câu hỏi… (Enter để gửi, Shift+Enter xuống dòng)": "Type a question… (Enter to send, Shift+Enter for a new line)",
"Câu trả lời dựa trên Knowledge Base và có thể sai. Hãy kiểm tra nguồn.": "Answers are based on the Knowledge Base and may be wrong. Check the sources.",
"Nhân viên chính thức được nghỉ phép mấy ngày?": "How many days of annual leave do full-time employees get?",
"Thời gian thử việc kéo dài bao lâu?": "How long does the probation period last?",
"Làm việc từ xa tối đa mấy ngày một tuần?": "How many days a week can I work remotely?",
}

I18N_RUNTIME = r'''const I18N=(()=>{
const EN=__DICT__;
let lang='vi';try{if(localStorage.getItem('lang')==='en')lang='en'}catch(e){}
const norm=s=>s.replace(/\s+/g,' ').trim();
// Luật cho chuỗi có tham số. Chuỗi gốc luôn là tiếng Việt; chưa khớp luật nào thì giữ nguyên.
const rules=[
 [/^(\d+) lượt hỏi$/,m=>`${m[1]} questions`],
 [/^(\d+) lượt trong 7 ngày$/,m=>`${m[1]} requests in 7 days`],
 [/^(\d+) tài khoản$/,m=>`${m[1]} accounts`],
 [/^(\d+)% đúng$/,m=>`${m[1]}% correct`],
 [/^(\d+) đã trả lời · (\d+) người dùng$/,m=>`${m[1]} answered · ${m[2]} users`],
 [/^(\d+) lượt bị chặn$/,m=>`${m[1]} blocked`],
 [/^(\d+) đúng · (\d+) sai$/,m=>`${m[1]} correct · ${m[2]} wrong`],
 [/^(.+): đã dùng (\S+)(?: \/ (\S+))? token hôm nay$/,m=>`${m[1]}: used ${m[2]}${m[3]?' / '+m[3]:''} tokens today`],
 [/^Chi phí đám mây: (.+) \/ (.+) USD$/,m=>`Cloud cost: ${m[1]} / ${m[2]} USD`],
 [/^Chi phí: (.+) USD$/,m=>`Cost: ${m[1]} USD`],
 [/^giữ đến (.*)$/,m=>`kept until ${m[1]}`],
 [/^(.+) GB · (\d+)% trên GPU$/,m=>`${m[1]} GB · ${m[2]}% on GPU`],
 [/^Còn trống (\d+) MiB$/,m=>`${m[1]} MiB free`],
 [/^Cập nhật (.+)$/,m=>`Updated ${m[1]}`],
 [/^(\S+): (\d+) lượt, (\d+) token$/,m=>`${m[1]}: ${m[2]} requests, ${m[3]} tokens`],
 [/^(.+) \(đám mây\)$/,m=>`${m[1]} (cloud)`],
 [/^Knowledge Base \((\d+)\): (.+)$/,m=>`Knowledge Base (${m[1]}): ${m[2]}`],
 [/^Đã (bật|tắt|khởi động lại) (.+)\.$/,m=>`${m[2]} ${m[1]==='bật'?'turned on':m[1]==='tắt'?'turned off':'restarted'}.`],
 [/^(.+): chưa thấy thay đổi, xem file logs\/(\w+)\.log$/,m=>`${m[1]}: no change detected, see logs/${m[2]}.log`],
 [/^Xóa tài liệu (.+) khỏi Knowledge Base\?$/,m=>`Delete document ${m[1]} from the Knowledge Base?`],
 [/^Xóa model (.+)\?$/,m=>`Delete model ${m[1]}?`],
 [/^Xóa tài khoản (.+) và toàn bộ lịch sử chat của người này\? Không thể khôi phục\.$/,m=>`Delete account ${m[1]} and all of this person's chat history? This cannot be undone.`],
 [/^Dòng không hợp lệ: (.+) \(cần dạng tên=số token\)$/,m=>`Invalid line: ${m[1]} (expected name=token count)`],
 [/^Đã nạp (.+) \((.+) ký tự\)\.$/,m=>`Loaded ${m[1]} (${m[2]} characters).`],
 [/^Đã xóa model (.+)\.$/,m=>`Model ${m[1]} deleted.`],
 [/^Đã xóa tài khoản (.+) và lịch sử chat của người đó\.$/,m=>`Account ${m[1]} and that person's chat history were deleted.`],
 [/^Đã xóa (.+)\.$/,m=>`${m[1]} deleted.`],
 [/^Đã tạo tài khoản (.+)\.$/,m=>`Account ${m[1]} created.`],
 [/^Đã lưu model (.+)\.$/,m=>`Model ${m[1]} saved.`],
 [/^(?:Đăng nhập sai quá nhiều lần|Sai quá nhiều lần)\. Thử lại sau (\d+) giây\.$/,m=>`Too many failed attempts. Try again in ${m[1]} seconds.`],
 [/^Không bật được (.+?): (.+)$/,m=>`Could not start ${m[1]}: ${m[2]}`],
 [/^Lỗi khi gọi model: (.+)$/,m=>`Error calling the model: ${m[1]}`],
 [/^Lỗi nội bộ: (.+)$/,m=>`Internal error: ${m[1]}`],
 [/^Lỗi: (.+)$/,m=>`Error: ${m[1]}`],
 [/^Không đọc được GPU: (.+)$/,m=>`Could not read the GPU: ${m[1]}`],
 [/^Không xác định được tiến trình đang giữ cổng (\d+)\. Hãy tắt thủ công\.$/,m=>`Could not identify the process holding port ${m[1]}. Please stop it manually.`],
 [/^Đã gửi lệnh tắt nhưng cổng vẫn mở(?: \(Ollama có thể đã tự khởi động lại từ khay hệ thống\))?\.$/,m=>`The stop command was sent but the port is still open${/Ollama/.test(m[0])?' (Ollama may have restarted itself from the system tray)':''}.`],
 [/^(.+) không do bảng điều khiển bật\. Tắt tiến trình đang giữ cổng (\d+): (.+?)( rồi bật lại)?\?( Ollama chạy từ khay hệ thống có thể tự bật lại; muốn tắt hẳn hãy chọn Quit ở khay\.)?$/,
   m=>`${m[1]} was not started by this dashboard. Stop the process holding port ${m[2]}: ${m[3]}${m[4]?' and start it again':''}?${m[5]?' Ollama running from the system tray may restart itself; to stop it completely choose Quit in the tray.':''}`],
 [/^Đã chuẩn bị (\d+) đoạn tài liệu\. Đang gửi prompt tới (.+) và chờ model sinh câu trả lời…$/,m=>`Prepared ${m[1]} document passages. Sending the prompt to ${m[2]} and waiting for the answer…`],
 [/^Câu hỏi bị chặn: (.+)\.$/,m=>`Question blocked: ${tr(m[1])}.`],
 [/^hết hạn mức (\d+) token hôm nay$/,m=>`daily limit of ${m[1]} tokens reached`],
 [/^bị chặn: (.+)$/,m=>`blocked: ${tr(m[1])}`,1],
 [/^bị từ chối: (.+)$/,m=>`rejected: ${tr(m[1])}`],
 [/^lỗi: (.+)$/,m=>`error: ${tr(m[1])}`],
 [/^lỗi, mã (-?\d+)$/,m=>`error, code ${m[1]}`],
 [/^(\d+) token\/người\/ngày, (\S+) USD\/ngày$/,m=>`${m[1]} tokens/user/day, ${m[2]} USD/day`],
 [/^câu #(\d+)$/,m=>`question #${m[1]}`],
 [/^Bảng điều khiển bật \(build (.+)\)$/,m=>`Dashboard started (build ${m[1]})`],
 [/^tên: (.+)$/,m=>`name: ${m[1]}`,1],
 [/^cổng (\d+)$/,m=>`port ${m[1]}`,1],
 [/^đã tắt (.+)$/,m=>`stopped ${m[1]}`,1],
 [/^(\d+) ký tự$/,m=>`${m[1]} characters`,1],
 [/^(\d+) token$/,m=>`${m[1]} tokens`,1],
 [/^(\d+)\+(\d+) token$/,m=>`${m[1]}+${m[2]} tokens`,1],
 [/^(\d+) vào \+ (\d+) ra token$/,m=>`${m[1]} in + ${m[2]} out tokens`,1],
 [/^nguồn: (.+)$/,m=>`sources: ${m[1]}`,1],
 [/^(.+) \((\d+)s\)$/,m=>{const x=one(m[1]);return x===undefined?undefined:`${x} (${m[2]}s)`}],
];
function one(k,wholeOnly){
  const o=EN[k];if(o!==undefined)return o;
  for(const [re,fn,seg] of rules){if(wholeOnly&&seg)continue;const m=k.match(re);if(m){const x=fn(m);if(x!==undefined)return x}}
}
// Dịch một chuỗi tiếng Việt sang ngôn ngữ đang chọn. Chuỗi ghép bằng " · " được dịch từng đoạn.
function tr(s){
  if(lang!=='en'||typeof s!=='string')return s;
  const k=norm(s);if(!k)return s;
  const comp=k.includes(' · ');
  let o=one(k,comp);                       // chuỗi ghép: thử khớp nguyên chuỗi (không dùng luật dành cho đoạn) trước
  if(o===undefined&&comp){let ch=false;const parts=k.split(' · ').map(p=>{const x=one(p);if(x!==undefined){ch=true;return x}return p});if(ch)o=parts.join(' · ')}
  return o===undefined?s:s.replace(s.trim(),()=>o);
}
const SKIP={SCRIPT:1,STYLE:1,TEXTAREA:1},ATTRS=['placeholder','title'];
function fixText(n){
  const p=n.parentNode;if(!p||SKIP[p.nodeName])return;
  if(n.__out===undefined||n.nodeValue!==n.__out)n.__vi=n.nodeValue;     // văn bản mới do ứng dụng ghi: coi là bản gốc
  const out=tr(n.__vi);n.__out=out;if(n.nodeValue!==out)n.nodeValue=out;
}
function fixAttrs(el){
  for(const a of ATTRS){
    if(!el.hasAttribute||!el.hasAttribute(a))continue;
    const key='__a_'+a,cur=el.getAttribute(a);
    if(!el[key]||cur!==el[key].out)el[key]={vi:cur,out:cur};
    const out=tr(el[key].vi);el[key].out=out;if(cur!==out)el.setAttribute(a,out);
  }
}
function walk(root){
  if(!root)return;
  if(root.nodeType===3){fixText(root);return}
  if(root.nodeType!==1)return;
  fixAttrs(root);      // thuộc tính (placeholder, title) của mọi phần tử, kể cả textarea; chữ bên trong script/style/textarea bị fixText bỏ qua
  const tw=document.createTreeWalker(root,5);
  let n;while((n=tw.nextNode())){n.nodeType===3?fixText(n):fixAttrs(n)}
}
function toggles(){document.querySelectorAll('[data-lang]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.lang===lang)))}
function set(l){
  lang=l==='en'?'en':'vi';try{localStorage.setItem('lang',lang)}catch(e){}
  document.documentElement.lang=lang;walk(document.documentElement);toggles();
  try{document.dispatchEvent(new CustomEvent('langchange'))}catch(e){}
}
function init(){
  if(typeof document==='undefined'||!document.documentElement||typeof MutationObserver==='undefined')return;
  document.documentElement.lang=lang;toggles();if(lang==='en')walk(document.documentElement);
  new MutationObserver(recs=>{for(const r of recs){
    if(r.type==='childList')r.addedNodes.forEach(walk);
    else if(r.type==='characterData')fixText(r.target);
    else if(r.type==='attributes')fixAttrs(r.target);
  }}).observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:ATTRS});
  document.addEventListener('click',e=>{const b=e.target&&e.target.closest&&e.target.closest('[data-lang]');if(b)set(b.dataset.lang)});
}
try{init()}catch(e){}
return {tr,set,get lang(){return lang},get loc(){return lang==='en'?'en-US':'vi-VN'},confirm:m=>confirm(tr(m))};
})();'''
I18N_JS = I18N_RUNTIME.replace("__DICT__", json.dumps(I18N_EN, ensure_ascii=False))

AUTH_PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Đăng nhập · LLMOps Workbench</title>
<style>
:root{color-scheme:dark;--bg:#090d18;--ink:#edf2ff;--mute:#94a3b8;--line:#263247;--accent:#8b9cff;--accent2:#6d7cff;--warn:#fb923c}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(900px 500px at 15% -10%,#1b2750 0,transparent 60%),var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
.lang{position:absolute;right:18px;top:18px;display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden}.lang button{border:0;border-radius:0;padding:5px 11px;font:inherit;font-size:12px;background:transparent;color:var(--mute);cursor:pointer;width:auto}.lang button[aria-pressed=true]{background:#222e4a;color:#fff}
.card{position:relative;width:100%;max-width:410px;background:linear-gradient(160deg,#141d2f,#0f1626 70%);border:1px solid var(--line);border-radius:20px;padding:30px 28px 24px;box-shadow:0 24px 60px #0006}
h1{margin:0 0 4px;font-size:23px;letter-spacing:-.5px}h1 span{color:var(--accent)}.sub{margin:0 0 22px;color:var(--mute);font-size:13px}
label{display:block;margin:0 0 14px;color:var(--mute);font-size:12px}
label input{display:block;width:100%;margin-top:6px;padding:11px 13px;border-radius:11px;border:1px solid #34415b;background:#0c1321;color:var(--ink);font:inherit;font-size:14px}
label input:focus{outline:2px solid var(--accent);outline-offset:1px}
label.chk{display:flex;align-items:center;gap:8px;margin:-2px 0 16px}label.chk input{display:inline-block;width:auto;margin:0}
button.main{width:100%;padding:12px;border:0;border-radius:12px;background:linear-gradient(135deg,var(--accent2),#8b6dff);color:#fff;font:inherit;font-weight:650;cursor:pointer}
button.main:disabled{opacity:.55;cursor:default}button.main:focus-visible{outline:2px solid #fff;outline-offset:2px}
#err{margin:0 0 12px;color:var(--warn);font-size:13px}#err:empty{display:none}
.note{margin-top:16px;color:var(--mute);font-size:12px}.ver{margin-top:14px;text-align:center;color:#5b6a86;font-size:11px}
</style></head><body><main class="card">
<div class="lang" role="group" aria-label="Language"><button type="button" data-lang="vi" aria-pressed="true">VI</button><button type="button" data-lang="en" aria-pressed="false">EN</button></div>
<h1>LLMOps <span>Workbench</span></h1><p class="sub" id="sub"></p>
<form id="f"><label>Tên đăng nhập<input name="username" id="un" autocomplete="username" autofocus required maxlength="32"></label>
<label>Mật khẩu<input name="password" id="pw" type="password" autocomplete="current-password" required maxlength="128"></label>
<label id="l2" hidden>Nhập lại mật khẩu<input name="password2" id="pw2" type="password" autocomplete="new-password" maxlength="128"></label>
<label class="chk"><input type="checkbox" id="show"> Hiện mật khẩu</label>
<div id="err" role="alert"></div><button class="main" id="go" type="submit">Đăng nhập</button></form>
<div class="note" id="note"></div><div class="ver">build __BUILD__</div></main>
<script>
/*__I18N__*/
const MODE='__MODE__',$=s=>document.querySelector(s);
const setup=MODE==='setup';
$('#sub').textContent=setup?'Chưa có tài khoản nào. Tạo tài khoản quản trị đầu tiên để bắt đầu.':'Đăng nhập để tiếp tục.';
$('#go').textContent=setup?'Tạo tài khoản quản trị':'Đăng nhập';
$('#l2').hidden=!setup;$('#pw').autocomplete=setup?'new-password':'current-password';
if(setup)$('#note').textContent='Tài khoản này có toàn quyền. Hãy nhớ mật khẩu. Nếu quên, chạy: python dashboard.py --set-password TÊN';
$('#show').onchange=e=>{$('#pw').type=$('#pw2').type=e.target.checked?'text':'password'};
$('#f').onsubmit=async e=>{e.preventDefault();const err=$('#err'),b=$('#go');err.textContent='';
 const u=$('#un').value.trim(),p=$('#pw').value;
 if(setup){if(p.length<8){err.textContent='Mật khẩu cần tối thiểu 8 ký tự.';return}if(p!==$('#pw2').value){err.textContent='Hai mật khẩu không khớp.';return}}
 b.disabled=true;b.textContent='Đang xử lý…';
 try{const r=await fetch(setup?'/api/setup':'/api/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Requested-With':'dashboard'},body:JSON.stringify({username:u,password:p})});
  const j=await r.json();if(j.ok){location.reload();return}err.textContent=j.error||'Không đăng nhập được.'}
 catch(x){err.textContent='Không kết nối được máy chủ.'}
 b.disabled=false;b.textContent=setup?'Tạo tài khoản quản trị':'Đăng nhập'};
</script></body></html>"""

PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LLMOps Workbench</title>
<style>
:root{color-scheme:dark;--bg:#090d18;--panel:#111827;--panel2:#151e30;--ink:#edf2ff;--mute:#94a3b8;--line:#263247;--ok:#34d399;--off:#536078;--warn:#fb923c;--accent:#8b9cff;--accent2:#6d7cff;--logbg:#070b13;--logink:#dbeafe;--shadow:0 16px 42px #0003}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(ellipse at 12% -10%,#26366a66,transparent 38%),radial-gradient(ellipse at 100% 0%,#164e6350,transparent 28%),var(--bg);color:var(--ink);font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif;min-height:100vh}
main{max-width:1500px;margin:0 auto;padding:30px 30px 70px}h1{font-size:clamp(28px,3vw,40px);letter-spacing:-1.3px;margin:0;font-weight:780;line-height:1.15}h2{font-size:15px;margin:0;font-weight:700;letter-spacing:.1px}.sub{color:var(--mute);margin:10px 0 22px;font-size:14px}
h1 span{background:linear-gradient(100deg,#9baaff,#6ee7d4);-webkit-background-clip:text;background-clip:text;color:transparent!important}
.grid{display:grid;grid-template-columns:minmax(310px, .82fr) minmax(0,1.7fr);gap:18px;align-items:stretch;margin-bottom:17px}.grid>section{margin-bottom:0}@media(max-width:900px){main{padding:22px 14px 48px}.grid{grid-template-columns:1fr}}
section{background:linear-gradient(145deg,#141d2d,#101725 70%);border:1px solid var(--line);border-radius:18px;padding:21px;margin-bottom:17px;box-shadow:var(--shadow);min-width:0;position:relative;overflow:hidden}section:focus-within{border-color:#45557b}section .head{margin-bottom:16px}
.svc{display:flex;align-items:center;gap:12px;padding:13px 0;border-top:1px solid #263247}.svc:first-of-type{border-top:0;padding-top:2px}.dot{width:9px;height:9px;border-radius:50%;background:var(--off);flex:none}.dot.on{background:var(--ok);box-shadow:0 0 0 4px #34d3991c,0 0 14px #34d39970}.svc .t{flex:1;min-width:0}.svc b{display:block;font-size:14px}.svc small{color:var(--mute);font-size:12px}
button{font:inherit;font-size:12px;font-weight:700;border:1px solid #34415b;background:#1a2538;color:var(--ink);border-radius:10px;padding:9px 13px;cursor:pointer;transition:transform .15s,border-color .15s,background .15s;white-space:nowrap}button:hover{border-color:var(--accent);background:#263451;transform:translateY(-1px)}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}button.main{background:linear-gradient(110deg,#6578f6,#7b6ff0);border-color:transparent;color:white;box-shadow:0 5px 18px #6578f630}button.main:hover{background:linear-gradient(110deg,#7889ff,#9387ff)}button:disabled{opacity:.45;cursor:default;transform:none}
.actions{display:flex;flex-wrap:wrap;gap:8px}.msg{color:#fdba74;margin-top:10px;font-size:13px}.msg:empty,#hist:empty{display:none}.log{background:var(--logbg);color:var(--logink);font:12px/1.6 Consolas,"Cascadia Mono",monospace;border:1px solid var(--line);border-radius:12px;padding:14px;height:360px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:8px}.head h2{margin:0}.state{color:var(--mute);font-size:12px}.state:before{content:"";display:inline-block;width:6px;height:6px;background:var(--ok);border-radius:50%;margin:0 7px 1px 0;opacity:.85}
pre.md{white-space:pre-wrap;margin:0 0 14px;font:12px/1.55 Consolas,monospace}.tw{overflow:auto;max-height:340px;border:1px solid var(--line);border-radius:12px;margin-top:12px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:10px 12px;border-bottom:1px solid #263247;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#1a2538;color:#b8c5e0;font-size:10px;text-transform:uppercase;letter-spacing:.7px;z-index:1}tbody tr:hover{background:#1b2639}tbody tr:last-child td{border-bottom:0}td.pass{color:var(--ok);font-weight:700}td.fail{color:var(--warn);font-weight:700}
select{font:inherit;font-size:13px;padding:10px 12px;border-radius:10px;border:1px solid #34415b;background:#0d1422;color:var(--ink);max-width:100%}.row{display:flex;gap:9px;flex-wrap:wrap}.row input{flex:1;min-width:180px;font:inherit;font-size:13px;padding:10px 12px;border-radius:10px;border:1px solid #34415b;background:#0c1321;color:var(--ink)}input::placeholder{color:#71809b}.hint{color:var(--mute);font-size:12px;margin:10px 0 0}#hist{margin-top:12px}.who{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 22px;color:var(--mute);font-size:12px}.who input{font:inherit;padding:8px 10px;border-radius:9px;border:1px solid #34415b;background:#0c1321;color:var(--ink);width:210px}thead th{background:#1a2538}td small{color:var(--mute)}
.meter{height:7px;background:#27334a;border-radius:99px;overflow:hidden;margin:8px 0}#mbar{height:100%;background:linear-gradient(90deg,#6578f6,#6ee7d4);width:0;border-radius:99px;transition:width .4s}#mbar.hot{background:var(--warn)}#gpu-bar{height:100%;background:linear-gradient(90deg,#34d399,#6ee7d4);width:0;transition:width .4s;border-radius:99px}#gpu-bar.hot{background:var(--warn)}.gpu-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:10px}.gpu-stat{border:1px solid #2b3951;border-radius:13px;padding:14px;background:linear-gradient(145deg,#172236,#111a2a);min-width:0}.gpu-stat small{display:block;color:var(--mute);font-size:11px}.gpu-stat b{display:block;font-size:clamp(20px,2vw,27px);letter-spacing:-.7px;margin:6px 0;overflow-wrap:anywhere}.gpu-stat:first-child b{color:#9baaff}@media(max-width:650px){.gpu-grid{grid-template-columns:1fr}.gpu-stat:first-child{grid-column:auto}.row input{min-width:100%}.who input{width:100%}.who label{width:100%}section{padding:16px}}
a{color:#a9b6ff;text-decoration:none}a:hover{text-decoration:underline}
.chat-progress{display:flex;gap:14px;align-items:flex-start;padding:16px;margin:14px 0;border:1px solid #3a4c7b;border-radius:14px;background:linear-gradient(120deg,#1a2540,#111827)}.spinner{width:22px;height:22px;border:3px solid #46516a;border-top-color:#9baaff;border-radius:50%;animation:spin .8s linear infinite;flex:none;margin-top:2px}@keyframes spin{to{transform:rotate(360deg)}}.steps{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.steps span{font-size:11px;padding:5px 9px;border-radius:999px;background:#242e42;color:#a1acc0}.steps span.current{background:#3b4c8c;color:#fff}.steps span.complete{background:#164e3d;color:#a7f3d0}
.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:10px 30px;background:#0b1120ee;backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.brand{font-weight:780;font-size:17px;letter-spacing:-.4px}.brand span{color:var(--accent)}.brand em{font-style:normal;color:var(--mute);font-size:11px;font-weight:500;margin-left:10px;letter-spacing:0}
#tabs{display:flex;gap:4px;flex:1;flex-wrap:wrap}#tabs button{background:transparent;border-color:transparent;color:var(--mute);padding:8px 14px}#tabs button:hover{transform:none;background:#1a2538;border-color:transparent;color:var(--ink)}#tabs button.on{background:#222e4a;color:#fff;border-color:#3b4c8c}
.top .who{margin:0}main{padding-top:22px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:17px}.kpi{border:1px solid var(--line);border-radius:16px;padding:16px 18px;background:linear-gradient(145deg,#141d2d,#101725 70%);min-width:0}.kpi small{display:block;color:var(--mute);font-size:11px}.kpi b{display:block;font-size:26px;letter-spacing:-.8px;margin:4px 0;overflow-wrap:anywhere}
.cols2{display:grid;grid-template-columns:1.2fr 1fr;gap:18px;margin-bottom:17px}.cols2>section{margin-bottom:0}
.mrow{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;padding:9px 0;border-top:1px solid var(--line)}.mrow:first-child{border-top:0;padding-top:0}.mrow b{min-width:120px}.mrow span{color:var(--ok);font-size:12px}.mrow small{color:var(--mute);font-size:11px;margin-left:auto}.mrow.plain{border:0;padding:0}.mrow.plain span{color:var(--ink);margin-left:auto;font-size:14px;font-weight:700}
.sub2{color:var(--mute);font-size:11px;margin:14px 0 4px;text-transform:uppercase;letter-spacing:.8px}
.bars{display:flex;gap:10px;align-items:flex-end;height:160px;padding-top:8px}.bar{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:4px;min-width:0}.bar i{display:block;width:100%;max-width:56px;min-height:3px;background:linear-gradient(180deg,#8b9cff,#6578f6);border-radius:6px 6px 2px 2px}.bar span{font-size:11px;color:var(--mute)}.bar em{font-style:normal;font-size:11px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.chips:empty{display:none}.chip{display:inline-flex;align-items:center;gap:8px;padding:5px 5px 5px 12px;border:1px solid #34415b;border-radius:999px;background:#141d30;font-size:12px}.chip small{color:var(--mute)}.chip button{padding:1px 9px;border-radius:999px;font-size:15px;line-height:1.3}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:12px}label.f{display:flex;flex-direction:column;gap:5px;color:var(--mute);font-size:12px}label.f input,label.f textarea{font:inherit;font-size:13px;padding:9px 11px;border-radius:10px;border:1px solid #34415b;background:#0c1321;color:var(--ink)}label.f.chk{flex-direction:row;align-items:center;gap:8px}label.f.chk input{width:auto}
#toast{position:fixed;right:22px;bottom:22px;z-index:50;max-width:380px;padding:12px 16px;border-radius:12px;background:#1d2a47;border:1px solid #4b5f99;color:#fff;box-shadow:0 12px 36px #0008;font-size:13px}
#sys-cpu-bar,#sys-ram-bar{height:100%;width:0;border-radius:99px;background:linear-gradient(90deg,#34d399,#6ee7d4);transition:width .4s}#sys-ram-bar.hot,#sys-cpu-bar.hot{background:var(--warn)}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.cols2{grid-template-columns:1fr}.top{padding:10px 14px}}
.gpu-card{display:flex;flex-direction:column}.gpu-hist{flex:1;display:flex;flex-direction:column;min-height:110px;margin-top:14px}
.gh-head{display:flex;gap:16px;align-items:center;font-size:12px;color:var(--mute);margin-bottom:6px}.gh-head small{margin-left:auto;font-size:11px}.lg{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
#gpu-svg{flex:1;width:100%;min-height:80px;border:1px solid var(--line);border-radius:10px;background:#0c1321}
table button{padding:5px 11px;font-size:12px;border-radius:9px}
.acct{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--mute)}.acct b{color:var(--ink)}
.role{padding:2px 10px;border-radius:999px;font-size:11px;border:1px solid #3b4c8c;background:#1b2750;color:#c7d0ff}.role.user{border-color:#2f6b57;background:#10302a;color:#8de9c4}



.lang{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden}.lang button{border:0;border-radius:0;padding:5px 11px;font-size:12px;background:transparent;color:var(--mute)}.lang button:hover{transform:none}.lang button[aria-pressed=true]{background:#222e4a;color:#fff}
.chatpane{display:flex;flex-direction:column;height:calc(100vh - 190px);min-height:460px}
.kb{margin:0 0 4px;border:1px solid var(--line);border-radius:12px;padding:8px 12px}.kb summary{cursor:pointer;color:var(--mute);font-size:13px}
.thread{flex:1;overflow-y:auto;padding:6px 4px 18px;margin:6px 0}.tcol{max-width:820px;margin:0 auto}
.msg{display:flex;margin:16px 0}.msg.u{justify-content:flex-end}
.msg.u .bub{max-width:78%;background:#1c2744;border:1px solid #2f3f6b;border-radius:18px 18px 4px 18px;padding:11px 15px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.a{flex-direction:column;align-items:flex-start;max-width:92%}.msg.a .body{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}.msg.a.bad .body{color:var(--warn)}
.mmeta{display:flex;gap:10px;flex-wrap:wrap;color:var(--mute);font-size:11px;margin-top:8px}
.macts{display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap}.macts button{font-size:12px;padding:3px 11px;border-radius:999px}.macts .fbl{color:var(--mute);font-size:12px}
.thread .chat-progress{margin:8px 0 14px;padding:0;border:0;background:transparent}
#morehist{display:block;margin:4px auto 10px;font-size:12px;border-radius:999px;padding:5px 14px}
.empty{text-align:center;padding:52px 10px 10px}.empty h3{margin:0 0 6px;font-size:22px}.empty p{margin:0;color:var(--mute)}
.sugg{display:flex;flex-direction:column;gap:8px;align-items:center;margin:20px 0 12px}.sugg button{width:100%;max-width:520px;text-align:left;border-radius:14px;padding:11px 15px}
.composer{width:100%;max-width:820px;margin:0 auto;border:1px solid #34415b;border-radius:20px;background:#0d1422;padding:10px 12px 8px;box-shadow:0 8px 30px #0006}
.composer:focus-within{border-color:#6d7cff}
.composer textarea{display:block;width:100%;border:0;outline:0;background:transparent;color:var(--ink);font:inherit;font-size:15px;line-height:1.5;resize:none;max-height:160px}
.crow{display:flex;align-items:center;gap:10px;margin-top:6px}.crow .disc{flex:1;color:var(--mute);font-size:11px;min-width:0}
.send{width:36px;height:36px;border-radius:50%;padding:0;font-size:18px;line-height:1;background:linear-gradient(135deg,#6d7cff,#8b6dff);border:0;color:#fff;flex:none}.send:disabled{opacity:.45}
#cm{min-width:150px}#uploaddoc,#refreshdocs{font-size:12px}
</style></head><body>
<header class="top"><div class="brand">LLMOps <span>Workbench</span><em title="Mã bản đang chạy">build __BUILD__</em></div>
<nav id="tabs"></nav>
<div class="acct"><div class="lang" role="group" aria-label="Language"><button type="button" data-lang="vi" aria-pressed="true">VI</button><button type="button" data-lang="en" aria-pressed="false">EN</button></div><span id="whoami"></span><span class="role" id="rolebadge"></span><button id="logout">Đăng xuất</button></div></header>
<main>
<div class="kpis" data-tabs="overview">
<div class="kpi"><small>Lượt hỏi hôm nay</small><b id="k-req">—</b><small id="k-req2">&nbsp;</small></div>
<div class="kpi"><small>Độ trễ p50 / p95 (giây)</small><b id="k-lat">—</b><small>các lượt đã gọi model</small></div>
<div class="kpi"><small>Bị chặn bởi guardrail / hạn mức</small><b id="k-blk">—</b><small id="k-blk2">&nbsp;</small></div>
<div class="kpi"><small>Người dùng đánh giá</small><b id="k-fb">—</b><small id="k-fb2">&nbsp;</small></div>
</div>
<div class="grid" data-tabs="overview">
<section><div class="head"><h2>Dịch vụ</h2></div><div id="svcs"></div><div class="msg" id="smsg"></div></section>
<section class="gpu-card"><div class="head"><h2>GPU & VRAM</h2><span class="state" id="gpu-state">Đang đọc GPU…</span></div>
<div class="gpu-grid"><div class="gpu-stat"><small>VRAM đang dùng</small><b id="gpu-vram">—</b><div class="meter"><div id="gpu-bar"></div></div><small id="gpu-vram-detail">Đang chờ dữ liệu từ NVIDIA</small></div>
<div class="gpu-stat"><small>GPU utilization</small><b id="gpu-util">—</b><small>Phần trăm tải GPU</small></div>
<div class="gpu-stat"><small>Nhiệt độ</small><b id="gpu-temp">—</b><small id="gpu-name">GPU chưa xác định</small></div></div>
<div class="gpu-hist"><div class="gh-head"><span><i class="lg" style="background:#8b9cff"></i>VRAM</span><span><i class="lg" style="background:#34d399"></i>GPU</span><small>3 phút gần nhất, chỉ ghi khi tab Tổng quan đang mở</small></div>
<svg id="gpu-svg" viewBox="0 0 300 90" preserveAspectRatio="none"></svg></div>
<div class="hint">Tự cập nhật mỗi 2 giây bằng nvidia-smi. VRAM phản ánh toàn GPU, không chỉ riêng Ollama.</div></section>
</div>
<div class="cols2" data-tabs="overview">
<section><div class="head"><h2>Model Ollama</h2><span class="state" id="ol-state">Đang đọc…</span></div>
<div class="sub2" style="margin-top:0">Đang nạp trong bộ nhớ</div><div id="ol-loaded"></div>
<div class="sub2">Đã tải về máy</div><div id="ol-installed"></div></section>
<section><div class="head"><h2>Máy chủ</h2><span class="state">CPU · RAM</span></div>
<div class="mrow plain"><b>CPU</b><span id="sys-cpu">—</span></div><div class="meter"><div id="sys-cpu-bar"></div></div>
<div class="mrow plain" style="margin-top:12px"><b>RAM</b><span id="sys-ram">—</span></div><div class="meter"><div id="sys-ram-bar"></div></div>
<div class="hint">Model lớn hơn VRAM sẽ dùng thêm RAM và chạy chậm hơn.</div></section>
</div>
<section data-tabs="activity"><div class="head"><h2>Hoạt động trực tiếp</h2><span><span class="state" id="as">Đang kết nối...</span> <a href="/api/activity.csv" download="hoat_dong.csv">Tải log (CSV)</a></span></div>
<div class="tw" style="max-height:300px"><table><thead><tr><th>Giờ</th><th>Ai</th><th>Làm gì</th><th>Chi tiết</th><th>Kết quả</th></tr></thead><tbody id="act"></tbody></table></div></section>
<section data-tabs="usage"><div class="head"><h2>Token và chi phí hôm nay</h2><span class="state" id="ucap"></span></div>
<div class="meter"><div id="mbar"></div></div><div class="hint" id="mtxt" style="margin:0 0 10px"></div>
<div class="tw" style="max-height:220px"><table><thead><tr><th>Ai</th><th>Model</th><th>Lượt</th><th>Token vào</th><th>Token ra</th><th>Chi phí (USD)</th></tr></thead><tbody id="ut"></tbody></table></div>
<div class="hint">Hạn mức chỉnh trong limits.json, giá model trong models.json (USD cho 1 triệu token).</div></section>
<section data-tabs="usage" data-roles="admin"><div class="head"><h2>7 ngày gần đây</h2><span class="state" id="d7-total"></span></div>
<div class="bars" id="d7"></div><div class="hint">Chiều cao cột là số lượt hỏi mỗi ngày. Rê chuột lên cột để xem số token.</div></section>
<section data-tabs="chat" class="chatpane">
<div class="head"><h2>Knowledge & RAG Chat</h2><span><span class="state" id="histcount"></span> <button id="clearhist">Xóa lịch sử</button></span></div>
<details class="kb" id="kb"><summary id="kbsum">Knowledge Base</summary>
<div class="hint" id="docs"></div><div class="chips" id="doclist"></div>
<div class="row" data-roles="admin" style="margin-top:14px"><input type="file" id="docfile" accept=".txt,.md,text/plain,text/markdown"><button id="uploaddoc">Thêm tài liệu vào Knowledge Base</button><button id="refreshdocs">Làm mới kho tài liệu</button></div>
<div class="hint" data-roles="admin">Tài liệu được chia chunk và truy xuất theo mức độ liên quan trước khi đưa vào prompt. Chỉ nhận .txt/.md.</div></details>
<div class="thread" id="thread"><div class="tcol"><button id="morehist" hidden>Xem tin nhắn cũ hơn</button><div id="hist"></div>
<div id="chatprogress" class="chat-progress" hidden><div class="spinner"></div><div><b id="chatstage">Đang xử lý…</b><div id="chatdetail" class="hint" style="margin:4px 0 0"></div><div class="steps"><span data-stage="checking">1. Kiểm tra</span><span data-stage="retrieval">2. Tìm tài liệu</span><span data-stage="generating">3. Gọi LLM</span><span data-stage="done">4. Hoàn tất</span></div></div></div></div></div>
<div class="composer"><textarea id="cq" rows="1" placeholder="Nhập câu hỏi… (Enter để gửi, Shift+Enter xuống dòng)"></textarea>
<div class="crow"><select id="cm"></select><span class="disc">Câu trả lời dựa trên Knowledge Base và có thể sai. Hãy kiểm tra nguồn.</span><button id="ask" class="send" title="Gửi">↑</button></div></div></section>

<section data-tabs="config"><div class="head"><h2>Hạn mức sử dụng</h2><span class="state">Lưu là áp dụng ngay</span></div>
<div class="form"><label class="f">Token mỗi người mỗi ngày (0 = không giới hạn)<input id="cl-tok" type="number" min="0" step="1000"></label>
<label class="f">Ngân sách model đám mây mỗi ngày (USD, 0 = không giới hạn)<input id="cl-usd" type="number" min="0" step="0.1"></label></div>
<label class="f" style="margin-bottom:12px">Hạn mức riêng từng người (mỗi dòng một người, dạng: tên=số token)<textarea id="cl-users" rows="3" placeholder="Kaya=100000"></textarea></label>
<button class="main" id="cl-save">Lưu hạn mức</button></section>
<section data-tabs="config"><div class="head"><h2>Model</h2><span class="state">Giá tính theo USD cho 1 triệu token</span></div>
<div class="tw"><table><thead><tr><th>Tên</th><th>Alias LiteLLM</th><th>Loại</th><th>Giá vào</th><th>Giá ra</th><th></th></tr></thead><tbody id="cm-rows"></tbody></table></div>
<div class="sub2">Thêm hoặc sửa model</div>
<div class="form"><label class="f">Tên model<input id="mf-name" placeholder="qwen2.5:3b"></label><label class="f">Alias trong LiteLLM<input id="mf-alias" placeholder="qwen-3b"></label>
<label class="f">Giá vào<input id="mf-in" type="number" min="0" step="0.01" value="0"></label><label class="f">Giá ra<input id="mf-out" type="number" min="0" step="0.01" value="0"></label>
<label class="f chk"><input id="mf-cloud" type="checkbox"> Model đám mây (gọi qua LiteLLM)</label></div>
<button class="main" id="mf-save">Lưu model</button>
<div class="hint">Model đám mây còn cần khối cấu hình tương ứng trong litellm_config.yaml và khóa API trong biến môi trường. Xem README mục G.</div></section>
<section data-tabs="users"><div class="head"><h2>Người dùng</h2><span class="state" id="us-count"></span></div>
<div class="tw"><table><thead><tr><th>Tên đăng nhập</th><th>Vai trò</th><th>Trạng thái</th><th>Model đám mây</th><th>Đăng nhập gần nhất</th><th></th></tr></thead><tbody id="us-rows"></tbody></table></div>
<div class="hint">Lịch sử chat là riêng của từng người. Quản trị viên không đọc được nội dung chat của người khác, chỉ thấy số liệu sử dụng.</div></section>
<section data-tabs="users"><div class="head"><h2>Thêm người dùng</h2></div>
<div class="form"><label class="f">Tên đăng nhập<input id="nu-name" maxlength="32" autocomplete="off"></label><label class="f">Mật khẩu tạm (tối thiểu 8 ký tự)<input id="nu-pass" type="password" autocomplete="new-password"></label>
<label class="f">Vai trò<select id="nu-role"><option value="user">Người dùng thường</option><option value="admin">Quản trị viên</option></select></label>
<label class="f chk"><input id="nu-cloud" type="checkbox"> Cho phép dùng model đám mây</label></div><button class="main" id="nu-save">Tạo tài khoản</button></section>
<section data-tabs="users"><div class="head"><h2>Đặt lại mật khẩu</h2><span class="state">Người đó sẽ bị đăng xuất</span></div>
<div class="form"><label class="f">Tài khoản<select id="rp-user"></select></label><label class="f">Mật khẩu mới<input id="rp-pass" type="password" autocomplete="new-password"></label></div><button class="main" id="rp-save">Đặt lại mật khẩu</button></section>
<section data-tabs="account"><div class="head"><h2>Tài khoản của tôi</h2><span class="state" id="acc-role"></span></div>
<div class="mrow plain"><b>Tên đăng nhập</b><span id="acc-name"></span></div><div class="mrow plain" style="margin-top:8px"><b>Model đám mây</b><span id="acc-cloud"></span></div>
<div class="sub2">Đổi mật khẩu</div>
<div class="form"><label class="f">Mật khẩu hiện tại<input id="pw-cur" type="password" autocomplete="current-password"></label><label class="f">Mật khẩu mới (tối thiểu 8 ký tự)<input id="pw-new" type="password" autocomplete="new-password"></label><label class="f">Nhập lại mật khẩu mới<input id="pw-new2" type="password" autocomplete="new-password"></label></div>
<button class="main" id="pw-save">Đổi mật khẩu</button><div class="hint">Đổi mật khẩu sẽ đăng xuất các thiết bị khác của bạn.</div></section>
<div id="toast" hidden></div>
</main><script>
/*__I18N__*/
const $=s=>document.querySelector(s);
let ME=null,CSRF='';
const api=(u,o={})=>fetch(u,{...o,credentials:'same-origin'}).then(r=>{if(r.status===401){location.reload();return new Promise(()=>{})}return r.json()});
const post=(u,b)=>api(u,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'dashboard','X-CSRF-Token':CSRF},body:JSON.stringify(b)});
let svcData=null,svcLast=null;const busy={};let flashT=null;
function flash(m,ms=4000){const el=$('#smsg');el.textContent=m;clearTimeout(flashT);if(m)flashT=setTimeout(()=>{el.textContent=''},ms)}
const LAB={start:['Đang bật…','Đã bật '],stop:['Đang tắt…','Đã tắt '],restart:['Đang khởi động lại…','Đã khởi động lại ']};
function renderSvcs(d){svcLast=d;
 for(const [k,s] of Object.entries(d)){const b=busy[k];if(!b)continue;
  if(!s.up)b.seenDown=true;
  const done=b.a==='stop'?!s.up:(s.up&&(b.a==='start'||b.seenDown||(b.posted&&Date.now()-b.posted>8000)));
  if(done){delete busy[k];flash(LAB[b.a][1]+s.label+'.',2500)}
  else if(Date.now()-b.t>45000){delete busy[k];flash(s.label+': chưa thấy thay đổi, xem file logs/'+k+'.log',6000)}}
 const h=Object.entries(d).map(([k,s])=>{const b=busy[k];
  const btn=b?`<button disabled>${LAB[b.a][0]}</button>`:(s.up?`<button data-a="restart" data-s="${k}" title="Tắt rồi bật lại, dùng khi vừa đổi cấu hình">Khởi động lại</button> <button data-a="stop" data-s="${k}">Tắt</button>`:`<button data-a="start" data-s="${k}" class="main">Bật</button>`);
  return `<div class="svc"><span class="dot ${s.up?'on':''}"></span><div class="t"><b>${s.label}</b><small>${s.note} · cổng ${s.port}</small></div>${btn}</div>`}).join('');
 if(h!==svcData){$('#svcs').innerHTML=h;svcData=h}}
$('#svcs').onclick=async e=>{const b=e.target.closest('button');if(!b||!b.dataset.s)return;const k=b.dataset.s,a=b.dataset.a;
 busy[k]={a,t:Date.now()};if(svcLast)renderSvcs(svcLast);
 const send=force=>post('/api/service',{name:k,action:a,force}).catch(()=>({msg:'Không kết nối được bảng điều khiển.'}));
 let r=await send(false);
 if(r.needs_confirm){if(I18N.confirm(r.msg))r=await send(true);else{delete busy[k];flash('Đã hủy.',2000);if(svcLast)renderSvcs(svcLast);return}}
 const ok=a==='stop'?r.msg==='Đã dừng.':String(r.msg).startsWith('Đang khởi động');
 if(!ok){delete busy[k];flash(r.msg,6000);if(svcLast)renderSvcs(svcLast)}else if(busy[k])busy[k].posted=Date.now();
 poll()};

async function poll(){
 const s=await api('/api/status');renderSvcs(s.services);
}
const gpuHist=[],GH_WIN=180000;
function drawGpuHist(){const now=Date.now();while(gpuHist.length&&now-gpuHist[0].t>GH_WIN)gpuHist.shift();
 const X=t=>(300-(now-t)/GH_WIN*300).toFixed(1),Y=p=>(88-Math.min(100,p)*0.86).toFixed(1);
 const line=(k,c)=>gpuHist.length>1?`<polyline fill="none" stroke="${c}" stroke-width="2" vector-effect="non-scaling-stroke" points="${gpuHist.map(h=>X(h.t)+','+Y(h[k])).join(' ')}"/>`:'';
 $('#gpu-svg').innerHTML='<line x1="0" y1="45" x2="300" y2="45" stroke="#2b3750" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"/>'+line('v','#8b9cff')+line('u','#34d399')}
async function pollGpu(){try{const g=await api('/api/gpu');
 if(!g.available){$('#gpu-state').textContent='Không khả dụng';$('#gpu-vram').textContent='N/A';$('#gpu-util').textContent='N/A';$('#gpu-temp').textContent='N/A';$('#gpu-vram-detail').textContent=g.message||'Cần NVIDIA driver và nvidia-smi';$('#gpu-name').textContent='';$('#gpu-bar').style.width='0';return}
 $('#gpu-state').textContent='Live · cập nhật 2s';$('#gpu-vram').textContent=`${g.used_mib} / ${g.total_mib} MiB`;
 $('#gpu-vram-detail').textContent=`Còn trống ${(g.total_mib-g.used_mib)} MiB`;
 $('#gpu-bar').style.width=Math.min(100,Math.round(g.used_mib*100/g.total_mib))+'%';
 $('#gpu-bar').className=(g.used_mib/g.total_mib)>=.85?'hot':'';
 $('#gpu-util').textContent=g.utilization+'%';$('#gpu-temp').textContent=g.temperature==null?'N/A':g.temperature+' °C';$('#gpu-name').textContent=g.name;gpuHist.push({t:Date.now(),v:g.used_mib*100/g.total_mib,u:g.utilization});drawGpuHist();
 }catch(e){$('#gpu-state').textContent='Mất kết nối'}}
const esc=t=>String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let chatPollTimer=null;
async function pollChatProgress(){try{const s=await api('/api/chat-status');$('#chatstage').textContent=({checking:'Đang kiểm tra yêu cầu',retrieval:'Đang truy xuất tài liệu',generating:'AI đang suy luận và tạo câu trả lời',done:'Hoàn tất'})[s.stage]||'Đang xử lý…';$('#chatdetail').textContent=s.message||'';document.querySelectorAll('.steps span').forEach(el=>{const order=['checking','retrieval','generating','done'];el.classList.toggle('current',el.dataset.stage===s.stage);el.classList.toggle('complete',order.indexOf(el.dataset.stage)<order.indexOf(s.stage));});}catch(e){}}
const scrollEnd=()=>{const t=$('#thread');t.scrollTop=t.scrollHeight};
function autoGrow(){const t=$('#cq');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,160)+'px'}
const SUGG=['Nhân viên chính thức được nghỉ phép mấy ngày?','Thời gian thử việc kéo dài bao lâu?','Làm việc từ xa tối đa mấy ngày một tuần?'];
function turnHtml(i){
 const meta=`${i.route||'không gọi model'} · ${i.latency_s}s · ${(i.prompt_tokens||0)+(i.tokens||0)} token${i.sources&&i.sources.length?' · nguồn: '+i.sources.join(', '):''}`;
 const fb=i.feedback?`<span class="fbl">${i.feedback==='up'?'Bạn đánh giá: đúng':'Bạn đánh giá: sai'}</span>`:`<button data-v="up" data-id="${i.id}">Đúng</button><button data-v="down" data-id="${i.id}">Sai</button>`;
 return `<div class="msg u"><div class="bub">${esc(i.question)}</div></div><div class="msg a${i.blocked?' bad':''}"><div class="body">${esc(i.answer)}</div><div class="mmeta"><span>${esc(String(i.ts).slice(11,16))}</span><span>${esc(meta)}</span></div><div class="macts"><button data-copy="1">Sao chép</button>${fb}</div></div>`}
const emptyHtml=(note,hasDocs)=>`<div class="empty"><h3>Hỏi về tài liệu của bạn</h3>${hasDocs?'<p>Chọn một câu gợi ý hoặc gõ câu hỏi của bạn bên dưới.</p><div class="sugg">'+SUGG.map(q=>`<button data-sugg="1">${q}</button>`).join('')+'</div>':''}<p class="hint">${esc(note)}</p></div>`;
async function ask(){const ta=$('#cq'),q=ta.value.trim(),b=$('#ask');if(!q||b.disabled)return;const P=$('#chatprogress'),H=$('#hist');
 b.disabled=ta.disabled=true;ta.value='';autoGrow();
 const em=H.querySelector('.empty');if(em)em.remove();
 H.insertAdjacentHTML('beforeend',`<div class="msg u"><div class="bub">${esc(q)}</div></div>`);
 P.hidden=false;scrollEnd();pollChatProgress();chatPollTimer=setInterval(pollChatProgress,500);
 let r;try{r=await post('/api/chat',{q,model:$('#cm').value,lang:I18N.lang})}catch(e){r={error:'Không kết nối được bảng điều khiển.'}}
 finally{clearInterval(chatPollTimer);P.hidden=true;b.disabled=false;ta.disabled=false}
 if(r.error){H.insertAdjacentHTML('beforeend',`<div class="msg a bad"><div class="body">${esc(r.error)}</div></div>`);ta.value=q;autoGrow();scrollEnd();ta.focus();return}
 await loadHist();scrollEnd();loadUsage();ta.focus()}
let histLimit=30,olderMode=false;
async function loadHist(){loadDocs();const th=$('#thread'),prevH=th.scrollHeight,prevTop=th.scrollTop,atBottom=prevH-prevTop-th.clientHeight<80;
 const r=await api('/api/history?limit='+histLimit);
 const note=r.docs.length?'Knowledge Base ('+r.docs.length+'): '+r.docs.join(', '):(ME.role==='admin'?'Knowledge Base đang trống. Thêm file .txt/.md để bật RAG có nguồn.':'Knowledge Base đang trống. Hãy nhờ quản trị viên thêm tài liệu.');
 $('#docs').textContent=note;$('#kbsum').textContent='Knowledge Base ('+r.docs.length+')';
 $('#histcount').textContent=r.total?r.total+' lượt hỏi':'Chưa có lịch sử';
 $('#hist').innerHTML=r.items.length?r.items.slice().reverse().map(turnHtml).join(''):emptyHtml(note,r.docs.length>0);
 $('#morehist').hidden=r.items.length>=r.total;
 if(olderMode){th.scrollTop=th.scrollHeight-prevH+prevTop;olderMode=false}else th.scrollTop=atBottom?th.scrollHeight:prevTop}
$('#hist').onclick=async e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.sugg){const t=$('#cq');t.value=b.textContent;autoGrow();t.focus();return}
 if(b.dataset.copy){const m=b.closest('.msg'),body=m&&m.querySelector('.body'),txt=body?body.textContent:'';
  try{await navigator.clipboard.writeText(txt)}catch(x){const t=document.createElement('textarea');t.value=txt;document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(y){}t.remove()}
  b.textContent='Đã sao chép';setTimeout(()=>{b.textContent='Sao chép'},1500);return}
 if(b.dataset.v){const r=await post('/api/feedback',{id:+b.dataset.id,vote:b.dataset.v});if(r.error)toast(r.error);loadHist()}};
$('#morehist').onclick=()=>{histLimit+=30;olderMode=true;loadHist()};
$('#clearhist').onclick=async()=>{if(!I18N.confirm('Xóa toàn bộ lịch sử chat của bạn? Không thể khôi phục.'))return;const r=await post('/api/history/clear',{});toast(r.error||r.msg);loadHist()};
$('#ask').onclick=ask;$('#cq').oninput=autoGrow;
$('#cq').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();ask()}};   // isComposing: không gửi khi đang gõ dấu tiếng Việt (Telex/VNI)
$('#refreshdocs').onclick=loadHist;
$('#uploaddoc').onclick=async()=>{const f=$('#docfile').files[0];if(!f){toast('Chọn file .txt hoặc .md trước.');return}const name=f.name;if(!/\.(txt|md)$/i.test(name)){toast('Chỉ hỗ trợ .txt và .md');return}const content=await f.text();const r=await post('/api/docs',{name,content});if(r.error){toast(r.error);return}$('#docfile').value='';loadHist();toast(r.msg)};
async function loadUsage(){const r=await api('/api/usage');const m=r.me,pct=m.cap?Math.min(100,Math.round(m.used*100/m.cap)):0;
 $('#mbar').style.width=pct+'%';$('#mbar').className=pct>=80?'hot':'';
 $('#mtxt').textContent=`${m.user}: đã dùng ${m.used.toLocaleString(I18N.loc)}${m.cap?' / '+m.cap.toLocaleString(I18N.loc):''} token hôm nay`;
 $('#ucap').textContent=r.cloud_cap?`Chi phí đám mây: ${r.cloud_cost} / ${r.cloud_cap} USD`:`Chi phí: ${r.cloud_cost} USD`;
 $('#ut').innerHTML=r.rows.length?r.rows.map(x=>`<tr><td>${esc(x.user)}</td><td>${esc(x.model)}</td><td>${x.requests}</td><td>${x.in}</td><td>${x.out}</td><td>${x.cost.toFixed(4)}</td></tr>`).join(''):'<tr><td colspan="6">Chưa có lượt hỏi nào hôm nay.</td></tr>'}
async function loadModels(){const r=await api('/api/models');$('#cm').innerHTML=r.models.map(m=>`<option value="${esc(m.name)}">${esc(m.name)}${m.cloud?' (đám mây)':''}</option>`).join('')}
let lastSeq=0;
async function pollAct(){try{const r=await api('/api/activity?since='+lastSeq);
 if(r.last<lastSeq){lastSeq=0;$('#act').innerHTML='';return}
 if(r.events.length){const rows=r.events.map(e=>`<tr><td>${esc(e.ts.slice(11,19))}</td><td>${esc(e.user)}<br><small>${esc(e.ip)}</small></td><td>${esc(e.action)}</td><td>${esc(e.detail)}</td><td class="${/lỗi|chặn|từ chối|sai/.test(e.status)?'fail':'pass'}">${esc(e.status)}</td></tr>`).reverse().join('');
  $('#act').insertAdjacentHTML('afterbegin',rows);lastSeq=r.last;while($('#act').rows.length>300)$('#act').deleteRow(-1)}
 $('#as').textContent='Cập nhật '+new Date().toLocaleTimeString(I18N.loc)}catch(e){$('#as').textContent='Mất kết nối bảng điều khiển'}}
const toast=(m,ms=3000)=>{const t=$('#toast');t.textContent=m;t.hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>{t.hidden=true},ms)};
const gb=b=>(b/1073741824).toFixed(1);
async function loadMetrics(){try{const r=await api('/api/metrics'),t=r.today;
 $('#k-req').textContent=t.requests;$('#k-req2').textContent=t.requests?`${t.answered} đã trả lời · ${r.users} người dùng`:'Chưa có lượt hỏi nào';
 $('#k-lat').textContent=t.answered?`${t.p50} / ${t.p95}`:'—';
 $('#k-blk').textContent=t.requests?Math.round(t.blocked*100/t.requests)+'%':'—';$('#k-blk2').textContent=`${t.blocked} lượt bị chặn`;
 const v=t.up+t.down;$('#k-fb').textContent=v?Math.round(t.up*100/v)+'% đúng':'—';$('#k-fb2').textContent=v?`${t.up} đúng · ${t.down} sai`:'Chưa có đánh giá';
 const mx=Math.max(1,...r.days.map(d=>d.requests));
 $('#d7').innerHTML=r.days.map(d=>`<div class="bar" title="${d.date}: ${d.requests} lượt, ${d.tokens} token"><em>${d.requests}</em><i style="height:${Math.round(d.requests*100/mx)}%"></i><span>${d.date.slice(5)}</span></div>`).join('');
 $('#d7-total').textContent=r.days.reduce((a,d)=>a+d.requests,0)+' lượt trong 7 ngày'}catch(e){}}
async function loadOllama(){try{const r=await api('/api/ollama');$('#ol-state').textContent=r.up?'Đang chạy':'Chưa chạy';
 $('#ol-loaded').innerHTML=!r.up?'<div class="hint" style="margin:0">Bật Ollama để xem model.</div>':(r.loaded.length?r.loaded.map(m=>`<div class="mrow"><b>${esc(m.name)}</b><span>${gb(m.size)} GB · ${m.gpu_pct}% trên GPU</span><small>giữ đến ${esc(m.expires||'—')}</small></div>`).join(''):'<div class="hint" style="margin:0">Chưa nạp model nào (sẽ nạp khi có câu hỏi đầu tiên).</div>');
 $('#ol-installed').innerHTML=r.installed.length?r.installed.map(m=>`<div class="mrow"><b>${esc(m.name)}</b><span style="color:var(--mute)">${gb(m.size)} GB</span><small>${esc([m.params,m.quant].filter(Boolean).join(' · '))}</small></div>`).join(''):(r.up?'<div class="hint" style="margin:0">Chưa tải model nào. Dùng: ollama pull qwen2.5:3b</div>':'')}catch(e){}}
async function loadSystem(){try{const r=await api('/api/system');
 $('#sys-cpu').textContent=r.cpu==null?'—':r.cpu+'%';$('#sys-cpu-bar').style.width=(r.cpu||0)+'%';$('#sys-cpu-bar').className=(r.cpu||0)>=90?'hot':'';
 if(r.ram_total){const p=Math.round(r.ram_used*100/r.ram_total);$('#sys-ram').textContent=`${gb(r.ram_used)} / ${gb(r.ram_total)} GB`;$('#sys-ram-bar').style.width=p+'%';$('#sys-ram-bar').className=p>=85?'hot':''}}catch(e){}}
async function loadDocs(){try{const r=await api('/api/docs');$('#doclist').innerHTML=r.documents.map(d=>`<span class="chip">${esc(d.name)} <small>${d.characters.toLocaleString(I18N.loc)} ký tự</small>${ME.role==='admin'?`<button data-del="${esc(d.name)}" title="Xóa tài liệu">×</button>`:''}</span>`).join('')}catch(e){}}
$('#doclist').onclick=async e=>{const b=e.target.closest('button[data-del]');if(!b)return;const n=b.dataset.del;if(!I18N.confirm('Xóa tài liệu '+n+' khỏi Knowledge Base?'))return;const r=await post('/api/docs/delete',{name:n});toast(r.error||r.msg);loadHist()};
let cfgModels={};
async function loadConfig(){try{const r=await api('/api/config'),L=r.limits;cfgModels=r.models;
 $('#cl-tok').value=L.daily_tokens_per_user??0;$('#cl-usd').value=L.daily_usd_total??0;
 $('#cl-users').value=Object.entries(L.users||{}).map(([k,v])=>k+'='+v).join('\n');
 $('#cm-rows').innerHTML=Object.entries(r.models).map(([k,m])=>`<tr><td>${esc(k)}</td><td>${esc(m.alias)}</td><td>${m.cloud?'đám mây':'local'}</td><td>${m.in_per_m}</td><td>${m.out_per_m}</td><td><button data-edit="${esc(k)}">Sửa</button> <button data-rm="${esc(k)}">Xóa</button></td></tr>`).join('')}catch(e){}}
$('#cl-save').onclick=async()=>{const users={};let bad=null;
 $('#cl-users').value.split('\n').map(x=>x.trim()).filter(Boolean).forEach(l=>{const i=l.lastIndexOf('='),n=l.slice(0,i).trim(),v=Number(l.slice(i+1));if(i<1||!n||!Number.isFinite(v)||v<0)bad=l;else users[n]=Math.round(v)});
 if(bad){toast('Dòng không hợp lệ: '+bad+' (cần dạng tên=số token)',5000);return}
 const r=await post('/api/limits',{daily_tokens_per_user:Number($('#cl-tok').value)||0,daily_usd_total:Number($('#cl-usd').value)||0,users});
 toast(r.error||r.msg,3500);if(!r.error)loadConfig()};
$('#cm-rows').onclick=async e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.edit){const m=cfgModels[b.dataset.edit];$('#mf-name').value=b.dataset.edit;$('#mf-alias').value=m.alias;$('#mf-cloud').checked=!!m.cloud;$('#mf-in').value=m.in_per_m;$('#mf-out').value=m.out_per_m;$('#mf-name').focus()}
 if(b.dataset.rm){if(!I18N.confirm('Xóa model '+b.dataset.rm+'?'))return;const r=await post('/api/model/delete',{name:b.dataset.rm});toast(r.error||r.msg);loadConfig();loadModels()}};
$('#mf-save').onclick=async()=>{const r=await post('/api/model',{name:$('#mf-name').value.trim(),alias:$('#mf-alias').value.trim(),cloud:$('#mf-cloud').checked,in_per_m:Number($('#mf-in').value)||0,out_per_m:Number($('#mf-out').value)||0});
 toast(r.error||r.msg,3500);if(!r.error){loadConfig();loadModels()}};
const ROLE_TABS={admin:[['overview','Tổng quan'],['chat','Hỏi đáp'],['usage','Sử dụng'],['activity','Hoạt động'],['config','Cấu hình'],['users','Người dùng'],['account','Tài khoản']],user:[['chat','Hỏi đáp'],['usage','Sử dụng'],['account','Tài khoản']]};
let usersCache=[];
async function loadUsers(){try{const r=await api('/api/users');usersCache=r.users;$('#us-count').textContent=r.users.length+' tài khoản';
 $('#us-rows').innerHTML=r.users.map(u=>{const me=u.username===ME.username;return `<tr><td>${esc(u.username)}${me?' <small>(bạn)</small>':''}</td><td>${u.role==='admin'?'Quản trị':'Người dùng thường'}</td><td>${u.disabled?'Đã khóa':'Đang hoạt động'}</td><td>${u.allow_cloud?'Được phép':'Không'}</td><td>${esc((u.last_login||'chưa').replace('T',' '))}</td><td>${me?'':`<button data-a="role" data-id="${u.id}">${u.role==='admin'?'Hạ xuống người dùng':'Nâng lên quản trị'}</button> <button data-a="lock" data-id="${u.id}">${u.disabled?'Mở khóa':'Khóa'}</button> <button data-a="cloud" data-id="${u.id}">${u.allow_cloud?'Cấm đám mây':'Cho phép đám mây'}</button> <button data-a="del" data-id="${u.id}">Xóa</button>`}</td></tr>`}).join('');
 $('#rp-user').innerHTML=r.users.filter(u=>u.username!==ME.username).map(u=>`<option value="${u.id}">${esc(u.username)}</option>`).join('')}catch(e){}}
$('#us-rows').onclick=async e=>{const b=e.target.closest('button');if(!b)return;const u=usersCache.find(x=>x.id===b.dataset.id);if(!u)return;let r;
 if(b.dataset.a==='role')r=await post('/api/users/update',{id:u.id,role:u.role==='admin'?'user':'admin'});
 if(b.dataset.a==='lock')r=await post('/api/users/update',{id:u.id,disabled:!u.disabled});
 if(b.dataset.a==='cloud')r=await post('/api/users/update',{id:u.id,allow_cloud:!u.allow_cloud});
 if(b.dataset.a==='del'){if(!I18N.confirm('Xóa tài khoản '+u.username+' và toàn bộ lịch sử chat của người này? Không thể khôi phục.'))return;r=await post('/api/users/delete',{id:u.id})}
 toast(r.error||r.msg,3500);loadUsers()};
$('#nu-save').onclick=async()=>{const r=await post('/api/users/create',{username:$('#nu-name').value.trim(),password:$('#nu-pass').value,role:$('#nu-role').value,allow_cloud:$('#nu-cloud').checked});
 toast(r.error||r.msg,4000);if(!r.error){$('#nu-name').value='';$('#nu-pass').value='';loadUsers()}};
$('#rp-save').onclick=async()=>{const r=await post('/api/users/reset-password',{id:$('#rp-user').value,password:$('#rp-pass').value});toast(r.error||r.msg,4000);if(!r.error)$('#rp-pass').value=''};
function renderAccount(){$('#acc-name').textContent=ME.username;$('#acc-role').textContent=ME.role==='admin'?'Quản trị viên':'Người dùng thường';$('#acc-cloud').textContent=ME.allow_cloud?'Được phép':'Không được phép'}
$('#pw-save').onclick=async()=>{const n=$('#pw-new').value;if(n!==$('#pw-new2').value){toast('Hai mật khẩu mới không khớp.',4000);return}
 const r=await post('/api/password',{current:$('#pw-cur').value,new:n});toast(r.error||r.msg,4000);if(!r.error)['#pw-cur','#pw-new','#pw-new2'].forEach(x=>{$(x).value=''})};
$('#logout').onclick=async()=>{try{await post('/api/logout',{})}catch(e){}location.reload()};
// Chỉ tải dữ liệu của tab đang mở, và dừng hẳn khi tab trình duyệt bị ẩn.
const TAB_JOBS={overview:()=>[poll(),pollGpu(),loadMetrics(),loadOllama(),loadSystem()],chat:()=>[],usage:()=>[loadUsage(),ME.role==='admin'&&loadMetrics()],activity:()=>[pollAct()],config:()=>[],users:()=>[],account:()=>[]};
let curTab=null;
const allowed=()=>ROLE_TABS[ME.role].map(x=>x[0]);
function tick(){if(ME&&curTab&&!document.hidden)try{TAB_JOBS[curTab]()}catch(e){}}
function setTab(t){if(!allowed().includes(t))t=allowed()[0];curTab=t;
 document.querySelectorAll('[data-tabs]').forEach(el=>{const okRole=!el.dataset.roles||el.dataset.roles===ME.role;el.hidden=!(okRole&&el.dataset.tabs.split(' ').includes(t))});
 document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
 if(location.hash!=='#'+t)history.replaceState(null,'','#'+t);
 enterTab(t);if(t==='chat'){try{$('#cq').focus()}catch(e){}}}
function enterTab(t){if(t==='chat'){loadHist();loadModels()}if(t==='config')loadConfig();if(t==='users')loadUsers();if(t==='account')renderAccount();tick()}
document.addEventListener('langchange',()=>{if(ME&&curTab)enterTab(curTab)});   // đổi ngôn ngữ: vẽ lại ngay giờ, số, nhãn do JS tạo ra
$('#tabs').onclick=e=>{const b=e.target.closest('button');if(b&&ME)setTab(b.dataset.t)};
window.onhashchange=()=>{if(ME)setTab(location.hash.slice(1))};
document.addEventListener('visibilitychange',tick);
(async()=>{ME=await api('/api/me');CSRF=ME.csrf;
 $('#whoami').innerHTML='<b>'+esc(ME.username)+'</b>';$('#rolebadge').textContent=ME.role==='admin'?'Quản trị':'Người dùng thường';$('#rolebadge').className='role '+ME.role;
 $('#tabs').innerHTML=ROLE_TABS[ME.role].map(([k,l])=>`<button data-t="${k}">${l}</button>`).join('');
 document.querySelectorAll('[data-roles]:not([data-tabs])').forEach(el=>{el.hidden=el.dataset.roles!==ME.role});
 loadModels();setTab(location.hash.slice(1)||ROLE_TABS[ME.role][0][0]);setInterval(tick,2000)})();
</script></body></html>"""


# ================= tài khoản, phiên đăng nhập, phân quyền =================
USERS_FILE = os.path.join(HERE, "users.json")
CHATS = os.path.join(HERE, "chats"); os.makedirs(CHATS, exist_ok=True)
COOKIE = "llmops_session"
SESSION_IDLE, SESSION_MAX = 8 * 3600, 24 * 3600          # 8 giờ không thao tác hoặc 24 giờ thì hết phiên
PBKDF2_ITER = 200_000
USERNAME_RE = r"[\w.\-]{3,32}"
_users_lock = threading.RLock()
sessions, _failed = {}, {}

def hash_password(pw, salt=None, iters=PBKDF2_ITER):
    salt = salt or secrets.token_bytes(16)
    return f"pbkdf2${iters}${salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt, iters).hex()}"

def verify_password(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it)).hex()
    except Exception:
        return False
    return hmac.compare_digest(dk, h)

_DUMMY = hash_password("khong-phai-mat-khau-that")      # so khớp giả khi tên đăng nhập không tồn tại, để thời gian phản hồi như nhau

def password_error(pw):
    if not isinstance(pw, str) or len(pw) < 8: return "Mật khẩu cần tối thiểu 8 ký tự."
    if len(pw) > 128: return "Mật khẩu tối đa 128 ký tự."
    return None

def users_db():
    try:
        with open(USERS_FILE, encoding="utf-8") as f: d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("users"), dict) else {"users": {}}
    except (OSError, ValueError):
        return {"users": {}}

def save_users(db):
    save_json(USERS_FILE, db)
    try: os.chmod(USERS_FILE, 0o600)      # Linux/macOS; Windows bỏ qua
    except OSError: pass

def find_user(username):
    name = (username or "").strip().lower()
    for uid, rec in users_db()["users"].items():
        if rec["username"].lower() == name: return uid, rec
    return None, None

def active_admins(db):
    return [k for k, v in db["users"].items() if v["role"] == "admin" and not v.get("disabled")]

def create_user(username, password, role="user", allow_cloud=False):
    username = str(username or "").strip()
    if not re.fullmatch(USERNAME_RE, username): return None, "Tên đăng nhập gồm 3-32 ký tự: chữ, số, dấu . _ -"
    err = password_error(password)
    if err: return None, err
    if role not in ("admin", "user"): return None, "Vai trò không hợp lệ."
    with _users_lock:
        db = users_db()
        if any(v["username"].lower() == username.lower() for v in db["users"].values()): return None, "Tên đăng nhập đã tồn tại."
        uid = secrets.token_hex(8)
        db["users"][uid] = {"username": username, "password": hash_password(password), "role": role, "disabled": False,
                            "allow_cloud": bool(allow_cloud) or role == "admin", "created": datetime.now().isoformat(timespec="seconds"), "last_login": None}
        save_users(db)
    return uid, None

def list_users():
    return [{"id": k, "username": v["username"], "role": v["role"], "disabled": bool(v.get("disabled")),
             "allow_cloud": bool(v.get("allow_cloud")) or v["role"] == "admin", "created": v.get("created"), "last_login": v.get("last_login")}
            for k, v in sorted(users_db()["users"].items(), key=lambda kv: kv[1]["username"].lower())]

def set_password(uid, pw):
    with _users_lock:
        db = users_db(); db["users"][uid]["password"] = hash_password(pw); save_users(db)

def drop_sessions(uid, keep=None):
    for t in [t for t, s in sessions.items() if s["uid"] == uid and t != keep]: sessions.pop(t, None)

def update_user(actor_uid, body):
    uid = str(body.get("id", ""))
    with _users_lock:
        db = users_db(); rec = db["users"].get(uid)
        if not rec: return "Không tìm thấy người dùng."
        if uid == actor_uid and ("role" in body or "disabled" in body): return "Không thể tự đổi vai trò hoặc tự khóa chính mình."
        if "role" in body:
            if body["role"] not in ("admin", "user"): return "Vai trò không hợp lệ."
            rec["role"] = body["role"]
        if "disabled" in body: rec["disabled"] = bool(body["disabled"])
        if "allow_cloud" in body: rec["allow_cloud"] = bool(body["allow_cloud"])
        rec["allow_cloud"] = bool(rec.get("allow_cloud")) or rec["role"] == "admin"
        if not active_admins(db): return "Phải còn ít nhất một quản trị viên đang hoạt động."
        save_users(db)
    if rec.get("disabled"): drop_sessions(uid)
    return None

def delete_user(actor_uid, uid):
    with _users_lock:
        db = users_db()
        if uid not in db["users"]: return "Không tìm thấy người dùng."
        if uid == actor_uid: return "Không thể tự xóa chính mình."
        del db["users"][uid]
        if not active_admins(db): return "Phải còn ít nhất một quản trị viên đang hoạt động."
        save_users(db)
    drop_sessions(uid)
    try: os.remove(chat_path(uid))            # xóa luôn lịch sử chat của người đó
    except OSError: pass
    return None

# ---- phiên đăng nhập ----
def new_session(uid):
    now = time.time(); tok = secrets.token_urlsafe(32)
    for t in [t for t, s in sessions.items() if now - s["created"] > SESSION_MAX or now - s["last"] > SESSION_IDLE]: sessions.pop(t, None)
    sessions[tok] = {"uid": uid, "csrf": secrets.token_urlsafe(24), "created": now, "last": now}
    return tok

def get_session(token):
    s = sessions.get(token)
    if not s: return None
    now = time.time()
    if now - s["created"] > SESSION_MAX or now - s["last"] > SESSION_IDLE:
        sessions.pop(token, None); return None
    rec = users_db()["users"].get(s["uid"])          # vai trò đọc lại từ file mỗi lần: đổi quyền hoặc khóa có hiệu lực ngay
    if not rec or rec.get("disabled"):
        sessions.pop(token, None); return None
    s["last"] = now
    return {"uid": s["uid"], "username": rec["username"], "role": rec["role"],
            "allow_cloud": bool(rec.get("allow_cloud")) or rec["role"] == "admin", "csrf": s["csrf"], "token": token}

def cookie_header(token, max_age=SESSION_MAX):
    return f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"

def lock_left(key):
    f = _failed.get(key)
    return int(f[1] - time.time()) + 1 if f and f[1] > time.time() else 0

def note_failure(key):
    n = (_failed.get(key) or [0, 0])[0] + 1
    _failed[key] = [n, time.time() + min(300, 30 * 2 ** (n - 5)) if n >= 5 else 0]     # từ lần sai thứ 5: khóa 30s, 60s, 120s... tối đa 5 phút
    if len(_failed) > 1000: _failed.clear()

def login(username, password):
    """Trả (token, lỗi, mã HTTP, lý do ghi nhật ký)."""
    key = str(username or "").strip().lower()[:40]
    left = lock_left(key)
    if left: return None, f"Đăng nhập sai quá nhiều lần. Thử lại sau {left} giây.", 429, "đang bị khóa tạm thời"
    uid, rec = find_user(username)
    good = verify_password(str(password or ""), rec["password"] if rec else _DUMMY)
    if not rec or not good or rec.get("disabled"):
        note_failure(key)
        return None, "Sai tên đăng nhập hoặc mật khẩu.", 401, ("tài khoản bị khóa" if rec and good else "sai mật khẩu" if rec else "không có tài khoản này")
    _failed.pop(key, None)
    with _users_lock:
        db = users_db(); db["users"][uid]["last_login"] = datetime.now().isoformat(timespec="seconds"); save_users(db)
    return new_session(uid), None, 200, "ok"

def cli_set_password(username, password):
    """Khôi phục quyền truy cập từ dòng lệnh: python dashboard.py --set-password TÊN"""
    err = password_error(password)
    if err: return err
    uid, rec = find_user(username)
    if not rec:
        _, err = create_user(username, password, "admin", True); return err
    set_password(uid, password)
    with _users_lock:
        db = users_db(); db["users"][uid]["disabled"] = False; save_users(db)
    return None

GET_ADMIN = {"/api/status", "/api/gpu", "/api/metrics", "/api/ollama", "/api/system", "/api/config", "/api/results", "/api/csv",
             "/api/activity", "/api/activity.csv", "/api/users"}
GET_USER = {"/api/me", "/api/chat-status", "/api/history", "/api/docs", "/api/models", "/api/usage"}
POST_ADMIN = {"/api/service", "/api/run", "/api/stop", "/api/docs", "/api/docs/delete", "/api/limits", "/api/model", "/api/model/delete",
              "/api/users/create", "/api/users/update", "/api/users/reset-password", "/api/users/delete"}
POST_USER = {"/api/chat", "/api/feedback", "/api/history/clear", "/api/password", "/api/logout"}
HTML = "text/html; charset=utf-8"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200, ctype="application/json; charset=utf-8", headers=None):
        body = obj.encode("utf-8") if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def bad_host(self):   # chặn DNS-rebinding: chỉ nhận Host là localhost
        return (self.headers.get("Host") or "").split(":")[0].lower() not in ("localhost", "127.0.0.1")

    def auth(self):
        try: c = SimpleCookie(self.headers.get("Cookie") or "")
        except Exception: return None
        m = c.get(COOKIE)
        return get_session(m.value) if m else None

    def do_GET(self):
        if self.bad_host(): return self.send({"error": "forbidden"}, 403)
        u = urlparse(self.path); path = u.path
        if path == "/api/ping":      # không cần đăng nhập: để bản mới nhận ra bản cũ khi thay thế
            return self.send({"app": "llmops-workbench", "build": BUILD})
        if path == "/":
            if not users_db()["users"]: return self.send(AUTH_PAGE.replace("__MODE__", "setup").replace("__BUILD__", BUILD).replace("/*__I18N__*/", I18N_JS), ctype=HTML)
            if not self.auth(): return self.send(AUTH_PAGE.replace("__MODE__", "login").replace("__BUILD__", BUILD).replace("/*__I18N__*/", I18N_JS), ctype=HTML)
            return self.send(PAGE.replace("__BUILD__", BUILD).replace("/*__I18N__*/", I18N_JS), ctype=HTML)
        sess = self.auth()
        if not sess: return self.send({"error": "unauthorized"}, 401)
        if path in GET_ADMIN and sess["role"] != "admin": return self.send({"error": "Cần quyền quản trị."}, 403)
        user, admin = sess["username"], sess["role"] == "admin"
        if path == "/api/me":
            return self.send({"username": user, "role": sess["role"], "allow_cloud": sess["allow_cloud"], "csrf": sess["csrf"]})
        if path == "/api/status":
            return self.send({"services": svc_status(), "job": {"name": job["name"], "running": job["running"],
                                                                "code": job["code"], "log": job["log"][-400:]}})
        if path == "/api/chat-status":
            with _CHAT_STATUS_LOCK: status = dict(_CHAT_STATUS.get(user, IDLE))
            return self.send(status)
        if path == "/api/gpu": return self.send(gpu_info())
        if path == "/api/metrics": return self.send(metrics())
        if path == "/api/ollama": return self.send(ollama_info())
        if path == "/api/system": return self.send(system_stats())
        if path == "/api/config": return self.send({"limits": limits(), "models": models()})
        if path == "/api/users": return self.send({"users": list_users()})
        if path == "/api/results":
            md = ""
            try: md = open(os.path.join(HERE, "results.md"), encoding="utf-8").read()
            except OSError: pass
            csvs = sorted(f for f in os.listdir(HERE) if re.fullmatch(r"results_[\w.\-]+\.csv", f))
            return self.send({"md": md, "csvs": csvs})
        if path == "/api/csv":
            rows = read_csv(parse_qs(u.query).get("name", [""])[0])
            return self.send({"rows": rows}) if rows else self.send({"error": "không tìm thấy"}, 404)
        if path == "/api/history":
            try: n = max(1, min(200, int(parse_qs(u.query).get("limit", ["30"])[0])))
            except ValueError: n = 30
            items, total = history_for(sess["uid"], n)
            return self.send({"items": items, "total": total, "docs": load_knowledge()[1]})
        if path == "/api/docs":
            docs, _ = load_knowledge()
            return self.send({"documents": [{"name": n, "characters": len(t)} for n, t in docs]})
        if path == "/api/models":
            return self.send({"models": [{"name": k, "cloud": bool(v.get("cloud"))} for k, v in models().items()
                                         if sess["allow_cloud"] or not v.get("cloud")]})
        if path == "/api/usage":
            lim, rows = limits(), usage_today()
            if not admin: rows = [r for r in rows if r["user"] == user]      # người dùng thường chỉ thấy số liệu của mình
            cap = lim.get("users", {}).get(user, lim.get("daily_tokens_per_user", 0))
            return self.send({"rows": sorted(rows, key=lambda r: -(r["in"] + r["out"])),
                              "me": {"user": user, "used": user_tokens(user, usage_today()), "cap": cap},
                              "cloud_cost": round(sum(r["cost"] for r in rows), 4), "cloud_cap": lim.get("daily_usd_total", 0) if admin else 0})
        if path == "/api/activity":
            try: since = int(parse_qs(u.query).get("since", ["0"])[0])
            except ValueError: since = 0
            return self.send({"events": [e for e in events if e["seq"] > since][-200:], "last": _seq[0]})
        if path == "/api/activity.csv":
            buf = io.StringIO(); w = csv.writer(buf)
            w.writerow(["thời gian", "ai", "ip", "làm gì", "chi tiết", "kết quả"])
            for e in events: w.writerow([e["ts"], e["user"], e["ip"], e["action"], e["detail"], e["status"]])
            return self.send("\ufeff" + buf.getvalue(), ctype="text/csv; charset=utf-8")
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        if self.bad_host(): return self.send({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 3_000_000: return self.send({"error": "Dữ liệu quá lớn."}, 413)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: body = {}
        if not isinstance(body, dict): body = {}
        u = urlparse(self.path).path; ip = self.client_address[0]
        if u == "/api/shutdown":      # dùng để bản mới thay bản cũ; cần header riêng nên trang web lạ không gọi được
            if self.headers.get("X-Dashboard") != "replace": return self.send({"error": "forbidden"}, 403)
            self.send({"msg": "đang tắt"})
            threading.Thread(target=lambda: (time.sleep(0.3), SERVER and SERVER.shutdown()), daemon=True).start()
            return
        if self.headers.get("X-Requested-With") != "dashboard": return self.send({"error": "forbidden"}, 403)   # trang web lạ không thêm được header này
        if u == "/api/setup":
            with _users_lock:
                if users_db()["users"]: return self.send({"error": "Đã có tài khoản. Hãy đăng nhập."}, 403)
                uid, err = create_user(body.get("username"), body.get("password"), "admin", True)
            if err: return self.send({"error": err}, 400)
            act(str(body.get("username")).strip(), ip, "tạo tài khoản quản trị đầu tiên", "", "ok")
            return self.send({"ok": True}, headers={"Set-Cookie": cookie_header(new_session(uid))})
        if u == "/api/login":
            tok, err, code, why = login(body.get("username"), body.get("password"))
            name = str(body.get("username") or "").strip()[:40]
            if err:
                act("(chưa đăng nhập)", ip, "đăng nhập thất bại", f"tên: {name} · {why}", err); return self.send({"error": err}, code)
            act(name, ip, "đăng nhập", "", "ok")
            return self.send({"ok": True}, headers={"Set-Cookie": cookie_header(tok)})
        sess = self.auth()
        if not sess: return self.send({"error": "unauthorized"}, 401)
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token") or "", sess["csrf"]):
            return self.send({"error": "Phiên không hợp lệ. Hãy tải lại trang."}, 403)
        if u in POST_ADMIN and sess["role"] != "admin": return self.send({"error": "Cần quyền quản trị."}, 403)
        user = sess["username"]
        if u == "/api/logout":
            sessions.pop(sess["token"], None); act(user, ip, "đăng xuất", "", "ok")
            return self.send({"ok": True}, headers={"Set-Cookie": cookie_header("", 0)})
        if u == "/api/password":
            key = user.lower()
            left = lock_left(key)
            if left: return self.send({"error": f"Sai quá nhiều lần. Thử lại sau {left} giây."}, 429)
            if not verify_password(str(body.get("current", "")), users_db()["users"][sess["uid"]]["password"]):
                note_failure(key); act(user, ip, "đổi mật khẩu", "", "lỗi: sai mật khẩu hiện tại")
                return self.send({"error": "Mật khẩu hiện tại không đúng."}, 400)
            err = password_error(body.get("new"))
            if err: return self.send({"error": err}, 400)
            _failed.pop(key, None); set_password(sess["uid"], body["new"]); drop_sessions(sess["uid"], keep=sess["token"])
            act(user, ip, "đổi mật khẩu", "đã đăng xuất các thiết bị khác", "ok")
            return self.send({"msg": "Đã đổi mật khẩu. Các thiết bị khác đã bị đăng xuất."})
        if u == "/api/history/clear":
            try: os.remove(chat_path(sess["uid"]))
            except OSError: pass
            act(user, ip, "xóa lịch sử chat của mình", "", "ok")
            return self.send({"msg": "Đã xóa lịch sử chat của bạn."})
        if u == "/api/feedback":
            rid, vote = body.get("id"), body.get("vote")
            if vote not in ("up", "down") or not isinstance(rid, int): return self.send({"error": "bad request"}, 400)
            if not own_record(sess["uid"], rid): return self.send({"error": "Không tìm thấy câu hỏi."}, 404)     # chỉ đánh giá được câu của chính mình
            with _lock:
                with open(chat_path(sess["uid"]), "a", encoding="utf-8") as f: f.write(json.dumps({"id": rid, "vote": vote}) + "\n")
                with open(REQ_LOG, "a", encoding="utf-8") as f: f.write(json.dumps({"id": rid, "vote": vote, "user": user}) + "\n")
            act(user, ip, "đánh giá câu trả lời", f"câu #{rid}", "đúng" if vote == "up" else "sai")
            return self.send({"msg": "ok"})
        if u == "/api/chat":
            try: return self.send(chat(body.get("q"), body.get("model"), sess, ip, "en" if body.get("lang") == "en" else "vi"))
            except Exception as e: return self.send({"error": f"Lỗi nội bộ: {e}"})
        if u == "/api/users/create":
            uid, err = create_user(body.get("username"), body.get("password"), body.get("role", "user"), body.get("allow_cloud", False))
            if err: return self.send({"error": err}, 400)
            act(user, ip, "tạo tài khoản", f"{str(body.get('username')).strip()} ({body.get('role', 'user')})", "ok")
            return self.send({"msg": f"Đã tạo tài khoản {str(body.get('username')).strip()}."})
        if u == "/api/users/update":
            err = update_user(sess["uid"], body)
            if err: return self.send({"error": err}, 400)
            act(user, ip, "sửa tài khoản", json.dumps({k: v for k, v in body.items() if k != "id"}, ensure_ascii=False), "ok")
            return self.send({"msg": "Đã cập nhật tài khoản."})
        if u == "/api/users/reset-password":
            uid, pw = str(body.get("id", "")), body.get("password")
            if uid == sess["uid"]: return self.send({"error": "Hãy dùng mục Tài khoản để đổi mật khẩu của chính bạn."}, 400)
            if uid not in users_db()["users"]: return self.send({"error": "Không tìm thấy người dùng."}, 404)
            err = password_error(pw)
            if err: return self.send({"error": err}, 400)
            set_password(uid, pw); drop_sessions(uid)
            act(user, ip, "đặt lại mật khẩu", users_db()["users"][uid]["username"], "ok")
            return self.send({"msg": "Đã đặt lại mật khẩu; người này bị đăng xuất."})
        if u == "/api/users/delete":
            uid = str(body.get("id", "")); name = users_db()["users"].get(uid, {}).get("username", "?")
            err = delete_user(sess["uid"], uid)
            if err: return self.send({"error": err}, 400)
            act(user, ip, "xóa tài khoản", name, "ok")
            return self.send({"msg": f"Đã xóa tài khoản {name} và lịch sử chat của người đó."})
        if u == "/api/service" and body.get("name") in SERVICES:
            nm, action = body["name"], body.get("action")
            if action not in ("start", "stop", "restart"): return self.send({"error": "bad request"}, 400)
            r = service_action(nm, action, bool(body.get("force")))
            if not r.get("needs_confirm"):
                act(user, ip, {"start": "bật dịch vụ", "stop": "tắt dịch vụ", "restart": "khởi động lại dịch vụ"}[action],
                    SERVICES[nm]["label"], r["msg"] + (f" · đã tắt {r['killed']}" if r.get("killed") else ""))
            return self.send(r)
        if u == "/api/run" and body.get("job") in JOBS:
            msg = run_job(body["job"], user, ip)
            act(user, ip, "chạy bài đo", JOBS[body["job"]][0], "đã bắt đầu" if msg == "ok" else "bị từ chối: " + msg)
            return self.send({"msg": msg})
        if u == "/api/stop":
            if job.get("proc"):
                kill(job["proc"]); act(user, ip, "dừng bài đo", job.get("name") or "", "đã dừng")
            return self.send({"msg": "ok"})
        if u == "/api/docs":
            name = os.path.basename(str(body.get("name", "")))
            content = body.get("content", "")
            if not re.fullmatch(DOC_NAME, name, re.I):
                return self.send({"error": "Tên file không hợp lệ; chỉ nhận .txt/.md."}, 400)
            if not isinstance(content, str) or not content.strip():
                return self.send({"error": "Tài liệu trống hoặc không đọc được."}, 400)
            if len(content) > 2_000_000:
                return self.send({"error": "File quá lớn (giới hạn 2 MB)."}, 413)
            with open(os.path.join(DOCS, name), "w", encoding="utf-8") as f: f.write(content)
            act(user, ip, "nạp tài liệu", name, f"ok · {len(content)} ký tự")
            return self.send({"msg": f"Đã nạp {name} ({len(content):,} ký tự)."})
        if u == "/api/docs/delete":
            name = os.path.basename(str(body.get("name", ""))); path = os.path.join(DOCS, name)
            if not re.fullmatch(DOC_NAME, name, re.I) or not os.path.isfile(path):
                return self.send({"error": "Không tìm thấy tài liệu."}, 404)
            os.remove(path); act(user, ip, "xóa tài liệu", name)
            return self.send({"msg": f"Đã xóa {name}."})
        if u == "/api/limits":
            err = save_limits(body)
            if err: return self.send({"error": err}, 400)
            act(user, ip, "đổi hạn mức", f"{body.get('daily_tokens_per_user')} token/người/ngày, {body.get('daily_usd_total')} USD/ngày")
            return self.send({"msg": "Đã lưu hạn mức, áp dụng ngay."})
        if u == "/api/model":
            err = save_model(body)
            if err: return self.send({"error": err}, 400)
            act(user, ip, "lưu model", str(body.get("name")))
            return self.send({"msg": f"Đã lưu model {str(body.get('name')).strip()}."})
        if u == "/api/model/delete":
            m = dict(models()); nm = body.get("name")
            if nm not in m: return self.send({"error": "Không tìm thấy model."}, 404)
            if len(m) <= 1: return self.send({"error": "Phải còn ít nhất một model."}, 400)
            del m[nm]; save_json(MODELS_FILE, m); act(user, ip, "xóa model", nm)
            return self.send({"msg": f"Đã xóa model {nm}."})
        self.send({"error": "bad request"}, 400)

class Server(ThreadingHTTPServer):
    # Trên Windows, SO_REUSEADDR cho phép 2 tiến trình cùng mở một cổng và trình duyệt
    # vẫn vào bản cũ. Tắt nó trên Windows để bản mới phát hiện cổng đã bị chiếm.
    allow_reuse_address = (os.name != "nt")

def bind():
    if port_open(PORT): return None
    try: return Server(("127.0.0.1", PORT), H)   # chỉ nhận kết nối từ chính máy này
    except OSError: return None

def parse_listen_pid(netstat_text, port):
    pids = parse_listen_pids(netstat_text, port)
    return pids[0] if pids else None

def free_port():
    """Nếu cổng đang do một bảng điều khiển cũ giữ thì tắt nó. Không đụng tới chương trình khác."""
    base = f"http://localhost:{PORT}"
    def is_dashboard():
        for path, key in (("/api/ping", "app"), ("/api/status", "services")):
            try:
                with urllib.request.urlopen(base + path, timeout=2) as r:
                    if key in json.loads(r.read()): return True
            except Exception:
                pass
        return False
    if not is_dashboard(): return False
    def freed():
        for _ in range(20):
            if not port_open(PORT): return True
            time.sleep(0.2)
        return False
    try:   # cách nhẹ nhàng (bản mới có sẵn)
        urllib.request.urlopen(urllib.request.Request(base + "/api/shutdown", b"{}",
                               {"X-Dashboard": "replace", "Content-Type": "application/json"}), timeout=3)
    except Exception:
        pass
    if freed(): return True
    if os.name == "nt":   # bản cũ chưa có /api/shutdown: tắt theo tiến trình đang giữ cổng
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        pid = parse_listen_pid(out, PORT)
        if pid: subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
        return freed()
    return False

def main():
    global SERVER
    if "--set-password" in sys.argv:       # khôi phục quyền truy cập khi quên mật khẩu quản trị
        import getpass
        i = sys.argv.index("--set-password")
        name = sys.argv[i + 1] if len(sys.argv) > i + 1 else input("Tên tài khoản: ").strip()
        pw = getpass.getpass("Mật khẩu mới (tối thiểu 8 ký tự): ")
        if pw != getpass.getpass("Nhập lại mật khẩu: "): sys.exit("Hai mật khẩu không khớp.")
        err = cli_set_password(name, pw)
        sys.exit(err or f"Đã đặt mật khẩu cho {name}. Nếu tài khoản chưa có thì đã tạo mới với quyền quản trị.")
    srv = bind()
    if srv is None and free_port():
        print("Đã tắt bảng điều khiển cũ đang chạy, khởi động bản mới...")
        srv = bind()
    if srv is None:
        sys.exit(f"Cổng {PORT} đang bị chương trình khác giữ và không tự tắt được. Hãy đóng nó rồi chạy lại.")
    SERVER = srv
    models(); limits()   # tạo models.json và limits.json mặc định nếu chưa có
    moved = migrate_legacy()
    if moved: print(f"Đã chuyển {moved} bản ghi chat cũ sang logs/legacy_chat_history.jsonl (log chung chỉ giữ số liệu).")
    if not users_db()["users"]: print("Chưa có tài khoản: mở trang web để tạo tài khoản quản trị đầu tiên.")
    act("hệ thống", "-", "khởi động", f"Bảng điều khiển bật (build {BUILD})")
    url = f"http://localhost:{PORT}"
    print(f"Bảng điều khiển: {url}   build {BUILD}   (Ctrl+C để thoát, dịch vụ do bảng bật sẽ tự tắt)")
    print(f"Đang chạy từ: {os.path.abspath(__file__)}")
    if "--no-browser" not in sys.argv: webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__": main()
