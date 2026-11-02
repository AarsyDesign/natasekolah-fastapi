import os
import uuid
import secrets
import hashlib
import io
import csv
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, Form, HTTPException, Depends, Cookie, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import jwt

from database import (
    get_db, verify_password, haversine_distance_meters, init_db
)

# Inisialisasi DB jika belum
init_db()

SECRET_KEY = os.environ.get("JWT_SECRET", "natasekolah_ultra_lightweight_secret_key_2026")
ALGORITHM = "HS256"

app = FastAPI(title="NataSekolah Core", description="Ultra-Lightweight Self-Hosted School Attendance System")
templates = Jinja2Templates(directory="/opt/data/work/natasekolah-core/templates")

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("nata_session")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            return None
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.*, i.name as institution_name, i.latitude as inst_lat, i.longitude as inst_lon,
                   i.radius_meters as inst_radius, i.qr_daily_token
            FROM users u
            JOIN institutions i ON u.institution_id = i.id
            WHERE u.id = ? AND u.is_active = 1
        """, (user_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)
    except Exception:
        return None

def require_auth(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user

# Pydantic Schemas
class GpsCheckInRequest(BaseModel):
    latitude: float
    longitude: float
    action: str = "IN" # "IN" or "OUT"

class QrCheckInRequest(BaseModel):
    token: str
    action: str = "IN"

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if user["role"] == "ADMIN":
        return RedirectResponse(url="/admin/recap")
    return RedirectResponse(url="/attendance")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username.strip(),))
    user_row = cur.fetchone()
    conn.close()

    if not user_row or not verify_password(password, user_row["password_hash"]):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Username atau kata sandi tidak cocok."},
            status_code=400
        )

    token = create_access_token({"user_id": user_row["id"], "role": user_row["role"]})
    redirect_url = "/admin/recap" if user_row["role"] == "ADMIN" else "/attendance"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="nata_session", value=token, httponly=True, max_age=7*86400, samesite="lax")
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="nata_session")
    return response

# PRESENSI MANDIRI GURU
@app.get("/attendance", response_class=HTMLResponse)
async def teacher_attendance_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cur = conn.cursor()
    # Kehadiran hari ini
    cur.execute("""
        SELECT * FROM teacher_attendance
        WHERE user_id = ? AND date = ?
    """, (user["id"], today_str))
    att_row = cur.fetchone()

    # Riwayat 5 hari terakhir
    cur.execute("""
        SELECT * FROM teacher_attendance
        WHERE user_id = ?
        ORDER BY date DESC LIMIT 5
    """, (user["id"],))
    history_rows = cur.fetchall()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="teacher_checkin.html",
        context={
            "user": user,
            "active_menu": "checkin",
            "today": today_str,
            "attendance": dict(att_row) if att_row else None,
            "history": [dict(r) for r in history_rows]
        }
    )

# API CHECK-IN GPS
@app.post("/api/attendance/check-in-gps")
async def api_checkin_gps(payload: GpsCheckInRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi telah berakhir, silakan login kembali.")

    # Hitung jarak dari koordinat sekolah
    school_lat = user["inst_lat"]
    school_lon = user["inst_lon"]
    max_radius = user["inst_radius"] or 50 # Default 50 meter

    dist = haversine_distance_meters(payload.latitude, payload.longitude, school_lat, school_lon)
    if dist > max_radius:
        raise HTTPException(
            status_code=400,
            detail=f"Posisi Anda di luar jangkauan sekolah ({dist:.1f} meter). Maksimal radius presensi adalah {max_radius} meter."
        )

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    # Logika Jam Masuk / Terlambat (Batas jam 07:15)
    hour = now.hour
    minute = now.minute
    is_late = (hour > 7) or (hour == 7 and minute > 15)
    att_status = "LATE" if is_late else "PRESENT"

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teacher_attendance WHERE user_id = ? AND date = ?", (user["id"], today_str))
    existing = cur.fetchone()

    if not existing:
        att_id = "att_" + secrets.token_hex(6)
        conn.execute("""
            INSERT INTO teacher_attendance (id, institution_id, user_id, date, check_in_time, status, method, distance_meters)
            VALUES (?, ?, ?, ?, ?, ?, 'GPS', ?)
        """, (att_id, user["institution_id"], user["id"], today_str, time_str, att_status, dist))
        conn.commit()
        conn.close()
        msg = f"Presensi Masuk berhasil dicatat ({att_status}) pada jam {time_str} (Jarak: {dist:.1f}m)."
        return {"status": "ok", "message": msg}
    else:
        # Check-out Pulang
        if existing["check_out_time"]:
            conn.close()
            return {"status": "ok", "message": "Anda sudah melakukan presensi pulang sebelumnya."}

        conn.execute("""
            UPDATE teacher_attendance
            SET check_out_time = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (time_str, existing["id"]))
        conn.commit()
        conn.close()
        return {"status": "ok", "message": f"Presensi Pulang berhasil dicatat pada jam {time_str}."}

