from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import os
from gridfs import GridFS
from utils.email_helper import send_credentials_email
import datetime


# Blueprint for onboarding
onboarding_routes = Blueprint('onboarding_routes', __name__)


# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]

# Create GridFS instance
fs = GridFS(db, collection="attachments")


@onboarding_routes.route('/personnel-view')
def personnel_view():
    if 'user_email' not in session:
        return redirect(url_for('index'))

    if not session.get("is_approval_role"):
        print("You are not authorized to view personnel list.", "error")
        return redirect(url_for('dashboard'))

    # Load active user profile metadata details directly out of MongoDB
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data:
        session.clear()
        return redirect(url_for('index'))

    user_role = str(user_data.get('role', 'civilian')).strip().lower()
    user_role_clean = user_role.replace('_', '').replace(' ', '')
    user_dir_clean = str(user_data.get('directorate', 'DOA')).strip().upper()

    # Scope rule: Only CDSA, DCDSA, Director of DOA, and Registry of DOA see cross-directorate personnel
    is_global_scope = (user_role_clean in ['cdsa', 'dcdsa']) or (user_role_clean in ['director', 'registry'] and user_dir_clean == 'DOA')

    if is_global_scope:
        personnel_list = list(db.users.find({"role": {"$nin": ["cdsa", "dcdsa"]}}).sort("_id", -1))
    else:
        personnel_list = list(db.users.find({
            "directorate": {"$regex": f"^{user_dir_clean}$", "$options": "i"},
            "role": {"$nin": ["cdsa", "dcdsa"]}
        }).sort("_id", -1))

    # Construct the exact data layout payload structure expected by personnel.html
    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "Officer"),
        "title": str(user_data.get("title", "Director")).upper(),
        "directorate": user_dir_clean.upper(), 
        "role": user_role  # <-- CRUCIAL: Must match lowercase "director" token
    }

    from permissions import ROLE_PERMISSIONS
    user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])

    # Explicitly pass 'user' and 'permissions' so the Jinja context binds smoothly
    return render_template(
        'personnel.html',
        user=ui_user_profile,
        permissions=user_allowed_features,
        personnel=personnel_list,
        role_permissions=ROLE_PERMISSIONS
    )


@onboarding_routes.route('/add-staff', methods=['POST'])
def add_staff():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized terminal session"}), 401
        
    data = request.get_json() or {}
    category = data.get('category')
    cat_role = data.get('cat_role')
    directorate = data.get('directorate')
    service_number = data.get('service_number')
    alt_email = data.get('alternate_email')
    plain_password = data.get('password')
    surname = (data.get('surname') or '').strip()
    firstname = (data.get('firstname') or '').strip()
    middlename = (data.get('middlename') or '').strip()

    # Formulate official email as firstname.surname@dsa.mil.ng
    fn_clean = "".join(firstname.lower().split())
    sn_clean = "".join(surname.lower().split())
    cat_lower = (category or '').lower().strip()
    
    if cat_lower in ['it', 'nysc']:
        official_login_email = f"{fn_clean}.{sn_clean}_{cat_lower}@dsa.mil.ng"
    else:
        official_login_email = f"{fn_clean}.{sn_clean}@dsa.mil.ng"

    full_name = f"{firstname} {middlename} {surname}".strip() if middlename else f"{firstname} {surname}".strip()

    # Middle name is optional and no longer required
    if not all([category, directorate, service_number, alt_email, plain_password, firstname, surname, official_login_email]):
        return jsonify({"status": "error", "message": "All required form fields must be populated (First Name, Surname, Directorate, Service Number, Alternate Email)"}), 400

    existing_user = db.users.find_one({"email": official_login_email})
    if existing_user:
        return jsonify({"status": "error", "message": "User profile or service number already registered"}), 409

    secure_flask_hash = generate_password_hash(plain_password)

    category_lower = category.lower().strip()
    if category_lower == "civilian":
        user_role = "civilian"
    elif category_lower == "military":
        user_role = cat_role if cat_role in ["officer", "personnel"] else "personnel"
    else:
        user_role = "personnel"

    new_user_document = {
        "email": official_login_email,
        "alternate_email": alt_email,
        "name": full_name.strip(),
        "surname": surname.strip(),
        "firstname": firstname.strip(),
        "middlename": middlename.strip(),
        "service_number": service_number.upper(),
        "role": user_role,
        "directorate": directorate.upper(),
        "category": category_lower,
        "password_hash": secure_flask_hash,
        "is_active": True,
        "is_onboarded": False,
        "is_approval_role": "false",
        "created_at": datetime.datetime.now()
    }
    
    # 1. Insert new account into MongoDB database
    db.users.insert_one(new_user_document)

    # 2. RUN DISPATCH SYSTEM: Fires transmission line using the plain password before hashing
    email_dispatched = send_credentials_email(
        target_alt_email=alt_email,
        official_login_email=official_login_email,
        plain_password=plain_password,
        service_number=service_number
    )

    if email_dispatched:
        db.users.update_one(
            {"email": official_login_email},
            {"$set": {"email_sent": True, "email_sent_at": datetime.datetime.now()}}
        )
        success_msg = f"Account compiled successfully! Credentials routed securely to {alt_email} via GovMail Gateway."
        return jsonify({"status": "success", "email_sent": True, "message": success_msg}), 201
    else:
        pending_data = {
            "target_alt_email": alt_email,
            "official_login_email": official_login_email,
            "plain_password": plain_password,
            "service_number": service_number
        }
        db.users.update_one(
            {"email": official_login_email},
            {"$set": {"email_sent": False, "pending_credentials": pending_data}}
        )
        success_msg = "Account created offline in database. Internet connection unavailable — credentials saved to 'Pending Mail Queue' tab for batch dispatch."
        return jsonify({"status": "success", "email_sent": False, "message": success_msg}), 201


