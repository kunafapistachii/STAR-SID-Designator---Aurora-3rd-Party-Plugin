# ✈️ Aurora STAR/SID Designator

STAR/SID Designator adalah plugin pihak ketiga (3rd-party plugin) untuk **IVAO Aurora ATC Client**. Plugin ini secara otomatis mencocokkan rute penerbangan pesawat dengan prosedur keberangkatan (SID) atau kedatangan (STAR) yang sesuai berdasarkan runway yang sedang aktif, lalu mengirimkan designator prosedur tersebut langsung ke label waypoint/scratchpad flight strip di Aurora via perintah TCP `#LBWP`.

---

## 🏛️ System Architecture

Aplikasi ini menggunakan arsitektur tiga arah (three-tier architecture):

```
┌────────────────────┐      Port 1130      ┌───────────────────────┐
│ Aurora ATC Client  │◄───────────────────►│ Python Bridge Server  │
│ (TCP/ASCII Server) │                     │ (Asyncio Event Loop)  │
└────────────────────┘                     └───────────┬───────────┘
                                                       │ WebSockets
                                                       │ (Port 8080)
                                                       ▼
                                            ┌───────────────────────┐
                                            │ Web Frontend Dashboard│
                                            │ (Dark Aviation Theme) │
                                            └───────────────────────┘
```

1. **Aurora ATC Client**: Sumber data rencana penerbangan (`#FP`), posisi traffic (`#TRPOS`), dan konfigurasi runway aktif (`#CTRLRWY`). Menerima perintah penugasan scratchpad label via `#LBWP`.
2. **Python Bridge Server**: 
   - Mengelola koneksi asinkron TCP ke Aurora dan siklus polling data traffic.
   - Melakukan parsing file sektor (`.sid` dan `.str`) secara rekursif dan menjalankan algoritma pencocokan rute.
   - Menyediakan API WebSocket dan static file server untuk dashboard web.
3. **Web Frontend Dashboard**: Interface controller dengan tema *dark glassmorphic* premium yang menampilkan daftar traffic keberangkatan (Departures) dan kedatangan (Arrivals) beserta saran pencocokan prosedur.

---

## ✨ Fitur Utama

- **Dynamic Sector Directory Scanning**: Memindai folder `Airports` secara rekursif berdasarkan konfigurasi `SECTOR_FILES_PATH` untuk memuat file `.sid` dan `.str` semua bandara di dalam FIR secara otomatis.
- **Smart Route Matching Algorithm**: Menghindari kesalahan pencocokan rute akibat beberapa prosedur yang menggunakan jalur akhir/awal yang sama (overlap) dengan mencocokkan core fix dari nama prosedur terlebih dahulu (misal: "EGUKO 2L" dicari fix `EGUKO` pada rute), baru kemudian menggunakan jumlah fix overlap sebagai tiebreaker.
- **Connection State Machine**: Indikator koneksi real-time pada UI dashboard (`AURORA: ONLINE`, `OFFLINE`, atau `DEMO MODE`). Dashboard akan memblokir interaksi dan menampilkan modal reconnecting jika koneksi ke server Python terputus.
- **Demo Mode**: Mode simulasi offline yang memuat traffic buatan realistis untuk Bandara Soekarno-Hatta (WIII) guna keperluan pengujian UI dan fitur tanpa perlu tersambung ke Aurora.

---

## ⚙️ Persyaratan Sistem (Prerequisites)

- **Python 3.10** atau versi lebih baru.
- **IVAO Aurora ATC Client** terinstal di PC.
- File Sektor FIR terinstal di Aurora (berisi folder `Airports` dengan file `.sid` & `.str`).

---

## 🔧 Konfigurasi (`.env`)

Salin berkas `.env.example` menjadi `.env` lalu sesuaikan parameternya:

