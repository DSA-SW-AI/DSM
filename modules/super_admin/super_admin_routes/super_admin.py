from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.security import check_password_hash
from initialize_all_balances import initialize_all_staff_balances, initialize_single_staff_balance
import io
import zipfile
import secrets
import string
from utils.excel_parser import parse_xlsx_to_dicts
from utils.email_helper import send_credentials_email

super_admin_routes = Blueprint('super_admin_routes', __name__)





# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]





@super_admin_routes.route('/super-admin-dashboard')
def super_admin_dashboard():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return redirect(url_for('index'))

    users_coll = current_app.users_collection
        
    user_data = users_coll.find_one({"email": session['user_email']})
    if not user_data:
        return redirect(url_for('index'))
        
    # Get all users
    all_users = list(users_coll.find())
    
    # Calculate stats
    directorate_counts = {}
    category_counts = {"civilian": 0, "military": 0, "it": 0, "nysc": 0}
    
    for u in all_users:
        if u.get('role') == 'super_admin':
            continue
        # Directorate breakdown
        d_name = str(u.get('directorate', 'DOA')).strip().upper()
        if d_name:
            directorate_counts[d_name] = directorate_counts.get(d_name, 0) + 1
            
        # Category breakdown
        raw_cat = str(u.get('category') or u.get('role') or 'civilian').lower().strip()
        if raw_cat in ['military', 'personnel']:
            category_counts["military"] += 1
        elif raw_cat == 'it':
            category_counts["it"] += 1
        elif raw_cat == 'nysc':
            category_counts["nysc"] += 1
        else:
            category_counts["civilian"] += 1
            
    from permissions import ROLE_PERMISSIONS
    user_allowed_features = ROLE_PERMISSIONS.get('super_admin', ROLE_PERMISSIONS['civilian'])
    
    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "Super Admin"),
        "role": "super_admin",
        "category": user_data.get("category", "civilian"),
        "appt": user_data.get("appt", "Super Administrator"),
        "directorate": str(user_data.get("directorate", "DOA")).upper(),
        "is_approval_role": session.get("is_approval_role", False)
    }
    
    from permissions import ROLE_PERMISSIONS
    all_available_roles = list(ROLE_PERMISSIONS.keys())

    # Load system settings
    system_coll = current_app.systems_collection
    system_settings = system_coll.find_one({"key": "portal_settings"})
    if not system_settings:
        system_settings = {
            "key": "portal_settings",
            "allow_registration": True,
            "maintenance_mode": False
        }
        system_coll.insert_one(system_settings)
        
    # Load recent support tickets

    recent_support_tickets = list(db.support_tickets.find().sort("created_at", -1))
    
    return render_template(
        'super_admin_dashboard.html',
        user=ui_user_profile,
        permissions=user_allowed_features,
        users=all_users,
        directorate_stats=directorate_counts,
        category_stats=category_counts,
        active_page='super_admin_dashboard',
        available_roles=all_available_roles,
        system_settings=system_settings,
        support_tickets=recent_support_tickets
    )


@super_admin_routes.route('/super-admin/settings', methods=['POST'])
def super_admin_save_settings():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    allow_registration = data.get('allow_registration', True)
    maintenance_mode = data.get('maintenance_mode', False)
    
    db.system_settings.update_one(
        {"key": "portal_settings"},
        {"$set": {
            "allow_registration": allow_registration,
            "maintenance_mode": maintenance_mode
        }},
        upsert=True
    )
    return jsonify({"status": "success", "message": "Portal settings saved successfully."})


@super_admin_routes.route('/super-admin/notification/send', methods=['POST'])
def super_admin_send_notification():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    target_type = data.get('target_type', 'broadcast') # broadcast, role, user
    message = data.get('message', '').strip()
    
    if not message:
        return jsonify({"status": "error", "message": "Message is required."}), 400
        
    notification_doc = {
        "type": "system_broadcast" if target_type == "broadcast" else "system_alert",
        "applicationId": ObjectId("000000000000000000000000"), # Dummy to satisfy schema validation
        "message": message,
        "status": "unread",
        "readBy": [],
        "createdAt": datetime.datetime.utcnow(),
        "isActive": True,
        "is_active": True
    }
    
    if target_type == "broadcast":
        notification_doc["target"] = {
            "type": "role",
            "role": "broadcast"
        }
    elif target_type == "role":
        target_role = data.get('role', '').strip()
        if not target_role:
            return jsonify({"status": "error", "message": "Role is required."}), 400
        notification_doc["target"] = {
            "type": "role",
            "role": target_role
        }
    elif target_type == "user":
        target_user = data.get('user', '').strip().lower()
        if not target_user:
            return jsonify({"status": "error", "message": "User identifier is required."}), 400
        found_user = db.users.find_one({"$or": [{"email": target_user}, {"service_number": target_user.upper()}]})
        if not found_user:
            return jsonify({"status": "error", "message": f"User '{target_user}' not found."}), 404
        
        notification_doc["target"] = {
            "type": "user",
            "userId": found_user.get("service_number") or found_user["email"]
        }
        
    db.notifications.insert_one(notification_doc)
    return jsonify({"status": "success", "message": "Notification dispatched successfully."})




