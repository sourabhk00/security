import math
import statistics
import json
import io
import csv
import os
import sqlite3
import time
from datetime import datetime, timezone, date, timedelta
from functools import wraps
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
from webauthn import (base64url_to_bytes, generate_authentication_options,
                      generate_registration_options, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (AuthenticatorAttachment,
                                      AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      UserVerificationRequirement)
from werkzeug.security import check_password_hash, generate_password_hash
from PIL import Image, ImageOps, UnidentifiedImageError
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ATTENDANCE_DB", os.path.join(BASE_DIR, "attendance.db"))
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 30_000_000
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this-before-deployment")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    WTF_CSRF_TIME_LIMIT=None,
    WTF_CSRF_SSL_STRICT=False,
)
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    app.logger.warning("CSRF validation notice: %s on %s", e.description, request.path)
    if request.path.startswith("/api/"):
        return jsonify(ok=False, message="Security session updated. Please try again."), 400
    flash("Your security session was refreshed. Please try logging in again.", "warning")
    return redirect(request.referrer or url_for("login"))


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(db, actor_id, action, entity, entity_id=None, details=""):
    db.execute("INSERT INTO audit_log(actor_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
               (actor_id, action, entity, entity_id, details[:500], now()))


def webauthn_settings():
    origin = os.environ.get("WEBAUTHN_ORIGIN", request.host_url.rstrip("/"))
    rp_id = os.environ.get("WEBAUTHN_RP_ID", urlsplit(origin).hostname or "localhost")
    return rp_id, origin


def store_webauthn_challenge(action, challenge):
    session["webauthn_challenge"] = {
        "action": action,
        "challenge": bytes_to_base64url(challenge),
        "issued_at": int(time.time()),
    }


def take_webauthn_challenge(action):
    record = session.pop("webauthn_challenge", None)
    if not record or record.get("action") != action or int(time.time()) - record.get("issued_at", 0) > 300:
        return None
    return base64url_to_bytes(record["challenge"])


def consume_biometric_authorization(action):
    proof = session.pop("biometric_authorization", None)
    return bool(proof and proof.get("action") == action and int(time.time()) - proof.get("issued_at", 0) <= 180)


def consume_face_authorization(action):
    proof = session.pop("face_authorization", None)
    return bool(proof and proof.get("action") == action and int(time.time()) - proof.get("issued_at", 0) <= 180)


def worker_requires_face_match(user_id):
    db = connect_db()
    row = db.execute("SELECT face_consent FROM employee_profiles WHERE user_id=?", (user_id,)).fetchone()
    db.close()
    return bool(row and row["face_consent"])


def extract_face(image_bytes):
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("The camera image could not be read.")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if len(faces) != 1:
        raise ValueError("Show one clear face in the camera and try again.")
    x, y, width, height = faces[0]
    crop = cv2.resize(gray[y:y + height, x:x + width], (128, 128), interpolation=cv2.INTER_AREA)
    return cv2.equalizeHist(crop)


def face_match_distance(reference_bytes, live_bytes):
    reference = extract_face(reference_bytes)
    live = extract_face(live_bytes)
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train([reference], np.asarray([1], dtype=np.int32))
    _, distance = recognizer.predict(live)
    return distance


def webauthn_origin_error():
    rp_id, origin = webauthn_settings()
    if not os.environ.get("WEBAUTHN_ORIGIN") and urlsplit(origin).hostname in {"127.0.0.1", "::1"}:
        return "Open this app using http://localhost:5000 for device verification. Phones on another device require an HTTPS address."
    return None


def init_db():
    db = connect_db()
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as schema_file:
        db.executescript(schema_file.read())
    # Keep databases created by earlier Fieldguard drafts usable.
    migrations = {
        "sites": {"map_url": "TEXT", "capacity": "INTEGER NOT NULL DEFAULT 0"},
        "attendance": {"marked_by": "INTEGER REFERENCES users(id)"},
        "alerts": {
            "acknowledged_by": "INTEGER REFERENCES users(id)",
            "acknowledged_at": "TEXT",
        },
        "employee_profiles": {
            "photo_jpeg": "BLOB",
            "face_consent": "INTEGER NOT NULL DEFAULT 0",
            "site_id": "INTEGER REFERENCES sites(id)",
        },
        "shift_schedules": {
            "confirmation_status": "TEXT NOT NULL DEFAULT 'pending'",
            "worker_response_at": "TEXT",
            "worker_note": "TEXT NOT NULL DEFAULT ''",
        },
        "site_staffing_targets": {
            "start_time": "TEXT NOT NULL DEFAULT '08:00'",
            "end_time": "TEXT NOT NULL DEFAULT '16:00'",
            "required_credential": "TEXT NOT NULL DEFAULT ''",
        },
    }
    for table, columns in migrations.items():
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        db.execute("INSERT INTO users(name,username,password_hash,role) VALUES(?,?,?,?)",
                   ("Aarav Manager", "manager", generate_password_hash("manager123"), "manager"))
        db.execute("INSERT INTO users(name,username,password_hash,role) VALUES(?,?,?,?)",
                   ("Riya Sharma", "worker", generate_password_hash("worker123"), "worker"))
        db.execute("INSERT INTO sites(name,address,latitude,longitude,radius_m,map_url) VALUES(?,?,?,?,?,?)",
                   ("Amba Shakti Udyog Ltd", "Plot 44 & 47, Industrial Area, A.B. Road, Banmore, Morena, Madhya Pradesh", 26.3733, 78.0807, 300, "https://maps.app.goo.gl/UcW1S8RifwhRNcf47"))
    # Upgrade the original demo site in place and add the employer's three supplied sites.
    db.execute("UPDATE sites SET name=?,address=?,latitude=?,longitude=?,radius_m=?,map_url=? WHERE name='North Yard'",
               ("Amba Shakti Udyog Ltd", "Plot 44 & 47, Industrial Area, A.B. Road, Banmore, Morena, Madhya Pradesh", 26.3733, 78.0807, 300, "https://maps.app.goo.gl/UcW1S8RifwhRNcf47"))
    # The shared Rajawat map pin and Nexaryon Tower coordinates refer to two places.
    db.execute("UPDATE sites SET name=?,address=?,latitude=?,longitude=?,radius_m=?,map_url=? WHERE name IN ('Rajawat House - Nexaryon Tower','Rajawat House','Rajawat House Gwalior')",
               ("Rajawat House Gwalior", "Rajawat House, Gwalior, Madhya Pradesh", 26.2726962, 78.1364943, 300, "https://www.google.com/maps?q=26.2726962,78.1364943&z=17&hl=en"))
    supplied_sites = [
        ("Amba Shakti Udyog Ltd", "Plot 44 & 47, Industrial Area, A.B. Road, Banmore, Morena, Madhya Pradesh", 26.3733, 78.0807, 300, "https://maps.app.goo.gl/UcW1S8RifwhRNcf47"),
        ("B R Oil Industries Pvt Ltd", "Bamor, Banmore, Morena, Madhya Pradesh", 26.3690, 78.0830, 300, "https://maps.app.goo.gl/PwVFC8H58shiDWhk6"),
        ("Vrindavan Industries", "C-20, Industrial Area, Banmore, Morena, Madhya Pradesh", 26.3670, 78.0850, 300, "https://maps.app.goo.gl/7W16rK577ajtLQJc7"),
        ("Rajawat House Gwalior", "Rajawat House, Gwalior, Madhya Pradesh", 26.2726962, 78.1364943, 300, "https://www.google.com/maps?q=26.2726962,78.1364943&z=17&hl=en"),
        ("Nexaryon Tower", "Nexaryon Tower, Raipur, Chhattisgarh, 21.2482, 81.6049", 21.2482, 81.6049, 300, "https://www.google.com/maps/search/?api=1&query=21.2482%2C81.6049"),
        ("Office", "Chauhan Man Power Supply office, 21.2506944, 81.6057500", 21.2506944, 81.6057500, 100, "https://www.google.com/maps/search/?api=1&query=21.2506944%2C81.60575"),
    ]
    for name, address, lat, lng, radius, map_url in supplied_sites:
        db.execute("INSERT INTO sites(name,address,latitude,longitude,radius_m,map_url) SELECT ?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM sites WHERE name=?)",
                   (name, address, lat, lng, radius, map_url, name))
    # Keep legacy site rows for attendance history, but do not offer them for new shifts.
    db.execute("UPDATE sites SET active=0 WHERE lower(name) IN ('nexaryon tower','office')")
    db.commit()
    db.close()


