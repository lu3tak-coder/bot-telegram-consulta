$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

# Usar Python da instalação principal (Python 3.14)
$py = "C:\Users\slowx86\AppData\Local\Programs\Python\Python314\python.exe"
if (-not (Test-Path $py)) {
    $py = "python"
}

Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "     INICIANDO BOT TELEGRAM " -ForegroundColor Cyan
Write-Host "  Python: $py" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Mata processos antigos
Write-Host "[0/1] Limpando processos antigos..." -ForegroundColor Yellow
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    try { (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "bot\.py" } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "       OK" -ForegroundColor Green

# Inicia Bot do Telegram
Write-Host "[1/1] Iniciando Bot do Telegram..." -ForegroundColor Yellow
$botProc = Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "bot.py" -PassThru
Start-Sleep -Seconds 3
Write-Host "       Bot rodando polling (PID: $($botProc.Id))" -ForegroundColor Green

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "        BOT EM EXECUCAO " -ForegroundColor Cyan
Write-Host "  Pressione Q para parar o bot" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan

# Aguarda Q
do {
    $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
} while ($key.Character -ne 'q' -and $key.Character -ne 'Q')

Write-Host ""
Write-Host "Parando bot..." -ForegroundColor Red
Stop-Process -Id $botProc.Id -Force -ErrorAction SilentlyContinue
Write-Host "Bot finalizado com sucesso." -ForegroundColor Green
