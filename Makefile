PYTHON ?= .venv/bin/python

.PHONY: setup verify test demo print-prep-demo serve replay-synthetic

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

print-prep-demo: demo
	$(PYTHON) print_prep.py --left DEMO-0001_left.stl --right DEMO-0001_right.stl \
		--order-id DEMO-0001 --out outputs/DEMO-0001_print_pair.stl

serve:
	$(PYTHON) app.py

replay-synthetic:
	MPLCONFIGDIR=/tmp/orthopipe-mpl-cache $(PYTHON) replay.py --synthetic 5 --out outputs/replay