# API CHECK-IN QR
@app.post("/api/attendance/check-in-qr")
async def api_checkin_qr(payload: QrCheckInRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi telah berakhir.")

    current_token = user["qr_daily_token"]
    if not current_token or payload.token.strip() != current_token.strip():
        raise HTTPException(status_code=400, detail="QR Code tidak valid atau telah diperbarui.")

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    hour = now.hour
    minute = now.minute
    is_late = (hour > 7) or (hour == 7 and minute > 15)
    att_status = "LATE" if is_late else "PRESENT"

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM teacher_attendance WHERE user_id = ? AND date = ?", (user["id"], today_str))
    existing = cur.fetchone()

    if not existing:
        att_id = "att_" + secrets.token_hex(6)
        conn.execute("""
            INSERT INTO teacher_attendance (id, institution_id, user_id, date, check_in_time, status, method, distance_meters)
            VALUES (?, ?, ?, ?, ?, ?, 'QR', 0.0)
        """, (att_id, user["institution_id"], user["id"], today_str, time_str, att_status))
        conn.commit()
        conn.close()
        return {"status": "ok", "message": f"Presensi Masuk via QR berhasil dicatat ({att_status}) pada jam {time_str}."}
    else:
        conn.execute("""
            UPDATE teacher_attendance
            SET check_out_time = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (time_str, existing["id"]))
        conn.commit()
        conn.close()
        return {"status": "ok", "message": f"Presensi Pulang via QR berhasil dicatat pada jam {time_str}."}

# ADMIN: MONITOR & REKAP HARIAN
@app.get("/admin/recap", response_class=HTMLResponse)
async def admin_recap_page(request: Request, date: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if user["role"] != "ADMIN":
        return RedirectResponse(url="/attendance")

    today_str = date or datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cur = conn.cursor()
    # Ambil seluruh guru aktif
    cur.execute("""
        SELECT u.id as user_id, u.full_name, u.username, u.nip,
               a.check_in_time, a.check_out_time, a.status, a.method, a.distance_meters, a.notes
        FROM users u
        LEFT JOIN teacher_attendance a ON u.id = a.user_id AND a.date = ?
        WHERE u.institution_id = ? AND u.role IN ('TEACHER', 'STAFF') AND u.is_active = 1
        ORDER BY u.full_name ASC
    """, (today_str, user["institution_id"]))
    teachers = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Hitung statistik
    total = len(teachers)
    present = sum(1 for t in teachers if t["status"] == "PRESENT")
    late = sum(1 for t in teachers if t["status"] == "LATE")
    absent = total - (present + late + sum(1 for t in teachers if t["status"] in ["SICK", "EXCUSED"]))

    return templates.TemplateResponse(
        request=request,
        name="admin_recap.html",
        context={
            "user": user,
            "active_menu": "recap",
            "selected_date": today_str,
            "teachers": teachers,
            "stats": {
                "total": total,
                "present": present,
                "late": late,
                "absent": max(0, absent)
            }
        }
    )

# ADMIN: QR CODE DISPLAY
@app.get("/admin/qr", response_class=HTMLResponse)
async def admin_qr_page(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM institutions WHERE id = ?", (user["institution_id"],))
    inst = dict(cur.fetchone())
    conn.close()

    today_str = datetime.now().strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request=request,
        name="admin_qr.html",
        context={
            "user": user,
            "active_menu": "qr",
            "today": today_str,
            "institution": inst,
            "token": inst.get("qr_daily_token", "QR-NATA-DEFAULT")
        }
    )

@app.post("/admin/qr/regenerate")
async def admin_regenerate_qr(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    new_token = f"NATA-{secrets.token_hex(4).upper()}"
    conn = get_db()
    conn.execute("UPDATE institutions SET qr_daily_token = ? WHERE id = ?", (new_token, user["institution_id"]))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/qr", status_code=303)

# ADMIN: SETTINGS JAM & GPS SEKOLAH
@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request, success: Optional[int] = 0):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM institutions WHERE id = ?", (user["institution_id"],))
    inst = dict(cur.fetchone())
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="admin_settings.html",
        context={
            "user": user,
            "active_menu": "settings",
            "inst": inst,
            "success": bool(success)
        }
    )

@app.post("/admin/settings")
async def admin_save_settings(
    request: Request,
    work_start_time: str = Form(...),
    late_cutoff_time: str = Form(...),
    work_end_time: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    radius_meters: int = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    conn = get_db()
    conn.execute("""
        UPDATE institutions
        SET work_start_time = ?, late_cutoff_time = ?, work_end_time = ?,
            latitude = ?, longitude = ?, radius_meters = ?
        WHERE id = ?
    """, (work_start_time, late_cutoff_time, work_end_time, latitude, longitude, radius_meters, user["institution_id"]))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/admin/settings?success=1", status_code=303)

# ADMIN: REKAP BULANAN
@app.get("/admin/monthly", response_class=HTMLResponse)
async def admin_monthly_page(request: Request, month: Optional[str] = None):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    # Format month: YYYY-MM
    selected_month = month or datetime.now().strftime("%Y-%m")
    year_str, month_str = selected_month.split("-")
    
    month_names = {
        "01": "Januari", "02": "Februari", "03": "Maret", "04": "April",
        "05": "Mei", "06": "Juni", "07": "Juli", "08": "Agustus",
        "09": "September", "10": "Oktober", "11": "November", "12": "Desember"
    }
    month_name = month_names.get(month_str, "Bulan Ini")

    conn = get_db()
    cur = conn.cursor()
    # Ambil seluruh guru dan agregasi status per bulan
    cur.execute("""
        SELECT u.id as user_id, u.full_name, u.nip,
               COUNT(CASE WHEN a.status = 'PRESENT' THEN 1 END) as present,
               COUNT(CASE WHEN a.status = 'LATE' THEN 1 END) as late,
               COUNT(CASE WHEN a.status = 'SICK' THEN 1 END) as sick,
               COUNT(CASE WHEN a.status = 'EXCUSED' THEN 1 END) as excused,
               COUNT(CASE WHEN a.status = 'ABSENT' THEN 1 END) as absent
        FROM users u
        LEFT JOIN teacher_attendance a ON u.id = a.user_id AND strftime('%Y-%m', a.date) = ?
        WHERE u.institution_id = ? AND u.role IN ('TEACHER', 'STAFF') AND u.is_active = 1
        GROUP BY u.id
        ORDER BY u.full_name ASC
    """, (selected_month, user["institution_id"]))
    
    teachers = [dict(r) for r in cur.fetchall()]
    conn.close()

    total_present = sum(t["present"] for t in teachers)
    total_late = sum(t["late"] for t in teachers)
    total_sick = sum(t["sick"] for t in teachers)
    total_excused = sum(t["excused"] for t in teachers)
    total_absent = sum(t["absent"] for t in teachers)

    return templates.TemplateResponse(
        request=request,
        name="admin_monthly.html",
        context={
            "user": user,
            "active_menu": "monthly",
            "selected_month": selected_month,
            "current_month_name": month_name,
            "current_year": year_str,
            "teachers": teachers,
            "stats": {
                "total_teachers": len(teachers),
                "total_present": total_present,
                "total_late": total_late,
                "total_sick": total_sick,
                "total_excused": total_excused,
                "total_absent": total_absent
            }
        }
    )

# ADMIN: EKSPOR REKAP BULANAN KE CSV
@app.get("/admin/monthly/export")
async def admin_export_monthly_csv(request: Request, month: Optional[str] = None):
    import csv
    import io

    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    selected_month = month or datetime.now().strftime("%Y-%m")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.full_name, u.nip,
               COUNT(CASE WHEN a.status = 'PRESENT' THEN 1 END) as present,
               COUNT(CASE WHEN a.status = 'LATE' THEN 1 END) as late,
               COUNT(CASE WHEN a.status = 'SICK' THEN 1 END) as sick,
               COUNT(CASE WHEN a.status = 'EXCUSED' THEN 1 END) as excused,
               COUNT(CASE WHEN a.status = 'ABSENT' THEN 1 END) as absent
        FROM users u
        LEFT JOIN teacher_attendance a ON u.id = a.user_id AND strftime('%Y-%m', a.date) = ?
        WHERE u.institution_id = ? AND u.role IN ('TEACHER', 'STAFF') AND u.is_active = 1
        GROUP BY u.id
        ORDER BY u.full_name ASC
    """, (selected_month, user["institution_id"]))
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Nama Guru / Staf", "NIP", "Hadir Tepat", "Terlambat", "Sakit", "Izin/Dinas", "Alpa", "Total Kehadiran", "Tingkat Disiplin (%)"])

    for r in rows:
        total_days = r["present"] + r["late"] + r["sick"] + r["excused"] + r["absent"]
        discipline = round((r["present"] / total_days * 100)) if total_days > 0 else 100
        writer.writerow([
            r["full_name"],
            r["nip"] or "-",
            r["present"],
            r["late"],
            r["sick"],
            r["excused"],
            r["absent"],
            total_days,
            f"{discipline}%"
        ])

    csv_data = output.getvalue()
    filename = f"rekap_presensi_guru_{selected_month}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ADMIN: MANUAL ATTENDANCE OVERRIDE
@app.post("/api/admin/manual-attendance")
async def admin_manual_attendance(
    request: Request,
    user_id: str = Form(...),
    date: str = Form(...),
    status: str = Form(...),
    notes: Optional[str] = Form(None)
):
    admin = get_current_user(request)
    if not admin or admin["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM teacher_attendance WHERE user_id = ? AND date = ?", (user_id, date))
    row = cur.fetchone()

    now_time = datetime.now().strftime("%H:%M:%S")
    if row:
        conn.execute("""
            UPDATE teacher_attendance
            SET status = ?, notes = ?, method = 'MANUAL_ADMIN', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, notes, row["id"]))
    else:
        new_id = "att_" + secrets.token_hex(6)
        conn.execute("""
            INSERT INTO teacher_attendance (id, institution_id, user_id, date, check_in_time, status, method, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'MANUAL_ADMIN', ?)
        """, (new_id, admin["institution_id"], user_id, date, now_time, status, notes))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/admin/recap?date={date}", status_code=303)

# GURU: DAFTAR KELAS & JADWAL MENGAJAR
@app.get("/teacher/classes", response_class=HTMLResponse)
async def teacher_classes_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if user["role"] not in ["TEACHER", "ADMIN"]:
        return RedirectResponse(url="/attendance")

    conn = get_db()
    cur = conn.cursor()
    # Jika admin, bisa lihat semua penugasan; jika guru, penugasan miliknya
    if user["role"] == "ADMIN":
        cur.execute("""
            SELECT a.id as assignment_id, c.name as classroom_name, c.level, c.academic_year,
                   s.name as subject_name,
                   (SELECT COUNT(*) FROM students std WHERE std.classroom_id = c.id AND std.is_active = 1) as total_students
            FROM teacher_assignments a
            JOIN classrooms c ON a.classroom_id = c.id
            JOIN subjects s ON a.subject_id = s.id
            WHERE a.institution_id = ?
            ORDER BY c.name ASC
        """, (user["institution_id"],))
    else:
        cur.execute("""
            SELECT a.id as assignment_id, c.name as classroom_name, c.level, c.academic_year,
                   s.name as subject_name,
                   (SELECT COUNT(*) FROM students std WHERE std.classroom_id = c.id AND std.is_active = 1) as total_students
            FROM teacher_assignments a
            JOIN classrooms c ON a.classroom_id = c.id
            JOIN subjects s ON a.subject_id = s.id
            WHERE a.institution_id = ? AND a.teacher_id = ?
            ORDER BY c.name ASC
        """, (user["institution_id"], user["id"]))

    classes = [dict(r) for r in cur.fetchall()]
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="teacher_classes.html",
        context={
            "user": user,
            "active_menu": "classes",
            "classes": classes
        }
    )

# GURU: LEMBAR ABSENSI SANTRI DALAM KELAS
@app.get("/teacher/attendance/{assignment_id}", response_class=HTMLResponse)
async def teacher_student_attendance_page(request: Request, assignment_id: str, date: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    today_str = date or datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cur = conn.cursor()

    # Ambil detail assignment
    cur.execute("""
        SELECT a.id, a.classroom_id, c.name as classroom_name, s.name as subject_name
        FROM teacher_assignments a
        JOIN classrooms c ON a.classroom_id = c.id
        JOIN subjects s ON a.subject_id = s.id
        WHERE a.id = ? AND a.institution_id = ?
    """, (assignment_id, user["institution_id"]))
    assignment = cur.fetchone()
    if not assignment:
        conn.close()
        raise HTTPException(status_code=404, detail="Jadwal mengajar tidak ditemukan.")
    assignment = dict(assignment)

    # Ambil atau buat session absensi hari ini
    cur.execute("""
        SELECT * FROM student_attendance_sessions
        WHERE assignment_id = ? AND date = ?
    """, (assignment_id, today_str))
    session_row = cur.fetchone()

    if not session_row:
        session_id = "sess_" + secrets.token_hex(6)
        conn.execute("""
            INSERT INTO student_attendance_sessions (id, institution_id, assignment_id, date, status)
            VALUES (?, ?, ?, ?, 'OPEN')
        """, (session_id, user["institution_id"], assignment_id, today_str))
        conn.commit()
        cur.execute("SELECT * FROM student_attendance_sessions WHERE id = ?", (session_id,))
        session_row = cur.fetchone()

    session_row = dict(session_row)

    # Ambil seluruh santri di kelas beserta status absensi sesi ini
    cur.execute("""
        SELECT s.id as student_id, s.full_name, s.nisn, s.gender,
               r.status, r.notes
        FROM students s
        LEFT JOIN student_attendance_records r ON s.id = r.student_id AND r.session_id = ?
        WHERE s.classroom_id = ? AND s.is_active = 1
        ORDER BY s.full_name ASC
    """, (session_row["id"], assignment["classroom_id"]))
    roster = [dict(r) for r in cur.fetchall()]
    conn.close()

    total = len(roster)
    present = sum(1 for r in roster if r["status"] == "PRESENT")
    sick = sum(1 for r in roster if r["status"] == "SICK")
    excused = sum(1 for r in roster if r["status"] == "EXCUSED")
    absent = sum(1 for r in roster if r["status"] == "ABSENT")

    return templates.TemplateResponse(
        request=request,
        name="teacher_student_attendance.html",
        context={
            "user": user,
            "active_menu": "classes",
            "assignment": assignment,
            "session": session_row,
            "roster": roster,
            "stats": {
                "total": total,
                "present": present,
                "sick": sick,
                "excused": excused,
                "absent": absent
            }
        }
    )

# API: UPDATE STATUS SANTRI DARI PANEL SAMPING
@app.post("/api/teacher/attendance/update-record")
async def api_update_student_record(
    request: Request,
    session_id: str = Form(...),
    assignment_id: str = Form(...),
    student_id: str = Form(...),
    status: str = Form(...),
    notes: Optional[str] = Form(None)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Harap login.")

    conn = get_db()
    cur = conn.cursor()
    # Cek apakah sesi masih OPEN
    cur.execute("SELECT status FROM student_attendance_sessions WHERE id = ?", (session_id,))
    s_row = cur.fetchone()
    if not s_row or s_row["status"] == "CLOSED":
        conn.close()
        raise HTTPException(status_code=400, detail="Sesi absensi sudah dikunci.")

    cur.execute("SELECT id FROM student_attendance_records WHERE session_id = ? AND student_id = ?", (session_id, student_id))
    rec = cur.fetchone()

    if rec:
        conn.execute("""
            UPDATE student_attendance_records
            SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, notes, rec["id"]))
    else:
        rec_id = "rec_" + secrets.token_hex(6)
        conn.execute("""
            INSERT INTO student_attendance_records (id, session_id, student_id, status, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (rec_id, session_id, student_id, status, notes))

    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/teacher/attendance/{assignment_id}", status_code=303)

# API: TANDAI SEMUA HADIR 1-KLIK
@app.post("/api/teacher/attendance/mark-all-present")
async def api_mark_all_present(
    request: Request,
    session_id: str = Form(...),
    assignment_id: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Harap login.")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT status FROM student_attendance_sessions WHERE id = ?", (session_id,))
    s_row = cur.fetchone()
    if not s_row or s_row["status"] == "CLOSED":
        conn.close()
        raise HTTPException(status_code=400, detail="Sesi absensi sudah dikunci.")

    # Ambil semua student_id di assignment ini
    cur.execute("""
        SELECT s.id FROM students s
        JOIN teacher_assignments a ON s.classroom_id = a.classroom_id
        WHERE a.id = ? AND s.is_active = 1
    """, (assignment_id,))
    student_ids = [r[0] for r in cur.fetchall()]

    for sid in student_ids:
        cur.execute("SELECT id FROM student_attendance_records WHERE session_id = ? AND student_id = ?", (session_id, sid))
        rec = cur.fetchone()
        if rec:
            conn.execute("UPDATE student_attendance_records SET status = 'PRESENT', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rec["id"],))
        else:
            rec_id = "rec_" + secrets.token_hex(6)
            conn.execute("""
                INSERT INTO student_attendance_records (id, session_id, student_id, status)
                VALUES (?, ?, ?, 'PRESENT')
            """, (rec_id, session_id, sid))

    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/teacher/attendance/{assignment_id}", status_code=303)

# API: KUNCI SESI KELAS
@app.post("/api/teacher/attendance/close-session")
async def api_close_session(
    request: Request,
    session_id: str = Form(...),
    assignment_id: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Harap login.")

    conn = get_db()
    conn.execute("UPDATE student_attendance_sessions SET status = 'CLOSED' WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    return RedirectResponse(url=f"/teacher/attendance/{assignment_id}", status_code=303)

# ADMIN: MASTER DATA (SANTRI, KELAS, GURU, IMPORT CSV)
@app.get("/admin/master", response_class=HTMLResponse)
async def admin_master_page(request: Request, classroom_id: Optional[str] = None, message: Optional[str] = None):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        return RedirectResponse(url="/login")

    conn = get_db()
    cur = conn.cursor()

    # Daftar Kelas
    cur.execute("SELECT * FROM classrooms WHERE institution_id = ? ORDER BY level ASC, name ASC", (user["institution_id"],))
    classrooms = cur.fetchall()

    # Query Santri (bisa difilter per kelas)
    if classroom_id:
        cur.execute("""
            SELECT s.*, c.name as classroom_name
            FROM students s
            JOIN classrooms c ON s.classroom_id = c.id
            WHERE s.institution_id = ? AND s.classroom_id = ?
            ORDER BY s.full_name ASC
        """, (user["institution_id"], classroom_id))
    else:
        cur.execute("""
            SELECT s.*, c.name as classroom_name
            FROM students s
            JOIN classrooms c ON s.classroom_id = c.id
            WHERE s.institution_id = ?
            ORDER BY c.level ASC, s.full_name ASC
        """, (user["institution_id"],))
    students = cur.fetchall()

    # Statistik master data
    cur.execute("SELECT COUNT(*) FROM students WHERE institution_id = ? AND is_active = 1", (user["institution_id"],))
    total_students = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM classrooms WHERE institution_id = ?", (user["institution_id"],))
    total_classrooms = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE institution_id = ? AND role IN ('TEACHER', 'STAFF')", (user["institution_id"],))
    total_teachers = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM teacher_assignments WHERE institution_id = ?", (user["institution_id"],))
    total_assignments = cur.fetchone()[0]

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="admin_master.html",
        context={
            "user": user,
            "active_menu": "master",
            "classrooms": classrooms,
            "students": students,
            "selected_classroom_id": classroom_id,
            "message": message,
            "stats": {
                "total_students": total_students,
                "total_classrooms": total_classrooms,
                "total_teachers": total_teachers,
                "total_assignments": total_assignments
            }
        }
    )

# UNDUH TEMPLATE CSV SANTRI
@app.get("/admin/master/template-students")
async def download_student_csv_template():
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nisn", "nama", "kelas", "gender"])
    writer.writerow(["NIS-2026-001", "Muhammad Farhan", "Kelas 10-A (IPA & Tahfidz)", "L"])
    writer.writerow(["NIS-2026-002", "Salma Khairunnisa", "Kelas 10-A (IPA & Tahfidz)", "P"])
    csv_text = output.getvalue()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="template_import_santri.csv"'}
    )

# SIMPAN / EDIT SANTRI DARI PANEL SAMPING
@app.post("/admin/master/save-student")
async def admin_save_student(
    request: Request,
    student_id: Optional[str] = Form(None),
    full_name: str = Form(...),
    nisn: Optional[str] = Form(None),
    classroom_id: str = Form(...),
    gender: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    conn = get_db()
    cur = conn.cursor()

    if student_id and student_id.strip():
        # Update
        cur.execute("""
            UPDATE students
            SET full_name = ?, nisn = ?, classroom_id = ?, gender = ?
            WHERE id = ? AND institution_id = ?
        """, (full_name.strip(), nisn.strip() if nisn else None, classroom_id, gender, student_id.strip(), user["institution_id"]))
        msg = f"Data santri {full_name} berhasil diperbarui."
    else:
        # Create
        new_sid = "std_" + secrets.token_hex(6)
        cur.execute("""
            INSERT INTO students (id, institution_id, classroom_id, full_name, nisn, gender)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (new_sid, user["institution_id"], classroom_id, full_name.strip(), nisn.strip() if nisn else None, gender))
        msg = f"Santri {full_name} berhasil ditambahkan ke daftar."

    conn.commit()
    conn.close()

    import urllib.parse
    encoded_msg = urllib.parse.quote(msg)
    return RedirectResponse(url=f"/admin/master?message={encoded_msg}", status_code=303)

