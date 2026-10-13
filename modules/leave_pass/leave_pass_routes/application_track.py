from flask import Blueprint, app, render_template, request, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime

from permissions import ROLE_PERMISSIONS 

application_track = Blueprint('application_track', __name__)


def compute_application_timeline(app, reference_id):
    """Build the applicant-facing tracking timeline from the application's real approvalChain.

    Every step in the chain is shown, in chain order, for every applicant type
    (civilian, personnel, officer, so, ad, dd, director): approvers (including the
    final Director DOA), directorate / issuing registries and the central registry
    receipt. Steps that have not been reached yet are listed as pending so the
    applicant can see the full path ahead.
    """
    approval_chain = app.get("approvalChain", []) or []

    APPROVED = ("approved", "Approved", "Recommended for Approval")
    REJECTED = ("rejected", "Rejected")

    def is_final_director(step):
        return step.get("role") == "director" and (
            step.get("is_final_approver") is True
            or step.get("registry_type") == "director_doa"
        )

    def display_for(step):
        role = step.get("role") or ""
        if role == "director":
            return "Director DOA" if is_final_director(step) else "Director"
        if role in ("civilian_head_cao", "civilian_head"):
            return "Head Civilian Affair"
        if role in ("deputy_civilian_head_cao", "deputy_civilian_head"):
            return "Deputy Head Civilian Affair"
        if role == "registry":
            return "Issuing Registry (DOA)" if step.get("registry_type") == "issuing_directorate" else "Directorate Registry"
        if role == "central_registry":
            return "Central Registry"
        return {
            "so": "Staff Officer",
            "ad": "AD Officer",
            "dd": "Deputy Director",
            "cdsa": "CDSA",
        }.get(role, role.replace("_", " ").upper())

    timeline = [{
        "title": "Step 1: Application Submitted",
        "date": app.get("createdAt"),
        "description": f"Reference ID: {reference_id}",
        "status": "completed",
        "icon": "ri-send-plane-line",
        "step_number": 1,
    }]

    state = {"step_no": 1, "found_current": False}  # first not-completed step is the "current" one

    def add_entry(*, done, rejected=False, title_done, title_waiting, title_future,
                  date_done=None, desc_done="", desc_waiting="", desc_rejected="",
                  icon_done="ri-check-line", rejected_date=None):
        """Append one timeline entry (always prefixed with its step number)."""
        state["step_no"] += 1
        n = state["step_no"]
        if rejected:
            timeline.append({
                "title": f"Step {n}: {title_done} Rejected",
                "date": rejected_date,
                "description": desc_rejected,
                "status": "rejected",
                "icon": "ri-close-line",
                "step_number": n,
                "current": True,
            })
            state["found_current"] = True
        elif done:
            timeline.append({
                "title": f"Step {n}: {title_done}",
                "date": date_done,
                "description": desc_done,
                "status": "completed",
                "icon": icon_done,
                "step_number": n,
            })
        elif not state["found_current"]:
            timeline.append({
                "title": f"Step {n}: {title_waiting}",
                "date": datetime.utcnow(),
                "description": desc_waiting,
                "status": "pending",
                "icon": "ri-time-line",
                "step_number": n,
                "current": True,
            })
            state["found_current"] = True
        else:
            timeline.append({
                "title": f"Step {n}: {title_future}",
                "date": None,
                "description": "Awaiting previous approvals",
                "status": "pending",
                "icon": "ri-time-line",
                "step_number": n,
            })

    for step in approval_chain:
        role = step.get("role") or ""
        name = display_for(step)
        status = step.get("status", "pending")
        approver_name = step.get("approverName") or step.get("name") or name
        approver_rank = step.get("approverRank", "")
        comments = step.get("comments", "")

        # ── Directorate / issuing registry: physical file acknowledgement ──
        if role == "registry":
            add_entry(
                done=bool(step.get("acknowledged")),
                title_done=f"{name} File Acknowledged",
                title_waiting=f"Awaiting {name} Acknowledgment",
                title_future=f"{name} Acknowledgment (Pending)",
                date_done=step.get("acknowledgedAt"),
                desc_done=f"File acknowledged by {step.get('approverName', 'Registry')}",
                desc_waiting=f"Waiting for {name} to acknowledge file receipt",
                icon_done="ri-folder-check-line",
            )
            continue

        # ── Central registry: receipt issuance ──
        if role == "central_registry":
            receipt = step.get("receipt") or {}
            add_entry(
                done=bool(receipt),
                title_done="Receipt Issued by Central Registry",
                title_waiting="Awaiting Receipt Issuance from Central Registry",
                title_future="Receipt Issuance by Central Registry (Pending)",
                date_done=receipt.get("issuedDate"),
                desc_done=f"Receipt Number: {receipt.get('receiptNumber', 'N/A')}<br>Issued by: {receipt.get('issuedByName', 'Central Registry')}",
                desc_waiting="Application approved, waiting for Central Registry to issue receipt",
                icon_done="ri-receipt-line",
            )
            continue

        # ── Approver steps: civilian head, so, ad, dd, director, final Director DOA, cdsa ──
        if status in APPROVED:
            action_text = "Recommended" if status == "Recommended for Approval" else "Approved"
            desc = f"{action_text} by {approver_name}"
            if approver_rank:
                desc += f" ({approver_rank})"
            if comments and comments not in ["Approved", "Approved by CDSA", "Recommended for Approval"]:
                desc += f" - {comments}"
            receipt = step.get("receipt") or {}
            if receipt.get("receiptNumber"):
                desc += f"<br>Receipt Number: {receipt.get('receiptNumber')}"
            add_entry(
                done=True,
                title_done=f"{name} {action_text}", title_waiting="", title_future="",
                date_done=step.get("timestamp"), desc_done=desc,
            )
        elif status in REJECTED:
            desc = f"Rejected by {approver_name}"
            if comments:
                desc += f" - {comments}"
            add_entry(
                done=False, rejected=True,
                title_done=name, title_waiting="", title_future="",
                desc_rejected=desc, rejected_date=step.get("timestamp"),
            )
            break  # nothing after a rejection will happen
        else:
            add_entry(
                done=False,
                title_done="", title_waiting=f"Awaiting {name} Approval",
                title_future=f"{name} (Pending)",
                desc_waiting=f"Waiting for {name} to review the application",
            )

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
        elif role == "dd":
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