@onboarding_routes.route('/pending-emails-view')
def pending_emails_view():
    if 'user_email' not in session:
        return redirect(url_for('index'))

    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data or user_data.get('role') != 'registry':
        return redirect(url_for('dashboard'))

    user_role = str(user_data.get('role', 'registry')).strip().lower()
    user_dir_clean = str(user_data.get('directorate', 'DOA')).strip().upper()

    pending_list = list(db.users.find({
        "$or": [
            {"email_sent": False},
            {"pending_credentials": {"$exists": True}}
        ]
    }).sort("created_at", -1))

    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "Officer"),
        "title": str(user_data.get("title", "Registry Clerk")).upper(),
        "directorate": user_dir_clean,
        "role": user_role
    }

    from permissions import ROLE_PERMISSIONS
    user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])

    return render_template(
        'pending_emails.html',
        user=ui_user_profile,
        permissions=user_allowed_features,
        pending_users=pending_list,
        unpushed_count=len(pending_list),
        active_page='pending_emails'
    )


@onboarding_routes.route('/resend-pending-emails', methods=['POST'])
def resend_pending_emails():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized session"}), 401

    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data or user_data.get('role') != 'registry':
        return jsonify({"status": "error", "message": "Only DOA Registry personnel can trigger mail dispatch"}), 403

    req_json = request.get_json() or {}
    target_email = req_json.get('email')

    if target_email:
        query = {"email": target_email, "$or": [{"email_sent": False}, {"pending_credentials": {"$exists": True}}]}
    else:
        query = {"$or": [{"email_sent": False}, {"pending_credentials": {"$exists": True}}]}

    pending_users = list(db.users.find(query))
    if not pending_users:
        return jsonify({"status": "info", "message": "No pending credential emails found in queue."}), 200

    sent_count = 0
    failed_count = 0

    for u in pending_users:
        cred = u.get('pending_credentials', {})
        target_alt = cred.get('target_alt_email') or u.get('alternate_email')
        official_email = cred.get('official_login_email') or u.get('email')
        plain_pass = cred.get('plain_password') or "password123"
        sn = cred.get('service_number') or u.get('service_number', '')

        if not target_alt or not official_email:
            continue

        dispatched = send_credentials_email(
            target_alt_email=target_alt,
            official_login_email=official_email,
            plain_password=plain_pass,
            service_number=sn
        )

        if dispatched:
            sent_count += 1
            db.users.update_one(
                {"_id": u["_id"]},
                {
                    "$set": {"email_sent": True, "email_sent_at": datetime.datetime.now()},
                    "$unset": {"pending_credentials": ""}
                }
            )
        else:
            failed_count += 1

    if sent_count > 0:
        msg = f"Successfully dispatched {sent_count} user account credentials via GovMail Gateway!"
        if failed_count > 0:
            msg += f" ({failed_count} remained offline/failed)."
        return jsonify({"status": "success", "sent_count": sent_count, "failed_count": failed_count, "message": msg}), 200
    else:
        return jsonify({"status": "error", "sent_count": 0, "failed_count": failed_count, "message": "Internet/GovMail gateway unavailable. Could not send emails. Verify server network connection."}), 400


