# Lệnh tắt cho Linux/macOS (Windows dùng start.bat hoặc PowerShell, xem README.md / DEPLOY.md).
.PHONY: help init-env venv run test lint build up up-gpu up-phoenix down logs pull-models backup

help:
	@echo "make init-env     tạo .env và sinh LITELLM_MASTER_KEY"
	@echo "make venv         tạo .venv và cài thư viện chạy local + dev"
	@echo "make run          chạy dashboard (không Docker)"
	@echo "make test / lint  chạy kiểm thử / ruff"
	@echo "make up           Docker Compose: build + chạy dashboard, Ollama, LiteLLM"
	@echo "make up-gpu       như trên nhưng có GPU NVIDIA"
	@echo "make up-phoenix   như trên và thêm Phoenix"
	@echo "make pull-models  tải model Ollama (PULL_MODELS trong .env)"
	@echo "make logs / down  xem log / dừng"
	@echo "make backup       nén dữ liệu (users, chats, logs, docs) ra llmops-data-YYYYMMDD.tgz"

init-env:
	python3 scripts/init_env.py

venv:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

run:
	python3 dashboard.py

test:
	python3 -m pytest -q

lint:
	ruff check .

build:
	docker compose build

up: init-env
	docker compose up -d --build

up-gpu: init-env
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

up-phoenix: init-env
	docker compose --profile observability up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

pull-models:
	docker compose run --rm ollama-pull

backup:
	docker run --rm -v llmops-data:/data:ro -v "$(CURDIR)":/backup alpine \
		tar czf /backup/llmops-data-$$(date +%Y%m%d).tgz -C /data .
