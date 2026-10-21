from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime, timedelta
from pymongo import MongoClient
import uuid

kitchen_routes = Blueprint('kitchen_routes', __name__)

client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]


def sanitize_doc(doc):
    if not doc:
        return doc
    if isinstance(doc, list):
        return [sanitize_doc(item) for item in doc]
    if isinstance(doc, dict):
        clean = {}
        for k, v in doc.items():
            if isinstance(v, ObjectId):
                clean[k] = str(v)
            elif isinstance(v, datetime):
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
    if 'user_email' not in session:
        return None, None
    email = session.get('user_email')
    user = db.users.find_one({"email": email})
    role = user.get('role', '') if user else ''
    return sanitize_doc(user), role


@kitchen_routes.route('/kitchen_dashboard', methods=['GET'])
def kitchen_dashboard_view():
    if 'user_email' not in session:
        flash("Please log in to access the kitchen scanner dashboard.")
        return redirect(url_for('index'))

    user, role = get_current_user_and_role()
    if not user or role not in ['kitchen', 'catering_officer', 'super_admin']:
        flash("Unauthorized access to kitchen scanner dashboard.")
        return redirect(url_for('dashboard'))

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)

    today_scans = list(db.meal_scans.find({
        "scanned_at": {"$gte": today_start, "$lt": today_end}
    }).sort("scanned_at", -1))

    for scan in today_scans:
        scan['_id'] = str(scan['_id'])
        if isinstance(scan.get('scanned_at'), datetime):
            scan['time_str'] = scan['scanned_at'].strftime("%I:%M %p")
        elif isinstance(scan.get('scanned_at'), str):
            try:
                dt = datetime.fromisoformat(scan['scanned_at'])
                scan['time_str'] = dt.strftime("%I:%M %p")
            except Exception:
                scan['time_str'] = scan.get('scanned_at', '')

    return render_template(
        'kitchen_dashboard.html',
        user=sanitize_doc(user),
        today_scans=sanitize_doc(today_scans),
        today_count=len(today_scans),
        active_page='dashboard'
    )


