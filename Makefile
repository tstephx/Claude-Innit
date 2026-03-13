.PHONY: install test check server

install:  ## Full install with embeddings + dev deps
	.venv/bin/pip install -e ".[embeddings,dev]"

test: check  ## Run test suite (checks deps first)
	.venv/bin/python -m pytest tests/ -v

check:  ## Verify critical deps are installed
	@.venv/bin/python -c "import sentence_transformers" 2>/dev/null \
		|| (echo "ERROR: Missing embeddings deps. Run: make install" && exit 1)
	@.venv/bin/python -c "import pytest" 2>/dev/null \
		|| (echo "ERROR: Missing dev deps. Run: make install" && exit 1)
	@.venv/bin/python -c "import hypothesis" 2>/dev/null \
		|| (echo "ERROR: Missing hypothesis. Run: make install" && exit 1)

server:  ## Run the MCP server
	.venv/bin/python -m claude_innit.server