@onboarding_routes.route('/onboarding')
def onboarding_portal():
    if 'user_email' not in session:
        return redirect(url_for('index'))

    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data:
        session.clear()
        return redirect(url_for('index'))

    status = user_data.get('status', 'In Progress')

    # FIXED BOUNCE CHECKS: Never redirect back to dashboard unless status is explicitly Approved!
    if status in ['Awaiting Approval', 'Rejected']:
        return redirect(url_for('onboarding_routes.pending_approval_notice'))
    elif status == 'Approved':
        return redirect(url_for('dashboard'))

    # Render data cleanly for users whose onboarding status is genuinely "In Progress"
    email_string = session['user_email']
    username_part = email_string.split('@')[0]
    generated_file_no = username_part.replace('_', '/').upper()

    # Split full name back into surname and firstname
    name_str = user_data.get("name", "").strip()
    name_parts = name_str.split()
    if len(name_parts) >= 2:
        surname = name_parts[-1]
        firstname = " ".join(name_parts[:-1])
    elif len(name_parts) == 1:
        surname = name_parts[0]
        firstname = ""
    else:
        surname = ""
        firstname = ""

    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "NEW USER"),
        "role": user_data.get("role", "civilian"),
        "category": user_data.get("category", "civilian"), # Default for missing legacy accounts
        "directorate": str(user_data.get("directorate", "DOA")).upper(),
        "file_no": generated_file_no,
        "training_request_active": user_data.get("training_request_active", False),
        "appt": user_data.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "middlename": user_data.get("middlename")
    }


    import datetime
    current_time = datetime.datetime.now().strftime("%A, %d %B %Y")

    return render_template(
        'onboarding.html', 
        user=ui_user_profile, 
        current_time=current_time,
        surname=surname,
        firstname=firstname
    )


# Optional: Create a local directory folder on your server machine to store file assets
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# app.py - Replace your submission endpoint with this updated version

