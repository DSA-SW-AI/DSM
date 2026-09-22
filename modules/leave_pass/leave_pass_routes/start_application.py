from flask import Flask, Blueprint, app, request, render_template, jsonify, redirect, flash, url_for, current_app, session
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from werkzeug.utils import secure_filename, send_file
from gridfs import GridFS
import os
import re
from .leave_logic import validate_leave_request, LeaveBalances, working_days_between, get_legacy_fields
from .leave_helper import update_leave_balance, deduct_casual_with_annual, get_public_holidays, get_staff_object, get_current_balances, get_hospitalization_records, calendar_days_between
import holidays
import random
from pymongo import MongoClient
from modules.extensions import socketio
from flask_wtf.csrf import CSRFProtect

from permissions import ROLE_PERMISSIONS 

csrf = CSRFProtect()
application_routes = Blueprint('application_routes', __name__)


def _parse_leave_date(val):
    """Parse date value from application document into a datetime.date object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00')).date()
        except Exception:
            try:
                return datetime.strptime(val[:10], '%Y-%m-%d').date()
            except Exception:
                pass
    return None


def get_staff_on_leave_identifiers(apps_coll, ref_date=None):
    """
    Returns a tuple of sets: (service_numbers_on_leave, emails_on_leave).
    A staff member is on active leave/pass if:
      - They have an active/approved/issued leave or pass application of ANY leave type.
      - They have not officially returned (returned_from_leave is not True and returned_status != 'approved').
    """
    if apps_coll is None:
        return set(), set()

    service_numbers = set()
    emails = set()

    users_coll = None
    try:
        users_coll = apps_coll.database.get_collection('users')
    except Exception:
        pass

    query = {
        'status': {
            '$in': [
                'approved', 'Approved', 
                'issued', 'Issued', 
                'pending', 'Pending', 
                'Recommended for Approval', 'recommended for approval', 
                'awaiting_reliever'
            ]
        }
    }
    try:
        for app_doc in apps_coll.find(query):
            # Check if staff member has already returned from leave
            ret = app_doc.get('leave_pass_returned') or {}
            is_returned = (
                ret.get('returned_from_leave') is True or 
                str(ret.get('returned_status', '')).lower() == 'approved'
            )
            if is_returned:
                continue

            # Record applicant service number and email
            app_id_val = str(app_doc.get('applicantId') or '').strip()
            if app_id_val:
                if '@' in app_id_val:
                    emails.add(app_id_val.lower())
                    if users_coll is not None:
                        u = users_coll.find_one({'email': app_id_val.lower()})
                        if u and u.get('service_number'):
                            service_numbers.add(str(u.get('service_number')).strip().upper())
                else:
                    service_numbers.add(app_id_val.upper())
                    if users_coll is not None:
                        u = users_coll.find_one({'service_number': app_id_val.upper()})
                        if u and u.get('email'):
                            emails.add(str(u.get('email')).strip().lower())

            app_email = str(app_doc.get('applicantEmail') or app_doc.get('email') or '').strip().lower()
            if app_email:
                emails.add(app_email)
                if users_coll is not None:
                    u = users_coll.find_one({'email': app_email})
                    if u and u.get('service_number'):
                        service_numbers.add(str(u.get('service_number')).strip().upper())
    except Exception as err:
        print(f"Error querying staff on leave: {err}")

    return service_numbers, emails


def generate_reference_id(directorate):
    """
    Generate unique reference ID: DSA-{DIRECTORATE}-{YEAR}-{6_RANDOM_DIGITS}
    Example: DSA-DCS-2026-123456
    """
    # Get current year
    year = datetime.now().year
    
    # Generate 6 random digits (not alphanumeric, just numbers)
    random_digits = ''.join(str(random.randint(0, 9)) for _ in range(6))
    
    # Format: DSA-{DIRECTORATE}-{YEAR}-{6_DIGITS}
    return f"DSA-{directorate}-{year}-{random_digits}"


def get_db_collections():
    """Get database collections directly instead of relying on current_app attributes."""
    client = MongoClient("mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "DSM")
    db = client[db_name]
    
    return {
        'users': db.users,
        'leave_balances': db.leave_balances,
        'applications': db.applications,
        'fs': GridFS(db),
        'medical_records': db.medical_records if 'medical_records' in db.list_collection_names() else None
    }


def convert_doc_to_LeaveBalances(doc: dict, service_number: str, year: int):
    """Convert MongoDB document to LeaveBalances object."""
    from .leave_logic import LeaveBalances
    
    return LeaveBalances(
        annual_entitlement=doc.get('annualEntitlement', 0),
        annual_remaining=doc.get('annualRemaining', 0),
        compassionate_used=doc.get('compassionateUsed', 0),
        compassionate_remaining=doc.get('compassionateRemaining', 10),
        casual_calendar_days_used=doc.get('casualCalendarDaysUsed', 0),
        casual_calendar_days_remaining=doc.get('casualCalendarDaysRemaining', 7),
        sick_this_year=doc.get('sickThisYear', 0),
        sick_rolling_12m=doc.get('sickRolling12m', 0),
        sick_this_year_remaining=doc.get('sickThisYearRemaining', 21),
        sick_rolling_remaining=doc.get('sickRollingRemaining', 42),
        maternity_available=doc.get('maternityAvailable', True),
        paternity_available=doc.get('paternityAvailable', True),
        disembarkation_available=doc.get('disembarkationAvailable', True),
        terminal_granted=doc.get('terminalGranted', False),
        terminal_available=doc.get('terminalAvailable', True),
        has_international_permission=doc.get('hasInternationalPermission', False),
        year=year,
        service_number=service_number,
        full_name=doc.get('name', ''),
        directorate=doc.get('directorate', ''),
        grade=doc.get('grade', 0)
    )



def notify_pending_approval(app, next_step, current_user):
    """
    Emits Socket.IO notification to next approver(s).
    Ensures notifications strictly target the approver's private email room and role room,
    never leaking to other users via shared/duplicate service numbers.
    """
    users_coll = current_app.users_collection
    directorate = app.get("directorate")
    message = f"Application {app['referenceId']} is awaiting your approval."

    role = next_step.get("role")
    approver_id = next_step.get("approverId")
    approver_email = (next_step.get("approverEmail") or "").strip().lower()

    # If approverEmail is missing from step, resolve it from users_coll accurately
    if not approver_email and users_coll is not None:
        target_user = None
        if role in ("civilian_head_cao", "civilian_head"):
            target_user = users_coll.find_one({
                "role": {"$in": ["civilian_head_cao", "civilian_head"]},
                "is_active": True
            })
            if not target_user:
                target_user = users_coll.find_one({
                    "role": "deputy_civilian_head_cao" if role == "civilian_head_cao" else "deputy_civilian_head",
                    "is_active": True
                })
        elif role == "so":
            target_user = users_coll.find_one({"directorate": directorate, "is_so_approver": True, "is_active": True})
        elif role == "ad":
            target_user = users_coll.find_one({"directorate": directorate, "is_ad_approver": True, "is_active": True})
        elif role == "dd":
            target_user = users_coll.find_one({"directorate": directorate, "is_dd_approver": True, "is_active": True})
        elif role == "director":
            if next_step.get("is_final_approver") in (True, "true", "True"):
                target_user = users_coll.find_one({"is_final_approver": {"$in": ["true", True]}, "is_active": True})
            else:
                target_user = users_coll.find_one({"directorate": directorate, "role": "director", "is_active": True})
        elif role == "central_registry":
            target_user = users_coll.find_one({"directorate": "CDSA", "role": "central_registry", "is_active": True}) \
                or users_coll.find_one({"directorate": "CDSA", "role": "registry", "is_active": True}) \
                or users_coll.find_one({"is_cdsa_approver": {"$in": ["true", True]}, "is_active": True})
        elif approver_id:
            target_user = users_coll.find_one({
                "service_number": approver_id,
                "$or": [
                    {"is_approval_role": True},
                    {"role": role},
                    {"is_so_approver": True},
                    {"is_ad_approver": True},
                    {"is_dd_approver": True},
                    {"is_final_approver": True}
                ]
            }) or users_coll.find_one({"service_number": approver_id})

        if target_user and target_user.get("email"):
            approver_email = target_user["email"].strip().lower()

    date_str = None
    if app.get("createdAt"):
        date_str = app.get("createdAt").strftime('%Y-%m-%d') if hasattr(app.get("createdAt"), 'strftime') else str(app.get("createdAt"))

    payload = {
        "type": "leave_approval",
        "_id": str(app["_id"]),
        "applicationId": str(app["_id"]),
        "triggeredBy": current_user.get("fullName", current_user.get("name")),
        "referenceId": app["referenceId"],
        "message": message,
        "role": role,
        "approverEmail": approver_email,
        "leave_type": app.get("leave_type"),
        "directorate": directorate,
        "date": date_str,
    }

    print(f"[SOCKET] Notifying next approver for role: {role}, approverEmail: {approver_email}")

    # Emit to individual approver's email room (each user joins USER_{email.lower()})
    if approver_email:
        email_room = f"USER_{approver_email}"
        print(f"[SOCKET] Direct notification to email room: {email_room}")
        socketio.emit("new_notification", payload, room=email_room)

    # Emit to role room (e.g. ROLE_civilian_head_cao, ROLE_so, etc.)
    if role:
        role_room = f"ROLE_{role}"
        print(f"[SOCKET] Role-based notification to room: {role_room}")
        socketio.emit("new_notification", payload, room=role_room)






def ensure_applicant_metadata(applicant):
    if not applicant:
        return
    keys_to_check = ['rankOrGrade', 'designation', 'is_so_approver', 'is_ad_approver', 'is_dd_approver', 'is_final_approver', 'category', 'role']
    if any(k not in applicant for k in keys_to_check):
        collections = get_db_collections()
        users_coll = collections.get('users')
        if users_coll is not None:
            user_doc = None
            if applicant.get('email'):
                user_doc = users_coll.find_one({"email": applicant.get('email')})
            if not user_doc and applicant.get('service_number'):
                user_doc = users_coll.find_one({
                    "service_number": applicant.get('service_number'),
                    "is_approval_role": {"$ne": True}
                })
            if not user_doc and applicant.get('service_number'):
                user_doc = users_coll.find_one({"service_number": applicant.get('service_number')})

            if user_doc:
                applicant['rankOrGrade'] = user_doc.get('rankOrGrade', '')
                applicant['designation'] = user_doc.get('appt', '') or user_doc.get('designation', '')
                applicant['gender'] = user_doc.get('onboarding_data', {}).get('step_5', {}).get('gender', '')
                applicant['is_so_approver'] = user_doc.get('is_so_approver') in (True, "true", "True")
                applicant['is_ad_approver'] = user_doc.get('is_ad_approver') in (True, "true", "True")
                applicant['is_dd_approver'] = user_doc.get('is_dd_approver') in (True, "true", "True")
                applicant['is_final_approver'] = user_doc.get('is_final_approver') in (True, "true", "True")
                if 'category' not in applicant or not applicant.get('category'):
                    applicant['category'] = user_doc.get('category', '')
                if 'role' not in applicant or not applicant.get('role'):
                    applicant['role'] = user_doc.get('role', '')

@application_routes.route('/application_form', methods=['GET', 'POST'])
def application_form():

    current_user = {
            "service_number":  session.get("service_number"),
            "fullName":        session.get("name"),
            "name":            session.get("name"),
            "role":            session.get("role"),
            "directorate":     session.get("directorate"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "gender":          session.get("onboarding_data", {}).get("step_5", {}).get("gender"),
            "email":           session.get("email"),
            "is_so_approver":  session.get("is_so_approver", False),
            "is_dd_approver":  session.get("is_dd_approver", False),
            "is_ad_approver":  session.get("is_ad_approver", False),
            "is_final_approver":  session.get("is_final_approver", False),
    }
    
    applicant = session.get('applicant')
    if not applicant:
        flash("Session expired. Please verify your service number again.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    
    ensure_applicant_metadata(applicant)

    # Guard: if session says this person isn't civilian, redirect to correct form
    role_bucket = applicant.get('role_bucket', 'civilian')
    if role_bucket != 'civilian':
        ROLE_ENDPOINTS = {
            'officer':         'application_routes.application_form_officer',
            'dd': 'application_routes.application_form_dd',
            'ad': 'application_routes.application_form_officer',
            'so': 'application_routes.application_form_officer',
            'personnel': 'application_routes.application_form_officer',
            'director':        'application_routes.application_form_director',
        }
        return redirect(url_for(ROLE_ENDPOINTS[role_bucket]))

    leave_data = get_leave_data_for_applicant(applicant, request)
    if request.method == 'POST':
        return handle_application_post(applicant, request, leave_data,
                                       role_bucket=role_bucket)  # from session
    
    potential_relievers = []
    try:
        collections = get_db_collections()
        users_coll = collections.get('users')
        apps_coll = collections.get('applications')
        applicant_dir = (applicant.get('directorate') or '').strip().lower()
        applicant_email = (applicant.get('email') or '').strip().lower()
        applicant_svc = (applicant.get('service_number') or '').strip().upper()

        if users_coll is not None and applicant_dir:
            # Query staff currently on any leave/pass type
            svc_nums_on_leave, emails_on_leave = get_staff_on_leave_identifiers(apps_coll)

            raw_candidates = list(users_coll.find({
                "directorate": {"$regex": f"^{re.escape(applicant_dir)}$", "$options": "i"},
                "role": {"$in": ["civilian", "civilian_head_cao"]},
                "category": {"$regex": "^civilian$", "$options": "i"},
                "email": {"$ne": applicant.get('email')},
                "is_active": True
            }))

            for u in raw_candidates:
                u_email = (u.get('email') or '').strip().lower()
                u_svc = (u.get('service_number') or '').strip().upper()

                # Exclude applicant themselves
                if applicant_email and u_email == applicant_email:
                    continue
                if applicant_svc and u_svc == applicant_svc and (applicant.get('name') or '').strip().lower() == (u.get('name') or '').strip().lower():
                    continue

                # Exclude if staff member is currently on any leave/pass type
                if (u_svc and u_svc in svc_nums_on_leave) or (u_email and u_email in emails_on_leave):
                    continue

                potential_relievers.append(u)
    except Exception as e:
        print(f"Error fetching potential relievers: {e}")

    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])

    applicant_gender = applicant.get('gender', '')
    return render_template('application_form.html', 
                           applicant=applicant, 
                           applicant_gender=applicant_gender,
                           leave_data=leave_data, 
                           user=current_user, 
                           permissions=user_allowed_features,
                           potential_relievers=potential_relievers)


@application_routes.route('/application_form_officer', methods=['GET', 'POST'])
def application_form_officer():
    current_user = {
                "service_number":  session.get("service_number"),
                "fullName":        session.get("name"),
                "name":            session.get("name"),
                "directorate":     session.get("directorate"),
                "designation":            session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
                "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
                "gender":          session.get("onboarding_data", {}).get("step_5", {}).get("gender"),
                "email":           session.get("email"),
                "role":            session.get("role"),
                "is_so_approver": session.get("is_so_approver", False),
                "is_dd_approver":  session.get("is_dd_approver", False),
                "is_ad_approver":  session.get("is_ad_approver", False),
                "is_final_approver":  session.get("is_final_approver", False),
    }
        
    applicant = session.get('applicant')
    if not applicant:
        flash("Session expired. Please verify your service number again.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
    ensure_applicant_metadata(applicant)

    role_bucket = applicant.get('role_bucket', 'officer')
    leave_data = get_leave_data_for_applicant(applicant, request)
    if request.method == 'POST':
        return handle_application_post(applicant, request, leave_data,
                                       role_bucket=role_bucket)
    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])

    applicant_gender = applicant.get('gender', '')
    return render_template('application_form_officer.html', 
                           applicant=applicant, 
                           leave_data=leave_data, 
                           user=current_user,
                           applicant_gender=applicant_gender, 
                           permissions=user_allowed_features)


@application_routes.route('/application_form_dd', methods=['GET', 'POST'])
def application_form_dd():
    current_user = {
                "service_number":  session.get("service_number"),
                "fullName":        session.get("name"),
                "name":            session.get("name"),
                "directorate":     session.get("directorate"),
                "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
                "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
                "gender":          session.get("onboarding_data", {}).get("step_5", {}).get("gender"),
                "email":           session.get("email"),
                "is_so_approver": session.get("is_so_approver", False),
                "is_dd_approver":  session.get("is_dd_approver", False),
                "is_ad_approver":  session.get("is_ad_approver", False),
                "is_final_approver":  session.get("is_final_approver", False),
                "role":            session.get("role"),
    }
        
    applicant = session.get('applicant')
    if not applicant:
        flash("Session expired. Please verify your service number again.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
    ensure_applicant_metadata(applicant)

    role_bucket = applicant.get('role_bucket', 'dd')
    leave_data = get_leave_data_for_applicant(applicant, request)
    if request.method == 'POST':
        return handle_application_post(applicant, request, leave_data,
                                       role_bucket=role_bucket)
    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])

    applicant_gender = applicant.get('gender', '')
    return render_template('application_form_dd.html', 
                           applicant=applicant, 
                           leave_data=leave_data, 
                           user=current_user, 
                           applicant_gender=applicant_gender,
                           permissions=user_allowed_features)


@application_routes.route('/application_form_director', methods=['GET', 'POST'])
def application_form_director():
    current_user = {
                "service_number":  session.get("service_number"),
                "fullName":        session.get("name"),
                "name":            session.get("name"),
                "directorate":     session.get("directorate"),
                "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
                "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
                "gender":          session.get("onboarding_data", {}).get("step_5", {}).get("gender"),
                "email":           session.get("email"),
                "is_so_approver": session.get("is_so_approver", False),
                "is_dd_approver":  session.get("is_dd_approver", False),
                "is_ad_approver":  session.get("is_ad_approver", False),
                "is_final_approver":  session.get("is_final_approver", False),
                "role":            session.get("role")
    }
        
    applicant = session.get('applicant')
    if not applicant:
        flash("Session expired. Please verify your service number again.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
    ensure_applicant_metadata(applicant)

    role_bucket = applicant.get('role_bucket', 'director')
    leave_data = get_leave_data_for_applicant(applicant, request)
    if request.method == 'POST':
        return handle_application_post(applicant, request, leave_data,
                                       role_bucket=role_bucket)

    collections = get_db_collections()
    users_coll = collections.get('users')
    dd_list = []
    if users_coll is not None and applicant.get("directorate"):
        # Query deputy directors in the same directorate
        dd_users = users_coll.find({
            "directorate": applicant.get("directorate"),
            "role": "dd",
            "is_active": True
        })
        for u in dd_users:
            dd_list.append({
                "service_number": u.get("service_number"),
                "name": u.get("name"),
                "rankOrGrade": u.get("rankOrGrade") or u.get("rank") or "",
                "telephone": u.get("telephone") or u.get("phone") or ""
            })

    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])

    applicant_gender = applicant.get('gender', '')
    return render_template(
        'application_form_director.html',
        applicant=applicant,
        leave_data=leave_data,
        user=current_user,
        permissions=user_allowed_features,
        deputy_directors=dd_list,
        applicant_gender=applicant_gender
    )


def get_leave_data_for_applicant(applicant, request):
    """
    Extracted from your existing application_form() — fetches balances,
    entitlements, and previous applications for the sidebar.
    Returns the leave_data dict.
    """
    leave_data = {
        'balances': None,
        'previous_applications': [],
        'entitlements': {
            'annual': 30,
            'casual_calendar': 7,
            'compassionate': 10,
            'sick_normal': 21,
            'sick_rolling': 42,
            'maternity': 0,
            'paternity': 0,
            'disembarkation': True,
            'terminal': 42
        }
    }

    try:
        # Check if users_collection, leave_balances, and applications_collection are defined on current_app
        users_coll = getattr(current_app, 'users_collection', None)
        balances_coll = getattr(current_app, 'leave_balances', None)
        apps_coll = getattr(current_app, 'applications_collection', None)
        
        # Fallback to get_db_collections if not found
        if users_coll is None or balances_coll is None or apps_coll is None:
            collections = get_db_collections()
            if users_coll is None:
                users_coll = collections['users']
            if balances_coll is None:
                balances_coll = collections['leave_balances']
            if apps_coll is None:
                apps_coll = collections['applications']

        service_number = applicant['service_number']
        actual_service_number = service_number

        # 1. Fetch staff member and populate custom entitlements
        if users_coll is not None:
            staff_member = users_coll.find_one({
                "$or": [
                    {"service_number": service_number},
                    {"email": service_number},
                    {"serviceNumber": service_number}
                ]
            })
            if staff_member:
                actual_service_number = staff_member.get('service_number') or staff_member.get('serviceNumber') or service_number
                grade = 0
                rank_or_grade = staff_member.get('rankOrGrade', '') or staff_member.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade", "")
                if 'Grade Level' in rank_or_grade:
                    try:
                        grade = int(rank_or_grade.split('Grade Level')[-1].strip())
                    except:
                        grade = 0

                # Entitlements by grade
                if 2 <= grade <= 6:
                    annual, terminal = 21, 42
                elif 7 <= grade <= 15:
                    annual, terminal = 30, 90
                else:
                    annual, terminal = 21, 42  # default

                
                gender = staff_member.get("onboarding_data", {}).get("step_5", {}).get("gender", "").lower()
                leave_data['entitlements'] = {
                    'annual': annual,
                    'casual_calendar': 7,
                    'compassionate': 10,
                    'sick_normal': 21,
                    'sick_rolling': 42,
                    'maternity': 112 if gender == 'female' else 0,
                    'paternity': 14 if gender == 'male' else 0,
                    'disembarkation': True,
                    'terminal': terminal
                }

        # 2. Fetch current balances
        if balances_coll is not None:
            current_year = datetime.now().year
            balance = balances_coll.find_one({
                "$or": [
                    {"serviceNumber": actual_service_number},
                    {"service_number": actual_service_number},
                    {"serviceNumber": service_number},
                    {"service_number": service_number}
                ],
                "year": current_year
            })

            if balance:
                current_balance = convert_doc_to_LeaveBalances(
                    balance, actual_service_number, current_year
                )
                display_annual = balance.get('annualRemaining', 0)
                display_casual = balance.get('casualCalendarDaysRemaining', 7)
                display_casual_used = balance.get('casualCalendarDaysUsed', 0)

                if request.method == 'POST':
                    pending_deduction = session.get('pending_deduction')
                    if pending_deduction:
                        if pending_deduction.get('annual_deduction', 0) > 0:
                            display_annual = current_balance.annual_remaining - pending_deduction['annual_deduction']
                        if pending_deduction.get('leave_type') == 'casual':
                            display_casual = max(0, 7 - (display_casual_used + pending_deduction.get('calendar_days', 0)))

                balance['annualRemaining'] = display_annual
                balance['casualCalendarDaysRemaining'] = display_casual
                balance['casualCalendarDaysUsed'] = display_casual_used

                if request.method == 'GET':
                    session.pop('pending_deduction', None)
                else:
                    pending_deduction = session.get('pending_deduction')
                    if pending_deduction:
                        balance['pending_deduction'] = pending_deduction

                leave_data['balances'] = balance
                leave_data['balance_object'] = current_balance

        # 3. Fetch previous applications
        if apps_coll is not None:
            six_months_ago = datetime.now() - timedelta(days=180)
            previous_apps = apps_coll.find({
                "$or": [
                    {"applicantId": actual_service_number},
                    {"applicantId": service_number}
                ],
                "status": {"$in": ["approved", "issued"]},
                "createdAt": {"$gte": six_months_ago}
            }).sort("createdAt", -1).limit(5)

            leave_data['previous_applications'] = list(previous_apps)

            # Fetch the most recent approved/issued application to get previous leave date
            most_recent_app = apps_coll.find_one(
                {
                    "$or": [
                        {"applicantId": actual_service_number},
                        {"applicantId": service_number}
                    ],
                    "status": {"$in": ["approved", "issued"]}
                },
                sort=[("createdAt", -1)]
            )
            previous_leave_date_str = "No recent date of previous leave/pass"
            if most_recent_app:
                prev_start = most_recent_app.get("startDate")
                if prev_start:
                    if isinstance(prev_start, datetime):
                        previous_leave_date_str = prev_start.strftime("%Y-%m-%d")
                    else:
                        previous_leave_date_str = str(prev_start)
                else:
                    dates_dict = most_recent_app.get("dates", {})
                    prev_date = dates_dict.get("applicationDate") or dates_dict.get("effectiveDate")
                    if prev_date:
                        if isinstance(prev_date, datetime):
                            previous_leave_date_str = prev_date.strftime("%Y-%m-%d")
                        else:
                            previous_leave_date_str = str(prev_date)
            leave_data['previous_leave_date_str'] = previous_leave_date_str
        else:
            leave_data['previous_leave_date_str'] = "No recent date of previous leave/pass"

    except Exception as e:
        print(f"Error fetching leave data: {e}")

    return leave_data




def handle_application_post(applicant, request, leave_data, role_bucket):
    ensure_applicant_metadata(applicant)
    
    # ─── Read form fields ──────────────────────────────────────────────
    leave_type              = request.form.get('leave_type', '').strip()
    start_date_str          = request.form.get('start_date', '').strip()
    end_date_str            = request.form.get('end_date', '').strip()
    effective_date_str      = request.form.get('effective_date', '').strip()
    number_of_days_str      = request.form.get('calculated_days_actual', '').strip()
    reasons                 = request.form.get('reasons_for_application', '').strip()
    place_intended          = request.form.get('place_intended', '').strip()
    contact_address         = request.form.get('contact_address', '').strip()
    name_of_reliever        = request.form.get('name_of_reliever', '').strip()
    reliever_email          = request.form.get('reliever_email', '').strip().lower()
    director_reliever_service_number = request.form.get('director_reliever_service_number', '').strip()
    director_reliever_rank = request.form.get('director_reliever_rank', '').strip()
    director_reliever_name = request.form.get('director_reliever_name', '').strip()
    director_reliever_telephone = request.form.get('director_reliever_telephone', '').strip()
    previous_leave_pass_date = request.form.get('previous_leave_pass_date', '').strip()
    appt_of_reliever        = request.form.get('appt_of_reliever', '').strip()
    telephone               = request.form.get('telephone', '').strip()
    expected_delivery_date_str = request.form.get('expected_delivery_date', '').strip()
    attachment_months       = request.form.get('attachment_months', type=int)
    outside_nigeria         = False

    # Derive the redirect endpoint for this role so error flashes
    # always send the user back to the correct form
    ROLE_FORM_ENDPOINTS = {
        'civilian':        'application_routes.application_form',
        'officer':         'application_routes.application_form_officer',
        'personnel':         'application_routes.application_form_officer',
        'ad':         'application_routes.application_form_officer',
        'so':         'application_routes.application_form_officer',
        'dd': 'application_routes.application_form_dd',
        'director':        'application_routes.application_form_director',
    }
    form_endpoint = ROLE_FORM_ENDPOINTS.get(role_bucket, 'application_routes.application_form_officer')

    # ─── Sick leave conditional fields ────────────────────────────────
    has_medical_certificate = False
    hospitalized            = False
    first_hospitalization   = False
    if leave_type == 'sick':
        has_medical_certificate = request.form.get('has_medical_certificate') == 'true'
        hospitalized            = request.form.get('hospitalized') == 'true'
        first_hospitalization   = request.form.get('first_hospitalization') == 'true'

    # ─── Parse dates ──────────────────────────────────────────────────
    try:
        start_date     = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date       = datetime.strptime(end_date_str, '%Y-%m-%d')
        effective_date = datetime.strptime(effective_date_str, '%Y-%m-%d')
    except ValueError:
        flash("Invalid date format.", "error")
        return redirect(url_for(form_endpoint))

    expected_delivery_date = None
    if expected_delivery_date_str:
        try:
            expected_delivery_date = datetime.strptime(expected_delivery_date_str, '%Y-%m-%d')
        except ValueError:
            flash("Invalid expected delivery date format.", "error")
            return redirect(url_for(form_endpoint))

    # ─── Date validation ──────────────────────────────────────────────
    if not (start_date <= effective_date < end_date):
        flash("Dates must follow: Application Date ≤ Effective Date < End Date", "error")
        return redirect(url_for(form_endpoint))

    if leave_type == 'maternity' and expected_delivery_date:
        if effective_date > expected_delivery_date:
            flash("Effective date cannot be after expected delivery date", "error")
            return redirect(url_for(form_endpoint))

    # ─── Calculate working/calendar days ──────────────────────────────
    year             = effective_date.year
    public_holidays  = get_public_holidays(year)
    working_days_requested  = working_days_between(effective_date, end_date, public_holidays)
    calendar_days_requested = calendar_days_between(effective_date, end_date)

    # ─── Required field validation ────────────────────────────────────
    required_fields = [
        ('Leave type',      leave_type),
        ('Start date',      start_date_str),
        ('End date',        end_date_str),
        ('Effective date',  effective_date_str),
        ('Reason',          reasons),
        ('Place intended',  place_intended),
        ('Contact address', contact_address),
    ]
    missing = [name for name, val in required_fields if not val]
    if missing:
        flash(f"Please fill in: {', '.join(missing)}", "error")
        return redirect(url_for(form_endpoint))

    if leave_type == 'maternity' and not expected_delivery_date_str:
        flash("Expected delivery date is required for maternity leave", "error")
        return redirect(url_for(form_endpoint))

    if leave_type == 'disembarkation' and not attachment_months:
        flash("Duration of course/attachment is required for disembarkation leave", "error")
        return redirect(url_for(form_endpoint))

    if leave_type == 'sick' and not has_medical_certificate:
        flash("Medical certificate is required for sick leave", "error")
        return redirect(url_for(form_endpoint))

    # ─── File uploads ─────────────────────────────────────────────────
    attachments = []
    if 'attachments' in request.files:
        fs    = current_app.fs
        files = request.files.getlist('attachments')
        for file in files:
            if file and file.filename:
                file_content = file.read()
                if len(file_content) > 20 * 1024 * 1024:
                    flash(f"File '{file.filename}' is too large (max 20MB)", "error")
                    return redirect(url_for(form_endpoint))
                file.seek(0)
                try:
                    file_id = fs.put(
                        file,
                        filename=secure_filename(file.filename),
                        content_type=file.content_type or 'application/octet-stream',
                        metadata={
                            "uploaded_by": applicant['service_number'],
                            "application_type": leave_type,
                            "upload_date": datetime.utcnow()
                        }
                    )
                    attachments.append({
                        "gridfs_id":   str(file_id),
                        "filename":    file.filename,
                        "contentType": file.content_type,
                        "size":        len(file_content),
                        "uploadedAt":  datetime.utcnow()
                    })
                except Exception as e:
                    flash(f"Failed to upload {file.filename}: {str(e)}", "error")
                    return redirect(url_for(form_endpoint))

    if has_medical_certificate and not attachments:
        flash("Please upload your medical certificate", "error")
        return redirect(url_for(form_endpoint))

    # ─── Fetch staff + balances ───────────────────────────────────────
    collections = get_db_collections()
    try:
        current_app.users_collection   = collections['users']
        current_app.user_collection    = collections['users']
        current_app.leave_balances    = collections['leave_balances']
        current_app.medical_records   = collections['medical_records']
        staff                         = get_staff_object(applicant['service_number'])
        balances                      = get_current_balances(applicant['service_number'])
        hospitalization_records       = get_hospitalization_records(applicant['service_number'])
    except Exception as e:
        flash(f"Error fetching staff data: {str(e)}", "error")
        return redirect(url_for(form_endpoint))

    # ─── Balance sufficiency check ────────────────────────────────────
    balance_sufficient   = True
    insufficient_message = ""
    excess_working_days  = 0

    if leave_type == "casual" and calendar_days_requested > balances.casual_calendar_days_remaining:
        free_days = balances.casual_calendar_days_remaining
        if free_days > 0:
            free_end_date    = effective_date + timedelta(days=free_days - 1)
            excess_start     = free_end_date + timedelta(days=1)
            excess_working_days = working_days_between(excess_start, end_date, public_holidays)
        else:
            excess_working_days = working_days_requested

    if leave_type == "annual" and balances.annual_remaining < working_days_requested:
        balance_sufficient   = False
        insufficient_message = (f"Insufficient annual leave. You have {balances.annual_remaining} "
                                f"days remaining but requested {working_days_requested}.")

    elif leave_type == "casual":
        if calendar_days_requested > balances.casual_calendar_days_remaining:
            if balances.annual_remaining < excess_working_days:
                balance_sufficient   = False
                insufficient_message = (f"Insufficient annual leave for excess casual days. "
                                        f"Need {excess_working_days} days but only "
                                        f"{balances.annual_remaining} remaining.")

    elif leave_type == "compassionate" and balances.compassionate_remaining < working_days_requested:
        balance_sufficient   = False
        insufficient_message = (f"Insufficient compassionate leave. You have "
                                f"{balances.compassionate_remaining} days remaining but "
                                f"requested {working_days_requested}.")

    elif leave_type == "disembarkation":
        required_days = 14 if attachment_months and attachment_months > 6 else 7
        if balances.annual_remaining < required_days:
            balance_sufficient   = False
            insufficient_message = (f"Insufficient annual leave for disembarkation. "
                                    f"Need {required_days} days but only "
                                    f"{balances.annual_remaining} remaining.")

    if not balance_sufficient:
        flash(f"❌ {insufficient_message}", "error")
        return redirect(url_for(form_endpoint))

    # ─── TACOS validation ─────────────────────────────────────────────
    request_data = {
        'type':                    leave_type,
        'working_days_requested':  working_days_requested,
        'start_date':              start_date,
        'end_date':                end_date,
        'effective_date':          effective_date,
        'has_medical_certificate': has_medical_certificate,
        'hospitalized':            hospitalized,
        'first_hospitalization':   first_hospitalization,
        'outside_nigeria':         outside_nigeria,
        'expected_delivery_date':  expected_delivery_date,
        'attachment_months':       attachment_months or 0,
        'calendar_days':           calendar_days_requested,
    }

    is_valid, message, metadata = validate_leave_request(
        request_data=request_data,
        staff=staff,
        current_year_balances=balances,
        public_holidays=public_holidays,
        hospitalization_records=hospitalization_records
    )

    if not is_valid:
        flash(f"Leave validation failed: {message}", "error")
        if metadata and metadata.get('notes'):
            for note in metadata['notes']:
                if isinstance(note, str) and note.startswith("Warning:"):
                    flash(note, "warning")
        return redirect(url_for(form_endpoint))

    # ─── Casual leave deduction preview ───────────────────────────────
    annual_deduction  = metadata.get('deduct_from_annual', 0) if metadata else 0
    free_casual_days  = 0
    excess_casual_days = 0
    excess_working_days = 0

    if leave_type == 'casual':
        casual_remaining = balances.casual_calendar_days_remaining
        if calendar_days_requested <= casual_remaining:
            free_casual_days = calendar_days_requested
        else:
            free_casual_days   = casual_remaining
            excess_casual_days = calendar_days_requested - casual_remaining
            if free_casual_days > 0:
                free_end_date   = effective_date + timedelta(days=free_casual_days - 1)
                excess_start    = free_end_date + timedelta(days=1)
                excess_working_days = working_days_between(excess_start, end_date, public_holidays)
            else:
                excess_working_days = working_days_requested
        annual_deduction = excess_working_days

    session['pending_deduction'] = {
        'leave_type':             leave_type,
        'calendar_days':          calendar_days_requested if leave_type == 'casual' else 0,
        'working_days':           working_days_requested  if leave_type != 'casual' else 0,
        'free_casual_days':       free_casual_days,
        'excess_casual_days':     excess_casual_days,
        'excess_working_days':    excess_working_days,
        'annual_deduction':       annual_deduction,
        'annual_remaining_before': balances.annual_remaining,
        'annual_remaining_after':  balances.annual_remaining - annual_deduction,
        'casual_remaining_before': balances.casual_calendar_days_remaining,
        'casual_remaining_after':  max(0, 7 - (balances.casual_calendar_days_used + calendar_days_requested))
                                   if leave_type == 'casual' else balances.casual_calendar_days_remaining,
    }

    if annual_deduction > 0:
        flash(f"ℹ️ This leave will deduct {annual_deduction} working days from your Annual Leave balance", "info")
    if free_casual_days > 0:
        flash(f"✅ {free_casual_days} calendar days will be covered by your Casual Leave allowance", "success")

    # ─── Build approval chain (role-aware) ────────────────────────────
    users_coll = current_app.users_collection
    chain     = build_approval_chain(applicant, role_bucket, users_coll)

    # ─── SO1 DOA final approval ───────────────────────────────────────
    director_doa = users_coll.find_one({"is_final_approver": {"$in": ["true", True]}})
    if not director_doa:
        flash("System error: Director DOA not found.", "error")
        return redirect(url_for(form_endpoint))

    # ─── Save application ─────────────────────────────────────────────
    applications_coll = current_app.applications_collection
    directorate       = applicant['directorate']
    reference_id      = generate_reference_id(directorate)
    while applications_coll.find_one({"referenceId": reference_id}):
        reference_id = generate_reference_id(directorate)

    app_status = "pending"
    reliever_status = None
    if role_bucket == 'civilian' and reliever_email:
        # Ensure selected reliever is not currently on leave/pass
        svc_nums_on_leave, emails_on_leave = get_staff_on_leave_identifiers(applications_coll)
        reliever_user = users_coll.find_one({"email": reliever_email}) if users_coll is not None else None
        reliever_svc = (reliever_user.get('service_number') or '').strip().upper() if reliever_user else ''
        if reliever_email in emails_on_leave or (reliever_svc and reliever_svc in svc_nums_on_leave):
            flash("The selected reliever is currently on leave/pass. Please select an available reliever.", "error")
            return redirect(url_for(form_endpoint))

        app_status = "pending"
        reliever_status = "accepted"

    application = {
        "referenceId":   reference_id,
        "applicantName": applicant['fullName'],
        "applicantId":   applicant['service_number'],
        "rankOrGrade":   applicant['rankOrGrade'],
        "designation":     applicant['designation'],
        "role_bucket":   role_bucket,           # stored for reference
        "leave_type":    leave_type,
        "directorate":   directorate,
        "status":        app_status,
        "reliever_email": reliever_email or None,
        "reliever_status": reliever_status,
        "approvalChain": chain,
        "finalApproval": {
            "approverId": "",
            "status": "pending",
            "comments": None,
            "timestamp": None,
            "receipt": {
                "receiptNumber": None,
                "issuedDate": None,
                "pdfUrl": None
            }
        },
        "createdAt":  datetime.utcnow(),
        "updatedAt":  datetime.utcnow(),
        "dates": {
            "applicationDate": start_date,
            "effectiveDate":   effective_date,
            "endDate":         end_date
        },
        "startDate":     start_date,
        "endDate":       end_date,
        "effectiveDate": effective_date,
        "numberOfDays":  calendar_days_requested if leave_type == 'casual' else working_days_requested,
        "reason":           reasons,
        "placeIntended":    place_intended,
        "contactAddress":   contact_address,
        "telephone":        telephone,
        "name_of_reliever": name_of_reliever,
        "director_reliever_service_number": director_reliever_service_number,
        "director_reliever_rank": director_reliever_rank,
        "director_reliever_name": director_reliever_name,
        "director_reliever_telephone": director_reliever_telephone,
        "previous_leave_pass_date": previous_leave_pass_date,
        "appt_of_reliever": appt_of_reliever,
        "attachments":      attachments,
        "tacosDetails": {
            "hasMedicalCertificate": has_medical_certificate,
            "hospitalized":          hospitalized,
            "firstHospitalization":  first_hospitalization,
            "outsideNigeria":        outside_nigeria,
            "expectedDeliveryDate":  expected_delivery_date,
            "attachmentMonths":      attachment_months,
            "calendarDays":          calendar_days_requested,
        },
        "validation": {
            "isValid":           is_valid,
            "validationMessage": message,
            "metadata":          metadata or {},
            "validatedAt":       datetime.utcnow()
        },
        "leaveBalances": {
            "annualRemaining":    balances.annual_remaining,
            "compassionateUsed":  balances.compassionate_used,
            "casualCalendarDays": balances.casual_calendar_days_used,
            "sickThisYear":       balances.sick_this_year,
            "sickRolling12m":     balances.sick_rolling_12m,
            "terminalGranted":    balances.terminal_granted,
        },
        "tacosCompliance": None,
        "notifications":   [],
        "auditTrail":      [],
        "leave_pass_returned": {
            "returned_from_leave": False,
            "actual_returned_date": None,
            "expected_returned_date": end_date + timedelta(days=1) if isinstance(end_date, datetime) else None,
            "returnedresult": None,
            "returned_status": "not_submitted"
        },
    }

    try:
        result        = applications_coll.insert_one(application)
        if app_status == "awaiting_reliever" and reliever_email:
            notifications_coll = current_app.notifications_collection
            notifications_coll.insert_one({
                "type": "reliever_request",
                "applicationId": result.inserted_id,
                "referenceId": reference_id,
                "target": {
                    "type": "user",
                    "userId": reliever_email
                },
                "message": f"{applicant['fullName']} has requested you to stand as a reliever for their leave application.",
                "status": "unread",
                "readBy": [],
                "meta": {
                    "applicantId": applicant['service_number'],
                    "applicantName": applicant['fullName'],
                    "createdAt": datetime.utcnow()
                },
                "createdAt": datetime.utcnow(),
                "isActive": True,
                "is_active": True
            })
            
            # Emit Socket.IO notification to reliever
            try:
                payload = {
                    "type": "reliever_request",
                    "applicationId": str(result.inserted_id),
                    "referenceId": reference_id,
                    "message": f"{applicant['fullName']} has requested you to stand as a reliever for their leave application.",
                    "createdAt": datetime.utcnow().isoformat(),
                    "acceptUrl": f"/reliever-action/{str(result.inserted_id)}/accept",
                    "declineUrl": f"/reliever-action/{str(result.inserted_id)}/decline"
                }
                socketio.emit("new_notification", payload, room=f"USER_{reliever_email.lower().strip()}")
            except Exception as e:
                print(f"[SOCKET] Error emitting reliever request notification: {e}")
        else:
            first_pending = next((s for s in chain if s['status'] == 'pending'), None)
            if first_pending:
                notify_pending_approval(application, first_pending, applicant)
                target_email = first_pending.get("approverEmail")
                if not target_email and users_coll is not None:
                    target_u = users_coll.find_one({"service_number": first_pending.get("approverId"), "role": first_pending.get("role")}) or users_coll.find_one({"role": first_pending.get("role")})
                    if target_u:
                        target_email = (target_u.get("email") or "").strip().lower()

                notifications_coll = current_app.notifications_collection
                notifications_coll.insert_one({
                    "type":          "action_required",
                    "applicationId": result.inserted_id,
                    "referenceId":   reference_id,
                    "target":        {
                        "type":   "user",
                        "userId": target_email or first_pending.get("approverId"),
                        "email":  target_email,
                        "role":   first_pending.get("role")
                    },
                    "message":       f"Application {reference_id} from {applicant.get('fullName')} is awaiting your approval.",
                    "status":        "unread",
                    "readBy":        [],
                    "meta": {
                        "triggeredBy":     applicant.get("service_number"),
                        "triggeredByName": applicant.get("fullName"),
                        "role":            first_pending.get("role")
                    },
                    "createdAt": datetime.utcnow(),
                    "isActive":  True,
                })

        flash(f"Application submitted successfully! Reference ID: {reference_id}", "success")
        session['last_application_id'] = str(result.inserted_id)
        session['last_reference_id']   = reference_id

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                "success": True,
                "referenceId": reference_id,
                "applicationId": str(result.inserted_id)
            })

        return redirect(url_for('application_success_routes.application_success',
                                ref_id=reference_id))

    except Exception as e:
        for att in attachments:
            try:
                current_app.fs.delete(ObjectId(att["gridfs_id"]))
            except:
                pass
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                "success": False,
                "error": f"Error saving application: {str(e)}"
            })

        flash(f"Error saving application: {str(e)}", "error")
        return redirect(url_for(form_endpoint))




def build_approval_chain(applicant, role_bucket, users_coll):
    chain = []
    sn          = applicant.get('service_number', '')
    directorate = applicant.get('directorate', '')

    # Fetch fresh applicant user doc to be 100% certain of category, role and approver flags
    user_doc = None
    if applicant.get('email'):
        user_doc = users_coll.find_one({"email": applicant.get('email')})
    if not user_doc and sn:
        user_doc = users_coll.find_one({
            "service_number": sn,
            "is_approval_role": {"$ne": True}
        })
    if not user_doc and sn:
        user_doc = users_coll.find_one({"service_number": sn}) or (users_coll.find_one({"email": sn}) if sn else None)
    user_doc = user_doc or {}

    category = (applicant.get('category') or user_doc.get('category') or '').lower().strip()
    raw_role = applicant.get('role') or user_doc.get('role') or ''
    if isinstance(raw_role, list):
        roles_list = [r.lower().strip() for r in raw_role if isinstance(r, str)]
        primary_role = roles_list[0] if roles_list else ''
    else:
        primary_role = str(raw_role).lower().strip()
        roles_list = [primary_role]

    is_so_applicant = (applicant.get('is_so_approver') == True) or (user_doc.get('is_so_approver') in (True, "true", "True")) or ('so' in roles_list)
    is_ad_applicant = (applicant.get('is_ad_approver') == True) or (user_doc.get('is_ad_approver') in (True, "true", "True")) or ('ad' in roles_list)
    is_dd_applicant = (applicant.get('is_dd_approver') == True) or (user_doc.get('is_dd_approver') in (True, "true", "True")) or ('dd' in roles_list)
    is_director_applicant = ('director' in roles_list) or (role_bucket == 'director')

    def create_step(role, approver, is_final_approver=False, registry_type=None):
        if not approver:
            return None
        step = {
            "role":                role,
            "approverId":          approver.get('service_number'),
            "approverEmail":       (approver.get('email') or '').strip().lower(),
            "approverName":        approver.get('name'),
            "approverRank":        approver.get('rankOrGrade'),
            "approverDesignation": approver.get('appt'),
            "status":              "pending",
            "comments":            "",
            "timestamp":           None,
        }
        if role == "so" or approver.get("is_so_approver"):
            step["is_so_approver"] = True
        if role == "ad" or approver.get("is_ad_approver"):
            step["is_ad_approver"] = True
        if role == "dd" or approver.get("is_dd_approver"):
            step["is_dd_approver"] = True
        if is_final_approver:
            step["is_final_approver"] = True
        if registry_type:
            step["registry_type"] = registry_type
        return step

    def add_registry_step(approver, registry_type="directorate"):
        """Directorate registry only — physical file acknowledgement."""
        step = create_step("registry", approver, registry_type=registry_type)
        if step:
            step["acknowledged"]   = False
            step["acknowledgedAt"] = None
        return step

    def add_central_registry_step(approver):
        """Central registry (CDSA) — physical file acknowledgement."""
        step = create_step("central_registry", approver, registry_type="central")
        if step:
            step["acknowledged"]   = False
            step["acknowledgedAt"] = None
        return step

    def get_central_registry():
        """CDSA central registry — acknowledges receipts for non-civilian forms."""
        central = users_coll.find_one({
            "directorate": "CDSA",
            "role": "central_registry"
        })
        if not central:
            central = users_coll.find_one({
                "directorate": "CDSA",
                "role": "registry"
            })
        if not central:
            central = users_coll.find_one({
                "is_cdsa_approver": {"$in": ["true", True]}
            })
        return central

    # Identify Director DOA (the final approver)
    director_doa = users_coll.find_one({"is_final_approver": {"$in": ["true", True]}})

    # Identify Local Director for applicant's directorate
    local_director = users_coll.find_one({
        "directorate": directorate,
        "role": "director",
        "service_number": {"$ne": sn}
    })

    # Check if applicant is in same directorate as final approver
    is_same_directorate_as_final = False
    if local_director and director_doa and local_director.get("service_number") == director_doa.get("service_number"):
        is_same_directorate_as_final = True
    elif director_doa and directorate and directorate.strip().upper() == (director_doa.get("directorate") or "DOA").strip().upper():
        is_same_directorate_as_final = True

    def add_director_approval_steps():
        if is_same_directorate_as_final:
            step = create_step("director", director_doa or local_director, is_final_approver=True, registry_type="director_doa")
            if step:
                chain.append(step)
            else:
                flash(f"Warning: No Director found in {directorate}", "warning")
        else:
            # Step: Local director
            step_local = create_step("director", local_director)
            if step_local:
                chain.append(step_local)
            else:
                flash(f"Warning: No Director found in {directorate}", "warning")

            # Step: Final approver (Director DOA)
            if director_doa:
                step_final = create_step("director", director_doa, is_final_approver=True, registry_type="director_doa")
                chain.append(step_final)
            else:
                flash("Warning: Director-DOA not found", "warning")

    # ─── 1. CIVILIAN (category == 'civilian') ─────────────────────────
    if category == 'civilian' or role_bucket == 'civilian' or 'civilian' in roles_list or 'civilian_head_cao' in roles_list:
        # 1. Civilian head
        target_role = "civilian_head_cao"
        target_deputy = "deputy_civilian_head_cao"

        applicant_email = (applicant.get('email') or user_doc.get('email') or '').strip().lower()
        applicant_role = (applicant.get('role') or user_doc.get('role') or '').strip().lower()

        is_civ_head_applicant = (
            applicant_role in (target_role, "civilian_head")
            or (applicant_email and applicant_email == "civilianhead.cao@dsa.mil.ng")
            or (target_role in roles_list and "civilian" not in roles_list)
        )

        if not is_civ_head_applicant:
            # Regular civilian applying: Civilian Head CAO is ALWAYS the 1st approver
            civ_head = users_coll.find_one({
                "role": {"$in": [target_role, "civilian_head"]}
            })
            if not civ_head:
                civ_head = users_coll.find_one({
                    "appt": {"$regex": "Head.*Civilian", "$options": "i"}
                })
            if not civ_head:
                civ_head = users_coll.find_one({
                    "role": target_deputy
                })
            step = create_step(target_role, civ_head)
            if step:
                chain.append(step)
            else:
                flash("Warning: No Civilian Head found in DOA", "warning")
        else:
            # Civilian Head himself applying: route to deputy or skip to SO
            deputy = users_coll.find_one({
                "role": target_deputy,
                "email": {"$ne": applicant_email}
            })
            if deputy:
                step = create_step(target_deputy, deputy)
                if step:
                    chain.append(step)

        # 2. SO Approver
        so = users_coll.find_one({
            "directorate": directorate,
            "role": "ad",
            "is_so_approver": True,
            "service_number": {"$ne": sn}
        })
        if not so:
            so = users_coll.find_one({
                "directorate": directorate,
                "is_so_approver": True,
                "service_number": {"$ne": sn}
            })
        if not so:
            so = users_coll.find_one({
                "directorate": directorate,
                "role": "ad",
                "service_number": {"$ne": sn}
            })
        step = create_step("so", so)
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No SO approver found in {directorate}", "warning")

        # 3. DD Approver
        dd = users_coll.find_one({
            "directorate": directorate,
            "role": "dd",
            "is_dd_approver": True,
            "service_number": {"$ne": sn}
        })
        if not dd:
            dd = users_coll.find_one({
                "directorate": directorate,
                "role": "dd",
                "service_number": {"$ne": sn}
            })
        step = create_step("dd", dd)
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Deputy Director found in {directorate}", "warning")

        # 4 & 5. Director (same directorate auto-issues receipt; cross-directorate: local director then director_doa)
        add_director_approval_steps()

        # 6. Registries (applicant directorate, and if cross-directorate, issuing DOA directorate)
        dir_registry = users_coll.find_one({
            "directorate": directorate,
            "role": "registry"
        })
        step = add_registry_step(dir_registry, registry_type="directorate")
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Registry found in {directorate}", "warning")

        if not is_same_directorate_as_final:
            doa_registry = users_coll.find_one({
                "directorate": director_doa.get("directorate", "DOA") if director_doa else "DOA",
                "role": "registry"
            })
            if doa_registry and (not dir_registry or doa_registry.get("service_number") != dir_registry.get("service_number")):
                step_doa_reg = add_registry_step(doa_registry, registry_type="issuing_directorate")
                if step_doa_reg:
                    chain.append(step_doa_reg)

    # ─── 5. MILITARY: DIRECTOR (role == 'director') ───────────────────
    elif is_director_applicant:
        # 1. role == cdsa
        cdsa = users_coll.find_one({
            "role": "cdsa",
            "service_number": {"$ne": sn}
        })
        if not cdsa:
            cdsa = users_coll.find_one({
                "is_cdsa_approver": {"$in": ["true", True]},
                "service_number": {"$ne": sn}
            })
        step = create_step("cdsa", cdsa)
        if step:
            chain.append(step)
        else:
            flash("Warning: CDSA approver not found", "warning")

        # 6. role == registry (applicant directorate)
        dir_registry = users_coll.find_one({
            "directorate": directorate,
            "role": "registry"
        })
        step = add_registry_step(dir_registry, registry_type="directorate")
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Registry found in {directorate}", "warning")

        # 7. role == central_registry
        central = get_central_registry()
        if central:
            chain.append(add_central_registry_step(central))
        else:
            flash("Warning: No Central Registry (CDSA) found", "warning")

    # ─── 4. MILITARY: DD (role == 'dd') ───────────────────────────────
    elif is_dd_applicant:
        # 1 & 5. Director (same directorate vs cross-directorate)
        add_director_approval_steps()

        # 6. role == registry (applicant directorate)
        dir_registry = users_coll.find_one({
            "directorate": directorate,
            "role": "registry"
        })
        step = add_registry_step(dir_registry, registry_type="directorate")
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Registry found in {directorate}", "warning")

        # 7. role == central_registry
        central = get_central_registry()
        if central:
            chain.append(add_central_registry_step(central))
        else:
            flash("Warning: No Central Registry (CDSA) found", "warning")

    # ─── 2. MILITARY: PERSONNEL (role == 'personnel') ─────────────────
    elif 'personnel' in roles_list or role_bucket == 'personnel':
        # 1. is_so_approver == true
        so = users_coll.find_one({
            "directorate": directorate,
            "role": "ad",
            "is_so_approver": True,
            "service_number": {"$ne": sn}
        })
        if not so:
            so = users_coll.find_one({
                "directorate": directorate,
                "is_so_approver": True,
                "service_number": {"$ne": sn}
            })
        if not so:
            so = users_coll.find_one({
                "directorate": directorate,
                "role": "ad",
                "service_number": {"$ne": sn}
            })
        step = create_step("so", so)
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No SO approver found in {directorate}", "warning")

        # 2. is_ad_approver == true
        ad = users_coll.find_one({
            "directorate": directorate,
            "role": "ad",
            "is_ad_approver": True,
            "service_number": {"$ne": sn}
        })
        if not ad:
            ad = users_coll.find_one({
                "directorate": directorate,
                "role": "ad",
                "service_number": {"$ne": sn}
            })
        step = create_step("ad", ad)
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No AD approver found in {directorate}", "warning")

        # 3. is_dd_approver == true
        dd = users_coll.find_one({
            "directorate": directorate,
            "role": "dd",
            "is_dd_approver": True,
            "service_number": {"$ne": sn}
        })
        if not dd:
            dd = users_coll.find_one({
                "directorate": directorate,
                "role": "dd",
                "service_number": {"$ne": sn}
            })
        step = create_step("dd", dd)
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Deputy Director found in {directorate}", "warning")

        # 4 & 5. Director (same vs cross)
        add_director_approval_steps()

        # 6. role == registry (applicant directorate)
        dir_registry = users_coll.find_one({
            "directorate": directorate,
            "role": "registry"
        })
        step = add_registry_step(dir_registry, registry_type="directorate")
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Registry found in {directorate}", "warning")

        # 7. role == central_registry
        central = get_central_registry()
        if central:
            chain.append(add_central_registry_step(central))
        else:
            flash("Warning: No Central Registry (CDSA) found", "warning")

    # ─── 3. MILITARY: OFFICER (role == 'officer') ─────────────────────
    else:
        # Commissioned officers skip SO!
        # 1. is_ad_approver == true (skip if applicant is AD)
        if not is_ad_applicant:
            ad = users_coll.find_one({
                "directorate": directorate,
                "role": "ad",
                "is_ad_approver": True,
                "service_number": {"$ne": sn}
            })
            if not ad:
                ad = users_coll.find_one({
                    "directorate": directorate,
                    "role": "ad",
                    "service_number": {"$ne": sn}
                })
            step = create_step("ad", ad)
            if step:
                chain.append(step)
            else:
                flash(f"Warning: No AD approver found in {directorate}", "warning")

        # 2. is_dd_approver == true (skip if applicant is DD)
        if not is_dd_applicant:
            dd = users_coll.find_one({
                "directorate": directorate,
                "role": "dd",
                "is_dd_approver": True,
                "service_number": {"$ne": sn}
            })
            if not dd:
                dd = users_coll.find_one({
                    "directorate": directorate,
                    "role": "dd",
                    "service_number": {"$ne": sn}
                })
            step = create_step("dd", dd)
            if step:
                chain.append(step)
            else:
                flash(f"Warning: No Deputy Director found in {directorate}", "warning")

        # 4 & 5. Director (same vs cross)
        add_director_approval_steps()

        # 6. role == registry (applicant directorate)
        dir_registry = users_coll.find_one({
            "directorate": directorate,
            "role": "registry"
        })
        step = add_registry_step(dir_registry, registry_type="directorate")
        if step:
            chain.append(step)
        else:
            flash(f"Warning: No Registry found in {directorate}", "warning")

        # 7. role == central_registry
        central = get_central_registry()
        if central:
            chain.append(add_central_registry_step(central))
        else:
            flash("Warning: No Central Registry (CDSA) found", "warning")

    return chain




@application_routes.route('/calculate_working_days', methods=['POST'])
def calculate_working_days():
    """API endpoint to calculate working days between two dates."""
    data = request.get_json(silent=True) or {}
    
    start_date_str = data.get('start_date')
    end_date_str = data.get('end_date')
    leave_type = data.get('leave_type', 'annual')
    
    if not start_date_str or not end_date_str:
        return jsonify({'error': 'Start and end dates required'}), 400
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    if start_date > end_date:
        return jsonify({'error': 'Start date must be before end date'}), 400
    
    # Get public holidays for the year
    year = start_date.year
    public_holidays = get_public_holidays(year)
    
    # Calculate based on leave type
    if leave_type == 'casual':
        # Casual leave uses calendar days
        days = calendar_days_between(start_date, end_date)
        return jsonify({
            'days': days,
            'type': 'calendar',
            'message': f'{days} calendar days (includes weekends)'
        })
    else:
        # Other leaves use working days
        days = working_days_between(start_date, end_date, public_holidays)
        
        # Get holiday names for display
        holiday_names = []
        for holiday_date in public_holidays:
            if start_date <= holiday_date <= end_date:
                # Get holiday name from holidays library
                nigeria_holidays = holidays.country_holidays('NG', years=year)
                name = nigeria_holidays.get(holiday_date.date(), 'Public Holiday')
                holiday_names.append({
                    'date': holiday_date.strftime('%Y-%m-%d'),
                    'name': name
                })
        
        return jsonify({
            'days': days,
            'type': 'working',
            'holidays_excluded': holiday_names,
            'message': f'{days} working days (excludes weekends and {len(holiday_names)} public holidays)'
        })


@application_routes.route('/reliever-action/<string:app_id>/accept', methods=['POST'])
def reliever_action_accept(app_id):
    if 'user_email' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    collections = get_db_collections()
    apps_coll = collections.get('applications')
    users_coll = collections.get('users')
    notifications_coll = current_app.notifications_collection

    try:
        app_doc = apps_coll.find_one({"_id": ObjectId(app_id)})
        if not app_doc:
            flash("Application not found.", "error")
            return redirect(url_for('dashboard'))

        # Security check: verify this user is the requested reliever
        current_email = session['user_email'].strip().lower()
        if app_doc.get("reliever_email", "").strip().lower() != current_email:
            flash("Unauthorized action.", "error")
            return redirect(url_for('dashboard'))

        # Fetch reliever's details to append
        reliever_user = users_coll.find_one({"email": current_email})
        if not reliever_user:
            flash("Reliever profile not found in database.", "error")
            return redirect(url_for('dashboard'))

        reliever_name = reliever_user.get("name", "")
        reliever_appt = reliever_user.get("appt") or reliever_user.get("title", "")

        # Update application status
        apps_coll.update_one(
            {"_id": ObjectId(app_id)},
            {
                "$set": {
                    "status": "pending",
                    "reliever_status": "accepted",
                    "name_of_reliever": reliever_name,
                    "appt_of_reliever": reliever_appt,
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        # Mark reliever notifications as read
        if notifications_coll is not None:
            notifications_coll.update_many(
                {"applicationId": ObjectId(app_id), "target.userId": current_email},
                {"$set": {"status": "read", "is_active": False}}
            )

        # Store notification for applicant that reliever has accepted
        if notifications_coll is not None:
            notifications_coll.insert_one({
                "type": "reliever_accepted",
                "applicationId": ObjectId(app_id),
                "referenceId": app_doc.get("referenceId"),
                "target": {
                    "type": "user",
                    "userId": app_doc.get("applicantId")
                },
                "message": f"{reliever_name} has accepted to stand as your reliever for application {app_doc.get('referenceId')}.",
                "status": "unread",
                "isActive": True,
                "is_active": True,
                "createdAt": datetime.utcnow()
            })
            
            # Emit Socket.IO notification to applicant
            try:
                applicant_id = app_doc.get("applicantId")
                if "@" in str(applicant_id):
                    applicant_room = f"USER_{applicant_id.lower().strip()}"
                else:
                    safe_applicant_id = str(applicant_id).replace("/", "_")
                    applicant_room = f"USER_{safe_applicant_id}"
                
                payload = {
                    "type": "reliever_accepted",
                    "applicationId": str(app_doc["_id"]),
                    "referenceId": app_doc.get("referenceId"),
                    "message": f"{reliever_name} has accepted to stand as your reliever for application {app_doc.get('referenceId')}.",
                    "createdAt": datetime.utcnow().isoformat()
                }
                socketio.emit("new_notification", payload, room=applicant_room)
            except Exception as e:
                print(f"[SOCKET] Error emitting reliever accepted notification: {e}")

        # Reload updated doc to pass to notification builder
        app_doc = apps_coll.find_one({"_id": ObjectId(app_id)})

        # Trigger notification to the first approver in the chain
        chain = app_doc.get("approvalChain", [])
        first_pending = next((s for s in chain if s['status'] == 'pending'), None)
        if first_pending:
            # Reconstruct applicant object
            applicant_user = {
                "fullName": app_doc.get("applicantName"),
                "service_number": app_doc.get("applicantId"),
                "directorate": app_doc.get("directorate")
            }
            notify_pending_approval(app_doc, first_pending, applicant_user)
            if notifications_coll is not None:
                target_email = first_pending.get("approverEmail")
                if not target_email and current_app.users_collection is not None:
                    target_u = current_app.users_collection.find_one({"service_number": first_pending.get("approverId"), "role": first_pending.get("role")}) or current_app.users_collection.find_one({"role": first_pending.get("role")})
                    if target_u:
                        target_email = (target_u.get("email") or "").strip().lower()

                notifications_coll.insert_one({
                    "type":          "action_required",
                    "applicationId": app_doc["_id"],
                    "referenceId":   app_doc.get("referenceId"),
                    "target":        {
                        "type":   "user",
                        "userId": target_email or first_pending.get("approverId"),
                        "email":  target_email,
                        "role":   first_pending.get("role")
                    },
                    "message":       f"Application {app_doc.get('referenceId')} from {app_doc.get('applicantName')} is awaiting your approval.",
                    "status":        "unread",
                    "readBy":        [],
                    "meta": {
                        "triggeredBy":     app_doc.get("applicantId"),
                        "triggeredByName": app_doc.get("applicantName"),
                        "role":            first_pending.get("role")
                    },
                    "createdAt": datetime.utcnow(),
                    "isActive":  True,
                })

        flash("Reliever request accepted successfully! The application has now been submitted for approval.", "success")
    except Exception as e:
        flash(f"Error accepting reliever request: {str(e)}", "error")

    return redirect(url_for('dashboard'))


@application_routes.route('/reliever-action/<string:app_id>/decline', methods=['POST'])
def reliever_action_decline(app_id):
    if 'user_email' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    collections = get_db_collections()
    apps_coll = collections.get('applications')
    notifications_coll = current_app.notifications_collection

    try:
        app_doc = apps_coll.find_one({"_id": ObjectId(app_id)})
        if not app_doc:
            flash("Application not found.", "error")
            return redirect(url_for('dashboard'))

        # Security check: verify this user is the requested reliever
        current_email = session['user_email'].strip().lower()
        if app_doc.get("reliever_email", "").strip().lower() != current_email:
            flash("Unauthorized action.", "error")
            return redirect(url_for('dashboard'))

        # Update application status
        apps_coll.update_one(
            {"_id": ObjectId(app_id)},
            {
                "$set": {
                    "status": "declined_by_reliever",
                    "reliever_status": "declined",
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        # Mark reliever notifications as read
        if notifications_coll is not None:
            notifications_coll.update_many(
                {"applicationId": ObjectId(app_id), "target.userId": current_email},
                {"$set": {"status": "read", "is_active": False}}
            )

        # Store notification for applicant that reliever has declined
        if notifications_coll is not None:
            reliever_display_name = session.get('name', 'Your reliever')
            notifications_coll.insert_one({
                "type": "reliever_declined",
                "applicationId": ObjectId(app_id),
                "referenceId": app_doc.get("referenceId"),
                "target": {
                    "type": "user",
                    "userId": app_doc.get("applicantId")
                },
                "message": f"{reliever_display_name} has declined to stand as your reliever for application {app_doc.get('referenceId')}.",
                "status": "unread",
                "isActive": True,
                "is_active": True,
                "createdAt": datetime.utcnow()
            })
            
            # Emit Socket.IO notification to applicant
            try:
                applicant_id = app_doc.get("applicantId")
                if "@" in str(applicant_id):
                    applicant_room = f"USER_{applicant_id.lower().strip()}"
                else:
                    safe_applicant_id = str(applicant_id).replace("/", "_")
                    applicant_room = f"USER_{safe_applicant_id}"
                
                payload = {
                    "type": "reliever_declined",
                    "applicationId": str(app_doc["_id"]),
                    "referenceId": app_doc.get("referenceId"),
                    "message": f"{reliever_display_name} has declined to stand as your reliever for application {app_doc.get('referenceId')}.",
                    "createdAt": datetime.utcnow().isoformat()
                }
                socketio.emit("new_notification", payload, room=applicant_room)
            except Exception as e:
                print(f"[SOCKET] Error emitting reliever declined notification: {e}")

        flash("Reliever request declined.", "info")
    except Exception as e:
        flash(f"Error declining reliever request: {str(e)}", "error")

    return redirect(url_for('dashboard'))


@application_routes.route('/api/reliever-request/send', methods=['POST'])
def api_reliever_request_send():
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json(silent=True) or {}
    reliever_email = data.get("reliever_email", "").strip().lower()
    if not reliever_email:
        return jsonify({"error": "Reliever email required"}), 400

    applicant = session.get('applicant')
    if not applicant:
        return jsonify({"error": "Applicant session expired"}), 400

    collections = get_db_collections()
    users_coll = collections.get('users')
    apps_coll = collections.get('applications')
    notifications_coll = current_app.notifications_collection

    # Verify reliever exists
    reliever = users_coll.find_one({"email": reliever_email, "is_active": True})
    if not reliever:
        return jsonify({"error": "Reliever not found or inactive"}), 404

    # Verify reliever is not currently on any leave/pass type
    svc_nums_on_leave, emails_on_leave = get_staff_on_leave_identifiers(apps_coll)
    reliever_svc = str(reliever.get('service_number') or '').strip().upper()
    if reliever_email in emails_on_leave or (reliever_svc and reliever_svc in svc_nums_on_leave):
        return jsonify({"error": f"{reliever.get('name', 'The selected staff')} is currently on leave/pass and cannot stand as a reliever."}), 400

    # Ensure role and category requirements for civilian application
    applicant_bucket = applicant.get('role_bucket', 'civilian')
    if applicant_bucket == 'civilian':
        r_cat = str(reliever.get('category') or '').strip().lower()
        r_role = str(reliever.get('role') or '').strip().lower()
        if r_cat != 'civilian' or (r_role != 'civilian' and r_role != 'civilian_head_cao'):
            return jsonify({"error": "Only civilian staff can stand as a reliever for this application."}), 400

    # Create request doc in reliever_requests collection
    reliever_requests_coll = users_coll.database["reliever_requests"]
    
    # Clean up previous pending/declined requests for this applicant-reliever pair
    reliever_requests_coll.delete_many({
        "applicantId": applicant['service_number'],
        "relieverEmail": reliever_email
    })

    request_doc = {
        "applicantId": applicant['service_number'],
        "applicantName": applicant['fullName'],
        "relieverEmail": reliever_email,
        "status": "pending",
        "createdAt": datetime.utcnow()
    }
    reliever_requests_coll.insert_one(request_doc)
    request_id = str(request_doc["_id"])

    # Create notification for the reliever
    if notifications_coll is not None:
        notifications_coll.insert_one({
            "type": "reliever_request_realtime",
            "relieverRequestId": request_doc["_id"],
            "applicationId": ObjectId(),
            "target": {
                "type": "user",
                "userId": reliever_email
            },
            "message": f"{applicant['fullName']} has requested you to stand as a reliever for their leave application.",
            "status": "unread",
            "readBy": [],
            "meta": {
                "applicantId": applicant['service_number'],
                "applicantName": applicant['fullName'],
                "createdAt": datetime.utcnow()
            },
            "createdAt": datetime.utcnow(),
            "isActive": True,
            "is_active": True
        })

    # Emit Socket.IO notification to reliever
    try:
        payload = {
            "type": "reliever_request",
            "applicationId": request_id,
            "message": f"{applicant['fullName']} has requested you to stand as a reliever for their leave application.",
            "createdAt": datetime.utcnow().isoformat(),
            "acceptUrl": f"/api/reliever-request/action/{request_id}/accept",
            "declineUrl": f"/api/reliever-request/action/{request_id}/decline"
        }
        socketio.emit("new_notification", payload, room=f"USER_{reliever_email.lower().strip()}")
    except Exception as e:
        print(f"[SOCKET] Error emitting API reliever request notification: {e}")

    return jsonify({"success": True, "request_id": request_id})


@application_routes.route('/api/reliever-request/status/<string:request_id>', methods=['GET'])
def api_reliever_request_status(request_id):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    collections = get_db_collections()
    users_coll = collections.get('users')
    reliever_requests_coll = users_coll.database["reliever_requests"]

    try:
        req = reliever_requests_coll.find_one({"_id": ObjectId(request_id)})
        if not req:
            return jsonify({"status": "not_found"}), 404
        
        status = req.get("status", "pending")
        reliever_name = ""
        reliever_appt = ""

        if status == "accepted":
            # Retrieve reliever details
            reliever = users_coll.find_one({"email": req.get("relieverEmail")})
            if reliever:
                reliever_name = reliever.get("name", "")
                reliever_appt = reliever.get("appt") or reliever.get("title", "")

        return jsonify({
            "status": status,
            "reliever_name": reliever_name,
            "reliever_appt": reliever_appt
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@application_routes.route('/api/reliever-request/action/<string:request_id>/<string:action>', methods=['POST'])
def api_reliever_request_action(request_id, action):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    if action not in ["accept", "decline"]:
        return jsonify({"error": "Invalid action"}), 400

    collections = get_db_collections()
    users_coll = collections.get('users')
    reliever_requests_coll = users_coll.database["reliever_requests"]
    notifications_coll = current_app.notifications_collection

    try:
        req = reliever_requests_coll.find_one({"_id": ObjectId(request_id)})
        if not req:
            return jsonify({"error": "Request not found"}), 404

        # Security check: verify this user is the requested reliever
        current_email = session['user_email'].strip().lower()
        if req.get("relieverEmail", "").strip().lower() != current_email:
            return jsonify({"error": "Unauthorized"}), 403

        status_val = "accepted" if action == "accept" else "declined"
        reliever_requests_coll.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": status_val}}
        )

        # Mark reliever notifications as read
        if notifications_coll is not None:
            notifications_coll.update_many(
                {"relieverRequestId": ObjectId(request_id), "target.userId": current_email},
                {"$set": {"status": "read", "is_active": False}}
            )

        if request.headers.get('Content-Type') == 'application/json' or request.is_json:
            return jsonify({"success": True, "status": status_val})
        else:
            flash(f"Reliever request {status_val}ed successfully.", "success")
            return redirect(url_for('dashboard'))
    except Exception as e:
        if request.headers.get('Content-Type') == 'application/json' or request.is_json:
            return jsonify({"error": str(e)}), 500
        else:
            flash(f"Error: {str(e)}", "error")
            return redirect(url_for('dashboard'))
