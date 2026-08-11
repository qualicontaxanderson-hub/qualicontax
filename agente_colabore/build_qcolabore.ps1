# Build do agente Q-Colabore com Nuitka (Windows).
#
# Por que Nuitka e nao PyInstaller: no Q-Robo o PyInstaller gerava um .exe que o
# Windows Defender colocava em quarentena (heuristica de "onefile empacotado").
# O Nuitka compila para C de verdade e passa limpo na maioria das maquinas.
#
# A --file-version NAO e digitada a mao: e derivada do __version__ do proprio
# fonte, para a proxima versao nao depender de alguem lembrar de trocar aqui
# (mesma licao do build.yml do Q-Robo).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\build_qcolabore.ps1
#
# Pre-requisitos (uma vez):
#   pip install nuitka requests
#   Um compilador C: o Nuitka baixa o MinGW64 sozinho na 1a vez (responde 'yes'),
#   ou use o MSVC (Build Tools do Visual Studio) se ja tiver.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$fonte = Join-Path $raiz "qcolabore_agente.py"
$saida = Join-Path $raiz "build"
$destinoInstalacao = "C:\qcolabore"

# --- versao derivada do __version__ do fonte (nao hardcoded) ---
$verLinha = Select-String -Path $fonte -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLinha) { throw "Nao achei __version__ em $fonte" }
$versao = $verLinha.Matches[0].Groups[1].Value
# Nuitka quer 4 campos em file-version (x.y.z.w); completa com .0
$fileVersion = ($versao.Split('.') + @('0','0','0','0'))[0..3] -join '.'
Write-Host "Q-Colabore agente - versao $versao (file-version $fileVersion)" -ForegroundColor Cyan

$icone = Join-Path $raiz "qcolabore.ico"
$argsNuitka = @(
    "-m", "nuitka",
    "--onefile",
    "--standalone",
    "--assume-yes-for-downloads",
    "--enable-plugin=tk-inter",
    "--windows-console-mode=disable",   # app de janela, sem console preto
    "--company-name=Qualicontax",
    "--product-name=Q-Colabore Agente",
    "--file-version=$fileVersion",
    "--product-version=$fileVersion",
    "--file-description=Agente Q-Colabore (envio de arquivos)",
    "--output-dir=$saida",
    "--output-filename=qcolabore.exe"
)
if (Test-Path $icone) { $argsNuitka += "--windows-icon-from-ico=$icone" }
$argsNuitka += $fonte

python @argsNuitka
if ($LASTEXITCODE -ne 0) { throw "Nuitka falhou (exit $LASTEXITCODE)." }

$exe = Join-Path $saida "qcolabore.exe"
Write-Host "`nOK: $exe" -ForegroundColor Green

# Copia para C:\qcolabore (a casa do agente). NAO sobrescreve o config.json.
New-Item -ItemType Directory -Force -Path $destinoInstalacao | Out-Null
Copy-Item -Force $exe (Join-Path $destinoInstalacao "qcolabore.exe")
Write-Host "Instalado em $destinoInstalacao\qcolabore.exe" -ForegroundColor Green
Write-Host "Lembrete: se o Defender reclamar, veja o passo 'Excecao no Defender' no README." -ForegroundColor Yellow
