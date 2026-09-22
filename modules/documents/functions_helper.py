from flask import request, session, current_app, flash, redirect, url_for
from datetime import datetime
from bson.objectid import ObjectId
import os
import re
import io
import copy
from pymongo import MongoClient
import bleach

from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview

# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]


def get_directorate_from_email(email):
    """Extract directorate from email address with db.users lookup"""
    if not email:
        return "Unknown"
    user = db.users.find_one({"email": email})
    if user and user.get("directorate"):
        return user.get("directorate").strip().upper()
    if "@" not in email:
        return "Unknown"
    local_part = email.split("@")[0]
    directorate_mappings = {
        "dcs": "DCS",
        "cdsa": "CDSA",
        "doa": "DOA",
        "dap": "DAP",
        "dia": "DIA",
        "central.registry": "CENTRAL_REGISTRY",
    }
    for key, value in directorate_mappings.items():
        if key in local_part.lower():
            return value
    if ".dcs" in email:
        return "DCS"
    elif ".cdsa" in email:
        return "CDSA"
    elif ".doa" in email:
        return "DOA"
    domain = email.split("@")[1].split(".")[0]
    return domain.upper()


def get_recipient_emails():
    recipients = [
        email.strip()
        for email in request.form.getlist("recipient_email")
        if email.strip()
    ]
    if recipients:
        return list(dict.fromkeys(recipients))

    single_value = request.form.get("recipient_email", "").strip()
    if not single_value:
        return []

    return list(
        dict.fromkeys(
            [email.strip() for email in single_value.split(",") if email.strip()]
        )
    )


def document_search_query_with_phases(search_text):
    """
    Enhanced search that includes phase fields.
    """
    search_text = (search_text or "").strip()
    if not search_text:
        return {}

    safe_pattern = re.escape(search_text)
    searchable_fields = [
        "reference_number",
        "subject",
        "memo_title",
        "sender",
        "sender_email",
        "assigned_to",
        "recipient",
        "origin_directorate",
        "target_directorate",
        "priority",
        "comment",
        "created_by",
        "pdf_path",
        "content_html",
    ]

    # ── PHASE SUPPORT: Add phase fields to search ──
    phase_fields = [
        "phases.review_chain.name",
        "phases.review_chain.email",
        "phases.assignment_history.assigned_to_name",
        "phases.assignment_history.assigned_to",
        "phases.directorate",
        "phases.current_holder",
        "phases.type",
        "phases.started_by",
    ]

    return {
        "$or": (
            [
                {field: {"$regex": safe_pattern, "$options": "i"}}
                for field in searchable_fields
            ]
            + [
                {field: {"$regex": safe_pattern, "$options": "i"}}
                for field in phase_fields
            ]
        )
    }


def clone_document_for_recipient(
    document, recipient_email, target_directorate, is_dispatch=False, is_cc=False
):
    """Clone a document for an additional recipient with phase support"""
    cloned_document = copy.deepcopy(document)
    cloned_document.pop("_id", None)
    cloned_document["assigned_to"] = recipient_email
    cloned_document["current_holder"] = recipient_email
    cloned_document["target_directorate"] = target_directorate
    cloned_document["viewed_by_target"] = False
    cloned_document["sender_notified"] = False
    cloned_document["date_assigned"] = datetime.now()
    cloned_document["cloned_from_document_id"] = str(document.get("_id"))

    if is_cc:
        cloned_document["is_cc_copy"] = True

    # ── PHASE SUPPORT: Update phases in clone ──
    phases = cloned_document.get("phases", [])
    if phases:
        current_phase_idx = cloned_document.get("current_phase", 0)
        if current_phase_idx < len(phases):
            # Update the current phase's holder
            phases[current_phase_idx]["current_holder"] = recipient_email
            # Add recipient to review chain only if it's not a dispatch and not a CC
            if not is_dispatch and not is_cc:
                user = db.users.find_one({"email": recipient_email})
                if user:
                    phases[current_phase_idx]["review_chain"].append(
                        {
                            "email": recipient_email,
                            "name": user.get("name", recipient_email),
                            "rank": user.get("rank", ""),
                            "appt": user.get("appt", ""),
                            "role": user.get("role", ""),
                        }
                    )

    return cloned_document


def create_watermarked_pdf(pdf_source, watermark_text):
    from reportlab.pdfgen import canvas as rl_canvas
    import PyPDF2

    output = io.BytesIO()

    # Accept both a file path string and a BytesIO object
    if isinstance(pdf_source, str):
        file_obj = open(pdf_source, "rb")
        should_close = True
    else:
        pdf_source.seek(0)
        file_obj = pdf_source
        should_close = False

    try:
        reader = PyPDF2.PdfReader(file_obj)
        writer = PyPDF2.PdfWriter()

        for page in reader.pages:
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)

            overlay_stream = io.BytesIO()
            overlay = rl_canvas.Canvas(
                overlay_stream, pagesize=(page_width, page_height)
            )
            overlay.saveState()
            overlay.setFont("Helvetica-Bold", 18)
            overlay.setFillColorRGB(0.25, 0.25, 0.25)
            if hasattr(overlay, "setFillAlpha"):
                overlay.setFillAlpha(0.12)

            step_x = 320
            step_y = 180
            for x in range(-int(page_width), int(page_width * 2), step_x):
                for y in range(-int(page_height), int(page_height * 2), step_y):
                    overlay.saveState()
                    overlay.translate(x, y)
                    overlay.rotate(35)
                    overlay.drawString(0, 0, watermark_text)
                    overlay.restoreState()

            overlay.restoreState()
            overlay.save()
            overlay_stream.seek(0)

            watermark_reader = PyPDF2.PdfReader(overlay_stream)
            page.merge_page(watermark_reader.pages[0])
            writer.add_page(page)

        writer.write(output)

    finally:
        if should_close:
            file_obj.close()

    output.seek(0)
    return output