@super_admin_routes.route('/super-admin/check-approver-conflict', methods=['POST'])
def super_admin_check_approver_conflict():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    directorate = data.get('directorate', '').strip().upper()
    is_so = data.get('is_so_approver') in (True, 'true', 'True')
    is_ad = data.get('is_ad_approver') in (True, 'true', 'True')
    is_dd = data.get('is_dd_approver') in (True, 'true', 'True')
    
    if not directorate:
        return jsonify({"status": "error", "message": "Directorate is required"}), 400
        
    conflicts = []
    
    if is_so:
        existing = db.users.find_one({
            "directorate": directorate,
            "is_so_approver": True,
            "email": {"$ne": email}
        })
        if existing:
            conflicts.append({
                "role": "SO Approver",
                "field": "is_so_approver",
                "name": existing.get("name", "Unknown"),
                "email": existing.get("email")
            })
            
    if is_ad:
        existing = db.users.find_one({
            "directorate": directorate,
            "is_ad_approver": True,
            "email": {"$ne": email}
        })
        if existing:
            conflicts.append({
                "role": "AD Approver",
                "field": "is_ad_approver",
                "name": existing.get("name", "Unknown"),
                "email": existing.get("email")
            })
            
    if is_dd:
        existing = db.users.find_one({
            "directorate": directorate,
            "is_dd_approver": True,
            "email": {"$ne": email}
        })
        if existing:
            conflicts.append({
                "role": "DD Approver",
                "field": "is_dd_approver",
                "name": existing.get("name", "Unknown"),
                "email": existing.get("email")
            })
            
    if conflicts:
        return jsonify({"status": "conflict", "conflicts": conflicts}), 200
        
    return jsonify({"status": "no_conflict"}), 200


@super_admin_routes.route('/super-admin/edit-user', methods=['POST'])
def super_admin_edit_user():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    target_email = data.get('target_email', '').strip().lower()
    if not target_email:
        return jsonify({"status": "error", "message": "Target email is required"}), 400
        
    user_record = db.users.find_one({"email": target_email})
    if not user_record:
        return jsonify({"status": "error", "message": "User not found"}), 404
        
    update_payload = {
        "name": data.get('name', '').strip(),
        "surname": data.get('surname', '').strip(),
        "firstname": data.get('firstname', '').strip(),
        "middlename": data.get('middlename', '').strip(),
        "alternate_email": data.get('alternate_email', '').strip(),
        "service_number": data.get('service_number', '').strip().upper(),
        "role": data.get('role', 'civilian').strip().lower(),
        "directorate": data.get('directorate', 'DOA').strip().upper(),
        "category": data.get('category', 'civilian').strip().lower(),
        "status": data.get('status', 'Approved').strip(),
        "is_onboarded": data.get('is_onboarded') in (True, 'true', 'True'),
        "is_active": data.get('is_active') in (True, 'true', 'True'),
        "is_approval_role": data.get('is_approval_role') in (True, 'true', 'True'),
        "is_dd_approver": data.get('is_dd_approver') in (True, 'true', 'True'),
        "is_ad_approver": data.get('is_ad_approver') in (True, 'true', 'True'),
        "is_so_approver": data.get('is_so_approver') in (True, 'true', 'True'),
        "is_final_approver": data.get('is_final_approver') in (True, 'true', 'True'),
        "appt": data.get('appt', '').strip(),
        "rankOrGrade": data.get('rankOrGrade', '').strip(),
    }

    # Handle force replacement for conflicts
    force_replace = data.get('force_replace') in (True, 'true', 'True')
    target_directorate = update_payload["directorate"]

    if update_payload.get("is_so_approver"):
        if force_replace:
            db.users.update_many(
                {"directorate": target_directorate, "email": {"$ne": target_email}},
                {"$set": {"is_so_approver": False}}
            )
        else:
            conflict = db.users.find_one({"directorate": target_directorate, "is_so_approver": True, "email": {"$ne": target_email}})
            if conflict:
                return jsonify({"status": "conflict_error", "message": f"Conflict detected: {conflict.get('name')} is already the SO Approver in {target_directorate} directorate."}), 409
                
    if update_payload.get("is_ad_approver"):
        if force_replace:
            db.users.update_many(
                {"directorate": target_directorate, "email": {"$ne": target_email}},
                {"$set": {"is_ad_approver": False}}
            )
        else:
            conflict = db.users.find_one({"directorate": target_directorate, "is_ad_approver": True, "email": {"$ne": target_email}})
            if conflict:
                return jsonify({"status": "conflict_error", "message": f"Conflict detected: {conflict.get('name')} is already the AD Approver in {target_directorate} directorate."}), 409

    if update_payload.get("is_dd_approver"):
        if force_replace:
            db.users.update_many(
                {"directorate": target_directorate, "email": {"$ne": target_email}},
                {"$set": {"is_dd_approver": False}}
            )
        else:
            conflict = db.users.find_one({"directorate": target_directorate, "is_dd_approver": True, "email": {"$ne": target_email}})
            if conflict:
                return jsonify({"status": "conflict_error", "message": f"Conflict detected: {conflict.get('name')} is already the DD Approver in {target_directorate} directorate."}), 409
    
    # Check email update
    new_email = data.get('email', '').strip().lower()
    if new_email and new_email != target_email:
        conflict = db.users.find_one({"email": new_email})
        if conflict:
            return jsonify({"status": "error", "message": "Email already in use by another user"}), 409
        update_payload["email"] = new_email
        
    new_password = data.get('password', '').strip()
    if new_password:
        update_payload["password_hash"] = generate_password_hash(new_password)
        
    db.users.update_one({"email": target_email}, {"$set": update_payload})
    return jsonify({"status": "success", "message": "User updated successfully"}), 200


