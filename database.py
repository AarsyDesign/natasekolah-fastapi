import sqlite3
import os
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import bcrypt

DB_PATH = os.environ.get("NATA_DB_PATH", "/opt/data/work/natasekolah-core/natasekolah.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS institutions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            latitude REAL DEFAULT -7.9666,
            longitude REAL DEFAULT 112.6326,
            radius_meters INTEGER DEFAULT 50,
            work_start_time TEXT DEFAULT '07:15', -- Batas jam masuk tepat waktu (HH:MM)
            late_cutoff_time TEXT DEFAULT '07:30', -- Lewat jam ini dihitung Terlambat (HH:MM)
            work_end_time TEXT DEFAULT '15:00', -- Jam kepulangan (HH:MM)
            qr_daily_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            institution_id TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('ADMIN', 'TEACHER', 'STAFF')),
            nip TEXT,
            phone TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (institution_id) REFERENCES institutions(id) ON DELETE CASCADE
        );
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS teacher_attendance (
            id TEXT PRIMARY KEY,
            institution_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL, -- YYYY-MM-DD
            check_in_time TEXT, -- HH:MM:SS
            check_out_time TEXT, -- HH:MM:SS
            status TEXT NOT NULL CHECK(status IN ('PRESENT', 'LATE', 'SICK', 'EXCUSED', 'ABSENT')),
            method TEXT NOT NULL CHECK(method IN ('GPS', 'QR', 'MANUAL_ADMIN')),
            distance_meters REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date),
            FOREIGN KEY (institution_id) REFERENCES institutions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_att_date ON teacher_attendance(institution_id, date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_att_user ON teacher_attendance(user_id, date);")

    # Seed data awal jika kosong
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM institutions")
    if cur.fetchone()[0] == 0:
        inst_id = "inst_demo"
        conn.execute("""
            INSERT INTO institutions (id, name, code, latitude, longitude, radius_meters, qr_daily_token)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (inst_id, "SMA NataSekolah Unggulan", "NATASCHOOL-01", -7.9666, 112.6326, 50, "QR-NATA-TODAY"))

        admin_id = "usr_admin"
        admin_pass = hash_password("admin123")
        conn.execute("""
            INSERT INTO users (id, institution_id, username, password_hash, full_name, role, nip, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (admin_id, inst_id, "admin", admin_pass, "Ustadz Ahmad Fauzi (Admin Piket)", "ADMIN", "198001012005011001", "081234567890"))

        teachers = [
            ("usr_t1", "guru1", "Ustadzah Siti Aminah, S.Pd", "198502122010012003", "Bahasa Arab"),
            ("usr_t2", "guru2", "Ustadz Bambang Hermanto, M.Pd", "197808202003121002", "Matematika & Fiqih"),
            ("usr_t3", "guru3", "Ustadzah Nurul Hidayah, S.Si", "199203152019032011", "IPA Terpadu"),
            ("usr_t4", "guru4", "Ustadz Ridwan Kamil, Lc", "198811052014021004", "Tahfidz & Hadits"),
        ]
        t_pass = hash_password("guru123")
        for tid, uname, name, nip, _ in teachers:
            conn.execute("""
                INSERT INTO users (id, institution_id, username, password_hash, full_name, role, nip, phone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (tid, inst_id, uname, t_pass, name, "TEACHER", nip, "081298765432"))

        conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database SQLite WAL initialized successfully.")
