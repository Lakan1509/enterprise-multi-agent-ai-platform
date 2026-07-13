install:
	python -m pip install -r requirements.txt

run:
	fastapi dev app/main.py

test:
	pytest -q

docker:
	docker compose up --build
