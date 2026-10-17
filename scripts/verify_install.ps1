$ErrorActionPreference = "Stop"
python -c "import grokbot_mcp; assert grokbot_mcp.__version__ == '0.1.3'"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m unittest discover -s tests -v
exit $LASTEXITCODE
