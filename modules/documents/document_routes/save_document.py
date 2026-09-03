from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    session,
    json,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
import bleach
from werkzeug.utils import secure_filename

save_document_routes = Blueprint("save_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@save_document_routes.route("/save_document/<doc_id>", methods=["POST"])
def save_document(doc_id):
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    document = db.documents.find_one({"_id": ObjectId(doc_id)})
    if not document:
        flash("Document not found.")
        return redirect(url_for("base_document_routes.documents_content"))

    content_html = request.form.get("content_html", "").strip()
    sequence = request.args.get("seq", 0, type=int)
    name = request.form.get("name", "").strip()
    rank = request.form.get("rank", "").strip()
    appt = request.form.get("appt", "").strip()
    folder_name = request.form.get("folder_name", "").strip() or None

    user_email = session.get("email")
    user_role = session.get("role")
    is_super_admin = user_role == "super_admin"

    current_phase_idx = document.get("current_phase", 0)
    phases = document.get("phases", [])
    phase_holder = phases[current_phase_idx].get("current_holder") if phases and current_phase_idx < len(phases) else None
    doc_holder = document.get("current_holder")

    is_authorized = is_super_admin or (user_email == doc_holder) or (user_email == phase_holder)
    if not is_authorized:
        flash("You do not have custody to edit and save this document.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence))

    if not content_html:
        flash("No content to save.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    allowed_tags = [
        "p",
        "br",
        "strong",
        "em",
        "u",
        "ol",
        "ul",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "span",
        "div",
        "a",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "img",
    ]
    allowed_attrs = {
        "*": ["style", "class", "id"],
        "a": ["href", "target"],
        "img": ["src", "alt", "width", "height"],
    }
    clean_html = bleach.clean(
        content_html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=["http", "https", "data"],
        strip=True,
    )

    now = datetime.now()
    version_entry = {
        "content": clean_html,
        "edited_by": session.get("name"),
        "timestamp": now,
    }

    # ── ATTACHMENTS (NEW IN EDIT MODE) ──
    saved_attachments = []
    raw_metadata = request.form.get("attachments_metadata", "[]")
    try:
        attachments_metadata = json.loads(raw_metadata)
    except Exception:
        attachments_metadata = []

    uploaded_files = request.files.getlist("attachments")
    allowed_extensions = (
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".png",
        ".jpg",
        ".jpeg",
    )
    created_by = session.get("name")

    for index, file in enumerate(uploaded_files):
        if not file or file.filename == "":
            continue
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(allowed_extensions):
            flash(f"Invalid attachment type: {filename}", "error")
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )
        metadata = (
            attachments_metadata[index] if index < len(attachments_metadata) else {}
        )
        file_id = fs.put(file, filename=filename, content_type=file.content_type)
        saved_attachments.append(
            {
                "type": metadata.get("type", "attachment"),
                "code": metadata.get("code", ""),
                "title": metadata.get("title", ""),
                "full_label": metadata.get("fullLabel", ""),
                "file_id": str(file_id),
                "filename": filename,
                "content_type": file.content_type,
                "uploaded_by": created_by,
                "uploaded_at": now,
            }
        )

    # ── PHASE SUPPORT: Get current phase ──
    current_phase_idx = document.get("current_phase", 0)
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

    # FIX: Use the sequence from URL, NOT document.get('active_correspondence')
    if sequence == 0:
        # Build push operators
        root_push = {
            "versions": version_entry,
            f"{phase_path}.versions": version_entry,
        }
        if saved_attachments:
            root_push["attachments"] = {"$each": saved_attachments}
            root_push[f"{phase_path}.attachments"] = {"$each": saved_attachments}

        # Update root document
        db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "content_html": clean_html,
                    "folder_name": folder_name,
                    "is_editable": True,
                    "status": "Edited",
                    "edited_at": now,
                    "edited_by": session.get("name"),
                    "name": name,
                    "rank": rank,
                    "appt": appt,
                    # ── PHASE SUPPORT: Update phase too ──
                    f"{phase_path}.content_html": clean_html,
                    f"{phase_path}.is_editable": True,
                    f"{phase_path}.status": "Edited",
                    f"{phase_path}.edited_at": now,
                    f"{phase_path}.edited_by": session.get("name"),
                },
                "$push": root_push,
            },
        )

        # Build correspondence push operators
        corr_push = {
            "correspondence.$.versions": version_entry,
        }
        if saved_attachments:
            corr_push["correspondence.$.attachments"] = {"$each": saved_attachments}

        # Also update correspondence[0] to keep them in sync
        db.documents.update_one(
            {"_id": ObjectId(doc_id), "correspondence.sequence": 0},
            {
                "$set": {
                    "correspondence.$.content_html": clean_html,
                    "correspondence.$.is_editable": True,
                    "correspondence.$.status": "Edited",
                    "correspondence.$.edited_at": now,
                    "correspondence.$.edited_by": session.get("name"),
                    "correspondence.$.name": name,
                    "correspondence.$.rank": rank,
                    "correspondence.$.appt": appt,
                },
                "$push": corr_push,
            },
        )
    else:
        # Saving a correspondence entry (reply)
        reply_push = {
            "correspondence.$.versions": version_entry,
            f"{phase_path}.versions": version_entry,
        }
        if saved_attachments:
            reply_push["correspondence.$.attachments"] = {"$each": saved_attachments}
            reply_push[f"{phase_path}.attachments"] = {"$each": saved_attachments}

        # Saving a correspondence entry (reply)
        db.documents.update_one(
            {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
            {
                "$set": {
                    "correspondence.$.content_html": clean_html,
                    "correspondence.$.is_editable": True,
                    "correspondence.$.status": "Edited",
                    "correspondence.$.edited_at": now,
                    "correspondence.$.edited_by": session.get("name"),
                    "correspondence.$.name": name,
                    "correspondence.$.rank": rank,
                    "correspondence.$.appt": appt,
                    # ── PHASE SUPPORT: Update phase too ──
                    f"{phase_path}.content_html": clean_html,
                    f"{phase_path}.is_editable": True,
                    f"{phase_path}.status": "Edited",
                    f"{phase_path}.edited_at": now,
                    f"{phase_path}.edited_by": session.get("name"),
                },
                "$push": reply_push,
            },
        )

    flash("Document saved successfully.", "success")
    return redirect(
        url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
    )