@kitchen_routes.route('/api/kitchen/scan', methods=['POST'])
def kitchen_scan_meal():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized session."}), 401

    user_scanner, scanner_role = get_current_user_and_role()
    if scanner_role not in ['kitchen', 'catering_officer', 'super_admin']:
        return jsonify({"status": "error", "message": "Access denied. Only kitchen staff can scan meal passes."}), 403

    data = request.get_json() or {}
    scanned_code = (data.get('code') or '').strip()

    if not scanned_code:
        return jsonify({"status": "error", "message": "No barcode or QR code scanned."}), 400

    # 1. First check if code is a 24-hour temporary pass token
    now = datetime.now()
    temp_pass = db.temp_meal_passes.find_one({
        "pass_code": scanned_code,
        "is_used": False,
        "expires_at": {"$gte": now}
    })

    target_user = None
    is_temp_override = False

    if temp_pass:
        is_temp_override = True
        target_user = db.users.find_one({"service_number": temp_pass.get("service_number")}) or \
                      db.users.find_one({"_id": ObjectId(temp_pass["user_id"])}) if temp_pass.get("user_id") else None

    # 2. If not a temp pass, look up target user by secure_code, secureCode, service_number, or email
    if not target_user:
        target_user = db.users.find_one({
            "$or": [
                {"secure_code": scanned_code},
                {"secureCode": scanned_code},
                {"service_number": scanned_code.upper()},
                {"email": scanned_code.lower()}
            ]
        })

    # Fallback search in meal_tickets registry if not in users
    if not target_user:
        mt_user = db.meal_tickets.find_one({"service_number": scanned_code.upper()})
        if mt_user:
            target_user = {
                "name": mt_user.get("name", "Staff Member"),
                "service_number": mt_user.get("service_number", scanned_code.upper()),
                "directorate": mt_user.get("directorate", "DOA"),
                "category": mt_user.get("category", "civilian"),
                "role": mt_user.get("role", "personnel"),
                "rank": "Staff",
                "secure_code": scanned_code,
                "secureCode": scanned_code
            }

    if not target_user:
        return jsonify({
            "status": "error",
            "message": f"Unrecognized barcode or user record not found: {scanned_code}"
        }), 440

    service_num = target_user.get("service_number", "UNKNOWN")
    user_name = target_user.get("name") or f"{target_user.get('firstname', '')} {target_user.get('surname', '')}".strip() or "Staff Member"

    # Date range calculations
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())  # Monday 00:00:00
    week_end = week_start + timedelta(days=7)

    # If it's NOT a 24-hr temp pass override, check limits: 1 per day, 2 per week
    if not is_temp_override:
        # Check if already scanned today
        scans_today = db.meal_scans.count_documents({
            "service_number": service_num,
            "scanned_at": {"$gte": today_start, "$lt": today_end}
        })

        if scans_today >= 1:
            return jsonify({
                "status": "limit_exceeded",
                "reason": "daily_limit",
                "message": f"{user_name} ({service_num}) HAS ALREADY BEEN SERVED FOR TODAY!",
                "user": {
                    "name": user_name,
                    "service_number": service_num,
                    "directorate": target_user.get("directorate", "DOA"),
                    "category": target_user.get("category", "civilian"),
                    "rank": target_user.get("rank") or target_user.get("appt") or "Staff"
                }
            })

        # Check if weekly limit reached (2 per week)
        scans_this_week = db.meal_scans.count_documents({
            "service_number": service_num,
            "scanned_at": {"$gte": week_start, "$lt": week_end}
        })

        if scans_this_week >= 2:
            return jsonify({
                "status": "limit_exceeded",
                "reason": "weekly_limit",
                "message": f"{user_name} ({service_num}) HAS ALREADY REACHED THE MAXIMUM WEEKLY LIMIT (2 SCANS / WEEK)!",
                "user": {
                    "name": user_name,
                    "service_number": service_num,
                    "directorate": target_user.get("directorate", "DOA"),
                    "category": target_user.get("category", "civilian"),
                    "rank": target_user.get("rank") or target_user.get("appt") or "Staff"
                }
            })

    # Mark 24-hr temp pass as used if applicable
    if temp_pass:
        db.temp_meal_passes.update_one(
            {"_id": temp_pass["_id"]},
            {"$set": {"is_used": True, "used_at": now, "used_by": session.get('user_email')}}
        )

    # Record valid scan in db.meal_scans
    scan_record = {
        "user_id": str(target_user.get("_id", "")),
        "name": user_name,
        "service_number": service_num,
        "directorate": target_user.get("directorate", "DOA").upper(),
        "category": target_user.get("category", "civilian").lower(),
        "role": target_user.get("role", "personnel"),
        "rank": target_user.get("rank") or target_user.get("appt") or "Staff",
        "secure_code": target_user.get("secure_code") or target_user.get("secureCode") or scanned_code,
        "is_temp_override": is_temp_override,
        "scanned_at": now,
        "scanned_by": session.get("user_email")
    }

    db.meal_scans.insert_one(scan_record)

    override_tag = " (24h Temporary Pass Override)" if is_temp_override else ""
    return jsonify({
        "status": "success",
        "message": f"READY TO BE SERVED{override_tag}",
        "user": {
            "name": user_name,
            "service_number": service_num,
            "directorate": target_user.get("directorate", "DOA"),
            "category": target_user.get("category", "civilian"),
            "rank": target_user.get("rank") or target_user.get("appt") or "Staff",
            "time_str": now.strftime("%I:%M %p")
        }
    })


@kitchen_routes.route('/api/kitchen/today_feed', methods=['GET'])
def get_today_feed():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)

    scans = list(db.meal_scans.find({
        "scanned_at": {"$gte": today_start, "$lt": today_end}
    }).sort("scanned_at", -1))

    feed_items = []
    for s in scans:
        t_str = s['scanned_at'].strftime("%I:%M %p") if isinstance(s.get('scanned_at'), datetime) else ''
        feed_items.append({
            "id": str(s['_id']),
            "name": s.get("name", "Staff Member"),
            "service_number": s.get("service_number", ""),
            "directorate": s.get("directorate", "DOA"),
            "category": s.get("category", "civilian"),
            "rank": s.get("rank", "Staff"),
            "time_str": t_str,
            "is_temp_override": s.get("is_temp_override", False)
        })

    return jsonify({"status": "success", "count": len(feed_items), "scans": feed_items})


