PY=.venv/bin/python
.PHONY: start install demo fetch serve test bench
start:
	python3 server.py
install:
	uv venv -p 3.12 .venv && uv pip install --python $(PY) -e '.[dev]' python-multipart
demo:
	.venv/bin/hp demo
fetch:
	.venv/bin/hp fetch
serve:
	.venv/bin/hp serve --port 8080
test:
	$(PY) -m pytest -q
bench:
	.venv/bin/hp bench --regions 5570 --draws 1000
