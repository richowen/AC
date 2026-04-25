.PHONY: dev build run lint

dev:
	pip install -r requirements.txt
	HA_URL=http://192.168.1.3:8123 HA_TOKEN=$${HA_TOKEN} \
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

lint:
	ruff check app/

build:
	docker build -t ac-controller:local .

run:
	docker run --rm \
		-e HA_URL=http://192.168.1.3:8123 \
		-e HA_TOKEN=$${HA_TOKEN} \
		-e TZ=Europe/London \
		-v $$(pwd)/data:/data \
		-p 8765:8000 \
		ac-controller:local
