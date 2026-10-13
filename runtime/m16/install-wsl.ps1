$ErrorActionPreference = 'Continue'
& wsl.exe --install --no-distribution *> 'C:\Users\Jonat\algoalpha\runtime\m16\install-wsl.log'
$code = $LASTEXITCODE
Set-Content -LiteralPath 'C:\Users\Jonat\algoalpha\runtime\m16\install-wsl.exit' -Value $code
exit $code
