from flask import Blueprint, app, render_template, request, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime

from permissions import ROLE_PERMISSIONS 

application_track = Blueprint('application_track', __name__)


def compute_application_timeline(app, reference_id):
    # Determine applicant type from role_bucket
    role_bucket = app.get("role_bucket", "officer")
    
    # Approval chain
    approval_chain = app.get("approvalChain", [])
    user_role = session.get("role", "civilian")
    
    # ==================== TIMELINE CALCULATION ====================
    timeline = []
    
    # Step 1: Application Submitted
    timeline.append({
        "title": "Step 1: Application Submitted",
        "date": app.get("createdAt"),
        "description": f"Reference ID: {reference_id}",
        "status": "completed",
        "icon": "ri-send-plane-line",
        "step_number": 1
    })
    
    # Build approval steps dynamically from the actual approvalChain
    approval_steps = []
    for step in approval_chain:
        role = step.get("role")
        # Registry steps and final Director DOA receipt steps are handled separately below
        if role in ("registry", "central_registry"):
            continue
        if role == "director" and (step.get("is_final_approver") or step.get("registry_type") == "director_doa"):
            continue
            
        # Standard approver step
        display_name = role.upper()
        if role == "civilian_head_cao":
            display_name = "Head Civilian Affair"
        elif role == "so":
            display_name = "Staff Officer"
        elif role == "ad":
            display_name = "AD Officer"
        elif role == "dd":
            display_name = "Deputy Director"
        elif role == "director":
            display_name = "Director"
        elif role == "cdsa":
            display_name = "CDSA"
            
        approval_steps.append({"role": role, "display": display_name})

    # Identify final receipt step and display label
    if role_bucket == 'civilian':
        receipt_step = next((s for s in approval_chain if s.get("role") == "director" and (s.get("is_final_approver") or s.get("registry_type") == "director_doa")), None)
        receipt_display = "Director DOA"
    else:
        receipt_step = next((s for s in approval_chain if s.get("role") == "central_registry"), None)
        receipt_display = "Central Registry"

    registry_step_number_offset = 2
    receipt_step_number_offset = 3
    
    found_current = False
    
    for idx, step_info in enumerate(approval_steps, start=2):
        role = step_info["role"]
        display_name = step_info["display"]
        step = next((s for s in approval_chain if s.get("role") == role), None)
        
        if step:
            status = step.get("status", "pending")
            step_date = step.get("timestamp")
            approver_name = step.get("approverName") or step.get("name") or display_name
            comments = step.get("comments", "")
            approver_rank = step.get("approverRank", "")
            
            if status in ("approved", "Recommended for Approval"):
                action_text = "Recommended" if status == "Recommended for Approval" else "Approved"
                desc = f"{action_text} by {approver_name}"
                if approver_rank:
                    desc += f" ({approver_rank})"
                if comments and comments not in ["Approved", "Approved by CDSA", "Recommended for Approval"]:
                    desc += f" - {comments}"
                
                timeline.append({
                    "title": f"Step {idx}: {display_name} {action_text}",
                    "date": step_date,
                    "description": desc,
                    "status": "completed",
                    "icon": "ri-check-line",
                    "step_number": idx
                })
            elif status in ("rejected", "Rejected"):
                desc = f"Rejected by {approver_name}"
                if comments:
                    desc += f" - {comments}"
                timeline.append({
                    "title": f"Step {idx}: {display_name} Rejected",
                    "date": step_date,
                    "description": desc,
                    "status": "rejected",
                    "icon": "ri-close-line",
                    "step_number": idx,
                    "current": True
                })
                found_current = True
                break
            else:  # pending
                if not found_current:
                    # Check if previous steps are completed
                    all_prev_completed = True
                    for prev_step_info in approval_steps[:idx-1]:
                        prev_step = next((s for s in approval_chain if s.get("role") == prev_step_info["role"]), None)
                        if not prev_step or prev_step.get("status") not in ("approved", "Recommended for Approval"):
                            all_prev_completed = False
                            break
                    
                    if all_prev_completed:
                        timeline.append({
                            "title": f"Step {idx}: Awaiting {display_name} Approval",
                            "date": datetime.utcnow(),
                            "description": f"Waiting for {display_name} to review the application",
                            "status": "pending",
                            "icon": "ri-time-line",
                            "step_number": idx,
                            "current": True
                        })
                        found_current = True
                    else:
                        # Not yet reached this step
                        timeline.append({
                            "title": f"Step {idx}: {display_name} (Pending)",
                            "date": None,
                            "description": "Awaiting previous approvals",
                            "status": "pending",
                            "icon": "ri-time-line",
                            "step_number": idx
                        })
        else:
            # Role missing in chain
            if not found_current:
                timeline.append({
                    "title": f"Step {idx}: {display_name} Not Assigned",
                    "date": datetime.utcnow(),
                    "description": "Approver not configured in approval chain",
                    "status": "pending",
                    "icon": "ri-alert-line",
                    "step_number": idx,
                    "current": True
                })
                found_current = True
    
    # Registry acknowledgement step (directorate registry)
    registry_step = next((s for s in approval_chain if s.get("role") == "registry" and s.get("registry_type") == "directorate"), None)
    if registry_step:
        ack_step_number = len(approval_steps) + registry_step_number_offset
        if registry_step.get("acknowledged"):
            timeline.append({
                "title": f"Step {ack_step_number}: Registry File Acknowledged",
                "date": registry_step.get("acknowledgedAt"),
                "description": f"File acknowledged by {registry_step.get('approverName', 'Registry')}",
                "status": "completed",
                "icon": "ri-folder-check-line",
                "step_number": ack_step_number
            })
        elif registry_step.get("status") == "approved" and not registry_step.get("acknowledged") and not found_current:
            timeline.append({
                "title": f"Step {ack_step_number}: Awaiting Registry Acknowledgment",
                "date": datetime.utcnow(),
                "description": "Waiting for registry to acknowledge file receipt",
                "status": "pending",
                "icon": "ri-time-line",
                "step_number": ack_step_number,
                "current": True
            })
            found_current = True
    
    # Receipt issuance step (SO1-DOA for civilian, Central Registry for others)
    receipt_step_number = len(approval_steps) + receipt_step_number_offset
    receipt_added = False  # Flag to track if receipt step has been added

    if receipt_step and receipt_step.get("receipt"):
        receipt_info = receipt_step.get("receipt", {})
        timeline.append({
            "title": f"Step {receipt_step_number}: Receipt Issued by {receipt_display}",
            "date": receipt_info.get("issuedDate"),
            "description": f"Receipt Number: {receipt_info.get('receiptNumber', 'N/A')}<br>Issued by: {receipt_info.get('issuedByName', receipt_display)}",
            "status": "completed",
            "icon": "ri-receipt-line",
            "step_number": receipt_step_number
        })
        receipt_added = True

    elif receipt_step and receipt_step.get("status") == "approved" and not receipt_step.get("receipt") and not found_current:
        timeline.append({
            "title": f"Step {receipt_step_number}: Awaiting Receipt Issuance from {receipt_display}",
            "date": datetime.utcnow(),
            "description": f"Application approved, waiting for {receipt_display} to issue receipt",
            "status": "pending",
            "icon": "ri-time-line",
            "step_number": receipt_step_number,
            "current": True
        })
        receipt_added = True
        found_current = True
    
    # For director applications, check central_registry ONLY if not already added
    if role_bucket == 'director' and not receipt_added:
        central_registry_step = next((s for s in approval_chain if s.get("role") == "central_registry"), None)
        if central_registry_step and central_registry_step.get("receipt"):
            receipt_info = central_registry_step.get("receipt", {})
            timeline.append({
                "title": f"Step {receipt_step_number}: Receipt Issued by Central Registry",
                "date": receipt_info.get("issuedDate"),
                "description": f"Receipt Number: {receipt_info.get('receiptNumber', 'N/A')}<br>Issued by: {receipt_info.get('issuedByName', 'Central Registry')}",
                "status": "completed",
                "icon": "ri-receipt-line",
                "step_number": receipt_step_number
            })
            
    # Progress calculation
    completed_steps_count = sum(1 for step in timeline if step['status'] in ['completed'] and step.get('date') is not None)
    total_steps_count = len(timeline)
    current_step = next((s for s in timeline if s.get("current")), None)
    
    # Sort timeline by step_number, None goes last
    timeline.sort(key=lambda x: x.get("step_number") or float('inf'))
    
    return timeline, completed_steps_count, total_steps_count, current_step


