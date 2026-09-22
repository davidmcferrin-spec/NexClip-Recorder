# Makefile — NexCLIP Recorder
# No Docker, no Node, no Composer, no pip (Nex* convention).

PYTHON ?= python3
PHP    ?= php
TESTDIR := test

.PHONY: help test test-py test-php demo lint version

help:
	@echo "make test     — unit tests (Python + PHP)"
	@echo "make demo     — IP-style ingest → 5s MP4 chunks → trim export"
	@echo "make version  — print VERSION"

version:
	@cat VERSION

test: test-py test-php

test-py:
	PYTHONPATH=worker $(PYTHON) -m unittest discover -s $(TESTDIR) -p 'test_*.py' -v

test-php:
	$(PHP) $(TESTDIR)/test_nexrec_auth.php
	$(PHP) $(TESTDIR)/test_nexrec_web_router.php
	$(PHP) $(TESTDIR)/test_nexrec_nexapp.php
	$(PHP) $(TESTDIR)/test_nexrec_features.php
	$(PHP) $(TESTDIR)/test_nexrec_ops_allowlist.php
	$(PHP) $(TESTDIR)/test_nexrec_settings.php

demo:
	./test/test_demo_pipeline.sh
