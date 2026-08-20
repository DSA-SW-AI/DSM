from flask import Blueprint, app, render_template, session, jsonify, make_response, flash, redirect, url_for, current_app, send_file, abort, request
from datetime import datetime, timedelta
from pymongo import MongoClient
from bson.objectid import ObjectId
from functools import wraps
# from flask_login import current_user
import gridfs
import io
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from modules.extensions import socketio
from flask_socketio import emit, join_room
from utils.sockets import handle_connect, handle_join_rooms, handle_join

from permissions import ROLE_PERMISSIONS 


approver_dashboard = Blueprint('approver_dashboard', __name__)

# ─────────────────────────────────────────────────────────────
# Functions to create notifications and emit sockets for leave application approvals
# ─────────────────────────────────────────────────────────────

def notify_pending_approval(app, next_step, current_user):
    """
    Emits Socket.IO notification to next approver(s) for Leave/Pass applications.

    The socket payload type is "leave_approval" so socket.js can distinguish
    it from parade notifications and show the correct modal on the correct dashboard.
    """
    users_coll = current_app.users_collection
    directorate = app.get("directorate")
    message = f"Application {app['referenceId']} is awaiting your approval."

    payload = {
        "type": "leave_approval",
        "_id": str(app["_id"]),
        "applicationId": str(app["_id"]),
        "triggeredBy": current_user.get("fullName", current_user.get("name")),
        "referenceId": app["referenceId"],
        "message": message,
        "role": next_step.get("role"),
        "leave_type": app.get("leave_type"),
        "directorate": directorate,
        "date": app.get("createdAt").strftime('%Y-%m-%d') if app.get("createdAt") else None,
    }

    approver_id = next_step.get("approverId")
    role = next_step.get("role")

    print(f"[SOCKET] Attempting to notify next approver for role: {role}")

    # If the step already has a bound approverId (which it always does from build_approval_chain)
    if approver_id:
        safe_id = approver_id.replace("/", "_")
        room = f"USER_{safe_id}"
        print(f"[SOCKET] Direct notification by approverId: {approver_id} to room: {room}")
        socketio.emit("new_notification", payload, room=room)
        return

    # Fallback dynamic lookup in case approverId is missing
    fallback_user = None
    if role == "so":
        fallback_user = users_coll.find_one({"directorate": directorate, "is_so_approver": True, "is_active": True})
    elif role == "ad":
        fallback_user = users_coll.find_one({"directorate": directorate, "is_ad_approver": True, "is_active": True})
    elif role == "dd":
        fallback_user = users_coll.find_one({"directorate": directorate, "is_dd_approver": True, "is_active": True})
    elif role == "director":
        if next_step.get("is_final_approver") in (True, "true", "True"):
            fallback_user = users_coll.find_one({"is_final_approver": {"$in": ["true", True]}, "is_active": True})
        else:
            fallback_user = users_coll.find_one({"directorate": directorate, "role": "director", "is_active": True})
    elif role == "central_registry":
        fallback_user = users_coll.find_one({"directorate": "CDSA", "role": "central_registry", "is_active": True})
        if not fallback_user:
            fallback_user = users_coll.find_one({"directorate": "CDSA", "role": "registry", "is_active": True})
        if not fallback_user:
            fallback_user = users_coll.find_one({"is_cdsa_approver": {"$in": ["true", True]}, "is_active": True})
    elif role in ("civilian_head_cao", "civilian_head"):
        target_deputy = "deputy_civilian_head_cao" if role == "civilian_head_cao" else "deputy_civilian_head"
        target_dir = "DOA" if role == "civilian_head_cao" else directorate
        fallback_user = users_coll.find_one({"role": role, "directorate": target_dir, "is_active": True}) or \
                        users_coll.find_one({"role": target_deputy, "directorate": target_dir, "is_active": True})

    if fallback_user and fallback_user.get("service_number"):
        fallback_id = fallback_user.get("service_number")
        safe_id = fallback_id.replace("/", "_")
        room = f"USER_{safe_id}"
        print(f"[SOCKET] Fallback notification to room: {room}")
        socketio.emit("new_notification", payload, room=room)
    else:
        print(f"[SOCKET] Warning: No approver found for role {role} and no approverId present.")



def _perform_leave_deduction(app, leave_balances_coll):
    """
    Performs leave balance deduction. Returns dict:
    {"success": bool, "message": str, "details": dict}
    Extracted so both Director-approve and CDSA-approve call the same logic.
    """
    from datetime import datetime, timedelta

    applicant_id   = app.get("applicantId")
    leave_type     = app.get("leave_type")
    days_requested = app.get("numberOfDays", 0)

    effective_date = app.get("effectiveDate")
    if isinstance(effective_date, dict) and "$date" in effective_date:
        from datetime import timezone
        effective_date = datetime.fromisoformat(
            effective_date["$date"].replace("Z", "+00:00")
        ).replace(tzinfo=None)
    year = effective_date.year if isinstance(effective_date, datetime) else datetime.now().year

    balance = leave_balances_coll.find_one({"serviceNumber": applicant_id, "year": year})
    if not balance:
        return {"success": False, "message": f"No leave balance for {applicant_id} in {year}", "details": {}}

    update_fields = {}
    details       = {}

    try:
        if leave_type == "annual":
            if balance.get("annualRemaining", 0) < days_requested:
                return {"success": False,
                        "message": f"Insufficient annual leave. Available: {balance.get('annualRemaining', 0)}, Requested: {days_requested}",
                        "details": {}}
            update_fields["annualRemaining"] = balance["annualRemaining"] - days_requested
            details = {"deducted": days_requested, "from": "annual"}

        elif leave_type == "casual":
            end_date = app.get("endDate")
            if isinstance(end_date, dict) and "$date" in end_date:
                end_date = datetime.fromisoformat(end_date["$date"].replace("Z", "+00:00")).replace(tzinfo=None)
            calendar_days   = (end_date - effective_date).days + 1 if isinstance(effective_date, datetime) and isinstance(end_date, datetime) else days_requested
            casual_remaining = balance.get("casualCalendarDaysRemaining", 7)
            casual_used      = balance.get("casualCalendarDaysUsed", 0)

            new_casual_used = casual_used + calendar_days
            update_fields["casualCalendarDaysUsed"]      = new_casual_used
            update_fields["casualCalendarDaysRemaining"]  = max(0, 7 - new_casual_used)

            if calendar_days > casual_remaining:
                excess_calendar  = calendar_days - casual_remaining
                excess_working   = days_requested  # approximation; precise calc requires holidays
                if balance.get("annualRemaining", 0) < excess_working:
                    return {"success": False,
                            "message": f"Insufficient annual leave for excess casual. Need {excess_working}, Available: {balance.get('annualRemaining', 0)}",
                            "details": {}}
                update_fields["annualRemaining"] = balance["annualRemaining"] - excess_working
                details = {"casual_days": calendar_days, "excess_working": excess_working, "from": "casual+annual"}
            else:
                details = {"casual_days": calendar_days, "from": "casual"}

        elif leave_type == "compassionate":
            if balance.get("compassionateRemaining", 0) < days_requested:
                return {"success": False,
                        "message": f"Insufficient compassionate leave. Available: {balance.get('compassionateRemaining', 0)}, Requested: {days_requested}",
                        "details": {}}
            update_fields["compassionateUsed"]      = balance.get("compassionateUsed", 0) + days_requested
            update_fields["compassionateRemaining"] = balance["compassionateRemaining"] - days_requested
            details = {"deducted": days_requested, "from": "compassionate"}

        elif leave_type == "sick":
            new_sick = balance.get("sickThisYear", 0) + days_requested
            update_fields["sickThisYear"]          = new_sick
            update_fields["sickThisYearRemaining"] = max(0, 21 - new_sick)
            rolling = balance.get("sickRolling12m", 0) + days_requested
            update_fields["sickRolling12m"]        = rolling
            update_fields["sickRollingRemaining"]  = max(0, 42 - rolling)
            details = {"tracked": days_requested, "from": "sick"}

        elif leave_type == "maternity":
            if not balance.get("maternityAvailable", True):
                return {"success": False, "message": "Maternity leave already used.", "details": {}}
            update_fields["maternityAvailable"] = False
            update_fields["maternityStartDate"] = datetime.utcnow()
            details = {"from": "maternity"}

        elif leave_type == "paternity":
            if not balance.get("paternityAvailable", True):
                return {"success": False, "message": "Paternity leave already used.", "details": {}}
            update_fields["paternityAvailable"] = False
            update_fields["paternityDaysUsed"]  = 14
            details = {"from": "paternity"}

        elif leave_type == "disembarkation":
            if not balance.get("disembarkationAvailable", True):
                return {"success": False, "message": "Disembarkation leave already used.", "details": {}}
            attachment_months = (app.get("tacosDetails") or {}).get("attachmentMonths", 0)
            days_to_deduct    = 14 if attachment_months and attachment_months > 6 else 7
            if balance.get("annualRemaining", 0) < days_to_deduct:
                return {"success": False,
                        "message": f"Insufficient annual leave for disembarkation. Need {days_to_deduct}, Available: {balance.get('annualRemaining', 0)}",
                        "details": {}}
            update_fields["annualRemaining"]        = balance["annualRemaining"] - days_to_deduct
            update_fields["disembarkationAvailable"] = False
            details = {"deducted": days_to_deduct, "from": "annual (disembarkation)"}

        elif leave_type == "terminal":
            if balance.get("terminalGranted", False):
                return {"success": False, "message": "Terminal leave already granted.", "details": {}}
            update_fields["terminalGranted"]   = True
            update_fields["terminalAvailable"] = False
            details = {"from": "terminal"}

        else:
            return {"success": False, "message": f"Unknown leave type: {leave_type}", "details": {}}

        # Apply
        update_fields["updatedAt"] = datetime.utcnow()
        notes = balance.get("notes", [])
        notes.append(f"{datetime.utcnow().isoformat()}: Deducted - {leave_type} - {days_requested} days")
        update_fields["notes"] = notes

        leave_balances_coll.update_one({"_id": balance["_id"]}, {"$set": update_fields})
        return {"success": True, "message": "Deduction successful", "details": details}

    except Exception as e:
        return {"success": False, "message": f"Deduction error: {str(e)}", "details": {}}


def _notify_applicant_approved_step(app, current_user, next_info, notifications_coll):
    notifications_coll.insert_one({
        "type":          "application_approved_step",
        "applicationId": app["_id"],
        "referenceId":   app.get("referenceId"),
        "applicantId":   app.get("applicantId"),
        "target":        {"type": "user", "userId": app.get("applicantId")},
        "message":       f"Your application {app.get('referenceId')} approved by "
                         f"{current_user.get('fullName')}. {next_info}",
        "status":        "unread",
        "readBy":        [],
        "createdAt":     datetime.utcnow(),
        "isActive":      True,
    })


def _notify_applicant_rejected(app, reason, current_user, notifications_coll):
    notifications_coll.insert_one({
        "type":          "application_rejected",
        "applicationId": app["_id"],
        "referenceId":   app.get("referenceId"),
        "applicantId":   app.get("applicantId"),
        "target":        {"type": "user", "userId": app.get("applicantId")},
        "message":       f"Your application {app.get('referenceId')} was rejected. Reason: {reason}"
                            f" Rejected by {current_user.get('fullName')}.",
        "status":        "unread",
        "readBy":        [],
        "createdAt":     datetime.utcnow(),
        "isActive":      True,
    })