def get_document_file_path(document):
    relative_path = document.get("pdf_path", "")
    if not relative_path:
        return None

    root_path = os.path.abspath(current_app.root_path)
    absolute_path = os.path.abspath(os.path.join(root_path, relative_path))
    uploads_path = os.path.abspath(os.path.join(root_path, "static", "uploads"))

    if not absolute_path.startswith(uploads_path + os.sep):
        return None

    return absolute_path if os.path.exists(absolute_path) else None


def assignment_history_entry(
    assigned_to_email, target_user=None, remark="", assigned_at=None
):
    """Create an assignment history entry with phase support"""

    assignee_rank = target_user.get("rank", "") if target_user else ""
    assignee_appt = target_user.get("appt", "") if target_user else ""
    assigner_rank = session.get("rank", "")
    assigner_appt = session.get("appt", "")
    assigner_email = session.get("email", "")
    assigner_name = session.get("name", "")
    assigner_role = session.get("role", "")

    return {
        "assigned_to": assigned_to_email,
        "assigned_to_name": (
            target_user.get("name") if target_user else assigned_to_email
        ),
        "assigned_by": assigner_email,
        "assigned_by_name": assigner_name,
        "assigned_by_role": assigner_role,
        "assigned_to_rank": assignee_rank,
        "assigned_to_appt": assignee_appt,
        "assigned_by_rank": assigner_rank,
        "assigned_by_appt": assigner_appt,
        "remark": remark,
        "timestamp": assigned_at or datetime.now(),
        # ── PHASE SUPPORT: Include phase info ──
        "phase_number": None,  # Will be set when adding to phase
    }


def get_phase_info_for_user(doc, user_directorate):
    """
    Returns phase information for a document relative to a user's directorate.
    """
    phases = doc.get("phases", [])
    current_phase_idx = doc.get("current_phase", 0)

    if not phases:
        return {
            "has_phases": False,
            "display_phase": None,
            "is_current_phase": True,
            "phase_directorate": doc.get("origin_directorate"),
            "phase_number": 0,
        }

    # Find the phase for this user's directorate
    display_phase = None
    for phase in reversed(phases):
        if phase.get("directorate") == user_directorate:
            display_phase = phase
            break

    if not display_phase:
        # User's directorate not found in any phase - show current phase
        display_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )

    return {
        "has_phases": True,
        "display_phase": display_phase,
        "is_current_phase": display_phase.get("phase_number") == current_phase_idx,
        "phase_directorate": display_phase.get("directorate"),
        "phase_number": display_phase.get("phase_number"),
        "phase_type": display_phase.get("type"),
        "review_chain_count": len(display_phase.get("review_chain", [])),
        "assignment_count": len(display_phase.get("assignment_history", [])),
        "current_holder": display_phase.get("current_holder"),
    }


def get_lifecycle_exclusions(user_email, user_directorate):
    user_dir_norm = (user_directorate or "").strip().upper()
    if not user_dir_norm:
        return {}

    user_role = session.get("role")

    target_exclusions = [
        {
            "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_TARGET,
            "origin_directorate": {"$ne": user_directorate},
            "sender_email": {"$ne": user_email},
        },
        {
            "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_ORIGIN,
            "target_directorate": {"$ne": user_directorate},
        },
    ]

    if user_role in ["registry", "central_registry"]:
        target_exclusions.append(
            {
                "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_TARGET,
                "origin_directorate": user_directorate,
                "current_holder": {"$ne": user_email},
                "assigned_to": {"$ne": user_email},
            }
        )
        target_exclusions.append(
            {
                "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_ORIGIN,
                "target_directorate": user_directorate,
                "current_holder": {"$ne": user_email},
                "assigned_to": {"$ne": user_email},
            }
        )
        # Exclude confidential documents from registries unless directly assigned/held/sent by them
        target_exclusions.append(
            {
                "is_confidential": True,
                "sender_email": {"$ne": user_email},
                "current_holder": {"$ne": user_email},
                "assigned_to": {"$ne": user_email},
            }
        )
    else:
        # Non-registry roles (directors, officers): hide incoming dispatched documents
        # that are sitting at the local registry and haven't yet been forwarded into the directorate
        target_exclusions.append(
            {
                "doc_lifecycle_stage": DocStage.AT_TARGET,
                "target_directorate": {"$in": [user_directorate, user_dir_norm]},
                "$or": [
                    {"target_forwarded_at": {"$exists": False}},
                    {"target_forwarded_at": None},
                ],
                "current_holder": {"$ne": user_email},
                "assigned_to": {"$ne": user_email},
            }
        )

    return {"$nor": target_exclusions}


