.DEFAULT_GOAL := help

.PHONY: help run worker redis flower clean close dev build \
	docker-build docker-up up-prod docker-down docker-reset docker-rm-conflicts docker-logs docker-ps docker-restart docker-test docker-test-quick \
	docker-build-server docker-up-server \
	_docker-test-wait-pillow

help:
	@echo "agent-base — kök Makefile"
	@echo ""
	@echo "Yerel geliştirme:"
	@echo "  make run          API: uvicorn --reload (agent-base-api)"
	@echo "  make dev          Arayüz: PHP yerel sunucu (php-ui; API ayri :8000)"
	@echo "  make build        (Istege bagli) Eski React: npm run build — Docker arayüzü PHP kullanir"
	@echo "  make redis        Redis 6379 (yoksa başlatır)"
	@echo "  make worker       Celery worker (api; --pool=solo)"
	@echo "  make flower       Celery Flower → http://127.0.0.1:5555"
	@echo "  make clean        API __pycache__ + uv clean"
	@echo "  make close        :8000 dinleyen süreçleri sonlandır"
	@echo ""
	@echo "Docker (all-in-one + Redis + MySQL, kök docker-compose.yml):"
	@echo "  (Kök .env VITE_* build-arg olarak gider; imajda kök .env yok — .dockerignore)"
	@echo "  make docker-build       docker compose build"
	@echo "  make docker-up          docker compose up -d --build"
	@echo "  make up-prod            Host MySQL + host bind mount (uses docker-compose.prod.yml)"
	@echo "  make docker-down        docker compose down (volume'ler kalir)"
	@echo "  make docker-reset       down -v + up --build (MySQL/medya volume SIFIRLANIR)"
	@echo "  make docker-logs        all-in-one konteyner logları (-f)"
	@echo "  make docker-ps          docker compose ps"
	@echo "  make docker-restart     agent-base-allinone yeniden başlat"
	@echo "  make docker-test        imaj derle + up; /api/health + Pillow (uzun)"
	@echo "  make docker-test-quick  isim cakisan konteynerleri sil + up + smoke (agent-base-mysql vb.)"
	@echo "  make docker-rm-conflicts  agent-base-mysql|redis|allinone adli konteynerleri zorla sil (elle docker run sonrasi)"
	@echo "  make docker-build-server  sunucu icin build (WEB_PORT=80 varsayilan)"
	@echo "  make docker-up-server     sunucu icin up -d --build (WEB_PORT=80 varsayilan)"

run:
	$(MAKE) -C agent-base-api run

worker:
	$(MAKE) -C agent-base-api worker

redis:
	$(MAKE) -C agent-base-api redis

flower:
	$(MAKE) -C agent-base-api flower

clean:
	$(MAKE) -C agent-base-api clean

close:
	$(MAKE) -C agent-base-api close

dev:
	@echo "PHP: http://127.0.0.1:8099 — API ayri: make run (:8000). JS dogrudan :8000 cagirir (CORS *)."
	cd php-ui/public && APP_BROWSER_API_BASE=http://127.0.0.1:8000 APP_INTERNAL_API_URL=http://127.0.0.1:8000 php -S 127.0.0.1:8099 router.php

build:
	npm run build

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

# Prod stack: host MySQL via host.docker.internal + host bind mount for media.
# Uses docker-compose.prod.yml explicitly so it works even if override.yml
# was deleted (e.g. by git clean). Requires DOCKER_DATABASE_URL in root .env.
up-prod:
	mkdir -p /home/agentbase/agentbase/media
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Sunucu/prod kolayligi: host portunu varsayilan 80'e ceker.
docker-build-server:
	WEB_PORT=$${WEB_PORT:-80} docker compose build

docker-up-server:
	WEB_PORT=$${WEB_PORT:-80} docker compose up -d --build

docker-down:
	docker compose down

# Ayni container_name baska bir compose veya "docker run" ile alinmissa `docker compose up` patlar; volume genelde compose projede kalir.
docker-rm-conflicts:
	-docker rm -f agent-base-mysql agent-base-redis agent-base-allinone 2>/dev/null

# Konteyner + compose volume'leri (agent_base_mysql, agent_base_media) siler; DB ve yuklenen dosyalar gider.
docker-reset:
	docker compose down -v --remove-orphans
	@$(MAKE) --no-print-directory docker-rm-conflicts
	docker compose up -d --build

docker-logs:
	docker compose logs -f agent-base-allinone

docker-ps:
	docker compose ps

docker-restart:
	docker compose restart agent-base-allinone

# Yerel dogrulama: nginx -> /api/health + konteyner icinde PIL
docker-test-quick: docker-rm-conflicts
	docker compose up -d
	@$(MAKE) --no-print-directory _docker-test-wait-pillow

docker-test: docker-build docker-rm-conflicts
	docker compose up -d
	@$(MAKE) --no-print-directory _docker-test-wait-pillow

_docker-test-wait-pillow:
	@port=$${WEB_PORT:-8080}; \
	echo "Bekleniyor: http://127.0.0.1:$$port/api/health (MySQL ilk acilista ~1 dk surer) ..."; \
	sleep 5; \
	for i in $$(seq 1 40); do \
	  if curl -fsS "http://127.0.0.1:$$port/api/health" >/dev/null 2>&1; then echo "OK /api/health"; break; fi; \
	  sleep 3; \
	  if [ "$$i" = 40 ]; then echo "HATA: /api/health yanit vermedi"; docker compose ps; docker compose logs --tail=100 agent-base-allinone; exit 1; fi; \
	done
	docker compose exec -T agent-base-allinone /opt/api/.venv/bin/python -c "from PIL import Image; print('OK Pillow', Image.__version__)"
	@echo "Docker smoke test tamam."