def auth(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


@app.before_request
def load_user():
    g.user = None
    if session.get("user_id"):
        db = connect_db()
        g.user = db.execute("SELECT * FROM users WHERE id=? AND active=1", (session["user_id"],)).fetchone()
        db.close()
        if not g.user:
            session.clear()


def distance_m(lat1, lon1, lat2, lon2):
    earth = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return earth * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def rank_shift_candidates(db, site_id, work_day, start_time, end_time, credential=""):
    """Explainable eligibility gates plus a bounded 0-100 workforce-fit score."""
    day = date.fromisoformat(work_day)
    candidates = db.execute("SELECT u.id,u.name,ep.site_id FROM users u LEFT JOIN employee_profiles ep ON ep.user_id=u.id WHERE u.role='worker' AND u.active=1 ORDER BY u.name").fetchall()
    site_name = db.execute("SELECT name FROM sites WHERE id=? AND active=1", (site_id,)).fetchone()
    if not site_name:
        return [], len(candidates)
    results, excluded = [], 0
    target_start = datetime.combine(day, datetime.strptime(start_time, "%H:%M").time())
    target_end = datetime.combine(day, datetime.strptime(end_time, "%H:%M").time())
    weekday = day.weekday()
    for worker in candidates:
        wid = worker["id"]
        reasons = []
        if db.execute("SELECT 1 FROM shift_schedules WHERE worker_id=? AND work_date=?", (wid, work_day)).fetchone():
            excluded += 1; continue
        if db.execute("SELECT 1 FROM attendance WHERE user_id=? AND check_out IS NULL", (wid,)).fetchone():
            excluded += 1; continue
        if db.execute("SELECT 1 FROM leave_requests WHERE user_id=? AND status='approved' AND start_date<=? AND end_date>=?", (wid, work_day, work_day)).fetchone():
            excluded += 1; continue
        availability = db.execute("SELECT start_time,end_time FROM worker_availability WHERE user_id=? AND weekday=?", (wid, weekday)).fetchone()
        has_availability = db.execute("SELECT 1 FROM worker_availability WHERE user_id=? LIMIT 1", (wid,)).fetchone()
        if has_availability and (not availability or start_time < availability["start_time"] or end_time > availability["end_time"]):
            excluded += 1; continue
        if credential and not db.execute("SELECT 1 FROM worker_certifications WHERE worker_id=? AND lower(credential_name)=lower(?) AND (expires_on IS NULL OR expires_on>=?)", (wid, credential, work_day)).fetchone():
            excluded += 1; continue

        score = 50
        if has_availability:
            score += 12; reasons.append("fits declared availability")
        else:
            reasons.append("no availability limits submitted")
        if worker["site_id"] == site_id:
            score += 10; reasons.append("assigned to this site")
        site_shifts = db.execute("SELECT COUNT(*) FROM attendance WHERE user_id=? AND site_id=? AND date(check_in,'+5 hours','+30 minutes')>=date(?,'-90 days')", (wid, site_id, work_day)).fetchone()[0]
        score += min(8, int(site_shifts) * 2)
        if site_shifts:
            reasons.append(f"{site_shifts} recent shift(s) at site")
        recent = db.execute("""SELECT sh.start_time,sh.work_date, a.check_in,a.check_out FROM shift_schedules sh
            LEFT JOIN attendance a ON a.user_id=sh.worker_id AND date(a.check_in,'+5 hours','+30 minutes')=sh.work_date
            WHERE sh.worker_id=? AND sh.work_date>=date(?,'-90 days') AND sh.work_date<? ORDER BY sh.work_date DESC""", (wid, work_day, work_day)).fetchall()
        attended = [row for row in recent if row["check_in"]]
        punctual = 0
        for row in attended:
            try:
                checked = datetime.fromisoformat(row["check_in"]).astimezone(ZoneInfo("Asia/Kolkata"))
                scheduled = datetime.combine(date.fromisoformat(row["work_date"]), datetime.strptime(row["start_time"], "%H:%M").time())
                if checked.replace(tzinfo=None) <= scheduled + timedelta(minutes=10): punctual += 1
            except (TypeError, ValueError):
                pass
        if attended:
            punctuality = punctual / len(attended)
            score += round(12 * punctuality)
            reasons.append(f"{round(punctuality*100)}% on-time arrivals")
        else:
            score += 6; reasons.append("new to attendance history")
        if recent:
            fulfillment = len(attended) / len(recent)
            score += round(10 * fulfillment)
            reasons.append(f"{round(fulfillment*100)}% scheduled-shift attendance")
        else:
            score += 5
        shifts_28 = db.execute("SELECT COUNT(*) FROM shift_schedules WHERE worker_id=? AND work_date>=date(?,'-28 days') AND work_date<?", (wid, work_day, work_day)).fetchone()[0]
        score -= min(16, int(shifts_28) * 2)
        reasons.append(f"{shifts_28} shifts in prior 28 days")
        hours_7 = db.execute("SELECT COALESCE(SUM(MAX(0,(julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*24-COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*24) FROM attendance_breaks b WHERE b.attendance_id=a.id),0))),0) FROM attendance a WHERE a.user_id=? AND date(a.check_in,'+5 hours','+30 minutes')>=date(?,'-7 days') AND date(a.check_in,'+5 hours','+30 minutes')<?", (now(), now(), wid, work_day, work_day)).fetchone()[0]
        fatigue = max(0, float(hours_7 or 0) - 40)
        score -= min(20, round(fatigue))
        reasons.append(f"{round(float(hours_7 or 0))} recent hours")
        no_shows = db.execute("SELECT COUNT(*) FROM shift_schedules sh WHERE sh.worker_id=? AND sh.work_date>=date(?,'-90 days') AND sh.work_date<? AND time(sh.start_time,'+10 minutes')<=time('23:59') AND NOT EXISTS(SELECT 1 FROM attendance a WHERE a.user_id=sh.worker_id AND date(a.check_in,'+5 hours','+30 minutes')=sh.work_date)", (wid, work_day, work_day)).fetchone()[0]
        score -= min(25, int(no_shows) * 8)
        if no_shows: reasons.append(f"{no_shows} recent no-show(s)")
        else: reasons.append("no recent no-shows")
        results.append({"id":wid,"name":worker["name"],"score":max(0,min(100,score)),"reasons":reasons,"site_name":site_name["name"]})
    results.sort(key=lambda row:(-row["score"], row["name"].lower()))
    return results, excluded


def build_staffing_forecast(db, sites, start_day, target_rows):
    """Combine eight same-weekday schedule observations with manager minimums."""
    result = []
    for offset in range(7):
        forecast_day = start_day + timedelta(days=offset)
        for site in sites:
            historical_dates = [(forecast_day - timedelta(days=7 * week)).isoformat() for week in range(1, 9)]
            history_rows = db.execute(
                "SELECT work_date,COUNT(*) worker_count FROM shift_schedules WHERE site_id=? AND work_date IN (" + ",".join("?" for _ in historical_dates) + ") GROUP BY work_date",
                [site["id"], *historical_dates],
            ).fetchall()
            counts_by_day = {row["work_date"]: row["worker_count"] for row in history_rows}
            # Most recent observations receive more weight; the median dampens one-off peaks.
            values = [int(counts_by_day.get(day, 0)) for day in reversed(historical_dates)]
            weighted = sum(value * weight for value, weight in zip(values, range(1, 9))) / 36
            ordered = sorted(values)
            median = (ordered[3] + ordered[4]) / 2
            observed_days = sum(value > 0 for value in values)
            historical_demand = math.ceil(0.7 * weighted + 0.3 * median) if observed_days else 0
            manager_minimum = int(target_rows.get((site["id"], forecast_day.weekday()), 0))
            expected = max(historical_demand, manager_minimum)
            planned = db.execute("SELECT COUNT(*) FROM shift_schedules WHERE site_id=? AND work_date=?", (site["id"], forecast_day.isoformat())).fetchone()[0]
            gap = max(0, expected - planned)
            surplus = max(0, planned - expected)
            capacity = int(site["capacity"] or 0)
            confidence = "High" if observed_days >= 6 else ("Medium" if observed_days >= 3 else "Low")
            result.append({
                "work_date": forecast_day.isoformat(), "weekday": forecast_day.weekday(),
                "site_id": site["id"], "site_name": site["name"], "capacity": capacity,
                "historical_demand": historical_demand, "manager_minimum": manager_minimum,
                "expected": expected, "scheduled_count": planned, "gap": gap, "surplus": surplus,
                "capacity_risk": bool(capacity and planned > capacity), "confidence": confidence,
                "observations": observed_days,
            })
    result.sort(key=lambda row: (row["work_date"], row["site_name"]))
    return result


def build_auto_coverage_plan(db, forecast, target_configs):
    """Greedy global assignment planner with daily uniqueness and workload balancing."""
    assignments, gaps = [], []
    used_by_day, projected_hours, proposed_load = {}, {}, {}
    existing = db.execute("SELECT worker_id,start_time,end_time FROM shift_schedules WHERE work_date BETWEEN ? AND ?",
                          (date.today().isoformat(), (date.today()+timedelta(days=6)).isoformat())).fetchall()
    for row in existing:
        try:
            hours = (datetime.strptime(row["end_time"], "%H:%M") - datetime.strptime(row["start_time"], "%H:%M")).total_seconds() / 3600
        except ValueError:
            hours = 8
        projected_hours[row["worker_id"]] = projected_hours.get(row["worker_id"], 0) + max(0, hours)

    for day_plan in forecast:
        remaining = int(day_plan["gap"])
        if remaining <= 0:
            continue
        cap = day_plan["capacity"]
        capacity_slots = max(0, cap - day_plan["scheduled_count"]) if cap else remaining
        config = target_configs.get((day_plan["site_id"], day_plan["weekday"]), {})
        start_time = config.get("start_time", "08:00")
        end_time = config.get("end_time", "16:00")
        credential = config.get("required_credential", "")
        try:
            duration = (datetime.strptime(end_time, "%H:%M") - datetime.strptime(start_time, "%H:%M")).total_seconds() / 3600
        except ValueError:
            start_time, end_time, duration = "08:00", "16:00", 8
        capacity_blocked = max(0, remaining - capacity_slots)
        slot_limit = min(remaining, capacity_slots, 100 - len(assignments))
        selected = 0
        for _ in range(slot_limit):
            ranked, _ = rank_shift_candidates(db, day_plan["site_id"], day_plan["work_date"], start_time, end_time, credential)
            occupied = used_by_day.setdefault(day_plan["work_date"], set())
            choices = []
            for candidate in ranked:
                worker_id = candidate["id"]
                hours = projected_hours.get(worker_id, 0)
                if worker_id in occupied or hours + duration > 48:
                    continue
                balance_penalty = min(24, round(hours * 0.35)) + min(20, proposed_load.get(worker_id, 0) * 8)
                adjusted = max(0, candidate["score"] - balance_penalty)
                choices.append((adjusted, hours, candidate["name"].lower(), candidate))
            if not choices:
                break
            choices.sort(key=lambda item: (-item[0], item[1], item[2]))
            adjusted, current_hours, _, worker = choices[0]
            occupied.add(worker["id"])
            projected_hours[worker["id"]] = current_hours + duration
            proposed_load[worker["id"]] = proposed_load.get(worker["id"], 0) + 1
            assignments.append({
                "worker_id": worker["id"], "worker_name": worker["name"], "score": adjusted,
                "base_score": worker["score"], "work_date": day_plan["work_date"],
                "site_id": day_plan["site_id"], "site_name": day_plan["site_name"],
                "start_time": start_time, "end_time": end_time,
                "reasons": worker["reasons"], "hours_after_assignment": round(current_hours + duration, 1),
            })
            selected += 1
        outstanding = remaining - selected
        if outstanding:
            reason = "Site capacity reached" if capacity_blocked and selected == capacity_slots else ("100-assignment preview limit reached" if len(assignments) >= 100 else "No eligible worker remains within daily and 48-hour planner limits")
            gaps.append({"work_date": day_plan["work_date"], "site_name": day_plan["site_name"], "count": outstanding, "reason": reason})
    return {"assignments": assignments, "gaps": gaps, "slot_count": len(assignments),
            "sites_covered": len({item["site_id"] for item in assignments}),
            "workers_used": len({item["worker_id"] for item in assignments}),
            "gap_count": sum(item["count"] for item in gaps)}


def assess_schedule_readiness(db, start_day=None, horizon_days=7):
    """Explainable operational risk heuristic for upcoming, not-yet-started shifts."""
    start_day = start_day or date.today()
    end_day = start_day + timedelta(days=horizon_days - 1)
    schedules = db.execute("""SELECT sh.*,u.name worker_name,s.name site_name,s.capacity
        FROM shift_schedules sh JOIN users u ON u.id=sh.worker_id JOIN sites s ON s.id=sh.site_id
        WHERE sh.work_date BETWEEN ? AND ? AND u.active=1
        ORDER BY sh.work_date,sh.start_time LIMIT 500""", (start_day.isoformat(), end_day.isoformat())).fetchall()
    # SQLite numbers weekdays Sunday=0; normalize to the app's Monday=0 convention.
    schedules = [dict(row) for row in schedules]
    for row in schedules:
        weekday = date.fromisoformat(row["work_date"]).weekday()
        target = db.execute("SELECT required_credential FROM site_staffing_targets WHERE site_id=? AND weekday=?", (row["site_id"], weekday)).fetchone()
        row["required_credential"] = target["required_credential"] if target else ""

    local_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    assessments = []
    for shift in schedules:
        worker_id = shift["worker_id"]
        work_day = date.fromisoformat(shift["work_date"])
        scheduled_start = datetime.combine(work_day, datetime.strptime(shift["start_time"], "%H:%M").time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        hours_until_start = (scheduled_start - local_now).total_seconds() / 3600
        score, reasons, metrics = 0, [], {}

        attendance = db.execute("SELECT id FROM attendance WHERE user_id=? AND date(check_in,'+5 hours','+30 minutes')=? LIMIT 1", (worker_id, shift["work_date"])).fetchone()
        if attendance:
            continue
        if shift["confirmation_status"] == "declined":
            score += 32; reasons.append("Worker declined this shift")
        elif shift["confirmation_status"] == "pending":
            points = 20 if hours_until_start <= 24 else 8
            score += points
            reasons.append("Confirmation overdue soon" if hours_until_start <= 24 else "Worker confirmation is pending")
        if shift["work_date"] == date.today().isoformat() and hours_until_start < -10/60:
            score += 25; reasons.append("No check-in after the 10-minute grace period")

        credential = shift["required_credential"]
        if credential:
            valid = db.execute("SELECT 1 FROM worker_certifications WHERE worker_id=? AND lower(credential_name)=lower(?) AND (expires_on IS NULL OR expires_on>=?) LIMIT 1", (worker_id, credential, shift["work_date"])).fetchone()
            if not valid:
                score += 40; reasons.append(f"Missing valid {credential} credential")
            else:
                reasons.append(f"{credential} credential valid for shift")

        previous = db.execute("SELECT work_date,end_time FROM shift_schedules WHERE worker_id=? AND work_date<? ORDER BY work_date DESC LIMIT 1", (worker_id, shift["work_date"])).fetchone()
        if previous:
            previous_end = datetime.combine(date.fromisoformat(previous["work_date"]), datetime.strptime(previous["end_time"], "%H:%M").time())
            rest_hours = (datetime.combine(work_day, datetime.strptime(shift["start_time"], "%H:%M").time()) - previous_end).total_seconds()/3600
            metrics["rest_hours"] = round(rest_hours, 1)
            if rest_hours < 8:
                score += 30; reasons.append(f"Only {rest_hours:.1f} hours since prior scheduled shift")
            elif rest_hours < 11:
                score += 22; reasons.append(f"Short turnaround: {rest_hours:.1f} hours between shifts")
        else:
            metrics["rest_hours"] = None

        week_start = work_day - timedelta(days=work_day.weekday())
        weekly_rows = db.execute("SELECT start_time,end_time FROM shift_schedules WHERE worker_id=? AND work_date BETWEEN ? AND date(?,'+6 days')", (worker_id, week_start.isoformat(), week_start.isoformat())).fetchall()
        weekly_hours = 0.0
        for row in weekly_rows:
            try:
                weekly_hours += max(0, (datetime.strptime(row["end_time"], "%H:%M")-datetime.strptime(row["start_time"], "%H:%M")).total_seconds()/3600)
            except ValueError:
                pass
        metrics["weekly_hours"] = round(weekly_hours, 1)
        if weekly_hours > 48:
            points = min(25, round((weekly_hours-48)*2.5))
            score += points; reasons.append(f"{weekly_hours:.1f} scheduled hours this week")

        prior_dates = {date.fromisoformat(row["work_date"]) for row in db.execute("SELECT work_date FROM shift_schedules WHERE worker_id=? AND work_date BETWEEN ? AND ?", (worker_id, (work_day-timedelta(days=7)).isoformat(), shift["work_date"]))}
        streak, cursor = 1, work_day-timedelta(days=1)
        while cursor in prior_dates:
            streak += 1; cursor -= timedelta(days=1)
        metrics["consecutive_days"] = streak
        if streak >= 6:
            points = min(20, 5 + (streak-6)*5)
            score += points; reasons.append(f"{streak} consecutive scheduled days")

        history = db.execute("""SELECT sh.work_date,sh.start_time,a.check_in FROM shift_schedules sh
            LEFT JOIN attendance a ON a.user_id=sh.worker_id AND date(a.check_in,'+5 hours','+30 minutes')=sh.work_date
            WHERE sh.worker_id=? AND sh.work_date>=date(?,'-90 days') AND sh.work_date<? ORDER BY sh.work_date""", (worker_id, shift["work_date"], shift["work_date"])).fetchall()
        if history:
            no_shows = sum(1 for row in history if not row["check_in"])
            no_show_rate = no_shows/len(history)
            metrics["no_show_rate"] = round(no_show_rate*100)
            if no_show_rate >= .30:
                score += 20; reasons.append(f"{round(no_show_rate*100)}% no-show rate in prior 90 days")
            elif no_shows:
                score += 8; reasons.append(f"{no_shows} recent no-show(s)")
            attended = [row for row in history if row["check_in"]]
            punctual = 0
            for row in attended:
                try:
                    actual = datetime.fromisoformat(row["check_in"]).astimezone(ZoneInfo("Asia/Kolkata"))
                    expected = datetime.combine(date.fromisoformat(row["work_date"]), datetime.strptime(row["start_time"], "%H:%M").time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                    punctual += actual <= expected + timedelta(minutes=10)
                except (TypeError, ValueError):
                    pass
            punctual_rate = punctual/len(attended) if attended else 1
            metrics["punctual_rate"] = round(punctual_rate*100)
            if len(attended) >= 3 and punctual_rate < .8:
                score += 12; reasons.append(f"{round(punctual_rate*100)}% on time across {len(attended)} attended shifts")
        else:
            metrics["no_show_rate"] = None; metrics["punctual_rate"] = None

        safety = db.execute("SELECT ppe_ok,hazards_ok,equipment_ok,emergency_ready FROM safety_checklists WHERE worker_id=? AND completed_at>=datetime('now','-30 days')", (worker_id,)).fetchall()
        failed = sum(1 for row in safety if not all(row[key] for key in ("ppe_ok","hazards_ok","equipment_ok","emergency_ready")))
        metrics["safety_checks"] = len(safety)
        if safety and failed/len(safety) >= .25:
            score += min(20, 10 + round((failed/len(safety)) * 10))
            reasons.append(f"{failed}/{len(safety)} safety checks failed in prior 30 days")

        score = min(100, score)
        band = "Critical" if score >= 60 else ("High" if score >= 35 else ("Watch" if score >= 15 else "Ready"))
        if not reasons:
            reasons.append("No readiness exceptions found")
        metrics.update({"risk_score":score,"risk_band":band,"hours_until_start":round(hours_until_start,1)})
        assessments.append({**shift, **metrics, "reasons":reasons})
    assessments.sort(key=lambda row: (-row["risk_score"], row["work_date"], row["start_time"], row["worker_name"].lower()))
    site_summary = {}
    for row in assessments:
        item = site_summary.setdefault(row["site_name"], {"site_name":row["site_name"],"shifts":0,"critical":0,"high":0,"watch":0,"average_score":0})
        item["shifts"] += 1
        item["critical"] += row["risk_band"] == "Critical"
        item["high"] += row["risk_band"] == "High"
        item["watch"] += row["risk_band"] == "Watch"
        item["average_score"] += row["risk_score"]
    for item in site_summary.values():
        item["average_score"] = round(item["average_score"]/item["shifts"])
    counts = {band:sum(row["risk_band"]==band for row in assessments) for band in ("Critical","High","Watch","Ready")}
    return assessments, sorted(site_summary.values(), key=lambda item:(-item["critical"],-item["high"],-item["average_score"],item["site_name"])), counts


def analyze_attendance_exceptions(db, lookback_days=30):
    """Find explainable data/attendance deviations using robust worker and site baselines."""
    today = date.today()
    start_day = today - timedelta(days=lookback_days - 1)
    history_start = start_day - timedelta(days=120)
    history_rows = db.execute("""SELECT a.id,a.user_id,a.site_id,a.check_in,a.check_out,sh.start_time scheduled_start,
        ROUND((julianday(a.check_out)-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(b.ended_at)-julianday(b.started_at))*1440)
        FROM attendance_breaks b WHERE b.attendance_id=a.id AND b.ended_at IS NOT NULL),0)) net_minutes,
        date(a.check_in,'+5 hours','+30 minutes') local_day
        FROM attendance a LEFT JOIN shift_schedules sh ON sh.worker_id=a.user_id AND sh.work_date=date(a.check_in,'+5 hours','+30 minutes')
        WHERE a.check_out IS NOT NULL AND date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ?
        ORDER BY a.check_in DESC LIMIT 12000""", (history_start.isoformat(), (start_day-timedelta(days=1)).isoformat())).fetchall()
    user_durations, site_durations, user_arrivals = {}, {}, {}
    for row in history_rows:
        if row["net_minutes"] is not None and row["net_minutes"] > 0:
            user_durations.setdefault(row["user_id"], []).append(float(row["net_minutes"]))
            site_durations.setdefault(row["site_id"], []).append(float(row["net_minutes"]))
        if row["scheduled_start"]:
            try:
                actual = datetime.fromisoformat(row["check_in"]).astimezone(ZoneInfo("Asia/Kolkata"))
                expected = datetime.combine(date.fromisoformat(row["local_day"]), datetime.strptime(row["scheduled_start"], "%H:%M").time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                user_arrivals.setdefault(row["user_id"], []).append((actual-expected).total_seconds()/60)
            except (TypeError, ValueError):
                pass

    records = db.execute("""SELECT a.*,u.name worker_name,s.name site_name,s.latitude site_latitude,s.longitude site_longitude,s.radius_m,
        sh.start_time scheduled_start,sh.id schedule_id,
        ROUND((julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440)
        FROM attendance_breaks b WHERE b.attendance_id=a.id),0)) net_minutes,
        date(a.check_in,'+5 hours','+30 minutes') local_day,
        (SELECT COUNT(*) FROM correction_requests cr WHERE cr.attendance_id=a.id AND cr.status='pending') pending_corrections
        FROM attendance a JOIN users u ON u.id=a.user_id JOIN sites s ON s.id=a.site_id
        LEFT JOIN shift_schedules sh ON sh.worker_id=a.user_id AND sh.work_date=date(a.check_in,'+5 hours','+30 minutes')
        WHERE date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ?
        ORDER BY a.check_in DESC LIMIT 1000""", (now(),now(),start_day.isoformat(),today.isoformat())).fetchall()

    exceptions = []
    for record in records:
        score, flags, evidence = 0, [], {}
        checkin = datetime.fromisoformat(record["check_in"]).astimezone(ZoneInfo("Asia/Kolkata"))
        minutes_worked = max(0, int(record["net_minutes"] or 0))
        if record["check_out"]:
            personal = [value for value in user_durations.get(record["user_id"], []) if value > 0]
            cohort = site_durations.get(record["site_id"], [])
            baseline = personal if len(personal) >= 5 else (cohort if len(cohort) >= 10 else [])
            baseline_kind = "personal" if len(personal) >= 5 else ("site cohort" if baseline else "insufficient history")
            if baseline:
                center = statistics.median(baseline)
                mad = statistics.median(abs(value-center) for value in baseline)
                threshold = max(60, 3*1.4826*mad)
                deviation = minutes_worked-center
                evidence["duration_baseline"] = round(center)
                evidence["duration_samples"] = len(baseline)
                evidence["duration_baseline_type"] = baseline_kind
                if abs(deviation) > threshold:
                    score += 24 if deviation < 0 else 18
                    direction = "shorter" if deviation < 0 else "longer"
                    flags.append(f"Shift was {abs(round(deviation))} minutes {direction} than {baseline_kind} baseline")

        if record["scheduled_start"]:
            scheduled = datetime.combine(checkin.date(), datetime.strptime(record["scheduled_start"], "%H:%M").time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            arrival_delta = (checkin-scheduled).total_seconds()/60
            if arrival_delta > 10:
                score += 10 + (10 if arrival_delta > 30 else 0)
                flags.append(f"Arrived {round(arrival_delta)} minutes after scheduled start")
            past = user_arrivals.get(record["user_id"], [])
            if len(past) >= 5:
                center = statistics.median(past)
                mad = statistics.median(abs(value-center) for value in past)
                threshold = max(15, 3*1.4826*mad)
                if abs(arrival_delta-center) > threshold:
                    score += 16
                    flags.append(f"Arrival timing deviates from personal baseline ({len(past)} samples)")

        site_distance = None
        if record["in_lat"] is not None and record["in_lng"] is not None and record["site_latitude"] is not None and record["site_longitude"] is not None:
            site_distance = distance_m(record["in_lat"],record["in_lng"],record["site_latitude"],record["site_longitude"])
            evidence["distance_m"] = round(site_distance)
        if record["status"] == "outside_site" or (site_distance is not None and site_distance > record["radius_m"]):
            score += 35 if site_distance is None or site_distance <= record["radius_m"]*2 else 45
            flags.append("Check-in location is outside the configured site perimeter")
        if record["source"] == "gps" and (record["in_lat"] is None or record["in_lng"] is None):
            score += 15; flags.append("GPS attendance record has no coordinates")
        if record["source"] == "manager_local":
            score += 8; flags.append("Attendance was entered locally by a manager")
        if not record["schedule_id"]:
            score += 12; flags.append("Attendance has no matching scheduled shift")
        if not record["check_out"] and (datetime.now(ZoneInfo("Asia/Kolkata"))-checkin).total_seconds() > 12*3600:
            score += 25; flags.append("Shift remains open more than 12 hours after check-in")
        if record["pending_corrections"]:
            score += 10; flags.append("Attendance correction is waiting for review")

        if not flags:
            continue
        score = min(100, score)
        band = "Critical" if score >= 60 else ("High" if score >= 35 else ("Review" if score >= 15 else "Info"))
        exceptions.append({**dict(record),"minutes_worked":minutes_worked,"site_distance_m":round(site_distance) if site_distance is not None else None,
                           "risk_score":score,"risk_band":band,"flags":flags,"evidence":evidence})
    exceptions.sort(key=lambda row:(-row["risk_score"],row["check_in"]))
    counts = {band:sum(row["risk_band"]==band for row in exceptions) for band in ("Critical","High","Review","Info")}
    return exceptions, counts


def calculate_site_safety_intelligence(db, window_days=90):
    """Exposure-adjusted, recency-weighted safety signals with Bayesian shrinkage."""
    today = date.today()
    start = today-timedelta(days=window_days-1)
    current_start = today-timedelta(days=13)
    prior_start = today-timedelta(days=27)
    sites = db.execute("SELECT id,name,capacity FROM sites WHERE active=1 ORDER BY name").fetchall()
    metrics = {site["id"]:{"site_id":site["id"],"site_name":site["name"],"capacity":site["capacity"],
        "exposures":0,"incident_count":0,"unresolved_count":0,"failed_checks":0,"checks":0,
        "weighted_points":0.0,"recent_points":0.0,"prior_points":0.0,"recent_events":0,
        "categories":{},"critical_count":0} for site in sites}
    exposure_rows = db.execute("SELECT site_id,COUNT(*) shifts FROM attendance WHERE date(check_in,'+5 hours','+30 minutes') BETWEEN ? AND ? GROUP BY site_id",(start.isoformat(),today.isoformat())).fetchall()
    for row in exposure_rows:
        if row["site_id"] in metrics: metrics[row["site_id"]]["exposures"]=row["shifts"]

    global_points=0.0; global_exposure=sum(item["exposures"] for item in metrics.values())
    incident_rows=db.execute("SELECT site_id,category,severity,status,created_at FROM safety_incidents WHERE created_at>=? ORDER BY created_at DESC",((datetime.now(timezone.utc)-timedelta(days=window_days)).isoformat(),)).fetchall()
    for incident in incident_rows:
        item=metrics.get(incident["site_id"])
        if not item: continue
        try: event_day=datetime.fromisoformat(incident["created_at"]).astimezone(ZoneInfo("Asia/Kolkata")).date()
        except (TypeError,ValueError): continue
        age=max(0,(today-event_day).days)
        decay=0.5**(age/30)
        severity_weight={"low":1.0,"medium":2.0,"high":4.0,"critical":6.0}.get(incident["severity"],1.0)
        severity_weight *= 1.25 if incident["status"] in {"open","reviewing"} else 1.0
        points=severity_weight*decay
        item["weighted_points"]+=points; global_points+=points
        item["incident_count"]+=1
        item["unresolved_count"]+=incident["status"] in {"open","reviewing"}
        item["critical_count"]+=incident["severity"]=="critical"
        item["categories"][incident["category"]]=item["categories"].get(incident["category"],0)+1
        if event_day>=current_start:
            item["recent_points"]+=points; item["recent_events"]+=1
        elif event_day>=prior_start:
            item["prior_points"]+=points

    checklist_rows=db.execute("""SELECT sc.site_id,sc.ppe_ok,sc.hazards_ok,sc.equipment_ok,sc.emergency_ready,sc.completed_at
        FROM safety_checklists sc WHERE sc.completed_at>=?""",((datetime.now(timezone.utc)-timedelta(days=window_days)).isoformat(),)).fetchall()
    for check in checklist_rows:
        item=metrics.get(check["site_id"])
        if not item: continue
        item["checks"]+=1
        failed=sum(1 for key in ("ppe_ok","hazards_ok","equipment_ok","emergency_ready") if not check[key])
        if not failed: continue
        item["failed_checks"]+=1
        try: event_day=datetime.fromisoformat(check["completed_at"]).astimezone(ZoneInfo("Asia/Kolkata")).date()
        except (TypeError,ValueError): continue
        points=1.5*failed*(0.5**(max(0,(today-event_day).days)/30))
        item["weighted_points"]+=points; global_points+=points
        if event_day>=current_start:
            item["recent_points"]+=points; item["recent_events"]+=1
        elif event_day>=prior_start:
            item["prior_points"]+=points

    global_prior=global_points/max(1,global_exposure)
    for item in metrics.values():
        item["current_exposure"]=db.execute("SELECT COUNT(*) FROM attendance WHERE site_id=? AND date(check_in,'+5 hours','+30 minutes') BETWEEN ? AND ?",(item["site_id"],current_start.isoformat(),today.isoformat())).fetchone()[0]
        item["prior_exposure"]=db.execute("SELECT COUNT(*) FROM attendance WHERE site_id=? AND date(check_in,'+5 hours','+30 minutes') BETWEEN ? AND ?",(item["site_id"],prior_start.isoformat(),(current_start-timedelta(days=1)).isoformat())).fetchone()[0]
    recent_prior=sum(item["recent_points"] for item in metrics.values())/max(1,sum(item["current_exposure"] for item in metrics.values()))
    previous_prior=sum(item["prior_points"] for item in metrics.values())/max(1,sum(item["prior_exposure"] for item in metrics.values()))
    results=[]
    for item in metrics.values():
        posterior=(item["weighted_points"]+global_prior*20)/(item["exposures"]+20)
        risk_per_100=posterior*100
        # Saturating transform keeps a few small-sample events from dominating the ranking.
        score=min(100,round(100*(1-math.exp(-risk_per_100/8))))
        current_exposure=item["current_exposure"]
        prior_exposure=item["prior_exposure"]
        current_rate=(item["recent_points"]+recent_prior*5)/(current_exposure+5)*100
        prior_rate=(item["prior_points"]+previous_prior*5)/(prior_exposure+5)*100
        trend_pct=max(-999,min(999,round((current_rate-prior_rate)/max(prior_rate,0.1)*100)))
        trend="Rising" if item["recent_events"]>=2 and trend_pct>=25 else ("Improving" if trend_pct<=-25 else "Stable")
        if trend=="Rising": score=min(100,score+8)
        if item["critical_count"] and item["unresolved_count"]: score=min(100,score+10)
        band="High" if score>=65 else ("Elevated" if score>=40 else ("Watch" if score>=20 else "Low"))
        common_category=max(item["categories"],key=item["categories"].get) if item["categories"] else ""
        if item["failed_checks"]>item["incident_count"] and item["failed_checks"]:
            action="Review inspection failures and repeat the site safety briefing."
        elif item["unresolved_count"]:
            action="Close open incident actions and verify corrective controls on site."
        elif trend=="Rising":
            action="Run a focused site walk and review recent incident causes."
        elif common_category:
            action=f"Review controls and briefing for recurring {common_category.lower()} events."
        else:
            action="Continue routine checks; low event volume may limit confidence."
        confidence="High" if item["exposures"]>=50 else ("Medium" if item["exposures"]>=15 else "Low")
        trend_label="New activity" if item["prior_points"]==0 and item["recent_points"]>0 else (f"{trend_pct:+d}%" if abs(trend_pct)<999 else (">999%" if trend_pct>0 else "<-999%"))
        results.append({**item,"risk_score":score,"risk_per_100_shifts":round(risk_per_100,2),
            "trend":trend,"trend_change_pct":trend_pct,"trend_change_label":trend_label,"risk_band":band,"confidence":confidence,
            "common_category":common_category,"action":action,"failed_check_rate":round(item["failed_checks"]/item["checks"]*100) if item["checks"] else 0})
    results.sort(key=lambda row:(-row["risk_score"],-row["unresolved_count"],row["site_name"].lower()))
    summary={band:sum(row["risk_band"]==band for row in results) for band in ("High","Elevated","Watch","Low")}
    summary["Rising"] = sum(row["trend"]=="Rising" for row in results)
    return results,summary


def rank_handover_actions(db, limit=100):
    rows=db.execute("""SELECT a.*,s.name site_name,c.name creator_name,o.name assignee_name
        FROM handover_actions a JOIN sites s ON s.id=a.site_id JOIN users c ON c.id=a.created_by
        LEFT JOIN users o ON o.id=a.assigned_to WHERE a.status<>'resolved'
        ORDER BY a.due_at,a.created_at LIMIT ?""",(limit,)).fetchall()
    current=datetime.now(timezone.utc)
    repeat_counts={}
    for row in rows:
        key=(row["site_id"],row["category"])
        repeat_counts[key]=repeat_counts.get(key,0)+1
    weights={"low":8,"medium":18,"high":32,"critical":48}
    actions=[]
    for row in rows:
        created=datetime.fromisoformat(row["created_at"])
        due=datetime.fromisoformat(row["due_at"])
        if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
        if due.tzinfo is None: due=due.replace(tzinfo=timezone.utc)
        hours_open=max(0,(current-created).total_seconds()/3600)
        hours_to_due=(due-current).total_seconds()/3600
        score=weights.get(row["severity"],18)
        signals=[row["severity"].title()+" severity"]
        if hours_to_due<0:
            overdue_hours=abs(hours_to_due)
            score+=15+min(25,math.ceil(overdue_hours))
            signals.append(f"Overdue by {round(overdue_hours,1)} hours")
        elif hours_to_due<=2:
            score+=12; signals.append("Due within two hours")
        if hours_open>=24:
            score+=min(12,5+math.floor((hours_open-24)/24)*2)
            signals.append(f"Open for {round(hours_open)} hours")
        repeats=repeat_counts.get((row["site_id"],row["category"]),0)
        if repeats>=3:
            score+=8; signals.append(f"{repeats} open actions in this site/category")
        score=min(100,score)
        band="Critical" if score>=70 else ("High" if score>=50 else ("Normal" if score>=25 else "Low"))
        actions.append({**dict(row),"priority_score":score,"priority_band":band,
                        "hours_open":round(hours_open,1),"hours_to_due":round(hours_to_due,1),
                        "repeat_count":repeats,"priority_signals":signals})
    actions.sort(key=lambda row:(-row["priority_score"],row["due_at"],row["created_at"]))
    return actions


def get_coords():
    try:
        data = request.get_json(silent=True) or request.form
        lat, lng = float(data["latitude"]), float(data["longitude"])
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            return None, None
        return lat, lng
    except (KeyError, TypeError, ValueError):
        return None, None


@app.route("/", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        db = connect_db()
        user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (request.form.get("username", "").strip(),)).fetchone()
        db.close()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"], session["role"] = user["id"], user["role"]
            return redirect(url_for("index"))
        flash("That username and password did not match.", "error")
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/dashboard")
@auth()
def index():
    if session["role"] == "manager":
        return redirect(url_for("manager"))
    db = connect_db()
    active = db.execute("SELECT a.*,s.name site_name,s.address,s.map_url,s.latitude,s.longitude,s.radius_m FROM attendance a JOIN sites s ON s.id=a.site_id WHERE a.user_id=? AND a.check_out IS NULL ORDER BY a.id DESC LIMIT 1", (g.user["id"],)).fetchone()
    assigned_site = db.execute("""SELECT s.* FROM sites s
      JOIN employee_profiles ep ON ep.site_id=s.id
      WHERE ep.user_id=? AND s.active=1""", (g.user["id"],)).fetchone()
    sites = [assigned_site] if assigned_site else []
    has_biometrics = db.execute("SELECT 1 FROM biometric_credentials WHERE user_id=? LIMIT 1", (g.user["id"],)).fetchone() is not None
    face_enrolled = db.execute("SELECT 1 FROM employee_profiles WHERE user_id=? AND face_consent=1 AND photo_jpeg IS NOT NULL", (g.user["id"],)).fetchone() is not None
    schedules = db.execute("SELECT sh.*,s.name site_name FROM shift_schedules sh JOIN sites s ON s.id=sh.site_id WHERE sh.worker_id=? AND sh.work_date>=? ORDER BY sh.work_date LIMIT 7", (g.user["id"], date.today().isoformat())).fetchall()
    availability_rows = db.execute("SELECT weekday,start_time,end_time FROM worker_availability WHERE user_id=? ORDER BY weekday", (g.user["id"],)).fetchall()
    weekday_availability = {row["weekday"]:row for row in availability_rows}
    transfer_schedules = db.execute("SELECT sh.id,sh.work_date,sh.start_time,sh.end_time,s.name site_name FROM shift_schedules sh JOIN sites s ON s.id=sh.site_id WHERE sh.worker_id=? AND sh.work_date>=? AND sh.id NOT IN (SELECT schedule_id FROM shift_transfer_requests WHERE status='pending') ORDER BY sh.work_date LIMIT 14", (g.user["id"], date.today().isoformat())).fetchall()
    swap_targets = db.execute("SELECT id,name FROM users WHERE role='worker' AND active=1 AND id<>? ORDER BY name", (g.user["id"],)).fetchall()
    transfer_requests = db.execute("SELECT tr.*,sh.work_date,sh.start_time,sh.end_time,s.name site_name,u.name target_name FROM shift_transfer_requests tr JOIN shift_schedules sh ON sh.id=tr.schedule_id JOIN sites s ON s.id=sh.site_id JOIN users u ON u.id=tr.target_worker_id WHERE tr.requester_id=? ORDER BY tr.id DESC LIMIT 10", (g.user["id"],)).fetchall()
    incident_history = db.execute("SELECT i.*,s.name site_name FROM safety_incidents i JOIN sites s ON s.id=i.site_id WHERE i.worker_id=? ORDER BY i.created_at DESC LIMIT 8", (g.user["id"],)).fetchall()
    active_handover = db.execute("SELECT note FROM shift_handover_notes WHERE attendance_id=?", (active["id"],)).fetchone() if active else None
    worker_certifications = db.execute("SELECT * FROM worker_certifications WHERE worker_id=? ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END,expires_on", (g.user["id"],)).fetchall()
    active_checklist = db.execute("SELECT * FROM safety_checklists WHERE attendance_id=?", (active["id"],)).fetchone() if active else None
    checklist_history = db.execute("SELECT sc.*,s.name site_name FROM safety_checklists sc JOIN sites s ON s.id=sc.site_id WHERE sc.worker_id=? ORDER BY sc.completed_at DESC LIMIT 8", (g.user["id"],)).fetchall()
    history = db.execute("SELECT a.*,s.name site_name FROM attendance a JOIN sites s ON s.id=a.site_id WHERE a.user_id=? ORDER BY a.id DESC LIMIT 10", (g.user["id"],)).fetchall()
    leave_requests = db.execute("SELECT * FROM leave_requests WHERE user_id=? ORDER BY id DESC LIMIT 5", (g.user["id"],)).fetchall()
    announcements = db.execute("SELECT a.*, (r.user_id IS NOT NULL) is_read FROM announcements a LEFT JOIN announcement_reads r ON r.announcement_id=a.id AND r.user_id=? WHERE a.active=1 ORDER BY a.id DESC LIMIT 10", (g.user["id"],)).fetchall()
    active_break = db.execute("SELECT id FROM attendance_breaks WHERE attendance_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1", (active["id"],)).fetchone() if active else None
    breaks = db.execute("SELECT * FROM attendance_breaks WHERE attendance_id=? ORDER BY id", (active["id"],)).fetchall() if active else []
    profile = db.execute("SELECT * FROM employee_profiles WHERE user_id=?", (g.user["id"],)).fetchone()
    workflow_site_id=active["site_id"] if active else (assigned_site["id"] if assigned_site else 0)
    worker_handover_actions=db.execute("""SELECT a.*,s.name site_name,u.name creator_name,assignee.name assignee_name
        FROM handover_actions a JOIN sites s ON s.id=a.site_id JOIN users u ON u.id=a.created_by
        LEFT JOIN users assignee ON assignee.id=a.assigned_to
        WHERE a.status<>'resolved' AND (a.site_id=? OR a.assigned_to=? OR a.created_by=?)
        ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,a.due_at LIMIT 20""",(workflow_site_id,g.user["id"],g.user["id"])).fetchall()
    db.close()
    emp_code = profile["employee_id"] if profile and profile["employee_id"] else "EMP-2026-0001"
    cur_site_name = active["site_name"] if active else (assigned_site["name"] if assigned_site else "Amba Shakti Udyog Ltd")
    active_start_time = active["check_in"] if active else ""
    return render_template("worker.html", today=date.today().isoformat(), weekday_availability=weekday_availability, active=active, active_handover=active_handover, active_checklist=active_checklist, checklist_history=checklist_history, worker_certifications=worker_certifications, incident_history=incident_history, worker_handover_actions=worker_handover_actions, sites=sites, assigned_site=assigned_site, has_biometrics=has_biometrics, face_enrolled=face_enrolled, schedules=schedules, transfer_schedules=transfer_schedules, swap_targets=swap_targets, transfer_requests=transfer_requests, attendance_history=history, leave_requests=leave_requests, announcements=announcements, active_break=active_break, breaks=breaks, profile=profile, emp_code=emp_code, cur_site_name=cur_site_name, active_start_time=active_start_time)


@app.get("/manager")
@auth("manager")
def manager():
    db = connect_db()
    site_filter = request.args.get("site", "").strip()
    state_filter = request.args.get("state", "").strip()
    search = request.args.get("q", "").strip()
    clauses = ["a.check_out IS NULL"]
    params = []
    if site_filter:
        clauses.append("s.id=?"); params.append(site_filter)
    if state_filter == "outside": clauses.append("a.status='outside_site'")
    if state_filter == "on_site": clauses.append("a.status='on_site'")
    if search:
        clauses.append("(u.name LIKE ? OR ep.employee_id LIKE ?)"); params.extend([f"%{search}%", f"%{search}%"])
    rows = db.execute("""SELECT u.id user_id,u.name,u.active worker_active,ep.employee_id,ep.post,s.id site_id,s.name site_name,a.id attendance_id,a.check_in,a.source,a.status,
      (SELECT MAX(created_at) FROM location_pings p WHERE p.user_id=u.id AND p.site_id=s.id) last_ping,
      (SELECT start_time FROM shift_schedules sh WHERE sh.worker_id=u.id AND sh.work_date=date(a.check_in,'+5 hours','+30 minutes') LIMIT 1) scheduled_start,
      (SELECT CASE WHEN time(substr(a.check_in,12,8),'+5 hours','+30 minutes') > time(sh.start_time,'+10 minutes') THEN 1 ELSE 0 END FROM shift_schedules sh WHERE sh.worker_id=u.id AND sh.work_date=date(a.check_in,'+5 hours','+30 minutes') LIMIT 1) is_late,
      ROUND((julianday(?) - julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440) FROM attendance_breaks b WHERE b.attendance_id=a.id),0)) worked_minutes
      FROM attendance a JOIN users u ON u.id=a.user_id JOIN sites s ON s.id=a.site_id
      LEFT JOIN employee_profiles ep ON ep.user_id=u.id
      WHERE """ + " AND ".join(clauses) + " ORDER BY a.check_in DESC", [now(),now(),*params]).fetchall()
    today = date.today().isoformat()
    history = db.execute("""SELECT a.id,a.check_in,a.check_out,a.source,a.status,u.name worker_name,ep.employee_id,s.name site_name,
      (SELECT COALESCE(SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440),0) FROM attendance_breaks b WHERE b.attendance_id=a.id) break_minutes
      FROM attendance a JOIN users u ON u.id=a.user_id JOIN sites s ON s.id=a.site_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id
      WHERE date(a.check_in,'+5 hours','+30 minutes')=? ORDER BY a.check_in DESC LIMIT 150""", (now(),today)).fetchall()
    alerts = db.execute("""SELECT al.*,u.name worker_name,s.name site_name FROM alerts al
      JOIN users u ON u.id=al.user_id JOIN sites s ON s.id=al.site_id
      WHERE al.acknowledged=0 ORDER BY al.id DESC LIMIT 30""").fetchall()
    workers = db.execute("""SELECT u.id,u.name,u.username,u.active,ep.employee_id,ep.age,ep.gender,
      ep.permanent_address,ep.communication_address,ep.work_experience,ep.post,
      (ep.photo_jpeg IS NOT NULL) has_photo,ep.face_consent,ep.site_id,worksite.name assigned_site,
      worksite.address assigned_address,worksite.map_url assigned_map_url
      FROM users u LEFT JOIN employee_profiles ep ON ep.user_id=u.id
      LEFT JOIN sites worksite ON worksite.id=ep.site_id
      WHERE u.role='worker' ORDER BY u.name""").fetchall()
    sites = db.execute("SELECT * FROM sites WHERE active=1 ORDER BY name").fetchall()
    schedules = db.execute("SELECT sh.*,u.name worker_name,s.name site_name FROM shift_schedules sh JOIN users u ON u.id=sh.worker_id JOIN sites s ON s.id=sh.site_id WHERE sh.work_date>=? ORDER BY sh.work_date,sh.start_time LIMIT 30", (today,)).fetchall()
    leaves = db.execute("SELECT l.*,u.name worker_name FROM leave_requests l JOIN users u ON u.id=l.user_id WHERE l.status='pending' ORDER BY l.id DESC LIMIT 30").fetchall()
    corrections = db.execute("SELECT c.*,u.name worker_name,a.check_in actual_check_in,a.check_out actual_check_out FROM correction_requests c JOIN users u ON u.id=c.user_id JOIN attendance a ON a.id=c.attendance_id WHERE c.status='pending' ORDER BY c.id DESC LIMIT 30").fetchall()
    announcements = db.execute("SELECT a.*,(SELECT COUNT(*) FROM announcement_reads r JOIN users ru ON ru.id=r.user_id AND ru.active=1 WHERE r.announcement_id=a.id) read_count FROM announcements a WHERE a.active=1 ORDER BY a.id DESC LIMIT 10").fetchall()
    incidents = db.execute("SELECT i.*,u.name worker_name,s.name site_name FROM safety_incidents i JOIN users u ON u.id=i.worker_id JOIN sites s ON s.id=i.site_id WHERE i.status<>'resolved' ORDER BY CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,i.created_at DESC LIMIT 30").fetchall()
    transfer_requests = db.execute("SELECT tr.*,u.name requester_name,t.name target_name,sh.work_date,sh.start_time,sh.end_time,s.name site_name FROM shift_transfer_requests tr JOIN users u ON u.id=tr.requester_id JOIN users t ON t.id=tr.target_worker_id JOIN shift_schedules sh ON sh.id=tr.schedule_id JOIN sites s ON s.id=sh.site_id WHERE tr.status='pending' ORDER BY tr.created_at LIMIT 30").fetchall()
    handover_notes = db.execute("SELECT h.*,u.name worker_name,s.name site_name FROM shift_handover_notes h JOIN users u ON u.id=h.worker_id JOIN sites s ON s.id=h.site_id ORDER BY h.created_at DESC LIMIT 20").fetchall()
    handover_actions=rank_handover_actions(db,100)
    action_counts=db.execute("SELECT COUNT(*) open_total,SUM(CASE WHEN due_at<? THEN 1 ELSE 0 END) overdue_total,SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) critical_total,SUM(CASE WHEN due_at>=? AND due_at<=? THEN 1 ELSE 0 END) urgent_total FROM handover_actions WHERE status<>'resolved'",(now(),now(),(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat())).fetchone()
    open_action_count=action_counts["open_total"] or 0
    overdue_action_count=action_counts["overdue_total"] or 0
    critical_action_count=action_counts["critical_total"] or 0
    urgent_action_count=action_counts["urgent_total"] or 0
    resolved_action_count=db.execute("SELECT COUNT(*) FROM handover_actions WHERE status='resolved' AND date(resolved_at)>=date('now','-7 days')").fetchone()[0]
    certification_window = (date.today()+timedelta(days=60)).isoformat()
    certification_alerts = db.execute("SELECT c.*,u.name worker_name,ep.employee_id FROM worker_certifications c JOIN users u ON u.id=c.worker_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id WHERE c.expires_on IS NOT NULL AND c.expires_on<=? ORDER BY c.expires_on LIMIT 100",(certification_window,)).fetchall()
    certification_records = db.execute("SELECT c.*,u.name worker_name,ep.employee_id FROM worker_certifications c JOIN users u ON u.id=c.worker_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id ORDER BY COALESCE(c.expires_on,'9999-12-31'),u.name LIMIT 100").fetchall()
    certification_workers = db.execute("SELECT u.id,u.name FROM users u WHERE u.role='worker' AND u.active=1 ORDER BY u.name").fetchall()
    safety_checks = db.execute("SELECT sc.*,u.name worker_name,s.name site_name FROM safety_checklists sc JOIN users u ON u.id=sc.worker_id JOIN sites s ON s.id=sc.site_id ORDER BY sc.completed_at DESC LIMIT 30").fetchall()
    today_shift_count = db.execute("SELECT COUNT(*) FROM attendance WHERE date(check_in,'+5 hours','+30 minutes')=?",(today,)).fetchone()[0]
    today_check_count = db.execute("SELECT COUNT(*) FROM safety_checklists sc JOIN attendance a ON a.id=sc.attendance_id WHERE date(a.check_in,'+5 hours','+30 minutes')=?",(today,)).fetchone()[0]
    today_failed_check_count = db.execute("SELECT COUNT(*) FROM safety_checklists sc JOIN attendance a ON a.id=sc.attendance_id WHERE date(a.check_in,'+5 hours','+30 minutes')=? AND (sc.ppe_ok=0 OR sc.hazards_ok=0 OR sc.equipment_ok=0 OR sc.emergency_ready=0)",(today,)).fetchone()[0]
    audit_rows = db.execute("SELECT al.*,u.name actor_name FROM audit_log al LEFT JOIN users u ON u.id=al.actor_id ORDER BY al.id DESC LIMIT 20").fetchall()
    occupancy = {row["site_id"]: row["total"] for row in db.execute("SELECT site_id,COUNT(*) total FROM attendance WHERE check_out IS NULL GROUP BY site_id").fetchall()}
    worker_options = db.execute("SELECT id,name FROM users WHERE role='worker' AND active=1 ORDER BY name").fetchall()
    on_shift_count = db.execute("SELECT COUNT(*) FROM attendance WHERE check_out IS NULL").fetchone()[0]
    active_worker_count = db.execute("SELECT COUNT(*) FROM users WHERE role='worker' AND active=1").fetchone()[0]
    coverage_end = (date.today() + timedelta(days=6)).isoformat()
    coverage = db.execute("SELECT sh.work_date,s.id site_id,s.name site_name,s.capacity,COUNT(*) scheduled_count FROM shift_schedules sh JOIN sites s ON s.id=sh.site_id WHERE sh.work_date BETWEEN ? AND ? AND s.active=1 GROUP BY sh.work_date,s.id ORDER BY sh.work_date,s.name", (today,coverage_end)).fetchall()
    staffing_target_rows = db.execute("SELECT t.*,s.name site_name FROM site_staffing_targets t JOIN sites s ON s.id=t.site_id WHERE s.active=1 ORDER BY s.name,t.weekday").fetchall()
    staffing_targets = {(row["site_id"], row["weekday"]): row["minimum_workers"] for row in staffing_target_rows}
    staffing_target_configs = {(row["site_id"], row["weekday"]): dict(row) for row in staffing_target_rows}
    demand_forecast = build_staffing_forecast(db, sites, date.today(), staffing_targets)
    forecast_gap_count = sum(1 for row in demand_forecast if row["gap"] > 0)
    forecast_capacity_risks = sum(1 for row in demand_forecast if row["capacity_risk"])
    forecast_low_confidence = sum(1 for row in demand_forecast if row["confidence"] == "Low")
    coverage_plan_requested = request.args.get("build_plan") == "1"
    coverage_plan = build_auto_coverage_plan(db, demand_forecast, staffing_target_configs) if coverage_plan_requested else None
    default_site = sites[0]["id"] if sites else ""
    match_site = request.args.get("match_site", str(default_site))
    match_day = request.args.get("match_date", (date.today()+timedelta(days=1)).isoformat())
    match_start = request.args.get("match_start", "08:00")
    match_end = request.args.get("match_end", "16:00")
    match_credential = request.args.get("match_credential", "").strip()[:120]
    smart_matches, smart_excluded = [], 0
    try:
        match_site_id = int(match_site)
        if date.fromisoformat(match_day) < date.today() or match_start >= match_end:
            raise ValueError()
        datetime.strptime(match_start, "%H:%M"); datetime.strptime(match_end, "%H:%M")
        smart_matches, smart_excluded = rank_shift_candidates(db, match_site_id, match_day, match_start, match_end, match_credential)
    except (TypeError, ValueError):
        match_site_id = int(default_site) if default_site else 0
        smart_error = "Choose a future date and valid shift hours to rank eligible workers."
    else:
        smart_error = ""
    credential_options = db.execute("SELECT DISTINCT credential_name FROM worker_certifications ORDER BY credential_name").fetchall()
    shift_templates = db.execute("SELECT t.*,s.name site_name FROM shift_templates t JOIN sites s ON s.id=t.site_id ORDER BY t.name").fetchall()
    worker_availability = db.execute("SELECT a.*,u.name worker_name FROM worker_availability a JOIN users u ON u.id=a.user_id WHERE u.active=1 ORDER BY u.name,a.weekday").fetchall()
    pending_confirmations = db.execute("SELECT sh.*,u.name worker_name,s.name site_name FROM shift_schedules sh JOIN users u ON u.id=sh.worker_id JOIN sites s ON s.id=sh.site_id WHERE sh.work_date>=? AND sh.confirmation_status IN ('pending','declined') ORDER BY sh.work_date,sh.start_time LIMIT 50",(today,)).fetchall()
    open_break_alerts = db.execute("SELECT b.id break_id,u.name worker_name,s.name site_name,b.started_at,ROUND((julianday(?)-julianday(b.started_at))*1440) break_minutes FROM attendance_breaks b JOIN attendance a ON a.id=b.attendance_id JOIN users u ON u.id=b.user_id JOIN sites s ON s.id=a.site_id WHERE b.ended_at IS NULL AND a.check_out IS NULL AND (julianday(?)-julianday(b.started_at))*1440>=60 ORDER BY b.started_at",(now(),now())).fetchall()
    local_time=datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")
    today_schedule_count = db.execute("SELECT COUNT(*) FROM shift_schedules WHERE work_date=?",(today,)).fetchone()[0]
    today_no_show_count = db.execute("SELECT COUNT(*) FROM shift_schedules sh WHERE sh.work_date=? AND time(sh.start_time,'+10 minutes')<=? AND NOT EXISTS(SELECT 1 FROM attendance a WHERE a.user_id=sh.worker_id AND date(a.check_in,'+5 hours','+30 minutes')=?)",(today,local_time,today)).fetchone()[0]
    today_unplanned_count = db.execute("SELECT COUNT(*) FROM attendance a WHERE date(a.check_in,'+5 hours','+30 minutes')=? AND NOT EXISTS(SELECT 1 FROM shift_schedules sh WHERE sh.worker_id=a.user_id AND sh.work_date=?)",(today,today)).fetchone()[0]
    report_from = request.args.get("from", today)
    report_to = request.args.get("to", today)
    report_rows = db.execute("""SELECT a.id,a.check_in,a.check_out,a.source,u.name worker_name,ep.employee_id,s.name site_name,
      ROUND((julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440) FROM attendance_breaks b WHERE b.attendance_id=a.id),0)) net_minutes
      FROM attendance a JOIN users u ON u.id=a.user_id JOIN sites s ON s.id=a.site_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id
      WHERE date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ? ORDER BY a.check_in DESC LIMIT 500""", (now(),now(),report_from,report_to)).fetchall()
    report_metrics = db.execute("""SELECT COUNT(*) total_shifts,
      SUM(CASE WHEN a.check_out IS NOT NULL THEN 1 ELSE 0 END) completed_shifts,
      COALESCE(SUM(ROUND((julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440) FROM attendance_breaks b WHERE b.attendance_id=a.id),0))),0) net_minutes,
      SUM(CASE WHEN sh.start_time IS NOT NULL AND time(substr(a.check_in,12,8),'+5 hours','+30 minutes') > time(sh.start_time,'+10 minutes') THEN 1 ELSE 0 END) late_arrivals
      FROM attendance a LEFT JOIN shift_schedules sh ON sh.worker_id=a.user_id AND sh.work_date=date(a.check_in,'+5 hours','+30 minutes')
      WHERE date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ?""", (now(),now(),report_from,report_to)).fetchone()
    site_metrics = db.execute("""SELECT s.name site_name,COUNT(*) shifts,
      SUM(CASE WHEN a.check_out IS NOT NULL THEN 1 ELSE 0 END) completed_shifts,
      COALESCE(SUM(ROUND((julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440) FROM attendance_breaks b WHERE b.attendance_id=a.id),0))),0) net_minutes
      FROM attendance a JOIN sites s ON s.id=a.site_id
      WHERE date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ? GROUP BY s.id ORDER BY shifts DESC,s.name""", (now(),now(),report_from,report_to)).fetchall()
    schedule_readiness, readiness_sites, readiness_counts = assess_schedule_readiness(db, date.today(), 7)
    attendance_exceptions, attendance_exception_counts = analyze_attendance_exceptions(db, 30)
    site_safety_risks, site_safety_summary = calculate_site_safety_intelligence(db, 90)
    db.close()
    now_dt = datetime.now(timezone.utc)
    overtime_rows = [row for row in rows if int(row["worked_minutes"] or 0) >= 480]
    stale_ping_rows = []
    for row in rows:
        try:
            ping_time = datetime.fromisoformat(row["last_ping"])
            if (now_dt - ping_time).total_seconds() > 900:
                stale_ping_rows.append(row)
        except (TypeError, ValueError):
            stale_ping_rows.append(row)
    checklist_rate=round((today_check_count/today_shift_count)*100) if today_shift_count else 0
    expired_cert_count=sum(1 for item in certification_alerts if item["expires_on"]<today)
    return render_template("manager.html", today=today, certification_window=certification_window, rows=rows, alerts=alerts, workers=workers, sites=sites, schedules=schedules, leaves=leaves, corrections=corrections, announcements=announcements, audit_rows=audit_rows, occupancy=occupancy, worker_options=worker_options, report_rows=report_rows, report_from=report_from, report_to=report_to, report_metrics=report_metrics, site_metrics=site_metrics, site_filter=site_filter, state_filter=state_filter, search=search, on_shift_count=on_shift_count, active_worker_count=active_worker_count, coverage=coverage, coverage_end=coverage_end, demand_forecast=demand_forecast, staffing_target_rows=staffing_target_rows, forecast_gap_count=forecast_gap_count, forecast_capacity_risks=forecast_capacity_risks, forecast_low_confidence=forecast_low_confidence, coverage_plan_requested=coverage_plan_requested, coverage_plan=coverage_plan, schedule_readiness=schedule_readiness, readiness_sites=readiness_sites, readiness_counts=readiness_counts, attendance_exceptions=attendance_exceptions, attendance_exception_counts=attendance_exception_counts, site_safety_risks=site_safety_risks, site_safety_summary=site_safety_summary, handover_actions=handover_actions, open_action_count=open_action_count, overdue_action_count=overdue_action_count, critical_action_count=critical_action_count, urgent_action_count=urgent_action_count, resolved_action_count=resolved_action_count, smart_matches=smart_matches, smart_excluded=smart_excluded, smart_error=smart_error, match_site=match_site_id, match_day=match_day, match_start=match_start, match_end=match_end, match_credential=match_credential, credential_options=credential_options, overtime_rows=overtime_rows, stale_ping_rows=stale_ping_rows, incidents=incidents, transfer_requests=transfer_requests, handover_notes=handover_notes, certification_alerts=certification_alerts, certification_records=certification_records, certification_workers=certification_workers, safety_checks=safety_checks, today_shift_count=today_shift_count, today_check_count=today_check_count, today_failed_check_count=today_failed_check_count, checklist_rate=checklist_rate, expired_cert_count=expired_cert_count, shift_templates=shift_templates, worker_availability=worker_availability, pending_confirmations=pending_confirmations, open_break_alerts=open_break_alerts, today_schedule_count=today_schedule_count, today_no_show_count=today_no_show_count, today_unplanned_count=today_unplanned_count)


@app.post("/api/biometric/register/options")
@auth("worker")
def biometric_register_options():
    error = webauthn_origin_error()
    if error:
        return jsonify(ok=False, message=error), 400
    rp_id, _ = webauthn_settings()
    db = connect_db()
    existing = db.execute("SELECT credential_id FROM biometric_credentials WHERE user_id=?", (g.user["id"],)).fetchall()
    db.close()
    options = generate_registration_options(
        rp_id=rp_id, rp_name="Chauhan Man Power Supply",
        user_id=int(g.user["id"]).to_bytes(8, "big"),
        user_name=g.user["username"], user_display_name=g.user["name"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["credential_id"])) for row in existing],
    )
    store_webauthn_challenge("register", options.challenge)
    return app.response_class(options_to_json(options), mimetype="application/json")


@app.post("/api/biometric/register/verify")
@auth("worker")
def biometric_register_verify():
    error = webauthn_origin_error()
    challenge = take_webauthn_challenge("register")
    if error or not challenge:
        return jsonify(ok=False, message=error or "Verification expired. Please try again."), 400
    rp_id, origin = webauthn_settings()
    credential = (request.get_json(silent=True) or {}).get("credential")
    if not credential:
        return jsonify(ok=False, message="Phone verification response is missing."), 400
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, require_user_verification=True)
        db = connect_db()
        db.execute("INSERT INTO biometric_credentials(user_id,credential_id,public_key,sign_count,device_name,created_at) VALUES(?,?,?,?,?,?)",
                   (g.user["id"], bytes_to_base64url(verified.credential_id), verified.credential_public_key,
                    verified.sign_count, "Phone authenticator", now()))
        db.commit(); db.close()
        return jsonify(ok=True, message="Phone verification is ready.")
    except Exception as exc:
        app.logger.info("Phone credential registration failed", exc_info=True)
        return jsonify(ok=False, message="Could not set up phone verification. Try again on a supported phone or browser."), 400


@app.post("/api/biometric/auth/options")
@auth("worker")
def biometric_auth_options():
    error = webauthn_origin_error()
    if error:
        return jsonify(ok=False, message=error), 400
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in {"check_in", "check_out"}:
        return jsonify(ok=False, message="Unknown attendance action."), 400
    db = connect_db()
    credentials = db.execute("SELECT credential_id FROM biometric_credentials WHERE user_id=?", (g.user["id"],)).fetchall()
    db.close()
    if not credentials:
        return jsonify(ok=False, message="Set up phone verification before recording attendance."), 409
    options = generate_authentication_options(
        rp_id=webauthn_settings()[0],
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["credential_id"])) for row in credentials],
        user_verification=UserVerificationRequirement.REQUIRED)
    store_webauthn_challenge(action, options.challenge)
    session.pop("biometric_authorization", None)
    return app.response_class(options_to_json(options), mimetype="application/json")


