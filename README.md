# NataSekolah FastAPI Core

> Ultra-lightweight, self-hosted school management engine built with Python FastAPI, SQLite WAL, and Vercel Geist design system.

## Key Highlights
- **Ultra-lightweight:** ~50MB RAM footprint (benchmark setara PushNotif)
- **Zero Build Step:** Native server-rendered HTML with Tailwind CSS & Lucide Icons
- **Ultra-low Latency:** SQLite WAL mode with microsecond response times
- **Teacher Attendance Feature:**
  - Slide-over Side Panel (drawer) for instant status overrides and notes
  - 50m Geofencing GPS Verification (Haversine formula)
  - Daily Rotating Dynamic QR Code Verification for desk check-ins
- **100% Self-Hosted:** No Vercel or vendor lock-in

## Running Locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PORT=8095 python server.py
```
