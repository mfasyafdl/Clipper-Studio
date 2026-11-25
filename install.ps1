# Clipper Studio installer for Windows 10/11 + NVIDIA GPU (tested target: RTX 3060).
# Run from the repo folder in PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass; .\install.ps1
# Safe to run again: steps that are already done are skipped.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

# Pinned upstream commit of AI-Youtube-Shorts-Generator (MIT). Bump on purpose, not by accident.
$AysRepo = "https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator.git"
$AysCommit = "8c9ac3103a137e53669aeebfc2af8dbdd810c35a"
$TorchIndex = "https://download.pytorch.org/whl/cu128"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                "$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:LOCALAPPDATA\Microsoft\WindowsApps"
}
function Ensure-Ffmpeg {
    # winget sometimes installs Gyan.FFmpeg without creating the Links shim.
    if (Have "ffmpeg") { return }
    $exe = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if (-not $exe) { throw "ffmpeg.exe tidak ditemukan setelah instal. Pasang FFmpeg manual lalu jalankan ulang." }
    $dir = $exe.DirectoryName
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$dir*") { [Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User") }
    $env:Path = "$env:Path;$dir"
    Write-Host "  ffmpeg ditambahkan ke PATH: $dir"
}
function Winget-Install($id, $cmd) {
    if (Have $cmd) { Write-Host "  $cmd sudah ada"; return }
    if (-not (Have "winget")) { throw "winget tidak ada. Pasang '$id' manual lalu jalankan ulang." }
    winget install --id $id -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Have $cmd)) { throw "$cmd belum terdeteksi. Tutup PowerShell, buka lagi, jalankan ulang install.ps1." }
}

Step "Cek GPU NVIDIA"
if (Have "nvidia-smi") {
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
} else {
    Write-Warning "nvidia-smi tidak ditemukan. Update driver NVIDIA dulu (GeForce Experience / nvidia.com). Tanpa GPU semuanya jalan di CPU dan jauh lebih lambat."
}

Step "Pasang Python 3.11, Git, FFmpeg (winget)"
Winget-Install "Git.Git" "git"
Refresh-Path
if (-not (Have "ffmpeg")) {
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
}
Ensure-Ffmpeg
$py = $null
if (Have "py") { py -3.11 --version 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $py = "py -3.11" } }
if (-not $py) {
    winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
    Refresh-Path
    $py = "py -3.11"
}
Invoke-Expression "$py --version"

Step "Ambil AI-Youtube-Shorts-Generator (commit terkunci)"
$Ays = Join-Path $Root "vendor\AI-Youtube-Shorts-Generator"
if (-not (Test-Path $Ays)) {
    New-Item -ItemType Directory -Force (Join-Path $Root "vendor") | Out-Null
    git clone $AysRepo $Ays
}
git -C $Ays fetch --quiet origin
git -C $Ays checkout --quiet $AysCommit

Step "Virtualenv untuk AI-Youtube-Shorts-Generator (.venv-ays)"
if (-not (Test-Path ".venv-ays")) { Invoke-Expression "$py -m venv .venv-ays" }
& .\.venv-ays\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-ays\Scripts\python.exe -m pip install -r (Join-Path $Ays "requirements-local.txt")
# OpenCV 5 dropped CascadeClassifier from the main package; the face tracker needs it.
& .\.venv-ays\Scripts\python.exe -m pip install "opencv-python>=4.8,<5"

Step "Virtualenv untuk WhisperX + yt-dlp dengan CUDA 12.8 (.venv-wx), unduhan besar ~3 GB"
if (-not (Test-Path ".venv-wx")) { Invoke-Expression "$py -m venv .venv-wx" }
& .\.venv-wx\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-wx\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url $TorchIndex
& .\.venv-wx\Scripts\python.exe -m pip install "whisperx[fast]" yt-dlp opentimelineio google-genai "opencv-python>=4.8,<5" numpy

Step "Cek CUDA dari Python"
& .\.venv-wx\Scripts\python.exe -c "import torch; ok=torch.cuda.is_available(); print('CUDA:', ok, torch.cuda.get_device_name(0) if ok else '(tidak ada, akan pakai CPU)')"

$JobsDir = (Get-Content ".env.example" | Where-Object { $_ -match '^JOBS_DIR=' }) -replace '^JOBS_DIR=', ''
if ($JobsDir -and -not (Test-Path $JobsDir)) {
    try { New-Item -ItemType Directory -Force $JobsDir | Out-Null; Write-Host "Folder output dibuat: $JobsDir" }
    catch { Write-Warning "Tidak bisa membuat $JobsDir. Ubah JOBS_DIR di .env." }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`n.env dibuat. Isi GEMINI_API_KEY (gratis di https://aistudio.google.com/apikey) sebelum menjalankan." -ForegroundColor Yellow
}

Write-Host "`nSelesai. Coba:  .\run.ps1 run `"https://www.youtube.com/watch?v=...`" --clips 5" -ForegroundColor Green
