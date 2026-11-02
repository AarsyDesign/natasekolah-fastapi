import pytest
from fastapi.testclient import TestClient
import sqlite3
import os
import secrets

# Set DB test khusus
os.environ["NATA_DB_PATH"] = "/opt/data/work/natasekolah-core/test_natasekolah.db"

from server import app
from database import init_db, get_db, haversine_distance_meters

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    if os.path.exists("/opt/data/work/natasekolah-core/test_natasekolah.db"):
        os.remove("/opt/data/work/natasekolah-core/test_natasekolah.db")
    init_db()
    yield
    if os.path.exists("/opt/data/work/natasekolah-core/test_natasekolah.db"):
        os.remove("/opt/data/work/natasekolah-core/test_natasekolah.db")

client = TestClient(app)

def test_login_page_renders():
    res = client.get("/login")
    assert res.status_code == 200
    assert "NataSekolah" in res.text
    assert "Username" in res.text

def test_login_teacher_success():
    res = client.post("/login", data={"username": "guru1", "password": "guru123"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/attendance"
    assert "nata_session" in res.cookies

def test_login_admin_success():
    res = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/admin/recap"
    assert "nata_session" in res.cookies

def test_haversine_distance():
    # Malang square coords
    lat1, lon1 = -7.9666, 112.6326
    # Exact point -> 0 meters
    dist = haversine_distance_meters(lat1, lon1, lat1, lon1)
    assert dist == 0.0

    # Slight offset ~35 meters
    lat2, lon2 = -7.9669, 112.6326
    dist2 = haversine_distance_meters(lat1, lon1, lat2, lon2)
    assert 30 < dist2 < 40

def test_teacher_gps_checkin_flow():
    # Login dulu sebagai guru1
    res_login = client.post("/login", data={"username": "guru1", "password": "guru123"}, follow_redirects=False)
    session_cookie = res_login.cookies["nata_session"]

    # 1. Coba check-in GPS di luar radius 50m (misal 500m jauhnya)
    res_fail = client.post(
        "/api/attendance/check-in-gps",
        json={"latitude": -7.9750, "longitude": 112.6326, "action": "IN"},
        cookies={"nata_session": session_cookie}
    )
    assert res_fail.status_code == 400
    assert "di luar jangkauan" in res_fail.json()["detail"]

    # 2. Check-in GPS valid (koordinat tepat di sekolah)
    res_success = client.post(
        "/api/attendance/check-in-gps",
        json={"latitude": -7.9666, "longitude": 112.6326, "action": "IN"},
        cookies={"nata_session": session_cookie}
    )
    assert res_success.status_code == 200
    assert "Presensi Masuk berhasil" in res_success.json()["message"]

    # 3. Check-out GPS
    res_out = client.post(
        "/api/attendance/check-in-gps",
        json={"latitude": -7.9666, "longitude": 112.6326, "action": "OUT"},
        cookies={"nata_session": session_cookie}
    )
    assert res_out.status_code == 200
    assert "Presensi Pulang berhasil" in res_out.json()["message"]

def test_teacher_qr_checkin_flow():
    # Login sebagai guru2
    res_login = client.post("/login", data={"username": "guru2", "password": "guru123"}, follow_redirects=False)
    session_cookie = res_login.cookies["nata_session"]

    # Token salah
    res_fail = client.post(
        "/api/attendance/check-in-qr",
        json={"token": "TOKEN_PALSU_123"},
        cookies={"nata_session": session_cookie}
    )
    assert res_fail.status_code == 400

    # Token benar (default seeded: QR-NATA-TODAY)
    res_success = client.post(
        "/api/attendance/check-in-qr",
        json={"token": "QR-NATA-TODAY"},
        cookies={"nata_session": session_cookie}
    )
    assert res_success.status_code == 200
    assert "Presensi Masuk via QR berhasil" in res_success.json()["message"]

def test_admin_recap_and_manual_override():
    # Login admin
    res_login = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    session_cookie = res_login.cookies["nata_session"]

    # Buka recap
    res_recap = client.get("/admin/recap", cookies={"nata_session": session_cookie})
    assert res_recap.status_code == 200
    assert "Ustadzah Siti Aminah" in res_recap.text

    # Override kehadiran guru3 jadi SAKIT secara manual
    res_override = client.post(
        "/api/admin/manual-attendance",
        data={
            "user_id": "usr_t3",
            "date": "2026-11-02",
            "status": "SICK",
            "notes": "Surat dokter terlampir"
        },
        cookies={"nata_session": session_cookie},
        follow_redirects=True
    )
    assert res_override.status_code == 200
    assert "Sakit" in res_override.text