@approver_dashboard.route('/dashboard_leave_pass')
def dashboard_leave_pass():
    if 'user_email' not in session:
        return redirect(url_for('index'))

    
    current_user = {
        "service_number":  session.get("service_number"),
        "fullName":        session.get("name"),
        "name":            session.get("name"),
        "role":            session.get("role"),
        "directorate":     session.get("directorate"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":           session.get("email"),
        "is_so_approver":  session.get("is_so_approver", False),
        "is_dd_approver":  session.get("is_dd_approver", False),
        "is_ad_approver":  session.get("is_ad_approver", False),
        "is_final_approver": session.get("is_final_approver", False),
        "is_approval_role": session.get("is_approval_role", False),
    }

    if not current_user.get("service_number") and not current_user.get("email"):
        flash("Session expired. Please log in again.", "error")
        return redirect(url_for('login'))

    user_id          = current_user["service_number"] or current_user["email"]
    user_directorate = current_user["directorate"]
    user_role       = session.get('role', 'civilian')
    is_director_doa = (user_role == 'director') and (session.get("is_final_approver") is True)
    is_chief_clerk    = 'registry'        in user_role
    is_cdsa           = 'cdsa'            in user_role
    is_central_reg    = 'central_registry' in user_role
    is_so_approver   = current_user["is_so_approver"]
    is_dd_approver    = current_user["is_dd_approver"]
    is_ad_approver    = current_user["is_ad_approver"]
    is_approval_role  = current_user["is_approval_role"]

    applications_coll = current_app.applications_collection
    # user_coll         = current_app.users_collection

    # ── Filters ───────────────────────────────────────────────────────
    filter_query       = {}
    filter_directorate = request.args.get('directorate', '')
    filter_status      = request.args.get('status', '')
    filter_leave_type  = request.args.get('batch', '')
    filter_date_from   = request.args.get('date_from', '')
    filter_date_to     = request.args.get('date_to', '')

    # Directorate scoping
    if filter_directorate and (is_director_doa or is_cdsa or user_role in ('civilian_head_cao', 'civilian_head', 'deputy_civilian_head_cao', 'deputy_civilian_head')):
        filter_query['directorate'] = filter_directorate
    elif is_chief_clerk or is_director_doa:
        # registry and is_director_doa are scoped to their own directorate

        if is_chief_clerk:
            filter_query['directorate'] = user_directorate
    elif not is_director_doa and not is_cdsa and user_role not in ('civilian_head_cao', 'civilian_head', 'deputy_civilian_head_cao', 'deputy_civilian_head'):
        filter_query['directorate'] = user_directorate

    if filter_leave_type:
        filter_query['leave_type'] = filter_leave_type

    date_query = {}
    if filter_date_from:
        try:
            date_query['$gte'] = datetime.strptime(filter_date_from, '%Y-%m-%d')
        except ValueError:
            pass
    if filter_date_to:
        try:
            dt = datetime.strptime(filter_date_to, '%Y-%m-%d')
            date_query['$lte'] = dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            pass
    if date_query:
        filter_query['createdAt'] = date_query

    pending_applications  = []
    approved_applications = []
    rejected_applications = []



    # ══════════════════════════════════════════════════════════════════
    # CENTRAL REGISTRY — receipt issuance for non-civilian applications
    # Shows up AFTER CDSA/Director approves (status == "approved")
    # Their step is auto-set to "approved" but receipt not yet issued
    # ══════════════════════════════════════════════════════════════════
    if is_central_reg:
        # Log for debugging
        print(f"[DEBUG] Central Registry User: {user_id}")
        
        # Pending: Application approved (status="approved"), central_registry step not yet issued receipt
        # Don't filter by directorate for central registry - they see all
        pending_query = {
            "status": {"$in": ["approved", "Approved"]},
            "approvalChain": {
                "$elemMatch": {
                    "role": "central_registry",
                    "approverId": user_id,
                    "status": {"$in": ["approved", "Approved"]},
                    "receipt": {"$exists": False}
                }
            }
        }
        
        # Add any additional filters (excluding directorate filter for central registry)
        if filter_leave_type:
            pending_query['leave_type'] = filter_leave_type
        if date_query:
            pending_query['createdAt'] = date_query
            
        print(f"[DEBUG] Pending Query: {pending_query}")
        
        for app_doc in applications_coll.find(pending_query).sort("updatedAt", -1).limit(200):
            chain = app_doc.get("approvalChain", [])
            central_step = next(
                (s for s in chain
                 if s["role"] == "central_registry" and s.get("approverId") == user_id),
                None
            )
            # Pending = approved but receipt not yet generated
            if central_step and central_step.get("status", "").lower() == "approved" \
                    and not central_step.get("receipt"):
                app_doc["_role_bucket"] = app_doc.get("role_bucket", "officer")
                pending_applications.append(app_doc)
                print(f"[DEBUG] Found pending app: {app_doc.get('referenceId')} - {app_doc.get('role_bucket')}")

        # Issued (receipt generated)
        issued_query = {
            "status": {"$in": ["issued", "Issued"]},
            "approvalChain": {
                "$elemMatch": {
                    "role": "central_registry",
                    "approverId": user_id,
                }
            }
        }
        if filter_leave_type:
            issued_query['leave_type'] = filter_leave_type
        if date_query:
            issued_query['createdAt'] = date_query
            
        for app_doc in applications_coll.find(issued_query).sort("updatedAt", -1).limit(50):
            approved_applications.append(app_doc)

        # Rejected history
        rejected_query = {
            "status": "rejected",
            "approvalChain": {
                "$elemMatch": {"role": "central_registry", "approverId": user_id}
            }
        }
        if filter_leave_type:
            rejected_query['leave_type'] = filter_leave_type
        if date_query:
            rejected_query['createdAt'] = date_query
            
        for app_doc in applications_coll.find(rejected_query).sort("updatedAt", -1).limit(50):
            rejected_applications.append(app_doc)


    # ══════════════════════════════════════════════════════════════════
    # Director-DOA — civilian receipt generation
    # Sees applications where:
    #   - status == "approved" (Director has approved, deduction done)
    #   - their director_doa chain step is pending (no receipt yet)
    # ══════════════════════════════════════════════════════════════════
    elif is_director_doa:
        # Pending: Either:
        # 1. Civilian apps from other directorates (status == "approved") awaiting receipt/final approval
        # 2. Civilian apps from own directorate (status == "pending") awaiting local Director approval
        pending_query = {
            "role_bucket": "civilian",
            "status": {"$in": ["pending", "approved", "Approved"]},
            "approvalChain": {
                "$elemMatch": {
                    "role":       "director",
                    "is_final_approver": True,
                    "approverId": user_id,
                    "status":     "pending",
                }
            },
            **filter_query
        }
        for app in applications_coll.find(pending_query).sort("updatedAt", -1).limit(200):
            chain = app.get("approvalChain", [])
            
            # Find the user's step and index in the chain
            user_step_index = None
            director_doa_step = None
            for idx, s in enumerate(chain):
                if s["role"] == "director" and s.get("is_final_approver") == True and s.get("approverId") == user_id:
                    director_doa_step = s
                    user_step_index = idx
                    break
            
            if director_doa_step and director_doa_step.get("status") == "pending" \
                    and not director_doa_step.get("receipt"):
                
                # Check if all previous steps in the chain are already approved/recommended
                all_prev = True
                if user_step_index is not None:
                    all_prev = all(
                        chain[i]["status"] in ("approved", "Recommended for Approval")
                        for i in range(user_step_index)
                    )
                
                if all_prev:
                    pending_applications.append(app)

        # Issued (receipt generated) or Approved (final step approved by final approver)
        issued_query = {
            "role_bucket": "civilian",
            "status": {"$in": ["Approved", "approved", "issued"]},
            "approvalChain": {
                "$elemMatch": {
                    "role":       "director",
                    "is_final_approver": True,
                    "approverId": user_id,
                    "status":     "approved",
                }
            },
            **filter_query
        }
        for app in applications_coll.find(issued_query).sort("updatedAt", -1).limit(50):
            approved_applications.append(app)

        # Rejected
        rejected_query = {
            "status": "rejected",
            "approvalChain": {
                "$elemMatch": {
                    "role":       "director",
                    "is_final_approver": True,
                    "approverId": user_id,
                }
            },
            **filter_query
        }
        for app in applications_coll.find(rejected_query).sort("updatedAt", -1).limit(50):
            rejected_applications.append(app)

    # ══════════════════════════════════════════════════════════════════
    # CDSA — director forms approval + central_registry receipt
    # ══════════════════════════════════════════════════════════════════
    elif is_cdsa:
        # 1. Pending CDSA approval (director applications awaiting CDSA)
        cdsa_pending_query = {
            "role_bucket": "director",
            "status":      "pending",
            "approvalChain": {
                "$elemMatch": {
                    "role":       "cdsa",
                    "approverId": user_id,
                    "status":     "pending"
                }
            },
            **filter_query
        }
        for app in applications_coll.find(cdsa_pending_query).sort("createdAt", -1).limit(200):
            chain    = app.get("approvalChain", [])
            cdsa_idx = next(
                (i for i, s in enumerate(chain)
                 if s["role"] == "cdsa" and s["approverId"] == user_id),
                None
            )
            if cdsa_idx is not None:
                all_prev = all(chain[i]["status"] == "approved" for i in range(cdsa_idx))
                if all_prev:
                    pending_applications.append(app)

        # 2. Pending central_registry receipt (after CDSA approved)
        # CDSA user also acts as central_registry issuer
        central_receipt_query = {
            "status": "approved",
            "approvalChain": {
                "$elemMatch": {
                    "role":       "central_registry",
                    "approverId": user_id,
                    "status":     "approved",
                }
            },
            **filter_query
        }
        for app in applications_coll.find(central_receipt_query).sort("updatedAt", -1).limit(200):
            chain        = app.get("approvalChain", [])
            central_step = next(
                (s for s in chain
                 if s["role"] == "central_registry" and s.get("approverId") == user_id),
                None
            )
            if central_step and not central_step.get("receipt"):
                # Avoid duplicates if already in pending list
                app_ids = {str(a["_id"]) for a in pending_applications}
                if str(app["_id"]) not in app_ids:
                    pending_applications.append(app)

        # Approved / issued history
        for app in applications_coll.find({
            "role_bucket": "director",
            "status": {"$in": ["approved", "issued"]},
            "approvalChain": {
                "$elemMatch": {"role": "cdsa", "approverId": user_id, "status": "approved"}
            },
            **filter_query
        }).sort("updatedAt", -1).limit(50):
            app_ids = {str(a["_id"]) for a in pending_applications}
            if str(app["_id"]) not in app_ids:
                approved_applications.append(app)

        # Rejected history
        for app in applications_coll.find({
            "role_bucket": "director",
            "status": "rejected",
            "approvalChain": {
                "$elemMatch": {"role": "cdsa", "approverId": user_id}
            },
            **filter_query
        }).sort("updatedAt", -1).limit(50):
            rejected_applications.append(app)

    # ══════════════════════════════════════════════════════════════════
    # REGISTRY (directorate) — file acknowledgement
    # Shows up AFTER Director/CDSA approves (status == "approved")
    # Their step is auto-set to "approved" but acknowledged=False
    # ══════════════════════════════════════════════════════════════════
    elif is_chief_clerk:
        # Pending: Director approved (status="approved"), registry step not yet acknowledged
        pending_query = {
            "status": {"$in": ["approved", "Approved", "issued"]},       # Director / CDSA has approved / issued
            "approvalChain": {
                "$elemMatch": {
                    "role":         "registry",
                    "approverId":   user_id,
                    "status":       "approved",   # auto-approved when director approved
                    "acknowledged": False          # not yet acknowledged
                }
            },
            **filter_query
        }
        for app in applications_coll.find(pending_query).sort("updatedAt", -1).limit(200):
            chain        = app.get("approvalChain", [])
            registry_step = next(
                (s for s in chain
                 if s["role"] == "registry" and s.get("approverId") == user_id),
                None
            )
            if registry_step and not registry_step.get("acknowledged", False):
                app["_role_bucket"] = app.get("role_bucket", "civilian")
                pending_applications.append(app)

        # Acknowledged history
        ack_query = {
            "approvalChain": {
                "$elemMatch": {
                    "role":         "registry",
                    "approverId":   user_id,
                    "acknowledged": True
                }
            },
            **filter_query
        }
        for app in applications_coll.find(ack_query).sort("updatedAt", -1).limit(50):
            approved_applications.append(app)

        # Rejected history
        rejected_query = {
            "status": "rejected",
            "approvalChain": {
                "$elemMatch": {"role": "registry", "approverId": user_id}
            },
            **filter_query
        }
        for app in applications_coll.find(rejected_query).sort("updatedAt", -1).limit(50):
            rejected_applications.append(app)

    # ══════════════════════════════════════════════════════════════════
    # ALL OTHER APPROVERS (civilian_head_cao, ad, dd, director)
    # Pending = status "pending" AND it's their turn in chain
    # Approved = they approved it (regardless of overall status)
    # ══════════════════════════════════════════════════════════════════
    else:
        query_conditions = [{"approvalChain.approverId": user_id}]

        if is_so_approver or current_user.get("is_so_approver"):
            query_conditions.append({
                "directorate": user_directorate,
                "approvalChain": {"$elemMatch": {
                    "role": "so", "status": "pending"
                }}
            })
        if is_ad_approver or current_user.get("is_ad_approver"):
            query_conditions.append({
                "directorate": user_directorate,
                "approvalChain": {"$elemMatch": {
                    "role": "ad", "status": "pending"
                }}
            })
        if is_dd_approver:
            query_conditions.append({
                "directorate": user_directorate,
                "approvalChain": {"$elemMatch": {
                    "role": "dd", "status": "pending"
                }}
            })

        user_apps_query = {"$or": query_conditions, **filter_query}

        for app in applications_coll.find(user_apps_query).sort("createdAt", -1).limit(500):
            chain      = app.get("approvalChain", [])
            app_status = app.get("status")

            user_step       = None
            user_step_index = None

            for i, step in enumerate(chain):
                if step.get("approverId") == user_id and step.get("status") == "pending":
                    user_step = step; 
                    user_step_index = i; 
                    break

            if not user_step:
                for i, step in enumerate(chain):
                    if step.get("approverId") == user_id:
                        user_step = step; 
                        user_step_index = i; 
                        break
                    
                if (is_so_approver or current_user.get("is_so_approver")) and step.get("role") == "so" \
                        and app.get("directorate") == user_directorate:
                    user_step = step; user_step_index = i; break
                if is_ad_approver and step.get("role") == "ad" \
                        and app.get("directorate") == user_directorate:
                    user_step = step; user_step_index = i; break
                if is_dd_approver and step.get("role") == "dd" \
                        and app.get("directorate") == user_directorate:
                    user_step = step; user_step_index = i; break

            if not user_step:
                continue

            user_step_status = user_step.get("status")

            # ── Classify into buckets ──────────────────────────────
            if app_status in ("issued", "Approved"):
                approved_applications.append(app)

            elif app_status in ("rejected", "Rejected"):
                rejected_applications.append(app)

            elif user_step_status in ("approved", "Recommended for Approval"):
                # They approved/recommended it — show in approved regardless of
                # whether downstream steps are done
                approved_applications.append(app)

            elif user_step_status in ("rejected", "Rejected"):
                rejected_applications.append(app)

            elif user_step_status == "pending" and app_status in ("pending", "Approved", "Recommended for Approval"):
                # Only show as pending if ALL previous steps are approved/recommended
                all_prev = all(
                    chain[i]["status"] in ("approved", "Recommended for Approval")
                    for i in range(user_step_index)
                )
                if all_prev:
                    pending_applications.append(app)
                # If not their turn yet — don't show at all (not pending for them)

    # Fetch user's own submitted applications (only if NOT logged into an office/approval account)
    own_apps = []
    if not session.get("is_approval_role"):
        service_number = current_user.get("service_number")
        email = current_user.get("email")
        if service_number or email:
            own_query_conditions = []
            if service_number:
                own_query_conditions.append({"applicantId": service_number})
            if email:
                own_query_conditions.append({"applicantId": email})
            
            try:
                own_apps = list(applications_coll.find({"$or": own_query_conditions}).sort("createdAt", -1))
            except Exception as e:
                print(f"Error fetching own applications in dashboard_leave_pass: {e}")
            
    if session.get("is_approval_role"):
        for app in own_apps:
            app_status = app.get("status")
            if app_status in ("pending", "awaiting_reliever", "declined_by_reliever", "Recommended for Approval"):
                pending_applications.append(app)
            elif app_status in ("approved", "Approved", "issued"):
                approved_applications.append(app)
            elif app_status in ("rejected", "Rejected"):
                rejected_applications.append(app)

    # ── Deduplicate & sort ─────────────────────────────────────────────
    seen_ids    = set()
    unique_apps = []
    for app in pending_applications + approved_applications + rejected_applications:
        aid = str(app['_id'])
        if aid not in seen_ids:
            seen_ids.add(aid)
            unique_apps.append(app)

    unique_apps.sort(key=lambda x: x.get('createdAt', datetime.min), reverse=True)

    # ── Enrich applicant name ──────────────────────────────────────────
    for app in unique_apps:
        if not app.get('applicantName'):
            applicant = current_app.users_collection.find_one(
                {"service_number": app.get("applicantId")}, {"fullName": 1}
            )
            if not applicant:
                applicant = current_app.users_collection.find_one(
                    {"service_number": app.get("applicantId")}, {"name": 1}
                )
                app['applicantName'] = applicant.get('name', 'Unknown') if applicant else "Unknown"
            else:
                app['applicantName'] = applicant.get('fullName', 'Unknown')

    # ── Directorate stats ──────────────────────────────────────────────
    directorate_stats = []
    all_directorates  = []

    if is_director_doa:
        all_directorates = applications_coll.distinct('directorate')
        pipeline = [
            {"$match": {
                "status": "approved",
                "approvalChain": {
                    "$elemMatch": {
                        "role":       "director",
                        "is_final_approver": True,
                        "approverId": user_id,
                        "status":     "pending",
                    }
                },
                **filter_query
            }},
            {"$group": {
                "_id":         "$directorate",
                "count":       {"$sum": 1},
                "latest_date": {"$max": "$updatedAt"}
            }},
            {"$sort": {"_id": 1}}
        ]
        directorate_stats = list(applications_coll.aggregate(pipeline))
        for stat in directorate_stats:
            if isinstance(stat.get('latest_date'), datetime):
                stat['latest_date'] = stat['latest_date'].strftime('%d %b %Y')
            stat['latest_batch'] = 'N/A'

    elif is_cdsa:
        all_directorates = applications_coll.distinct('directorate')
        pipeline = [
            {"$match": {
                "role_bucket": "director",
                "status":      "pending",
                "approvalChain": {
                    "$elemMatch": {
                        "role":       "cdsa",
                        "approverId": user_id,
                        "status":     "pending"
                    }
                },
                **filter_query
            }},
            {"$group": {
                "_id":         "$directorate",
                "count":       {"$sum": 1},
                "latest_date": {"$max": "$createdAt"}
            }},
            {"$sort": {"_id": 1}}
        ]
        directorate_stats = list(applications_coll.aggregate(pipeline))
        for stat in directorate_stats:
            if isinstance(stat.get('latest_date'), datetime):
                stat['latest_date'] = stat['latest_date'].strftime('%d %b %Y')
            stat['latest_batch'] = 'N/A'

    filter_values = {
        'filter_directorate': filter_directorate if (is_director_doa or is_cdsa) else '',
        'filter_status':      filter_status,
        'filter_leave_type':  filter_leave_type,
        'filter_date_from':   filter_date_from,
        'filter_date_to':     filter_date_to,
    }

    leave_pending_count = len(pending_applications)
    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])

    with open("dashboard_debug.txt", "a", encoding="utf-8") as debug_f:
        debug_f.write(f"is_director_doa: {is_director_doa}\n")
        debug_f.write(f"is_chief_clerk: {is_chief_clerk}\n")
        debug_f.write(f"is_approval_role: {is_approval_role}\n")
        debug_f.write(f"pending_applications count: {len(pending_applications)}\n")
        for app in pending_applications:
            debug_f.write(f"  Pending App: {app.get('referenceId')} | Status: {app.get('status')}\n")
        debug_f.write(f"approved_applications count: {len(approved_applications)}\n")
        for app in approved_applications:
            debug_f.write(f"  Approved App: {app.get('referenceId')} | Status: {app.get('status')}\n")

    return render_template(
        'dashboard_leave_pass.html',
        applications=unique_apps,
        submitted_applications=own_apps,
        user=current_user,
        current_time=datetime.utcnow(),
        pending_count=len(pending_applications),
        leave_pending_count=leave_pending_count,
        approved_count=len(approved_applications),
        rejected_count=len(rejected_applications),
        total_count=len(unique_apps),
        is_director_doa=is_director_doa,
        is_chief_clerk=is_chief_clerk,
        is_cdsa=is_cdsa,
        is_so_approver=is_so_approver,
        is_dd_approver=is_dd_approver,
        is_ad_approver=is_ad_approver,
        is_approval_role=is_approval_role,
        directorate_stats=directorate_stats,
        all_directorates=all_directorates,
        **filter_values,
        permissions=user_allowed_features,
        active_page='leave_pass',
        pending_applications=pending_applications,
        approved_applications=approved_applications,
        rejected_applications=rejected_applications,
    )



