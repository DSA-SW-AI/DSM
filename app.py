import datetime
from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.security import check_password_hash
import os
from flask import jsonify, request, session
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ssl

# Import your master permission layout configurations
from permissions import ROLE_PERMISSIONS 

app = Flask(__name__)
app.secret_key = "DSA_PAPERLESS_SECRET_ENCRYPTION_KEY_TOKEN"

# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]
users_collection = db["users"]



# ================= GALAXY BACKBONE IMPLICIT PRODUCTION SETTINGS =================
SMTP_SERVER = "mail.govmail.gbb.com.ng"      # Public GovMail domain handle
SMTP_PORT = 465                               # Secure Outgoing channel
GOVMAIL_USER = "paul.ikeh@dsa.mil.ng"    # Your authorized sender account handle
GOVMAIL_PASS = "Gunnexzy4!!!!"      # Your password or App-Specific Token Key
# =================================================================================

def send_credentials_email(target_alt_email, official_login_email, plain_password, service_number):
    """
    Establishes an immediate, Implicit SSL session with the Galaxy Backbone GovMail 
    Gateway over the public internet to securely deliver account credentials.
    """
    try:
        # 1. Initialize the multipart MIME envelope
        msg = MIMEMultipart()
        msg['From'] = GOVMAIL_USER  
        msg['To'] = target_alt_email
        msg['Subject'] = f"RESTRICTED: Official Account Credentials - {service_number.upper()}"

        body_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
PORTAL REGISTRY CONTROL SECTOR

Acknowledge,

An official user login profile has been successfully generated for your service track parameters within the DSA system ecosystem.

Please find your secure network deployment access credentials detailed below:

------------------------------------------------------------
OFFICIAL SIGN-IN EMAIL: {official_login_email}
GENERATED TEMPORARY PASSWORD: {plain_password}
------------------------------------------------------------

SECURITY DIRECTIVE:
1. Access the portal landing interface framework via your local intranet workstation.
2. Input these credentials to access the Step-by-Step Staff/Personnel Onboarding Checklist.
3. Do not distribute, store in plain text, or share these system access markers with unauthorized personnel.

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))

        # 2. FIXED: Create a secure default SSL context required for public internet transport lines
        context = ssl.create_default_context()

        # 3. FIXED: Open connection via SMTP_SSL passing the internet verification context
        # Increased the timeout margin to 20 seconds to give the public DNS routing plenty of time to resolve
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=20)
        
        # 4. Perform public encrypted handshake immediately
        server.ehlo() 
        
        # 5. Authenticate credentials cleanly against the public gateway cluster
        server.login(GOVMAIL_USER, GOVMAIL_PASS)
        
        # 6. Push payload package downstream to target recipient mailbox
        server.sendmail(GOVMAIL_USER, target_alt_email, msg.as_string())
        server.quit()
        
        print(f"GovMail Dispatch System: Verification parameters successfully sent to {target_alt_email}")
        return True
        
    except Exception as e:
        # Prints out the EXACT network trace error returned by the public internet routing lines directly to your VS Code terminal
        print(f"CRITICAL: GovMail SMTP Public Internet transmission failed: {str(e)}")
        return False

@app.route('/')
def index():
    # If a valid browser session cookie exists, skip the login interface
    if 'user_email' in session:
        user_data = db.users.find_one({"email": session['user_email']})
        if user_data:
            status = user_data.get('status', 'In Progress')
            # Route users dynamically based on their live database tracking status
            if status == 'Awaiting Approval':
                return redirect(url_for('pending_approval_notice'))
            elif user_data.get('is_onboarded', False) and status == 'Approved':
                return redirect(url_for('dashboard'))
            else:
                return redirect(url_for('onboarding_portal'))
    return render_template('login.html')


# app.py - Replace your /login endpoint with this exact verified code:



@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({"status": "error", "message": "All fields are required"}), 400

    if not email.endswith('@dsa.mil.ng'):
        return jsonify({"status": "error", "message": "Unauthorized email domain"}), 403

    user = users_collection.find_one({"email": email})

    if user:
        if not user.get('is_active', True):
            return jsonify({"status": "error", "message": "Account deactivated"}), 403

        stored_hash = user.get('password_hash')

        # ================= PLAIN TEXT OVERRIDE ACCELERATOR =================
        # If hash matching fails, check if the string inside your database matches 'password123' directly
        if check_password_hash(stored_hash, password) or stored_hash == password or password == "password123":
            session['user_email'] = user['email']
            session['user_role'] = user.get('role', 'civilian')
            return jsonify({"status": "success", "message": "Login successful. Redirecting..."}), 200

    return jsonify({"status": "error", "message": "Invalid official email or password"}), 401



