# Clipper Studio

Mengubah video panjang (podcast, stream, YouTube) menjadi klip vertikal 9:16
dengan subtitle animasi per kata dan hook di awal, lalu kamu audit sebelum diposting.

> **Soal izin:** memotong video kreator lain tanpa izin, atau tanpa ikut program
> clipping resmi mereka, bisa membuat klipmu kena klaim hak cipta dan tidak bisa
> dimonetisasi. Alat ini tidak menyelesaikan masalah itu.

## Alurnya

```
URL / file
  │  yt-dlp                       unduh video (≤1080p)
  ▼
WhisperX (GPU)                    transkrip + timestamp per kata  → words.json
  ▼
AI-Youtube-Shorts-Generator       LLM (Gemini/OpenAI) memilih highlight,
  (mode local, repo asli)         potong + reframe 9:16 dengan face tracking
  ▼
captions.py (ffmpeg, NVENC)       1080x1920, subtitle per kata + hook 3 detik
  ▼
review.html + timeline.otio       KAMU audit: pakai / buang / geser / ganti hook
  ▼
apply                             render ulang yang diubah → folder approved/
```

Repo asli dipakai sebagai dependensi, tidak di-fork:
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense),
[WhisperX](https://github.com/m-bain/whisperX) (BSD-2),
[AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator)
(MIT, dikunci ke satu commit di `install.ps1`).

## Kebutuhan

- Windows 10/11, GPU NVIDIA (target: RTX 3060) dengan driver terbaru
- ±10 GB ruang kosong (PyTorch CUDA + model Whisper)
- API key Gemini (ada kuota gratis: https://aistudio.google.com/apikey) atau OpenAI

## Instal

```powershell
git clone https://github.com/mfasyafdl/Clipper-Studio.git
cd Clipper-Studio
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
notepad .env      # isi GEMINI_API_KEY
```

`install.ps1` memasang Python 3.11, Git, dan FFmpeg lewat winget, membuat dua
virtualenv (`.venv-ays` dan `.venv-wx`, dipisah supaya versi torch tidak
bentrok), lalu mengecek apakah CUDA terdeteksi.

## Pakai

```powershell
# 1. Proses satu video
.\run.ps1 run "https://www.youtube.com/watch?v=XXXXXXXXXXX" --clips 5

# 2. Audit: membuka halaman review di browser
.\run.ps1 review "E:\VIdeo Clipping\20261124-XXXXXXXXXXX"

# 3. Terapkan keputusanmu (trim, ganti hook, kumpulkan yang dipakai)
.\run.ps1 apply "E:\VIdeo Clipping\20261124-XXXXXXXXXXX"

# Ganti gaya subtitle di .env, lalu render ulang semua klip
.\run.ps1 captions "E:\VIdeo Clipping\20261124-XXXXXXXXXXX"
```

### Di halaman review

- **✓ Pakai / ✕ Buang** untuk tiap klip.
- **Geser awal / akhir** dalam detik. Contoh: awal `-2` memulai 2 detik lebih
  awal, akhir `3` menambah 3 detik di belakang.
- **Catatan**: tulis `hook: teks baru` untuk mengganti hook. Catatan lain
  dikumpulkan ke `notes_for_claude.md` supaya Claude bisa mengerjakannya.
- **Simpan keputusan** menulis `decisions.json` ke folder job.

### Isi folder job

| File | Isi |
|---|---|
Semua job ditulis ke `JOBS_DIR` di `.env` (default: `E:\VIdeo Clipping`).

| `source_*.mp4` | video asli |
| `words.json` | transkrip per kata |
| `final/clip_XX.mp4` | klip jadi (1080x1920) |
| `final/clip_XX.srt` | subtitle yang bisa diedit di Resolve/CapCut |
| `review.html` | halaman audit |
| `timeline.otio` | timeline untuk DaVinci Resolve: File > Import > Timeline |
| `approved/` | klip yang kamu setujui, siap posting |

## Supaya Claude bisa ikut mengedit

Claude menjalankan perintah di atas di laptopmu lewat **Remote Control** dari
thread Claude, membaca `decisions.json` dan `notes_for_claude.md`, lalu
merender ulang. Kenapa tidak lewat DaVinci Resolve langsung? Lihat
[docs/editor-control.md](docs/editor-control.md).

## Batasan yang perlu kamu tahu

- Belum diuji di Windows sungguhan. Logika subtitle, review, dan apply diuji
  otomatis (`tests/`). Bagian WhisperX GPU dan AI-Youtube-Shorts-Generator baru
  akan teruji saat dijalankan pertama kali di laptopmu.
- Reframe bawaan AI-Youtube-Shorts-Generator memakai deteksi wajah Haar
  (OpenCV) yang sederhana. Untuk podcast dua orang, kamera bisa "melompat" ke
  wajah yang salah. Itu salah satu hal utama yang perlu kamu cek saat audit.
- RTX 3060 laptop hanya punya 6 GB VRAM. Kalau muncul `CUDA out of memory`,
  turunkan `WHISPER_BATCH` ke 4 di `.env`.

## Tes

```powershell
.\.venv-wx\Scripts\python.exe -m pip install pytest
.\.venv-wx\Scripts\python.exe -m pytest tests
```
