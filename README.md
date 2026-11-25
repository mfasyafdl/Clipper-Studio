# Clipper Studio

Mengubah video panjang (podcast, stream, YouTube) menjadi klip vertikal 9:16
dengan subtitle animasi per kata dan hook di awal, lalu kamu audit sebelum diposting.

> **Soal izin:** memotong video kreator lain tanpa izin, atau tanpa ikut program
> clipping resmi mereka, bisa membuat klipmu kena klaim hak cipta dan tidak bisa
> dimonetisasi. Alat ini tidak menyelesaikan masalah itu.

## Mesin `gaming` (default)

Dirancang dari riset clip Windah yang viral (laporan di folder project:
`research/reports/`):

1. **Momen dari reaksi, bukan dari kata-kata.** Replay chat YouTube
   (penulis unik per 8 detik, digeser mundur 10 detik), lonjakan suara
   "hening lalu teriak", heatmap Most Replayed, dan kata reaksi (anjir,
   wkwk, aaa) digabung jadi skor per detik. 40 puncak teratas jadi kandidat.
2. **Gemini menilai lalu menonton.** Kandidat tanpa satu punchline jelas
   dibuang. Finalis ditonton Gemini (video 360p) untuk menentukan awal,
   punchline, dan akhir: horor menyimpan build-up 10-20 detik, lainnya
   2-5 detik, selesai sekitar 1 detik setelah reaksi. Durasi 15-35 detik.
   Judul gaya kutipan: `FREDDY: DULUAN BANG🗿 - MOMEN KOCAK WINDAH BASUDARA`.
3. **Layout facecam besar.** Header hitam berisi hook, facecam 1080x790,
   gameplay 4:3 di bawah. Wajah dicari dengan YuNet hanya di pojok kamera,
   satu crop statis per klip (tidak loncat ke wajah di dalam game).
4. **Efek di punchline saja.** `EFFECTS=light` (default): satu zoom di wajah
   + satu SFX. `full` menambah shake/flash, `off` mematikan semua. SFX dibuat
   sendiri oleh ffmpeg; taruh file sendiri di `sfx/` (mis. `sfx/boom.wav`)
   untuk menggantinya.

Mesin lama tetap ada: `ENGINE=ays` di `.env`.

## Alurnya (mesin lama `ays`)

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

- Windows 10/11, GPU NVIDIA (diuji: RTX 3050 Laptop 4 GB) dengan driver terbaru
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

# Pilih ulang momen + render ulang job lama dengan mesin gaming
# (tanpa unduh/transkrip ulang; replay chat diunduh kalau belum ada)
.\run.ps1 rerun "E:\VIdeo Clipping\20261124-XXXXXXXXXXX" --clips 5

# Ulangi klip yang gagal (crop/caption)
.\run.ps1 retry "E:\VIdeo Clipping\20261124-XXXXXXXXXXX"

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
- GPU dengan VRAM kecil (RTX 3050 Laptop 4 GB, RTX 3060 Laptop 6 GB) bisa kehabisan memori. Kalau muncul `CUDA out of memory`,
  turunkan `WHISPER_BATCH` ke 4 (atau 2) dan pakai `WHISPER_COMPUTE=int8_float16` di `.env`.

## Kalau ada masalah

- **Unduhan model Whisper macet di 0 byte**: jalankan `$env:HF_HUB_DISABLE_XET="1"` di PowerShell, lalu ulangi perintahnya.
- **`h264_nvenc` gagal**: klip tetap dirender dengan libx264 (CPU, lebih lambat). Biasanya penyebabnya driver NVIDIA yang lebih tua dari build FFmpeg; update driver lalu coba lagi.
- **Video panjang**: menjalankan ulang `run` di hari yang sama memakai ulang video dan transkrip yang sudah ada.

## Tes

```powershell
.\.venv-wx\Scripts\python.exe -m pip install pytest
.\.venv-wx\Scripts\python.exe -m pytest tests
```
