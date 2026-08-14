from flask import request, render_template, redirect, flash, url_for, Blueprint, current_app, session

verify_service_number_routes = Blueprint('verify_service_number_routes', __name__)


def resolve_role_bucket(user: dict) -> str:
    """
    Reads designation field to determine form routing.
    Works whether the record comes from staff_collection or user_collection.
    """
    category = user.get('category', '').lower().strip()
    role = user.get('role', '')

    # Normalize roles to a list of lowercased strings
    roles_list = []
    if isinstance(role, str):
        roles_list = [role.lower().strip()]
    elif isinstance(role, list):
        roles_list = [r.lower().strip() for r in role if isinstance(r, str)]

    # Civilian check
    if category == 'civilian' or 'civilian' in roles_list or 'civilian_head_cao' in roles_list or 'civilian_head' in roles_list:
        return 'civilian'

    # Check deputy director before director — order matters
    if 'dd' in roles_list or 'deputy_director' in roles_list:
        return 'deputy_director'
    
    if 'director' in roles_list:
        return 'director'

    # Fallback to officer for AD, officer, personnel, civilian_head_cao, registry, etc.
    return 'officer'


@verify_service_number_routes.route('/apply_id', methods=['GET'])
def apply_id():
    return render_template('apply_id.html')


@verify_service_number_routes.route('/service_number_verification', methods=['POST'])
def service_number_verification():
    service_number = request.form.get('service_number', '').strip()
    if not service_number:
        flash('Please provide a valid Service Number', 'error')
        return redirect(url_for('verify_service_number_routes.apply_id'))
    
    users_coll = current_app.users_collection
    
    # Look up by service number or email prioritizing personal accounts
    staff = users_coll.find_one({
        "is_approval_role": {"$ne": True},
        "$or": [
            {"service_number": service_number},
            {"email": service_number}
        ]
    })
    if not staff:
        # Fallback to any account if personal not found (e.g. legacy/testing accounts)
        staff = users_coll.find_one({
            "$or": [
                {"service_number": service_number},
                {"email": service_number}
            ]
        })
    
    if not staff:
        flash('Service number not found. Please check and try again.', 'error')
        return redirect(url_for('verify_service_number_routes.apply_id'))
        
    session['service_number'] = service_number
    role_bucket = resolve_role_bucket(staff)
    
    session['applicant'] = {
        'service_number': service_number,
        'fullName':       staff.get('name') or staff.get('fname'),
        'category':       staff.get('category', ''),
        'directorate':    staff.get('directorate', ''),
        'email':          staff.get('email', ''),
        'role_bucket':    role_bucket,
    }
    
    flash('Service number verified successfully.', 'success')
    
    ROLE_ENDPOINTS = {
        'civilian':        'application_routes.application_form',
        'officer':         'application_routes.application_form_officer',
        'deputy_director': 'application_routes.application_form_dd',
        'director':        'application_routes.application_form_director',
    }
    
    endpoint = ROLE_ENDPOINTS.get(role_bucket, 'application_routes.application_form_officer')
    return redirect(url_for(endpoint))


@verify_service_number_routes.route('/application_form_check', methods=['GET', 'POST'])
def application_form_check():
    service_number = session.get('service_number', '')
    user_email = session.get('user_email', '')
    
    users_coll = current_app.users_collection
    
    staff = None
    if service_number:
        staff = users_coll.find_one({
            "is_approval_role": {"$ne": True},
            "$or": [
                {"service_number": service_number},
                {"email": service_number}
            ]
        })
        if not staff:
            staff = users_coll.find_one({
                "$or": [
                    {"service_number": service_number},
                    {"email": service_number}
                ]
            })
    if not staff and user_email:
        staff = users_coll.find_one({"email": user_email})
        if staff and not service_number:
            service_number = staff.get('service_number') or staff.get('email')
            session['service_number'] = service_number

    if not staff:
        flash('Please provide a valid Service Number', 'error')
        return redirect(url_for('verify_service_number_routes.apply_id'))

    role_bucket = resolve_role_bucket(staff)

    session['applicant'] = {
        'service_number': service_number,
        'fullName':       staff.get('name') or staff.get('fname'),
        'category':       staff.get('category', ''),
        'directorate':    staff.get('directorate', ''),
        'email':          staff.get('email', ''),
        'role_bucket':    role_bucket,
    }

    flash('Service number verified successfully.', 'success')

    ROLE_ENDPOINTS = {
        'civilian':        'application_routes.application_form',
        'officer':         'application_routes.application_form_officer',
        'deputy_director': 'application_routes.application_form_dd',
        'director':        'application_routes.application_form_director',
    }

    endpoint = ROLE_ENDPOINTS.get(role_bucket, 'application_routes.application_form_officer')
    return redirect(url_for(endpoint))




    