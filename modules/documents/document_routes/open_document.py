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
    ensure_assignment_history,
    get_directorate_from_email,
)
from utils.constants import DocStage

open_document_routes = Blueprint("open_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@open_document_routes.route("/view_main_document/<doc_id>")
def view_main_document(doc_id):

    document = db.documents.find_one({"_id": ObjectId(doc_id)})

    if not document:
        return "Document not found", 404

    file_id = document.get("main_file_id")

    if not file_id:
        return "No file attached", 404

    grid_out = fs.get(ObjectId(file_id))

    return send_file(
        io.BytesIO(grid_out.read()),
        mimetype=grid_out.content_type,
        download_name=grid_out.filename,
        as_attachment=False,
    )


@open_document_routes.route("/open_document/<doc_id>")
def open_document(doc_id):
    document = db.documents.find_one({"_id": ObjectId(doc_id)})
    came_from = request.args.get("ref", "documents_content")

    if not document:
        flash("Document not found.")
        return redirect(url_for("base_document_routes.documents_content"))

    user_email = session.get("email")
    user_directorate = (session.get("directorate") or "").strip().upper()
    user_role = session.get("role")

    # ── PHASE SUPPORT: Check access using phases ──
    # Global admins have access to all documents
    is_global_admin = user_role in ["super_admin", "cdsa", "dcdsa"]

    # Directorate-level admins/directors have access to documents in their directorate
    is_dir_admin = user_role in ["registry", "central_registry", "director"]
    has_dir_access = False
    if is_dir_admin and user_directorate:
        doc_origin_dir = (document.get("origin_directorate") or "").strip().upper()
        doc_target_dir = (document.get("target_directorate") or "").strip().upper()
        has_dir_access = (
            doc_origin_dir == user_directorate
            or doc_target_dir == user_directorate
            or any(
                (phase.get("directorate") or "").strip().upper() == user_directorate
                for phase in document.get("phases", [])
            )
        )

    has_access = (
        is_global_admin
        or has_dir_access
        or document.get("sender_email") == user_email
        or document.get("assigned_to") == user_email
        or document.get("current_holder") == user_email
        or user_email in document.get("cc", [])
        or
        # Check assignment history (including phase-specific)
        any(
            h.get("assigned_to") == user_email
            for h in document.get("assignment_history", [])
        )
        or
        # Check phase-specific assignment history
        any(
            h.get("assigned_to") == user_email
            for phase in document.get("phases", [])
            for h in phase.get("assignment_history", [])
        )
        or
        # Check correspondence assignments
        any(
            a.get("assigned_to") == user_email
            for c in document.get("correspondence", [])
            for a in c.get("assignments", [])
        )
    )

    if not has_access:
        flash("Access denied.", "error")
        return redirect(url_for("base_document_routes.documents_content"))

    current_email = session.get("email")
    current_user_obj = db.users.find_one({"email": current_email}) if current_email else None

    user_rank = (
        (current_user_obj.get("rank") if current_user_obj else "")
        or (current_user_obj.get("rankOrGrade") if current_user_obj else "")
        or (current_user_obj.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade") if current_user_obj else "")
        or (current_user_obj.get("onboarding_data", {}).get("step_1", {}).get("rank") if current_user_obj else "")
        or session.get("rank")
        or session.get("rankOrGrade")
        or ""
    )
    if not user_rank or user_rank == "None":
        user_rank = ""

    user_appt = (
        (current_user_obj.get("appt") if current_user_obj else "")
        or session.get("appt")
        or (current_user_obj.get("role", "").replace("_", " ").title() if current_user_obj else "")
        or (session.get("role", "").replace("_", " ").title() if session.get("role") else "")
        or ""
    )
    if not user_appt or user_appt == "None":
        user_appt = ""

    user_name = (
        (current_user_obj.get("name") if current_user_obj else "")
        or session.get("name")
        or ""
    )

    # ── Determine which sequence to show ──
    requested_seq = request.args.get("view_seq", None)
    if requested_seq is not None:
        try:
            active_seq = int(requested_seq)
        except ValueError:
            active_seq = 0
    else:
        active_seq = 0

    if current_email:
        clean_email = current_email.strip().lower()
        db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {"$addToSet": {"read_by": clean_email}},
        )
        if document.get("assigned_to") == current_email and not document.get("viewed_by_target"):
            db.documents.update_one(
                {"_id": ObjectId(doc_id)}, {"$set": {"viewed_by_target": True}}
            )
            document["viewed_by_target"] = True

        try:
            db.notifications.update_many(
                {
                    "doc_id": str(doc_id),
                    "$or": [
                        {"target.email": clean_email},
                        {"target.userId": clean_email},
                    ],
                },
                {"$addToSet": {"readBy": clean_email}},
            )
        except Exception:
            pass

        try:
            from modules.extensions import socketio
            from modules.documents.functions_helper import calculate_user_document_counts

            counts = calculate_user_document_counts(
                clean_email, session.get("role"), session.get("directorate")
            )
            socketio.emit("update_sidebar_badges", counts, room=f"USER_{clean_email}")
            sn = session.get("service_number")
            if sn:
                safe_sn = sn.replace("/", "_")
                socketio.emit("update_sidebar_badges", counts, room=f"USER_{safe_sn}")
        except Exception as socket_err:
            pass

    document["_id"] = str(document["_id"])
    document["assignment_history"] = ensure_assignment_history(document)

    # ── Resolve correspondence list and active entry with phase-level visibility ──
    raw_correspondence_list = document.get("correspondence", [])
    correspondence_list = []

    user_role = session.get("role")
    user_email = session.get("email")
    user_directorate = (session.get("directorate") or "").strip().upper()
    current_holder = document.get("current_holder")

    is_holder_registry = False
    if current_holder:
        holder_user = db.users.find_one({"email": current_holder})
        if holder_user and holder_user.get("role") in ["registry", "central_registry"]:
            holder_dir = (holder_user.get("directorate") or "").strip().upper()
            if holder_dir == user_directorate:
                is_holder_registry = True

    for c in raw_correspondence_list:
        seq = c.get("sequence", 0)
        if seq == 0:
            correspondence_list.append(c)
            continue

        if user_role == "super_admin":
            correspondence_list.append(c)
            continue

        # 1. The author of the reply always sees it
        author_email = c.get("author", {}).get("email", "")
        if author_email == user_email:
            correspondence_list.append(c)
            continue

        # Current holder can see the thread
        if current_holder == user_email:
            correspondence_list.append(c)
            continue

        # 3. Someone explicitly assigned this reply (via assignment history within the reply)
        reply_assigned = any(
            a.get("assigned_to") == user_email for a in c.get("assignments", [])
        )
        if reply_assigned:
            correspondence_list.append(c)
            continue

        # # Registry/Central Registry can see it to dispatch
        # if user_role in ['registry', 'central_registry']:
        #     correspondence_list.append(c)
        #     continue

        # 4. Registry/Central Registry can only see if the reply has been
        #    forwarded *through them* for dispatch (status indicates pending or completed dispatch)
        if user_role in ["registry", "central_registry"]:
            if c.get("forwarded_status") in ["Forwarded for Dispatch", "Dispatched"]:
                correspondence_list.append(c)
                continue
            else:
                # Do NOT show draft replies that were never forwarded to registry
                continue

        # # Check author's directorate
        # author_email = c.get('author', {}).get('email', '')
        # author_dir = get_directorate_from_email(author_email).strip().upper()

        # # Same directorate (internal draft/review)
        # if author_dir == user_directorate:
        #     correspondence_list.append(c)
        #     continue

        author_dir = get_directorate_from_email(author_email).strip().upper()
        if author_dir == user_directorate and c.get("forwarded_status") in [
            "Forwarded",
            "Forwarded for Dispatch",
            "Completed",
            "Dispatched",
        ]:
            # If document is still at the local registry and not yet forwarded, hide from officers
            if (
                document.get("doc_lifecycle_stage")
                in [DocStage.CLOSED, DocStage.AT_TARGET]
                and is_holder_registry
            ):
                if user_role not in ["registry", "central_registry"]:
                    continue
            correspondence_list.append(c)
            continue

        # Officially dispatched
        if (
            c.get("forwarded_status") == "Dispatched"
            or c.get("visibility_status") == "visible"
        ):
            # If document is still at the local registry and not yet forwarded, hide from officers
            if (
                document.get("doc_lifecycle_stage")
                in [DocStage.CLOSED, DocStage.AT_TARGET]
                and is_holder_registry
            ):
                if user_role not in ["registry", "central_registry"]:
                    continue
            correspondence_list.append(c)
            continue

    active_correspondence = None
    is_current_editable = False

    for c in correspondence_list:
        if c.get("sequence") == active_seq:
            active_correspondence = c
            is_current_editable = c.get("is_editable", False)
            break

    if not active_correspondence and correspondence_list:
        active_correspondence = correspondence_list[0]
        active_seq = active_correspondence.get("sequence", 0)

    # ── PHASE SUPPORT: Get the current phase data ──
    current_phase_idx = document.get("current_phase", 0)
    phases = document.get("phases", [])
    current_phase = (
        phases[current_phase_idx] if current_phase_idx < len(phases) else None
    )

    # ── PHASE SUPPORT: Find the phase for this user's directorate ──

    # ── PHASE SUPPORT: Find the phase to display ──
    user_directorate = (session.get("directorate") or "").strip().upper()

    display_phase = None

    # Priority 1: Phase matching user's directorate (most recent first)
    for phase in reversed(document.get("phases", [])):
        phase_dir = (phase.get("directorate") or "").strip().upper()
        if phase_dir == user_directorate:
            display_phase = phase
            break

    # Priority 2: Current active phase
    if not display_phase:
        current_phase_idx = document.get("current_phase", 0)
        phases = document.get("phases", [])
        if phases and current_phase_idx < len(phases):
            display_phase = phases[current_phase_idx]

    # Priority 3: Last phase as final fallback
    if not display_phase and document.get("phases"):
        display_phase = document["phases"][-1]

    # Extract data from the chosen phase
    phase_number = display_phase.get("phase_number", 0) if display_phase else 0
    phase_directorate = display_phase.get("directorate") if display_phase else ""
    phase_assignment_history = (
        display_phase.get("assignment_history", []) if display_phase else []
    )
    phase_review_chain = display_phase.get("review_chain", []) if display_phase else []

    # If the display phase does not match the user's directorate, clear the assignment history
    # (except for super_admin) to prevent leaking internal routing history across directorates.
    if display_phase and session.get("role") != "super_admin":
        display_dir = (display_phase.get("directorate") or "").strip().upper()
        if display_dir != user_directorate:
            phase_assignment_history = []
    phase_seen_stamps = (
        display_phase.get("seen_stamp_history", []) if display_phase else []
    )
    # Keep active phase current_holder in sync with document current_holder
    if display_phase and phases and current_phase_idx < len(phases):
        if display_phase == phases[current_phase_idx] and document.get("current_holder"):
            if display_phase.get("current_holder") != document.get("current_holder"):
                display_phase["current_holder"] = document.get("current_holder")
                try:
                    db.documents.update_one(
                        {"_id": ObjectId(doc_id)},
                        {"$set": {f"phases.{current_phase_idx}.current_holder": document.get("current_holder")}}
                    )
                except Exception as sync_err:
                    print(f"Error syncing phase current_holder: {sync_err}")

    phase_current_holder = (
        display_phase.get("current_holder")
        if display_phase
        else document.get("current_holder")
    )

    # Check if user has a pending acknowledgement
    pending_acknowledgement = None
    all_stamps = (
        phase_seen_stamps
        if phase_seen_stamps
        else document.get("seen_stamp_history", [])
    )
    for stamp in all_stamps:
        for entry in stamp.get("assignments", []):
            if (
                entry.get("email") == current_email
                and entry.get("action_type") == "acknowledgement"
                and not entry.get("acknowledged")
            ):
                pending_acknowledgement = {
                    "stamped_by": stamp.get("stamped_by"),
                    "stamped_rank": stamp.get("stamped_rank"),
                    "stamped_appt": stamp.get("stamped_appt"),
                    "stamped_at": stamp.get("stamped_at"),
                }
                break
        if pending_acknowledgement:
            break

    # ── Fetch parent document if this is a linked correspondence ──
    parent_document = None
    if document.get("parent_doc_id"):
        try:
            parent_doc = db.documents.find_one(
                {"_id": ObjectId(document["parent_doc_id"])}
            )
            if parent_doc:
                parent_doc["_id"] = str(parent_doc["_id"])
                parent_document = parent_doc
        except Exception:
            pass

    # ── Fetch all correspondences linked TO this document ──
    linked_correspondences = []
    try:
        linked_cursor = db.documents.find({"parent_doc_id": doc_id}).sort(
            "created_at", -1
        )

        for linked in linked_cursor:
            linked["_id"] = str(linked["_id"])
            linked_correspondences.append(linked)
    except Exception as e:
        print(f"LINKED DOCS ERROR: {str(e)}")

    # ── Fetch dispatch status of cloned copies ──
    dispatch_tracking = []
    try:
        parent_id = document.get("parent_doc_id")
        parent_doc = None
        if parent_id:
            parent_doc = db.documents.find_one({"_id": ObjectId(parent_id)})
        else:
            parent_doc = document

        if parent_doc:
            parent_doc_id_str = str(parent_doc["_id"])
            # 1. Add parent document dispatch destination if internal
            if (
                (
                    parent_doc.get("has_been_dispatched")
                    or parent_doc.get("dispatched_to")
                )
                and parent_doc.get("dispatched_to") != "External Recipient"
                and parent_doc.get("dispatched_to") != "external"
            ):
                dispatch_tracking.append(
                    {
                        "directorate": parent_doc.get("target_directorate", "N/A"),
                        "appt": parent_doc.get("dispatched_to_appt") or "Registry",
                        "viewed": parent_doc.get("viewed_by_target", False),
                        "dispatched_at": parent_doc.get("dispatched_at"),
                    }
                )

            # 2. Add all cloned copies
            clones_cursor = db.documents.find({"parent_doc_id": parent_doc_id_str})
            for c in clones_cursor:
                if (
                    c.get("dispatched_to") != "External Recipient"
                    and c.get("dispatched_to") != "external"
                ):
                    dispatch_tracking.append(
                        {
                            "directorate": c.get("target_directorate", "N/A"),
                            "appt": c.get("dispatched_to_appt")
                            or c.get("forwarded_to_appt")
                            or "Registry",
                            "viewed": c.get("viewed_by_target", False),
                            "dispatched_at": c.get("dispatched_at"),
                        }
                    )
    except Exception as e:
        print(f"DISPATCH TRACKING ERROR: {e}")

    users = list(
        db.users.find(
            {
                "email": {"$ne": current_email},
                "is_approval_role": True
             },
            {
                "name": 1,
                "email": 1,
                "role": 1,
                "directorate": 1,
                "appt": 1,
                "rankOrGrade": 1,
                "_id": 0,
            },
        ).sort("name", 1)
    )

    # ── PHASE SUPPORT: Get seen stamp users from the phase's directorate ──
    seen_stamp_users = []
    user_role = session.get("role")

    if user_role in ["cdsa", "dcdsa"]:
        cdsa_directorate = session.get("directorate")
        raw_users = list(
            db.users.find(
                {"directorate": cdsa_directorate, "is_approval_role": True},
                {
                    "name": 1,
                    "email": 1,
                    "role": 1,
                    "rankOrGrade": 1,
                    "appt": 1,
                    "directorate": 1,
                    "signature_image": 1,
                    "_id": 0,
                },
            )
        )
        all_directors = list(
            db.users.find(
                {"role": {"$in": ["director", "dcdsa"]}, "is_approval_role": True},
                {
                    "name": 1,
                    "email": 1,
                    "role": 1,
                    "rankOrGrade": 1,
                    "appt": 1,
                    "directorate": 1,
                    "signature_image": 1,
                    "_id": 0,
                },
            )
        )
        seen_emails = {u["email"] for u in raw_users}
        for director in all_directors:
            if director["email"] not in seen_emails:
                raw_users.append(director)
    else:
        target_directorate = (
            session.get("directorate")
            or phase_directorate
            or document.get("target_directorate")
            or document.get("origin_directorate", "")
        )
        if target_directorate:
            raw_users = list(
                db.users.find(
                    {"directorate": target_directorate, "is_approval_role": True},
                    {
                        "name": 1,
                        "email": 1,
                        "role": 1,
                        "rankOrGrade": 1,
                        "appt": 1,
                        "directorate": 1,
                        "signature_image": 1,
                        "_id": 0,
                    },
                )
            )
        else:
            raw_users = []

    ROLE_ORDER = [
        "cdsa",
        "dcdsa",
        "director",
        "dd",
        "ad",
        "so",
        "so2",
        "so1",
        "officer",
        "civilian_head_cao",
        "civilian_head",
        "admin_officer",
        "registry",
    ]

    APPT_KEYWORDS = [
        ["director"],
        ["dd ", "deputy director"],
        ["ad ", "assistant dir"],
        ["Capt (NN)"],
        ["so2"],
        ["so1"],
        ["Capt"],
        ["civ hod", "civilian hod"],
        ["chief clerk"],
        ["admin officer"],
    ]

    def get_sort_key(user):
        role = (user.get("role") or "").lower().strip()
        if role in ROLE_ORDER:
            role_idx = ROLE_ORDER.index(role)
        else:
            role_idx = len(ROLE_ORDER) + 1

        appt = (user.get("appt") or "").lower().strip()
        appt_idx = len(APPT_KEYWORDS) + 1
        for idx, keywords in enumerate(APPT_KEYWORDS):
            for kw in keywords:
                if kw in appt:
                    appt_idx = idx
                    break
        return (role_idx, appt_idx)

    if user_role in ["cdsa", "dcdsa"]:
        cdsa_directorate = (session.get("directorate") or "").strip().upper()

        def get_cdsa_sort_key(user):
            role = (user.get("role") or "").lower().strip()
            dir_name = (user.get("directorate") or "").strip().upper()
            if role == "cdsa":
                priority = 1
            elif role == "dcdsa":
                priority = 2
            elif role == "director" and dir_name == cdsa_directorate:
                priority = 3
            elif role == "director":
                priority = 4
            elif role == "ad":
                priority = 5
            elif role == "central_registry":
                priority = 6
            else:
                priority = 7

            if priority == 7:
                role_idx, appt_idx = get_sort_key(user)
            else:
                role_idx, appt_idx = 0, 0
            return (
                priority,
                role_idx,
                appt_idx,
                (user.get("name") or "").lower().strip(),
            )

        seen_stamp_users = sorted(raw_users, key=get_cdsa_sort_key)
    else:
        seen_stamp_users = sorted(raw_users, key=get_sort_key)

    preview_html = None
    if not document.get("content_html") and document.get(
        "pdf_path", ""
    ).lower().endswith((".docx", ".doc")):
        try:
            import mammoth

            abs_path = os.path.join(current_app.root_path, document["pdf_path"])
            with open(abs_path, "rb") as docx_file:
                result = mammoth.convert_to_html(docx_file)
                preview_html = result.value
        except Exception as e:
            print(f"Error converting document for preview: {e}")

    doc_status = document.get("status", "pending")
    root_forwarded_status = document.get("forwarded_status")

    if root_forwarded_status:
        forward_status = root_forwarded_status
    elif active_correspondence:
        forward_status = active_correspondence.get("forwarded_status", "Not Forwarded")
    else:
        forward_status = "Not Forwarded"

    # ── Determine directorate relationships ──
    is_cross_directorate = document.get("origin_directorate") != document.get(
        "target_directorate"
    )
    is_target_directorate = session.get("directorate") == document.get(
        "target_directorate"
    )
    is_origin_directorate = session.get("directorate") == document.get(
        "origin_directorate"
    )
    is_signing_role = session.get("role") in [
        "director",
        "cdsa",
        "dcdsa",
        "dd",
    ]

    is_ready_for_target_action = (
        doc_status in ["Treated", "Dispatched"]
        and forward_status in ["Dispatched", "Completed"]
        and is_target_directorate
    )

    # print("=" * 60)
    # print(f"user_directorate: {user_directorate}")
    # print(f"display_phase: {display_phase}")
    # print(f"phase_assignment_history: {phase_assignment_history}")
    # print(f"phase_number: {phase_number}")
    # print(f"phase_directorate: {phase_directorate}")
    # print("=" * 60)

    # ── PHASE SUPPORT: Build chain context using phase data ──
    chain_ctx = _build_chain_context_phase(
        document, active_seq, current_email, phases, current_phase_idx
    )

    # Find the task assigned to this user's directorate from seen stamp history
    current_dir_task = None
    if user_directorate:
        user_dir_upper = user_directorate.strip().upper()
        all_stamps = []

        # Extract seen stamp history from root
        root_stamps = document.get("seen_stamp_history", [])
        if isinstance(root_stamps, list):
            all_stamps.extend(root_stamps)

        # Extract seen stamp history from all phases
        for p in document.get("phases", []):
            p_stamps = p.get("seen_stamp_history", [])
            if isinstance(p_stamps, list):
                all_stamps.extend(p_stamps)

        # Sort stamps by timestamp descending (most recent first)
        def get_stamp_time(s):
            t = s.get("stamped_at")
            if isinstance(t, datetime):
                return t
            return datetime.min

        sorted_stamps = sorted(all_stamps, key=get_stamp_time, reverse=True)

        for stamp in sorted_stamps:
            for assignment in stamp.get("assignments", []):
                assignee_dir = (assignment.get("directorate") or "").strip().upper()
                if assignee_dir == user_dir_upper and assignment.get("task"):
                    current_dir_task = {
                        "user": assignment.get("stamped_by"),
                        "email": assignment.get("stamped_email"),
                        "role": assignment.get("stamped_role"),
                        "rankOrGrade": assignment.get("stamped_rank"),
                        "appt": assignment.get("stamped_appt"),
                        "remark": assignment.get("task"),
                        "timestamp": assignment.get("stamped_at"),
                    }
                    break
            if current_dir_task:
                break

    current_user_obj = db.users.find_one({"email": current_email}) if current_email else None
    current_user_signature = current_user_obj.get("signature_image", "") if current_user_obj else ""

    return render_template(
        "open_document.html",
        document=document,
        dispatch_tracking=dispatch_tracking,
        current_dir_task=current_dir_task,
        current_user_signature=current_user_signature,
        users=users,
        user_appt=user_appt,
        user_rank=user_rank,
        user_name=user_name,
        preview_html=preview_html,
        parent_document=parent_document,
        linked_correspondences=linked_correspondences,
        correspondence_list=correspondence_list,
        active_correspondence=active_correspondence,
        active_seq=active_seq,
        is_current_editable=is_current_editable,
        seen_stamp_users=seen_stamp_users,
        forward_status=forward_status,
        doc_status=doc_status,
        is_target_directorate=is_target_directorate,
        is_origin_directorate=is_origin_directorate,
        is_signing_role=is_signing_role,
        is_cross_directorate=is_cross_directorate,
        is_ready_for_target_action=is_ready_for_target_action,
        now=datetime.now(),
        # ── PHASE SUPPORT: Add phase data to template ──
        phase_review_chain=phase_review_chain,
        phase_assignment_history=phase_assignment_history,
        phase_seen_stamps=phase_seen_stamps,
        phase_current_holder=phase_current_holder,
        pending_acknowledgement=pending_acknowledgement,
        phase_number=phase_number,
        phase_directorate=phase_directorate,
        display_phase=display_phase,
        current_phase=current_phase,
        **chain_ctx,
        active_page=came_from,
        doc_lifecycle_stage=document.get("doc_lifecycle_stage", DocStage.DRAFT),
    )


# ── PHASE SUPPORT: Updated helper function ──
def _build_chain_context_phase(doc, sequence, email, phases, current_phase_idx):
    """
    Build chain context using the phase-based structure.
    First checks the user's directorate phase, falls back to current phase.
    """
    user_directorate = session.get("directorate")

    # Find the phase for this user's directorate
    display_phase = None
    for phase in reversed(phases):
        if phase.get("directorate") == user_directorate:
            display_phase = phase
            break

    if not display_phase and phases:
        display_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else None
        )

    if not display_phase:
        return {
            "chain": [],
            "my_chain_index": -1,
            "is_in_chain": False,
            "is_current_holder": False,
            "is_top": False,
            "is_bottom": False,
            "is_middle": False,
            "above": None,
            "below": None,
        }

    raw_chain = display_phase.get("review_chain", []) if display_phase else []

    # Deduplicate review chain by email and appointment/role
    seen_identifiers = set()
    chain = []
    for m in raw_chain:
        email_k = (m.get("email") or "").strip().lower()
        appt_k = (m.get("appt") or "").strip().lower()
        role_k = (m.get("role") or "").strip().lower()
        
        # Check if already seen
        if email_k and email_k in seen_identifiers:
            continue
        if appt_k and appt_k in seen_identifiers and ("@" not in email_k or email_k == appt_k):
            continue
        if role_k and role_k in ["director", "cdsa", "dcdsa"] and role_k in seen_identifiers and ("@" not in email_k or email_k == role_k):
            continue

        if email_k:
            seen_identifiers.add(email_k)
        if appt_k:
            seen_identifiers.add(appt_k)
        if role_k:
            seen_identifiers.add(role_k)
        chain.append(m)

    # Ensure every member in chain has a valid appt
    for m in chain:
        if not m.get("appt"):
            m_email = m.get("email")
            m_name = m.get("name")
            m_role = m.get("role")
            u = None
            if m_email and "@" in m_email:
                u = db.users.find_one({"email": m_email})
            if not u and m_name:
                u = db.users.find_one({"name": m_name})
            if not u and m_role:
                u = db.users.find_one({"role": m_role})
            if u and u.get("appt"):
                m["appt"] = u.get("appt")
            elif u and u.get("role"):
                m["appt"] = u.get("role").replace("_", " ").title()
            elif m_role:
                m["appt"] = m_role.replace("_", " ").title()
            elif m_name:
                m["appt"] = m_name
            else:
                m["appt"] = "Director" if m_role in ["director", "cdsa", "dcdsa"] else "Reviewer"

    my_index = next((i for i, m in enumerate(chain) if m.get("email") == email), -1)

    is_in_chain = my_index != -1

    # Override: if the user has a pending acknowledgement (meaning they are CC'd), they are not in the review chain
    has_pending_ack = False
    all_stamps = (
        display_phase.get("seen_stamp_history", [])
        if display_phase
        else doc.get("seen_stamp_history", [])
    )
    for stamp in all_stamps:
        for entry in stamp.get("assignments", []):
            if (
                entry.get("email") == email
                and entry.get("action_type") == "acknowledgement"
                and not entry.get("acknowledged")
            ):
                has_pending_ack = True
                break
        if has_pending_ack:
            break

    if has_pending_ack:
        is_in_chain = False

    is_current_holder = display_phase.get("current_holder") == email

    is_top = is_in_chain and my_index == 0
    is_bottom = is_in_chain and my_index == len(chain) - 1
    is_middle = is_in_chain and not is_top and not is_bottom

    above = chain[my_index - 1] if is_in_chain and my_index > 0 else None
    below = chain[my_index + 1] if is_in_chain and my_index < len(chain) - 1 else None

    return {
        "chain": chain,
        "my_chain_index": my_index,
        "is_in_chain": is_in_chain,
        "is_current_holder": is_current_holder,
        "is_top": is_top,
        "is_bottom": is_bottom,
        "is_middle": is_middle,
        "above": above,
        "below": below,
    }