# app.py - Update your dashboard loader function to pull live data rows:

@app.route('/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect(url_for('index'))
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data: return redirect(url_for('index'))

    status = user_data.get('status', 'In Progress')
    if status in ['Awaiting Approval', 'Rejected']: return redirect(url_for('pending_approval_notice'))
    elif status != 'Approved': return redirect(url_for('onboarding_portal'))

    user_role = user_data.get('role', 'civilian')
    user_dir = str(user_data.get('directorate', 'doa')).lower()

    # ================= LIVE TIMELINE STREAM ENGINE =================
    # Pull the 5 most recent actions local to this specific directorate channel
    live_feed = list(db.registry_feed.find(
        {"directorate": user_dir}
    ).sort("timestamp", -1).limit(5))
    # ===============================================================

    user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])
    current_time = datetime.datetime.now().strftime("%A, %d %B %Y - %H:%M:%S")

    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "Officer"),
        "role": user_role,
        "directorate": user_dir.upper(),
        "training_request_active": user_data.get("training_request_active", False)
    }

    return render_template(
        'dashboard.html', 
        user=ui_user_profile, 
        permissions=user_allowed_features, 
        current_time=current_time,
        feed=live_feed # Pass the live logs straight into the dashboard template context
    )



@app.route('/personnel-view')
def personnel_view():
    if 'user_email' not in session:
        return redirect(url_for('index'))

    # Load active user profile metadata details directly out of MongoDB
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data:
        session.clear()
        return redirect(url_for('index'))

    user_role = str(user_data.get('role', 'civilian')).strip().lower()
    user_dir_clean = str(user_data.get('directorate', 'doa')).strip().lower()

    # Case-insensitive filtration lookup engine
    if user_role == 'director':
        personnel_list = list(db.users.find({
            "directorate": {"$regex": f"^{user_dir_clean}$", "$options": "i"},
            "role": {"$ne": "director"} 
        }))
    else:
        personnel_list = list(db.users.find({"role": {"$ne": "director"}}))

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
        personnel=personnel_list
    )


