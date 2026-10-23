$ErrorActionPreference = "Stop"
python -c "import importlib.metadata as m,grokbot_mcp; assert grokbot_mcp.__version__ == m.version('grokbot-mcp-bridge')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m unittest discover -s tests -v
exit $LASTEXITCODE
