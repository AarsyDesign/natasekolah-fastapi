import pytest
from fastapi.testclient import TestClient
import sqlite3
import os

from server import app

client = TestClient(app)

def test_student_attendance_flow():
    # Login sebagai guru1 (Ustadzah Siti Aminah)
    res = client.post("/login", data={"username": "guru1", "password": "guru123"}, follow_redirects=False)
    assert res.status_code == 303
    cookie = res.cookies["nata_session"]

    # 1. Buka daftar kelas mengajar
    res_classes = client.get("/teacher/classes", cookies={"nata_session": cookie})
    assert res_classes.status_code == 200
    assert "Kelas 10-A (IPA &amp; Tahfidz)" in res_classes.text or "Kelas 10-A (IPA & Tahfidz)" in res_classes.text
    assert "Bahasa Arab" in res_classes.text

    # 2. Buka lembar presensi santri (asg_1)
    res_att = client.get("/teacher/attendance/asg_1", cookies={"nata_session": cookie})
    assert res_att.status_code == 200
    assert "Abdullah Faqih" in res_att.text
    assert "Aisyah Putri Azzahra" in res_att.text

    # Ambil session_id yang terbuat
    import re
    match = re.search(r'name="session_id" value="(sess_[a-f0-9]+)"', res_att.text)
    assert match is not None
    session_id = match.group(1)

    # 3. Uji 1-Klik 'Tandai Semua Hadir'
    res_all_present = client.post("/api/teacher/attendance/mark-all-present", data={
        "session_id": session_id,
        "assignment_id": "asg_1"
    }, cookies={"nata_session": cookie}, follow_redirects=False)
    assert res_all_present.status_code == 303

    # Verifikasi roster berubah jadi Hadir
    res_att2 = client.get("/teacher/attendance/asg_1", cookies={"nata_session": cookie})
    assert "Hadir" in res_att2.text

    # 4. Ubah status salah satu santri (std_2: Sakit) via Panel Samping API
    res_update = client.post("/api/teacher/attendance/update-record", data={
        "session_id": session_id,
        "assignment_id": "asg_1",
        "student_id": "std_2",
        "status": "SICK",
        "notes": "Izin demam dan istirahat di asrama"
    }, cookies={"nata_session": cookie}, follow_redirects=False)
    assert res_update.status_code == 303

    # Verifikasi perubahan tercatat
    res_att3 = client.get("/teacher/attendance/asg_1", cookies={"nata_session": cookie})
    assert "Sakit" in res_att3.text
    assert "Izin demam dan istirahat di asrama" in res_att3.text

    # 5. Kunci sesi kelas (CLOSED)
    res_close = client.post("/api/teacher/attendance/close-session", data={
        "session_id": session_id,
        "assignment_id": "asg_1"
    }, cookies={"nata_session": cookie}, follow_redirects=False)
    assert res_close.status_code == 303

    # Verifikasi status Terkunci
    res_att4 = client.get("/teacher/attendance/asg_1", cookies={"nata_session": cookie})
    assert "Terkunci (Selesai)" in res_att4.text

    # Percobaan ubah setelah terkunci harus ditolak HTTP 400
    res_rejected = client.post("/api/teacher/attendance/update-record", data={
        "session_id": session_id,
        "assignment_id": "asg_1",
        "student_id": "std_1",
        "status": "ABSENT"
    }, cookies={"nata_session": cookie})
    assert res_rejected.status_code == 400