# HAPUS SANTRI
@app.post("/admin/master/delete-student")
async def admin_delete_student(request: Request, student_id: str = Form(...)):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    conn = get_db()
    conn.execute("DELETE FROM students WHERE id = ? AND institution_id = ?", (student_id, user["institution_id"]))
    conn.commit()
    conn.close()

    import urllib.parse
    msg = urllib.parse.quote("Data santri berhasil dihapus.")
    return RedirectResponse(url=f"/admin/master?message={msg}", status_code=303)

# TAMBAH ROMBEL KELAS BARU
@app.post("/admin/master/create-classroom")
async def admin_create_classroom(
    request: Request,
    name: str = Form(...),
    level: str = Form(...),
    academic_year: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    conn = get_db()
    cls_id = "cls_" + secrets.token_hex(5)
    conn.execute("""
        INSERT INTO classrooms (id, institution_id, name, level, academic_year)
        VALUES (?, ?, ?, ?, ?)
    """, (cls_id, user["institution_id"], name.strip(), level.strip(), academic_year.strip()))
    conn.commit()
    conn.close()

    import urllib.parse
    msg = urllib.parse.quote(f"Kelas baru {name} berhasil dibuat.")
    return RedirectResponse(url=f"/admin/master?message={msg}", status_code=303)

# TAMBAH GURU BARU CEPAT
@app.post("/admin/master/create-teacher")
async def admin_create_teacher(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...)
):
    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    conn = get_db()
    cur = conn.cursor()
    # Cek username
    cur.execute("SELECT id FROM users WHERE username = ?", (username.strip(),))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username sudah dipakai.")

    teacher_id = "usr_" + secrets.token_hex(5)
    pw_hash = hashlib.sha256(password.strip().encode()).hexdigest()
    cur.execute("""
        INSERT INTO users (id, institution_id, username, password_hash, full_name, role)
        VALUES (?, ?, ?, ?, ?, 'TEACHER')
    """, (teacher_id, user["institution_id"], username.strip(), pw_hash, full_name.strip()))
    conn.commit()
    conn.close()

    import urllib.parse
    msg = urllib.parse.quote(f"Akun guru {full_name} ({username}) berhasil didaftarkan.")
    return RedirectResponse(url=f"/admin/master?message={msg}", status_code=303)

