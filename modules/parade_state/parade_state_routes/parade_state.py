import os
import re
import io
import json
import base64
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, date, timedelta
from bson import ObjectId
from pymongo import MongoClient, ReturnDocument
from gridfs import GridFS
from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    send_file,
    Response,
    current_app,
)
from modules.extensions import socketio

parade_state_routes = Blueprint("parade_state_routes", __name__)

# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]
fs = GridFS(db, collection="parade_snapshots")


def sanitize_doc(doc):
    """Recursively converts ObjectIds and Datetimes to JSON-serializable strings."""
    if not doc:
        return doc
    if isinstance(doc, list):
        return [sanitize_doc(item) for item in doc]
    if isinstance(doc, dict):
        clean = {}
        for k, v in doc.items():
            if isinstance(v, ObjectId):
                clean[k] = str(v)
            elif isinstance(v, (datetime, date)):
                clean[k] = v.isoformat()
            elif isinstance(v, dict):
                clean[k] = sanitize_doc(v)
            elif isinstance(v, list):
                clean[k] = sanitize_doc(v)
            else:
                clean[k] = v
        return clean
    return str(doc) if isinstance(doc, ObjectId) else doc


def get_current_user_and_role():
    """Extracts session user details and permissions."""
    if "user_email" not in session:
        return None, None, None
    email = session.get("user_email")
    user = db.users.find_one({"email": email})
    if not user:
        return None, None, None
    role = user.get("role", "civilian")
    directorate = str(user.get("directorate", "DOA")).strip().upper()
    return sanitize_doc(user), role, directorate