def calculate_user_document_counts(user_email, user_role, user_directorate):
    """
    Calculates unread and assigned document counts for the user to display in the sidebar.
    """
    if not user_email:
        return {"unread_docs_count": 0, "assigned_docs_count": 0}

    user_email_clean = user_email.strip().lower()

    # 1. Calculate unread documents count
    try:
        if user_role == "super_admin":
            unread_query = {
                "status": {"$ne": "Saved"},
                "read_by": {"$ne": user_email_clean},
            }
        elif user_role in ["registry", "central_registry"]:
            reg_involvement = {
                "status": {"$ne": "Saved"},
                "is_confidential": {"$ne": True},
                "$or": [
                    {"origin_directorate": user_directorate},
                    {"target_directorate": user_directorate},
                    {"phases.directorate": user_directorate},
                ],
                "read_by": {"$ne": user_email_clean},
            }
            exclusions = get_lifecycle_exclusions(user_email_clean, user_directorate)
            unread_query = combine_queries(reg_involvement, exclusions)
        else:
            involvement = user_involvement_query_with_phases(user_email_clean, user_directorate)
            exclusions = get_lifecycle_exclusions(user_email_clean, user_directorate)
            unread_query = combine_queries(
                involvement,
                {
                    "status": {"$ne": "Saved"},
                    "read_by": {"$ne": user_email_clean},
                },
                exclusions,
            )
        unread_docs_count = db.documents.count_documents(unread_query)
    except Exception as e:
        print(f"Error calculating unread_docs_count: {e}")
        unread_docs_count = 0

    # 2. Calculate assigned documents count
    try:
        active_filter = {
            "status": {"$ne": "Completed"},
            "doc_lifecycle_stage": {"$ne": "closed"},
        }
        if user_role in ["registry", "central_registry"]:
            assigned_or = [
                {"current_holder": user_email_clean},
                {"assigned_to": user_email_clean},
                {"phases.current_holder": user_email_clean},
                {
                    "doc_lifecycle_stage": {
                        "$in": ["awaiting_dispatch_to_target", "awaiting_dispatch_to_origin"]
                    },
                    "$or": [
                        {"origin_directorate": user_directorate},
                        {"target_directorate": user_directorate},
                        {"phases.directorate": user_directorate},
                    ],
                },
            ]
        else:
            assigned_or = [
                {"current_holder": user_email_clean},
                {"assigned_to": user_email_clean},
                {"phases.current_holder": user_email_clean},
                {
                    "seen_stamp_history.assignments": {
                        "$elemMatch": {
                            "assigned_to": user_email_clean,
                            "acknowledged": False,
                        }
                    }
                },
            ]
        assigned_query = combine_queries(active_filter, {"$or": assigned_or})
        assigned_docs_count = db.documents.count_documents(assigned_query)
    except Exception as e:
        print(f"Error calculating assigned_docs_count: {e}")
        assigned_docs_count = 0

    return {
        "unread_docs_count": unread_docs_count,
        "assigned_docs_count": assigned_docs_count,
    }


def emit_document_notification(
    recipient_email,
    doc_id,
    doc_subject,
    sender_name,
    remark="",
    action="forwarded",
    doc_reference="",
):
    try:
        from modules.extensions import socketio

        if not recipient_email or recipient_email == "external":
            return

        recipient_email_clean = recipient_email.strip().lower()

        # Mark as unread for the recipient on the document itself
        db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {"$pull": {"read_by": recipient_email_clean}},
        )

        action_titles = {
            "forwarded": "Document Forwarded",
            "forward_for_dispatch": "Forwarded for Dispatch",
            "dispatched": "Document Dispatched",
            "assigned": "Task Assigned (Seen Stamp)",
            "returned": "Correspondence Returned",
        }
        action_title = action_titles.get(action, "Document Notification")

        action_messages = {
            "forwarded": f"Document '{doc_subject}' was forwarded to you by {sender_name}.",
            "forward_for_dispatch": f"Document '{doc_subject}' was signed and forwarded to Registry for dispatch by {sender_name}.",
            "dispatched": f"Document '{doc_subject}' was dispatched to your directorate by {sender_name}.",
            "assigned": f"A task was assigned to you on document '{doc_subject}' by {sender_name}.",
            "returned": f"Correspondence '{doc_subject}' was returned by {sender_name}.",
        }
        message = action_messages.get(
            action, f"Action required on document '{doc_subject}' from {sender_name}."
        )

        payload = {
            "type": "document",
            "action": action,
            "actionTitle": action_title,
            "reference": doc_reference,
            "_id": str(doc_id),
            "message": message,
            "subject": doc_subject,
            "triggeredBy": sender_name,
            "remark": remark,
            "date": datetime.now().isoformat(),
        }

        # 1. Emit to recipient's email room
        email_room = f"USER_{recipient_email_clean}"
        socketio.emit("new_notification", payload, room=email_room)
        print(f"📡 Sent socket notification to {email_room} for doc {doc_id} ({action})")

        # 2. Emit to service number room if user has one
        user = db.users.find_one({"email": {"$regex": f"^{recipient_email_clean}$", "$options": "i"}})
        if user and user.get("service_number"):
            safe_id = user["service_number"].replace("/", "_")
            if safe_id != recipient_email_clean:
                socketio.emit("new_notification", payload, room=f"USER_{safe_id}")

        # 3. Persist in db.notifications collection
        try:
            db.notifications.insert_one(
                {
                    "type": "document",
                    "action": action,
                    "title": action_title,
                    "message": message,
                    "doc_id": str(doc_id),
                    "reference": doc_reference,
                    "triggeredBy": sender_name,
                    "remark": remark,
                    "target": {
                        "email": recipient_email_clean,
                        "userId": (
                            user.get("service_number")
                            if user and user.get("service_number")
                            else recipient_email_clean
                        ),
                    },
                    "readBy": [],
                    "isActive": True,
                    "is_active": True,
                    "created_at": datetime.now(),
                }
            )
        except Exception as db_err:
            print(f"Error persisting notification to DB: {db_err}")

        # 4. Calculate and emit updated badge counters to recipient
        if user:
            u_email = user.get("email", recipient_email_clean)
            u_role = user.get("role", "")
            u_dir = user.get("directorate", "")
            counts = calculate_user_document_counts(u_email, u_role, u_dir)
            socketio.emit("update_sidebar_badges", counts, room=email_room)
            if user.get("service_number"):
                safe_id = user["service_number"].replace("/", "_")
                socketio.emit("update_sidebar_badges", counts, room=f"USER_{safe_id}")

    except Exception as e:
        print(f"Error emitting socket notification: {e}")