# IMPORT MASSAL SANTRI DARI BERKAS CSV
@app.post("/admin/master/import-students-csv")
async def admin_import_students_csv(request: Request, file: UploadFile = File(...)):
    import csv, io, urllib.parse

    user = get_current_user(request)
    if not user or user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    content = await file.read()
    decoded = content.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(decoded))

    conn = get_db()
    cur = conn.cursor()

    # Cache nama kelas -> ID
    cur.execute("SELECT id, name FROM classrooms WHERE institution_id = ?", (user["institution_id"],))
    cls_map = {r["name"].strip().lower(): r["id"] for r in cur.fetchall()}

    # Jika belum ada kelas sama sekali, buat kelas default
    if not cls_map:
        default_cls_id = "cls_default"
        cur.execute("INSERT INTO classrooms (id, institution_id, name, level) VALUES (?, ?, 'Kelas Umum', '10')", (default_cls_id, user["institution_id"]))
        cls_map["kelas umum"] = default_cls_id

    imported_count = 0
    for row in reader:
        # Bersihkan keys dan value (case-insensitive)
        clean_row = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items() if k}
        nama = clean_row.get("nama") or clean_row.get("nama lengkap") or clean_row.get("name")
        if not nama:
            continue

        nisn = clean_row.get("nisn") or clean_row.get("nis") or None
        kelas_name = clean_row.get("kelas") or clean_row.get("rombel") or ""
        gender = (clean_row.get("gender") or clean_row.get("l/p") or "L").upper()
        if gender not in ["L", "P"]:
            gender = "L"

        target_cls_id = None
        if kelas_name and kelas_name.strip().lower() in cls_map:
            target_cls_id = cls_map[kelas_name.strip().lower()]
        else:
            # Ambil kelas pertama yang tersedia
            target_cls_id = list(cls_map.values())[0]

        student_id = "std_" + secrets.token_hex(6)
        cur.execute("""
            INSERT INTO students (id, institution_id, classroom_id, full_name, nisn, gender)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, user["institution_id"], target_cls_id, nama, nisn, gender))
        imported_count += 1

    conn.commit()
    conn.close()

    msg = urllib.parse.quote(f"Berhasil mengimpor {imported_count} santri dari berkas CSV.")
    return RedirectResponse(url=f"/admin/master?message={msg}", status_code=303)

# FASE 4: MODUL NILAI SISWA & RAPOR CETAK MANDIRI

@app.get("/teacher/grades", response_class=HTMLResponse)
async def teacher_grades_page(
    request: Request,
    assignment_id: Optional[str] = None,
    semester: str = "Ganjil",
    academic_year: str = "2026/2027",
    message: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    if user["role"] not in ["TEACHER", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Akses ditolak")
    
    conn = get_db()
    # Jika assignment_id tidak diberikan, ambil penugasan pertama guru
    if not assignment_id:
        if user["role"] == "TEACHER":
            first_asg = conn.execute("SELECT id FROM teacher_assignments WHERE teacher_id = ? LIMIT 1", (user["id"],)).fetchone()
        else:
            first_asg = conn.execute("SELECT id FROM teacher_assignments LIMIT 1").fetchone()
        if first_asg:
            assignment_id = first_asg["id"]
        else:
            return RedirectResponse(url="/teacher/classes", status_code=status.HTTP_303_SEE_OTHER)

    # Ambil detail assignment
    asg = conn.execute("""
        SELECT ta.id, ta.classroom_id, ta.subject_id,
               c.name as classroom_name, c.level, c.academic_year,
               s.name as subject_name, s.code as subject_code
        FROM teacher_assignments ta
        JOIN classrooms c ON ta.classroom_id = c.id
        JOIN subjects s ON ta.subject_id = s.id
        WHERE ta.id = ?
    """, (assignment_id,)).fetchone()

    if not asg:
        raise HTTPException(status_code=404, detail="Jadwal penugasan tidak ditemukan")

    # Ambil daftar santri di kelas ini dan nilai mereka jika ada
    students_rows = conn.execute("""
        SELECT st.id, st.full_name, st.nisn, st.gender,
               COALESCE(sg.score_formative, 0) as score_formative,
               COALESCE(sg.score_midterm, 0) as score_midterm,
               COALESCE(sg.score_final, 0) as score_final,
               COALESCE(sg.final_grade, 0) as final_grade,
               COALESCE(sg.predicate, 'B') as predicate,
               COALESCE(sg.teacher_notes, '') as teacher_notes
        FROM students st
        LEFT JOIN student_grades sg ON st.id = sg.student_id 
             AND sg.subject_id = ? AND sg.semester = ? AND sg.academic_year = ?
        WHERE st.classroom_id = ? AND st.is_active = 1
        ORDER BY st.full_name ASC
    """, (asg["subject_id"], semester, academic_year, asg["classroom_id"])).fetchall()

    return templates.TemplateResponse(
        request=request,
        name="teacher_grades.html",
        context={
            "user": user,
            "active_menu": "grades",
            "assignment": asg,
            "students": students_rows,
            "semester": semester,
            "academic_year": academic_year,
            "message": message
        }
    )

@app.post("/teacher/grades/save-batch")
async def teacher_save_grades_batch(
    request: Request,
    assignment_id: str = Form(...),
    semester: str = Form("Ganjil"),
    academic_year: str = Form("2026/2027"),
    user: Dict = Depends(get_current_user)
):
    if user["role"] not in ["TEACHER", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    form_data = await request.form()
    student_ids = form_data.getlist("student_id")

    conn = get_db()
    asg = conn.execute("SELECT classroom_id, subject_id, institution_id FROM teacher_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not asg:
        raise HTTPException(status_code=404, detail="Jadwal kelas tidak ditemukan")

    for sid in student_ids:
        try:
            f_score = float(form_data.get(f"score_formative_{sid}", 0))
            m_score = float(form_data.get(f"score_midterm_{sid}", 0))
            e_score = float(form_data.get(f"score_final_{sid}", 0))
        except ValueError:
            f_score, m_score, e_score = 0.0, 0.0, 0.0

        notes = str(form_data.get(f"notes_{sid}", "")).strip()

        # Rumus bobot nilai akhir: 40% Formatif + 30% PTS + 30% PAS
        final_grade = round((f_score * 0.40) + (m_score * 0.30) + (e_score * 0.30), 1)

        # Hitung predikat nilai
        if final_grade >= 88:
            predicate = "A"
        elif final_grade >= 75:
            predicate = "B"
        elif final_grade >= 60:
            predicate = "C"
        else:
            predicate = "D"

        # Simpan atau update ke database (UPSERT)
        conn.execute("""
            INSERT INTO student_grades (
                id, institution_id, student_id, subject_id, semester, academic_year,
                score_formative, score_midterm, score_final, final_grade, predicate, teacher_notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id, subject_id, semester, academic_year) DO UPDATE SET
                score_formative = excluded.score_formative,
                score_midterm = excluded.score_midterm,
                score_final = excluded.score_final,
                final_grade = excluded.final_grade,
                predicate = excluded.predicate,
                teacher_notes = excluded.teacher_notes,
                updated_at = CURRENT_TIMESTAMP
        """, (
            str(uuid.uuid4()), asg["institution_id"], sid, asg["subject_id"], semester, academic_year,
            f_score, m_score, e_score, final_grade, predicate, notes
        ))

    conn.commit()
    return RedirectResponse(
        url=f"/teacher/grades?assignment_id={assignment_id}&semester={semester}&academic_year={academic_year}&message=Nilai+seluruh+siswa+berhasil+disimpan",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.get("/teacher/grades/export-csv")
async def teacher_export_grades_csv(
    assignment_id: str,
    semester: str = "Ganjil",
    academic_year: str = "2026/2027",
    user: Dict = Depends(get_current_user)
):
    conn = get_db()
    asg = conn.execute("""
        SELECT c.name as classroom_name, s.name as subject_name
        FROM teacher_assignments ta
        JOIN classrooms c ON ta.classroom_id = c.id
        JOIN subjects s ON ta.subject_id = s.id
        WHERE ta.id = ?
    """, (assignment_id,)).fetchone()

    rows = conn.execute("""
        SELECT st.nisn, st.full_name, st.gender,
               COALESCE(sg.score_formative, 0) as formative,
               COALESCE(sg.score_midterm, 0) as midterm,
               COALESCE(sg.score_final, 0) as final_exam,
               COALESCE(sg.final_grade, 0) as final_grade,
               COALESCE(sg.predicate, '-') as predicate,
               COALESCE(sg.teacher_notes, '') as notes
        FROM students st
        JOIN teacher_assignments ta ON st.classroom_id = ta.classroom_id
        LEFT JOIN student_grades sg ON st.id = sg.student_id 
             AND sg.subject_id = ta.subject_id AND sg.semester = ? AND sg.academic_year = ?
        WHERE ta.id = ? AND st.is_active = 1
        ORDER BY st.full_name ASC
    """, (semester, academic_year, assignment_id)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["NO", "NISN", "NAMA SISWA", "GENDER", "FORMATIF (40%)", "PTS (30%)", "PAS (30%)", "NILAI AKHIR", "PREDIKAT", "CATATAN"])
    for idx, r in enumerate(rows, start=1):
        writer.writerow([idx, r["nisn"] or "", r["full_name"], r["gender"] or "", r["formative"], r["midterm"], r["final_exam"], r["final_grade"], r["predicate"], r["notes"]])

    filename = f"Leger_Nilai_{asg['classroom_name']}_{asg['subject_name']}_{semester}.csv".replace(" ", "_")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/admin/grades", response_class=HTMLResponse)
async def admin_grades_page(
    request: Request,
    classroom_id: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    if user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses khusus Administrator")

    conn = get_db()
    classrooms = conn.execute("SELECT id, name, level, academic_year FROM classrooms ORDER BY level, name").fetchall()
    
    if not classroom_id and classrooms:
        classroom_id = classrooms[0]["id"]

    # Ambil daftar santri & rata-rata nilai rapor mereka
    students = []
    if classroom_id:
        students = conn.execute("""
            SELECT st.id, st.full_name, st.nisn, st.gender, c.name as classroom_name,
                   ROUND(AVG(sg.final_grade), 1) as avg_grade,
                   CASE 
                     WHEN AVG(sg.final_grade) >= 88 THEN 'A'
                     WHEN AVG(sg.final_grade) >= 75 THEN 'B'
                     WHEN AVG(sg.final_grade) >= 60 THEN 'C'
                     WHEN AVG(sg.final_grade) > 0 THEN 'D'
                     ELSE '-'
                   END as overall_predicate
            FROM students st
            JOIN classrooms c ON st.classroom_id = c.id
            LEFT JOIN student_grades sg ON st.id = sg.student_id
            WHERE st.classroom_id = ? AND st.is_active = 1
            GROUP BY st.id
            ORDER BY st.full_name ASC
        """, (classroom_id,)).fetchall()

    # Statistik global
    stat_graded = conn.execute("SELECT COUNT(DISTINCT student_id) FROM student_grades").fetchone()[0]
    avg_score = conn.execute("SELECT ROUND(AVG(final_grade), 1) FROM student_grades").fetchone()[0] or 0.0
    passed_count = conn.execute("SELECT COUNT(*) FROM student_grades WHERE final_grade >= 75").fetchone()[0]

    return templates.TemplateResponse(
        request=request,
        name="admin_grades.html",
        context={
            "user": user,
            "active_menu": "grades",
            "classrooms": classrooms,
            "selected_classroom_id": classroom_id,
            "students": students,
            "stats": {
                "graded_students": stat_graded,
                "avg_school_score": avg_score,
                "total_passed": passed_count
            }
        }
    )

@app.get("/admin/grades/export-classroom-csv")
async def admin_export_classroom_grades_csv(
    classroom_id: str,
    user: Dict = Depends(get_current_user)
):
    if user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Akses khusus Administrator")

    conn = get_db()
    c = conn.execute("SELECT name FROM classrooms WHERE id = ?", (classroom_id,)).fetchone()
    c_name = c["name"] if c else "Kelas"

    students = conn.execute("""
        SELECT st.nisn, st.full_name, st.gender,
               ROUND(AVG(sg.final_grade), 1) as avg_score,
               CASE 
                 WHEN AVG(sg.final_grade) >= 88 THEN 'A'
                 WHEN AVG(sg.final_grade) >= 75 THEN 'B'
                 WHEN AVG(sg.final_grade) >= 60 THEN 'C'
                 ELSE 'D'
               END as predicate
        FROM students st
        LEFT JOIN student_grades sg ON st.id = sg.student_id
        WHERE st.classroom_id = ? AND st.is_active = 1
        GROUP BY st.id
        ORDER BY st.full_name ASC
    """, (classroom_id,)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["NO", "NISN", "NAMA LENGKAP SISWA", "GENDER", "RATA-RATA NILAI", "PREDIKAT AKHIR"])
    for idx, s in enumerate(students, start=1):
        writer.writerow([idx, s["nisn"] or "", s["full_name"], s["gender"] or "", s["avg_score"] or 0, s["predicate"]])

    filename = f"Leger_Nilai_Rombel_{c_name}.csv".replace(" ", "_")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/report-card/{student_id}", response_class=HTMLResponse)
async def student_report_card_page(
    request: Request,
    student_id: str,
    semester: str = "Ganjil",
    academic_year: str = "2026/2027",
    user: Dict = Depends(get_current_user)
):
    conn = get_db()
    student = conn.execute("""
        SELECT st.id, st.full_name, st.nisn, st.gender, c.name as classroom_name, st.institution_id
        FROM students st
        JOIN classrooms c ON st.classroom_id = c.id
        WHERE st.id = ?
    """, (student_id,)).fetchone()

    if not student:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    inst = conn.execute("SELECT id, name, code FROM institutions WHERE id = ?", (student["institution_id"],)).fetchone()

    # Ambil nilai per mata pelajaran
    grades = conn.execute("""
        SELECT s.name as subject_name,
               sg.score_formative, sg.score_midterm, sg.score_final,
               sg.final_grade, sg.predicate, sg.teacher_notes
        FROM student_grades sg
        JOIN subjects s ON sg.subject_id = s.id
        WHERE sg.student_id = ? AND sg.semester = ? AND sg.academic_year = ?
        ORDER BY s.name ASC
    """, (student_id, semester, academic_year)).fetchall()

    avg_score = 0.0
    overall_predicate = "-"
    if grades:
        avg_score = round(sum(g["final_grade"] for g in grades) / len(grades), 1)
        if avg_score >= 88:
            overall_predicate = "A"
        elif avg_score >= 75:
            overall_predicate = "B"
        elif avg_score >= 60:
            overall_predicate = "C"
        else:
            overall_predicate = "D"

    # Rekap absensi santri di kelas
    att_present = conn.execute("""
        SELECT COUNT(*) FROM student_attendance_records WHERE student_id = ? AND status = 'PRESENT'
    """, (student_id,)).fetchone()[0]
    att_sick = conn.execute("""
        SELECT COUNT(*) FROM student_attendance_records WHERE student_id = ? AND status = 'SICK'
    """, (student_id,)).fetchone()[0]
    att_excused = conn.execute("""
        SELECT COUNT(*) FROM student_attendance_records WHERE student_id = ? AND status = 'EXCUSED'
    """, (student_id,)).fetchone()[0]
    att_absent = conn.execute("""
        SELECT COUNT(*) FROM student_attendance_records WHERE student_id = ? AND status = 'ABSENT'
    """, (student_id,)).fetchone()[0]

    today_str = datetime.now().strftime("%d %B %Y")

    return templates.TemplateResponse(
        request=request,
        name="student_report_card.html",
        context={
            "student": student,
            "institution": inst,
            "grades": grades,
            "semester": semester,
            "academic_year": academic_year,
            "avg_score": avg_score,
            "overall_predicate": overall_predicate,
            "attendance": {
                "present": att_present,
                "sick": att_sick,
                "excused": att_excused,
                "absent": att_absent
            },
            "today_date": today_str
        }
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
