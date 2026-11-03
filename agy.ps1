param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$venvPython = Join-Path $PSScriptRoot "project\backend\.venv\Scripts\python.exe"
$py = if (Test-Path $venvPython) { $venvPython } else { "python" }
& $py (Join-Path $PSScriptRoot "antigravity.py") @RemainingArgs
