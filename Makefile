PYTHON ?= .venv/bin/python

.PHONY: setup verify test demo serve replay-synthetic

setup:
	python3.11 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

test:
	MPLCONFIGDIR=/tmp/orthopipe-mpl-cache $(PYTHON) -m unittest discover -s tests -v

verify: test
	$(PYTHON) -m eval.score_rx --selfcheck
	$(PYTHON) -m eval.score_rx --offline
	MPLCONFIGDIR=/tmp/orthopipe-mpl-cache $(PYTHON) replay.py --selftest

demo:
	MPLCONFIGDIR=/tmp/orthopipe-mpl-cache $(PYTHON) run_demo.py

serve:
	$(PYTHON) app.py

replay-synthetic:
	MPLCONFIGDIR=/tmp/orthopipe-mpl-cache $(PYTHON) replay.py --synthetic 5 --out outputs/replay