def resolve_delegate(email):
    """
    Recursively resolves a recipient's email if active delegation is enabled.
    Returns a tuple: (final_email, final_name, final_role, delegation_happened, list_of_delegators)
    """
    if not email:
        return email, None, None, False, []

    visited = set()
    current_email = email
    delegators = []
    delegation_happened = False
    final_name = None
    final_role = None

    # Deep limit to avoid infinite loops (max 3 delegation hops)
    for _ in range(3):
        if current_email in visited:
            break
        visited.add(current_email)

        user = db.users.find_one({"email": current_email})
        if not user:
            # Fallback to search by service number
            user = db.users.find_one({"service_number": current_email})

        if user:
            final_name = user.get("name")
            final_role = user.get("role")

            delegation = user.get("delegation", {})
            if delegation.get("active") and delegation.get("delegate_email"):
                delegate_email = delegation.get("delegate_email").strip()
                if delegate_email and delegate_email != current_email:
                    delegators.append(user.get("name", current_email))
                    current_email = delegate_email
                    delegation_happened = True
                    continue
        break

    # Fetch final user details for final resolved email
    final_user = db.users.find_one({"email": current_email})
    if not final_user:
        final_user = db.users.find_one({"service_number": current_email})
    if final_user:
        final_name = final_user.get("name")
        final_role = final_user.get("role")

    return current_email, final_name, final_role, delegation_happened, delegators


def user_involvement_query_with_phases(user_email, user_directorate):
    """
    Returns a MongoDB query that matches documents where the user is involved,
    considering both root-level and phase-level data.
    """
    involvement = {
        "$or": [
            # Root-level involvement (legacy and current holder)
            {"sender_email": user_email},
            {"created_by": user_email},
            {"assigned_to": user_email},
            {"current_holder": user_email},
            {"cc": user_email},
            # Root assignment history
            {"assignment_history.assigned_to": user_email},
            {"assignment_history.assigned_by": user_email},
            # ── PHASE SUPPORT: Phase-level involvement ──
            # User is current holder in any phase
            {"phases.current_holder": user_email},
            # User is in any phase's review chain
            {"phases.review_chain.email": user_email},
            # User is in any phase's assignment history
            {"phases.assignment_history.assigned_to": user_email},
            {"phases.assignment_history.assigned_by": user_email},
            # User is in any phase's seen stamp history
            {"phases.seen_stamp_history.stamped_email": user_email},
            {"phases.seen_stamp_history.assignments.email": user_email},
            # Correspondence involvement (keep existing)
            {"correspondence.assignments.assigned_to": user_email},
            {"correspondence.author.email": user_email},
            {"correspondence.remarks.email": user_email},
            # Remarks
            {"remarks.email": user_email},
            # Seen stamps
            {"seen_stamp_history.stamped_email": user_email},
            {"seen_stamp_history.assignments.email": user_email},
        ]
    }

    user_dir_upper = (user_directorate or "").strip().upper()
    clone_exclusion = {
        "$nor": [
            {
                "parent_doc_id": {"$ne": None, "$exists": True},
                "target_directorate": {"$ne": user_dir_upper},
            }
        ]
    }

    exclusions = get_lifecycle_exclusions(user_email, user_directorate)

    query_parts = [involvement, clone_exclusion]
    if exclusions:
        query_parts.append(exclusions)

    return {"$and": query_parts}


def get_phase_info_for_document(doc, user_directorate):
    """
    Returns phase information for a document relative to a user's directorate.
    """
    phases = doc.get("phases", [])
    current_phase_idx = doc.get("current_phase", 0)

    if not phases:
        return {
            "has_phases": False,
            "display_phase": None,
            "is_current_phase": True,
            "phase_directorate": doc.get("origin_directorate"),
            "phase_number": 0,
            "review_chain_count": len(doc.get("review_chain", [])),
            "assignment_count": len(doc.get("assignment_history", [])),
            "current_holder": doc.get("current_holder"),
            "status": doc.get("status"),
            "forwarded_status": doc.get("forwarded_status"),
        }

    # Find the phase for this user's directorate
    display_phase = None
    for phase in reversed(phases):
        if phase.get("directorate") == user_directorate:
            display_phase = phase
            break

    if not display_phase:
        # User's directorate not found in any phase - show current phase
        display_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )

    return {
        "has_phases": True,
        "display_phase": display_phase,
        "is_current_phase": display_phase.get("phase_number") == current_phase_idx,
        "phase_directorate": display_phase.get("directorate"),
        "phase_number": display_phase.get("phase_number"),
        "phase_type": display_phase.get("type"),
        "review_chain_count": len(display_phase.get("review_chain", [])),
        "assignment_count": len(display_phase.get("assignment_history", [])),
        "seen_stamp_count": len(display_phase.get("seen_stamp_history", [])),
        "current_holder": display_phase.get("current_holder"),
        "started_at": display_phase.get("started_at"),
        "started_by": display_phase.get("started_by"),
        "status": display_phase.get("status", doc.get("status")),
        "forwarded_status": display_phase.get(
            "forwarded_status", doc.get("forwarded_status")
        ),
    }


def add_phase_info_to_documents(documents, user_directorate):
    """Add phase info to a list of documents"""
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        doc["phase_info"] = get_phase_info_for_document(doc, user_directorate)
        doc["assignment_history"] = ensure_assignment_history(doc)
    return documents


