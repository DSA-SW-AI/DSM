from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    session,
    render_template,
    current_app,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient

from modules.documents.functions_helper import (
    get_phase_info_for_document,
)
import os

edit_document_routes = Blueprint("edit_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@edit_document_routes.route("/edit_document/<doc_id>")
def edit_document(doc_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    user_rank = session.get("rank")
    user_appt = session.get("appt")
    user_email = session.get("email")
    user_directorate = session.get("directorate")

    document = db.documents.find_one({"_id": ObjectId(doc_id)})
    if not document:
        flash("Document not found.")
        return redirect(url_for("base_document_routes.documents_content"))

    sequence = request.args.get("seq", 0, type=int)

    # ── PHASE SUPPORT: Get phase info ──
    phases = document.get("phases", [])
    current_phase_idx = document.get("current_phase", 0)

    if not phases:
        phases = [
            {
                "phase_number": 0,
                "directorate": document.get("origin_directorate", ""),
                "current_holder": document.get("current_holder"),
                "is_editable": document.get("is_editable", False),
                "content_html": document.get("content_html", ""),
                "review_chain": document.get("review_chain", []),
            }
        ]
        current_phase_idx = 0

    # Find the phase for the user's directorate
    user_phase = None
    for phase in reversed(phases):
        if phase.get("directorate") == user_directorate:
            user_phase = phase
            break

    if not user_phase:
        user_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )

    # ── Check if user can edit based on custody, signatures, and directorate ──
    can_edit = False
    edit_reason = ""

    user_dir_norm = (user_directorate or "").strip().upper()
    doc_origin_dir_norm = (document.get("origin_directorate") or "").strip().upper()
    is_super_admin = session.get("role") == "super_admin"

    doc_holder = document.get("current_holder")
    phase_holder = user_phase.get("current_holder") if user_phase else None
    is_current_custody = (user_email == doc_holder) or (user_email == phase_holder)

    if is_super_admin:
        can_edit = True
        edit_reason = "Super admin override"
    elif not is_current_custody:
        can_edit = False
        edit_reason = "You cannot edit this document because it is not currently in your custody."
    elif sequence > 0:
        # For replies, check correspondence entry
        target_corr = next((c for c in document.get("correspondence", []) if c.get("sequence") == sequence), None)
        if not target_corr:
            can_edit = False
            edit_reason = "Correspondence entry not found."
        elif target_corr.get("signatures") and len(target_corr.get("signatures", [])) > 0:
            can_edit = False
            edit_reason = "This correspondence has already been signed and cannot be edited."
        elif not (target_corr.get("is_editable") or target_corr.get("content_html") or target_corr.get("source") == "created_in_system"):
            can_edit = False
            edit_reason = "This correspondence is not editable in the editor."
        else:
            can_edit = True
            edit_reason = "You are the current holder of this editable correspondence."
    else:
        # For original document (sequence 0)
        root_sigs = document.get("signatures", [])
        if root_sigs and len(root_sigs) > 0:
            can_edit = False
            edit_reason = "This document has already been signed and cannot be edited."
        elif user_dir_norm != doc_origin_dir_norm:
            can_edit = False
            edit_reason = "Target directorate cannot edit the original incoming document."
        elif not (document.get("is_editable") or document.get("content_html") or document.get("source") == "created_in_system"):
            can_edit = False
            edit_reason = "This document is an uploaded file and cannot be edited in the editor."
        else:
            can_edit = True
            edit_reason = "You are the current holder in the originating directorate."

    if not can_edit:
        flash(edit_reason or "You do not have permission to edit this document.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence))

    # ── Get content ──
    content_html = ""
    editable_doc = document
    is_correspondence = False

    if sequence > 0:
        correspondence_list = document.get("correspondence", [])
        for corr in correspondence_list:
            if corr.get("sequence") == sequence:
                editable_doc = corr
                is_correspondence = True
                content_html = corr.get("content_html", "")
                break
    else:
        correspondence_list = document.get("correspondence", [])
        seq0 = next((c for c in correspondence_list if c.get("sequence") == 0), None)

        if seq0 and seq0.get("content_html"):
            content_html = seq0.get("content_html", "")
            editable_doc = seq0
        elif document.get("content_html"):
            content_html = document.get("content_html", "")
            editable_doc = document
        elif document.get("pdf_path", "").lower().endswith((".docx", ".doc")):
            try:
                import mammoth

                abs_path = os.path.join(current_app.root_path, document["pdf_path"])
                with open(abs_path, "rb") as docx_file:
                    result = mammoth.convert_to_html(docx_file)
                    content_html = result.value
            except Exception as e:
                print(f"Error converting document: {e}")
                content_html = f"<p>Error loading document content: {e}</p>"

    existing_folders = [f for f in db.documents.distinct("folder_name") if f]

    return render_template(
        "edit_document.html",
        document=document,
        editable_doc=editable_doc,
        content_html=content_html,
        sequence=sequence,
        is_correspondence=is_correspondence,
        user_rank=user_rank,
        user_appt=user_appt,
        # ── PHASE SUPPORT ──
        can_edit=can_edit,
        edit_reason=edit_reason,
        phase_number=user_phase.get("phase_number", 0),
        phase_directorate=user_phase.get("directorate"),
        is_phase_holder=user_phase.get("current_holder") == user_email,
        phase_info=get_phase_info_for_document(document, user_directorate),
        current_phase_idx=current_phase_idx,
        user_phase=user_phase,
        all_phases=phases,
        existing_folders=existing_folders,
    )
