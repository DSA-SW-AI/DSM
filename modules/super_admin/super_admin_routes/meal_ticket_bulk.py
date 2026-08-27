from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime, timedelta
from pymongo import MongoClient
import io
import zipfile
from utils.excel_parser import parse_xlsx_to_dicts

meal_ticket_upload_routes = Blueprint('meal_ticket_upload_routes', __name__)

# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]


# ================= TEMPLATE DOWNLOAD =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/bulk-upload/template', methods=['GET'])
def meal_ticket_bulk_upload_template():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return redirect(url_for('index'))
    
    # Generate the XLSX template bytes dynamically containing exactly the 6 required headers
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="6" uniqueCount="6"><si><t>Category</t></si><si><t>Service Number</t></si><si><t>Directorate</t></si><si><t>Surname</t></si><si><t>Firstname</t></si><si><t>Middlename</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>5</v></c></row></sheetData></worksheet>')
    out.seek(0)
    
    from flask import send_file
    return send_file(
        out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="meal_ticket_user_import_template.xlsx"
    )


# ================= BULK USER IMPORT =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/bulk-upload', methods=['POST'])
def meal_ticket_bulk_upload():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return redirect(url_for('index'))
        
    if 'file' not in request.files:
        flash("No file part provided in the request.", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))
        
    file = request.files['file']
    if file.filename == '':
        flash("No file selected for upload.", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    if not file.filename.endswith('.xlsx'):
        flash("Invalid file format. Please upload a standard Excel .xlsx file.", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    try:
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        records = parse_xlsx_to_dicts(file_stream)
    except Exception as e:
        flash(f"Failed to parse Excel file: {str(e)}", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    if not records:
        flash("Excel spreadsheet is empty or missing headers.", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    meal_ticket_coll = db["meal_tickets"]
    success_count = 0
    skipped_count = 0
    errors = []

    for idx, row in enumerate(records, start=2):
        firstname = row.get("firstname", "").strip()
        surname = row.get("surname", "").strip()
        middlename = row.get("middlename", "").strip()
        category = row.get("category", "").strip().lower()
        directorate = row.get("directorate", "").strip().upper()
        service_number = row.get("service_number", "").strip().upper()

        # Required fields validation
        if not all([firstname, surname, category, directorate, service_number]):
            errors.append(f"Row {idx}: Missing required fields (Firstname, Surname, Category, Directorate, Service Number).")
            continue

        # Check existing user checks
        existing_by_sn = meal_ticket_coll.find_one({"service_number": service_number})
        if existing_by_sn:
            skipped_count += 1
            continue

        # Derive role based on category
        if category == "civilian":
            user_role = "civilian"
        elif category == "military":
            user_role = "military"
        elif category == "nysc":
            user_role = "nysc"
        elif category == "it":
            user_role = "it_attachment"
        else:
            user_role = "personnel"

        new_user = {
            "name": f"{firstname} {surname}",
            "surname": surname,
            "firstname": firstname,
            "middlename": middlename,
            "service_number": service_number,
            "role": user_role,
            "directorate": directorate,
            "category": category,
            "is_active": True,
            "created_at": datetime.now()
        }

        try:
            meal_ticket_coll.insert_one(new_user)
            success_count += 1
        except Exception as e:
            errors.append(f"Row {idx}: Error saving to database: {str(e)}")

    flash_msg = f"Bulk import complete. Successfully added {success_count} user(s). {skipped_count} existing profile(s) bypassed."
    if errors:
        flash_msg += " Warnings: " + " | ".join(errors[:5])
        if len(errors) > 5:
            flash_msg += " (and more...)"
            
    flash(flash_msg, "success" if success_count > 0 else "info")
    return redirect(url_for('super_admin_routes.super_admin_dashboard'))


# ================= ADD SINGLE USER =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/add-single', methods=['POST'])
def meal_ticket_add_single():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form or {}

    firstname = data.get("firstname", "").strip()
    surname = data.get("surname", "").strip()
    middlename = data.get("middlename", "").strip()
    category = data.get("category", "").strip().lower()
    directorate = data.get("directorate", "").strip().upper()
    service_number = data.get("service_number", "").strip().upper()

    if not all([firstname, surname, category, directorate, service_number]):
        return jsonify({"status": "error", "message": "Missing required fields (Firstname, Surname, Category, Directorate, Service Number)."}), 400

    meal_ticket_coll = db["meal_tickets"]
    
    # Check duplicate
    existing = meal_ticket_coll.find_one({"service_number": service_number})
    if existing:
        return jsonify({"status": "error", "message": f"User with Service Number {service_number} already exists."}), 400

    # Derive role based on category
    if category == "civilian":
        user_role = "civilian"
    elif category == "military":
        user_role = "military"
    elif category == "nysc":
        user_role = "nysc"
    elif category == "it":
        user_role = "it_attachment"
    else:
        user_role = "personnel"

    new_user = {
        "name": f"{firstname} {surname}",
        "surname": surname,
        "firstname": firstname,
        "middlename": middlename,
        "service_number": service_number,
        "role": user_role,
        "directorate": directorate,
        "category": category,
        "is_active": True,
        "created_at": datetime.now()
    }

    try:
        meal_ticket_coll.insert_one(new_user)
        return jsonify({"status": "success", "message": "Meal ticket user added successfully."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error saving to database: {str(e)}"}), 500


# ================= DELETE USER =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/delete/<service_number>', methods=['POST'])
def meal_ticket_delete(service_number):
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    meal_ticket_coll = db["meal_tickets"]
    try:
        result = meal_ticket_coll.delete_one({"service_number": service_number.upper().strip()})
        if result.deleted_count > 0:
            return jsonify({"status": "success", "message": "Meal ticket user deleted successfully."}), 200
        else:
            return jsonify({"status": "error", "message": "Meal ticket user not found."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error deleting user: {str(e)}"}), 500


# ================= RECORD DAILY COLLECTION =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/collect', methods=['POST'])
def meal_ticket_collect():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    data = request.get_json() or {}
    service_number = data.get("service_number", "").strip().upper()

    if not service_number:
        return jsonify({"status": "error", "message": "Service Number is required."}), 400

    meal_ticket_coll = db["meal_tickets"]
    claims_coll = db["meal_ticket_claims"]

    # Verify user exists in meal tickets registry
    user = meal_ticket_coll.find_one({"service_number": service_number})
    if not user:
        return jsonify({"status": "error", "message": f"User with Service Number {service_number} is not registered for meal ticketing."}), 404

    # Check if user already collected today
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)

    existing_claim = claims_coll.find_one({
        "service_number": service_number,
        "collected_at": {"$gte": today_start, "$lt": today_end}
    })

    if existing_claim:
        return jsonify({"status": "error", "message": "User has already collected a meal ticket today."}), 400

    # Log collection
    claim = {
        "service_number": service_number,
        "name": user.get("name", ""),
        "directorate": user.get("directorate", "DOA").upper(),
        "category": user.get("category", "civilian").lower(),
        "collected_at": now
    }

    try:
        claims_coll.insert_one(claim)
        return jsonify({"status": "success", "message": "Meal ticket collection recorded successfully."}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error saving collection to database: {str(e)}"}), 500


# ================= ANALYTICS & AUTO-SEEDING =================
@meal_ticket_upload_routes.route('/super-admin/meal-tickets/analytics', methods=['GET'])
def meal_ticket_analytics():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    meal_ticket_coll = db["meal_tickets"]
    claims_coll = db["meal_ticket_claims"]

    # Auto-seed mock data if empty and users exist
    if claims_coll.count_documents({}) == 0:
        users = list(meal_ticket_coll.find())
        if users:
            import random
            seed_start_date = datetime.now() - timedelta(days=30)
            mock_claims = []
            for d in range(31):
                current_day = seed_start_date + timedelta(days=d)
                # Skip weekends for realistic work meal distribution
                if current_day.weekday() >= 5:
                    continue
                # Pick a random subset of users to have collected meal tickets on this day
                num_claims = random.randint(min(3, len(users)), min(15, len(users)))
                selected_users = random.sample(users, num_claims)
                for u in selected_users:
                    # Random meal time hour (e.g. 11am to 2pm)
                    hour = random.randint(11, 14)
                    minute = random.randint(0, 59)
                    collected_time = datetime(current_day.year, current_day.month, current_day.day, hour, minute)
                    mock_claims.append({
                        "service_number": u.get("service_number"),
                        "name": u.get("name"),
                        "directorate": u.get("directorate", "DOA").upper(),
                        "category": u.get("category", "civilian").lower(),
                        "collected_at": collected_time
                    })
            if mock_claims:
                claims_coll.insert_many(mock_claims)

    # Calculate statistics
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=today_start.weekday())  # Monday of current week
    month_start = datetime(now.year, now.month, 1)

    count_today = claims_coll.count_documents({"collected_at": {"$gte": today_start}})
    count_week = claims_coll.count_documents({"collected_at": {"$gte": week_start}})
    count_month = claims_coll.count_documents({"collected_at": {"$gte": month_start}})
    count_total = claims_coll.count_documents({})

    # Directorate breakdown
    pipeline_dir = [
        {"$group": {"_id": "$directorate", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    dir_stats = {row["_id"]: row["count"] for row in claims_coll.aggregate(pipeline_dir) if row["_id"]}

    # Category breakdown
    pipeline_cat = [
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    cat_stats = {row["_id"]: row["count"] for row in claims_coll.aggregate(pipeline_cat) if row["_id"]}

    # Daily breakdown (last 7 days)
    daily_stats = []
    for i in range(7):
        day_date = today_start - timedelta(days=6-i)
        next_day = day_date + timedelta(days=1)
        cnt = claims_coll.count_documents({"collected_at": {"$gte": day_date, "$lt": next_day}})
        daily_stats.append({
            "date": day_date.strftime("%Y-%m-%d"),
            "day_name": day_date.strftime("%a"),
            "count": cnt
        })

    # Weekly breakdown (last 4 weeks)
    weekly_stats = []
    for i in range(4):
        w_start = today_start - timedelta(days=today_start.weekday() + (3-i)*7)
        w_end = w_start + timedelta(days=7)
        cnt = claims_coll.count_documents({"collected_at": {"$gte": w_start, "$lt": w_end}})
        weekly_stats.append({
            "week_label": f"Week of {w_start.strftime('%b %d')}",
            "count": cnt
        })

    # Monthly breakdown (last 6 months)
    monthly_stats = []
    for i in range(6):
        month_idx = now.month - 1 - (5-i)
        year_offset = month_idx // 12
        m = (month_idx % 12) + 1
        y = now.year + year_offset
        
        m_start = datetime(y, m, 1)
        if m == 12:
            m_end = datetime(y+1, 1, 1)
        else:
            m_end = datetime(y, m+1, 1)
            
        cnt = claims_coll.count_documents({"collected_at": {"$gte": m_start, "$lt": m_end}})
        monthly_stats.append({
            "month_label": m_start.strftime("%B %Y"),
            "count": cnt
        })

    return jsonify({
        "status": "success",
        "summary": {
            "today": count_today,
            "week": count_week,
            "month": count_month,
            "total": count_total
        },
        "directorates": dir_stats,
        "categories": cat_stats,
        "daily": daily_stats,
        "weekly": weekly_stats,
        "monthly": monthly_stats
    }), 200