@onboarding_routes.route('/submit-onboarding-step', methods=['POST'])
def submit_onboarding_step():
    # 1. Enforce active terminal session check
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized session context"}), 401
        
    # Query current user's profile metadata data tracks out of MongoDB
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data:
        return jsonify({"status": "error", "message": "User document not found"}), 404
        
    step_data = {}
    
    # 2. HYBRID ENGINE: Check if incoming request contains Form Files or standard JSON data
    if request.content_type and 'multipart/form-data' in request.content_type:
        # --- HANDLE STEP 1 (File Uploads & Text Fields) ---
        step = int(request.form.get('step', 1))
        
        raw_rank = request.form.get('rankOrGrade', '')
        if raw_rank and "grade level" in raw_rank.lower():
            import re
            match = re.search(r'\d+', raw_rank)
            normalized_rank = f"Grade Level {match.group(0)}" if match else raw_rank.title().strip()
        else:
            normalized_rank = raw_rank.title().strip() if raw_rank else ''

        # Extract text field datasets cleanly
        step_data = {
            "staffTitle": request.form.get('staffTitle'),
            "appt": request.form.get('appt') or request.form.get('appointment'),
            "phoneNo": request.form.get('phoneNo') or request.form.get('phone'),
            "rankOrGrade": normalized_rank
        }

        # Extract and save physical file attachments safely into MongoDB GridFS
        for key in request.files:
            file = request.files[key]
            if file and file.filename != '':
                file_id = fs.put(
                    file,
                    filename=secure_filename(file.filename),
                    content_type=file.content_type or 'application/octet-stream',
                    metadata={
                        "uploaded_by": session['user_email'],
                        "field_key": key,
                        "upload_date": datetime.datetime.utcnow()
                    }
                )
                
                # Store the GridFS attachment url path inside onboarding_data
                step_data[key] = f"/attachment/{str(file_id)}"

    else:
        # --- HANDLE STEPS 2, 3, & 4 (Standard JSON payloads) ---
        json_payload = request.get_json()
        if not json_payload:
            return jsonify({"status": "error", "message": "Missing request footprint dataset"}), 400
            
        step = int(json_payload.get('step'))
        step_data = json_payload.get('formData', {})

    # 3. Guard validation checking rule
    if not step:
        return jsonify({"status": "error", "message": "Invalid step tracker parameter configuration"}), 400

    if step == 5 and "gender" in step_data:
        raw_gender = step_data.get("gender") or ""
        step_data["gender"] = raw_gender.title().strip()

    # 4. DATABASE TRANSACTIONS: Map values inside your 'DSM' users collection document node
    update_node_query = {f"onboarding_data.step_{step}": step_data}
    
    # Overwrite the user's primary name field to match their newly typed Surname/Firstname string 
    if step == 1:
        compiled_name = f"{step_data.get('surname', '')} {step_data.get('firstName', '')}".strip().upper()
        if compiled_name:
            update_node_query["name"] = compiled_name

    # FIXED DIRECT REGISTRY ASSIGNMENT ENFORCEMENT:
    # If the user completes Step 5... Or if they are IT/NYSC and they just completed Step 4:
    user_category = user_data.get("category", "civilian")
    is_special_role = user_category in ['it', 'nysc']

    if step == 5 or (step == 4 and is_special_role):
        update_node_query["status"] = "Awaiting Approval"
        update_node_query["is_onboarded"] = False 
        
        # Duplicate appt and rankOrGrade to the root fields
        onboarding_data = user_data.get("onboarding_data", {})
        step_1 = onboarding_data.get("step_1", {})
        step_5 = onboarding_data.get("step_5", {})
        
        appt_val = step_data.get("appt") or step_5.get("appt") or step_1.get("appt") or user_data.get("appt")
        rank_val = step_data.get("rankOrGrade") or step_5.get("rankOrGrade") or step_1.get("rankOrGrade") or user_data.get("rankOrGrade")
        
        if appt_val:
            update_node_query["appt"] = appt_val
        if rank_val:
            update_node_query["rankOrGrade"] = rank_val

    db.users.update_one(
        {"email": session['user_email']},
        {"$set": update_node_query}
    )

    # ================= AUTOMATED TIMELINE EVENT FOOTPRINT LOGGER =================
    step_logs_matrix = {
        1: "Completed Step 1: Employee Information Form & File Uploads",
        2: "Completed Step 2: Additional Personal Background Information Details",
        3: "Completed Step 3: Generated and Certified Digital ID Card Preview Layout",
        4: "Completed Step 4: Submitted Salary Account Emolument Details Roster Form",
        5: "Completed Step 5: Submitted DSA Civilian Staff Registration Form"
    }
    action_log_message = step_logs_matrix.get(step, f"Updated Onboarding Phase Milestone Step {step}")

    timeline_feed_item = {
        "email": session['user_email'],
        "name": user_data.get("name", "New Recruit").upper(),
        # Store directorate in strict lowercase to keep filters case-insensitive
        "directorate": str(user_data.get("directorate", "doa")).strip().lower(),
        "action": action_log_message,
        "timestamp": datetime.datetime.now().strftime("%A, %d %B %Y - %H:%M")
    }
    
    # Push timeline log downstream directly to your registry feed collection table
    db.registry_feed.insert_one(timeline_feed_item)
    # ==============================================================================

    return jsonify({"status": "success", "message": f"Step {step} information successfully logged."}), 200


@onboarding_routes.route('/pending-approval')
def pending_approval_notice():
    if 'user_email' not in session:
        return redirect(url_for('index'))
        
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data or user_data.get('status') not in ['Awaiting Approval', 'Rejected']:
        return redirect(url_for('index'))
        
    return render_template('pending_notice.html', user=user_data)


@onboarding_routes.route('/get-personnel-profile')
def get_personnel_profile():
    if 'user_email' not in session: 
        return "Unauthorized", 401
        
    target_email = request.args.get('email')
    user_record = db.users.find_one({"email": target_email}, {"password_hash": 0, "_id": 0})
    
    if not user_record: 
        return "Not Found", 404
        
    return jsonify(user_record), 200


