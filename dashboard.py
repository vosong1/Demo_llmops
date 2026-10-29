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
from settings import GW_KEY, GW_HOST, GW_PORT, GW_URL, OLLAMA_PORT, OLLAMA_URL, PHOENIX_PORT, PHOENIX_URL, PORT

HERE = os.path.dirname(os.path.abspath(__file__))          # mã nguồn, web/, prompt.txt, litellm_config.yaml
DATA = settings.DATA_DIR                                    # dữ liệu chạy: users, chats, logs, docs, models.json, limits.json
LOGS = os.path.join(DATA, "logs"); os.makedirs(LOGS, exist_ok=True)
BUILD = "2026-10-29-v4.4"   # hiện ở đầu trang: thấy dòng này nghĩa là đang chạy đúng bản mới
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
    "ollama":  {"label": "Ollama", "note": "chạy model 3B, 7B", "port": OLLAMA_PORT, "url": OLLAMA_URL,
                "cmd": lambda: [ollama_exe(), "serve"]},
    "litellm": {"label": "LiteLLM", "note": "gateway: key, giới hạn, fallback", "port": GW_PORT, "url": GW_URL,
                "cmd": lambda: [sys.executable, "-m", "litellm.proxy.proxy_cli",
                                "--config", "litellm_config.yaml", "--host", GW_HOST, "--port", str(GW_PORT)]},
    "phoenix": {"label": "Phoenix", "note": f"xem trace tại localhost:{PHOENIX_PORT}", "port": PHOENIX_PORT, "url": PHOENIX_URL,
                "cmd": lambda: [sys.executable, "-m", "phoenix.server.main", "serve"]},
}
procs = {}

def port_open(p, host="127.0.0.1"):
    try:
        with socket.create_connection((host, p), timeout=0.4): return True
    except OSError:
        return False

def url_up(url):
    """Dịch vụ có đang nhận kết nối không (đọc host:cổng từ URL, dùng được cho dịch vụ ở container/máy khác)."""
    u = urlparse(url)
    return port_open(u.port or (443 if u.scheme == "https" else 80), u.hostname or "127.0.0.1")

def is_local(url):
    return (urlparse(url).hostname or "localhost") in ("localhost", "127.0.0.1", "::1")

def kill(proc):
    if proc.poll() is not None: return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()

def start_service(name):
    s = SERVICES[name]
    if url_up(s["url"]): return "Đã chạy sẵn."
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
    if not url_up(s["url"]): return {"msg": "Dịch vụ đang tắt."}
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

EXTERNAL_MSG = "Dịch vụ này do hạ tầng bên ngoài quản lý (Docker/systemd/máy khác), bảng điều khiển không bật/tắt được. Hãy dùng docker compose hoặc systemctl."

def service_action(name, action, force=False):
    if not (settings.MANAGE_SERVICES and is_local(SERVICES[name]["url"])): return {"msg": EXTERNAL_MSG}
    if action == "start": return {"msg": start_service(name)}
    if action == "stop": return stop_service(name, force)
    if url_up(SERVICES[name]["url"]):               # restart: tắt rồi bật lại để nạp lại cấu hình
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
    "trace":   ("Gửi trace sang Phoenix", lambda: [sys.executable, "trace_phoenix.py"] + (["--gateway"] if url_up(GW_URL) else [])),
    "eval":    ("Chấm eval bằng promptfoo", eval_cmd),
    "all":     ("Chạy tất cả", lambda: [sys.executable, "main.py", "--all"]),
}
job = {"name": None, "running": False, "log": [], "code": None, "proc": None}

def run_job(key, user="ẩn danh", ip="-"):
    if job["running"]: return "Đang có tác vụ chạy, hãy chờ hoặc bấm Dừng."
    if not url_up(OLLAMA_URL): return "Ollama chưa chạy. Bấm Bật ở dòng Ollama trước."
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
DOCS = os.path.join(DATA, "docs"); os.makedirs(DOCS, exist_ok=True)
REQ_LOG = os.path.join(LOGS, "requests.jsonl")
MODELS_FILE = os.path.join(DATA, "models.json")     # model, alias LiteLLM, giá (USD / 1 triệu token)
LIMITS_FILE = os.path.join(DATA, "limits.json")     # hạn mức token mỗi ngày
DEFAULT_MODELS = {"qwen2.5:3b": {"alias": "qwen-3b", "cloud": False, "in_per_m": 0, "out_per_m": 0},
                  "qwen2.5:7b": {"alias": "qwen-7b", "cloud": False, "in_per_m": 0, "out_per_m": 0}}
DEFAULT_LIMITS = {"daily_tokens_per_user": 50000, "daily_usd_total": 1.0, "users": {}}

def load_json(path, default):
    if not os.path.exists(path):
        seed = os.path.join(HERE, os.path.basename(path))      # models.json / limits.json mẫu đi kèm repo
        if os.path.abspath(seed) != os.path.abspath(path) and os.path.exists(seed):
            try:
                with open(seed, encoding="utf-8") as f: default = json.load(f)
            except ValueError:
                pass
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

