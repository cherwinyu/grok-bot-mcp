param(
    [string]$Python,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'grokbot-mcp\onboarding-venv'),
    [string]$Target,
    [string]$Registry,
    [string]$Workspace,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
# Bootstrap only. The Python wizard owns the single registration/configuration system.
try {
    # Reject read/write mode conflicts before interpreter discovery or helper dispatch.
    if ($Check -and $PSBoundParameters.ContainsKey('Target')) {
        Write-Host 'invalid_arguments: -Check cannot be combined with -Target. No credential helper was started.'
        exit 1
    }
    if ($env:OS -ne 'Windows_NT') {
        Write-Host 'windows_required: Run this installer in Windows PowerShell.'
        exit 1
    }
    if (-not $Python) {
        $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) {
            $Python = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE) { $Python = $null }
        }
        if (-not $Python) {
            $candidate = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
            if ($candidate) { $Python = $candidate.Source }
        }
    }
    if (-not $Python -or -not [IO.Path]::IsPathRooted($Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-Host 'python_interpreter_invalid: Install Python 3.12, reopen PowerShell, then use -Python with its absolute python.exe path.'
        exit 1
    }
    & $Python -I -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'
    if ($LASTEXITCODE) {
        Write-Host 'python_unsupported: Python 3.12 or newer is required. Recommended tested version: 3.12.'
        exit 1
    }
    # Preserve the original credential helper interface; no new registry or installation.
    if ($Target) {
        & $Python -m grokbot_mcp.init_credentials --target $Target
        exit $LASTEXITCODE
    }
    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        Write-Host 'git_missing: Install Git for Windows, reopen PowerShell and run git --version, then rerun setup.ps1.'
        exit 1
    }
    & git --version
    if ($LASTEXITCODE) {
        Write-Host 'git_unavailable: Repair Git on PATH; no installation was changed.'
        exit 1
    }
    if (-not [IO.Path]::IsPathRooted($InstallDir)) {
        Write-Host 'installation_unavailable: -InstallDir must be an absolute new or previously created venv directory.'
        exit 1
    }
    $source = Split-Path -Parent $PSScriptRoot
    $venvPython = Join-Path $InstallDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $InstallDir) {
            Write-Host 'installation_unavailable: Existing directory is not a recognized venv. Select a new -InstallDir; nothing was replaced.'
            exit 1
        }
        if ($Check) {
            Write-Host 'installation_unavailable: --check does not create an installation. Run setup without -Check first.'
            exit 1
        }
        & $Python -m venv $InstallDir
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
    }
    & $venvPython -I -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('grokbot_mcp') else 1)"
    if ($LASTEXITCODE) {
        if ($Check) {
            Write-Host 'installation_unavailable: The venv has no bridge package. Run setup without -Check to resume installation.'
            exit 1
        }
        & $venvPython -m pip install --no-input $source
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
    } else {
        Write-Host 'Existing bridge installation preserved. No pip upgrade or replacement.'
    }
    & $venvPython -I -c "import importlib.metadata as m,sys,tomllib; wanted=tomllib.load(open(sys.argv[1],'rb'))['project']['version']; sys.exit(0 if m.version('grokbot-mcp-bridge') == wanted else 1)" (Join-Path $source 'pyproject.toml')
    if ($LASTEXITCODE) {
        Write-Host 'installation_mismatch: This venv does not contain the checked-out candidate version. Select a NEW -InstallDir; the existing installation was not upgraded.'
        exit 1
    }
    $wizardArgs = @('-I', '-X', 'utf8', '-m', 'grokbot_mcp.setup_wizard')
    if ($Registry) { $wizardArgs += @('--registry', $Registry) }
    if ($Workspace) { $wizardArgs += @('--workspace', $Workspace) }
    if ($Check) { $wizardArgs += '--check' }
    & $venvPython @wizardArgs
    exit $LASTEXITCODE
} catch {
    # PowerShell exception text can contain paths/usernames; do not emit it.
    Write-Host 'setup_failed: Check Python/Git and local directory permissions. No raw exception details were logged.'
    exit 1
}