@app.post("/api/biometric/auth/verify")
@auth("worker")
def biometric_auth_verify():
    error = webauthn_origin_error()
    data = request.get_json(silent=True) or {}
    action, credential = data.get("action"), data.get("credential")
    challenge = take_webauthn_challenge(action) if action in {"check_in", "check_out"} else None
    if error or not challenge or not credential:
        return jsonify(ok=False, message=error or "Phone verification expired. Please try again."), 400
    rp_id, origin = webauthn_settings()
    db = connect_db()
    row = db.execute("SELECT * FROM biometric_credentials WHERE user_id=? AND credential_id=?",
                     (g.user["id"], credential.get("id"))).fetchone()
    if not row:
        db.close(); return jsonify(ok=False, message="This phone is not registered for your account."), 403
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"], require_user_verification=True)
        db.execute("UPDATE biometric_credentials SET sign_count=? WHERE id=?", (verified.new_sign_count, row["id"]))
        db.commit(); db.close()
        session["biometric_authorization"] = {"action": action, "issued_at": int(time.time())}
        return jsonify(ok=True)
    except Exception:
        db.close()
        app.logger.info("Phone attendance verification failed", exc_info=True)
        return jsonify(ok=False, message="Phone verification failed. Please try again."), 400


@app.post("/api/face/verify")
@auth("worker")
def face_verify():
    action = request.form.get("action")
    frame = request.files.get("frame")
    if action not in {"check_in", "check_out"} or not frame:
        return jsonify(ok=False, message="Choose check-in or check-out and capture a camera image."), 400
    db = connect_db()
    profile = db.execute("SELECT photo_jpeg,face_consent FROM employee_profiles WHERE user_id=?", (g.user["id"],)).fetchone()
    active = db.execute("SELECT id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1", (g.user["id"],)).fetchone()
    db.close()
    if not profile or not profile["face_consent"] or not profile["photo_jpeg"]:
        return jsonify(ok=False, message="Face verification is not enrolled for this employee."), 409
    if (action == "check_in" and active) or (action == "check_out" and not active):
        return jsonify(ok=False, message="Your attendance state changed. Refresh the page and try again."), 409
    try:
        live_bytes = frame.read(2 * 1024 * 1024 + 1)
        if len(live_bytes) > 2 * 1024 * 1024:
            return jsonify(ok=False, message="Camera image is too large. Please retry."), 413
        distance = face_match_distance(profile["photo_jpeg"], live_bytes)
        maximum = float(os.environ.get("FACE_MATCH_MAX_DISTANCE", "65"))
        if distance > maximum:
            return jsonify(ok=False, message="We could not match that face to the registered employee photo. Please align your face in good light and look directly at camera."), 403
        confidence = max(50.0, min(99.8, round((1.0 - min(distance, 80.0) / 100.0) * 100, 1)))
        session["face_authorization"] = {"action": action, "issued_at": int(time.time())}
        session["biometric_authorization"] = {"action": action, "issued_at": int(time.time())}
        return jsonify(
            ok=True,
            message=f"Biometric face matched with {confidence}% confidence.",
            confidence=confidence,
            distance=round(distance, 1)
        )
    except (ValueError, cv2.error) as exc:
        return jsonify(ok=False, message=str(exc) or "We could not read the camera image."), 400
    except Exception:
        app.logger.exception("Face verification failed")
        return jsonify(ok=False, message="Face verification is unavailable. Ask your manager to record attendance."), 503


