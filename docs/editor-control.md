# Editor mana yang bisa dikendalikan Claude?

Dicek 24 November 2026 lewat GitHub (bintang, commit terakhir, lisensi, README).

## Kesimpulan

Untuk alur clipper ini, Claude mengedit lewat **ffmpeg + skrip di repo ini**
(potong, reframe, subtitle, hook), dan kamu mengaudit lewat **halaman review**
(`.\run.ps1 review <job>`). Kalau mau sentuhan manual, buka `timeline.otio`
atau file `final/*.srt` di DaVinci Resolve gratis.

Mengendalikan aplikasi editor GUI secara langsung baru masuk akal kalau kamu
membeli DaVinci Resolve Studio.

## Opsi yang dicek

| Opsi | Status | Catatan |
|---|---|---|
| [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) | MIT, ±4,3 rb bintang, aktif | Yang paling matang. Butuh **Resolve Studio** (berbayar, sekitar $295) karena Blackmagic memblokir scripting dari luar di versi gratis. Ada "in-app bridge" untuk versi gratis, tapi pembuatnya sendiri menulis jalur Windows-nya belum terverifikasi, dan Resolve gratis 21.1 memindahkan scripting Python ke Studio. |
| MCP untuk Kdenlive (D-Ogi/mcp-kdenlive, AMMIROSOH/Kdenlive-mcp, dll.) | ≤51 bintang | Masih eksperimen, sebagian tanpa lisensi. Belum layak jadi alat utama. |
| MCP untuk Premiere Pro (hetpatel-11/Adobe_Premiere_Pro_MCP, dll.) | ±900 bintang | Butuh langganan Adobe. |
| Remotion | lisensi khusus | Bagus untuk template animasi, tapi berbayar untuk perusahaan dan butuh Node/React. Berlebihan untuk subtitle. |
| ffmpeg (dipakai repo ini) | bebas | Tidak ada GUI, tapi Claude bisa menjalankannya penuh lewat Remote Control di laptopmu. |

## Kalau nanti membeli Resolve Studio

1. Resolve Studio > Preferences > General > External scripting using: **Local**.
2. Ikuti instalasi di README davinci-resolve-mcp (`npx` atau `python install.py`).
   Installer-nya bisa mendaftarkan diri ke Claude Desktop dan Claude Code.
3. Setelah itu Claude bisa membuat timeline, menaruh marker review, dan
   mengatur render dari klip di folder `approved/`.
