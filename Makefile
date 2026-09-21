.PHONY: test seed dbt-run dbt-test dbt-docs refresh

VENV := .venv/bin

test:
	$(VENV)/pytest -v

seed:
	$(VENV)/python scripts/seed_fixture.py

dbt-run:
	cd transform && ../$(VENV)/dbt run --profiles-dir .

dbt-test:
	cd transform && ../$(VENV)/dbt test --profiles-dir .

dbt-docs:
	cd transform && ../$(VENV)/dbt docs generate --profiles-dir .

# One reproducible command: fresh fixture data -> build the warehouse -> test it.
refresh: seed dbt-run dbt-test