@super_admin_routes.route('/super-admin/initialize-all-balances', methods=['POST'])
def super_admin_initialize_all_balances():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    delete_existing = data.get('delete_existing') in (True, 'true', 'True')
    year = data.get('year')
    try:
        year = int(year) if year else datetime.datetime.now().year
    except ValueError:
        year = datetime.datetime.now().year
        
    try:
        success = initialize_all_staff_balances(delete_existing=delete_existing, year=year)
        if success:
            return jsonify({"status": "success", "message": f"Successfully initialized balances for all staff for year {year}."}), 200
        else:
            return jsonify({"status": "error", "message": "Balance initialization failed. Please check server logs."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error running balance initialization: {str(e)}"}), 500


@super_admin_routes.route('/super-admin/initialize-single-balance', methods=['POST'])
def super_admin_initialize_single_balance():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    target_identifier = data.get('identifier', '').strip()
    delete_existing = data.get('delete_existing') in (True, 'true', 'True')
    year = data.get('year')
    try:
        year = int(year) if year else datetime.datetime.now().year
    except ValueError:
        year = datetime.datetime.now().year
        
    if not target_identifier:
        return jsonify({"status": "error", "message": "User identifier is required"}), 400
        
    try:
        success, message = initialize_single_staff_balance(
            service_number_or_email=target_identifier,
            delete_existing=delete_existing,
            year=year
        )
        if success:
            return jsonify({"status": "success", "message": f"Balance initialized: {message}"}), 200
        else:
            return jsonify({"status": "error", "message": f"Initialization failed: {message}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500


# @app.route('/switch-view')
# def switch_view():
#     if 'user_email' not in session:
#         return redirect(url_for('index'))
        
#     user_data = db.users.find_one({"email": session['user_email']})
#     if not user_data:
#         return redirect(url_for('index'))
        
#     role = user_data.get('role', 'civilian')
#     db_is_approval = user_data.get("is_approval_role") in (True, "true", "True") or role in ['cdsa', 'dcdsa', 'director', 'dd', 'ad', 'so', 'registry', 'super_admin']
    
#     if db_is_approval:
#         current_mode = session.get('is_approval_role', False)
#         session['is_approval_role'] = not current_mode
        
#         # Toggle actual operational role back and forth
#         session['role'] = role if session['is_approval_role'] else 'civilian'
#         session['user_role'] = role if session['is_approval_role'] else 'civilian'
        
#         session['is_so_approver'] = user_data.get('is_so_approver', False) if session['is_approval_role'] else False
#         session['is_ad_approver'] = user_data.get('is_ad_approver', False) if session['is_approval_role'] else False
#         session['is_dd_approver'] = user_data.get('is_dd_approver', False) if session['is_approval_role'] else False
#         session['is_final_approver'] = user_data.get('is_final_approver') in (True, "true", "True") if session['is_approval_role'] else False
        
#     return redirect(url_for('dashboard'))


# ================= BULK USER IMPORT & EXCEL PARSER SYSTEM =================
@super_admin_routes.route('/super-admin/bulk-upload/template', methods=['GET'])
def super_admin_bulk_upload_template():
    if 'user_email' not in session or session.get('user_role') != 'super_admin':
        return redirect(url_for('index'))
    
    # Generate the XLSX template bytes dynamically containing standard headers
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="9" uniqueCount="9"><si><t>Firstname</t></si><si><t>Surname</t></si><si><t>Middlename</t></si><si><t>Category</t></si><si><t>Role</t></si><si><t>Directorate</t></si><si><t>Service Number</t></si><si><t>Personal Email</t></si><si><t>Official Email</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>5</v></c><c r="G1" t="s"><v>6</v></c><c r="H1" t="s"><v>7</v></c><c r="I1" t="s"><v>8</v></c></row></sheetData></worksheet>')
    out.seek(0)
    
    from flask import send_file
    return send_file(
        out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="bulk_user_import_template.xlsx"
    )

@super_admin_routes.route('/super-admin/bulk-upload', methods=['POST'])
def super_admin_bulk_upload():
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
        # Load file bytes into BytesIO stream to bypass SpooledTemporaryFile seekable compatibility issues in older Python versions
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        records = parse_xlsx_to_dicts(file_stream)
    except Exception as e:
        flash(f"Failed to parse Excel file: {str(e)}", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    if not records:
        flash("Excel spreadsheet is empty or missing headers.", "error")
        return redirect(url_for('super_admin_routes.super_admin_dashboard'))

    users_coll = current_app.users_collection
    success_count = 0
    skipped_count = 0
    errors = []

    for idx, row in enumerate(records, start=2):
        firstname = row.get("firstname", "").strip()
        surname = row.get("surname", "").strip()
        middlename = row.get("middlename", "").strip()
        category = row.get("category", "").strip().lower()
        role = row.get("role", "").strip().lower()
        directorate = row.get("directorate", "").strip().upper()
        service_number = row.get("service_number", "").strip().upper()
        alternate_email = row.get("alternate_email", "").strip()
        provided_official_email = row.get("official_email", "").strip().lower()

        # Required fields validation
        if not all([firstname, surname, category, directorate, service_number, alternate_email]):
            errors.append(f"Row {idx}: Missing required fields (Firstname, Surname, Category, Directorate, Service Number, or Personal Email).")
            continue

        # Handle official email
        if provided_official_email:
            official_email = provided_official_email
        else:
            official_email = f"{firstname.lower()}.{surname.lower()}@dsa.mil.ng"

        # Check existing user checks
        existing_by_sn = users_coll.find_one({"service_number": service_number})
        existing_by_email = users_coll.find_one({"email": official_email})

        if existing_by_sn or existing_by_email:
            skipped_count += 1
            # Bypass/skip duplicates gracefully without dispatching emails
            continue

        # Generate a plain temporary password (alphanumeric, 8 chars)
        chars = string.ascii_letters + string.digits
        plain_password = "".join(secrets.choice(chars) for _ in range(8))
        password_hash = generate_password_hash(plain_password, method='scrypt')

        # Derive role based on category
        if category == "civilian":
            user_role = "civilian"
        elif category == "military":
            user_role = role if role in ["officer", "personnel"] else "personnel"
        elif category == "nysc":
            user_role = "nysc"
        elif category == "it":
            user_role = "it_attachment"
        else:
            user_role = "personnel"

        new_user = {
            "email": official_email,
            "alternate_email": alternate_email,
            "name": f"{firstname} {surname}",
            "surname": surname,
            "firstname": firstname,
            "middlename": middlename,
            "service_number": service_number,
            "role": user_role,
            "directorate": directorate,
            "category": category,
            "password_hash": password_hash,
            "is_active": True,
            "is_onboarded": False,
            "is_approval_role": "false",
            "created_at": datetime.now()
        }

        try:
            users_coll.insert_one(new_user)
            
            # Send plain password and email details via credentials email
            # Pre-save pending credentials state so password is saved if offline
            pending_data = {
                "target_alt_email": alternate_email,
                "official_login_email": official_email,
                "plain_password": plain_password,
                "service_number": service_number
            }
            users_coll.update_one(
                {"email": official_email},
                {"$set": {"email_sent": False, "pending_credentials": pending_data}}
            )

            # Dispatch credentials email in background thread without blocking bulk upload loop
            def _async_bulk_email(target_alt, official_login, pwd, sn):
                try:
                    dispatched = send_credentials_email(
                        target_alt_email=target_alt,
                        official_login_email=official_login,
                        plain_password=pwd,
                        service_number=sn
                    )
                    if dispatched:
                        users_coll.update_one(
                            {"email": official_login},
                            {"$set": {"email_sent": True, "email_sent_at": datetime.now()}}
                        )
                except Exception as ex:
                    print(f"Bulk import async email error for {official_login}: {ex}")

            import threading
            threading.Thread(
                target=_async_bulk_email,
                args=(alternate_email, official_email, plain_password, service_number),
                daemon=True
            ).start()
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