@application_track.route('/track_application', methods=['GET', 'POST'])
def track_application():

    if 'user_email' not in session:
        return redirect(url_for('index'))
    
    current_user = {
            "service_number":  session.get("service_number"),
            "fullName":        session.get("name"),
            "name":            session.get("name"),
            "directorate":     session.get("directorate"),
            "designation":     session.get("appt") or session.get("onboarding_data", {}).get("step_1", {}).get("appt"),
            "rankOrGrade":     session.get("rankOrGrade") or session.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
            "email":           session.get("email"),
            "role":            session.get("role"),
            "is_so_approver": session.get("is_so_approver", False),
            "is_dd_approver":  session.get("is_dd_approver", False),
            "is_ad_approver":  session.get("is_ad_approver", False),
            "is_final_approver": session.get("is_final_approver", False)
    }
    if not session.get("is_approval_role"):
        user_allowed_features = ROLE_PERMISSIONS['civilian']
    else:
        user_allowed_features = ROLE_PERMISSIONS.get(current_user['role'], ROLE_PERMISSIONS['civilian'])
    
    """Public tracking page (no login required)"""
    reference_id = request.form.get('reference_id') or request.args.get('reference_id')
    
    if not reference_id:
        return render_template('track_application.html', 
                               error="Please enter a Reference ID.",
                               user=current_user,
                               permissions=user_allowed_features)
    
    reference_id = reference_id.strip().upper()
    
    try:
        # Look up the application
        app = current_app.applications_collection.find_one({"referenceId": reference_id})
        
        if not app:
            return render_template('track_application.html',
                                   error="Application not found. Please check your Reference ID.",
                                   reference_id=reference_id,
                                   show_modal=True,
                                   user=current_user,
                                   permissions=user_allowed_features)
        
        # Compute timeline using extracted helper function
        timeline, completed_steps_count, total_steps_count, current_step = compute_application_timeline(app, reference_id)
        if not session.get("is_approval_role"):
            user_allowed_features = ROLE_PERMISSIONS['civilian']
        else:
            user_allowed_features = ROLE_PERMISSIONS.get(current_user.get('role'), ROLE_PERMISSIONS['civilian'])
        
        return render_template('track_application.html',
                               show_result_modal=True,
                               application=app,
                               user=current_user,
                               permissions=user_allowed_features,
                               reference_id=reference_id,
                               timeline=timeline,
                               current_step=current_step,
                               completed_steps=completed_steps_count,
                               total_steps=total_steps_count,
                               status=app.get("status", "pending"))
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template('track_application.html',
                               error="Error tracking application. Please try again.",
                               reference_id=reference_id,
                               show_modal=True,
                               user=current_user,
                               permissions=user_allowed_features)


def get_step_description(step):
    """Get description for current step"""
    status = step['status']
    role = step['step'].get('role', '')
    
    if status == "pending":
        if role == "so1_doa":
            return "Awaiting Leave/Pass Receipt Issued from SO1 DOA"
        elif role == "registry":
            return "Awaiting forwarding to SO1 DOA"
        elif role == "civilian_head_cao":
            return "Awaiting Civilian HOD approval"
        elif role == "officer":
            return "Awaiting Officer approval"
        elif role == "deputy_director":
            return "Awaiting Deputy Director approval"
        elif role == "director":
            return "Awaiting Director approval"
        else:
            return f"Awaiting approval from {role}"
    elif status in ("rejected", "Rejected"):
        return "Application has been rejected"
    elif status == "completed":
        if role == "Completed":
            return "Application processing completed"
        return "Approval completed"
    return "Processing..."

def get_step_icon(status):
    """Get icon based on status"""
    icons = {
        "pending": "ri-time-line",
        "approved": "ri-check-line",
        "rejected": "ri-close-line",
        "Rejected": "ri-close-line",
        "completed": "ri-check-double-line"
    }
    return icons.get(status, "ri-time-line")


@application_track.route('/track_result', methods=['GET'])
def track_result():
    return render_template('track_result.html')