@kitchen_routes.route('/api/meal/search_users', methods=['GET'])
def search_users_api():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"status": "success", "users": []})

    regex = {"$regex": query, "$options": "i"}
    matched = list(db.users.find({
        "$or": [
            {"name": regex},
            {"firstname": regex},
            {"surname": regex},
            {"service_number": regex},
            {"email": regex},
            {"directorate": regex}
        ]
    }, {
        "name": 1, "service_number": 1, "directorate": 1, "category": 1, "role": 1, "email": 1, "rank": 1, "appt": 1, "secure_code": 1, "secureCode": 1
    }).sort("name", 1).limit(20))

    return jsonify({"status": "success", "users": sanitize_doc(matched)})


@kitchen_routes.route('/meal_statistics', methods=['GET'])
def meal_statistics_view():
    if 'user_email' not in session:
        flash("Please log in to access meal statistics.")
        return redirect(url_for('index'))

    user, role = get_current_user_and_role()
    allowed_roles = ['kitchen', 'catering_officer', 'super_admin', 'cdsa', 'dcdsa', 'civilian_head_cao', 'director']

    if not user or role not in allowed_roles:
        flash("You do not have permission to view meal statistics.")
        return redirect(url_for('dashboard'))

    now = datetime.now()

    # 1. Parse Daily Filter Date (defaults to today)
    req_date = request.args.get('date', '').strip()
    try:
        if req_date:
            daily_dt = datetime.strptime(req_date, "%Y-%m-%d")
        else:
            daily_dt = datetime(now.year, now.month, now.day)
    except Exception:
        daily_dt = datetime(now.year, now.month, now.day)

    daily_start = datetime(daily_dt.year, daily_dt.month, daily_dt.day)
    daily_end = daily_start + timedelta(days=1)

    # 2. Parse Weekly Filter Date (defaults to current week)
    req_week = request.args.get('week_date', '').strip()
    try:
        if req_week:
            week_dt = datetime.strptime(req_week, "%Y-%m-%d")
        else:
            week_dt = datetime(now.year, now.month, now.day)
    except Exception:
        week_dt = datetime(now.year, now.month, now.day)

    week_start = datetime(week_dt.year, week_dt.month, week_dt.day) - timedelta(days=week_dt.weekday())
    week_end = week_start + timedelta(days=7)

    # 3. Parse Monthly Filter Date (defaults to current month)
    req_month = request.args.get('month', '').strip()
    try:
        if req_month:
            m_parts = req_month.split('-')
            m_year, m_month = int(m_parts[0]), int(m_parts[1])
            month_start = datetime(m_year, m_month, 1)
        else:
            month_start = datetime(now.year, now.month, 1)
    except Exception:
        month_start = datetime(now.year, now.month, 1)

    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1)
    else:
        month_end = datetime(month_start.year, month_start.month + 1, 1)

    today_scans = list(db.meal_scans.find({"scanned_at": {"$gte": daily_start, "$lt": daily_end}}).sort("scanned_at", -1))
    week_scans = list(db.meal_scans.find({"scanned_at": {"$gte": week_start, "$lt": week_end}}).sort("scanned_at", -1))
    month_scans = list(db.meal_scans.find({"scanned_at": {"$gte": month_start, "$lt": month_end}}).sort("scanned_at", -1))

    for item in today_scans:
        item['_id'] = str(item['_id'])
        if isinstance(item.get('scanned_at'), datetime):
            item['date_str'] = item['scanned_at'].strftime("%Y-%m-%d %I:%M %p")
    for item in week_scans:
        item['_id'] = str(item['_id'])
        if isinstance(item.get('scanned_at'), datetime):
            item['date_str'] = item['scanned_at'].strftime("%Y-%m-%d %I:%M %p")
    for item in month_scans:
        item['_id'] = str(item['_id'])
        if isinstance(item.get('scanned_at'), datetime):
            item['date_str'] = item['scanned_at'].strftime("%Y-%m-%d %I:%M %p")

    return render_template(
        'meal_statistics.html',
        user=sanitize_doc(user),
        today_scans=sanitize_doc(today_scans),
        week_scans=sanitize_doc(week_scans),
        month_scans=sanitize_doc(month_scans),
        today_count=len(today_scans),
        week_count=len(week_scans),
        month_count=len(month_scans),
        filter_date_str=daily_start.strftime("%Y-%m-%d"),
        filter_week_date_str=week_start.strftime("%Y-%m-%d"),
        filter_week_label=f"Week of {week_start.strftime('%b %d, %Y')} to {(week_end - timedelta(days=1)).strftime('%b %d, %Y')}",
        filter_month_str=month_start.strftime("%Y-%m"),
        filter_month_label=month_start.strftime("%B %Y"),
        active_page='meal_statistics'
    )