```env
# Koneksi TCP ke Aurora
AURORA_HOST=localhost
AURORA_PORT=1130

# Path folder Include sector files Aurora
# Contoh: C:/IVAO/Aurora/SectorFiles/Include
SECTOR_FILES_PATH=F:/Aurora/SectorFiles/Include

# Interval refresh traffic dari Aurora (dalam detik)
POLL_INTERVAL=3

# Port untuk Web Dashboard
WEB_PORT=8080

# Demo Mode (true untuk simulasi offline, false untuk koneksi real-time)
DEMO_MODE=true
```

> [!NOTE]  
> Jika `SECTOR_FILES_PATH` tidak valid atau belum diisi, dashboard akan masuk ke mode setup otomatis untuk meminta lokasi folder melalui GUI web.

---

## 🚀 Cara Menjalankan Aplikasi

1. **Instal Dependensi**:
   Buka terminal di root direktori project, lalu jalankan perintah berikut untuk menginstal package yang dibutuhkan:
   ```bash
   pip install -r requirements.txt
   ```

2. **Jalankan Server**:
   Cukup klik ganda (double-click) pada file **`Start-Server.bat`** di Windows. Script ini akan mendeteksi virtual environment `.venv` secara otomatis jika ada, atau menggunakan Python global sistem Anda.

3. **Buka Web Dashboard**:
   Buka browser Anda dan akses alamat:
   [http://localhost:8080](http://localhost:8080)

4. **Matikan Server**:
   Klik ganda pada file **`Stop-Server.bat`** atau tutup langsung jendela terminal server yang sedang berjalan.

---

## 🎮 Cara Penggunaan Dashboard

1. **Menghubungkan ke Aurora**:
   - Pastikan opsi *3rd Party Connection* sudah aktif di dalam pengaturan aplikasi IVAO Aurora (menggunakan port TCP 1130).
   - Pastikan indikator status di pojok kanan atas dashboard menunjukkan `AURORA: ONLINE` (hijau berkedip) atau `AURORA: DEMO MODE` (oranye).
2. **Memilih Runway Config**:
   - Runway keberangkatan (DEP) dan kedatangan (ARR) untuk setiap bandara dideteksi secara otomatis dari Aurora. Anda dapat menyesuaikannya melalui tombol konfigurasi runway di panel dashboard jika diperlukan.
3. **Mencocokkan & Memilih Prosedur**:
   - Daftar pesawat akan otomatis terbagi menjadi panel **Departures (SID)** dan **Arrivals (STAR)**.
   - Sistem akan menganalisis rute filed flight plan dan memberikan rekomendasi prosedur terbaik berdasarkan runway aktif.
4. **Mengirim Prosedur ke Aurora (Assign)**:
   - Pilih baris pesawat yang ingin diproses.
   - Klik tombol **Assign** untuk mengirimkan kode prosedur terpilih (maksimal 12 karakter) ke Aurora. Label ini akan muncul di kolom *waypoint/scratchpad* flight strip pesawat di layar radar Aurora Anda.
   - Klik tombol **Clear** jika ingin menghapus label penugasan tersebut dari strip.

---

## ❌ Troubleshooting & Penanganan Masalah

- **Status `AURORA: OFFLINE`**:
  Pastikan Aurora Client sudah berjalan, Anda sedang terhubung online/simulasi di Aurora, dan opsi *3rd Party Connection* aktif.
- **Error `traffic not assumed` saat Assign**:
  Sebelum Anda dapat mengirim label ke flight strip pesawat, Anda harus terlebih dahulu melakukan **Assume (F3)** terhadap pesawat tersebut di dalam client Aurora. Jika belum di-assume, Aurora akan menolak perubahan data strip dari pihak ketiga.
- **Daftar Prosedur Kosong**:
  Periksa kembali konfigurasi `SECTOR_FILES_PATH`. Pastikan mengarah ke folder yang tepat (misal: `SectorFiles/Include`) di mana di dalamnya terdapat struktur subfolder FIR dan bandara (contoh: `Include/WIIF/Airports/WIII.sid`).
