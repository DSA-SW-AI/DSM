from flask import (
    Blueprint,
    request,
    Response,
    redirect,
    url_for,
    flash,
    session,
    json,
    abort,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview
from modules.documents.functions_helper import (
    get_directorate_from_email,
    emit_document_notification,
)

view_stream_correspondence_routes = Blueprint(
    "view_stream_correspondence_routes", __name__
)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


def _can_user_access_entry(document, entry, user_email, user_role, user_directorate):
    """Robust access check for correspondence entries across phases and directorates."""
    if user_role == "super_admin":
        return True

    if not entry:
        return True

    # Author / Creator always has access
    author_email = entry.get("author", {}).get("email") or entry.get("created_by_email")
    if author_email and user_email and author_email.strip().lower() == user_email.strip().lower():
        return True
    if entry.get("created_by") and session.get("name") and entry.get("created_by").strip().lower() == session.get("name").strip().lower():
        return True

    # Current holder of document or any phase always has access
    if document.get("current_holder") and user_email and document.get("current_holder").strip().lower() == user_email.strip().lower():
        return True
    for p in document.get("phases", []):
        if p.get("current_holder") and user_email and p.get("current_holder").strip().lower() == user_email.strip().lower():
            return True

    # Check visible/dispatched status
    visibility = entry.get("visibility_status", CorrespondenceVisibility.VISIBLE)
    if visibility in [CorrespondenceVisibility.VISIBLE, CorrespondenceVisibility.DISPATCHED, "visible", "dispatched"]:
        return True

    # Check direct involvement in assignments, remarks, or review chains
    for assign in entry.get("assignments", []):
        if assign.get("assigned_to") == user_email or assign.get("assigned_by") == user_email:
            return True
    for remark in entry.get("remarks", []):
        if remark.get("email") == user_email:
            return True
    for p in document.get("phases", []):
        for m in p.get("review_chain", []):
            if m.get("email") == user_email:
                return True
        for a in p.get("assignment_history", []):
            if a.get("assigned_to") == user_email or a.get("assigned_by") == user_email:
                return True

    # Directorate checks for internal drafts
    author_user = db.users.find_one({"email": author_email}) if author_email else None
    author_dir = (author_user.get("directorate") if author_user else "") or get_directorate_from_email(author_email)
    author_dir = (author_dir or "").strip().upper()

    if user_directorate and author_dir and user_directorate == author_dir:
        return True

    doc_origin_dir = (document.get("origin_directorate") or "").strip().upper()
    doc_target_dir = (document.get("target_directorate") or "").strip().upper()
    if user_directorate and (user_directorate == doc_origin_dir or user_directorate == doc_target_dir):
        return True

    return False


@view_stream_correspondence_routes.route(
    "/view_correspondence_entry/<doc_id>/<int:sequence>"
)
def view_correspondence_entry(doc_id, sequence):
    """
    Switches the active_correspondence pointer so open_document
    displays the selected entry in the main preview panel.
    """
    try:
        document = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not document:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        user_directorate = (session.get("directorate") or "").strip().upper()
        user_email = session.get("email")
        user_role = session.get("role")

        # Find the correspondence entry
        entry = None
        for c in document.get("correspondence", []):
            if c.get("sequence") == sequence:
                entry = c
                break

        # Sequence 0 is the root document, always accessible to authorized document viewers
        if sequence != 0 and not entry:
            flash("Correspondence entry not found.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        has_access = _can_user_access_entry(document, entry, user_email, user_role, user_directorate)

        if not has_access:
            flash("You don't have access to this correspondence entry.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        # Update active correspondence
        db.documents.update_one(
            {"_id": ObjectId(doc_id)}, {"$set": {"active_correspondence": sequence}}
        )

    except Exception as e:
        print(f"VIEW CORRESPONDENCE ENTRY ERROR: {str(e)}")
        flash("An error occurred.", "error")

    return redirect(
        url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
    )


@view_stream_correspondence_routes.route(
    "/stream_correspondence_file/<doc_id>/<int:sequence>"
)
def stream_correspondence_file(doc_id, sequence):
    """Stream the uploaded file from a specific correspondence entry."""
    try:
        document = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not document:
            abort(404)

        user_email = session.get("email")
        user_directorate = (session.get("directorate") or "").strip().upper()
        user_role = session.get("role")

        # Find the correspondence entry
        entry = None
        for c in document.get("correspondence", []):
            if c.get("sequence") == sequence:
                entry = c
                break

        if not entry or not entry.get("main_file_id"):
            abort(404)

        has_access = _can_user_access_entry(document, entry, user_email, user_role, user_directorate)

        if not has_access:
            abort(403)  # Forbidden

        file_id = entry["main_file_id"]
        grid_file = fs.get(ObjectId(file_id))
        filename = entry.get("main_filename", "document")
        mimetype = grid_file.content_type or "application/octet-stream"

        return Response(
            grid_file.read(),
            mimetype=mimetype,
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )

    except Exception as e:
        print(f"STREAM CORRESPONDENCE FILE ERROR: {str(e)}")
        abort(404)
