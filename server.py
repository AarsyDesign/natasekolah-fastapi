import os
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
