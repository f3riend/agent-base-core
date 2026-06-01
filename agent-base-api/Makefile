run:
	@find app -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	uv run uvicorn app.main:app --reload

redis:
	@if command -v redis-cli >/dev/null 2>&1 && redis-cli -p 6379 ping >/dev/null 2>&1; then \
		echo "Redis zaten calisiyor (port 6379)."; \
	elif command -v ss >/dev/null 2>&1 && ss -ltn '( sport = :6379 )' | rg -q 6379; then \
		echo "Redis/Valkey port 6379 dinleniyor."; \
	else \
		redis-server; \
	fi

worker:
	uv run python -m celery -A app.core.celery_app:celery_app worker --pool=solo --loglevel=info

flower:
	uv run python -m celery -A app.core.celery_app:celery_app flower --port=5555

clean:
	@find app -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	uv run python developer-tools.py --clean


close:
	lsof -t -i :8000 | xargs -r kill -9