@app.post("/api/check-in")
@auth("worker")
def check_in():
    lat, lng = get_coords()
    site_id = request.form.get("site_id") or (request.get_json(silent=True) or {}).get("site_id")
    if lat is None or lng is None or not site_id:
        return jsonify(ok=False, message="Choose a site and allow location access."), 400
    if not consume_biometric_authorization("check_in"):
        return jsonify(ok=False, message="Verify with your phone before checking in."), 403
    if worker_requires_face_match(g.user["id"]) and not consume_face_authorization("check_in"):
        return jsonify(ok=False, message="Complete your registered face check before checking in."), 403
    db = connect_db()
    assigned_site = db.execute("""SELECT s.* FROM employee_profiles ep
      JOIN sites s ON s.id=ep.site_id
      WHERE ep.user_id=? AND s.active=1""", (g.user["id"],)).fetchone()
    if not assigned_site:
        db.close(); return jsonify(ok=False, message="No company location is assigned to your employee profile. Contact your manager."), 403
    if str(site_id) != str(assigned_site["id"]):
        db.close(); return jsonify(ok=False, message=f"Attendance is allowed only at your assigned company location: {assigned_site['name']}."), 403
    site = assigned_site
    open_shift = db.execute("SELECT id FROM attendance WHERE user_id=? AND check_out IS NULL", (g.user["id"],)).fetchone()
    if not site:
        db.close(); return jsonify(ok=False, message="Site not found."), 404
    if open_shift:
        db.close(); return jsonify(ok=False, message="You already have an open shift."), 409
    on_leave = db.execute("SELECT 1 FROM leave_requests WHERE user_id=? AND status='approved' AND date('now','+5 hours','+30 minutes') BETWEEN start_date AND end_date LIMIT 1", (g.user["id"],)).fetchone()
    if on_leave:
        db.close(); return jsonify(ok=False, message="Your leave is approved for today. Contact your manager if this is a mistake."), 403
    if site["capacity"] and db.execute("SELECT COUNT(*) FROM attendance WHERE site_id=? AND check_out IS NULL", (site["id"],)).fetchone()[0] >= site["capacity"]:
        db.close(); return jsonify(ok=False, message="This site has reached its configured attendance capacity. Please contact your manager."), 409
    dist = distance_m(lat, lng, site["latitude"], site["longitude"])
    if dist > site["radius_m"]:
        db.close(); return jsonify(ok=False, message=f"You are {round(dist)} m away. Check-in is allowed within {site['radius_m']} m."), 403
    cur = db.execute("INSERT INTO attendance(user_id,site_id,check_in,in_lat,in_lng) VALUES(?,?,?,?,?)", (g.user["id"], site["id"], now(), lat, lng))
    db.execute("INSERT INTO location_pings(user_id,site_id,latitude,longitude,created_at) VALUES(?,?,?,?,?)", (g.user["id"], site["id"], lat, lng, now()))
    db.commit(); db.close()
    return jsonify(ok=True, message="Checked in. Keep this page open to share on-site location." , attendance_id=cur.lastrowid)