def ensure_assignment_history(document):
    history = document.get("assignment_history") or []
    if history:
        return history

    assigned_to = document.get("assigned_to")
    if not assigned_to:
        return []

    return [
        {
            "assigned_to": assigned_to,
            "assigned_to_name": document.get("recipient") or assigned_to,
            "assigned_by": document.get("sender_email"),
            "assigned_by_name": document.get("created_by") or document.get("sender"),
            "assigned_by_role": "",
            "remark": document.get("comment", ""),
            "timestamp": document.get("date_assigned") or document.get("created_at"),
        }
    ]


def add_assignment_history_to_documents(documents):
    for document in documents:
        document["assignment_history"] = ensure_assignment_history(document)
    return documents


def combine_queries(*queries):
    active_queries = [query for query in queries if query]
    if not active_queries:
        return {}
    if len(active_queries) == 1:
        return active_queries[0]
    return {"$and": active_queries}


def document_search_query(search_text):
    search_text = (search_text or "").strip()
    if not search_text:
        return {}

    safe_pattern = re.escape(search_text)
    searchable_fields = [
        "reference_number",
        "subject",
        "memo_title",
        "sender",
        "sender_email",
        "assigned_to",
        "recipient",
        "origin_directorate",
        "target_directorate",
        "priority",
        "comment",
        "created_by",
        "pdf_path",
        "content_html",
    ]

    return {
        "$or": [
            {field: {"$regex": safe_pattern, "$options": "i"}}
            for field in searchable_fields
        ]
    }


