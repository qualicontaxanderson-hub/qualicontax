# Build do agente Q-Colabore com Nuitka (Windows) — modo PASTA + ZIP.
#
# Por que PASTA (--standalone) e NAO --onefile: o onefile (e o PyInstaller) se
# descompactam numa pasta temporaria a CADA execucao — justamente o padrao que o
# Windows Defender marca como suspeito. Foi por isso que o Q-Robo abandonou o
# onefile. Em modo pasta o .exe roda direto, com as pecas ao lado, sem extrair
# nada em runtime.
#
# A saida vira um .zip com a MESMA convencao do Q-Robo (qualicontax.zip): a RAIZ
# do zip ja e o conteudo do programa (o .exe + DLLs + tcl/tk), sem uma subpasta
# extra. O funcionario extrai e roda o .exe; o proprio programa cria C:\qcolabore
# (a casa dos dados) sozinho na 1a execucao.
#
# A --file-version e derivada do __version__ do fonte (nao hardcoded).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\build_qcolabore.ps1
#
# Pre-requisitos (uma vez):
#   pip install nuitka requests pystray pillow
#   Um compilador C: o Nuitka baixa o ziglang sozinho na 1a vez
#   (--assume-yes-for-downloads ja responde 'sim').

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$fonte = Join-Path $raiz "qcolabore_agente.py"
$saida = Join-Path $raiz "build"

# --- versao derivada do __version__ do fonte (nao hardcoded) ---
$verLinha = Select-String -Path $fonte -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLinha) { throw "Nao achei __version__ em $fonte" }
$versao = $verLinha.Matches[0].Groups[1].Value
$fileVersion = ($versao.Split('.') + @('0','0','0','0'))[0..3] -join '.'
Write-Host "Q-Colabore agente - versao $versao (file-version $fileVersion)" -ForegroundColor Cyan

$icone = Join-Path $raiz "qcolabore.ico"
$argsNuitka = @(
    "-m", "nuitka",
    "--standalone",                      # PASTA, sem --onefile
    "--assume-yes-for-downloads",
    "--enable-plugin=tk-inter",
    "--include-package=pystray",         # backend do tray e importado por plataforma
    "--windows-console-mode=disable",    # app de janela, sem console preto
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

# Nuitka standalone gera <script>.dist com o .exe + pecas dentro.
$dist = Join-Path $saida "qcolabore_agente.dist"
if (-not (Test-Path (Join-Path $dist "qcolabore.exe"))) {
    throw "Pasta standalone nao encontrada em $dist"
}

# Compacta o CONTEUDO da pasta na RAIZ do zip (flat), como o qualicontax.zip.
$zip = Join-Path $saida ("qcolabore-{0}.zip" -f $versao)
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip -CompressionLevel Optimal

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 2)
$nArq = (Get-ChildItem $dist -Recurse -File).Count
Write-Host "`nOK." -ForegroundColor Green
Write-Host ("  Pasta standalone: {0} ({1} arquivos)" -f $dist, $nArq)
Write-Host ("  ZIP para distribuir: {0} ({1} MB)" -f $zip, $mb) -ForegroundColor Green
Write-Host "Publique o ZIP na pasta do Dropbox /Aplicativos/QUALICONTAX/Q-Colabore/." -ForegroundColor Yellow
Write-Host "Lembrete: se o Defender reclamar, veja 'Excecao no Defender' no README." -ForegroundColor Yellow