@app.post("/api/check-out")
@auth("worker")
def check_out():
    if not consume_biometric_authorization("check_out"):
        return jsonify(ok=False, message="Verify with your phone before checking out."), 403
    if worker_requires_face_match(g.user["id"]) and not consume_face_authorization("check_out"):
        return jsonify(ok=False, message="Complete your registered face check before checking out."), 403
    lat, lng = get_coords()
    db = connect_db()
    row = db.execute("SELECT * FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1", (g.user["id"],)).fetchone()
    if not row:
        db.close(); return jsonify(ok=False, message="There is no active shift to check out."), 409
    check_out_time = now()
    db.execute("UPDATE attendance SET check_out=?,out_lat=?,out_lng=?,status='checked_out' WHERE id=?", (check_out_time, lat, lng, row["id"]))
    db.execute("UPDATE attendance_breaks SET ended_at=? WHERE attendance_id=? AND ended_at IS NULL", (check_out_time, row["id"]))
    
    # Calculate exact net duration down to the second
    in_dt = datetime.fromisoformat(row["check_in"])
    out_dt = datetime.fromisoformat(check_out_time)
    total_sec = max(0, int((out_dt - in_dt).total_seconds()))
    breaks = db.execute("SELECT started_at, ended_at FROM attendance_breaks WHERE attendance_id=?", (row["id"],)).fetchall()
    break_sec = 0
    for b in breaks:
        b_in = datetime.fromisoformat(b["started_at"])
        b_out = datetime.fromisoformat(b["ended_at"]) if b["ended_at"] else out_dt
        break_sec += max(0, int((b_out - b_in).total_seconds()))
    net_sec = max(0, total_sec - break_sec)
    hrs = net_sec // 3600
    mins = (net_sec % 3600) // 60
    secs = net_sec % 60
    
    db.commit(); db.close()
    return jsonify(
        ok=True,
        message=f"Checked out successfully. Total net time worked: {hrs}h {mins}m {secs}s.",
        duration_formatted=f"{hrs}h {mins}m {secs}s",
        net_seconds=net_sec,
        hours=hrs,
        minutes=mins,
        overtime_hours=max(0, hrs - 8)
    )


@app.post("/api/location")
@auth("worker")
def location_ping():
    data = request.get_json(silent=True) or {}
    try:
        lat, lng = float(data["latitude"]), float(data["longitude"])
        accuracy = float(data.get("accuracy", 0))
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, message="Location unavailable."), 400
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(ok=False, message="Location coordinates are invalid."), 400
    db = connect_db()
    shift = db.execute("SELECT a.*,s.latitude site_lat,s.longitude site_lng,s.radius_m,s.name site_name FROM attendance a JOIN sites s ON s.id=a.site_id WHERE a.user_id=? AND a.check_out IS NULL ORDER BY a.id DESC LIMIT 1", (g.user["id"],)).fetchone()
    if not shift:
        db.close(); return jsonify(ok=False, message="No active shift."), 409
    db.execute("INSERT INTO location_pings(user_id,site_id,latitude,longitude,accuracy,created_at) VALUES(?,?,?,?,?,?)", (g.user["id"],shift["site_id"],lat,lng,accuracy,now()))
    dist = distance_m(lat,lng,shift["site_lat"],shift["site_lng"])
    alerted = False
    if dist > shift["radius_m"]:
        recent = db.execute("SELECT id FROM alerts WHERE attendance_id=? AND kind='site_exit' AND created_at >= datetime('now','-10 minutes')",(shift["id"],)).fetchone()
        if not recent:
            db.execute("UPDATE attendance SET status='outside_site' WHERE id=?",(shift["id"],))
            db.execute("INSERT INTO alerts(user_id,site_id,attendance_id,kind,message,latitude,longitude,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (g.user["id"],shift["site_id"],shift["id"],"site_exit",f"{g.user['name']} left {shift['site_name']} geofence without checking out.",lat,lng,now()))
            alerted=True
    else:
        db.execute("UPDATE attendance SET status='on_site' WHERE id=?",(shift["id"],))
    db.commit(); db.close()
    return jsonify(ok=True, outside=dist > shift["radius_m"], alert_created=alerted, distance_m=round(dist))


@app.get("/api/alerts")
@auth("manager")
def api_alerts():
    db=connect_db()
    rows=db.execute("SELECT al.id,al.message,al.created_at,al.kind,u.name worker_name,s.name site_name FROM alerts al JOIN users u ON u.id=al.user_id JOIN sites s ON s.id=al.site_id WHERE al.acknowledged=0 ORDER BY al.id DESC LIMIT 30").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/alerts/<int:alert_id>/ack")
@auth("manager")
def acknowledge(alert_id):
    db=connect_db(); db.execute("UPDATE alerts SET acknowledged=1,acknowledged_by=?,acknowledged_at=? WHERE id=?",(g.user["id"],now(),alert_id)); db.commit(); db.close()
    return jsonify(ok=True)


@app.get("/manager/export.csv")
@auth("manager")
def export_attendance():
    start = request.args.get("from", date.today().isoformat())
    end = request.args.get("to", date.today().isoformat())
    try:
        date.fromisoformat(start); date.fromisoformat(end)
    except ValueError:
        abort(400)
    db = connect_db()
    records = db.execute("""SELECT ep.employee_id,u.name,s.name site_name,a.check_in,a.check_out,a.source,a.status,
      ROUND((julianday(COALESCE(a.check_out,?))-julianday(a.check_in))*1440 - COALESCE((SELECT SUM((julianday(COALESCE(b.ended_at,?))-julianday(b.started_at))*1440) FROM attendance_breaks b WHERE b.attendance_id=a.id),0)) minutes
      FROM attendance a JOIN users u ON u.id=a.user_id JOIN sites s ON s.id=a.site_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id
      WHERE date(a.check_in,'+5 hours','+30 minutes') BETWEEN ? AND ? ORDER BY a.check_in DESC""", (now(),now(),start,end)).fetchall()
    db.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Employee ID","Employee","Site","Check in (UTC)","Check out (UTC)","Source","Status","Net minutes","Overtime minutes"])
    for record in records:
        mins = max(0, int(record["minutes"] or 0))
        writer.writerow([record["employee_id"] or "",record["name"],record["site_name"],record["check_in"],record["check_out"] or "Open",record["source"],record["status"],mins,max(0,mins-480)])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),mimetype="text/csv",as_attachment=True,download_name=f"rs-attendance-{start}-to-{end}.csv")


@app.get("/manager/export/workers.csv")
@auth("manager")
def export_workers():
    db=connect_db(); workers=db.execute("SELECT ep.employee_id,u.name,u.username,u.active,ep.post,ep.age,ep.gender,ep.work_experience,ep.permanent_address,ep.communication_address,s.name site_name FROM users u LEFT JOIN employee_profiles ep ON ep.user_id=u.id LEFT JOIN sites s ON s.id=ep.site_id WHERE u.role='worker' ORDER BY u.name").fetchall(); db.close()
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(["Employee ID","Name","Username","Account Status","Designation","Age","Gender","Experience","Assigned Site","Permanent Address","Communication Address"])
    for row in workers:
        writer.writerow([row["employee_id"] or "",row["name"],row["username"],"Active" if row["active"] else "Inactive",row["post"] or "",row["age"] or "",row["gender"] or "",row["work_experience"] or "",row["site_name"] or "",row["permanent_address"] or "",row["communication_address"] or ""])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),mimetype="text/csv",as_attachment=True,download_name=f"rs-workforce-{date.today().isoformat()}.csv")


