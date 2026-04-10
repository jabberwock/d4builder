# d4builder — common task entrypoints
#
# Usage: make <target>

.PHONY: help verify install-hooks fetch-maxroll rebuild-passives rebuild-coefficients

help:
	@echo "Available targets:"
	@echo "  make verify              Run data regression suite (data/verify_data.py)"
	@echo "  make install-hooks       Install git pre-commit hook"
	@echo "  make fetch-maxroll       Fetch latest maxroll game data JSON"
	@echo "  make rebuild-passives    Re-extract passive_table.json from maxroll"
	@echo "  make rebuild-coefficients Re-extract d4data_coefficients.json + cooldowns"

verify:
	@python3 data/verify_data.py

install-hooks:
	@bash scripts/install-hooks.sh

fetch-maxroll:
	@bash data/fetch_maxroll.sh

rebuild-passives:
	@cd data && python3 build_passive_table.py

rebuild-coefficients:
	@cd data && python3 extract_coefficients_d4data.py
	@cd data && python3 extract_cooldowns_d4data.py