@kitchen_routes.route('/api/kitchen/stats_filter', methods=['GET'])
def api_stats_filter():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    user, role = get_current_user_and_role()
    allowed_roles = ['kitchen', 'catering_officer', 'super_admin', 'cdsa', 'dcdsa', 'civilian_head_cao', 'director']
    if role not in allowed_roles:
        return jsonify({"status": "error", "message": "Permission denied"}), 403

    now = datetime.now()
    filter_type = request.args.get('type', 'daily').strip().lower()
    value = request.args.get('value', '').strip()

    items = []

    if filter_type == 'daily':
        try:
            dt = datetime.strptime(value, "%Y-%m-%d") if value else datetime(now.year, now.month, now.day)
        except Exception:
            dt = datetime(now.year, now.month, now.day)

        start = datetime(dt.year, dt.month, dt.day)
        end = start + timedelta(days=1)
        raw_items = list(db.meal_scans.find({"scanned_at": {"$gte": start, "$lt": end}}).sort("scanned_at", -1))

    elif filter_type == 'weekly':
        try:
            dt = datetime.strptime(value, "%Y-%m-%d") if value else datetime(now.year, now.month, now.day)
        except Exception:
            dt = datetime(now.year, now.month, now.day)

        start = datetime(dt.year, dt.month, dt.day) - timedelta(days=dt.weekday())
        end = start + timedelta(days=7)
        raw_items = list(db.meal_scans.find({"scanned_at": {"$gte": start, "$lt": end}}).sort("scanned_at", -1))

    elif filter_type == 'monthly':
        try:
            parts = value.split('-')
            m_year, m_month = int(parts[0]), int(parts[1])
            start = datetime(m_year, m_month, 1)
        except Exception:
            start = datetime(now.year, now.month, 1)

        if start.month == 12:
            end = datetime(start.year + 1, 1, 1)
        else:
            end = datetime(start.year, start.month + 1, 1)

        raw_items = list(db.meal_scans.find({"scanned_at": {"$gte": start, "$lt": end}}).sort("scanned_at", -1))
    else:
        raw_items = []

    for item in raw_items:
        t_str = item['scanned_at'].strftime("%Y-%m-%d %I:%M %p") if isinstance(item.get('scanned_at'), datetime) else str(item.get('scanned_at', ''))
        items.append({
            "id": str(item['_id']),
            "name": item.get("name", "Staff Member"),
            "service_number": item.get("service_number", ""),
            "directorate": item.get("directorate", "DOA"),
            "category": item.get("category", "civilian"),
            "rank": item.get("rank", "Staff"),
            "date_str": t_str,
            "is_temp_override": item.get("is_temp_override", False)
        })

    return jsonify({
        "status": "success",
        "filter_type": filter_type,
        "count": len(items),
        "items": items
    })


@kitchen_routes.route('/generate_24h_qr', methods=['GET'])
def generate_24h_qr_view():
    if 'user_email' not in session:
        flash("Please log in to access 24-hour pass generator.")
        return redirect(url_for('index'))

    user, role = get_current_user_and_role()
    if not user or role not in ['super_admin', 'catering_officer']:
        flash("Only Catering Officers and Super Admins can generate 24-hour meal QR codes.")
        return redirect(url_for('dashboard'))

    all_users = list(db.users.find({}, {
        "name": 1, "service_number": 1, "directorate": 1, "category": 1, "role": 1, "email": 1, "rank": 1, "appt": 1, "secure_code": 1, "secureCode": 1
    }).sort("name", 1))

    return render_template(
        'meal_qr_generator.html',
        user=sanitize_doc(user),
        system_users=sanitize_doc(all_users),
        active_page='meal_qr_generator'
    )