@approver_dashboard.route('/approve/<string:app_id>', methods=['POST'])
def approve(app_id):
    current_user = {
        "service_number":  session.get("service_number"),
        "fullName":        session.get("name"),
        "directorate":     session.get("directorate"),
        "role":           session.get("role"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":           session.get("email"),
        "is_so_approver":  session.get("is_so_approver", False),
        "is_dd_approver":  session.get("is_dd_approver", False),
        "is_ad_approver":  session.get("is_ad_approver", False),
        "is_final_approver": session.get("is_final_approver", False),
    }

    if not current_user.get("service_number"):
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    applications_coll  = current_app.applications_collection
    notifications_coll = current_app.notifications_collection
    leave_balances_coll = current_app.leave_balances
    users_coll          = current_app.users_collection

    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
    except Exception:
        app = None
    
    if not app:
        flash("Application not found.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    if app.get("status", "").lower() not in ("pending", "recommended for approval", "approved"):
        flash(f"Cannot approve: Application is already {app.get('status')}.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    chain            = app.get("approvalChain", [])
    user_id          = current_user["service_number"]
    user_directorate = current_user["directorate"]
    is_so_approver   = current_user["is_so_approver"]
    is_ad_approver   = current_user["is_ad_approver"]
    is_dd_approver   = current_user["is_dd_approver"]
    is_final_approver = current_user["is_final_approver"]

    # ── Find user's step ──────────────────────────────────────────────
    user_step_index = None
    user_step       = None

    for i, step in enumerate(chain):
        if step.get("approverId") == user_id and step.get("status") == "pending":
            user_step_index = i
            user_step = step
            break
        
        if is_so_approver and step.get("role") == "so" \
                and step.get("status") == "pending" \
                and app.get("directorate") == user_directorate:
            user_step_index = i
            user_step = step
            break
        
        if is_ad_approver and step.get("role") == "ad" \
                and step.get("status") == "pending" \
                and app.get("directorate") == user_directorate:
            user_step_index = i
            user_step = step
            break
        
        if is_dd_approver and step.get("role") == "dd" \
                and step.get("status") == "pending" \
                and app.get("directorate") == user_directorate:
            user_step_index = i
            user_step = step
            break

    if user_step_index is None:
        flash("This application is not waiting for your approval.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # In the approve route, add a check to prevent self-approval
    if user_step and user_step.get("approverId") == app.get("applicantId"):
        flash("You cannot approve your own application.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # Check previous steps are approved/recommended
    for i in range(user_step_index):
        if chain[i]["status"] not in ("approved", "Recommended for Approval"):
            flash("Cannot approve: Previous approvals are still pending.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    step_role = user_step.get("role")
    is_civilian_recommendation = (app.get("role_bucket") == "civilian" and step_role in ("civilian_head_cao", "so", "dd"))

    if is_civilian_recommendation:
        default_comment = "Recommended for Approval"
        step_status = "Recommended for Approval"
    else:
        default_comment = "Approved"
        step_status = "approved"

    comments = request.form.get("comments", "").strip() or default_comment
    chain[user_step_index].update({
        "status":                step_status,
        "comments":              comments,
        "timestamp":             datetime.utcnow()
    })

    is_cdsa_step = (user_step.get("role") == "cdsa")
    if is_cdsa_step:
        receipt_number = _process_cdsa_receipt_issuance(
            app, chain, user_id, current_user, comments,
            applications_coll, notifications_coll, users_coll
        )
        try:
            socketio.emit(
                "application_update",
                {
                    "status":      "issued",
                    "referenceId": app.get("referenceId"),
                    "approved_by": current_user.get("fullName") or current_user.get("name"),
                    "step":        "cdsa",
                    "timestamp":   datetime.utcnow().isoformat(),
                },
                room=f"APPLICATION_{app.get('referenceId')}"
            )
        except Exception as e:
            print(f"Socket.IO emit failed: {e}")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "message": f"Application approved by CDSA. Receipt {receipt_number} issued successfully."})
        flash(f"Application approved by CDSA. Receipt {receipt_number} issued successfully.", "success")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    is_director_step = (user_step.get("role") == "director")
    role_bucket      = app.get("role_bucket", "officer")

    # ══════════════════════════════════════════════════════════════════
    # DEDUCTION — happens when Director or CDSA approves
    # ══════════════════════════════════════════════════════════════════
    update_op = {
        "$set": {
            "approvalChain": chain,
            "updatedAt":     datetime.utcnow(),
        }
    }

    if is_director_step and role_bucket != "director":

        deduction_result = _perform_leave_deduction(app, leave_balances_coll)
        if not deduction_result["success"]:
            applications_coll.update_one(
                {"_id": app["_id"]},
                {"$set": {
                    "approvalChain": chain,
                    "status": "rejected",
                    "updatedAt": datetime.utcnow(),
                }}
            )
            _notify_applicant_rejected(
                app, deduction_result['message'], current_user, notifications_coll
            )
            flash(f"❌ {deduction_result['message']}. Application rejected.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

        # If this director step is also the final approval step (same directorate as final approver),
        # issue receipt and finalize the approval immediately in one go.
        if user_step.get("is_final_approver") == True:
            receipt_number = _process_same_directorate_receipt_issuance(
                app, chain, user_step_index, user_id, current_user, comments,
                applications_coll, notifications_coll, deduction_result
            )
            try:
                socketio.emit(
                    "application_update",
                    {
                        "status":      "issued",
                        "referenceId": app.get("referenceId"),
                        "approved_by": current_user.get("fullName") or current_user.get("name"),
                        "step":        "director",
                        "timestamp":   datetime.utcnow().isoformat(),
                    },
                    room=f"APPLICATION_{app.get('referenceId')}"
                )
            except Exception as e:
                print(f"Socket.IO emit failed: {e}")

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": True, "message": f"Application approved and receipt {receipt_number} issued successfully."})
            flash(f"Application approved and receipt {receipt_number} issued successfully.", "success")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

        # ══════════════════════════════════════════════════════════════════
        # AUTO-APPROVE DOWNSTREAM STEPS BASED ON APPLICANT TYPE
        # ══════════════════════════════════════════════════════════════════
        notified_steps = []
        final_approver_step = None
        
        for step in chain:
            # Skip if already approved or not pending
            if step.get("status") != "pending":
                continue
            
            # Directorate registry - always auto-approve
            if step.get("role") == "registry" and step.get("registry_type") == "directorate":
                step.update({
                    "status":       "approved",
                    "comments":     "Automatically approved upon Director's approval",
                    "timestamp":    datetime.utcnow(),
                    "approvedBy":   user_id,
                    "approvedByName": current_user.get("name"),
                })
                notified_steps.append(step)
            
            # Civilian: is_final_approver (keep pending but notify)
            elif role_bucket == "civilian" and step.get("is_final_approver") == True:
                # Do NOT auto-approve. Keep status as pending, but capture for notification
                final_approver_step = step
            
            # Officer/Deputy Director: Central registry for receipt
            elif role_bucket in ["officer", "dd"] and step.get("role") == "central_registry":
                step.update({
                    "status":       "approved",
                    "comments":     "Automatically approved upon Director's approval",
                    "timestamp":    datetime.utcnow(),
                    "approvedBy":   user_id,
                    "approvedByName": current_user.get("name"),
                })
                notified_steps.append(step)

        update_op["$set"]["status"]          = "Approved" if (role_bucket == "civilian") else "approved"
        update_op["$set"]["leaveDeductedAt"] = datetime.utcnow()
        update_op["$set"]["leaveDeductedBy"] = user_id
        update_op["$push"] = {
            "auditTrail": {
                "action":    "leave_deducted",
                "by":        user_id,
                "byName":    current_user.get("fullName"),
                "timestamp": datetime.utcnow(),
                "details":   deduction_result.get("details", {}),
            }
        }

        # Notify each downstream step
        for step in notified_steps:
            is_receipt_step = (step.get("role") == "so1_doa") or (step.get("role") == "central_registry")
            
            if is_receipt_step:
                msg = f"Application {app.get('referenceId')} approved by Director. Please issue the leave receipt."
            else:
                msg = f"Application {app.get('referenceId')} approved by Director. Please acknowledge receipt of file."
            
            notify_pending_approval(app, step, current_user)
            notifications_coll.insert_one({
                "type":          "action_required",
                "applicationId": app["_id"],
                "referenceId":   app.get("referenceId"),
                "target":        {"type": "user", "userId": step.get("approverId"), "role": step.get("role")},
                "message":       msg,
                "status":        "unread",
                "readBy":        [],
                "meta": {
                    "triggeredBy":     user_id,
                    "triggeredByName": current_user.get("name"),
                    "role":            step.get("role"),
                    "is_receipt_step": is_receipt_step,
                },
                "createdAt": datetime.utcnow(),
                "isActive":  True,
            })

        # Notify final approver if present (Director DOA)
        if final_approver_step:
            msg = f"Application {app.get('referenceId')} approved by Director. Please approve and issue the leave receipt."
            notify_pending_approval(app, final_approver_step, current_user)
            notifications_coll.insert_one({
                "type":          "action_required",
                "applicationId": app["_id"],
                "referenceId":   app.get("referenceId"),
                "target":        {"type": "user", "userId": final_approver_step.get("approverId"), "role": final_approver_step.get("role")},
                "message":       msg,
                "status":        "unread",
                "readBy":        [],
                "meta": {
                    "triggeredBy":     user_id,
                    "triggeredByName": current_user.get("name"),
                    "role":            final_approver_step.get("role"),
                    "is_receipt_step": True,
                },
                "createdAt": datetime.utcnow(),
                "isActive":  True,
            })

        _notify_applicant_approved_step(
            app, current_user,
            "Leave approved. Receipt will be issued shortly.",
            notifications_coll
        )

    else:
        # Not a director step — find next pending step for notification
        next_step = next(
            (s for s in chain if s["status"] == "pending"),
            None
        )
        if next_step:
            notify_pending_approval(app, next_step, current_user)

        _notify_applicant_approved_step(
            app, current_user,
            f"Approved by {user_step.get('role')}. Next: {next_step.get('role') if next_step else 'Final review'}",
            notifications_coll
        )

    applications_coll.update_one({"_id": app["_id"]}, update_op)

    try:
        socketio.emit(
            "application_update",
            {
                "status":      "approved_step",
                "referenceId": app.get("referenceId"),
                "approved_by": current_user.get("name"),
                "step":        user_step.get("role"),
                "timestamp":   datetime.utcnow().isoformat(),
            },
            room=f"APPLICATION_{app.get('referenceId')}"
        )
    except Exception as e:
        print(f"Socket.IO emit failed: {e}")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": "Application approved successfully."})
    flash("Application approved successfully.", "success")
    return redirect(url_for('approver_dashboard.dashboard_leave_pass'))







def _process_cdsa_receipt_issuance(app, chain, user_id, current_user, comments, applications_coll, notifications_coll, users_coll):
    receipt_number = f"REC-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{str(app['_id'])[-6:]}"
    
    notified_steps = []
    for step in chain:
        if step.get("status") != "pending":
            continue
        
        # Auto-approve both registry steps (directorate and central)
        if step.get("role") in ("registry", "central_registry"):
            step.update({
                "status":       "approved",
                "comments":     "Automatically approved upon CDSA's approval",
                "timestamp":    datetime.utcnow(),
                "approvedBy":   user_id,
                "approvedByName": current_user.get("fullName") or current_user.get("name"),
            })
            if step.get("role") == "central_registry":
                step["receipt"] = {
                    "receiptNumber": receipt_number,
                    "issuedDate":    datetime.utcnow(),
                    "issuedBy":      user_id,
                    "issuedByName":  current_user.get("fullName") or current_user.get("name"),
                    "comments":      "Automatically issued upon CDSA's approval",
                }
                step["acknowledged"] = True
                step["acknowledgedAt"] = datetime.utcnow()
                step["acknowledgedBy"] = user_id
                step["acknowledgedByName"] = current_user.get("fullName") or current_user.get("name")
            notified_steps.append(step)

    # Prepare database update operation
    update_op = {
        "$set": {
            "approvalChain":    chain,
            "status":           "issued",
            "receiptNumber":    receipt_number,
            "updatedAt":        datetime.utcnow(),
        },
        "$push": {
            "auditTrail": {
                "action":        "receipt_issued",
                "registry_type": "central_registry",
                "by":            user_id,
                "byName":        current_user.get("fullName") or current_user.get("name"),
                "timestamp":     datetime.utcnow(),
                "receiptNumber": receipt_number,
                "comments":      "Automatically issued upon CDSA approval",
            }
        }
    }
    
    # Update the application
    applications_coll.update_one({"_id": app["_id"]}, update_op)

    # Notify each auto-approved step
    for step in notified_steps:
        is_central = (step.get("registry_type") == "central") or (step.get("role") == "central_registry")
        msg = f"Director application {app.get('referenceId')} approved by CDSA. Please issue the leave receipt." if is_central else f"Director application {app.get('referenceId')} approved by CDSA. Please acknowledge receipt of file."
        
        notifications_coll.insert_one({
            "type":          "action_required",
            "applicationId": app["_id"],
            "referenceId":   app.get("referenceId"),
            "target":        {"type": "user", "userId": step.get("approverId"), "role": step.get("role")},
            "message":       msg,
            "status":        "unread",
            "readBy":        [],
            "meta": {
                "triggeredBy":     user_id,
                "triggeredByName": current_user.get("fullName") or current_user.get("name"),
                "role":            step.get("role"),
                "is_central":      is_central,
            },
            "createdAt": datetime.utcnow(),
            "isActive":  True,
        })

    # Notify applicant
    notifications_coll.insert_one({
        "type":          "receipt_issued",
        "applicationId": app["_id"],
        "referenceId":   app.get("referenceId"),
        "applicantId":   app.get("applicantId"),
        "target":        {"type": "user", "userId": app.get("applicantId")},
        "message":       f"Your leave/pass receipt {receipt_number} has been issued. Application {app.get('referenceId')} is fully approved.",
        "status":        "unread",
        "readBy":        [],
        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id},
        "createdAt":     datetime.utcnow(),
        "isActive":      True,
    })

    # Forward receipt to registries
    try:
        # 1. Forward to applicant's directorate registry
        applicant_dir = app.get("directorate")
        if applicant_dir:
            dir_registries = list(users_coll.find({
                "directorate": {"$regex": f"^{applicant_dir.strip()}$", "$options": "i"},
                "role": {"$in": ["registry", "central_registry"]}
            }))
            if not dir_registries:
                dir_registries = list(users_coll.find({
                    "directorate": {"$regex": f"^{applicant_dir.strip()}$", "$options": "i"},
                    "role": {"$in": ["registry", "central_registry"]}
                }))
            for reg in dir_registries:
                if reg.get("service_number"):
                    notifications_coll.insert_one({
                        "type":          "receipt_forwarded",
                        "applicationId": app["_id"],
                        "referenceId":   app.get("referenceId"),
                        "target":        {"type": "user", "userId": reg.get("service_number")},
                        "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                        "status":        "unread",
                        "readBy":        [],
                        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": applicant_dir},
                        "createdAt":     datetime.utcnow(),
                        "isActive":      True,
                        "is_active":     True
                    })
        
        # 2. Forward to final approval registry (CDSA)
        final_dir = "CDSA"
        final_registries = list(users_coll.find({
            "directorate": {"$regex": f"^{final_dir}$", "$options": "i"},
            "role": {"$in": ["registry", "central_registry"]}
        }))
        if not final_registries:
            final_registries = list(users_coll.find({
                "directorate": {"$regex": f"^{final_dir}$", "$options": "i"},
                "role": {"$in": ["registry", "central_registry"]}
            }))
        for reg in final_registries:
            if reg.get("service_number"):
                notifications_coll.insert_one({
                    "type":          "receipt_forwarded",
                    "applicationId": app["_id"],
                    "referenceId":   app.get("referenceId"),
                    "target":        {"type": "user", "userId": reg.get("service_number")},
                    "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                    "status":        "unread",
                    "readBy":        [],
                    "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": final_dir},
                    "createdAt":     datetime.utcnow(),
                    "isActive":      True,
                    "is_active":     True
                })
    except Exception as e:
        print(f"Failed to forward receipt notifications to registries: {e}")

    # Send email receipt
    try:
        applicant = users_coll.find_one({"service_number": app.get("applicantId")})
        if not applicant:
            applicant = current_app.user_collection.find_one(
                {"service_number": app.get("applicantId")}
            )
        if applicant and applicant.get("email"):
            from utils.email_service import send_final_approval_email
            send_final_approval_email(applicant, app, receipt_number)
    except Exception as e:
        print(f"Email send failed: {e}")

    # Socket.io notification
    try:
        socketio.emit(
            "new_notification",
            {
                "type":          "receipt_issued",
                "referenceId":   app.get("referenceId"),
                "receiptNumber": receipt_number,
                "message":       f"Your receipt {receipt_number} is ready.",
                "timestamp":     datetime.utcnow().isoformat(),
            },
            room=f"USER_{app.get('applicantId', '').replace('/', '_')}"
        )
    except Exception as e:
        print(f"Socket.IO emit failed: {e}")

    return receipt_number


def _process_same_directorate_receipt_issuance(app, chain, user_step_index, user_id, current_user, comments, applications_coll, notifications_coll, deduction_result):
    receipt_number = f"REC-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{str(app['_id'])[-6:]}"
    
    # Update the user_step (which is the director step)
    chain[user_step_index]["status"] = "approved"
    chain[user_step_index]["timestamp"] = datetime.utcnow()
    chain[user_step_index]["approvedBy"] = user_id
    chain[user_step_index]["approvedByName"] = current_user.get("fullName") or current_user.get("name")
    chain[user_step_index]["receipt"] = {
        "receiptNumber": receipt_number,
        "issuedDate":    datetime.utcnow(),
        "issuedBy":      user_id,
        "issuedByName":  current_user.get("fullName") or current_user.get("name"),
        "comments":      comments or "Receipt issued",
    }
    chain[user_step_index]["acknowledged"] = True
    chain[user_step_index]["acknowledgedAt"] = datetime.utcnow()
    chain[user_step_index]["acknowledgedBy"] = user_id
    chain[user_step_index]["acknowledgedByName"] = current_user.get("fullName") or current_user.get("name")
    
    # Auto-approve downstream steps (directorate registry)
    notified_steps = []
    for step in chain:
        if step.get("status") == "pending":
            if step.get("role") == "registry" and step.get("registry_type") == "directorate":
                step.update({
                    "status":       "approved",
                    "comments":     "Automatically approved upon Director's approval",
                    "timestamp":    datetime.utcnow(),
                    "approvedBy":   user_id,
                    "approvedByName": current_user.get("name"),
                })
                notified_steps.append(step)

    # Prepare database update operation
    update_op = {
        "$set": {
            "approvalChain":    chain,
            "status":           "issued",
            "receiptNumber":    receipt_number,
            "leaveDeductedAt":  datetime.utcnow(),
            "leaveDeductedBy":  user_id,
            "updatedAt":        datetime.utcnow(),
        },
        "$push": {
            "auditTrail": {
                "$each": [
                    {
                        "action":    "leave_deducted",
                        "by":        user_id,
                        "byName":    current_user.get("fullName"),
                        "timestamp": datetime.utcnow(),
                        "details":   deduction_result.get("details", {}),
                    },
                    {
                        "action":        "receipt_issued",
                        "registry_type": "director_doa",
                        "by":            user_id,
                        "byName":        current_user.get("fullName") or current_user.get("name"),
                        "timestamp":     datetime.utcnow(),
                        "receiptNumber": receipt_number,
                        "comments":      comments,
                    }
                ]
            }
        }
    }
    
    # Update the application
    applications_coll.update_one({"_id": app["_id"]}, update_op)

    # Notify each auto-approved step (directorate registry)
    for step in notified_steps:
        msg = f"Application {app.get('referenceId')} approved by Director. Please acknowledge receipt of file."
        notifications_coll.insert_one({
            "type":          "action_required",
            "applicationId": app["_id"],
            "referenceId":   app.get("referenceId"),
            "target":        {"type": "user", "userId": step.get("approverId"), "role": step.get("role")},
            "message":       msg,
            "status":        "unread",
            "readBy":        [],
            "meta": {
                "triggeredBy":     user_id,
                "triggeredByName": current_user.get("name"),
                "role":            step.get("role"),
                "is_receipt_step": False,
            },
            "createdAt": datetime.utcnow(),
            "isActive":  True,
        })

    # Notify applicant about receipt issuance
    notifications_coll.insert_one({
        "type":          "receipt_issued",
        "applicationId": app["_id"],
        "referenceId":   app.get("referenceId"),
        "applicantId":   app.get("applicantId"),
        "target":        {"type": "user", "userId": app.get("applicantId")},
        "message":       f"Your leave/pass receipt {receipt_number} has been issued. "
                         f"Application {app.get('referenceId')} is fully approved.",
        "status":        "unread",
        "readBy":        [],
        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id},
        "createdAt":     datetime.utcnow(),
        "isActive":      True,
    })

    # Forward receipt to registries
    try:
        users_coll = current_app.users_collection
        applicant_dir = app.get("directorate")
        if applicant_dir:
            dir_registries = list(users_coll.find({
                "directorate": {"$regex": f"^{applicant_dir.strip()}$", "$options": "i"},
                "role": {"$in": ["registry", "central_registry"]}
            }))
            for reg in dir_registries:
                if reg.get("service_number"):
                    notifications_coll.insert_one({
                        "type":          "receipt_forwarded",
                        "applicationId": app["_id"],
                        "referenceId":   app.get("referenceId"),
                        "target":        {"type": "user", "userId": reg.get("service_number")},
                        "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                        "status":        "unread",
                        "readBy":        [],
                        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": applicant_dir},
                        "createdAt":     datetime.utcnow(),
                        "isActive":      True,
                        "is_active":     True
                    })
        
        final_dir = "DOA"
        final_registries = list(users_coll.find({
            "directorate": {"$regex": f"^{final_dir}$", "$options": "i"},
            "role": {"$in": ["registry", "central_registry"]}
        }))
        for reg in final_registries:
            if reg.get("service_number"):
                notifications_coll.insert_one({
                    "type":          "receipt_forwarded",
                    "applicationId": app["_id"],
                    "referenceId":   app.get("referenceId"),
                    "target":        {"type": "user", "userId": reg.get("service_number")},
                    "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                    "status":        "unread",
                    "readBy":        [],
                    "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": final_dir},
                    "createdAt":     datetime.utcnow(),
                    "isActive":      True,
                    "is_active":     True
                })
    except Exception as e:
        print(f"Failed to forward receipt notifications to registries: {e}")

    # Send email receipt
    try:
        applicant = users_coll.find_one({"service_number": app.get("applicantId")})
        if not applicant:
            applicant = current_app.user_collection.find_one(
                {"service_number": app.get("applicantId")}
            )
        if applicant and applicant.get("email"):
            from utils.email_service import send_final_approval_email
            send_final_approval_email(applicant, app, receipt_number)
    except Exception as e:
        print(f"Email send failed: {e}")

    # Socket.io notification
    try:
        socketio.emit(
            "new_notification",
            {
                "type":          "receipt_issued",
                "referenceId":   app.get("referenceId"),
                "receiptNumber": receipt_number,
                "message":       f"Your receipt {receipt_number} is ready.",
                "timestamp":     datetime.utcnow().isoformat(),
            },
            room=f"USER_{app.get('applicantId', '').replace('/', '_')}"
        )
    except Exception as e:
        print(f"Socket.IO emit failed: {e}")

    return receipt_number


# ══════════════════════════════════════════════════════════════════════
# CDSA APPROVE  — director forms only
# ══════════════════════════════════════════════════════════════════════
@approver_dashboard.route('/cdsa_approve/<string:app_id>', methods=['POST'])
# @login_required
# @role_required(['cdsa', 'dcdsa', 'super_admin'])
def cdsa_approve(app_id):
    current_user = {
        "service_number": session.get("service_number"),
        "fullName":       session.get("name"),
        "directorate":    session.get("directorate"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":          session.get("email"),
    }

    if not current_user.get("service_number"):
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    applications_coll   = current_app.applications_collection
    notifications_coll  = current_app.notifications_collection
    leave_balances_coll = current_app.leave_balances
    users_coll          = current_app.users_collection

    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        if not app:
            flash("Application not found.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    except Exception:
        flash("Invalid application ID.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    if app.get("role_bucket") != "director":
        flash("This route is only for Director applications.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    if app.get("status") != "pending":
        flash(f"Cannot approve: Application is already {app.get('status')}.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    chain   = app.get("approvalChain", [])
    user_id = current_user["service_number"]

    cdsa_idx = next(
        (i for i, s in enumerate(chain)
         if s["role"] == "cdsa" and s["approverId"] == user_id and s["status"] == "pending"),
        None
    )
    if cdsa_idx is None:
        flash("This application is not waiting for your CDSA approval.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    for i in range(cdsa_idx):
        if chain[i]["status"] != "approved":
            flash("Cannot approve: Previous steps incomplete.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # ── Deduction at CDSA approval ─────────────────────────────────────
    deduction_result = _perform_leave_deduction(app, leave_balances_coll)
    if not deduction_result["success"]:
        flash(f"❌ {deduction_result['message']}. Application rejected.", "error")
        applications_coll.update_one(
            {"_id": app["_id"]},
            {"$set": {"status": "rejected", "updatedAt": datetime.utcnow()}}
        )
        _notify_applicant_rejected(app, deduction_result['message'],
                                   current_user, notifications_coll)
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    comments = request.form.get("comments", "").strip() or "Approved by CDSA"
    chain[cdsa_idx].update({
        "status":                "approved",
        "comments":              comments,
        "timestamp":             datetime.utcnow()
    })

    # Perform automated receipt generation, registry documentation and email generation
    receipt_number = _process_cdsa_receipt_issuance(
        app, chain, user_id, current_user, comments,
        applications_coll, notifications_coll, users_coll
    )

    try:
        socketio.emit(
            "application_update",
            {
                "status":      "issued",
                "referenceId": app.get("referenceId"),
                "approved_by": current_user.get("fullName"),
                "step":        "cdsa",
                "timestamp":   datetime.utcnow().isoformat(),
            },
            room=f"APPLICATION_{app.get('referenceId')}"
        )
    except Exception as e:
        print(f"Socket.IO emit failed: {e}")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": f"Application approved by CDSA. Receipt {receipt_number} issued successfully."})
    flash(f"Application approved by CDSA. Receipt {receipt_number} issued successfully.", "success")
    return redirect(url_for('approver_dashboard.dashboard_leave_pass'))



@approver_dashboard.route('/issue_receipt/<string:app_id>', methods=['POST'])
def issue_receipt(app_id):
    """
    SO1-DOA issues receipt for civilian applications.
    Central Registry issues receipt for officer/DD/director applications.
    """
    current_user = {
        "service_number": session.get("service_number"),
        "fullName":       session.get("name"),
        "directorate":    session.get("directorate"),
        "role":           session.get("role"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":          session.get("email"),
    }

    user_id = current_user.get("service_number") or current_user.get("email")
    if not user_id:
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    applications_coll  = current_app.applications_collection
    notifications_coll = current_app.notifications_collection

    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        if not app:
            flash("Application not found.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    except Exception:
        flash("Invalid application ID.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # Only allow receipt issuance for approved applications
    if app.get("status", "").lower() not in ("approved",):
        flash(f"Cannot issue receipt: Application status is '{app.get('status')}'. Only approved applications can receive a receipt.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    chain = app.get("approvalChain", [])
    user_roles = current_user.get("role", [])

    # Determine which role to look for
    is_central_registry_user = 'central_registry' in user_roles
    is_director_doa_user = ('director' in user_roles) and (session.get("is_final_approver") is True)

    user_step_idx = None
    target_role = None

    if is_central_registry_user:
        target_role = "central_registry"
        for i, s in enumerate(chain):
            if s.get("role") == "central_registry" and s.get("approverId") == user_id:
                user_step_idx = i
                break
    elif is_director_doa_user:
        target_role = "director_doa"
        for i, s in enumerate(chain):
            if s.get("role") == "director" and s.get("is_final_approver") == True and s.get("approverId") == user_id:
                user_step_idx = i
                break
    else:
        flash("You are not authorized to issue receipts.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    if user_step_idx is None:
        flash("You are not authorized to issue a receipt for this application.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    user_step = chain[user_step_idx]
    
    # Check if receipt already issued
    if user_step.get("receipt"):
        existing_receipt = user_step.get("receipt", {})
        receipt_number = existing_receipt.get("receiptNumber", "Unknown")
        flash(f"Receipt {receipt_number} has already been issued for this application.", "warning")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    
    # Check if step is approved or pending
    if user_step.get("status") not in ("approved", "pending"):
        flash(f"Cannot issue receipt: Step status is '{user_step.get('status')}'.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    comments = request.form.get("comments", "").strip()

    # Generate receipt number
    receipt_number = f"REC-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{str(app['_id'])[-6:]}"

    # Update the step with receipt information and set status to approved
    chain[user_step_idx]["status"] = "approved"
    chain[user_step_idx]["timestamp"] = datetime.utcnow()
    chain[user_step_idx]["approvedBy"] = user_id
    chain[user_step_idx]["approvedByName"] = current_user.get("fullName") or current_user.get("name")
    chain[user_step_idx]["receipt"] = {
        "receiptNumber": receipt_number,
        "issuedDate":    datetime.utcnow(),
        "issuedBy":      user_id,
        "issuedByName": current_user.get("fullName") or current_user.get("name"),
        "comments":      comments or "Receipt issued",
    }
    
    # Also mark as acknowledged if needed
    chain[user_step_idx]["acknowledged"] = True
    chain[user_step_idx]["acknowledgedAt"] = datetime.utcnow()
    chain[user_step_idx]["acknowledgedBy"] = user_id
    chain[user_step_idx]["acknowledgedByName"] = current_user.get("fullName") or current_user.get("name")

    update_op = {
        "$set": {
            "approvalChain": chain,
            "status":        "issued",  # Change overall status to issued
            "receiptNumber": receipt_number,
            "updatedAt":     datetime.utcnow(),
        },
        "$push": {
            "auditTrail": {
                "action":        "receipt_issued",
                "registry_type": target_role,
                "by":            user_id,
                "byName":        current_user.get("name"),
                "timestamp":     datetime.utcnow(),
                "receiptNumber": receipt_number,
                "comments":      comments,
            }
        }
    }

    # Notify applicant
    notifications_coll.insert_one({
        "type":          "receipt_issued",
        "applicationId": app["_id"],
        "referenceId":   app.get("referenceId"),
        "applicantId":   app.get("applicantId"),
        "target":        {"type": "user", "userId": app.get("applicantId")},
        "message":       f"Your leave/pass receipt {receipt_number} has been issued. "
                         f"Application {app.get('referenceId')} is fully approved.",
        "status":        "unread",
        "readBy":        [],
        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id},
        "createdAt":     datetime.utcnow(),
        "isActive":      True,
    })

    # ─── Forward receipt to both Registries ───
    try:
        users_coll = current_app.users_collection
        
        # 1. Forward to applicant's directorate registry
        applicant_dir = app.get("directorate")
        if applicant_dir:
            dir_registries = list(users_coll.find({
                "directorate": {"$regex": f"^{applicant_dir.strip()}$", "$options": "i"},
                "role": {"$in": ["registry", "central_registry"]}
            }))
            if not dir_registries:
                dir_registries = list(users_coll.find({
                    "directorate": {"$regex": f"^{applicant_dir.strip()}$", "$options": "i"},
                    "role": {"$in": ["registry", "central_registry"]}
                }))
            for reg in dir_registries:
                if reg.get("service_number"):
                    notifications_coll.insert_one({
                        "type":          "receipt_forwarded",
                        "applicationId": app["_id"],
                        "referenceId":   app.get("referenceId"),
                        "target":        {"type": "user", "userId": reg.get("service_number")},
                        "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                        "status":        "unread",
                        "readBy":        [],
                        "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": applicant_dir},
                        "createdAt":     datetime.utcnow(),
                        "isActive":      True,
                        "is_active":     True
                    })
        
        # 2. Forward to final approval directorate registry (DOA for Civilians, CDSA for Military)
        final_dir = "DOA" if app.get("role_bucket") == "civilian" else "CDSA"
        final_registries = list(users_coll.find({
            "directorate": {"$regex": f"^{final_dir}$", "$options": "i"},
            "role": {"$in": ["registry", "central_registry"]}
        }))
        if not final_registries:
            final_registries = list(users_coll.find({
                "directorate": {"$regex": f"^{final_dir}$", "$options": "i"},
                "role": {"$in": ["registry", "central_registry"]}
            }))
        for reg in final_registries:
            if reg.get("service_number"):
                notifications_coll.insert_one({
                    "type":          "receipt_forwarded",
                    "applicationId": app["_id"],
                    "referenceId":   app.get("referenceId"),
                    "target":        {"type": "user", "userId": reg.get("service_number")},
                    "message":       f"Leave receipt {receipt_number} for application {app.get('referenceId')} ({app.get('applicantName')}) has been forwarded to you for documentation.",
                    "status":        "unread",
                    "readBy":        [],
                    "meta":          {"receiptNumber": receipt_number, "issuedBy": user_id, "directorate": final_dir},
                    "createdAt":     datetime.utcnow(),
                    "isActive":      True,
                    "is_active":     True
                })
    except Exception as e:
        print(f"Failed to forward receipt notifications to registries: {e}")

    # Send email receipt
    try:
        users_coll = current_app.users_collection
        applicant = users_coll.find_one({"service_number": app.get("applicantId")})
        if not applicant:
            applicant = current_app.user_collection.find_one(
                {"service_number": app.get("applicantId")}
            )
        if applicant and applicant.get("email"):
            from utils.email_service import send_final_approval_email
            send_final_approval_email(applicant, app, receipt_number)
    except Exception as e:
        print(f"Email send failed: {e}")

    # Socket.io notification
    try:
        socketio.emit(
            "new_notification",
            {
                "type":          "receipt_issued",
                "referenceId":   app.get("referenceId"),
                "receiptNumber": receipt_number,
                "message":       f"Your receipt {receipt_number} is ready.",
                "timestamp":     datetime.utcnow().isoformat(),
            },
            room=f"USER_{app.get('applicantId', '').replace('/', '_')}"
        )
    except Exception as e:
        print(f"Socket.IO emit failed: {e}")

    applications_coll.update_one({"_id": ObjectId(app_id)}, update_op)
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": f"Receipt {receipt_number} issued successfully."})
    flash(f"Receipt {receipt_number} issued successfully.", "success")
    return redirect(url_for('approver_dashboard.dashboard_leave_pass'))




@approver_dashboard.route('/acknowledge/<string:app_id>', methods=['POST'])
def acknowledge(app_id):
    """
    Directorate registry acknowledges file receipt.
    This is separate from issue_receipt for clarity.
    """
    current_user = {
        "service_number": session.get("service_number"),
        "fullName":       session.get("name"),
        "directorate":    session.get("directorate"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":          session.get("email"),
    }

    user_id = current_user.get("service_number") or current_user.get("email")
    if not user_id:
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    applications_coll  = current_app.applications_collection

    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        if not app:
            flash("Application not found.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    except Exception:
        flash("Invalid application ID.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # Only allow acknowledgement for approved or issued applications (case-insensitive check)
    app_status = app.get("status", "").lower()
    if app_status not in ("approved", "issued"):
        flash(f"Cannot acknowledge: Application status is '{app.get('status')}'. Only approved or issued applications can be acknowledged.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    chain = app.get("approvalChain", [])

    # Find registry step for this user
    user_step_idx = None
    for i, s in enumerate(chain):
        if s.get("approverId") == user_id and s.get("role") == "registry":
            user_step_idx = i
            break

    if user_step_idx is None:
        flash("You have no registry action on this application.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    user_step = chain[user_step_idx]
    
    # Check if already acknowledged
    if user_step.get("acknowledged", False):
        flash("File has already been acknowledged.", "warning")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    
    # Check if status is approved (should be, but double-check, case-insensitive)
    if user_step.get("status", "").lower() != "approved":
        flash(f"Cannot acknowledge: Step status is '{user_step.get('status')}'.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    comments = request.form.get("comments", "").strip()

    # Update the step
    chain[user_step_idx].update({
        "acknowledged":    True,
        "acknowledgedAt":  datetime.utcnow(),
        "comments":        comments or "File acknowledged and received for documentation",
        "acknowledgedBy":  user_id,
        "acknowledgedByName": current_user.get("fullName") or current_user.get("name"),
    })

    update_op = {
        "$set": {
            "approvalChain": chain,
            "updatedAt":     datetime.utcnow(),
        },
        "$push": {
            "auditTrail": {
                "action":        "file_acknowledged",
                "registry_type": "directorate",
                "by":            user_id,
                "byName":        current_user.get("fullName") or current_user.get("name"),
                "timestamp":     datetime.utcnow(),
                "comments":      comments,
            }
        }
    }

    applications_coll.update_one({"_id": ObjectId(app_id)}, update_op)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "message": "File acknowledged successfully."})
    flash("File acknowledged successfully. Application recorded in registry.", "success")
    return redirect(url_for('approver_dashboard.dashboard_leave_pass'))





import logging


logger = logging.getLogger(__name__)

@approver_dashboard.route('/reject/<string:app_id>', methods=['GET', 'POST'])
def reject(app_id):
    """Reject a pending application by an approver in the chain"""

    # ── 1. Build current user from session ──────────────────────────
    current_user = {
        "service_number": session.get("service_number"),
        "fullName": session.get("name"),
        "directorate": session.get("directorate"),
        "role": session.get("role"),
        "designation": session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade": session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email": session.get("email"),
        "is_so_approver": session.get("is_so_approver", False),
        "is_dd_approver": session.get("is_dd_approver", False),
        "is_ad_approver": session.get("is_ad_approver", False),
        "is_final_approver": session.get("is_final_approver", False),
    }

    print("\n🔍 [REJECT] Current user:", current_user)   # DEBUG

    if not current_user.get("service_number"):
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    applications_coll = current_app.applications_collection
    users_coll = current_app.users_collection
    notifications_coll = current_app.notifications_collection

    # ── 2. Fetch application ──────────────────────────────────────────
    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        if not app:
            flash("Application not found.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
    except Exception as e:
        print(f"❌ Invalid ObjectId: {e}")
        flash("Invalid application ID.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    print(f"📄 [REJECT] App status: {app.get('status')}, directorate: {app.get('directorate')}")

    # ── 3. Get approval chain (CRITICAL FIX) ──────────────────────
    chain = app.get("approvalChain", [])
    if not chain:
        flash("Application has no approval chain.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # ── 4. Validate overall status ───────────────────────────────────
    if app.get("status") != "pending":
        flash(f"Cannot reject: Application is already {app.get('status')}.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    user_id = current_user["service_number"]
    user_directorate = current_user["directorate"]
    is_so_approver = current_user["is_so_approver"]
    is_ad_approver = current_user["is_ad_approver"]
    is_dd_approver = current_user["is_dd_approver"]

    # ── 5. Find the user's pending step ──────────────────────────────
    def find_user_step(chain, user, app):
        """Return (index, step) or (None, None)"""
        for i, step in enumerate(chain):
            if step.get("status") != "pending":
                continue

            # Direct match by approverId (highest priority)
            if step.get("approverId") == user["service_number"]:
                print(f"✅ Direct match: approverId = {step.get('approverId')}")
                return i, step

            # Role‑based matching (only for roles with flags)
            if is_so_approver and step.get("role") == "so" and app.get("directorate") == user["directorate"]:
                print(f"✅ SO role match: {step.get('role')}")
                return i, step
            if is_ad_approver and step.get("role") == "ad" and app.get("directorate") == user["directorate"]:
                print(f"✅ AD role match: {step.get('role')}")
                return i, step
            if is_dd_approver and step.get("role") == "dd" and app.get("directorate") == user["directorate"]:
                print(f"✅ DD role match: {step.get('role')}")
                return i, step

        return None, None

    user_step_index, user_step = find_user_step(chain, current_user, app)
    print(f"🔎 user_step_index = {user_step_index}, user_step = {user_step}")

    if user_step_index is None:
        flash("This application is not waiting for your action.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # ── 6. Ensure all previous steps are approved/recommended ──────
    for i in range(user_step_index):
        if chain[i]["status"] not in ("approved", "Recommended for Approval"):
            flash("Cannot reject: Previous approvals are still pending.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # ── 7. Handle POST (actual rejection) ─────────────────────────
    if request.method == 'POST':
        comments = request.form.get('comments', '').strip()
        print(f"🔍 DEBUG: Received comments = '{comments}' (length: {len(comments)})")

        if not comments:
            flash("Comments are required for rejection.", "error")  # ← Better error message
            return redirect(url_for('approver_dashboard.view_application', app_id=app_id))

        # ── Check if leave was already deducted (case‑insensitive) ──
        current_status = app.get("status", "").lower()
        is_leave_deducted = current_status in ("issued", "approved")
        print(f"💰 is_leave_deducted = {is_leave_deducted} (status = {current_status})")

        if is_leave_deducted:
            print(f"⚠️ Rejecting after leave deducted for {app.get('referenceId')}")
            try:
                from .leave_helper import refund_leave_balance
                refund_success = refund_leave_balance(
                    service_number=app.get("applicantId"),
                    application=app
                )
                if refund_success:
                    print(f"✅ Leave balance refunded for {app.get('applicantId')}")
                else:
                    print(f"⚠️ Failed to refund leave balance for {app.get('applicantId')}")
            except Exception as e:
                print(f"❌ Error refunding leave balance: {e}")
                flash("Error processing leave balance refund. Please check manually.", "error")

        # ── Update the step in the chain ──────────────────────────────
        chain[user_step_index].update({
            "status": "rejected",
            "comments": comments,
            "timestamp": datetime.utcnow()
        })

        # Ensure approver properties are set
        if not chain[user_step_index].get("approverId"):
            chain[user_step_index]["approverId"] = user_id
        if not chain[user_step_index].get("approverName"):
            chain[user_step_index]["approverName"] = current_user.get("fullName") or current_user.get("name")
        if not chain[user_step_index].get("approverRank"):
            chain[user_step_index]["approverRank"] = current_user.get("rankOrGrade")
        if not chain[user_step_index].get("approverDesignation"):
            chain[user_step_index]["approverDesignation"] = current_user.get("designation")

        # ── Update whole application ──────────────────────────────────
        
        
        result = applications_coll.update_one(
            {"_id": ObjectId(app_id)},
            {"$set": {
                "approvalChain": chain,
                "status": "rejected",
                "updatedAt": datetime.utcnow()
            }}
        )
        print(f"📝 Update result: matched={result.matched_count}, modified={result.modified_count}")

        if result.modified_count == 0:
            flash("Failed to update application. Please try again.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

        print(f"✅ Application {app.get('referenceId')} rejected by {user_id}")

        # ── Send rejection email ──────────────────────────────────────
        applicant = users_coll.find_one({"service_number": app.get("applicantId")})
        if applicant and applicant.get("email"):
            try:
                from utils.email_service import send_rejection_email
                send_rejection_email(
                    applicant_email=applicant["email"],
                    applicant_name=applicant.get("fullName"),
                    application=app,
                    rejected_by=current_user.get("name"),
                    comments=comments
                )
            except Exception as e:
                print(f"❌ Email sending failed: {e}")

        # ── Socket.IO event ───────────────────────────────────────────
        try:
            current_app.socketio.emit(
                "application_update",
                {
                    "status": "rejected",
                    "comments": comments,
                    "referenceId": app.get("referenceId"),
                    "rejected_by": current_user.get("name"),
                    "rejected_step": user_step.get("role"),
                    "timestamp": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                },
                room=f"APPLICATION_{app.get('referenceId')}"
            )
        except Exception as e:
            print(f"❌ Socket.IO emit failed: {e}")

        # ── In‑app notification for applicant ────────────────────────
        notification = {
            "type": "application_rejected",
            "applicationId": app["_id"],
            "referenceId": app.get("referenceId"),
            "applicantId": app.get("applicantId"),
            "target": {
                "type": "user",
                "userId": app.get("applicantId")
            },
            "message": f"Your application {app.get('referenceId')} has been rejected by {current_user.get('name')}. Reason: {comments}",
            "status": "unread",
            "readBy": [],
            "meta": {
                "rejectedBy": user_id,
                "rejectedByName": current_user.get("name"),
                "rejectedAt": datetime.utcnow(),
                "rejectedStep": user_step.get("role"),
                "comments": comments,
                "leaveRefunded": is_leave_deducted
            },
            "createdAt": datetime.utcnow(),
            "isActive": True
        }
        notifications_coll.insert_one(notification)

        # ── Return response ────────────────────────────────────────────
        # ── Return response ────────────────────────────────────────────────
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            msg = "Application rejected. Leave balance has been refunded." if is_leave_deducted else "Application rejected."
            return jsonify({"success": True, "message": msg})

        flash("Application rejected." + (" Leave balance refunded." if is_leave_deducted else ""), "info")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # ── 8. GET request – show a confirmation page or redirect ──────
    flash("Please use the form to submit rejection comments.", "warning")
    return redirect(url_for('approver_dashboard.dashboard_leave_pass'))



















def calendar_days_between(start_date, end_date):
    """Calculate calendar days between two dates."""
    if not start_date or not end_date:
        return 0
    if isinstance(start_date, dict) and "$date" in start_date:
        start_date = datetime.fromisoformat(start_date["$date"].replace("Z", "+00:00"))
    if isinstance(end_date, dict) and "$date" in end_date:
        end_date = datetime.fromisoformat(end_date["$date"].replace("Z", "+00:00"))
    return (end_date - start_date).days + 1



@approver_dashboard.route('/view/<string:app_id>')
def view_application(app_id):

    current_user = {
        "service_number": session.get("service_number"),
        "name":           session.get("name"),
        "directorate":     session.get("directorate"),
        "role":            session.get("role"),
        "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
        "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        "email":           session.get("email")
    }

    # Read which listing page the user came from
    came_from = request.args.get('ref', 'dashboard_leave_pass')

    if not current_user:
        flash("Session expired.", "error")
        return redirect(url_for('login'))

    # Fetch the application
    app = current_app.applications_collection.find_one({"_id": ObjectId(app_id)})
    if not app:
        flash("Application not found", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))

    # Get applicant name
    applicant = current_app.users_collection.find_one(
        {"service_number": app["applicantId"]},
        {"name": 1}
    )
    app['applicantName'] = applicant['name'] if applicant else "Unknown"

    # Simple date formatter
    def format_date(date_field):
        if not date_field:
            return None
        
        # If it's a MongoDB date object
        if isinstance(date_field, dict) and '$date' in date_field:
            try:
                # Extract ISO string and convert to datetime
                iso_str = date_field['$date']
                # Handle Z timezone
                if iso_str.endswith('Z'):
                    iso_str = iso_str[:-1] + '+00:00'
                dt = datetime.fromisoformat(iso_str)
                return dt.strftime('%d %b %Y')
            except:
                return str(date_field)
        
        # If it's already a datetime
        if isinstance(date_field, datetime):
            return date_field.strftime('%d %b %Y')
        
        # If it's a string
        if isinstance(date_field, str):
            return date_field
        
        return str(date_field)


    # Format main dates
    app['startDate'] = format_date(app.get('startDate'))
    app['endDate'] = format_date(app.get('endDate'))
    app['effectiveDate'] = format_date(app.get('effectiveDate'))
    app['createdAt'] = format_date(app.get('createdAt'))

    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])

    return render_template('application_detail.html', 
                           application=app, 
                           user=current_user,
                           permissions=user_allowed_features,  
                           active_page=came_from
                           )



# serve/download the attachment
@approver_dashboard.route('/attachment/<attachment_id>')
def serve_attachment(attachment_id):
    try:
        fs = current_app.fs
        file = fs.get(ObjectId(attachment_id))
        return send_file(
            file,
            download_name=file.filename,
            mimetype=file.content_type,
            as_attachment=False   # or True to force download
        )
    except Exception:
        flash("Attachment not found or access denied.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))




@approver_dashboard.route('/applicant-previous-leaves', methods=['GET'])
def applicant_history():
    current_user = {
            "service_number":  session.get("service_number"),
            "fullName":        session.get("name"),
            "directorate":     session.get("directorate"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "email":           session.get("email"),
            "is_so_approver":  session.get("is_so_approver", False),
            "is_dd_approver":  session.get("is_dd_approver", False),
            "is_ad_approver":  session.get("is_ad_approver", False),
            "is_final_approver": session.get("is_final_approver", False),
        }
    
    if not current_user.get("service_number"):
        flash("Session expired.", "error")
        return redirect(url_for('login'))
    
    try:
        applicant_id = request.args.get('applicant_id')
        if not applicant_id:
            return jsonify({"success": False, "error": "No applicant ID provided"}), 400
            
        print(f"Searching for applicantId: '{applicant_id}'")  # Debug log
        
        six_months_ago = datetime.utcnow() - timedelta(days=180)
        
        # Query the database
        apps_cursor = current_app.applications_collection.find({
            "applicantId": applicant_id,
            "status": {"$in": ["approved", "issued", "rejected"]},
            "createdAt": {"$gte": six_months_ago}
        }).sort("createdAt", -1).limit(12)
        
        apps = list(apps_cursor)
        print(f"Found {len(apps)} applications")  # Debug log
        
        applications = []
        for app in apps:
            dates = app.get("dates", {})
            
            # Handle finalApproval field - convert datetime objects to strings for JSON
            final_approval = app.get("finalApproval", {})
            if final_approval:
                # Convert timestamp if it exists
                if final_approval.get("timestamp"):
                    if hasattr(final_approval["timestamp"], 'strftime'):
                        final_approval["timestamp"] = final_approval["timestamp"].strftime('%d %b %Y %H:%M:%S')
                
                # Convert receipt issuedDate if it exists
                if final_approval.get("receipt") and final_approval["receipt"].get("issuedDate"):
                    issued_date = final_approval["receipt"]["issuedDate"]
                    if hasattr(issued_date, 'strftime'):
                        final_approval["receipt"]["issuedDate"] = issued_date.strftime('%d %b %Y')
            
            applications.append({
                "referenceId": app.get("referenceId", "N/A"),  # ADD THIS
                "leave_type": app.get("leave_type", "Unknown"),
                "status": app.get("status"),
                "dates": {
                    "effectiveDate": dates.get("effectiveDate").strftime('%d %b %Y') 
                        if dates.get("effectiveDate") else None,
                    "endDate": dates.get("endDate").strftime('%d %b %Y') 
                        if dates.get("endDate") else None
                },
                "numberOfDays": app.get("numberOfDays"),
                "createdAt": app.get("createdAt").strftime('%d %b %Y') 
                    if app.get("createdAt") else None,
                "reason": app.get("reason"),
                "approvalChain": app.get("approvalChain", []),
                "finalApproval": final_approval  # ADD THIS - CRITICAL FOR so1_doa
            })
        
        return jsonify({
            "success": True,
            "applications": applications
        })
        
    except Exception as e:
        print(f"Error in applicant_history for ID {applicant_id}:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500



@approver_dashboard.route('/view_receipt/<string:app_id>')
def view_receipt(app_id):
    """Display receipt HTML page"""

    current_user = {
            "service_number":  session.get("service_number"),
            "fullName":        session.get("name"),
            "directorate":     session.get("directorate"),
            "role":            session.get("role"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "email":           session.get("email"),
            "is_so_approver":  session.get("is_so_approver", False),
            "is_dd_approver":  session.get("is_dd_approver", False),
            "is_ad_approver":  session.get("is_ad_approver", False),
            "is_final_approver": session.get("is_final_approver", False),
        }
    
    if not current_user.get("service_number"):
        flash("Session expired.", "error")
        return redirect(url_for('login'))
    
    try:
        from datetime import datetime as dt
        
        applications_coll = current_app.applications_collection
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        if not app:
            flash("Application not found.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
        # Check if application has receipt issued
        if app.get('status') != 'issued':
            flash("Receipt not available - application not issued.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
        # Check if user has permission to view this receipt
        current_user = {
            "service_number": session.get("service_number"),
            "fullName": session.get("name"),
            "directorate": session.get("directorate"),
            "role": session.get("role"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "email": session.get("email")
        }
        user_roles = current_user.get('role', [])
        user_id = current_user.get('service_number')
        
        has_permission = False
        
        # Super admin can view all
        if "super_admin" in user_roles:
            has_permission = True
        
        # registry can view if they were involved
        elif "registry" in user_roles:
            for step in app.get('approvalChain', []):
                if step.get('role') == 'registry' and step.get('approverId') == user_id:
                    has_permission = True
                    break
        
        # so1_doa / director_doa can view any receipt
        elif "so1_doa" in user_roles or ("director" in user_roles and session.get("is_final_approver") is True):
            has_permission = True
        
        # central_registry can view
        elif "central_registry" in user_roles:
            has_permission = True
        
        # Applicant can view their own receipt
        elif app.get('applicantId') == user_id:
            has_permission = True
        
        # Director/CDSA can view receipts from their directorate
        elif "director" in user_roles or "cdsa" in user_roles:
            if app.get('directorate') == current_user.get('directorate'):
                has_permission = True
        
        if not has_permission:
            flash("You are not authorized to view this receipt.", "error")
            return redirect(url_for('approver_dashboard.dashboard_leave_pass'))
        
        # Helper function to convert MongoDB date to Python datetime
        def convert_date(date_value):
            if not date_value:
                return None
            if isinstance(date_value, dt):
                return date_value
            if isinstance(date_value, dict) and '$date' in date_value:
                try:
                    from datetime import datetime
                    date_str = date_value['$date']
                    if 'Z' in date_str:
                        date_str = date_str.replace('Z', '+00:00')
                    return datetime.fromisoformat(date_str)
                except:
                    return None
            return date_value
        
        # Extract receipt information from approval chain
        receipt_info = None
        receipt_step = None
        director_step = None
        
        # Find relevant steps
        for step in app.get('approvalChain', []):
            if step.get('role') == 'director' and step.get('status') == 'approved':
                if step.get('is_final_approver') == True:
                    receipt_step = step
                    receipt_info = step.get('receipt')
                    director_step = step
                else:
                    director_step = step
            elif step.get('role') in ('so1_doa', 'central_registry') and step.get('receipt'):
                receipt_step = step
                receipt_info = step.get('receipt')
        
        # Use director as final approver
        final_approver = director_step.get('approverName', 'N/A') if director_step else 'N/A'
        final_approver_rank = director_step.get('approverRank', 'N/A') if director_step else 'N/A'
        final_approval_date = convert_date(director_step.get('timestamp')) if director_step else None
        
        # Receipt issuer info
        receipt_issuer_name = receipt_info.get('issuedByName', 'N/A') if receipt_info else 'N/A'
        receipt_issuer_rank = receipt_step.get('approverRank', 'N/A') if receipt_step else 'N/A'
        receipt_issue_date = convert_date(receipt_info.get('issuedDate')) if receipt_info else None
        receipt_number = receipt_info.get('receiptNumber') if receipt_info else app.get('receiptNumber', 'N/A')
        
        # Convert dates in application
        start_date = convert_date(app.get('startDate'))
        end_date = convert_date(app.get('endDate'))
        
        # Prepare receipt data for template
        receipt_data = {
            'receipt_number': receipt_number,
            'issued_date': receipt_issue_date,
            'issued_by': receipt_issuer_name,
            'issued_by_rank': receipt_issuer_rank,
            'final_approver': final_approver,
            'final_approver_rank': final_approver_rank,
            'final_approval_date': final_approval_date,
            'approving_authority': 'Director',
            'application': app,
            'start_date': start_date,
            'end_date': end_date,
            'user': current_user
        }
        
        return render_template('receipt.html', receipt_data=receipt_data)
        
    except Exception as e:
        print(f"Error viewing receipt: {e}")
        import traceback
        traceback.print_exc()
        flash("Error viewing receipt.", "error")
        return redirect(url_for('approver_dashboard.dashboard_leave_pass'))


@approver_dashboard.route('/download_receipt/<string:app_id>')
# @login_required
def download_receipt(app_id):
    """Download receipt as PDF with logo and ranks"""
    try:
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
        from io import BytesIO
        import os
        
        applications_coll = current_app.applications_collection
        users_coll = current_app.users_collection
        
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        
        if not app:
            return "Application not found.", 404
        
        # Check if application is issued and has receipt
        if app.get('status') != 'issued':
            return "Receipt not available - application not issued.", 404
        
        # Find receipt in approval chain and extract all information from the chain
        receipt_info = None
        receipt_step = None
        director_step = None
        so1_doa_step = None
        
        # Iterate through approval chain to find all relevant steps
        for step in app.get('approvalChain', []):
            if step.get('role') == 'director' and step.get('status') == 'approved':
                if step.get('is_final_approver') == True:
                    receipt_step = step
                    receipt_info = step.get('receipt')
                    director_step = step
                else:
                    director_step = step
            elif step.get('role') in ('so1_doa', 'central_registry') and step.get('receipt'):
                receipt_step = step
                receipt_info = step.get('receipt')
        
        if not receipt_info:
            return "Receipt not found.", 404
        
        # Extract rank information from the steps (already stored in the chain)
        # For Final Approved By - use Director's info (they approved first)
        final_approver_name = director_step.get('approverName', 'N/A') if director_step else 'N/A'
        final_approver_rank = director_step.get('approverRank', 'N/A') if director_step else 'N/A'
        final_approval_date = director_step.get('timestamp') if director_step else None
        
        # For Receipt Issued By - use SO1-DOA/Central Registry info
        receipt_issuer_name = receipt_info.get('issuedByName', 'N/A')
        receipt_issuer_rank = receipt_step.get('approverRank', 'N/A') if receipt_step else 'N/A'
        receipt_issue_date = receipt_info.get('issuedDate')
        
        # Get applicant rank
        applicant_rank = app.get('rank', 'N/A')
        if applicant_rank == 'N/A':
            applicant_rank = app.get('rankOrGrade', 'N/A')
        
        # Create PDF buffer
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.7*inch, bottomMargin=0.5*inch, leftMargin=0.7*inch, rightMargin=0.7*inch)
        styles = getSampleStyleSheet()
        
        # Create custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a3a5c'),
            alignment=TA_CENTER,
            spaceAfter=5
        )
        
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor('#4a5568'),
            alignment=TA_CENTER,
            spaceAfter=20
        )
        
        label_style = ParagraphStyle(
            'Label',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#4a5568'),
            fontName='Helvetica-Bold'
        )
        
        value_style = ParagraphStyle(
            'Value',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.black
        )
        
        # Build story elements
        story = []
        
        # Add Logo
        logo_path = os.path.join(current_app.root_path, 'static', 'images', 'dsa2.png')
        if os.path.exists(logo_path):
            try:
                logo = Image(logo_path, width=1.2*inch, height=1.2*inch)
                logo.hAlign = 'CENTER'
                story.append(logo)
                story.append(Spacer(1, 0.1*inch))
            except Exception as e:
                print(f"Error adding logo: {e}")
        
        # Header
        story.append(Paragraph("DIRECTORATE OF ADMINISTRATION (DOA)", title_style))
        story.append(Paragraph("LEAVE/PASS APPROVAL", subtitle_style))
        
        # Receipt number box
        receipt_number = receipt_info.get('receiptNumber', 'N/A')
        
        # Create receipt number as a table for better styling
        receipt_table_data = [[Paragraph(f"<b>RECEIPT NUMBER: {receipt_number}</b>", 
                                         ParagraphStyle('ReceiptText', parent=styles['Normal'], 
                                                       fontSize=12, textColor=colors.HexColor('#e53e3e'),
                                                       alignment=TA_CENTER))]]
        receipt_table = Table(receipt_table_data, colWidths=[5*inch])
        receipt_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff5f5')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e53e3e')),
        ]))
        story.append(receipt_table)
        story.append(Spacer(1, 0.3*inch))
        
        # Format dates helper
        def format_date(date_value):
            if not date_value:
                return 'N/A'
            if isinstance(date_value, datetime):
                return date_value.strftime('%d %B, %Y')
            if isinstance(date_value, str):
                try:
                    from dateutil import parser
                    dt = parser.parse(date_value)
                    return dt.strftime('%d %B, %Y')
                except:
                    return date_value
            return 'N/A'
        
        def format_datetime(dt_value):
            if not dt_value:
                return 'N/A'
            if isinstance(dt_value, datetime):
                return dt_value.strftime('%d %B, %Y at %I:%M %p')
            if isinstance(dt_value, str):
                try:
                    from dateutil import parser
                    dt = parser.parse(dt_value)
                    return dt.strftime('%d %B, %Y at %I:%M %p')
                except:
                    return dt_value
            return 'N/A'
        
        # Format applicant rank
        applicant_rank = app.get('rankOrGrade') or app.get('rank') or app.get('designation', 'N/A')
        if applicant_rank == 'N/A':
            # Try to fetch from user collection
            applicant_id = app.get('applicantId')
            if applicant_id:
                applicant_user = current_app.users_collection.find_one({"$or": [{"service_number": applicant_id}, {"email": applicant_id}]})
                if applicant_user:
                    applicant_rank = applicant_user.get('rank') or applicant_user.get('rankOrGrade') or applicant_user.get('designation', 'N/A')
        
        # Application details table
        details_data = [
            [Paragraph("<b>APPLICATION DETAILS</b>", styles['Normal']), ""],
            ["Reference ID:", app.get('referenceId', 'N/A')],
            ["Applicant Name:", f"{app.get('applicantName', 'N/A')}"],
            ["Service Number:", app.get('applicantId', 'N/A')],
            ["Rank:", applicant_rank],
            ["Directorate:", app.get('directorate', 'N/A')],
            ["Leave Type:", app.get('leave_type', 'N/A').title()],
            ["Number of Days:", str(app.get('numberOfDays', 'N/A'))],
            ["Start Date:", format_date(app.get('startDate'))],
            ["End Date:", format_date(app.get('endDate'))],
        ]
        
        # Create table
        table = Table(details_data, colWidths=[1.5*inch, 4*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('SPAN', (0, 0), (1, 0)),
            ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#e2e8f0')),
            ('TEXTCOLOR', (0, 0), (1, 0), colors.HexColor('#1a3a5c')),
            ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (1, 0), 12),
            ('ALIGN', (0, 0), (1, 0), 'CENTER'),
        ]))
        
        story.append(table)
        story.append(Spacer(1, 0.3*inch))

        # Approval details with ranks (using data directly from approvalChain)
        approval_data = [
            [Paragraph("<b>APPROVAL INFORMATION</b>", styles['Normal']), ""],
            ["Final Approved By:", f"{final_approver_name} ({final_approver_rank})" if final_approver_rank != 'N/A' else final_approver_name],
            ["Approving Authority:", "Director"],
            ["Approval Date:", format_datetime(final_approval_date)],
            ["", ""],
            ["Leave/Pass Issued By:", f"{receipt_issuer_name} ({receipt_issuer_rank})" if receipt_issuer_rank != 'N/A' else receipt_issuer_name],
            ["Issuing Authority:", "Director"],
            ["Issue Date:", format_datetime(receipt_issue_date)],
        ]
        
        approval_table = Table(approval_data, colWidths=[1.5*inch, 4*inch])
        approval_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('SPAN', (0, 0), (1, 0)),
            ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#e2e8f0')),
            ('TEXTCOLOR', (0, 0), (1, 0), colors.HexColor('#1a3a5c')),
            ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (1, 0), 12),
            ('ALIGN', (0, 0), (1, 0), 'CENTER'),
        ]))
        
        story.append(approval_table)
        story.append(Spacer(1, 0.3*inch))
        
        # Comments if any
        comments = app.get('comments') or receipt_info.get('comments')
        if comments:
            comments_style = ParagraphStyle(
                'Comments',
                parent=styles['Normal'],
                fontSize=10,
                textColor=colors.HexColor('#4a5568'),
                leftIndent=20,
                rightIndent=20
            )
            story.append(Paragraph("<b>Comments:</b>", styles['Normal']))
            story.append(Paragraph(comments, comments_style))
            story.append(Spacer(1, 0.2*inch))
        
        # Footer note
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#718096'),
            alignment=TA_CENTER,
            spaceBefore=20
        )
        
        story.append(Paragraph(
            "This is a computer-generated receipt and does not require a signature. "
            "Please present this receipt when requested by relevant authorities.",
            footer_style
        ))
        
        # Add QR code or stamp area (optional)
        story.append(Spacer(1, 0.2*inch))
        stamp_style = ParagraphStyle(
            'Stamp',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#a0aec0'),
            alignment=TA_CENTER
        )
        story.append(Paragraph("Digitally Generated Document", stamp_style))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        
        # Create response
        response = make_response(buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        filename = f"receipt_{app.get('referenceId', app_id)}.pdf"
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except Exception as e:
        print(f"Error downloading receipt: {e}")
        import traceback
        traceback.print_exc()
        return f"Error generating receipt: {str(e)}", 500



        

@approver_dashboard.route('/email_receipt_self/<string:app_id>')
# @login_required
def email_receipt_self(app_id):
    """Email receipt to the current user"""
    try:
        current_user = {
            "service_number": session.get("service_number"),
            "fullName": session.get("name"),
            "directorate": session.get("directorate"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "email": session.get("email")
        }
        applications_coll = current_app.applications_collection
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
        
        if not app:
            return jsonify({'success': False, 'message': 'Application not found'}), 404
        
        # Check if user is registry involved in this application
        user_involved = False
        if "registry" in current_user.get('role', []):
            for step in app.get('approvalChain', []):
                if step['role'] == 'registry' and step['approverId'] == current_user['service_number']:
                    user_involved = True
                    break
        
        if not user_involved and "so1_doa" not in current_user.get('role', []):
            return jsonify({'success': False, 'message': 'Not authorized'}), 403
        
        # Send email
        if current_user.get('email'):
            send_chief_clerk_receipt_notification(
                current_user['email'],
                current_user['fullName'],
                app['finalApproval']['receipt']['receiptNumber'],
                app_id,
                app,
                current_user
            )
            return jsonify({'success': True, 'message': 'Receipt sent to your email'})
        else:
            return jsonify({'success': False, 'message': 'Email not found in your profile'}), 400
            
    except Exception as e:
        print(f"Error emailing receipt: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
    

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import os
from threading import Thread

def send_chief_clerk_receipt_notification(chief_clerk_email, chief_clerk_name, receipt_no, app_id, app, so1_doa_user):
    """
    Send immediate receipt notification to registry
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = chief_clerk_email
        msg['Subject'] = f"🚨 FINAL APPROVAL RECEIPT - {receipt_no}"
        
        # Create email body with all details
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd;">
                <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                    <h2 style="color: #2c3e50; margin: 0;">📋 FINAL APPROVAL CONFIRMATION</h2>
                    <p style="color: #666; margin: 5px 0;">DSA Pass/Leave System - Official Receipt</p>
                </div>
                
                <div style="margin-bottom: 20px;">
                    <div style="background-color: #e8f5e9; padding: 15px; border-radius: 5px; border-left: 4px solid #4caf50;">
                        <h3 style="color: #2e7d32; margin: 0 0 10px 0;">
                            ✅ Application FINALLY APPROVED by so1_doa
                        </h3>
                        <p style="margin: 0;">
                            <strong>Receipt Number:</strong> {receipt_no}<br>
                            <strong>Status:</strong> <span style="color: #4caf50; font-weight: bold;">ISSUED</span>
                        </p>
                    </div>
                </div>
                
                <div style="margin-bottom: 20px;">
                    <h3 style="color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px;">
                        📄 Application Details
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Application ID:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{app_id}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Applicant:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{app['applicantName']} ({app['applicantId']})</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Type:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{app['type'].title()}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Directorate:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{app['directorate']}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Period:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">
                                {app['details']['startDate'].strftime('%d-%b-%Y')} to {app['details']['endDate'].strftime('%d-%b-%Y')}
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Days:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{app['details']['numberOfDays']} days</td>
                        </tr>
                    </table>
                </div>
                
                <div style="margin-bottom: 20px;">
                    <h3 style="color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px;">
                        👤 Approval Details
                    </h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Final Approved By:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{so1_doa_user.get('name')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Designation:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{so1_doa_user.get('designation', 'so1_doa')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Approval Date & Time:</strong></td>
                            <td style="padding: 8px; border-bottom: 1px solid #eee;">{datetime.utcnow().strftime('%d-%b-%Y %H:%M UTC')}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px;"><strong>Your Action:</strong></td>
                            <td style="padding: 8px;">
                                You forwarded this application on: 
                                {app['approvalChain'][-1].get('forwardedAt', '').strftime('%d-%b-%Y %H:%M') if isinstance(app['approvalChain'][-1].get('forwardedAt'), datetime.datetime) else 'N/A'}
                            </td>
                        </tr>
                    </table>
                </div>
                
                <div style="background-color: #f1f8ff; padding: 15px; border-radius: 5px; border-left: 4px solid #2196f3;">
                    <h4 style="color: #1565c0; margin: 0 0 10px 0;">📋 Administrative Action Required:</h4>
                    <ul style="margin: 0; padding-left: 20px;">
                        <li>File this receipt in official records</li>
                        <li>Update physical register if applicable</li>
                        <li>Notify relevant administrative staff</li>
                        <li>Process any associated paperwork</li>
                    </ul>
                </div>
                
                <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; text-align: center; color: #666;">
                    <p style="margin: 5px 0;">
                        <strong>Access in System:</strong> 
                        <a href="{request.host_url}dashboard_leave_pass" style="color: #2196f3;">
                            Go to Dashboard
                        </a>
                    </p>
                    <p style="margin: 5px 0; font-size: 12px;">
                        This is an auto-generated receipt. Valid only with official stamp.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        # Send email
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.sendmail(app.config['MAIL_USERNAME'], chief_clerk_email, msg.as_string())
        server.quit()
        
        print(f"[OK] Receipt sent to registry: {chief_clerk_email}")
        return True
        
    except Exception as e:
        print(f"[ERROR] Error sending to registry {chief_clerk_email}: {e}")
        return False

def send_applicant_receipt_notification(applicant_email, applicant_name, receipt_no, app_id, app, so1_doa_user):
    """
    Send receipt notification to applicant
    """
    try:
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = applicant_email
        msg['Subject'] = f"✅ Your {app['type'].title()} Approved - {receipt_no}"
        
        body = f"""
        Dear {applicant_name},
        
        GOOD NEWS! Your {app['type']} application has been **FINAL APPROVED**.
        
        📋 APPROVAL DETAILS:
        • Receipt Number: {receipt_no}
        • Application ID: {app_id}
        • Type: {app['type'].title()}
        • Directorate: {app['directorate']}
        • Period: {app['details']['startDate'].strftime('%d-%b-%Y')} to {app['details']['endDate'].strftime('%d-%b-%Y')}
        • Days: {app['details']['numberOfDays']}
        • Approved By: {so1_doa_user['fullName']} (so1_doa)
        • Approval Date: {datetime.utcnow().strftime('%d-%b-%Y %H:%M UTC')}
        
        This receipt serves as official confirmation. Please:
        1. Keep this email for your records
        2. Present it if required for verification
        3. Download the receipt from the application portal
        
        You can access your receipt here: {request.host_url}view_receipt/{app_id}
        
        Regards,
        DSA Pass/Leave System
        Directorate of Space Administration
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.starttls()
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.sendmail(app.config['MAIL_USERNAME'], applicant_email, msg.as_string())
        server.quit()
        
        print(f"[OK] Receipt sent to Applicant: {applicant_email}")
        return True
        
    except Exception as e:
        print(f"[ERROR] Error sending to Applicant {applicant_email}: {e}")
        return False


@approver_dashboard.route('/api/leave-return/submit/<string:app_id>', methods=['POST'])
def submit_leave_return(app_id):
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    applications_coll = current_app.applications_collection
    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
    except Exception:
        return jsonify({"status": "error", "message": "Invalid Application ID"}), 400
        
    if not app:
        return jsonify({"status": "error", "message": "Application not found"}), 404
        
    user_email = session.get('user_email')
    
    # Find user profile
    client = MongoClient("mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "DSM")
    db = client[db_name]
    user_data = db.users.find_one({"email": user_email})
    if not user_data:
        return jsonify({"status": "error", "message": "User not found"}), 404
        
    applicant_id = app.get("applicantId")
    # Verify applicant owns this leave record
    if applicant_id != user_data.get("service_number") and applicant_id != user_email:
        return jsonify({"status": "error", "message": "Only the applicant can report return from leave"}), 403
        
    data = request.get_json() or {}
    actual_return_str = data.get("actual_return_date")
    if not actual_return_str:
        return jsonify({"status": "error", "message": "Actual return date is required"}), 400
        
    try:
        actual_return_date = datetime.strptime(actual_return_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid date format, use YYYY-MM-DD"}), 400
        
    end_date = app.get("endDate")
    if isinstance(end_date, dict) and "$date" in end_date:
        end_date = datetime.fromisoformat(end_date["$date"].replace("Z", "+00:00")).replace(tzinfo=None)
        
    if not isinstance(end_date, datetime):
        end_date = datetime.utcnow()
        
    expected_returned_date = end_date + timedelta(days=1)
    
    actual_date_only = actual_return_date.date()
    expected_date_only = expected_returned_date.date()
    
    if actual_date_only < expected_date_only:
        returnedresult = "returned early"
    elif actual_date_only == expected_date_only:
        returnedresult = "returned on time"
    else:
        returnedresult = "overstayed"
        
    leave_pass_returned = {
        "returned_from_leave": False,
        "actual_returned_date": actual_return_date,
        "expected_returned_date": expected_returned_date,
        "returnedresult": returnedresult,
        "returned_status": "pending"
    }
    
    applications_coll.update_one(
        {"_id": ObjectId(app_id)},
        {"$set": {"leave_pass_returned": leave_pass_returned}}
    )
    
    directorate = app.get("directorate")
    directors = list(db.users.find({"role": "director", "directorate": directorate}))
    notifications_coll = current_app.notifications_collection
    
    for director in directors:
        notifications_coll.insert_one({
            "type": "leave_return_approval",
            "applicationId": ObjectId(app_id),
            "referenceId": app.get("referenceId"),
            "target": {
                "type": "user",
                "email": director.get("email")
            },
            "message": f"{app.get('applicantName')} reported return from leave ({returnedresult}). Awaiting your approval.",
            "status": "unread",
            "isActive": True,
            "createdAt": datetime.utcnow()
        })
        
    return jsonify({"status": "success", "message": "Return report submitted successfully"})


@approver_dashboard.route('/api/leave-return/approve/<string:app_id>', methods=['POST'])
def approve_leave_return(app_id):
    if 'user_email' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    user_role = session.get('role', 'civilian')
    if user_role != 'director':
        return jsonify({"status": "error", "message": "Only the Directorate Director can approve leave returns"}), 403
        
    applications_coll = current_app.applications_collection
    try:
        app = applications_coll.find_one({"_id": ObjectId(app_id)})
    except Exception:
        return jsonify({"status": "error", "message": "Invalid Application ID"}), 400
        
    if not app:
        return jsonify({"status": "error", "message": "Application not found"}), 404
        
    leave_pass_returned = app.get("leave_pass_returned")
    if not leave_pass_returned or leave_pass_returned.get("returned_status") != "pending":
        return jsonify({"status": "error", "message": "No pending return report for this application"}), 400
        
    actual_return_date = leave_pass_returned.get("actual_returned_date")
    end_date = app.get("endDate")
    
    if isinstance(actual_return_date, dict) and "$date" in actual_return_date:
        actual_return_date = datetime.fromisoformat(actual_return_date["$date"].replace("Z", "+00:00")).replace(tzinfo=None)
    if isinstance(end_date, dict) and "$date" in end_date:
        end_date = datetime.fromisoformat(end_date["$date"].replace("Z", "+00:00")).replace(tzinfo=None)
        
    returnedresult = leave_pass_returned.get("returnedresult")
    
    if returnedresult == "returned early" and isinstance(actual_return_date, datetime) and isinstance(end_date, datetime):
        leave_type = app.get("leave_type")
        applicant_id = app.get("applicantId")
        year = actual_return_date.year
        
        client = MongoClient("mongodb://localhost:27017/")
        db_name = os.getenv("DATABASE_NAME", "DSM")
        db = client[db_name]
        leave_balances_coll = db.leave_balances
        balance = leave_balances_coll.find_one({"serviceNumber": applicant_id, "year": year})
        
        if balance:
            from .leave_helper import get_public_holidays
            public_holidays = get_public_holidays(year)
            
            if leave_type == "casual":
                unused_days = (end_date - actual_return_date).days + 1
            else:
                from .leave_logic import working_days_between
                unused_days = working_days_between(actual_return_date, end_date, public_holidays)
                
            if unused_days > 0:
                update_fields = {}
                notes = balance.get("notes", [])
                
                if leave_type == "annual":
                    update_fields["annualRemaining"] = balance.get("annualRemaining", 0) + unused_days
                    notes.append(f"{datetime.utcnow().isoformat()}: Credited - {leave_type} - {unused_days} days (Early Return)")
                    
                elif leave_type == "casual":
                    new_used = max(0, balance.get("casualCalendarDaysUsed", 0) - unused_days)
                    update_fields["casualCalendarDaysUsed"] = new_used
                    update_fields["casualCalendarDaysRemaining"] = min(7, 7 - new_used)
                    notes.append(f"{datetime.utcnow().isoformat()}: Credited - {leave_type} - {unused_days} days (Early Return)")
                    
                elif leave_type == "compassionate":
                    update_fields["compassionateUsed"] = max(0, balance.get("compassionateUsed", 0) - unused_days)
                    update_fields["compassionateRemaining"] = balance.get("compassionateRemaining", 10) + unused_days
                    notes.append(f"{datetime.utcnow().isoformat()}: Credited - {leave_type} - {unused_days} days (Early Return)")
                    
                elif leave_type == "sick":
                    update_fields["sickThisYear"] = max(0, balance.get("sickThisYear", 0) - unused_days)
                    update_fields["sickThisYearRemaining"] = min(21, balance.get("sickThisYearRemaining", 21) + unused_days)
                    update_fields["sickRolling12m"] = max(0, balance.get("sickRolling12m", 0) - unused_days)
                    update_fields["sickRollingRemaining"] = min(42, balance.get("sickRollingRemaining", 42) + unused_days)
                    notes.append(f"{datetime.utcnow().isoformat()}: Credited - {leave_type} - {unused_days} days (Early Return)")
                    
                if update_fields:
                    update_fields["notes"] = notes
                    update_fields["updatedAt"] = datetime.utcnow()
                    leave_balances_coll.update_one({"_id": balance["_id"]}, {"$set": update_fields})
                    print(f"[OK] Credited back {unused_days} days to {applicant_id} for early return.")

    applications_coll.update_one(
        {"_id": ObjectId(app_id)},
        {
            "$set": {
                "leave_pass_returned.returned_from_leave": True,
                "leave_pass_returned.returned_status": "approved"
            },
            "$push": {
                "auditTrail": {
                    "action": "Return Approved",
                    "actor": session.get("name"),
                    "timestamp": datetime.utcnow()
                }
            }
        }
    )
    
    # Notify applicant
    client = MongoClient("mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "DSM")
    db = client[db_name]
    notifications_coll = db.notifications
    
    # Get applicant user record to get their email
    applicant_email = app.get("applicantId") if "@" in str(app.get("applicantId")) else None
    if not applicant_email:
        applicant_user = db.users.find_one({"service_number": app.get("applicantId")})
        if applicant_user:
            applicant_email = applicant_user.get("email")
            
    if applicant_email:
        notifications_coll.insert_one({
            "type": "leave_return_approved",
            "applicationId": ObjectId(app_id),
            "referenceId": app.get("referenceId"),
            "target": {
                "type": "user",
                "email": applicant_email
            },
            "message": f"Your leave return approval request for {app.get('referenceId')} has been approved.",
            "status": "unread",
            "isActive": True,
            "createdAt": datetime.utcnow()
        })
    
    return jsonify({"status": "success", "message": "Return report approved successfully"})