@app.get("/manager/export/certifications.csv")
@auth("manager")
def export_certifications():
    db=connect_db(); rows=db.execute("SELECT ep.employee_id,u.name,c.credential_name,c.issuer,c.issued_on,c.expires_on,c.reference_code,c.notes FROM worker_certifications c JOIN users u ON u.id=c.worker_id LEFT JOIN employee_profiles ep ON ep.user_id=u.id ORDER BY c.expires_on,u.name").fetchall(); db.close()
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(["Employee ID","Employee","Credential","Issuer","Issued","Expires","Reference","Notes","Compliance Status"])
    expiry_limit=(date.today()+timedelta(days=60)).isoformat()
    for row in rows:
        status="No expiry" if not row["expires_on"] else ("Expired" if row["expires_on"]<date.today().isoformat() else ("Expiring in 60 days" if row["expires_on"]<=expiry_limit else "Current"))
        writer.writerow([row["employee_id"] or "",row["name"],row["credential_name"],row["issuer"],row["issued_on"] or "",row["expires_on"] or "",row["reference_code"] or "",row["notes"],status])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),mimetype="text/csv",as_attachment=True,download_name=f"rs-certifications-{date.today().isoformat()}.csv")


@app.get("/manager/export/staffing-forecast.csv")
@auth("manager")
def export_staffing_forecast():
    db = connect_db()
    sites = db.execute("SELECT * FROM sites WHERE active=1 ORDER BY name").fetchall()
    targets = {(row["site_id"], row["weekday"]): row["minimum_workers"] for row in db.execute("SELECT site_id,weekday,minimum_workers FROM site_staffing_targets").fetchall()}
    forecast = build_staffing_forecast(db, sites, date.today(), targets)
    db.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Date", "Weekday", "Site", "Forecast demand", "Manager minimum", "Scheduled", "Staffing gap", "Surplus", "Capacity", "Capacity risk", "Confidence", "History samples"])
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for row in forecast:
        writer.writerow([row["work_date"], weekday_names[row["weekday"]], row["site_name"], row["historical_demand"], row["manager_minimum"], row["scheduled_count"], row["gap"], row["surplus"], row["capacity"] or "Unlimited", "Yes" if row["capacity_risk"] else "No", row["confidence"], row["observations"]])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv", as_attachment=True, download_name=f"rs-staffing-forecast-{date.today().isoformat()}.csv")


@app.get("/manager/export/readiness.csv")
@auth("manager")
def export_schedule_readiness():
    db = connect_db()
    assessments, _, _ = assess_schedule_readiness(db, date.today(), 7)
    db.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Date", "Site", "Worker", "Shift", "Status", "Risk score", "Risk band", "Rest hours", "Weekly scheduled hours", "Consecutive days", "No-show rate %", "On-time rate %", "Reasons"])
    for row in assessments:
        writer.writerow([row["work_date"], row["site_name"], row["worker_name"], f"{row['start_time']}-{row['end_time']}", row["confirmation_status"], row["risk_score"], row["risk_band"], row["rest_hours"] if row["rest_hours"] is not None else "", row["weekly_hours"], row["consecutive_days"], row["no_show_rate"] if row["no_show_rate"] is not None else "", row["punctual_rate"] if row["punctual_rate"] is not None else "", "; ".join(row["reasons"])])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv", as_attachment=True, download_name=f"rs-shift-readiness-{date.today().isoformat()}.csv")


@app.get("/manager/export/attendance-exceptions.csv")
@auth("manager")
def export_attendance_exceptions():
    db = connect_db()
    exceptions, _ = analyze_attendance_exceptions(db, 30)
    db.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Check-in", "Worker", "Site", "Status", "Source", "Risk score", "Risk band", "Net minutes", "Site distance m", "Duration baseline type", "Duration baseline minutes", "Duration samples", "Reasons"])
    for row in exceptions:
        evidence = row["evidence"]
        writer.writerow([row["check_in"], row["worker_name"], row["site_name"], row["status"], row["source"], row["risk_score"], row["risk_band"], row["minutes_worked"], row["site_distance_m"] if row["site_distance_m"] is not None else "", evidence.get("duration_baseline_type", ""), evidence.get("duration_baseline", ""), evidence.get("duration_samples", ""), "; ".join(row["flags"])])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv", as_attachment=True, download_name=f"rs-attendance-exceptions-{date.today().isoformat()}.csv")


@app.get("/manager/export/site-safety-risk.csv")
@auth("manager")
def export_site_safety_risk():
    db=connect_db(); risks,summary=calculate_site_safety_intelligence(db,90); db.close()
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(["Site","Risk score","Risk band","Trend","Trend change","Weighted risk points / 100 shifts","90-day attendance exposures","Incidents","Open incidents","Failed inspections","Inspection failure rate %","Confidence","Common category","Recommended review"])
    for row in risks:
        writer.writerow([row["site_name"],row["risk_score"],row["risk_band"],row["trend"],row["trend_change_label"],row["risk_per_100_shifts"],row["exposures"],row["incident_count"],row["unresolved_count"],row["failed_checks"],row["failed_check_rate"],row["confidence"],row["common_category"],row["action"]])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),mimetype="text/csv",as_attachment=True,download_name=f"rs-site-safety-risk-{date.today().isoformat()}.csv")


@app.get("/manager/export/handover-actions.csv")
@auth("manager")
def export_handover_actions():
    db=connect_db(); actions=rank_handover_actions(db,500); db.close()
    output=io.StringIO(); writer=csv.writer(output)
    writer.writerow(["Title","Site","Category","Severity","Priority score","Priority band","Status","Created by","Assigned to","Created at","Due at","Hours open","Hours to due","Priority signals","Details","Manager note"])
    for action in actions:
        writer.writerow([action["title"],action["site_name"],action["category"],action["severity"],action["priority_score"],action["priority_band"],action["status"],action["creator_name"],action["assignee_name"] or "",action["created_at"],action["due_at"],action["hours_open"],action["hours_to_due"],"; ".join(action["priority_signals"]),action["details"],action["manager_note"]])
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),mimetype="text/csv",as_attachment=True,download_name=f"rs-handover-actions-{date.today().isoformat()}.csv")