def handle_dispatch(doc_id, document, recipient_emails, remark, submission_key):
    """Handle document dispatch - creates a new phase"""
    try:
        sent_at = datetime.now()
        sent_by = session.get("name")
        sent_email = session.get("email")
        sent_role = session.get("role")
        sent_rank = session.get("rank")
        sent_appt = session.get("appt")

        clean_remark = bleach.clean(remark) if remark else ""

        new_remark = {
            "user": sent_by,
            "email": sent_email,
            "role": sent_role,
            "rank": sent_rank,
            "appt": sent_appt,
            "remark": f"DISPATCH: {clean_remark}",
            "timestamp": sent_at,
        }

        primary_recipient = recipient_emails[0]
        if primary_recipient == "external":
            target_user = None
            target_name = (
                request.form.get("external_recipient", "").strip()
                or document.get("sender")
                or document.get("target_directorate")
                or "External Entity"
            )
            target_directorate = "EXTERNAL"
            target_rank = ""
            target_appt = ""
        else:
            # Resolve delegation
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

        documents_collection = db["documents"]

        is_registry = session.get("role") in ["registry", "central_registry"]

        # ── PHASE SUPPORT: Get current phase and create new one ──
        current_phase_idx = document.get("current_phase", 0)
        phases = document.get("phases", [])

        # Ensure phases exist
        if not phases:
            phases = [
                {
                    "phase_number": 0,
                    "directorate": document.get("origin_directorate"),
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

        # Determine if this is a cross-directorate dispatch
        origin_directorate = document.get("origin_directorate")
        doc_stage = document.get("doc_lifecycle_stage", DocStage.DRAFT)

        if is_registry:
            # Registry dispatching - create a new phase
            new_phase_num = current_phase_idx + 1

            # Update in-memory status so clones inherit the dispatched status
            document["status"] = (
                "Dispatched" if primary_recipient == "external" else "Treated"
            )
            document["forwarded_status"] = "Dispatched"
            if current_phase_idx < len(document.setdefault("phases", [])):
                document["phases"][current_phase_idx]["status"] = "Treated"
                document["phases"][current_phase_idx]["forwarded_status"] = "Dispatched"

            num_others = len(recipient_emails) - 1
            # Determine phase type
            if primary_recipient == "external":
                phase_type = "external_dispatch"
                new_stage = DocStage.CLOSED
                final_directorate = "EXTERNAL"
                flash_message = (
                    f"Document dispatched externally to {target_appt} successfully."
                )
            elif current_phase_idx == 0:
                phase_type = "at_target"
                new_stage = DocStage.AT_TARGET
                final_directorate = target_directorate
                if num_others > 0:
                    flash_message = f"Document dispatched to {target_appt} in {target_directorate} and {num_others} other(s) successfully."
                else:
                    flash_message = f"Document dispatched to {target_appt} in {target_directorate} successfully."
            else:
                # Dispatching reply back to origin
                phase_type = "reply_to_origin"
                new_stage = DocStage.CLOSED
                final_directorate = origin_directorate
                if num_others > 0:
                    flash_message = f"Reply dispatched back to {origin_directorate} and {num_others} other(s) successfully."
                else:
                    flash_message = (
                        f"Reply dispatched back to {origin_directorate} successfully."
                    )

            # ── CREATE NEW PHASE ──
            new_phase = {
                "phase_number": new_phase_num,
                "directorate": final_directorate,
                "type": phase_type,
                "started_at": sent_at,
                "started_by": sent_email,
                "current_holder": (
                    None if primary_recipient == "external" else primary_recipient
                ),
                "review_chain": [],  # RESET for new directorate (no registry)
                "assignment_history": [],  # RESET
                "seen_stamp_history": [],
                "status": (
                    "Dispatched" if primary_recipient == "external" else "Pending"
                ),
                "forwarded_status": (
                    "Dispatched" if primary_recipient == "external" else "Not Forwarded"
                ),
            }

            # Dispatch entry for full history
            dispatch_entry = {
                "assigned_to": (
                    "External Recipient"
                    if primary_recipient == "external"
                    else primary_recipient
                ),
                "assigned_to_name": target_name,
                "assigned_to_rank": target_rank,
                "assigned_to_appt": target_appt,
                "assigned_by": sent_email,
                "assigned_by_name": sent_by,
                "assigned_by_role": sent_role,
                "assigned_by_rank": sent_rank,
                "assigned_by_appt": sent_appt,
                "remark": (
                    f"EXTERNAL DISPATCH: {clean_remark}"
                    if primary_recipient == "external"
                    else f"DISPATCH: {clean_remark}"
                ),
                "timestamp": sent_at,
                "action_type": "dispatch",
            }

            # Update document with new phase
            dispatch_destination = {
                "directorate": final_directorate,
                "recipient": (
                    "External Recipient"
                    if primary_recipient == "external"
                    else primary_recipient
                ),
                "recipient_name": target_name,
                "dispatched_at": sent_at,
            }

            # Accumulate target directorates for registry tracking
            dispatched_dirs = []
            if primary_recipient == "external":
                dispatched_dirs = ["EXTERNAL"]
            else:
                if target_directorate:
                    dispatched_dirs.append(target_directorate)
                for recipient_email in recipient_emails[1:]:
                    extra_user = db.users.find_one({"email": recipient_email})
                    if extra_user and extra_user.get("directorate"):
                        dispatched_dirs.append(extra_user.get("directorate"))

            # Deduplicate while preserving order
            seen_dirs = set()
            dispatched_dirs = [
                x for x in dispatched_dirs if not (x in seen_dirs or seen_dirs.add(x))
            ]

            # Update in-memory so clones inherit it too
            document["dispatched_directorates"] = dispatched_dirs

            # Build update data WITHOUT pushing the new phase yet (to avoid conflict)
            update_data = {
                "$set": {
                    "current_phase": new_phase_num,
                    "current_holder": (
                        None if primary_recipient == "external" else primary_recipient
                    ),
                    "assigned_to": (
                        None if primary_recipient == "external" else primary_recipient
                    ),
                    "target_directorate": final_directorate,
                    "viewed_by_target": False,
                    "sender_notified": False,
                    "status": (
                        "Dispatched" if primary_recipient == "external" else "Treated"
                    ),
                    "forwarded_status": "Dispatched",
                    f"phases.{current_phase_idx}.status": "Treated",
                    f"phases.{current_phase_idx}.forwarded_status": "Dispatched",
                    f"phases.{current_phase_idx}.current_holder": None,
                    "dispatched_at": sent_at,
                    "dispatched_to": (
                        "External Recipient"
                        if primary_recipient == "external"
                        else primary_recipient
                    ),
                    "dispatched_to_name": target_name,
                    "dispatched_to_rank": target_rank,
                    "dispatched_to_appt": target_appt,
                    "dispatched_by": sent_by,
                    "dispatched_by_email": sent_email,
                    "dispatched_by_role": sent_role,
                    "dispatch_destination": dispatch_destination,
                    "doc_lifecycle_stage": new_stage,
                    "has_been_dispatched": True,
                    "dispatched_directorates": dispatched_dirs,
                },
                "$push": {
                    "phases": new_phase,
                    "full_assignment_history": dispatch_entry,
                    "full_review_chain": {
                        "email": (
                            "external"
                            if primary_recipient == "external"
                            else primary_recipient
                        ),
                        "name": target_name,
                        "rank": target_rank,
                        "appt": target_appt,
                        "role": target_user.get("role") if target_user else None,
                    },
                    f"phases.{current_phase_idx}.assignment_history": dispatch_entry,
                    "remarks": new_remark,
                },
            }

            # Separate the phases push to avoid MongoDB conflict
            phases_push = update_data["$push"].pop("phases", None)

            result = documents_collection.update_one(
                {"_id": ObjectId(doc_id)}, update_data
            )

            # Now push the new phase in a second operation (FIXED SYNTAX)
            if phases_push is not None:
                documents_collection.update_one(
                    {"_id": ObjectId(doc_id)},
                    {"$push": {"phases": phases_push}},  # <-- correct key
                )

        else:
            # Director/CDSA forwarding to Registry - no new phase yet
            new_status = "Treated"
            new_forward_status = "Forwarded for Dispatch"
            flash_message = (
                f"Document forwarded to Registry ({target_appt}) for dispatch."
            )

            # FIX: Set final_directorate to recipient's directorate, not origin
            final_directorate = target_directorate

            if current_phase_idx > 0:
                new_stage = DocStage.AWAITING_DISPATCH_TO_ORIGIN
            else:
                new_stage = DocStage.AWAITING_DISPATCH_TO_TARGET

            dispatch_entry = {
                "assigned_to": primary_recipient,
                "assigned_to_name": target_name,
                "assigned_to_rank": target_rank,
                "assigned_to_appt": target_appt,
                "assigned_by": sent_email,
                "assigned_by_name": sent_by,
                "assigned_by_role": sent_role,
                "assigned_by_rank": sent_rank,
                "assigned_by_appt": sent_appt,
                "remark": f"DISPATCH TO REGISTRY: {clean_remark}",
                "timestamp": sent_at,
                "action_type": "dispatch_to_registry",
            }

            update_data = {
                "$set": {
                    "current_holder": primary_recipient,
                    "assigned_to": primary_recipient,
                    f"phases.{current_phase_idx}.current_holder": primary_recipient,
                    "target_directorate": final_directorate,  # <-- now correct
                    "viewed_by_target": False,
                    "sender_notified": False,
                    "status": new_status,
                    "forwarded_status": new_forward_status,
                    f"phases.{current_phase_idx}.status": new_status,
                    f"phases.{current_phase_idx}.forwarded_status": new_forward_status,
                    "dispatched_at": sent_at,
                    "dispatched_to": primary_recipient,
                    "dispatched_to_name": target_name,
                    "dispatched_to_rank": target_rank,
                    "dispatched_to_appt": target_appt,
                    "dispatched_by": sent_by,
                    "dispatched_by_email": sent_email,
                    "dispatched_by_role": sent_role,
                    "doc_lifecycle_stage": new_stage,
                },
                "$push": {
                    "full_assignment_history": dispatch_entry,
                    "full_review_chain": {
                        "email": primary_recipient,
                        "name": target_name,
                        "rank": target_rank,
                        "appt": target_appt,
                        "role": target_user.get("role") if target_user else None,
                    },
                    f"phases.{current_phase_idx}.assignment_history": dispatch_entry,
                    "remarks": new_remark,
                },
            }

            result = documents_collection.update_one(
                {"_id": ObjectId(doc_id)}, update_data
            )

            # Socket notification for registry recipient
            sender_desc = sent_appt or sent_role.replace("_", " ").title()
            emit_document_notification(
                primary_recipient,
                doc_id,
                document.get("subject", "No Subject"),
                sender_desc,
                clean_remark,
                action="forward_for_dispatch",
                doc_reference=document.get("reference_number", ""),
            )

        # ── Update reply correspondence visibility ──
        if is_registry:
            if new_stage in (DocStage.CLOSED, DocStage.AT_TARGET):
                correspondence = document.get("correspondence", [])
                reply_entries = [c for c in correspondence if c.get("sequence", 0) > 0]
                if reply_entries:
                    latest_reply = max(
                        reply_entries, key=lambda c: c.get("sequence", 0)
                    )
                    latest_seq = latest_reply.get("sequence")
                    documents_collection.update_one(
                        {
                            "_id": ObjectId(doc_id),
                            "correspondence.sequence": latest_seq,
                        },
                        {
                            "$set": {
                                "correspondence.$.visibility_status": CorrespondenceVisibility.DISPATCHED,
                                "correspondence.$.review_status": CorrespondenceReview.DISPATCHED,
                                "correspondence.$.forwarded_status": "Dispatched",
                            }
                        },
                    )

        if result.modified_count == 0:
            flash("Document dispatch failed. Please try again.", "error")
            session.pop(submission_key, None)
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id)
            )

        # ── Extra recipients (clone document for each) ──
        if primary_recipient != "external":
            for recipient_email in recipient_emails[1:]:
                extra_user = db.users.find_one({"email": recipient_email})
                extra_directorate = (
                    extra_user.get("directorate") if extra_user else None
                )
                extra_name = (
                    extra_user.get("name", recipient_email)
                    if extra_user
                    else recipient_email
                )
                extra_rank = extra_user.get("rank") if extra_user else None
                extra_appt = extra_user.get("appt") if extra_user else None

                cloned = clone_document_for_recipient(
                    document, recipient_email, extra_directorate, is_dispatch=True
                )
                cloned.setdefault("remarks", [])
                cloned["remarks"].append(copy.deepcopy(new_remark))
                cloned.setdefault(
                    "assignment_history", ensure_assignment_history(document)
                )
                cloned["assignment_history"].append(
                    assignment_history_entry(
                        recipient_email,
                        extra_user,
                        f"DISPATCH: {clean_remark}",
                        sent_at,
                    )
                )
                cloned["status"] = "Treated"
                cloned["forwarded_status"] = "Dispatched"
                cloned["dispatched_at"] = sent_at
                cloned["dispatched_to"] = recipient_email
                cloned["dispatched_to_name"] = extra_name
                cloned["dispatched_to_rank"] = extra_rank
                cloned["dispatched_to_appt"] = extra_appt
                cloned["dispatched_by"] = sent_by
                cloned["dispatched_by_email"] = sent_email
                cloned["dispatched_by_role"] = sent_role
                cloned["target_directorate"] = extra_directorate
                cloned["parent_doc_id"] = str(document.get("_id"))
                cloned["forwarded_at"] = sent_at
                cloned["forwarded_to"] = recipient_email
                cloned["forwarded_to_name"] = extra_name
                cloned["forwarded_to_rank"] = extra_rank
                cloned["forwarded_to_appt"] = extra_appt
                cloned["forwarded_by"] = sent_by
                cloned["forwarded_by_email"] = sent_email

                # ── PHASE SUPPORT: Clone phases too ──
                cloned["current_phase"] = new_phase_num
                cloned["doc_lifecycle_stage"] = new_stage
                cloned["has_been_dispatched"] = True

                cloned_new_phase = copy.deepcopy(new_phase)
                cloned_new_phase["current_holder"] = recipient_email
                cloned_new_phase["directorate"] = extra_directorate
                cloned_new_phase["review_chain"] = (
                    []
                )  # Ensure empty review chain (no registry)

                cloned.setdefault("phases", []).append(cloned_new_phase)

                cloned.setdefault("full_assignment_history", [])
                cloned["full_assignment_history"].append(
                    {
                        "assigned_to": recipient_email,
                        "assigned_to_name": extra_name,
                        "assigned_by": sent_email,
                        "assigned_by_name": sent_by,
                        "remark": f"DISPATCH: {clean_remark}",
                        "timestamp": sent_at,
                        "action_type": "dispatch_clone",
                    }
                )

                documents_collection.insert_one(cloned)

                # Socket notification for extra dispatched recipient
                sender_desc = sent_appt or sent_role.replace("_", " ").title()
                emit_document_notification(
                    recipient_email,
                    cloned.get("_id"),
                    cloned.get("subject", "No Subject"),
                    sender_desc,
                    clean_remark,
                    action="dispatched",
                    doc_reference=cloned.get("reference_number", ""),
                )

        # Socket notification for primary recipient
        if primary_recipient != "external":
            sender_desc = sent_appt or sent_role.replace("_", " ").title()
            emit_document_notification(
                primary_recipient,
                doc_id,
                document.get("subject", "No Subject"),
                sender_desc,
                clean_remark,
                action="dispatched",
                doc_reference=document.get("reference_number", ""),
            )

        session.pop(submission_key, None)
        flash(flash_message, "success")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        print(error_details)
        flash(f"Dispatch error: {str(e)}", "error")
        session.pop(submission_key, None)
        return redirect(
            request.referrer or url_for("base_document_routes.documents_content")
        )


