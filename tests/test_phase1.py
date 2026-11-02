import pytest
from fastapi.testclient import TestClient
import sqlite3
import os

from server import app

client = TestClient(app)

def test_admin_settings_flow():
    # Login admin
    res = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert res.status_code == 303
    cookie = res.cookies["nata_session"]

    # Simpan settings jam & GPS
    res_save = client.post("/admin/settings", data={
        "work_start_time": "07:00",
        "late_cutoff_time": "07:15",
        "work_end_time": "15:30",
        "latitude": "-7.9500",
        "longitude": "112.6000",
        "radius_meters": "75"
    }, cookies={"nata_session": cookie}, follow_redirects=False)
    assert res_save.status_code == 303

    # Akses halaman settings
    res_page = client.get("/admin/settings", cookies={"nata_session": cookie})
    assert res_page.status_code == 200
    assert "07:00" in res_page.text
    assert "75" in res_page.text

def test_admin_monthly_recap_and_export():
    res = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    cookie = res.cookies["nata_session"]

    # Akses rekap bulanan
    res_monthly = client.get("/admin/monthly", cookies={"nata_session": cookie})
    assert res_monthly.status_code == 200
    assert "Rekap Bulanan Presensi Guru" in res_monthly.text
    assert "Tingkat Disiplin" in res_monthly.text

    # Ekspor CSV
    res_export = client.get("/admin/monthly/export", cookies={"nata_session": cookie})
    assert res_export.status_code == 200
    assert "text/csv" in res_export.headers["content-type"]
    assert "Nama Guru / Staf,NIP" in res_export.text
