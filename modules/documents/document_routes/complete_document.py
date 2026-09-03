from flask import Blueprint, request, session, current_app, flash, redirect, url_for
from datetime import datetime
from bson.objectid import ObjectId
from pymongo import MongoClient
import bleach

from utils.constants import DocStage

complete_document_routes = Blueprint("complete_document_routes", __name__)

# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]


@complete_document_routes.route("/complete_document/<doc_id>", methods=["POST"])
@complete_document_routes.route("/conclude_document/<doc_id>", methods=["POST"])
def complete_document(doc_id):
    if "user_id" not in session and "email" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    documents_collection = db["documents"]

    try:
        doc = documents_collection.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        doc = None

    if not doc:
        flash("Document not found.", "error")
        return redirect(url_for("base_document_routes.documents_content"))

    user_role = session.get("role")
    user_email = session.get("email")
    user_name = session.get("name") or user_email
    user_rank = session.get("rank") or ""
    user_appt = session.get("appt") or (user_role.replace("_", " ").title() if user_role else "")

    # Check if already closed
    if doc.get("doc_lifecycle_stage") == DocStage.CLOSED or doc.get("status") == "Completed":
        flash("This correspondence thread is already concluded and filed.", "warning")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    # Authority check: Global admins OR current holder in receiving/origin directorate
    current_phase_idx = doc.get("current_phase", 0)
    phases = doc.get("phases", [])
    phase_holder = (
        phases[current_phase_idx].get("current_holder")
        if phases and current_phase_idx < len(phases)
        else None
    )
    doc_holder = doc.get("current_holder")
    assigned_to = doc.get("assigned_to")

    is_global_admin = user_role in ["super_admin", "cdsa", "dcdsa"]
    is_holder = (
        user_email == doc_holder
        or user_email == phase_holder
        or user_email == assigned_to
    )

    if not is_global_admin and not is_holder:
        flash("Unauthorized: Only the current holder or an authorized officer can conclude and file this document.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    raw_remark = request.form.get("closing_remark", "").strip()
    closing_remark = bleach.clean(raw_remark) if raw_remark else "Noted with thanks. Matter concluded and file closed."

    now = datetime.now()
    phase_path = f"phases.{current_phase_idx}"

    closing_remark_entry = {
        "user": user_name,
        "email": user_email,
        "role": user_role,
        "rank": user_rank,
        "appt": user_appt,
        "remark": f"[CONCLUDED & FILED]: {closing_remark}",
        "timestamp": now,
    }

    closing_history_entry = {
        "action_type": "conclude_and_file",
        "concluded_by": user_email,
        "concluded_by_name": user_name,
        "concluded_by_rank": user_rank,
        "concluded_by_appt": user_appt,
        "assigned_to": "Registry Archives",
        "assigned_to_name": "Filed / P.A.",
        "remark": closing_remark,
        "timestamp": now,
    }

    update_set = {
        "status": "Completed",
        "doc_lifecycle_stage": DocStage.CLOSED,
        "is_editable": False,
        "current_holder": None,
        "date_completed": now,
        "concluded_at": now,
        "concluded_by": user_email,
        "concluded_by_name": user_name,
        "concluded_by_rank": user_rank,
        "concluded_by_appt": user_appt,
        "closing_remark": closing_remark,
        f"{phase_path}.status": "Completed",
        f"{phase_path}.current_holder": None,
        f"{phase_path}.is_editable": False,
        f"{phase_path}.concluded_at": now,
        f"{phase_path}.concluded_by": user_email,
        f"{phase_path}.concluded_by_name": user_name,
        f"{phase_path}.concluded_by_appt": user_appt,
        f"{phase_path}.closing_remark": closing_remark,
    }

    update_push = {
        "remarks": closing_remark_entry,
        "assignment_history": closing_history_entry,
        f"{phase_path}.assignment_history": closing_history_entry,
    }

    documents_collection.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": update_set, "$push": update_push},
    )

    flash("Correspondence thread concluded and filed successfully.", "success")
    return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))