def handle_target_forward(doc_id, document, recipient_emails, remark, submission_key):
    """Handle forwarding within the target directorate - updates current phase"""
    try:
        sent_at = datetime.now()
        sent_by = session.get("name")
        sent_email = session.get("email")
        sent_role = session.get("role")
        sent_rank = session.get("rank")
        sent_appt = session.get("appt")

        clean_remark = bleach.clean(remark) if remark else ""

        new_remark = {
            "user": sent_by,
            "email": sent_email,
            "role": sent_role,
            "rank": sent_rank,
            "appt": sent_appt,
            "remark": f"TARGET DIRECTORATE ACTION: {clean_remark}",
            "timestamp": sent_at,
        }

        primary_recipient = recipient_emails[0]

        # Resolve delegation
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

        documents_collection = db["documents"]

        # ── PHASE SUPPORT: Get current phase ──
        current_phase_idx = document.get("current_phase", 0)

        # Get or create current phase data
        phases = document.get("phases", [])
        if not phases:
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

        # ── Update both phase and root ──
        assignment_entry = {
            "assigned_to": primary_recipient,
            "assigned_to_name": target_name,
            "assigned_to_rank": target_rank,
            "assigned_to_appt": target_appt,
            "assigned_by": sent_email,
            "assigned_by_name": sent_by,
            "assigned_by_role": sent_role,
            "assigned_by_rank": sent_rank,
            "assigned_by_appt": sent_appt,
            "remark": f"TARGET DIRECTORATE ACTION: {clean_remark}",
            "timestamp": sent_at,
            "action_type": "target_forward",
        }

        # Prevent duplicate entries in review chain
        existing_chain = current_phase.get("review_chain", [])
        is_already_in_chain = any(
            m.get("email") == primary_recipient
            or (m.get("appt") and target_appt and m.get("appt").strip().lower() == target_appt.strip().lower())
            or (m.get("role") and target_user and target_user.get("role") in ["director", "cdsa", "dcdsa"] and m.get("role") == target_user.get("role"))
            for m in existing_chain
        )

        push_ops = {
            f"{phase_path}.assignment_history": assignment_entry,
            "assignment_history": assignment_entry,
            "remarks": new_remark,
            "full_assignment_history": assignment_entry,
        }

        if not is_already_in_chain:
            review_entry = {
                "email": primary_recipient,
                "name": target_name,
                "rank": target_rank,
                "appt": target_appt,
                "role": target_user.get("role") if target_user else None,
            }
            push_ops[f"{phase_path}.review_chain"] = review_entry
            push_ops["full_review_chain"] = review_entry

        update_data = {
            "$set": {
                f"{phase_path}.current_holder": primary_recipient,
                f"{phase_path}.status": "Under Review",
                f"{phase_path}.forwarded_status": "Forwarded to Director",
                "current_holder": primary_recipient,
                "assigned_to": primary_recipient,
                "target_directorate": target_directorate,
                "viewed_by_target": False,
                "sender_notified": False,
                "status": "Under Review",
                "forwarded_status": "Forwarded to Director",
                "target_forwarded_at": sent_at,
                "target_forwarded_to": primary_recipient,
                "target_forwarded_to_name": target_name,
                "target_forwarded_by": sent_by,
                "target_forwarded_by_email": sent_email,
                "doc_lifecycle_stage": DocStage.AT_TARGET,
            },
            "$push": push_ops,
        }

        result = documents_collection.update_one({"_id": ObjectId(doc_id)}, update_data)

        if result.modified_count == 0:
            flash("Target directorate forward failed. Please try again.", "error")
            session.pop(submission_key, None)
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id)
            )

        # Socket notification trigger
        if primary_recipient:
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

        session.pop(submission_key, None)
        disp_target = target_appt or target_name or "Recipient"
        flash(
            f"Document forwarded to {disp_target} for action.",
            "success",
        )
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    except Exception as e:
        import traceback
        print(f"TARGET FORWARD ERROR: {str(e)}")
        print(traceback.format_exc())
        session.pop(submission_key, None)
        flash("An error occurred while forwarding in the target directorate.", "error")
        return redirect(
            request.referrer or url_for("base_document_routes.documents_content")
        )


def check_user_signing_password(user_or_hash, password):
    """
    Safely verify a signing password against stored hash, supporting:
    - werkzeug hashes (scrypt, pbkdf2)
    - bcrypt hashes
    - plaintext fallback (seeds/dev)
    """
    if not password:
        return False

    if isinstance(user_or_hash, dict):
        stored_hash = user_or_hash.get("signing_password_hash")
        # fallback to password_hash if no signing_password_hash is set
        if not stored_hash:
            stored_hash = user_or_hash.get("password_hash")
    else:
        stored_hash = user_or_hash

    if not stored_hash:
        return False

    # 1. Plaintext comparison
    if stored_hash == password:
        return True

    # 2. Werkzeug check
    try:
        from werkzeug.security import check_password_hash
        if check_password_hash(stored_hash, password):
            return True
    except Exception:
        pass

    # 3. Bcrypt check
    try:
        from flask_bcrypt import Bcrypt
        bcrypt = Bcrypt()
        if bcrypt.check_password_hash(stored_hash, password):
            return True
    except Exception:
        pass

    return False