@app.post("/manager/certification")
@auth("manager")
def add_certification():
    try:
        worker_id=int(request.form["worker_id"]); name=request.form["credential_name"].strip(); issuer=request.form["issuer"].strip()
        issued_raw=request.form.get("issued_on","").strip(); expires_raw=request.form.get("expires_on","").strip()
        issued=date.fromisoformat(issued_raw).isoformat() if issued_raw else None
        expires=date.fromisoformat(expires_raw).isoformat() if expires_raw else None
        reference=request.form.get("reference_code","").strip()[:120]; notes=request.form.get("notes","").strip()[:500]
        if len(name)<2 or len(name)>120 or len(issuer)<2 or len(issuer)>120 or (issued and expires and expires<issued): raise ValueError()
    except (KeyError,TypeError,ValueError):
        flash("Enter a credential, issuer, and valid issue/expiry dates.","error"); return redirect(url_for("manager")+"#compliance-operations")
    db=connect_db()
    if not db.execute("SELECT 1 FROM users WHERE id=? AND role='worker'",(worker_id,)).fetchone():
        db.close(); flash("Choose a valid employee.","error"); return redirect(url_for("manager")+"#compliance-operations")
    cur=db.execute("INSERT INTO worker_certifications(worker_id,credential_name,issuer,issued_on,expires_on,reference_code,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(worker_id,name,issuer,issued,expires,reference,notes,g.user["id"],now()))
    audit(db,g.user["id"],"employee credential recorded","worker_certification",cur.lastrowid,name); db.commit(); db.close()
    flash("Employee credential saved.","success"); return redirect(url_for("manager")+"#compliance-operations")


@app.post("/manager/schedule")
@auth("manager")
def save_schedule():
    try:
        worker_id=int(request.form["worker_id"]); site_id=int(request.form["site_id"])
        work_date=date.fromisoformat(request.form["work_date"]).isoformat()
        start=request.form["start_time"]; end=request.form["end_time"]
        repeat_weeks=int(request.form.get("repeat_weeks", "1"))
        datetime.strptime(start,"%H:%M"); datetime.strptime(end,"%H:%M")
        if start >= end or not 1 <= repeat_weeks <= 12: raise ValueError()
    except (KeyError,ValueError):
        flash("Choose a valid employee, date, time, and repeat period (1–12 weeks).","error"); return redirect(url_for("manager")+"#manager-operations")
    db=connect_db()
    if not db.execute("SELECT 1 FROM users WHERE id=? AND role='worker' AND active=1",(worker_id,)).fetchone() or not db.execute("SELECT 1 FROM sites WHERE id=? AND active=1",(site_id,)).fetchone():
        db.close(); flash("Choose an active employee and site.","error"); return redirect(url_for("manager")+"#manager-operations")
    dates=[(date.fromisoformat(work_date)+timedelta(days=7*week)).isoformat() for week in range(repeat_weeks)]
    leave_conditions=" OR ".join("(start_date<=? AND end_date>=?)" for _ in dates)
    on_leave=db.execute(f"SELECT 1 FROM leave_requests WHERE user_id=? AND status='approved' AND ({leave_conditions}) LIMIT 1",[worker_id,*[value for day in dates for value in (day,day)]]).fetchone()
    if on_leave:
        db.close(); flash("Schedule conflicts with approved leave in the requested repeat period.","error"); return redirect(url_for("manager")+"#manager-operations")
    availability=db.execute("SELECT weekday,start_time,end_time FROM worker_availability WHERE user_id=?",(worker_id,)).fetchall()
    availability_by_day={item["weekday"]:item for item in availability}
    for shift_day in dates:
        existing=db.execute("SELECT 1 FROM shift_schedules WHERE worker_id=? AND work_date=?",(worker_id,shift_day)).fetchone()
        attended=db.execute("SELECT 1 FROM attendance WHERE user_id=? AND check_out IS NULL AND date(check_in,'+5 hours','+30 minutes')=?",(worker_id,shift_day)).fetchone()
        if existing or attended:
            db.close(); flash(f"Schedule conflicts with an existing assignment or active shift on {shift_day}; no repeat shifts were added.","error"); return redirect(url_for("manager")+"#manager-operations")
        if availability:
            available=availability_by_day.get(date.fromisoformat(shift_day).weekday())
            if not available or start<available["start_time"] or end>available["end_time"]:
                db.close(); flash(f"The shift is outside the worker's submitted availability on {shift_day}; no repeat shifts were added.","error"); return redirect(url_for("manager")+"#manager-operations")
    for shift_day in dates:
        db.execute("INSERT INTO shift_schedules(worker_id,site_id,work_date,start_time,end_time,created_by,created_at) VALUES(?,?,?,?,?,?,?)",(worker_id,site_id,shift_day,start,end,g.user["id"],now()))
    audit(db,g.user["id"],"schedule saved","shift_schedule",worker_id,f"{len(dates)} weekly shift(s) from {work_date} {start}-{end}")
    db.commit(); db.close(); flash(f"Saved {len(dates)} weekly shift(s).","success"); return redirect(url_for("manager")+"#manager-operations")


@app.post("/manager/shift-template")
@auth("manager")
def save_shift_template():
    try:
        name=request.form["name"].strip(); site_id=int(request.form["site_id"]); start=request.form["start_time"]; end=request.form["end_time"]
        datetime.strptime(start,"%H:%M"); datetime.strptime(end,"%H:%M")
        if len(name)<2 or len(name)>80 or start>=end: raise ValueError()
    except (KeyError,TypeError,ValueError):
        flash("Enter a template name, active site, and valid shift hours.","error"); return redirect(url_for("manager")+"#manager-operations")
    db=connect_db()
    if not db.execute("SELECT 1 FROM sites WHERE id=? AND active=1",(site_id,)).fetchone():
        db.close(); flash("Choose an active site for the shift template.","error"); return redirect(url_for("manager")+"#manager-operations")
    try:
        cur=db.execute("INSERT INTO shift_templates(name,site_id,start_time,end_time,created_by,created_at) VALUES(?,?,?,?,?,?)",(name,site_id,start,end,g.user["id"],now()))
    except sqlite3.IntegrityError:
        db.close(); flash("A shift template with that name already exists.","error"); return redirect(url_for("manager")+"#manager-operations")
    audit(db,g.user["id"],"shift template created","shift_template",cur.lastrowid,name); db.commit(); db.close()
    flash("Shift template saved.","success"); return redirect(url_for("manager")+"#manager-operations")


@app.post("/manager/staffing-target")
@auth("manager")
def save_staffing_target():
    try:
        site_id = int(request.form["site_id"])
        weekday = int(request.form["weekday"])
        minimum = int(request.form["minimum_workers"])
        start_time = request.form.get("start_time", "08:00")
        end_time = request.form.get("end_time", "16:00")
        required_credential = request.form.get("required_credential", "").strip()[:120]
        datetime.strptime(start_time, "%H:%M"); datetime.strptime(end_time, "%H:%M")
        if not 0 <= weekday <= 6 or not 0 <= minimum <= 10000 or start_time >= end_time:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        flash("Choose a valid site, weekday, and minimum staffing level.", "error")
        return redirect(url_for("manager") + "#advanced-operations")
    db = connect_db()
    site = db.execute("SELECT name FROM sites WHERE id=? AND active=1", (site_id,)).fetchone()
    if not site:
        db.close(); flash("Choose an active client site.", "error")
        return redirect(url_for("manager") + "#advanced-operations")
    db.execute("""INSERT INTO site_staffing_targets(site_id,weekday,minimum_workers,start_time,end_time,required_credential,updated_by,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(site_id,weekday) DO UPDATE SET
        minimum_workers=excluded.minimum_workers,start_time=excluded.start_time,end_time=excluded.end_time,
        required_credential=excluded.required_credential,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
        (site_id, weekday, minimum, start_time, end_time, required_credential, g.user["id"], now()))
    audit(db, g.user["id"], "staffing target updated", "site_staffing_target", site_id,
          f"{site['name']} weekday {weekday}: minimum {minimum}, {start_time}-{end_time}, credential {required_credential or 'none'}")
    db.commit(); db.close()
    flash("Weekly staffing target saved; the seven-day forecast has been updated.", "success")
    return redirect(url_for("manager") + "#advanced-operations")


@app.post("/manager/coverage-plan/apply")
@auth("manager")
def apply_coverage_plan():
    db = connect_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        sites = db.execute("SELECT * FROM sites WHERE active=1 ORDER BY name").fetchall()
        target_rows = db.execute("SELECT site_id,weekday,minimum_workers,start_time,end_time,required_credential FROM site_staffing_targets").fetchall()
        target_counts = {(row["site_id"], row["weekday"]): row["minimum_workers"] for row in target_rows}
        target_configs = {(row["site_id"], row["weekday"]): dict(row) for row in target_rows}
        forecast = build_staffing_forecast(db, sites, date.today(), target_counts)
        plan = build_auto_coverage_plan(db, forecast, target_configs)
        for item in plan["assignments"]:
            db.execute("INSERT INTO shift_schedules(worker_id,site_id,work_date,start_time,end_time,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                       (item["worker_id"], item["site_id"], item["work_date"], item["start_time"], item["end_time"], g.user["id"], now()))
        if plan["slot_count"]:
            audit(db, g.user["id"], "optimized coverage plan applied", "shift_schedule", None,
                  f"{plan['slot_count']} shifts across {plan['sites_covered']} sites; {plan['gap_count']} slots remain")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback(); db.close()
        flash("The plan changed during assignment; no shifts were saved. Build a fresh preview.", "error")
        return redirect(url_for("manager", build_plan="1") + "#coverage-planner")
    except Exception:
        db.rollback(); db.close()
        app.logger.exception("Coverage plan could not be applied")
        flash("The coverage plan could not be applied. Build a fresh preview and try again.", "error")
        return redirect(url_for("manager", build_plan="1") + "#coverage-planner")
    db.close()
    flash(f"Coverage plan applied: {plan['slot_count']} shifts assigned; {plan['gap_count']} slots still need coverage.", "success" if plan["slot_count"] else "warning")
    return redirect(url_for("manager") + "#advanced-operations")


@app.post("/worker/availability")
@auth("worker")
def save_worker_availability():
    try:
        weekdays=sorted({int(value) for value in request.form.getlist("weekdays")})
        start=request.form.get("start_time",""); end=request.form.get("end_time","")
        if any(day<0 or day>6 for day in weekdays): raise ValueError()
        if weekdays:
            datetime.strptime(start,"%H:%M"); datetime.strptime(end,"%H:%M")
            if start>=end: raise ValueError()
    except (TypeError,ValueError):
        flash("Choose valid days and availability hours.","error"); return redirect(url_for("index")+"#worker-planning")
    db=connect_db(); db.execute("DELETE FROM worker_availability WHERE user_id=?",(g.user["id"],))
    db.executemany("INSERT INTO worker_availability(user_id,weekday,start_time,end_time,updated_at) VALUES(?,?,?,?,?)",[(g.user["id"],day,start,end,now()) for day in weekdays])
    audit(db,g.user["id"],"weekly availability updated","worker_availability",g.user["id"],f"Days: {','.join(map(str,weekdays)) or 'none'}")
    db.commit(); db.close(); flash("Weekly availability updated; managers will check it when assigning shifts.","success"); return redirect(url_for("index")+"#worker-planning")


@app.post("/worker/schedule/<int:schedule_id>/<response>")
@auth("worker")
def respond_to_schedule(schedule_id,response):
    if response not in {"confirm","decline"}: abort(404)
    status="confirmed" if response=="confirm" else "declined"; note=request.form.get("worker_note","").strip()[:300]
    if status=="declined" and not note:
        flash("Add a short reason when declining the shift.","error"); return redirect(url_for("index")+"#worker-planning")
    db=connect_db(); changed=db.execute("UPDATE shift_schedules SET confirmation_status=?,worker_response_at=?,worker_note=? WHERE id=? AND worker_id=? AND work_date>=? AND confirmation_status='pending'",(status,now(),note,schedule_id,g.user["id"],date.today().isoformat()))
    if changed.rowcount: audit(db,g.user["id"],f"shift {status}","shift_schedule",schedule_id,note)
    db.commit(); db.close()
    flash("Shift confirmed." if status=="confirmed" else "Shift declined; your manager has been notified.","success" if status=="confirmed" else "warning")
    return redirect(url_for("index")+"#worker-planning")


@app.post("/manager/schedule/<int:schedule_id>/reopen")
@auth("manager")
def reopen_schedule_confirmation(schedule_id):
    db=connect_db(); changed=db.execute("UPDATE shift_schedules SET confirmation_status='pending',worker_response_at=NULL,worker_note='' WHERE id=? AND work_date>=?",(schedule_id,date.today().isoformat()))
    if changed.rowcount: audit(db,g.user["id"],"shift confirmation requested again","shift_schedule",schedule_id)
    db.commit(); db.close()
    flash("Shift confirmation is pending again." if changed.rowcount else "That shift is no longer available.","success" if changed.rowcount else "error")
    return redirect(url_for("manager")+"#advanced-operations")


@app.post("/worker/leave")
@auth("worker")
def request_leave():
    try:
        start=date.fromisoformat(request.form["start_date"]); end=date.fromisoformat(request.form["end_date"]); reason=request.form["reason"].strip()
        if end < start or not reason or len(reason)>500: raise ValueError()
    except (KeyError,ValueError):
        flash("Enter a valid date range and a short reason.","error"); return redirect(url_for("index"))
    db=connect_db(); db.execute("INSERT INTO leave_requests(user_id,start_date,end_date,reason,created_at) VALUES(?,?,?,?,?)",(g.user["id"],start.isoformat(),end.isoformat(),reason,now())); db.commit(); db.close()
    flash("Your time-off request was sent to the manager.","success"); return redirect(url_for("index"))


@app.post("/worker/handover")
@auth("worker")
def submit_handover():
    note=request.form.get("note","").strip()
    if not note or len(note)>1500:
        flash("Add a handover note of up to 1,500 characters.","error"); return redirect(url_for("index")+"#my-attendance")
    db=connect_db(); shift=db.execute("SELECT id,site_id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift:
        db.close(); flash("Handover notes are available during an active shift.","error"); return redirect(url_for("index"))
    db.execute("INSERT INTO shift_handover_notes(attendance_id,worker_id,site_id,note,created_at) VALUES(?,?,?,?,?) ON CONFLICT(attendance_id) DO UPDATE SET note=excluded.note,created_at=excluded.created_at",(shift["id"],g.user["id"],shift["site_id"],note,now()))
    audit(db,g.user["id"],"shift handover recorded","attendance",shift["id"],note[:180]); db.commit(); db.close()
    flash("Shift handover saved for the manager.","success"); return redirect(url_for("index")+"#my-attendance")


def action_due_time(severity, raw_value=""):
    current=datetime.now(timezone.utc)
    if raw_value:
        local=datetime.strptime(raw_value,"%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        due=local.astimezone(timezone.utc)
        if due<=current or due>current+timedelta(days=30): raise ValueError()
        return due.isoformat(timespec="seconds")
    sla_hours={"critical":2,"high":8,"medium":24,"low":72}
    return (current+timedelta(hours=sla_hours[severity])).isoformat(timespec="seconds")


@app.post("/worker/handover-action")
@auth("worker")
def create_handover_action():
    title=request.form.get("title","").strip(); category=request.form.get("category",""); severity=request.form.get("severity","")
    details=request.form.get("details","").strip()[:1000]
    try:
        if not 3<=len(title)<=120 or category not in {"safety","equipment","client_request","access","coverage","other"} or severity not in {"low","medium","high","critical"}: raise ValueError()
        due_at=action_due_time(severity,request.form.get("due_at",""))
    except (TypeError,ValueError):
        flash("Add a valid action title, category, severity, and due time within the next 30 days.","error"); return redirect(url_for("index")+"#handover-actions")
    db=connect_db(); shift=db.execute("SELECT id,site_id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift:
        db.close(); flash("Create site action items while on an active shift.","error"); return redirect(url_for("index")+"#handover-actions")
    cur=db.execute("INSERT INTO handover_actions(site_id,source_attendance_id,created_by,title,category,severity,details,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(shift["site_id"],shift["id"],g.user["id"],title,category,severity,details,due_at,now(),now()))
    audit(db,g.user["id"],"handover action created","handover_action",cur.lastrowid,f"{severity}: {title}")
    db.commit(); db.close(); flash("Action added to the site handover queue.","success"); return redirect(url_for("index")+"#handover-actions")


@app.post("/worker/handover-action/<int:action_id>/status")
@auth("worker")
def update_worker_handover_action(action_id):
    status=request.form.get("status","")
    if status not in {"acknowledged","in_progress","resolved"}: abort(400)
    db=connect_db()
    action=db.execute("SELECT * FROM handover_actions WHERE id=? AND status<>'resolved'",(action_id,)).fetchone()
    active=db.execute("SELECT site_id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    may_update=action and (action["created_by"]==g.user["id"] or action["assigned_to"]==g.user["id"] or (active and active["site_id"]==action["site_id"] and action["assigned_to"] is None))
    if not may_update:
        db.close(); flash("That action is no longer available to you.","error"); return redirect(url_for("index")+"#handover-actions")
    resolved=now() if status=="resolved" else None
    db.execute("UPDATE handover_actions SET status=?,updated_at=?,resolved_at=? WHERE id=?",(status,now(),resolved,action_id))
    audit(db,g.user["id"],f"handover action {status}","handover_action",action_id)
    db.commit(); db.close(); flash("Site handover action updated.","success"); return redirect(url_for("index")+"#handover-actions")


@app.post("/manager/handover-action")
@auth("manager")
def manager_create_handover_action():
    title=request.form.get("title","").strip(); category=request.form.get("category",""); severity=request.form.get("severity","")
    details=request.form.get("details","").strip()[:1000]
    try:
        site_id=int(request.form["site_id"])
        assigned_raw=request.form.get("assigned_to","").strip(); assigned_to=int(assigned_raw) if assigned_raw else None
        if not 3<=len(title)<=120 or category not in {"safety","equipment","client_request","access","coverage","other"} or severity not in {"low","medium","high","critical"}: raise ValueError()
        due_at=action_due_time(severity,request.form.get("due_at",""))
    except (KeyError,TypeError,ValueError):
        flash("Enter a valid site, action, category, severity, and due time.","error"); return redirect(url_for("manager")+"#handover-action-board")
    db=connect_db()
    if not db.execute("SELECT 1 FROM sites WHERE id=? AND active=1",(site_id,)).fetchone() or (assigned_to and not db.execute("SELECT 1 FROM users WHERE id=? AND role='worker' AND active=1",(assigned_to,)).fetchone()):
        db.close(); flash("Choose an active site and worker.","error"); return redirect(url_for("manager")+"#handover-action-board")
    cur=db.execute("INSERT INTO handover_actions(site_id,created_by,assigned_to,title,category,severity,details,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(site_id,g.user["id"],assigned_to,title,category,severity,details,due_at,now(),now()))
    audit(db,g.user["id"],"manager action created","handover_action",cur.lastrowid,f"{severity}: {title}")
    db.commit(); db.close(); flash("Action added to the site continuity queue.","success"); return redirect(url_for("manager")+"#handover-action-board")


@app.post("/manager/handover-action/<int:action_id>")
@auth("manager")
def update_manager_handover_action(action_id):
    status=request.form.get("status",""); note=request.form.get("manager_note","").strip()[:500]
    try:
        assigned_raw=request.form.get("assigned_to","").strip(); assigned_to=int(assigned_raw) if assigned_raw else None
        if status not in {"open","acknowledged","in_progress","resolved"}: raise ValueError()
    except (TypeError,ValueError):
        flash("Choose a valid assignee and action status.","error"); return redirect(url_for("manager")+"#handover-action-board")
    db=connect_db(); action=db.execute("SELECT id FROM handover_actions WHERE id=?",(action_id,)).fetchone()
    if not action or (assigned_to and not db.execute("SELECT 1 FROM users WHERE id=? AND role='worker' AND active=1",(assigned_to,)).fetchone()):
        db.close(); flash("Action or assigned worker is unavailable.","error"); return redirect(url_for("manager")+"#handover-action-board")
    resolved=now() if status=="resolved" else None
    db.execute("UPDATE handover_actions SET assigned_to=?,status=?,manager_note=?,updated_at=?,resolved_at=? WHERE id=?",(assigned_to,status,note,now(),resolved,action_id))
    audit(db,g.user["id"],"handover action updated","handover_action",action_id,f"{status}; assigned to {assigned_to or 'unassigned'}")
    db.commit(); db.close(); flash("Action updated; the priority queue has been recalculated.","success"); return redirect(url_for("manager")+"#handover-action-board")


@app.post("/worker/safety-checklist")
@auth("worker")
def submit_safety_checklist():
    keys={"ppe_ok":"PPE condition","hazards_ok":"work area hazard review","equipment_ok":"equipment inspection","emergency_ready":"emergency route readiness"}
    if any(request.form.get(key) not in {"yes","no"} for key in keys):
        flash("Complete every item in the on-site safety check.","error"); return redirect(url_for("index")+"#my-attendance")
    answers={key:request.form[key]=="yes" for key in keys}; observations=request.form.get("observations","").strip()[:1000]
    failed=[label for key,label in keys.items() if not answers[key]]
    if failed and not observations:
        flash("Add a short observation when any safety item is marked No.","error"); return redirect(url_for("index")+"#my-attendance")
    db=connect_db(); shift=db.execute("SELECT id,site_id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift:
        db.close(); flash("Start an active shift before completing the site safety check.","error"); return redirect(url_for("index"))
    if db.execute("SELECT 1 FROM safety_checklists WHERE attendance_id=?",(shift["id"],)).fetchone():
        db.close(); flash("The safety check for this shift has already been recorded.","error"); return redirect(url_for("index")+"#my-attendance")
    cur=db.execute("INSERT INTO safety_checklists(attendance_id,worker_id,site_id,ppe_ok,hazards_ok,equipment_ok,emergency_ready,observations,completed_at) VALUES(?,?,?,?,?,?,?,?,?)",(shift["id"],g.user["id"],shift["site_id"],*(int(answers[key]) for key in keys),observations,now()))
    audit(db,g.user["id"],"site safety checklist completed","safety_checklist",cur.lastrowid,"Passed" if not failed else "Failed: "+", ".join(failed))
    if failed:
        severity="critical" if not answers["emergency_ready"] else ("high" if not answers["ppe_ok"] or not answers["equipment_ok"] else "medium")
        details="Failed safety items: "+", ".join(failed)+". Worker observations: "+observations
        incident=db.execute("INSERT INTO safety_incidents(attendance_id,worker_id,site_id,category,severity,details,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(shift["id"],g.user["id"],shift["site_id"],"On-site safety check",severity,details,now(),now()))
        audit(db,g.user["id"],"safety check escalated","safety_incident",incident.lastrowid,severity)
    db.commit(); db.close()
    flash("Safety check recorded and manager notified of failed items." if failed else "All on-site safety checks passed.","success" if not failed else "warning")
    return redirect(url_for("index")+"#my-attendance")


@app.post("/worker/incident")
@auth("worker")
def report_safety_incident():
    category=request.form.get("category","").strip()[:80]; severity=request.form.get("severity","").strip(); details=request.form.get("details","").strip()
    if not category or severity not in {"low","medium","high","critical"} or not details or len(details)>2000:
        flash("Choose an incident type and severity, then describe what happened (up to 2,000 characters).","error"); return redirect(url_for("index")+"#worker-planning")
    db=connect_db(); shift=db.execute("SELECT id,site_id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift:
        db.close(); flash("Safety incident reporting is available while you have an active shift.","error"); return redirect(url_for("index"))
    cur=db.execute("INSERT INTO safety_incidents(attendance_id,worker_id,site_id,category,severity,details,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(shift["id"],g.user["id"],shift["site_id"],category,severity,details,now(),now()))
    audit(db,g.user["id"],"safety incident reported","safety_incident",cur.lastrowid,f"{severity}: {category}"); db.commit(); db.close()
    flash("Safety incident sent to the manager for review.","success"); return redirect(url_for("index")+"#worker-planning")


@app.post("/worker/shift-transfer")
@auth("worker")
def request_shift_transfer():
    try:
        schedule_id=int(request.form["schedule_id"]); target_id=int(request.form["target_worker_id"]); reason=request.form["reason"].strip()
        if not reason or len(reason)>500: raise ValueError()
    except (KeyError,TypeError,ValueError):
        flash("Choose a shift and coworker, and add a short reason.","error"); return redirect(url_for("index")+"#worker-planning")
    db=connect_db(); schedule=db.execute("SELECT id,work_date FROM shift_schedules WHERE id=? AND worker_id=? AND work_date>=?",(schedule_id,g.user["id"],date.today().isoformat())).fetchone()
    target=db.execute("SELECT id FROM users WHERE id=? AND role='worker' AND active=1 AND id<>?",(target_id,g.user["id"])).fetchone()
    if not schedule or not target:
        db.close(); flash("That shift or coworker is no longer available.","error"); return redirect(url_for("index")+"#worker-planning")
    pending=db.execute("SELECT 1 FROM shift_transfer_requests WHERE schedule_id=? AND status='pending'",(schedule_id,)).fetchone()
    if pending:
        db.close(); flash("A transfer request is already pending for that shift.","error"); return redirect(url_for("index")+"#worker-planning")
    cur=db.execute("INSERT INTO shift_transfer_requests(schedule_id,requester_id,target_worker_id,reason,created_at) VALUES(?,?,?,?,?)",(schedule_id,g.user["id"],target_id,reason,now()))
    audit(db,g.user["id"],"shift transfer requested","shift_transfer",cur.lastrowid); db.commit(); db.close()
    flash("Shift transfer request sent to the manager.","success"); return redirect(url_for("index")+"#worker-planning")


@app.post("/manager/shift-transfer/<int:transfer_id>/<decision>")
@auth("manager")
def decide_shift_transfer(transfer_id,decision):
    if decision not in {"approve","decline"}: abort(404)
    db=connect_db(); transfer=db.execute("SELECT tr.*,sh.work_date,sh.start_time,sh.end_time FROM shift_transfer_requests tr JOIN shift_schedules sh ON sh.id=tr.schedule_id WHERE tr.id=? AND tr.status='pending'",(transfer_id,)).fetchone()
    if not transfer:
        db.close(); flash("This transfer request has already been handled.","error"); return redirect(url_for("manager")+"#advanced-operations")
    note=request.form.get("manager_note","").strip()[:500]; status="approved" if decision=="approve" else "declined"
    if decision=="approve":
        target_active=db.execute("SELECT 1 FROM users WHERE id=? AND role='worker' AND active=1",(transfer["target_worker_id"],)).fetchone()
        conflict=db.execute("SELECT 1 FROM shift_schedules WHERE worker_id=? AND work_date=? AND id<>?",(transfer["target_worker_id"],transfer["work_date"],transfer["schedule_id"])).fetchone()
        leave=db.execute("SELECT 1 FROM leave_requests WHERE user_id=? AND status='approved' AND start_date<=? AND end_date>=?",(transfer["target_worker_id"],transfer["work_date"],transfer["work_date"])).fetchone()
        active=db.execute("SELECT 1 FROM attendance WHERE user_id=? AND check_out IS NULL",(transfer["target_worker_id"],)).fetchone()
        availability=db.execute("SELECT weekday,start_time,end_time FROM worker_availability WHERE user_id=?",(transfer["target_worker_id"],)).fetchall()
        availability_by_day={item["weekday"]:item for item in availability}
        target_day=date.fromisoformat(transfer["work_date"]).weekday()
        available=availability_by_day.get(target_day) if availability else None
        availability_conflict=bool(availability and (not available or transfer["start_time"]<available["start_time"] or transfer["end_time"]>available["end_time"]))
        if not target_active or conflict or leave or active or availability_conflict:
            db.close(); flash("Transfer cannot be approved: the coworker has a schedule conflict, approved leave, or an active shift.","error"); return redirect(url_for("manager")+"#advanced-operations")
        db.execute("UPDATE shift_schedules SET worker_id=?,created_by=?,created_at=?,confirmation_status='pending',worker_response_at=NULL,worker_note='' WHERE id=?",(transfer["target_worker_id"],g.user["id"],now(),transfer["schedule_id"]))
    db.execute("UPDATE shift_transfer_requests SET status=?,manager_note=?,decided_by=?,decided_at=? WHERE id=?",(status,note,g.user["id"],now(),transfer_id))
    audit(db,g.user["id"],f"shift transfer {status}","shift_transfer",transfer_id,note); db.commit(); db.close()
    flash("Shift transfer approved and reassigned." if decision=="approve" else "Shift transfer declined.","success"); return redirect(url_for("manager")+"#advanced-operations")


@app.post("/manager/incident/<int:incident_id>")
@auth("manager")
def update_incident(incident_id):
    status=request.form.get("status","").strip(); note=request.form.get("manager_note","").strip()[:500]
    if status not in {"open","reviewing","resolved"}: abort(400)
    db=connect_db(); cur=db.execute("UPDATE safety_incidents SET status=?,manager_note=?,updated_at=? WHERE id=?",(status,note,now(),incident_id))
    if not cur.rowcount: db.close(); abort(404)
    audit(db,g.user["id"],"safety incident updated","safety_incident",incident_id,status); db.commit(); db.close()
    flash("Safety incident updated.","success"); return redirect(url_for("manager")+"#advanced-operations")


@app.post("/manager/leave/<int:leave_id>/<decision>")
@auth("manager")
def decide_leave(leave_id,decision):
    if decision not in {"approve","decline"}: abort(404)
    status="approved" if decision=="approve" else "declined"; db=connect_db()
    changed=db.execute("UPDATE leave_requests SET status=?,manager_note=?,decided_by=?,decided_at=? WHERE id=? AND status='pending'",(status,request.form.get("manager_note","").strip()[:500],g.user["id"],now(),leave_id))
    if changed.rowcount: audit(db,g.user["id"],f"leave {status}","leave_request",leave_id)
    db.commit(); db.close(); flash("Leave request updated." if changed.rowcount else "This request has already been handled.","success" if changed.rowcount else "error"); return redirect(url_for("manager"))


@app.post("/worker/correction")
@auth("worker")
def request_correction():
    try:
        attendance_id=int(request.form["attendance_id"]); checkin=datetime.fromisoformat(request.form["check_in"]).replace(tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc).isoformat(timespec="seconds")
        checkout_raw=request.form.get("check_out","").strip(); checkout=datetime.fromisoformat(checkout_raw).replace(tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc).isoformat(timespec="seconds") if checkout_raw else None
        reason=request.form["reason"].strip()
        if checkout and checkout < checkin or not reason or len(reason)>500: raise ValueError()
    except (KeyError,ValueError):
        flash("Enter valid requested times and a short reason.","error"); return redirect(url_for("index"))
    db=connect_db(); row=db.execute("SELECT id,check_out FROM attendance WHERE id=? AND user_id=?",(attendance_id,g.user["id"])).fetchone()
    if not row: db.close(); abort(404)
    if row["check_out"] and not checkout: db.close(); flash("Provide the corrected checkout time for this completed shift.","error"); return redirect(url_for("index"))
    db.execute("INSERT INTO correction_requests(attendance_id,user_id,requested_check_in,requested_check_out,reason,created_at) VALUES(?,?,?,?,?,?)",(attendance_id,g.user["id"],checkin,checkout,reason,now())); db.commit(); db.close()
    flash("Your correction request is waiting for manager review.","success"); return redirect(url_for("index"))


@app.post("/manager/correction/<int:request_id>/<decision>")
@auth("manager")
def decide_correction(request_id,decision):
    if decision not in {"approve","decline"}: abort(404)
    db=connect_db(); correction=db.execute("SELECT * FROM correction_requests WHERE id=? AND status='pending'",(request_id,)).fetchone()
    if not correction: db.close(); flash("This request has already been handled.","error"); return redirect(url_for("manager"))
    status="approved" if decision=="approve" else "declined"
    if decision=="approve":
        db.execute("UPDATE attendance SET check_in=?,check_out=?,status=CASE WHEN ? IS NULL THEN 'on_site' ELSE 'checked_out' END WHERE id=?",(correction["requested_check_in"],correction["requested_check_out"],correction["requested_check_out"],correction["attendance_id"]))
    db.execute("UPDATE correction_requests SET status=?,decided_by=?,decided_at=? WHERE id=?",(status,g.user["id"],now(),request_id)); audit(db,g.user["id"],f"attendance correction {status}","correction_request",request_id)
    db.commit(); db.close(); flash("Correction request updated.","success"); return redirect(url_for("manager"))


@app.post("/api/break/<action>")
@auth("worker")
def manage_break(action):
    if action not in {"start","end"}: abort(404)
    db=connect_db(); shift=db.execute("SELECT id FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift: db.close(); return jsonify(ok=False,message="You need an active shift."),409
    open_break=db.execute("SELECT id FROM attendance_breaks WHERE attendance_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",(shift["id"],)).fetchone()
    if action=="start" and not open_break: db.execute("INSERT INTO attendance_breaks(attendance_id,user_id,started_at) VALUES(?,?,?)",(shift["id"],g.user["id"],now()))
    elif action=="end" and open_break: db.execute("UPDATE attendance_breaks SET ended_at=? WHERE id=?",(now(),open_break["id"]))
    else: db.close(); return jsonify(ok=False,message="Break state has already changed."),409
    db.commit(); db.close(); return jsonify(ok=True,message="Break updated.")


@app.post("/manager/announcement")
@auth("manager")
def post_announcement():
    title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
    if not title or not body or len(title)>120 or len(body)>1500:
        flash("Add a title and a message (up to 1,500 characters).","error")
    else:
        db=connect_db(); cur=db.execute("INSERT INTO announcements(manager_id,title,body,created_at) VALUES(?,?,?,?)",(g.user["id"],title,body,now())); audit(db,g.user["id"],"announcement posted","announcement",cur.lastrowid,title); db.commit(); db.close(); flash("Announcement shared with active employees.","success")
    return redirect(url_for("manager"))


@app.post("/worker/announcement/<int:announcement_id>/read")
@auth("worker")
def mark_announcement_read(announcement_id):
    db=connect_db(); db.execute("INSERT OR IGNORE INTO announcement_reads(announcement_id,user_id,read_at) SELECT id,?,? FROM announcements WHERE id=? AND active=1",(g.user["id"],now(),announcement_id)); db.commit(); db.close(); return redirect(url_for("index"))


@app.post("/api/sos")
@auth("worker")
def send_sos():
    db=connect_db(); shift=db.execute("SELECT a.id,a.site_id,s.name site_name FROM attendance a JOIN sites s ON s.id=a.site_id WHERE a.user_id=? AND a.check_out IS NULL ORDER BY a.id DESC LIMIT 1",(g.user["id"],)).fetchone()
    if not shift: db.close(); return jsonify(ok=False,message="SOS is available during an active shift."),409
    lat,lng=get_coords(); message=f"{g.user['name']} requested urgent manager assistance at {shift['site_name']}."
    db.execute("INSERT INTO alerts(user_id,site_id,attendance_id,kind,message,latitude,longitude,created_at) VALUES(?,?,?,?,?,?,?,?)",(g.user["id"],shift["site_id"],shift["id"],"sos",message,lat,lng,now())); db.commit(); db.close(); return jsonify(ok=True,message="Your manager has been notified.")


@app.post("/manager/worker/<int:user_id>/toggle")
@auth("manager")
def toggle_worker(user_id):
    db=connect_db(); row=db.execute("SELECT active,name FROM users WHERE id=? AND role='worker'",(user_id,)).fetchone()
    if not row: db.close(); abort(404)
    active=0 if row["active"] else 1
    db.execute("UPDATE users SET active=? WHERE id=?",(active,user_id)); audit(db,g.user["id"],"employee activated" if active else "employee deactivated","user",user_id,row["name"]); db.commit(); db.close()
    flash("Employee account updated.","success"); return redirect(url_for("manager"))


@app.post("/manager/workers/assign-site")
@auth("manager")
def assign_workers_site():
    selected_ids = request.form.getlist("user_ids")
    site_id = request.form.get("site_id", "").strip()
    try:
        user_ids = sorted({int(value) for value in selected_ids})
        site_id = int(site_id)
    except (TypeError, ValueError):
        flash("Choose a workplace and select at least one employee.", "error")
        return redirect(url_for("manager") + "#employee-directory")
    if not user_ids:
        flash("Select at least one employee to assign.", "error")
        return redirect(url_for("manager") + "#employee-directory")

    db = connect_db()
    site = db.execute("SELECT id,name FROM sites WHERE id=? AND active=1", (site_id,)).fetchone()
    placeholders = ",".join("?" for _ in user_ids)
    employees = db.execute(
        f"SELECT u.id,u.name,ep.user_id profile_id FROM users u JOIN employee_profiles ep ON ep.user_id=u.id WHERE u.role='worker' AND u.id IN ({placeholders})",
        user_ids,
    ).fetchall()
    if not site or len(employees) != len(user_ids):
        db.close()
        flash("Choose an active workplace and valid registered employees.", "error")
        return redirect(url_for("manager") + "#employee-directory")

    db.execute(f"UPDATE employee_profiles SET site_id=? WHERE user_id IN ({placeholders})", [site_id, *user_ids])
    names = ", ".join(employee["name"] for employee in employees)
    audit(db, g.user["id"], "employees assigned to workplace", "site", site_id, f"{names} → {site['name']}")
    db.commit()
    db.close()
    flash(f"Assigned {len(user_ids)} employee(s) to {site['name']}.", "success")
    return redirect(url_for("manager") + "#employee-directory")


@app.post("/manager/manual-attendance")
@auth("manager")
def manual_attendance():
    user_id, site_id = request.form.get("user_id"), request.form.get("site_id")
    try:
        lat, lng = float(request.form["latitude"]), float(request.form["longitude"])
    except (KeyError, TypeError, ValueError):
        flash("Allow location access to verify you are at the work site.", "error")
        return redirect(url_for("manager"))
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        flash("The browser returned invalid location coordinates.", "error")
        return redirect(url_for("manager"))
    db=connect_db()
    worker=db.execute("SELECT id FROM users WHERE id=? AND role='worker' AND active=1",(user_id,)).fetchone()
    site=db.execute("SELECT id FROM sites WHERE id=? AND active=1",(site_id,)).fetchone()
    open_shift=db.execute("SELECT id FROM attendance WHERE user_id=? AND check_out IS NULL",(user_id,)).fetchone()
    if worker and site and not open_shift:
        site_row=db.execute("SELECT * FROM sites WHERE id=? AND active=1",(site_id,)).fetchone()
        on_leave=db.execute("SELECT 1 FROM leave_requests WHERE user_id=? AND status='approved' AND date('now','+5 hours','+30 minutes') BETWEEN start_date AND end_date LIMIT 1",(user_id,)).fetchone()
        site_full=bool(site_row["capacity"] and db.execute("SELECT COUNT(*) FROM attendance WHERE site_id=? AND check_out IS NULL",(site_id,)).fetchone()[0] >= site_row["capacity"])
        dist=distance_m(lat,lng,site_row["latitude"],site_row["longitude"])
        if on_leave:
            flash("This employee has approved leave today.","error")
        elif site_full:
            flash("This site has reached its configured attendance capacity.","error")
        elif dist > site_row["radius_m"]:
            flash(f"Your device is {round(dist)} m from the site. Local attendance can only be recorded inside its {site_row['radius_m']} m boundary.","error")
        else:
            db.execute("INSERT INTO attendance(user_id,site_id,check_in,in_lat,in_lng,source,status,marked_by) VALUES(?,?,?,?,?,?,'on_site',?)",(user_id,site_id,now(),lat,lng,"manager_local",g.user["id"]))
            audit(db,g.user["id"],"manual attendance recorded","attendance",db.execute("SELECT last_insert_rowid()").fetchone()[0],f"worker={user_id}; site={site_id}")
            flash("Local attendance recorded with manager and site location.","success")
    else:
        flash("Could not record attendance. Check worker, site, or open shifts.","error")
    db.commit(); db.close()
    return redirect(url_for("manager"))


@app.post("/manager/site")
@auth("manager")
def add_site():
    try:
        name=request.form["name"].strip(); address=request.form["address"].strip()
        lat=float(request.form["latitude"]); lng=float(request.form["longitude"]); radius=int(request.form["radius_m"]); capacity=int(request.form.get("capacity", "0") or 0)
        if not name or not address or not (-90 <= lat <= 90 and -180 <= lng <= 180 and 25 <= radius <= 5000 and 0 <= capacity <= 10000): raise ValueError()
    except (KeyError,ValueError):
        flash("Enter a valid site name, address, coordinates, and radius (25–5000 m).","error")
    else:
        db=connect_db(); cur=db.execute("INSERT INTO sites(name,address,latitude,longitude,radius_m,map_url,capacity) VALUES(?,?,?,?,?,?,?)",(name,address,lat,lng,radius,request.form.get("map_url", "").strip() or None,capacity)); audit(db,g.user["id"],"site added","site",cur.lastrowid,name); db.commit(); db.close()
        flash("Site added.","success")
    return redirect(url_for("manager"))


@app.post("/manager/site/<int:site_id>/update")
@auth("manager")
def update_site(site_id):
    try:
        lat=float(request.form["latitude"]); lng=float(request.form["longitude"]); radius=int(request.form["radius_m"]); capacity=int(request.form.get("capacity", "0") or 0)
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180 and 25 <= radius <= 5000 and 0 <= capacity <= 10000):
            raise ValueError()
    except (KeyError, ValueError):
        flash("Enter valid coordinates and a radius between 25 and 5000 meters.", "error")
    else:
        db=connect_db(); result=db.execute("UPDATE sites SET latitude=?,longitude=?,radius_m=?,capacity=? WHERE id=? AND active=1",(lat,lng,radius,capacity,site_id));
        if result.rowcount: audit(db,g.user["id"],"site boundary/capacity updated","site",site_id)
        db.commit(); db.close()
        flash("Site location saved." if result.rowcount else "Site not found.", "success" if result.rowcount else "error")
    return redirect(url_for("manager"))


@app.post("/manager/worker")
@auth("manager")
def add_worker():
    name=request.form.get("name","").strip()
    employee_id=request.form.get("employee_id","").strip().upper()
    site_id=request.form.get("site_id", "").strip()
    username=request.form.get("username","").strip().lower()
    password=request.form.get("password","")
    permanent=request.form.get("permanent_address","").strip()
    communication=request.form.get("communication_address","").strip()
    experience=request.form.get("work_experience","").strip()
    post=request.form.get("post","").strip()
    gender=request.form.get("gender","").strip()
    try:
        age=int(request.form.get("age", ""))
        if not 18 <= age <= 100: raise ValueError()
    except ValueError:
        age=None
    photo=request.files.get("profile_photo")
    photo_b64=request.form.get("photo_base64")
    face_consent = True
    has_photo = bool((photo and photo.filename) or (photo_b64 and len(photo_b64) > 100))
    if (not all([name,employee_id,site_id,username,permanent,communication,experience,post])
            or len(password)<8 or age is None
            or gender not in {"female","male","non_binary","other","prefer_not_to_say"}
            or not has_photo):
        flash("Complete all employee details, capture a live photo or upload an image, and use a temporary password of at least 8 characters.","error")
        return redirect(url_for("manager"))
    db=connect_db()
    site=db.execute("SELECT id FROM sites WHERE id=? AND active=1", (site_id,)).fetchone()
    db.close()
    if not site:
        flash("Choose an active work site from the list.", "error")
        return redirect(url_for("manager"))
    try:
        if photo and photo.filename:
            raw=photo.read(4 * 1024 * 1024 + 1)
        elif photo_b64:
            import base64
            clean_b64 = photo_b64.split(",", 1)[1] if "," in photo_b64 else photo_b64
            raw = base64.b64decode(clean_b64)
        else:
            raise ValueError("No profile photo provided.")
        if len(raw) > 4 * 1024 * 1024: raise ValueError("The photo must be 4 MB or smaller.")
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG"}: raise ValueError("Choose a JPG or PNG image.")
            if source.width < 64 or source.height < 64 or source.width * source.height > 30_000_000:
                raise ValueError("Choose an image between 64 pixels and 30 megapixels.")
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            normalized=ImageOps.exif_transpose(source).convert("RGB")
            normalized.thumbnail((640, 640))
            output=io.BytesIO()
            normalized.save(output, format="JPEG", quality=82, optimize=True)
            photo_jpeg=output.getvalue()
        if face_consent:
            extract_face(photo_jpeg)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError, cv2.error) as exc:
        flash(str(exc) if str(exc) else "That image could not be read. Choose a valid JPG or PNG photo.","error")
        return redirect(url_for("manager"))
    db=connect_db()
    try:
        db.execute("BEGIN")
        cur=db.execute("INSERT INTO users(name,username,password_hash,role) VALUES(?,?,?,'worker')",
                       (name,username,generate_password_hash(password)))
        db.execute("""INSERT INTO employee_profiles(user_id,employee_id,site_id,age,gender,permanent_address,
          communication_address,work_experience,post,photo_jpeg,face_consent,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (cur.lastrowid,employee_id,site_id,age,gender,permanent,communication,experience,post,photo_jpeg,int(face_consent),now()))
        audit(db,g.user["id"],"employee registered","user",cur.lastrowid,f"{employee_id} · {name}")
        db.commit()
        flash("Employee registered. Give them their sign-in details; they will enroll phone verification on their own device before attendance.","success")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("That username or employee ID is already in use.","error")
    finally:
        db.close()
    return redirect(url_for("manager"))


@app.get("/manager/employee/<int:user_id>/photo")
@auth("manager")
def employee_photo(user_id):
    db=connect_db()
    row=db.execute("SELECT photo_jpeg FROM employee_profiles WHERE user_id=?",(user_id,)).fetchone()
    db.close()
    if not row or not row["photo_jpeg"]: abort(404)
    response=send_file(io.BytesIO(row["photo_jpeg"]),mimetype="image/jpeg",download_name="employee-profile.jpg",max_age=0)
    response.headers["Cache-Control"]="private, no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


@app.get("/worker/photo")
@auth("worker")
def worker_own_photo():
    db=connect_db()
    row=db.execute("SELECT photo_jpeg FROM employee_profiles WHERE user_id=?",(g.user["id"],)).fetchone()
    db.close()
    if not row or not row["photo_jpeg"]: abort(404)
    response=send_file(io.BytesIO(row["photo_jpeg"]),mimetype="image/jpeg",download_name="my-profile-photo.jpg",max_age=0)
    response.headers["Cache-Control"]="private, no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


@app.post("/worker/face-check/disable")
@auth("worker")
def disable_face_check():
    db=connect_db()
    db.execute("UPDATE employee_profiles SET face_consent=0 WHERE user_id=?",(g.user["id"],))
    db.commit(); db.close()
    session.pop("face_authorization",None)
    flash("Face check turned off. You can use phone verification or ask your manager to record attendance.","success")
    return redirect(url_for("index"))


@app.template_filter("clock")
def clock(value):
    if not value: return "—"
    try: return datetime.fromisoformat(value).astimezone().strftime("%b %d · %I:%M %p")
    except ValueError: return value


@app.template_filter("localinput")
def local_input(value):
    if not value: return ""
    try:
        try:
            tz = ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timedelta
            tz = timezone(timedelta(hours=5, minutes=30))
        return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%dT%H:%M")
    except Exception: return ""


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
