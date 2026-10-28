"""Gửi trace sang Phoenix. Cần: phoenix serve đang chạy + pip install openai arize-phoenix openinference-instrumentation-openai"""
import json, sys
import settings
from phoenix.otel import register
register(project_name="llmops-demo", endpoint=f"http://localhost:{settings.PHOENIX_PORT}/v1/traces", auto_instrument=True)
from openai import OpenAI  # import sau register để được instrument

if "--gateway" in sys.argv:   # đi qua LiteLLM: ứng dụng -> LiteLLM -> Ollama
    MODEL, client = "qwen-3b", OpenAI(base_url=f"http://localhost:{settings.GW_PORT}/v1", api_key=settings.GW_KEY)
else:                         # gọi thẳng Ollama
    MODEL, client = "qwen2.5:3b", OpenAI(base_url=f"http://localhost:{settings.OLLAMA_PORT}/v1", api_key="ollama")
ctx = open("context.txt", encoding="utf-8").read()
tpl = open("prompt.txt", encoding="utf-8").read()
for q in json.load(open("questions.json", encoding="utf-8")):
    p = tpl.replace("{{context}}", ctx).replace("{{question}}", q["question"])
    r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=256,
                                       messages=[{"role": "user", "content": p}])
    print(q["id"], r.choices[0].message.content.strip()[:100])
print(f"Xong. Mở http://localhost:{settings.PHOENIX_PORT} để xem trace.")
