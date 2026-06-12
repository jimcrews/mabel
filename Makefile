.PHONY: reset process test

reset:
	uv run python reset.py

process:
	uv run python process.py tenant=mable

test:
	uv run pytest test_process.py -v
