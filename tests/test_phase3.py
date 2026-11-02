import pytest
from fastapi.testclient import TestClient
import io

from server import app

client = TestClient(app)

def test_master_data_flow():
    # Login as admin
    res_login = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert res_login.status_code == 303
    cookie = res_login.cookies["nata_session"]

    # 1. Buka halaman master data
    res_master = client.get("/admin/master", cookies={"nata_session": cookie})
    assert res_master.status_code == 200
    assert "Master Data: Santri, Rombel &amp; Dewan Guru" in res_master.text or "Master Data: Santri, Rombel & Dewan Guru" in res_master.text
    assert "Abdullah Faqih" in res_master.text

    # 2. Unduh template CSV santri
    res_template = client.get("/admin/master/template-students", cookies={"nata_session": cookie})
    assert res_template.status_code == 200
    assert "nisn,nama,kelas,gender" in res_template.text

    # 3. Tambah rombel kelas baru
    res_cls = client.post("/admin/master/create-classroom", data={
        "name": "Kelas 12-A (Tahfidz Lanjutan)",
        "level": "12",
        "academic_year": "2026/2027"
    }, cookies={"nata_session": cookie}, follow_redirects=True)
    assert res_cls.status_code == 200
    assert "Kelas 12-A (Tahfidz Lanjutan)" in res_cls.text

    # 4. Tambah santri baru lewat panel samping
    res_new_std = client.post("/admin/master/save-student", data={
        "full_name": "Hamzah Bin Abdul Muthalib",
        "nisn": "NIS-2026-099",
        "classroom_id": "cls_10a",
        "gender": "L"
    }, cookies={"nata_session": cookie}, follow_redirects=True)
    assert res_new_std.status_code == 200
    assert "Hamzah Bin Abdul Muthalib" in res_new_std.text

    # 5. Import massal santri lewat CSV
    csv_content = """nisn,nama,kelas,gender
NIS-2026-101,Zubair Bin Awwam,Kelas 10-A (IPA & Tahfidz),L
NIS-2026-102,Khadijah Al-Kubra,Kelas 10-A (IPA & Tahfidz),P
"""
    files = {"file": ("santri_baru.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res_import = client.post("/admin/master/import-students-csv", files=files, cookies={"nata_session": cookie}, follow_redirects=True)
    assert res_import.status_code == 200
    assert "Zubair Bin Awwam" in res_import.text
    assert "Khadijah Al-Kubra" in res_import.text

    # 6. Tambah akun guru baru (gunakan username dinamis unik agar idempotent di test run berulang)
    import uuid
    rand_user = f"guru_{uuid.uuid4().hex[:6]}"
    res_teacher = client.post("/admin/master/create-teacher", data={
        "full_name": "Ustadz Salman Al-Farisi, Lc",
        "username": rand_user,
        "password": "gurupassword123"
    }, cookies={"nata_session": cookie}, follow_redirects=True)
    assert res_teacher.status_code == 200
    assert rand_user in res_teacher.text
