# Wrapper: .\run.ps1 run <url> [--clips 5] | review <job> | apply <job> | captions <job>
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv-wx\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Belum terinstal. Jalankan .\install.ps1 dulu." }

# faster-whisper (ctranslate2) looks for cuBLAS/cuDNN DLLs on PATH; the torch
# CUDA wheel already ships them in torch\lib, so expose that folder.
$TorchLib = Join-Path $Root ".venv-wx\Lib\site-packages\torch\lib"
if (Test-Path $TorchLib) { $env:Path = "$TorchLib;$env:Path" }

Push-Location $Root
try {
    & $Py -m clipper @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