@onboarding_routes.route('/process-personnel-approval', methods=['POST'])
def process_personnel_approval():
    user_role = str(session.get('user_role', '')).lower().strip()
    if 'user_email' not in session or user_role not in ['cdsa', 'dcdsa', 'director', 'registry']:
        return jsonify({"status": "error", "message": "Access Denied."}), 403
        
    data = request.get_json()
    target_email = data.get('email')
    action_decision = data.get('status')  # Expecting: 'Approved' or 'Rejected'

    user_record = db.users.find_one({"email": target_email})
    if not user_record:
        return jsonify({"status": "error", "message": "User document not found"}), 404

    if action_decision == "Approved":
        onboarding_data = user_record.get("onboarding_data", {})
        step_1 = onboarding_data.get("step_1", {})
        step_5 = onboarding_data.get("step_5", {})
        update_payload = {
            "status": "Approved",
            "is_onboarded": True,
            "rankOrGrade": step_1.get("rankOrGrade"),
            "appt": step_1.get("appt"),
            "telephone": step_1.get("phoneNo")
        }
        if step_5.get("gender"):
            update_payload["gender"] = step_5.get("gender")
    elif action_decision == "Rejected":
        # FIXED: Sets the status matching your exact screenshot design rule
        update_payload = {"status": "Rejected", "directorate": "doa", "is_onboarded": False}

    db.users.update_one({"email": target_email}, {"$set": update_payload})
    return jsonify({"status": "success", "message": f"Personnel state updated."}), 200


# app.py addition at the bottom of the file

@onboarding_routes.route('/execute-directorate-reassignment', methods=['POST'])
def execute_directorate_reassignment():
    user_role = str(session.get('user_role', '')).lower().strip()
    if 'user_email' not in session or user_role not in ['cdsa', 'dcdsa', 'registry', 'director']:
        return jsonify({"status": "error", "message": "Unauthorized role token access"}), 403
        
    data = request.get_json()
    target_email = data.get('email')
    new_dir = data.get('directorate')
    
    if not target_email or not new_dir:
        return jsonify({"status": "error", "message": "Missing required field configurations"}), 400

    # Move staff member to the new directorate, and reset status back to In Progress
    db.users.update_one(
        {"email": target_email},
        {"$set": {
            "directorate": new_dir.upper().strip(),
            "status": "In Progress" 
        }}
    )
    
    return jsonify({"status": "success", "message": "Directorate tracking paths successfully re-mapped."}), 200


# app.py addition inside your routes definitions

@onboarding_routes.route('/request-additional-document', methods=['POST'])
def request_additional_document():
    # Only allow a Registry Clerk inside the DOA directorate to initialize this action
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized session"}), 403

    current_user = db.users.find_one({"email": session['user_email']})
    if not current_user:
        return jsonify({"status": "error", "message": "User session invalid"}), 403

    user_role = str(current_user.get('role', '')).strip().lower()
    user_directorate = str(current_user.get('directorate', '')).strip().upper()

    if user_role != 'registry' or user_directorate != 'DOA':
        return jsonify({"status": "error", "message": "Unauthorized: Only Registry personnel in DOA can request additional documents."}), 403
        
    data = request.get_json() or {}
    target_recruit_email = data.get('email')

    if not target_recruit_email:
        return jsonify({"status": "error", "message": "Target email is required"}), 400

    # Stamp the target user record with a pending request flag directive
    db.users.update_one(
        {"email": target_recruit_email},
        {"$set": {"training_request_active": True}}
    )
    return jsonify({"status": "success", "message": "Additional document channel open on user dashboard."}), 200


@onboarding_routes.route('/append-training-record', methods=['POST'])
def append_training_record():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Session expired"}), 401
        
    training_name = request.form.get('trainingName')
    training_period = request.form.get('trainingPeriod')
    training_file = request.files.get('trainingFile')

    if not training_name or not training_period:
        return jsonify({"status": "error", "message": "Training Name and Period are required fields"}), 400

    file_url = None
    if training_file and training_file.filename != '':
        # Save training file attachment cleanly into MongoDB GridFS
        file_id = fs.put(
            training_file,
            filename=secure_filename(training_file.filename),
            content_type=training_file.content_type or 'application/octet-stream',
            metadata={
                "uploaded_by": session['user_email'],
                "type": "additional_training",
                "upload_date": datetime.datetime.utcnow()
            }
        )
        file_url = f"/attachment/{str(file_id)}"

    new_training_entry = {
        "course_title": training_name.upper(),
        "timeline_period": training_period.upper(),
        "file_url": file_url,
        "timestamp_logged": datetime.datetime.now()
    }

    # Use $push to append this standalone object to an independent collection node array 
    # without overwriting or exposing step_1 baseline historical records!
    db.users.update_one(
        {"email": session['user_email']},
        {
            "$push": {"additional_trainings_ledger": new_training_entry},
            "$set": {"training_request_active": False} # Close card once appended successfully
        }
    )
    return jsonify({"status": "success", "message": "Ledger appended successfully."}), 200