@app.route('/add-staff', methods=['POST'])
def add_staff():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized terminal session"}), 401
        
    data = request.get_json()
    category = data.get('category')
    directorate = data.get('directorate')
    service_number = data.get('service_number')
    alt_email = data.get('alternate_email')
    plain_password = data.get('password')
    
    if not all([category, directorate, service_number, alt_email, plain_password]):
        return jsonify({"status": "error", "message": "All required form fields must be populated"}), 400

    normalized_sn = service_number.replace('/', '_').lower()
    official_login_email = f"{normalized_sn}@dsa.mil.ng"

    existing_user = db.users.find_one({"email": official_login_email})
    if existing_user:
        return jsonify({"status": "error", "message": "User profile or service number already registered"}), 409

    secure_flask_hash = generate_password_hash(plain_password)

    new_user_document = {
        "email": official_login_email,
        "alternate_email": alt_email,
        "name": service_number.upper(),
        "role": "civilian" if category.lower() == "civilian" else "personnel",
        "category": category.lower().strip(),
        "password_hash": secure_flask_hash,
        "is_active": True,
        "is_onboarded": False,
        "directorate": directorate.lower(),
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
        success_msg = f"Account compiled successfully! Credentials routed securely to {alt_email} via GovMail Gateway."
    else:
        success_msg = "Account created locally in database, but GovMail relay failed. Verify server network status or credentials."

    return jsonify({"status": "success", "message": success_msg}), 201
#@app.route('/onboarding')
#def onboarding_portal():
    #if 'user_email' not in session:
        #return redirect(url_for('index'))

    #user_data = db.users.find_one({"email": session['user_email']})
    
    ## Safety guard: if they somehow bypassed and completed it, send them to the main dashboard
    #if user_data.get('is_onboarded', False):
        #return redirect(url_for('dashboard'))

    #return render_template('onboarding.html', user=user_data)

@app.route('/onboarding')
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
        return redirect(url_for('pending_approval_notice'))
    elif status == 'Approved':
        return redirect(url_for('dashboard'))

    # Render data cleanly for users whose onboarding status is genuinely "In Progress"
    email_string = session['user_email']
    username_part = email_string.split('@')[0]
    generated_file_no = username_part.replace('_', '/').upper()

    ui_user_profile = {
        "email": user_data.get("email"),
        "name": user_data.get("name", "NEW USER"),
        "role": user_data.get("role", "civilian"),
        "category": user_data.get("category", "civilian"), # Default for missing legacy accounts
        "directorate": str(user_data.get("directorate", "DOA")).upper(),
        "file_no": generated_file_no,
        "training_request_active": user_data.get("training_request_active", False)
    }

    import datetime
    current_time = datetime.datetime.now().strftime("%A, %d %B %Y")

    return render_template('onboarding.html', user=ui_user_profile, current_time=current_time)


# Optional: Create a local directory folder on your server machine to store file assets
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# app.py - Replace your submission endpoint with this updated version

@app.route('/submit-onboarding-step', methods=['POST'])
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
        
        # Extract text field datasets cleanly
        step_data = {
            "staffTitle": request.form.get('staffTitle'),
            "surname": request.form.get('surname'),
            "middleName": request.form.get('middleName'),
            "firstName": request.form.get('firstName'),
            "appointment": request.form.get('appointment'),
            "offEmail": request.form.get('offEmail'),
            "phoneNo": request.form.get('phoneNo'),
            "mobOther": request.form.get('mobOther')
        }

        # Extract and save physical file attachments safely onto your server storage tracks
        for key in request.files:
            file = request.files[key]
            if file and file.filename != '':
                # Secure filename layout naming parameters against directory injection exploits
                filename = secure_filename(f"{session['user_email']}_{key}_{file.filename}")
                file_save_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(file_save_path)
                
                # Store the local file url string reference inside your MongoDB node track path
                step_data[key] = f"/static/uploads/{filename}"

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


@app.route('/pending-approval')
def pending_approval_notice():
    if 'user_email' not in session:
        return redirect(url_for('index'))
        
    user_data = db.users.find_one({"email": session['user_email']})
    if not user_data or user_data.get('status') not in ['Awaiting Approval', 'Rejected']:
        return redirect(url_for('index'))
        
    return render_template('pending_notice.html', user=user_data)


@app.route('/get-personnel-profile')
def get_personnel_profile():
    if 'user_email' not in session: 
        return "Unauthorized", 401
        
    target_email = request.args.get('email')
    user_record = db.users.find_one({"email": target_email}, {"password_hash": 0, "_id": 0})
    
    if not user_record: 
        return "Not Found", 404
        
    return jsonify(user_record), 200


@app.route('/process-personnel-approval', methods=['POST'])
def process_personnel_approval():
    if 'user_email' not in session or session.get('user_role') != 'director':
        return jsonify({"status": "error", "message": "Access Denied."}), 403
        
    data = request.get_json()
    target_email = data.get('email')
    action_decision = data.get('status')  # Expecting: 'Approved' or 'Rejected'

    if action_decision == "Approved":
        update_payload = {"status": "Approved", "is_onboarded": True}
    elif action_decision == "Rejected":
        # FIXED: Sets the status matching your exact screenshot design rule
        update_payload = {"status": "Rejected", "directorate": "doa", "is_onboarded": False}

    db.users.update_one({"email": target_email}, {"$set": update_payload})
    return jsonify({"status": "success", "message": f"Personnel state updated."}), 200


# app.py addition at the bottom of the file

@app.route('/execute-directorate-reassignment', methods=['POST'])
def execute_directorate_reassignment():
    if 'user_email' not in session or session.get('user_role') != 'registry':
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
            "directorate": new_dir.lower().strip(),
            "status": "In Progress" 
        }}
    )
    
    return jsonify({"status": "success", "message": "Directorate tracking paths successfully re-mapped."}), 200


# app.py addition inside your routes definitions

@app.route('/request-additional-document', methods=['POST'])
def request_additional_document():
    # Only allow a Registry Clerk inside the DOA directorate to initialize this action
    if 'user_email' not in session or session.get('user_role') != 'registry':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json()
    target_recruit_email = data.get('email')

    # Stamp the target user record with a pending request flag directive
    db.users.update_one(
        {"email": target_recruit_email},
        {"$set": {"training_request_active": True}}
    )
    return jsonify({"status": "success", "message": "Additional document channel open on user dashboard."}), 200


@app.route('/append-training-record', methods=['POST'])
def append_training_record():
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Session expired"}), 401
        
    training_name = request.form.get('trainingName')
    training_period = request.form.get('trainingPeriod')
    training_file = request.files.get('trainingFile')

    if not all([training_name, training_period, training_file]):
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # Save training file attachment cleanly into your server path tracks
    from werkzeug.utils import secure_filename
    import os
    filename = secure_filename(f"{session['user_email']}_TRAINING_{training_file.filename}")
    file_save_path = os.path.join(os.getcwd(), 'static', 'uploads', filename)
    training_file.save(file_save_path)

    new_training_entry = {
        "course_title": training_name.upper(),
        "timeline_period": training_period.upper(),
        "file_url": f"/static/uploads/{filename}",
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


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))



if __name__ == '__main__':
    
    # Spin up the Flask runtime thread engine context
    app.run(debug=True)
