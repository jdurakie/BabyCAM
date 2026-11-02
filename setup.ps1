$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if (-not $uv) {
    [Console]::Error.WriteLine('uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/')
    exit 1
}

Push-Location -LiteralPath $PSScriptRoot
try {
    & $uv.Source sync --locked
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
