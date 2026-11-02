import pytest
from fastapi.testclient import TestClient

from server import app
from database import get_db

client = TestClient(app)

def test_grades_and_report_card_flow():
    # 1. Login sebagai guru1
    res_login = client.post("/login", data={"username": "guru1", "password": "guru123"}, follow_redirects=False)
    assert res_login.status_code == 303
    t_cookie = res_login.cookies["nata_session"]

    # 2. Buka lembar input nilai guru
    res_grades_page = client.get("/teacher/grades", cookies={"nata_session": t_cookie})
    assert res_grades_page.status_code == 200
    assert "Modul Nilai Siswa" in res_grades_page.text
    assert "Lembar Input Nilai Formatif &amp; Sumatif" in res_grades_page.text or "Lembar Input Nilai Formatif & Sumatif" in res_grades_page.text

    # Ambil assignment_id & student_id untuk uji simpan
    conn = get_db()
    asg = conn.execute("SELECT id FROM teacher_assignments WHERE teacher_id = 'usr_t1' LIMIT 1").fetchone()
    assert asg is not None
    asg_id = asg["id"]

    student = conn.execute("SELECT id, full_name FROM students LIMIT 1").fetchone()
    assert student is not None
    std_id = student["id"]

    # 3. Simpan nilai batch untuk siswa
    # Misal: Formatif = 90, Midterm = 85, Final = 90 -> Final Grade = (90*0.4)+(85*0.3)+(90*0.3) = 36 + 25.5 + 27 = 88.5 (Predikat A)
    res_save = client.post("/teacher/grades/save-batch", data={
        "assignment_id": asg_id,
        "semester": "Ganjil",
        "academic_year": "2026/2027",
        "student_id": [std_id],
        f"score_formative_{std_id}": "90",
        f"score_midterm_{std_id}": "85",
        f"score_final_{std_id}": "90",
        f"notes_{std_id}": "Sangat aktif dalam mufrodat bahasa Arab."
    }, cookies={"nata_session": t_cookie}, follow_redirects=True)
    assert res_save.status_code == 200
    assert "Nilai seluruh siswa berhasil disimpan" in res_save.text

    # Verifikasi langsung di database
    grade_row = conn.execute("""
        SELECT final_grade, predicate, teacher_notes 
        FROM student_grades 
        WHERE student_id = ? AND semester = 'Ganjil'
    """, (std_id,)).fetchone()
    assert grade_row is not None
    assert grade_row["final_grade"] == 88.5
    assert grade_row["predicate"] == "A"
    assert "mufrodat" in grade_row["teacher_notes"]

    # 4. Unduh Leger CSV Nilai Guru
    res_csv = client.get(f"/teacher/grades/export-csv?assignment_id={asg_id}&semester=Ganjil", cookies={"nata_session": t_cookie})
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "NILAI AKHIR" in res_csv.text
    assert student["full_name"] in res_csv.text

    # 5. Login sebagai Admin & buka rekap nilai sekolah
    res_admin_login = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    a_cookie = res_admin_login.cookies["nata_session"]

    res_admin_grades = client.get("/admin/grades", cookies={"nata_session": a_cookie})
    assert res_admin_grades.status_code == 200
    assert "Rekap Nilai Siswa &amp; Manajemen Cetak Rapor" in res_admin_grades.text or "Rekap Nilai Siswa & Manajemen Cetak Rapor" in res_admin_grades.text
    assert student["full_name"] in res_admin_grades.text

    # 6. Buka lembar cetak rapor digital siswa (A4 / PDF print-ready)
    res_report = client.get(f"/report-card/{std_id}?semester=Ganjil&academic_year=2026/2027", cookies={"nata_session": a_cookie})
    assert res_report.status_code == 200
    assert "Laporan Hasil Belajar (Rapor)" in res_report.text
    assert student["full_name"] in res_report.text
    assert "88.5" in res_report.text
    assert "Cetak Dokumen / Simpan PDF" in res_report.text
    assert "Kepala Sekolah / Mudir Ma&#39;had" in res_report.text or "Kepala Sekolah" in res_report.text