@kitchen_routes.route('/api/meal/check_user_limits/<target>', methods=['GET'])
def check_user_meal_limits(target):
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    user, role = get_current_user_and_role()
    if role not in ['super_admin', 'catering_officer']:
        return jsonify({"status": "error", "message": "Permission denied"}), 403

    target_user = db.users.find_one({
        "$or": [
            {"service_number": target.upper().strip()},
            {"email": target.lower().strip()},
            {"_id": ObjectId(target) if len(target) == 24 else None}
        ]
    })

    if not target_user:
        return jsonify({"status": "error", "message": "User not found."}), 404

    service_num = target_user.get("service_number", "")
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)

    scans_today = db.meal_scans.count_documents({
        "service_number": service_num,
        "scanned_at": {"$gte": today_start, "$lt": today_end}
    })

    scans_this_week = db.meal_scans.count_documents({
        "service_number": service_num,
        "scanned_at": {"$gte": week_start, "$lt": week_end}
    })

    exhausted_day = scans_today >= 1
    exhausted_week = scans_this_week >= 2
    is_exhausted = exhausted_day or exhausted_week

    user_name = target_user.get("name") or f"{target_user.get('firstname', '')} {target_user.get('surname', '')}".strip()

    return jsonify({
        "status": "success",
        "is_exhausted": is_exhausted,
        "exhausted_day": exhausted_day,
        "exhausted_week": exhausted_week,
        "scans_today": scans_today,
        "scans_this_week": scans_this_week,
        "user": {
            "name": user_name,
            "service_number": service_num,
            "directorate": target_user.get("directorate", "DOA"),
            "category": target_user.get("category", "civilian"),
            "email": target_user.get("email")
        }
    })


@kitchen_routes.route('/api/meal/create_24h_pass', methods=['POST'])
def create_24h_pass():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    user, role = get_current_user_and_role()
    if role not in ['super_admin', 'catering_officer']:
        return jsonify({"status": "error", "message": "Only Super Admin and Catering Officers can generate 24-hour passes."}), 403

    data = request.get_json() or {}
    target = (data.get("target_user") or "").strip()
    force_override = data.get("force_override") is True

    if not target:
        return jsonify({"status": "error", "message": "Target user is required."}), 400

    target_user = db.users.find_one({
        "$or": [
            {"service_number": target.upper()},
            {"email": target.lower()},
            {"_id": ObjectId(target) if len(target) == 24 else None}
        ]
    })

    if not target_user:
        return jsonify({"status": "error", "message": f"User '{target}' not found."}), 404

    service_num = target_user.get("service_number", "")
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)

    scans_today = db.meal_scans.count_documents({
        "service_number": service_num,
        "scanned_at": {"$gte": today_start, "$lt": today_end}
    })
    scans_this_week = db.meal_scans.count_documents({
        "service_number": service_num,
        "scanned_at": {"$gte": week_start, "$lt": week_end}
    })

    is_exhausted = (scans_today >= 1) or (scans_this_week >= 2)
    user_name = target_user.get("name") or f"{target_user.get('firstname', '')} {target_user.get('surname', '')}".strip()

    if is_exhausted and not force_override:
        return jsonify({
            "status": "warning_required",
            "message": f"User {user_name} ({service_num}) has ALREADY EXHAUSTED their meal limit (Scans Today: {scans_today}/1, Scans This Week: {scans_this_week}/2).",
            "scans_today": scans_today,
            "scans_this_week": scans_this_week,
            "user": {
                "name": user_name,
                "service_number": service_num
            }
        })

    # Generate 24-hour temporary pass token
    raw_id = uuid.uuid4().hex.upper()[:10]
    pass_code = f"PASS24-{service_num}-{raw_id}"
    expires_at = now + timedelta(hours=24)

    pass_doc = {
        "pass_code": pass_code,
        "user_id": str(target_user.get("_id", "")),
        "service_number": service_num,
        "user_name": user_name,
        "directorate": target_user.get("directorate", "DOA"),
        "category": target_user.get("category", "civilian"),
        "created_by": session.get('user_email'),
        "created_at": now,
        "expires_at": expires_at,
        "is_used": False
    }

    db.temp_meal_passes.insert_one(pass_doc)

    return jsonify({
        "status": "success",
        "message": f"24-Hour Temporary Pass created successfully for {user_name}!",
        "pass_code": pass_code,
        "expires_at_str": expires_at.strftime("%Y-%m-%d %I:%M %p"),
        "user": {
            "name": user_name,
            "service_number": service_num,
            "directorate": target_user.get("directorate", "DOA"),
            "category": target_user.get("category", "civilian"),
            "rank": target_user.get("rank") or target_user.get("appt") or "Staff"
        }
    })
