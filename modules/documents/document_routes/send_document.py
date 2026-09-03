from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo import MongoClient
import bleach
import copy
from modules.documents.functions_helper import (
    get_recipient_emails,
    resolve_delegate,
    emit_document_notification,
    clone_document_for_recipient,
    ensure_assignment_history,
    assignment_history_entry,
    handle_dispatch,
    handle_target_forward

)
from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview

send_document_routes = Blueprint("send_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]


@send_document_routes.route("/send_document", methods=["POST"])
def send_document():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    try:
        doc_id = request.form.get("doc_id", "").strip()
        remark = request.form.get("remark", "").strip()
        action_type = request.form.get(
            "action_type", "forward"
        )  # ← NEW: Get action type

        # Check for double submission using session
        submission_key = f"submitted_{doc_id}"
        if session.get(submission_key):
            flash("This document has already being processed. Please wait.", "warning")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        # Mark as processing
        session[submission_key] = True
        session.modified = True

        recipient_emails = get_recipient_emails()

        if not recipient_emails:
            flash("Please select at least one recipient.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        documents_collection = db["documents"]
        document = documents_collection.find_one({"_id": ObjectId(doc_id)})

        if not document:
            flash("Document not found.", "error")
            session.pop(submission_key, None)
            return redirect(request.referrer)

        # ── HANDLE DISPATCH ACTION ──
        if action_type == "dispatch":
            return handle_dispatch(
                doc_id, document, recipient_emails, remark, submission_key
            )

        # In send_document route, add this before the action_type check
        action_type = request.form.get("action_type", "forward")

        # ── HANDLE TARGET DIRECTORATE FORWARD ACTION ──
        if action_type == "target_forward":
            return handle_target_forward(
                doc_id, document, recipient_emails, remark, submission_key
            )

        # ── NORMAL FORWARD ACTION (existing logic) ──
        # Check if document has already been forwarded recently
        last_forward = document.get("forwarded_at")
        if last_forward and isinstance(last_forward, datetime):
            time_diff = datetime.now() - last_forward
            if time_diff.total_seconds() < 5:  # Within 5 seconds
                flash(
                    "Document was recently forwarded. Please check before forwarding again.",
                    "warning",
                )
                session.pop(submission_key, None)
                return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        clean_remark = bleach.clean(remark) if remark else ""
        sent_at = datetime.now()
        sent_by = session.get("name")
        sent_email = session.get("email")
        sent_role = session.get("role")
        sent_rank = session.get("rank")
        sent_appt = session.get("appt")

        new_remark = {
            "user": sent_by,
            "email": sent_email,
            "role": sent_role,
            "rank": sent_rank,
            "appt": sent_appt,
            "remark": clean_remark,
            "timestamp": sent_at,
        }

        # ── Primary recipient ──
        primary_recipient = recipient_emails[0]

        # Read and filter CC emails
        cc_emails = request.form.getlist("cc_emails")
        cc_emails = [
            email.strip()
            for email in cc_emails
            if email and email.strip() and email.strip() not in recipient_emails
        ]

        # Resolve delegation for primary_recipient
        resolved_recipient, res_name, res_role, delegated, delegators = (
            resolve_delegate(primary_recipient)
        )
        if delegated:
            orig_recipient = primary_recipient
            primary_recipient = resolved_recipient
            clean_remark = f"{clean_remark} (Auto-delegated from {orig_recipient} who is Out of Office)"

        target_user = db.users.find_one({
            "$or": [
                {"email": primary_recipient},
                {"appt": primary_recipient},
                {"name": primary_recipient},
            ]
        })
        if target_user:
            primary_recipient = target_user.get("email") or primary_recipient
            target_name = target_user.get("name") or primary_recipient
            target_directorate = target_user.get("directorate") or document.get("target_directorate") or ""
            target_rank = target_user.get("rank") or ""
            target_appt = target_user.get("appt") or (target_user.get("role", "").replace("_", " ").title() if target_user.get("role") else target_name)
        else:
            target_name = primary_recipient
            target_directorate = document.get("target_directorate") or ""
            target_rank = ""
            target_appt = primary_recipient

        print(f"DEBUG: Target rank for {primary_recipient} is {target_rank}")
        print(f"DEBUG: Target appt for {primary_recipient} is {target_appt}")

        primary_history = assignment_history_entry(
            primary_recipient, target_user, clean_remark, sent_at
        )

        # Build CC seen stamp entry if CC emails exist
        cc_seen_stamp = None
        if cc_emails:
            cc_seen_entries = []
            for email in cc_emails:
                u = db.users.find_one({"email": email}) or {}
                cc_seen_entries.append(
                    {
                        "email": email,
                        "name": u.get("name", email),
                        "rank": u.get("rank", ""),
                        "appt": u.get("appt", ""),
                        "role": u.get("role", ""),
                        "directorate": u.get("directorate", ""),
                        "task": "Copied for information / acknowledgement.",
                        "action_type": "acknowledgement",
                        "stamped_by": sent_by,
                        "stamped_email": sent_email,
                        "stamped_role": sent_role,
                        "stamped_rank": sent_rank,
                        "stamped_appt": sent_appt,
                        "stamped_at": sent_at,
                        "signature": "",
                        "signing_date": None,
                        "acknowledged": False,
                    }
                )

            cc_seen_stamp = {
                "stamped_by": sent_by,
                "stamped_email": sent_email,
                "stamped_role": sent_role,
                "stamped_rank": sent_rank,
                "stamped_appt": sent_appt,
                "stamped_at": sent_at,
                "signature": "",
                "assignments": cc_seen_entries,
                "review_chain_snapshot": [],
            }

        # Set doc_lifecycle_stage to IN_REVIEW if it was CLOSED
        current_stage = document.get("doc_lifecycle_stage", DocStage.DRAFT)
        new_doc_stage = (
            DocStage.IN_REVIEW if current_stage == DocStage.CLOSED else current_stage
        )

        current_phase_idx = document.get("current_phase", 0)
        phase_path = f"phases.{current_phase_idx}"

        update_set = {
            "assigned_to": primary_recipient,
            "current_holder": primary_recipient,
            f"{phase_path}.current_holder": primary_recipient,
            f"{phase_path}.status": "Forwarded",
            f"{phase_path}.forwarded_status": "Forwarded",
            "target_directorate": target_directorate,
            "viewed_by_target": False,
            "sender_notified": False,
            "status": "Forwarded",
            "forwarded_status": "Forwarded",
            "forwarded_at": sent_at,
            "forwarded_to": primary_recipient,
            "forwarded_to_name": target_name,
            "forwarded_to_rank": target_rank,
            "forwarded_to_appt": target_appt,
            "forwarded_by": sent_by,
            "forwarded_by_email": sent_email,
            "doc_lifecycle_stage": new_doc_stage,
        }

        update_push = {
            "remarks": new_remark,
            "assignment_history": primary_history,
            f"{phase_path}.assignment_history": primary_history,
        }

        if cc_seen_stamp:
            update_push["seen_stamp_history"] = cc_seen_stamp
            update_push[f"{phase_path}.seen_stamp_history"] = cc_seen_stamp

        result = documents_collection.update_one(
            {
                "_id": ObjectId(doc_id),
                "$or": [
                    {"forwarded_at": {"$exists": False}},
                    {"forwarded_at": {"$lt": datetime.now() - timedelta(seconds=10)}},
                ],
            },
            {"$set": update_set, "$push": update_push},
        )

        # Check if document was already updated
        if result.modified_count == 0:
            flash(
                "Document has already been forwarded. Cannot forward again.", "warning"
            )
            session.pop(submission_key, None)
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        # Socket notification for primary recipient
        sender_desc = sent_appt or sent_role.replace("_", " ").title()
        emit_document_notification(
            primary_recipient,
            doc_id,
            document.get("subject", "No Subject"),
            sender_desc,
            clean_remark,
            action="forwarded",
            doc_reference=document.get("reference_number", ""),
        )

        # ── Extra recipients (clone document for each) ──
        extra_recipients = list(set(recipient_emails[1:]))
        for recipient_email in extra_recipients:
            # Resolve delegation for extra recipient
            resolved_recipient, res_name, res_role, delegated, delegators = (
                resolve_delegate(recipient_email)
            )
            if delegated:
                recipient_email = resolved_recipient

            extra_user = db.users.find_one({"email": recipient_email})
            extra_directorate = extra_user.get("directorate") if extra_user else None
            extra_name = (
                extra_user.get("name", recipient_email)
                if extra_user
                else recipient_email
            )
            extra_rank = extra_user.get("rank") if extra_user else None
            extra_appt = extra_user.get("appt") if extra_user else None

            print(f"DEBUG: Cloned recipient rank for {recipient_email} is {extra_rank}")
            print(f"DEBUG: Cloned recipient appt for {recipient_email} is {extra_appt}")

            cloned = clone_document_for_recipient(
                document, recipient_email, extra_directorate
            )
            cloned.setdefault("remarks", [])
            cloned["remarks"].append(copy.deepcopy(new_remark))
            cloned.setdefault("assignment_history", ensure_assignment_history(document))

            cloned_remark_text = clean_remark
            if delegated:
                cloned_remark_text = f"{cloned_remark_text} (Auto-delegated from original target who is Out of Office)"

            cloned_assign_entry = assignment_history_entry(
                recipient_email, extra_user, cloned_remark_text, sent_at
            )
            cloned["assignment_history"].append(cloned_assign_entry)

            # Update cloned phase assignment history
            cloned_phases = cloned.get("phases", [])
            if current_phase_idx < len(cloned_phases):
                cloned_phases[current_phase_idx].setdefault("assignment_history", [])
                cloned_phases[current_phase_idx]["assignment_history"].append(
                    cloned_assign_entry
                )

            # Push the CC seen stamp entry to the cloned document too!
            if cc_seen_stamp:
                cloned.setdefault("seen_stamp_history", [])
                cloned["seen_stamp_history"].append(cc_seen_stamp)
                if current_phase_idx < len(cloned_phases):
                    cloned_phases[current_phase_idx].setdefault(
                        "seen_stamp_history", []
                    )
                    cloned_phases[current_phase_idx]["seen_stamp_history"].append(
                        cc_seen_stamp
                    )

            # Set cc list on the cloned copies as well
            if cc_emails:
                cloned.setdefault("cc", [])
                cloned["cc"] = list(set(cloned["cc"] + cc_emails))

            # ── Status tracking on clones too ──
            cloned["status"] = "Forwarded"
            cloned["forwarded_status"] = "Forwarded"
            cloned["forwarded_at"] = sent_at
            cloned["forwarded_to"] = recipient_email
            cloned["forwarded_to_name"] = extra_name
            cloned["forwarded_to_rank"] = extra_rank
            cloned["forwarded_to_appt"] = extra_appt
            cloned["forwarded_by"] = sent_by
            cloned["forwarded_by_email"] = sent_email
            cloned["doc_lifecycle_stage"] = new_doc_stage

            documents_collection.insert_one(cloned)

            # Socket notification for extra recipient clone
            sender_desc = sent_appt or sent_role.replace("_", " ").title()
            emit_document_notification(
                recipient_email,
                cloned.get("_id"),
                cloned.get("subject", "No Subject"),
                sender_desc,
                cloned_remark_text,
                action="forwarded",
                doc_reference=cloned.get("reference_number", ""),
            )

        # Update the CC list on the main parent document too!
        if cc_emails:
            db.documents.update_one(
                {"_id": ObjectId(doc_id)}, {"$addToSet": {"cc": {"$each": cc_emails}}}
            )

        # Emit socket notifications for CC recipients (on the main parent document)
        sender_desc = sent_appt or sent_role.replace("_", " ").title()
        for cc_email in cc_emails:
            emit_document_notification(
                cc_email,
                doc_id,
                document.get("subject", "No Subject"),
                sender_desc,
                "Copied (CC) for information / acknowledgement.",
                action="forwarded",
                doc_reference=document.get("reference_number", ""),
            )

        # Clear the submission flag
        session.pop(submission_key, None)

        disp_target = target_appt or target_name or "Recipient"
        if len(recipient_emails) == 1:
            flash(f"Document forwarded to {disp_target} for action.", "success")
        else:
            flash(
                f"Document forwarded to {len(recipient_emails)} users successfully.",
                "success",
            )

        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    except Exception as e:
        print(f"SEND DOCUMENT ERROR: {str(e)}")
        # Clear the submission flag on error
        session.pop(submission_key, None)
        flash("An error occurred while forwarding the document.", "error")
        return redirect(request.referrer or url_for("base_document_routes.documents_content"))