def load_knowledge():
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
    if _tracing["on"] or not url_up(PHOENIX_URL): return
    try:
        from phoenix.otel import register
        register(project_name="llmops-chat", endpoint=f"{PHOENIX_URL}/v1/traces", auto_instrument=True)
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
    base = GW_URL if use_gw else OLLAMA_URL
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
            if not url_up(GW_URL): return {"error": "Model đám mây cần LiteLLM đang chạy (khóa API nằm ở LiteLLM). Bấm Bật LiteLLM."}
        elif not url_up(OLLAMA_URL): return {"error": "Ollama chưa chạy. Bấm Bật ở dòng Ollama."}
        set_chat_status(user, "retrieval", "Đang tìm các đoạn tài liệu liên quan trong Knowledge Base…")
        chunks = retrieve_chunks(q)
        ctx = "\n\n".join(f"[Nguồn: {c['source']}]\n{c['text']}" for c in chunks)
        if not ctx:
            ctx = "Không tìm thấy đoạn tài liệu phù hợp. Hãy nói rõ rằng chưa có bằng chứng trong kho tài liệu; không tự bịa chính sách hay trích dẫn."
        prompt = open(os.path.join(HERE, "prompt.txt"), encoding="utf-8").read() \
            .replace("{{context}}", ctx).replace("{{question}}", q)
        if lang == "en":      # giao diện tiếng Anh: yêu cầu trả lời bằng tiếng Anh
            prompt += "\n\nIMPORTANT: Write the answer in English. If the documents contain no relevant information, reply exactly: \"No relevant information found in the documents.\""
        use_gw = url_up(GW_URL)
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
        with urllib.request.urlopen(OLLAMA_URL + path, timeout=timeout) as r: return json.loads(r.read())
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
        return k, {"label": sv["label"], "note": sv["note"], "port": sv["port"], "up": url_up(sv["url"])}
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

# ================= Giao diện web (HTML/JS/từ điển nằm trong thư mục web/) =================
# web/page.html (trang chính), web/login.html (đăng nhập/tạo admin), web/i18n_runtime.js + web/i18n_en.json (Việt/Anh).
# Thêm bản dịch mới: bổ sung vào web/i18n_en.json. Chuỗi chưa có bản dịch được giữ nguyên tiếng Việt.
WEB = os.path.join(HERE, "web")

def _web(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f: return f.read()

I18N_EN = json.loads(_web("i18n_en.json"))
I18N_RUNTIME = _web("i18n_runtime.js")
I18N_JS = I18N_RUNTIME.replace("__DICT__", json.dumps(I18N_EN, ensure_ascii=False))
AUTH_PAGE = _web("login.html")
PAGE = _web("page.html")


# ================= tài khoản, phiên đăng nhập, phân quyền =================
USERS_FILE = os.path.join(DATA, "users.json")
CHATS = os.path.join(DATA, "chats"); os.makedirs(CHATS, exist_ok=True)
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
    return f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}" + ("; Secure" if settings.COOKIE_SECURE else "")

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

def host_only(h):
    """Tách tên máy khỏi header Host: 'a.com:8765' -> 'a.com', '[::1]:8765' -> '::1'."""
    h = (h or "").strip().lower()
    return h[1:h.index("]")] if h.startswith("[") and "]" in h else h.split(":")[0]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200, ctype="application/json; charset=utf-8", headers=None):
        body = obj.encode("utf-8") if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def bad_host(self):   # chặn DNS-rebinding: chỉ nhận Host nằm trong ALLOWED_HOSTS (mặc định chỉ localhost)
        return host_only(self.headers.get("Host")) not in settings.ALLOWED_HOSTS

    def client_ip(self):  # sau reverse proxy: lấy IP do proxy ghi ở cuối X-Forwarded-For (chỉ khi TRUST_PROXY=1)
        if settings.TRUST_PROXY:
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            if xff: return xff[:64]
        return self.client_address[0]

    def auth(self):
        try: c = SimpleCookie(self.headers.get("Cookie") or "")
        except Exception: return None
        m = c.get(COOKIE)
        return get_session(m.value) if m else None

    def do_GET(self):
        if self.bad_host(): return self.send({"error": "forbidden"}, 403)
        u = urlparse(self.path); path = u.path
        if path == "/healthz":       # liveness: không cần đăng nhập, không lộ dữ liệu
            return self.send({"status": "ok", "build": BUILD})
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
        u = urlparse(self.path).path; ip = self.client_ip()
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
    try: return Server((settings.HOST, PORT), H)   # mặc định 127.0.0.1: chỉ nhận kết nối từ chính máy này
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
    loopback = settings.HOST in ("127.0.0.1", "localhost", "::1")
    if not loopback:
        print(f"CẢNH BÁO: đang lắng nghe trên {settings.HOST}:{PORT}. Chỉ làm vậy trong Docker/mạng riêng hoặc sau reverse proxy có TLS (xem DEPLOY.md).")
    print(f"Bảng điều khiển: {url}   build {BUILD}   (Ctrl+C để thoát, dịch vụ do bảng bật sẽ tự tắt)")
    print(f"Đang chạy từ: {os.path.abspath(__file__)}")
    if loopback and not settings.NO_BROWSER and "--no-browser" not in sys.argv: webbrowser.open(url)
    def on_term(*_): raise KeyboardInterrupt      # docker stop / systemctl stop: thoát êm, tắt các dịch vụ con
    signal.signal(signal.SIGTERM, on_term)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: srv.server_close()

if __name__ == "__main__": main()