def check_active_leave(service_number, email, date_obj):
    """
    Checks db.applications for active approved leaves covering the target date.
    Returns: (is_on_leave: bool, leave_type_label: str, leave_details: dict)
    """
    if not date_obj:
        date_obj = datetime.now()
    date_str = date_obj.strftime("%Y-%m-%d")

    query = {
        "$and": [
            {
                "$or": [
                    {"applicantId": {"$regex": f"^{re.escape(service_number)}$", "$options": "i"}},
                    {"applicantEmail": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
                ]
            },
            {
                "status": {
                    "$in": [
                        "approved", "Approved", "APPROVED",
                        "issued", "Issued", "ISSUED"
                    ]
                }
            }
        ]
    }

    try:
        candidate_apps = db.applications.find(query).sort("created_at", -1)
        for app in candidate_apps:
            raw_start = app.get("startDate") or app.get("start_date") or app.get("effectiveDate")
            raw_end = app.get("endDate") or app.get("end_date")
            if not raw_start or not raw_end:
                continue

            # Standardize date comparisons
            start_str = str(raw_start)[:10]
            end_str = str(raw_end)[:10]

            if start_str <= date_str <= end_str:
                leave_type = str(app.get("leaveType") or app.get("leave_type") or "leave").lower()
                if "sick" in leave_type:
                    label = "Sick"
                elif "pass" in leave_type:
                    label = "On Pass"
                else:
                    label = "On Leave"

                return True, label, {
                    "leave_type": leave_type,
                    "label": label,
                    "start_date": start_str,
                    "end_date": end_str,
                    "reason": app.get("reason", "")
                }
    except Exception as e:
        print(f"[PARADE STATE] Error checking leave for {service_number}: {e}")

    return False, None, None


def get_daily_parade_state(directorate, ao_type, date_str):
    """Fetches or initializes the daily parade state header for a directorate and AO type."""
    active_shift = "A" if ao_type == "civilian" else "Both"
    batch_val = "A" if active_shift == "A" else ("B" if active_shift == "B" else "ALL")
    now_dt = datetime.utcnow()

    # Use find_one_and_update with upsert=True to avoid DuplicateKeyError from
    # the unique_daily_parade index on {date, directorate, batch}.
    # $setOnInsert only fires when a new document is created, $set is always applied.
    state = db.daily_parade_states.find_one_and_update(
        {
            "directorate": directorate,
            "ao_type": ao_type,
            "date_str": date_str
        },
        {
            "$setOnInsert": {
                # MongoDB collection validator required fields
                "date": date_str,
                "directorate": directorate,
                "batch": batch_val,
                "strength": 0,
                "on_parade": 0,
                "sick": 0,
                "leave": 0,
                "pass": 0,
                "hospital": 0,
                "course": 0,
                "absent": 0,
                "awol": 0,
                "excuse_duty": 0,
                "study_leave": 0,
                "cds": 0,
                "details": {},
                "remark": "",
                "createdBy": "system",
                "createdAt": now_dt,
                # Enhanced fields
                "ao_type": ao_type,
                "date_str": date_str,
                "active_shift": active_shift,
                "is_dispatched": False,
                "dispatched_at": None,
                "dispatched_by": None,
                "dispatched_by_name": None,
                "dispatched_to": [],
                "remarks": "",
                "created_at": now_dt,
                "updated_at": now_dt
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER  # Return the document after upsert
    )
    return state


def resolve_user_category(user):
    """Accurately classifies user as civilian, military, nysc, or it."""
    if not user:
        return "civilian"
    cat = str(user.get("category") or "").strip().lower()
    if cat in ["civilian", "military", "nysc", "it"]:
        return cat
    sn = str(user.get("service_number") or "").strip().upper()
    role = str(user.get("role") or "").strip().lower()
    if sn.startswith(("NA/", "NN/", "NAF/", "N/")) or role in ["military_ao", "rsm"]:
        return "military"
    return "civilian"


def compile_directorate_parade_state(directorate, ao_type, date_str):
    """
    Compiles the full parade state report for a given directorate and AO type.
    Reconciles nominal roll, shift group A/B, barcode clock-ins, and leave/pass/sick statuses.
    """
    directorate_clean = str(directorate).strip().upper()
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        date_obj = datetime.now()
        date_str = date_obj.strftime("%Y-%m-%d")

    day_of_week = date_obj.strftime("%A")
    is_weekday = date_obj.weekday() < 5  # Mon=0, Fri=4

    # Fetch daily state settings
    daily_state = get_daily_parade_state(directorate_clean, ao_type, date_str)
    active_shift = daily_state.get("active_shift", "A" if ao_type == "civilian" else "Both")

    # Fetch all active users in this directorate
    all_dir_users = list(db.users.find({
        "directorate": {"$regex": f"^{directorate_clean}$", "$options": "i"},
        "is_active": {"$ne": False},
        "role": {"$nin": ["super_admin", "cdsa", "dcdsa"]}
    }).sort([("shift_group", 1), ("name", 1)]))

    # Filter nominal list by resolved category
    nominal_users = []
    for u in all_dir_users:
        u_resolved_cat = resolve_user_category(u)
        if ao_type == "military" and u_resolved_cat == "military":
            nominal_users.append(u)
        elif ao_type == "civilian" and u_resolved_cat in ["civilian", "nysc", "it"]:
            nominal_users.append(u)

    # Fetch all clock-in scans for this date, directorate, and ao_type
    attendance_records = list(db.parade_attendance.find({
        "directorate": directorate_clean,
        "ao_type": ao_type,
        "date_str": date_str
    }))
    scanned_map = {}
    for att in attendance_records:
        sn_key = str(att.get("service_number", "")).strip().upper()
        email_key = str(att.get("email", "")).strip().lower()
        if sn_key:
            scanned_map[sn_key] = att
        if email_key:
            scanned_map[email_key] = att

    # Statistics counters
    roster = []
    total_nominal = len(nominal_users)
    total_expected = 0
    total_present = 0
    total_absent = 0
    total_leave = 0
    total_sick = 0
    total_nysc = 0
    nysc_present = 0
    nysc_absent = 0
    total_it = 0
    it_present = 0
    it_absent = 0

    group_a_count = 0
    group_b_count = 0

    for idx, user in enumerate(nominal_users):
        u_sn = str(user.get("service_number") or "").strip().upper()
        u_email = str(user.get("email") or "").strip().lower()
        u_cat = str(user.get("category", "civilian")).lower()
        u_shift = str(user.get("shift_group", "A")).upper()
        if u_shift not in ["A", "B"]:
            u_shift = "A"

        if u_shift == "A":
            group_a_count += 1
        elif u_shift == "B":
            group_b_count += 1

        if u_cat == "nysc":
            total_nysc += 1
        elif u_cat == "it":
            total_it += 1

        # Strict Shift Filtering:
        # If AO picks Shift A, those in Shift B or off-duty MUST NOT show on the list or dispatched list.
        # If AO picks Shift B, those in Shift A or off-duty MUST NOT show on the list or dispatched list.
        is_scheduled = False
        if ao_type == "military":
            if active_shift == "None":
                continue
            is_scheduled = True
        else:
            if active_shift == "A":
                if u_shift != "A":
                    continue
                is_scheduled = True
            elif active_shift == "B":
                if u_shift != "B":
                    continue
                is_scheduled = True
            elif active_shift == "Both":
                is_scheduled = True
            elif active_shift == "None":
                continue
            else:
                is_scheduled = (active_shift == u_shift)
                if not is_scheduled:
                    continue

        total_expected += 1

        # Check clock in scan
        scan = scanned_map.get(u_sn) or scanned_map.get(u_email)

        # Check Leave / Pass / Sick status
        on_leave, leave_label, leave_info = check_active_leave(u_sn, u_email, date_obj)

        status = "absent"
        status_label = "Absent"
        clock_in_time = None
        snapshot_id = None

        if scan:
            status = "present"
            status_label = "Present"
            clock_in_time = scan.get("time_str", "Scanned")
            snapshot_id = str(scan.get("snapshot_id")) if scan.get("snapshot_id") else None
            total_present += 1
            if u_cat == "nysc":
                nysc_present += 1
            elif u_cat == "it":
                it_present += 1
        elif on_leave:
            status = leave_label.lower().replace(" ", "_")
            status_label = leave_label
            if leave_label == "Sick":
                total_sick += 1
            else:
                total_leave += 1
        else:
            status = "absent"
            status_label = "Absent"
            total_absent += 1
            if u_cat == "nysc":
                nysc_absent += 1
            elif u_cat == "it":
                it_absent += 1

        passport = (
            user.get("passport_image")
            or user.get("profile_image")
            or user.get("passport_photo")
            or (user.get("onboarding_data", {}).get("step_1", {}).get("uploadPassport") if isinstance(user.get("onboarding_data"), dict) else None)
        )

        roster_item = {
            "sn": len(roster) + 1,
            "user_id": str(user["_id"]),
            "name": user.get("name") or f"{user.get('firstName', '')} {user.get('surName', '')}".strip() or "Staff Member",
            "service_number": u_sn,
            "email": u_email,
            "directorate": directorate_clean,
            "category": u_cat,
            "rank": user.get("rank") or user.get("rankOrGrade") or "",
            "appt": user.get("appt", ""),
            "shift_group": u_shift,
            "is_scheduled": True,
            "status": status,
            "status_label": status_label,
            "clock_in_time": clock_in_time,
            "snapshot_id": snapshot_id,
            "passport_photo": passport,
            "leave_info": leave_info,
        }
        roster.append(roster_item)

    summary = {
        "date_str": date_str,
        "day_of_week": day_of_week,
        "is_weekday": is_weekday,
        "directorate": directorate_clean,
        "ao_type": ao_type,
        "active_shift": active_shift,
        "total_nominal": total_nominal,
        "total_expected": total_expected,
        "total_present": total_present,
        "total_absent": total_absent,
        "total_leave": total_leave,
        "total_sick": total_sick,
        "total_nysc": total_nysc,
        "nysc_present": nysc_present,
        "nysc_absent": nysc_absent,
        "total_it": total_it,
        "it_present": it_present,
        "it_absent": it_absent,
        "group_a_count": group_a_count,
        "group_b_count": group_b_count,
        "attendance_rate": round((total_present / total_expected * 100), 1) if total_expected > 0 else 0,
    }

    return roster, summary, sanitize_doc(daily_state)


# ==============================================================================
# ROUTE: KIOSK CLOCK-IN VIEW (STANDALONE DEDICATED URL)
# ==============================================================================
@parade_state_routes.route("/clock-in", methods=["GET"])
@parade_state_routes.route("/clockin", methods=["GET"])
@parade_state_routes.route("/kiosk", methods=["GET"])
@parade_state_routes.route("/parade-kiosk", methods=["GET"])
@parade_state_routes.route("/attendance", methods=["GET"])
def clock_in_kiosk_view():
    """
    Renders the fullscreen high-tech clock-in kiosk for barcode scanning and silent photo capture.
    Can run completely standalone on its own dedicated URL upon application startup without requiring an AO.
    """
    user, role, directorate = get_current_user_and_role()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%A, %d %B %Y")

    # Fetch recent clock-ins for live feed display and total count today
    recent_clockins = list(db.parade_attendance.find({"date_str": today_str}).sort("clock_in_time", -1).limit(10))
    total_today_clockins = db.parade_attendance.count_documents({"date_str": today_str})

    return render_template(
        "clock_in_kiosk.html",
        user=user,
        role=role,
        directorate=directorate,
        today_str=today_str,
        time_str=time_str,
        recent_clockins=sanitize_doc(recent_clockins),
        total_today_clockins=total_today_clockins
    )


# ==============================================================================
# API: PROCESS BARCODE SCAN WITH SILENT SNAPSHOT
# ==============================================================================
def resolve_personnel_from_scan(raw_code):
    """
    Intelligently extracts personnel credentials from raw QR code or barcode data.
    Supports:
    1. Direct secure_code (e.g., SEC-85CE-55E7-2B25)
    2. Direct service_number (e.g., NA/09753, DSA/CIV/0245) or without slashes
    3. Official email address
    4. JSON-encoded QR payloads (e.g. {"code": "...", "service_number": "..."})
    5. URLs containing secure_code, service_number, or query params (?code=..., ?sn=...)
    6. Prefixed strings (e.g. STAFF:..., ID:..., SEC:...)
    7. 24-hour temporary passes (PASS24-{sn}-{id})
    8. MongoDB ObjectId
    """
    if not raw_code:
        return None

    raw_str = str(raw_code).strip().strip('"\'')
    candidates = [raw_str]

    # A. Check if it's JSON
    if (raw_str.startswith("{") and raw_str.endswith("}")) or (raw_str.startswith("[") and raw_str.endswith("]")):
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict):
                for k in ["secure_code", "secureCode", "code", "service_number", "serviceNumber", "sn", "email", "id", "userId"]:
                    val = parsed.get(k)
                    if val and isinstance(val, str):
                        candidates.append(val.strip())
        except Exception:
            pass

    # B. Check if it's a URL
    if "://" in raw_str or raw_str.startswith("http") or "/" in raw_str:
        try:
            parsed_url = urlparse(raw_str)
            qs = parse_qs(parsed_url.query)
            for param in ["code", "secure_code", "secureCode", "sn", "service_number", "id", "email"]:
                if param in qs and qs[param]:
                    candidates.append(qs[param][0].strip())
            path_parts = [p.strip() for p in parsed_url.path.split("/") if p.strip()]
            for part in reversed(path_parts):
                candidates.append(unquote(part))
        except Exception:
            pass

    # C. Check prefixes (e.g., STAFF:, ID:, SEC:, CODE:, PASS:)
    for prefix in ["STAFF:", "ID:", "SEC:", "CODE:", "SN:", "USER:"]:
        if raw_str.upper().startswith(prefix):
            candidates.append(raw_str[len(prefix):].strip())

    # D. Check 24-hour pass pattern (PASS24-{service_number}-{id})
    if raw_str.upper().startswith("PASS24-"):
        parts = raw_str.split("-")
        if len(parts) >= 2:
            candidates.append(parts[1].strip())

    # E. Check if raw_str contains SEC-XXXX-XXXX-XXXX anywhere inside it
    sec_match = re.search(r"SEC-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", raw_str.upper())
    if sec_match:
        candidates.append(sec_match.group(0))

    # Iterate through all unique candidate values and look up user
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        cand_clean = cand.strip().upper()

        # 1. Exact or case-insensitive match on secure_code / secureCode
        user = db.users.find_one({
            "$or": [
                {"secure_code": cand_clean},
                {"secureCode": cand_clean},
                {"secure_code": {"$regex": f"^{re.escape(cand)}$", "$options": "i"}},
                {"secureCode": {"$regex": f"^{re.escape(cand)}$", "$options": "i"}}
            ]
        })
        if user:
            return user

        # 2. Match on service_number
        user = db.users.find_one({
            "service_number": {"$regex": f"^{re.escape(cand)}$", "$options": "i"}
        })
        if user:
            return user

        # 2b. Match service_number with/without slashes/dashes/spaces
        clean_sn = cand_clean.replace("-", "/").replace(" ", "")
        user = db.users.find_one({
            "service_number": {"$regex": f"^{re.escape(clean_sn)}$", "$options": "i"}
        })
        if user:
            return user

        # 3. Match on email
        user = db.users.find_one({
            "email": {"$regex": f"^{re.escape(cand)}$", "$options": "i"}
        })
        if user:
            return user

        # 4. Check if cand is a valid ObjectId
        if ObjectId.is_valid(cand):
            user = db.users.find_one({"_id": ObjectId(cand)})
            if user:
                return user

    return None


@parade_state_routes.route("/api/parade/clock-in", methods=["POST"])
def api_parade_clock_in():
    """
    Receives scanned barcode code and base64 covert webcam snapshot.
    Identifies personnel, checks military vs civilian, logs attendance, and stores snapshot in GridFS.
    """
    data = request.get_json(silent=True) or {}
    barcode = str(data.get("barcode") or data.get("code") or "").strip()
    snapshot_base64 = data.get("snapshot") or data.get("image") or ""

    if not barcode:
        return jsonify({"status": "error", "message": "No barcode or QR code received."}), 400

    # Robust multi-format user lookup
    user = resolve_personnel_from_scan(barcode)

    if not user:
        return jsonify({
            "status": "not_found",
            "message": f"Unrecognized QR code or unregistered credentials: '{barcode}'. Please see your AO."
        }), 404

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_display = now.strftime("%I:%M %p")

    u_id = str(user["_id"])
    u_name = user.get("name") or f"{user.get('firstName', '')} {user.get('surName', '')}".strip() or "Staff Member"
    u_sn = str(user.get("service_number") or user.get("email")).upper()
    u_dir = str(user.get("directorate", "DOA")).strip().upper()
    u_cat = resolve_user_category(user)
    u_rank = user.get("rank") or user.get("rankOrGrade") or ""
    u_shift = str(user.get("shift_group", "A")).upper()

    # Route civilian vs military AO sheet
    ao_type = "military" if u_cat == "military" else "civilian"

    # ── Late-arrival detection: any clock-in at or after 09:00 is LATE ─────────
    LATE_HOUR, LATE_MINUTE = 9, 00
    is_late = (now.hour > LATE_HOUR) or (now.hour == LATE_HOUR and now.minute >= LATE_MINUTE)
    late_label = "Late Arrival" if is_late else "On Time"

    # Check if user already clocked in today
    existing = db.parade_attendance.find_one({
        "user_id": u_id,
        "date_str": today_str
    })
    if existing:
        return jsonify({
            "status": "already_clocked_in",
            "message": f"Already clocked in today at {existing.get('time_str', 'Earlier')}. Clock-out opens at 3:50 PM.",
            "user": {
                "name": u_name,
                "service_number": u_sn,
                "rank": u_rank,
                "directorate": u_dir,
                "category": u_cat,
                "ao_type": ao_type,
                "time_str": existing.get("time_str"),
                "snapshot_id": str(existing.get("snapshot_id", "")),
                "is_late": existing.get("is_late", False),
                "late_label": existing.get("late_label", "On Time"),
                "clock_out_time": existing.get("clock_out_time"),
                "clock_out_time_str": existing.get("clock_out_time_str")
            }
        }), 200

    # Save silent snapshot to GridFS if provided
    snapshot_id = None
    if snapshot_base64 and "base64," in snapshot_base64:
        try:
            header, encoded = snapshot_base64.split("base64,", 1)
            image_data = base64.b64decode(encoded)
            file_meta = {
                "user_id": u_id,
                "service_number": u_sn,
                "name": u_name,
                "date_str": today_str,
                "time_str": time_display,
                "content_type": "image/jpeg"
            }
            grid_file = fs.put(
                image_data,
                filename=f"snapshot_{u_sn}_{today_str}_{now.strftime('%H%M%S')}.jpg",
                metadata=file_meta
            )
            snapshot_id = str(grid_file)
        except Exception as snap_err:
            print(f"[PARADE SNAPSHOT] Failed saving snapshot to GridFS: {snap_err}")

    # Create new attendance record
    attendance_doc = {
        "user_id": u_id,
        "name": u_name,
        "service_number": u_sn,
        "email": user.get("email"),
        "directorate": u_dir,
        "category": u_cat,
        "rank": u_rank,
        "appt": user.get("appt", ""),
        "ao_type": ao_type,
        "shift_group": u_shift,
        "clock_in_time": now,
        "time_str": time_display,
        "date_str": today_str,
        "snapshot_id": snapshot_id,
        "is_late": is_late,
        "late_label": late_label,
        "status": "present",
        "created_at": datetime.utcnow()
    }
    ins_res = db.parade_attendance.insert_one(attendance_doc)
    attendance_doc["_id"] = str(ins_res.inserted_id)

    # Real-time websocket notification for open AO dashboards
    try:
        socket_payload = {
            "type": "parade_clockin",
            "attendance": sanitize_doc(attendance_doc)
        }
        socketio.emit("parade_clockin_event", socket_payload)
    except Exception as sock_err:
        print(f"[SOCKETIO] Error broadcasting clock-in event: {sock_err}")

    msg = f"Clock-in confirmed. Welcome {u_name} ({u_dir})."
    if is_late:
        msg = f"Late clock-in recorded at {time_display}. Welcome {u_name} ({u_dir})."

    return jsonify({
        "status": "success",
        "message": msg,
        "user": {
            "name": u_name,
            "service_number": u_sn,
            "rank": u_rank,
            "directorate": u_dir,
            "category": u_cat,
            "ao_type": ao_type,
            "shift_group": u_shift,
            "time_str": time_display,
            "snapshot_id": snapshot_id,
            "is_late": is_late,
            "late_label": late_label
        }
    }), 200


# ==============================================================================
# API: CLOCK-OUT
# ==============================================================================
@parade_state_routes.route("/api/parade/clock-out", methods=["POST"])
def api_parade_clock_out():
    """
    Records clock-out for a personnel member.
    - Clock-out is only allowed from 15:50 (3:50 PM) onwards.
    - Personnel must have clocked in today before they can clock out.
    - A second scan during the allowed window triggers clock-out.
    """
    data = request.get_json(silent=True) or {}
    barcode = str(data.get("barcode") or data.get("code") or "").strip()
    snapshot_base64 = data.get("snapshot") or data.get("image") or ""

    if not barcode:
        return jsonify({"status": "error", "message": "No barcode or QR code received."}), 400

    user = resolve_personnel_from_scan(barcode)
    if not user:
        return jsonify({
            "status": "not_found",
            "message": f"Unrecognized QR code or unregistered credentials: '{barcode}'. Please see your AO."
        }), 404

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    time_display = now.strftime("%I:%M %p")

    u_id = str(user["_id"])
    u_name = user.get("name") or f"{user.get('firstName', '')} {user.get('surName', '')}".strip() or "Staff Member"
    u_sn = str(user.get("service_number") or user.get("email")).upper()
    u_dir = str(user.get("directorate", "DOA")).strip().upper()
    u_cat = resolve_user_category(user)
    u_rank = user.get("rank") or user.get("rankOrGrade") or ""

    # ── Clock-out window: allowed from 15:50 (3:50 PM) ──────────────────────
    CLOCKOUT_HOUR, CLOCKOUT_MINUTE = 15, 50
    clockout_allowed = (now.hour > CLOCKOUT_HOUR) or (
        now.hour == CLOCKOUT_HOUR and now.minute >= CLOCKOUT_MINUTE
    )
    if not clockout_allowed:
        return jsonify({
            "status": "too_early",
            "message": "Clock-out is not yet open. It opens at 3:50 PM. Please wait.",
            "user": {
                "name": u_name,
                "service_number": u_sn,
                "rank": u_rank,
                "directorate": u_dir,
                "category": u_cat
            }
        }), 200

    # Must have clocked in today first
    existing = db.parade_attendance.find_one({"user_id": u_id, "date_str": today_str})
    if not existing:
        return jsonify({
            "status": "not_clocked_in",
            "message": f"{u_name} has no clock-in record for today. Please clock in first.",
            "user": {"name": u_name, "service_number": u_sn, "rank": u_rank, "directorate": u_dir}
        }), 200

    # Check if already clocked out
    if existing.get("clock_out_time"):
        return jsonify({
            "status": "already_clocked_out",
            "message": f"{u_name} already clocked out at {existing.get('clock_out_time_str', 'Earlier')} today.",
            "user": {
                "name": u_name,
                "service_number": u_sn,
                "rank": u_rank,
                "directorate": u_dir,
                "category": u_cat,
                "time_str": existing.get("time_str"),
                "clock_out_time_str": existing.get("clock_out_time_str")
            }
        }), 200

    # Save clock-out snapshot if provided
    clockout_snapshot_id = None
    if snapshot_base64 and "base64," in snapshot_base64:
        try:
            _, encoded = snapshot_base64.split("base64,", 1)
            image_data = base64.b64decode(encoded)
            file_meta = {"user_id": u_id, "service_number": u_sn, "name": u_name,
                         "date_str": today_str, "time_str": time_display,
                         "content_type": "image/jpeg", "event": "clock_out"}
            grid_file = fs.put(
                image_data,
                filename=f"clockout_{u_sn}_{today_str}_{now.strftime('%H%M%S')}.jpg",
                metadata=file_meta
            )
            clockout_snapshot_id = str(grid_file)
        except Exception as snap_err:
            print(f"[PARADE SNAPSHOT] Clock-out snapshot error: {snap_err}")

    # Record clock-out
    db.parade_attendance.update_one(
        {"_id": existing["_id"]},
        {"$set": {
            "clock_out_time": now,
            "clock_out_time_str": time_display,
            "clockout_snapshot_id": clockout_snapshot_id,
            "updated_at": datetime.utcnow()
        }}
    )

    # Broadcast clock-out event for live AO dashboards
    try:
        socketio.emit("parade_clockout_event", {
            "type": "parade_clockout",
            "user_id": u_id,
            "name": u_name,
            "service_number": u_sn,
            "directorate": u_dir,
            "clock_out_time_str": time_display
        })
    except Exception as sock_err:
        print(f"[SOCKETIO] Clock-out broadcast error: {sock_err}")

    return jsonify({
        "status": "success",
        "message": f"Clock-out recorded for {u_name} at {time_display}. Have a good evening!",
        "user": {
            "name": u_name,
            "service_number": u_sn,
            "rank": u_rank,
            "directorate": u_dir,
            "category": u_cat,
            "time_str": existing.get("time_str"),
            "clock_out_time_str": time_display,
            "clockout_snapshot_id": clockout_snapshot_id
        }
    }), 200


# ==============================================================================
# ROUTE: COVERT SNAPSHOT IMAGE STREAMING
# ==============================================================================
@parade_state_routes.route("/parade-state/snapshot/<snapshot_id>", methods=["GET"])
def get_parade_snapshot(snapshot_id):
    """Retrieves the covert snapshot image directly from GridFS."""
    if not snapshot_id or not ObjectId.is_valid(snapshot_id):
        return Response("Snapshot not found", status=404)
    try:
        grid_out = fs.get(ObjectId(snapshot_id))
        return Response(grid_out.read(), mimetype="image/jpeg")
    except Exception as e:
        return Response(f"Error fetching snapshot: {e}", status=404)


# ==============================================================================
# ROUTE: PARADE STATE DASHBOARD
# ==============================================================================
# AO roles that can manage and compile live parade state data
AO_ROLES = {"civilian_ao", "military_ao", "ao", "admin_officer"}

# Senior HQ roles that see ALL directorates' dispatched parade states.
# ONLY these 5 roles are permitted to see parade states across all directorates:
GLOBAL_VIEWER_ROLES = {"super_admin", "cdsa", "dcdsa", "civilian_head_cao", "rsm"}

# Roles that see dispatched parade states for their own directorate only
DIRECTORATE_VIEWER_ROLES = {
    "director", "dd", "ad", "so", "officer",
    "registry", "central_registry", "personnel", "civilian_head"
}


def is_user_global_viewer(user, role):
    """
    Checks if user is strictly authorized as a global viewer of all directorates' parade states.
    ONLY: Civilian Head CAO, RSM, CDSA, DCDSA, and Super Admin.
    Directors, Registries, and Directorate Civilian Heads are strictly directorate-scoped.
    """
    if not user or not role:
        return False
    clean_role = str(role).strip().lower()
    if clean_role in GLOBAL_VIEWER_ROLES:
        return True
    appt_str = str(user.get("appt") or "").strip().lower()
    if "rsm" in clean_role or "rsm" in appt_str:
        return True
    return False


@parade_state_routes.route("/parade-state", methods=["GET"])
@parade_state_routes.route("/parade_states", methods=["GET"])
def parade_state_dashboard_view():
    """
    Main Directorate Parade State entrypoint.
    - Administrative Officers (AO) see the live management & compilation view (parade_states.html) by default,
      or can switch to the dispatched cards feed via ?view=cards.
    - Directorate recipients (Registry of AO, Civilian Head of AO, Director of OA/directorate) see cards of
      daily parade states that have been dispatched to them.
    - Senior HQ (Civilian Head CAO, RSM, CDSA, DCDSA, Super Admin) see cards across ALL directorates.
    - Cards follow the First In Last Out (FILO) principle (newest dispatched on top) and can be searched
      by dates, directorates, AO type, or keywords.
    """
    if "user_email" not in session:
        return redirect(url_for("index"))

    user, role, user_dir = get_current_user_and_role()
    if not user:
        return redirect(url_for("index"))

    user_dir_clean = str(user_dir or "DOA").strip().upper()
    req_view = request.args.get("view", "").strip().lower()

    # ── 1. AO Live Compilation Management View ────────────────────────────────
    # AOs manage the live nominal roll, shift allocation, and dispatching.
    if role in AO_ROLES and req_view != "cards":
        target_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d")).strip()
        req_dir = request.args.get("directorate", "").strip().upper()
        req_ao_type = request.args.get("ao_type", "").strip().lower()

        directorate = req_dir if req_dir else user_dir_clean

        if req_ao_type in ["civilian", "military"]:
            ao_type = req_ao_type
        elif role == "military_ao":
            ao_type = "military"
        else:
            ao_type = "civilian"

        roster, summary, daily_state = compile_directorate_parade_state(directorate, ao_type, target_date)

        available_directorates = [
            {"code": "DOA", "name": "Directorate of Administration"},
            {"code": "DCS", "name": "Directorate of Communications Satellite"},
            {"code": "DNPT", "name": "Directorate of Navigation & Positioning"},
            {"code": "DFA", "name": "Directorate of Finance & Accounts"},
            {"code": "DLSO", "name": "Directorate of Launch Services & Ops"},
            {"code": "DEO", "name": "Directorate of Earth Observation"},
            {"code": "DCYBER", "name": "Directorate of Cyber Security"},
            {"code": "DPPR", "name": "Directorate of Policy, Planning & Research"},
            {"code": "DLOG", "name": "Directorate of Logistics"},
            {"code": "DELSPACE", "name": "Delspace"}
        ]

        return render_template(
            "parade_states.html",
            user=user,
            role=role,
            is_global_officer=False,
            active_page="parade_states",
            target_date=target_date,
            directorate=directorate,
            ao_type=ao_type,
            roster=roster,
            summary=summary,
            daily_state=daily_state,
            directorates=available_directorates,
            is_ao=True
        )

    # ── 2. Dispatched Parade State Cards View (FILO Queue) ─────────────────────
    # Determine global viewer vs directorate-scoped viewer
    is_global_viewer = is_user_global_viewer(user, role)

    # Base query: only dispatched parade states
    card_query = {"is_dispatched": True}

    # Directorate Access Control:
    # ONLY Civilian Head CAO, RSM, CDSA, DCDSA, and Super Admin see all directorates.
    # Registry, Civilian Head, and Director are strictly scoped to their own directorate!
    selected_dir = request.args.get("directorate", "").strip().upper()
    if is_global_viewer:
        if selected_dir and selected_dir != "ALL":
            card_query["directorate"] = {"$regex": f"^{re.escape(selected_dir)}$", "$options": "i"}
    else:
        # Strictly locked to user's directorate — ignore any URL tampering
        card_query["directorate"] = {"$regex": f"^{re.escape(user_dir_clean)}$", "$options": "i"}
        selected_dir = user_dir_clean

    # Date Filtering (Search by date, date range, or presets):
    target_date = request.args.get("date", "").strip()
    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()
    date_preset = request.args.get("preset", "").strip().lower()

    if target_date:
        card_query["date_str"] = target_date
    elif from_date or to_date:
        date_sub = {}
        if from_date:
            date_sub["$gte"] = from_date
        if to_date:
            date_sub["$lte"] = to_date
        card_query["date_str"] = date_sub
    elif date_preset == "today":
        card_query["date_str"] = datetime.now().strftime("%Y-%m-%d")
    elif date_preset == "yesterday":
        card_query["date_str"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    elif date_preset == "week":
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        card_query["date_str"] = {"$gte": week_ago}
    elif date_preset == "month":
        month_start = datetime.now().strftime("%Y-%m-01")
        card_query["date_str"] = {"$gte": month_start}
    # Note: If no date filter is passed, we intentionally do NOT filter by date so that
    # recipients see all dispatched returns in First In Last Out (FILO) order!

    # AO Type Filter (Civilian vs Military):
    selected_ao_type = request.args.get("ao_type", "").strip().lower()
    if selected_ao_type in ["civilian", "military"]:
        card_query["ao_type"] = selected_ao_type

    # Multi-field Text Search (Keywords: directorate, remarks, AO name, batch, etc.):
    search_kw = request.args.get("search", "").strip()
    if search_kw:
        kw_re = {"$regex": re.escape(search_kw), "$options": "i"}
        card_query["$or"] = [
            {"directorate": kw_re},
            {"date_str": kw_re},
            {"date": kw_re},
            {"dispatched_by_name": kw_re},
            {"dispatched_by": kw_re},
            {"remarks": kw_re},
            {"remark": kw_re},
            {"ao_type": kw_re},
            {"batch": kw_re}
        ]

    # Sorting Principle: First In Last Out (FILO = newest dispatched on top)
    sort_param = request.args.get("sort", "filo").strip().lower()
    if sort_param == "oldest":
        sort_spec = [("dispatched_at", 1), ("date_str", 1), ("created_at", 1)]
    elif sort_param == "highest_rate":
        sort_spec = [("on_parade", -1), ("dispatched_at", -1)]
    elif sort_param == "lowest_rate":
        sort_spec = [("on_parade", 1), ("dispatched_at", -1)]
    else:
        # Default: First In Last Out (FILO)
        sort_spec = [("dispatched_at", -1), ("date_str", -1), ("created_at", -1)]

    raw_states = list(db.daily_parade_states.find(card_query).sort(sort_spec))
    dispatched_states = sanitize_doc(raw_states)

    # Calculate executive aggregates across returned cards
    total_cards = len(dispatched_states)
    total_strength_sum = sum(int(s.get("strength") or 0) for s in dispatched_states)
    total_present_sum = sum(int(s.get("on_parade") or 0) for s in dispatched_states)
    total_absent_sum = sum(int(s.get("absent") or 0) for s in dispatched_states)
    total_leave_sum = sum(int(s.get("leave") or 0) + int(s.get("sick") or 0) + int(s.get("pass") or 0) for s in dispatched_states)
    avg_attendance_rate = round((total_present_sum / total_strength_sum * 100), 1) if total_strength_sum > 0 else 0

    latest_dispatch_str = None
    if dispatched_states:
        first_card = dispatched_states[0]
        latest_dispatch_str = first_card.get("dispatched_at") or first_card.get("date_str") or first_card.get("date")

    stats_summary = {
        "total_cards": total_cards,
        "total_strength": total_strength_sum,
        "total_present": total_present_sum,
        "total_absent": total_absent_sum,
        "total_leave": total_leave_sum,
        "avg_attendance_rate": avg_attendance_rate,
        "latest_dispatch_str": latest_dispatch_str
    }

    available_directorates = [
        {"code": "DOA", "name": "Directorate of Administration"},
        {"code": "DCS", "name": "Directorate of Communications Satellite"},
        {"code": "DNPT", "name": "Directorate of Navigation & Positioning"},
        {"code": "DFA", "name": "Directorate of Finance & Accounts"},
        {"code": "DLSO", "name": "Directorate of Launch Services & Ops"},
        {"code": "DEO", "name": "Directorate of Earth Observation"},
        {"code": "DCYBER", "name": "Directorate of Cyber Security"},
        {"code": "DPPR", "name": "Directorate of Policy, Planning & Research"},
        {"code": "DLOG", "name": "Directorate of Logistics"},
        {"code": "DELSPACE", "name": "Delspace"}
    ]

    return render_template(
        "parade_state_cards.html",
        user=user,
        role=role,
        is_global_viewer=is_global_viewer,
        active_page="parade_states",
        target_date=target_date,
        from_date=from_date,
        to_date=to_date,
        date_preset=date_preset,
        selected_directorate=selected_dir,
        selected_ao_type=selected_ao_type,
        search_kw=search_kw,
        sort_param=sort_param,
        user_dir=user_dir_clean,
        dispatched_states=dispatched_states,
        directorates=available_directorates,
        stats_summary=stats_summary,
        is_ao=(role in AO_ROLES)
    )


# ==============================================================================
# API: SET ACTIVE SHIFT (GROUP A, B, BOTH, NONE)
# ==============================================================================
@parade_state_routes.route("/api/parade/set-shift", methods=["POST"])
def api_set_daily_shift():
    """Allows Civilian AO to select the active working shift (Group A, Group B, Both, None)."""
    if "user_email" not in session:
        return jsonify({"status": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    directorate = str(data.get("directorate", "DOA")).strip().upper()
    ao_type = str(data.get("ao_type", "civilian")).strip().lower()
    date_str = str(data.get("date_str") or datetime.now().strftime("%Y-%m-%d")).strip()
    shift = str(data.get("shift", "A")).strip()

    batch_val = "A" if shift == "A" else ("B" if shift == "B" else "ALL")
    now_dt = datetime.utcnow()

    db.daily_parade_states.update_one(
        {"directorate": directorate, "ao_type": ao_type, "date_str": date_str},
        {
            "$set": {
                "active_shift": shift,
                "batch": batch_val,
                "updated_at": now_dt
            }
            # No upsert — document is always pre-created when the AO opens the parade state page
        }
    )

    return jsonify({
        "status": "success",
        "message": f"Active shift updated to Group '{shift}' for {directorate} ({date_str}).",
        "active_shift": shift
    }), 200


# ==============================================================================
# API: ASSIGN GROUP A OR B TO PERSONNEL
# ==============================================================================
@parade_state_routes.route("/api/parade/assign-group", methods=["POST"])
def api_assign_personnel_group():
    """Allows Civilian AO to divide personnel into Group A or Group B."""
    if "user_email" not in session:
        return jsonify({"status": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    user_ids = data.get("user_ids")
    group = str(data.get("group", "A")).strip().upper()

    if group not in ["A", "B"]:
        return jsonify({"status": "error", "message": "Shift group must be 'A' or 'B'."}), 400

    targets = []
    if user_ids and isinstance(user_ids, list):
        for uid in user_ids:
            if ObjectId.is_valid(uid):
                targets.append(ObjectId(uid))
    elif user_id and ObjectId.is_valid(user_id):
        targets.append(ObjectId(user_id))

    if not targets:
        return jsonify({"status": "error", "message": "No valid personnel specified."}), 400

    result = db.users.update_many(
        {"_id": {"$in": targets}},
        {"$set": {"shift_group": group, "updated_at": datetime.utcnow()}}
    )

    return jsonify({
        "status": "success",
        "message": f"Successfully assigned {result.modified_count} personnel to Group {group}.",
        "group": group,
        "count": result.modified_count
    }), 200


# ==============================================================================
# API: ONE-CLICK DISPATCH PARADE STATE
# ==============================================================================
@parade_state_routes.route("/api/parade/dispatch", methods=["POST"])
def api_dispatch_parade_state():
    """
    One-click action to freeze and broadcast the compiled parade state to:
    1. civilian_head_cao (Civilian Head CAO)
    2. Director of the AO's directorate
    3. Registry of the AO's directorate
    4. Civilian head of the AO's directorate
    5. RSM (Regimental Sergeant Major)
    6. Senior HQ (CDSA, DCDSA, Super Admin)
    """
    if "user_email" not in session:
        return jsonify({"status": "unauthorized", "message": "Session expired"}), 401

    try:
        user, role, user_dir = get_current_user_and_role()
        if not user:
            return jsonify({"status": "unauthorized", "message": "User not found"}), 401

        data = request.get_json(silent=True) or {}
        directorate = str(data.get("directorate") or user_dir or "DOA").strip().upper()
        ao_type = str(data.get("ao_type", "civilian")).strip().lower()
        date_str = str(data.get("date_str") or datetime.now().strftime("%Y-%m-%d")).strip()
        remarks = str(data.get("remarks", "")).strip()

        # Compile the final snapshot
        roster, summary, _ = compile_directorate_parade_state(directorate, ao_type, date_str)

        sender_email = session.get("user_email")
        sender_name = user.get("name") or "Administrative Officer"

        # Define target recipient queries:
        target_queries = [
            # 1. Civilian Head CAO
            {"role": "civilian_head_cao"},
            # 2. Director of the AO's directorate
            {"role": "director", "directorate": {"$regex": f"^{re.escape(directorate)}$", "$options": "i"}},
            # 3. Registry of the AO's directorate
            {"role": {"$in": ["registry", "central_registry"]}, "directorate": {"$regex": f"^{re.escape(directorate)}$", "$options": "i"}},
            # 4. Civilian Head of the AO's directorate
            {"role": "civilian_head", "directorate": {"$regex": f"^{re.escape(directorate)}$", "$options": "i"}},
            # 5. RSM (Regimental Sergeant Major)
            {"$or": [
                {"role": "rsm"},
                {"appt": {"$regex": "rsm", "$options": "i"}},
                {"role": "personnel", "appt": {"$regex": "rsm", "$options": "i"}}
            ]},
            # 6. Senior HQ Executives
            {"role": {"$in": ["cdsa", "dcdsa", "super_admin"]}}
        ]

        recipients_found = []
        for q in target_queries:
            matched = list(db.users.find(q))
            recipients_found.extend(matched)

        # Remove duplicates
        unique_recipients = {}
        for r in recipients_found:
            unique_recipients[str(r["_id"])] = r

        dispatched_recipients_list = []
        now_utc = datetime.utcnow()

        # Create notifications and socket alerts for each recipient
        for r_id, r_doc in unique_recipients.items():
            r_email = str(r_doc.get("email") or "")
            r_sn = str(r_doc.get("service_number") or r_email)
            dispatched_recipients_list.append({
                "user_id": r_id,
                "email": r_email,
                "role": r_doc.get("role"),
                "name": r_doc.get("name")
            })

            try:
                notif_doc = {
                    "type": "parade_state",
                    "action": "parade_state_dispatched",
                    "title": f"Daily Parade State Dispatched - {directorate}",
                    "applicationId": ObjectId("000000000000000000000000"),
                    "message": (
                        f"The official {ao_type.title()} Parade State for {directorate} on {date_str} "
                        f"has been submitted by {sender_name}. Total Present: {summary['total_present']}/{summary['total_expected']}."
                    ),
                    "status": "unread",
                    "target": {
                        "type": "user",
                        "userId": r_sn,
                        "email": r_email
                    },
                    "link": f"/parade-state?date={date_str}&directorate={directorate}&ao_type={ao_type}",
                    "directorate": directorate,
                    "ao_type": ao_type,
                    "date_str": date_str,
                    "readBy": [],
                    "isActive": True,
                    "is_active": True,
                    "createdAt": now_utc,
                    "created_at": now_utc
                }
                db.notifications.insert_one(notif_doc)
            except Exception as notif_err:
                print(f"[PARADE DISPATCH] Notification insert skipped/error: {notif_err}")

            try:
                socketio.emit(
                    "new_notification",
                    {
                        "title": f"Daily Parade State Dispatched - {directorate}",
                        "message": (
                            f"The official {ao_type.title()} Parade State for {directorate} on {date_str} "
                            f"has been submitted by {sender_name}."
                        ),
                        "link": f"/parade-state?date={date_str}&directorate={directorate}&ao_type={ao_type}"
                    },
                    room=f"USER_{r_email}"
                )
            except Exception as e:
                print(f"[PARADE DISPATCH] Socket notification error: {e}")

        active_shift = summary.get("active_shift", "ALL")
        batch_val = "A" if active_shift == "A" else ("B" if active_shift == "B" else "ALL")

        dispatch_set = {
            # Stats counts (validator required fields — safe to update)
            "strength": int(summary.get("total_expected", 0)),
            "on_parade": int(summary.get("total_present", 0)),
            "sick": int(summary.get("total_sick", 0)),
            "leave": int(summary.get("total_leave", 0)),
            "pass": 0,
            "hospital": 0,
            "course": 0,
            "absent": int(summary.get("total_absent", 0)),
            "awol": 0,
            "excuse_duty": 0,
            "study_leave": 0,
            "cds": 0,
            "details": {
                "absent": [p["name"] for p in roster if p["status"] == "absent"],
                "leave":  [p["name"] for p in roster if p["status"] == "on_leave"],
                "pass":   [p["name"] for p in roster if p["status"] == "on_pass"],
                "sick":   [p["name"] for p in roster if p["status"] == "sick"],
            },
            "roster": sanitize_doc(roster),
            "remark": remarks,
            "createdBy": sender_email or "system",
            # Dispatch metadata
            "is_dispatched": True,
            "dispatched_at": now_utc,
            "dispatched_by": sender_email,
            "dispatched_by_name": sender_name,
            "remarks": remarks,
            "summary": summary,
            "dispatched_to": [
                "civilian_head_cao",
                "super_admin",
                "cdsa",
                "dcdsa",
                f"director_{directorate}",
                f"registry_{directorate}",
                f"civilian_head_{directorate}",
                "rsm"
            ],
            "recipient_details": dispatched_recipients_list,
            "updated_at": now_utc
        }

        db.daily_parade_states.update_one(
            {"directorate": directorate, "ao_type": ao_type, "date_str": date_str},
            {"$set": dispatch_set}
        )

        return jsonify({
            "status": "success",
            "message": (
                f"Official Parade State for {directorate} ({date_str}) successfully dispatched at "
                f"{now_utc.strftime('%H:%M:%S UTC')} to Director, Civilian Head, Directorate Registry, Civilian Head CAO, and RSM."
            ),
            "dispatched_at": now_utc.strftime("%d-%b-%Y %I:%M %p"),
            "recipient_count": len(unique_recipients)
        }), 200

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Server encountered an error while dispatching: {str(exc)}"
        }), 500


# ==============================================================================
# API: FETCH NOMINAL ROLL & BREAKDOWN FOR DISPATCHED RETURN CARD
# ==============================================================================
@parade_state_routes.route("/api/parade/card-details/<state_id>", methods=["GET"])
def api_get_parade_card_details(state_id):
    """
    Returns full nominal roll breakdown, photos, and stats for a specific dispatched return.
    Strictly verifies permissions:
    - Global viewers (Civilian Head CAO, RSM, CDSA, DCDSA, Super Admin) can view any directorate's return.
    - Directorate viewers can ONLY view returns dispatched for their own directorate!
    """
    if "user_email" not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    user, role, user_dir = get_current_user_and_role()
    if not user:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    if not state_id or not ObjectId.is_valid(state_id):
        return jsonify({"status": "error", "message": "Invalid parade return ID"}), 400

    state_doc = db.daily_parade_states.find_one({"_id": ObjectId(state_id)})
    if not state_doc:
        return jsonify({"status": "error", "message": "Parade state return not found"}), 404

    target_dir = str(state_doc.get("directorate", "")).strip().upper()
    user_dir_clean = str(user_dir or "DOA").strip().upper()
    is_global = is_user_global_viewer(user, role)

    # Scoping security check
    if not is_global and target_dir != user_dir_clean:
        return jsonify({
            "status": "forbidden",
            "message": f"Access denied: You are only authorized to view parade state returns for {user_dir_clean}."
        }), 403

    # Check if roster is already saved in state_doc
    roster = state_doc.get("roster")
    summary = state_doc.get("summary")

    if not roster or not summary:
        # Reconstruct on the fly if needed
        roster, summary, _ = compile_directorate_parade_state(
            target_dir,
            state_doc.get("ao_type", "civilian"),
            state_doc.get("date_str") or state_doc.get("date")
        )

    return jsonify({
        "status": "success",
        "state": sanitize_doc(state_doc),
        "summary": sanitize_doc(summary),
        "roster": sanitize_doc(roster)
    }), 200


# ==============================================================================
# ROUTE: OFFICIAL PRINTABLE PARADE STATE RETURN
# ==============================================================================
@parade_state_routes.route("/parade-state/print", methods=["GET"])
def print_parade_state_view():
    """Renders the official military-grade printable/PDF proforma sheet of the Parade State."""
    if "user_email" not in session:
        return redirect(url_for("index"))

    user, role, user_dir = get_current_user_and_role()
    target_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d")).strip()
    directorate = request.args.get("directorate", user_dir or "DOA").strip().upper()
    ao_type = request.args.get("ao_type", "civilian").strip().lower()

    roster, summary, daily_state = compile_directorate_parade_state(directorate, ao_type, target_date)

    return render_template(
        "parade_state_print.html",
        user=user,
        directorate=directorate,
        ao_type=ao_type,
        target_date=target_date,
        roster=roster,
        summary=summary,
        daily_state=daily_state,
        print_date=datetime.now().strftime("%d-%b-%Y %H:%M:%S")
    )
