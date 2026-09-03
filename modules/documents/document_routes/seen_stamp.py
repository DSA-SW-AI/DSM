from flask import (
    Blueprint,
    render_template,
    send_file,
    request,
    redirect,
    url_for,
    flash,
    session,
    current_app,
)
from datetime import datetime
from bson import ObjectId
import bleach
import io
import os
from gridfs import GridFS
from pymongo import MongoClient
from modules.documents.functions_helper import (
    resolve_delegate,
    emit_document_notification,
    check_user_signing_password,
)
from utils.constants import DocStage

seen_stamp_routes = Blueprint("seen_stamp_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@seen_stamp_routes.route("/seen_stamp_send", methods=["POST"])
def seen_stamp_send():
    try:
        # --- PASSWORD VERIFICATION ---
        signing_password = request.form.get("signing_password", "").strip()
        if not signing_password:
            flash("Signing password is required.", "error")
            return redirect(request.referrer)

        user_id = session.get("user_id")
        user = None
        if user_id:
            try:
                user = db.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                pass
        if not user:
            user_email = session.get("email") or session.get("user_email")
            if user_email:
                user = db.users.find_one({"email": user_email})

        if not user:
            flash("User not found.", "error")
            return redirect(request.referrer)

        if not check_user_signing_password(user, signing_password):
            flash("Incorrect signing password.", "error")
            return redirect(request.referrer)

        doc_id = request.form.get("doc_id", "").strip()
        selected = request.form.getlist("seen_recipients")
        tasks = request.form.to_dict()
        stamped_by = session.get("name")
        stamped_email = session.get("email")
        stamped_role = session.get("role")
        stamped_rank = session.get("rank")
        stamped_appt = session.get("appt")
        stamped_at = datetime.now()
        sequence = request.args.get("seq", 0, type=int)

        if not doc_id or not selected:
            flash("Please select at least one recipient.", "error")
            return redirect(request.referrer)
        if stamped_email in selected:
            flash("You cannot assign a document to yourself.", "error")
            return redirect(request.referrer)

        # Enforce hierarchy: Subordinates cannot assign to Director via Seen Stamp
        if stamped_role not in ["super_admin", "cdsa", "dcdsa", "director"]:
            director_users = list(
                db.users.find(
                    {"role": {"$in": ["director", "cdsa", "dcdsa"]}},
                    {"email": 1},
                )
            )
            director_emails = {d["email"] for d in director_users}
            for rec in selected:
                if rec in director_emails:
                    flash(
                        "Documents cannot be assigned to the Director via Seen Stamp. Please use the Forward Document action.",
                        "error",
                    )
                    return redirect(request.referrer)

        document = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not document:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        current_user_signature = user.get("signature_image", "")

        # ── PHASE SUPPORT: Get current phase ──
        current_phase_idx = document.get("current_phase", 0)
        phases = document.get("phases", [])

        # Ensure phases exist (for backward compatibility)
        if not phases:
            # Initialize phases if missing (should only happen for old docs)
            phases = [
                {
                    "phase_number": 0,
                    "directorate": document.get("origin_directorate", "DCS"),
                    "type": "origin",
                    "started_at": document.get("created_at"),
                    "started_by": document.get("created_by"),
                    "current_holder": document.get("current_holder"),
                    "review_chain": document.get("review_chain", []),
                    "assignment_history": document.get("assignment_history", []),
                    "seen_stamp_history": document.get("seen_stamp_history", []),
                }
            ]
            current_phase_idx = 0

        current_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )
        phase_path = f"phases.{current_phase_idx}"

        # ── Read the chain from the current phase ──
        existing_chain = current_phase.get("review_chain", [])

        # Deduplicate existing chain
        deduped_existing = []
        seen_keys = set()
        for m in existing_chain:
            k = (m.get("email") or "").strip().lower()
            a = (m.get("appt") or "").strip().lower()
            r = (m.get("role") or "").strip().lower()
            if k and k in seen_keys:
                continue
            if a and a in seen_keys and ("@" not in k or k == a):
                continue
            if r and r in ["director", "cdsa", "dcdsa"] and r in seen_keys and ("@" not in k or k == r):
                continue
            if k:
                seen_keys.add(k)
            if a:
                seen_keys.add(a)
            if r:
                seen_keys.add(r)
            deduped_existing.append(m)

        # Find where the current stamper sits in the existing chain
        stamper_index = next(
            (
                i
                for i, m in enumerate(deduped_existing)
                if m.get("email") == stamped_email
                or (m.get("appt") and stamped_appt and m.get("appt").strip().lower() == stamped_appt.strip().lower())
                or (m.get("role") and stamped_role in ["director", "cdsa", "dcdsa"] and m.get("role") == stamped_role)
            ),
            -1,
        )

        stamper_entry = {
            "email": stamped_email,
            "name": stamped_by,
            "rank": stamped_rank,
            "appt": stamped_appt,
            "role": stamped_role,
        }

        if stamper_index >= 0:
            deduped_existing[stamper_index] = stamper_entry
            chain_head = deduped_existing[: stamper_index + 1]
        else:
            chain_head = deduped_existing + [stamper_entry]

        # Build the new recipients to append
        head_emails = {m["email"] for m in chain_head}
        new_tail = []
        action_recipients = []

        user_role = session.get("role")
        user_directorate = (session.get("directorate") or "").strip().upper()
        has_cross_dir_recipient = False
        cross_dir_target = None

        for email in selected:
            safe_email = email.replace("@", "_").replace(".", "_")
            action_type = request.form.get(f"action_type_{safe_email}", "action")
            if action_type == "action":
                u = db.users.find_one({"email": email}) or {}
                recipient_dir = (u.get("directorate") or "").strip().upper()

                if (
                    user_directorate
                    and recipient_dir
                    and recipient_dir != user_directorate
                ):
                    has_cross_dir_recipient = True
                    cross_dir_target = u.get("directorate") or ""
                else:
                    action_recipients.append(email)
                    if email not in head_emails:
                        new_tail.append(
                            {
                                "email": email,
                                "name": u.get("name", email),
                                "rank": u.get("rank", ""),
                                "appt": u.get("appt", ""),
                                "role": u.get("role", ""),
                            }
                        )

        chain = chain_head + new_tail

        # ── Build seen / assignment entries ──
        seen_entries = []
        assignment_entries = []

        for email in selected:
            safe_email = email.replace("@", "_").replace(".", "_")
            action_type = request.form.get(f"action_type_{safe_email}", "action")
            task_key = f"task_{safe_email}"
            task_text = tasks.get(task_key, "").strip()
            member = next((m for m in chain if m["email"] == email), None)
            if not member:
                u = db.users.find_one({"email": email}) or {}
                member = {
                    "email": email,
                    "name": u.get("name", email),
                    "rank": u.get("rank", ""),
                    "appt": u.get("appt", ""),
                    "role": u.get("role", ""),
                }

            seen_entries.append(
                {
                    "email": email,
                    "name": member.get("name", email),
                    "rank": member.get("rank", ""),
                    "appt": member.get("appt", ""),
                    "role": member.get("role") or u.get("role", ""),
                    "directorate": u.get("directorate", ""),
                    "task": task_text,
                    "action_type": action_type,
                    "stamped_by": stamped_by,
                    "stamped_email": stamped_email,
                    "stamped_role": stamped_role,
                    "stamped_rank": stamped_rank,
                    "stamped_appt": stamped_appt,
                    "stamped_at": stamped_at,
                    "signature": current_user_signature,
                    "signing_date": stamped_at,
                }
            )

            assignment_entries.append(
                {
                    "assigned_to": email,
                    "assigned_to_name": member.get("name", email),
                    "assigned_to_rank": member.get("rank", ""),
                    "assigned_to_appt": member.get("appt", ""),
                    "assigned_by": stamped_email,
                    "assigned_by_name": stamped_by,
                    "assigned_by_role": stamped_role,
                    "assigned_by_rank": stamped_rank,
                    "assigned_by_appt": stamped_appt,
                    "remark": task_text,
                    "timestamp": stamped_at,
                    "action_type": (
                        "seen_stamp_assign"
                        if action_type == "action"
                        else "seen_stamp_acknowledgement"
                    ),
                }
            )

            if task_text:
                db.documents.update_one(
                    {"_id": ObjectId(doc_id)},
                    {
                        "$push": {
                            "remarks": {
                                "user": stamped_by,
                                "email": stamped_email,
                                "role": stamped_role,
                                "rank": stamped_rank,
                                "appt": stamped_appt,
                                "remark": f"[To {member.get('name', email)}]: {task_text}",
                                "timestamp": stamped_at,
                            }
                        }
                    },
                )

        if has_cross_dir_recipient:
            first_recipient = stamped_email
            first_recipient_name = stamped_by
            status_val = "Awaiting Dispatch"
            fwd_status_val = "Awaiting Dispatch"
            lifecycle_stage_val = DocStage.AWAITING_DISPATCH_TO_TARGET
        elif action_recipients:
            first_recipient = action_recipients[-1]
            first_member = next((m for m in chain if m["email"] == first_recipient), {})
            first_recipient_name = first_member.get("name", first_recipient)
            status_val = "Assigned"
            fwd_status_val = "Forwarded"
            lifecycle_stage_val = DocStage.IN_REVIEW
        else:
            first_recipient = stamped_email
            first_recipient_name = stamped_by
            status_val = "Stamped"
            fwd_status_val = "Stamped"
            lifecycle_stage_val = DocStage.IN_REVIEW

        # Resolve delegation for first_recipient
        resolved_recipient, res_name, res_role, delegated, delegators = (
            resolve_delegate(first_recipient)
        )
        if delegated:
            orig_recipient = first_recipient
            orig_recipient_name = first_recipient_name
            first_recipient = resolved_recipient
            first_recipient_name = res_name or first_recipient
            # Append delegation entry to assignment history
            delegation_log = {
                "assigned_to": first_recipient,
                "assigned_to_name": first_recipient_name,
                "assigned_to_rank": "",
                "assigned_to_appt": res_role or "",
                "assigned_by": orig_recipient,
                "assigned_by_name": orig_recipient_name,
                "assigned_by_role": "",
                "assigned_by_rank": "",
                "assigned_by_appt": "",
                "remark": f"Auto-delegated to {first_recipient_name} on behalf of {orig_recipient_name} (Out of Office).",
                "timestamp": stamped_at,
                "action_type": "delegation",
            }
            assignment_entries.append(delegation_log)

        # ── PHASE SUPPORT: Update both phase and root ──
        update_data = {
            "$set": {
                # Update phase-specific fields
                f"{phase_path}.review_chain": chain,
                f"{phase_path}.current_holder": first_recipient,
                f"{phase_path}.status": status_val,
                f"{phase_path}.forwarded_status": fwd_status_val,
                # Root fields (keep for backward compatibility)
                "review_chain": chain,
                "current_holder": first_recipient,
                "assigned_to": first_recipient,
                "viewed_by_target": False,
                "status": status_val,
                "forwarded_status": fwd_status_val,
                "forwarded_at": stamped_at,
                "assigned_at": stamped_at,
                "original_assigner": stamped_email,
                "forwarded_by": stamped_by,
                "forwarded_by_email": stamped_email,
                "forwarded_to": first_recipient,
                "forwarded_to_name": first_recipient_name,
                "doc_lifecycle_stage": lifecycle_stage_val,
            },
            "$push": {
                # Push to both phase and root
                f"{phase_path}.assignment_history": {"$each": assignment_entries},
                f"{phase_path}.seen_stamp_history": {
                    "stamped_by": stamped_by,
                    "stamped_email": stamped_email,
                    "stamped_role": stamped_role,
                    "stamped_rank": stamped_rank,
                    "stamped_appt": stamped_appt,
                    "stamped_at": stamped_at,
                    "signature": current_user_signature,
                    "assignments": seen_entries,
                    "review_chain_snapshot": chain,
                },
                "assignment_history": {"$each": assignment_entries},
                "seen_stamp_history": {
                    "stamped_by": stamped_by,
                    "stamped_email": stamped_email,
                    "stamped_role": stamped_role,
                    "stamped_rank": stamped_rank,
                    "stamped_appt": stamped_appt,
                    "stamped_at": stamped_at,
                    "signature": current_user_signature,
                    "assignments": seen_entries,
                    "review_chain_snapshot": chain,
                },
                "full_assignment_history": {"$each": assignment_entries},
                "full_review_chain": {
                    "$each": [m for m in chain if m.get("email") not in head_emails]
                },
            },
        }
        if has_cross_dir_recipient and cross_dir_target:
            update_data["$set"]["target_directorate"] = cross_dir_target
        db.documents.update_one({"_id": ObjectId(doc_id)}, update_data)

        # ── Update correspondence entry too ──
        if sequence > 0:
            db.documents.update_one(
                {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
                {
                    "$set": {
                        "correspondence.$.review_chain": chain,
                        "correspondence.$.current_holder": first_recipient,
                        "correspondence.$.assigned_to": first_recipient,
                        "correspondence.$.forwarded_status": fwd_status_val,
                        "correspondence.$.status": status_val,
                        "correspondence.$.forwarded_at": stamped_at,
                        "correspondence.$.forwarded_to": first_recipient,
                        "correspondence.$.forwarded_to_name": first_recipient_name,
                    }
                },
            )

        # Socket notification trigger for all assigned recipients
        sender_desc = stamped_appt or stamped_role.replace("_", " ").title()
        for rec_email in selected:
            if rec_email and rec_email != stamped_email:
                rec_safe_email = rec_email.replace("@", "_").replace(".", "_")
                rec_task = tasks.get(f"task_{rec_safe_email}", "").strip()
                emit_document_notification(
                    rec_email,
                    doc_id,
                    document.get("subject", "No Subject"),
                    sender_desc,
                    rec_task,
                    action="assigned",
                    doc_reference=document.get("reference_number", ""),
                )

        flash(
            f"Document assigned/stamped for {len(selected)} recipient(s). "
            f"Review chain now has {len(chain)} members.",
            "success",
        )
        return redirect(
            url_for(
                "open_document_routes.open_document",
                doc_id=doc_id,
                view_seq=sequence if sequence > 0 else None,
            )
        )

    except Exception as e:
        print(f"SEEN STAMP SEND ERROR: {e}")
        import traceback

        traceback.print_exc()
        flash("An error occurred while processing the Seen Stamp.", "error")
        return redirect(request.referrer or url_for("base_document_routes.documents_content"))
