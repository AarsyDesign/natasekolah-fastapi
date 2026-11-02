import pytest
import os
from database import init_db, get_db

@pytest.fixture(scope="function", autouse=True)
def setup_test_suite_db():
    test_db = "/opt/data/work/natasekolah-core/test_suite.db"
    os.environ["NATA_DB_PATH"] = test_db
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass
    init_db()
    # Seed classrooms & subjects & assignments sama seperti di natasekolah.db
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO classrooms (id, institution_id, name, level, academic_year) VALUES ('cls_10a', 'inst_demo', 'Kelas 10-A (IPA & Tahfidz)', '10', '2026/2027')")
    cur.execute("INSERT OR REPLACE INTO classrooms (id, institution_id, name, level, academic_year) VALUES ('cls_11a', 'inst_demo', 'Kelas 11-A (Bahasa & Dirosah)', '11', '2026/2027')")
    cur.execute("INSERT OR REPLACE INTO subjects (id, institution_id, name, code) VALUES ('sbj_arab', 'inst_demo', 'Bahasa Arab', 'ARB10')")
    cur.execute("INSERT OR REPLACE INTO subjects (id, institution_id, name, code) VALUES ('sbj_fiqih', 'inst_demo', 'Fiqih Ibadah', 'FQH10')")
    cur.execute("INSERT OR REPLACE INTO teacher_assignments (id, institution_id, teacher_id, classroom_id, subject_id) VALUES ('asg_1', 'inst_demo', 'usr_t1', 'cls_10a', 'sbj_arab')")
    cur.execute("INSERT OR REPLACE INTO teacher_assignments (id, institution_id, teacher_id, classroom_id, subject_id) VALUES ('asg_2', 'inst_demo', 'usr_t1', 'cls_11a', 'sbj_fiqih')")
    sample_students = [
        ("std_1", "inst_demo", "cls_10a", "Abdullah Faqih", "NIS-2026-001", "L"),
        ("std_2", "inst_demo", "cls_10a", "Ahmad Zaki Mubarak", "NIS-2026-002", "L"),
        ("std_3", "inst_demo", "cls_10a", "Aisyah Putri Azzahra", "NIS-2026-003", "P"),
        ("std_4", "inst_demo", "cls_10a", "Bilal Al-Ghifari", "NIS-2026-004", "L"),
        ("std_5", "inst_demo", "cls_10a", "Fatimah Az-Zahra", "NIS-2026-005", "P"),
        ("std_6", "inst_demo", "cls_10a", "Hasan Basri", "NIS-2026-006", "L"),
    ]
    cur.executemany("INSERT OR REPLACE INTO students (id, institution_id, classroom_id, full_name, nisn, gender) VALUES (?, ?, ?, ?, ?, ?)", sample_students)
    conn.commit()

    yield

    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass
