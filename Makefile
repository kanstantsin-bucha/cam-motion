.PHONY: start stop restart reload logs status shell config-check

start:
	docker compose up -d

stop:
	docker compose down

restart:
	docker compose restart frigate

reload:
	docker compose down && docker compose up -d

logs:
	docker compose logs -f frigate

status:
	docker compose ps

shell:
	docker compose exec frigate /bin/bash

config-check:
	docker compose exec frigate python3 -c "import yaml; yaml.safe_load(open('/config/config.yml'))" && echo "Config OK"
