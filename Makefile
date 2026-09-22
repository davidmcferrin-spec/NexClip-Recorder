# Makefile — NexCLIP Recorder
# No Docker, no Node, no Composer, no pip (Nex* convention).

PYTHON ?= python3
PHP    ?= php
TESTDIR := test

# Local PostgreSQL used by unit tests and the demo. Override to point elsewhere.
NEXREC_PGHOST ?= 127.0.0.1
NEXREC_PGPORT ?= 5432
NEXREC_PGUSER ?= nexrec_test
NEXREC_PGPASSWORD ?= nexrec_test
NEXREC_PGDATABASE ?= nexrec_test
export NEXREC_PGHOST NEXREC_PGPORT NEXREC_PGUSER NEXREC_PGPASSWORD NEXREC_PGDATABASE

.PHONY: help test test-db test-py test-php demo lint version

help:
	@echo "make test     — unit tests (Python + PHP; local PostgreSQL database nexrec_test)"
	@echo "make demo     — IP-style ingest → 5s MP4 chunks → trim export"
	@echo "make version  — print VERSION"

version:
	@cat VERSION

test: test-db test-py test-php

test-db:
	./bin/nexrec-test-db.sh

test-py:
	PYTHONPATH=worker $(PYTHON) -m unittest discover -s $(TESTDIR) -p 'test_*.py' -v

test-php:
	$(PHP) $(TESTDIR)/test_nexrec_auth.php
	$(PHP) $(TESTDIR)/test_nexrec_web_router.php
	$(PHP) $(TESTDIR)/test_nexrec_nexapp.php
	$(PHP) $(TESTDIR)/test_nexrec_features.php
	$(PHP) $(TESTDIR)/test_nexrec_ops_allowlist.php
	$(PHP) $(TESTDIR)/test_nexrec_settings.php
	$(PHP) $(TESTDIR)/test_nexrec_decklink.php

demo: test-db
	./test/test_demo_pipeline.sh
