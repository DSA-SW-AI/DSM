from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient
from modules.documents.functions_helper import (
    add_phase_info_to_documents,
    document_search_query,
    combine_queries,
    get_lifecycle_exclusions,
    user_involvement_query_with_phases,
    get_phase_info_for_document,
    assignment_history_entry,
)

assigned_document_routes = Blueprint("assigned_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]


@assigned_document_routes.route("/assign_document", methods=["GET", "POST"])
def assign_document():
    if "user_id" not in session:
        return redirect(url_for("assigned_document_routes.login"))

    user_role = session.get("role")
    user_email = session.get("email")
    user_directorate = session.get("directorate")

    # =========================
    # GET
    # =========================
    if request.method == "GET":
        # SUPER ADMIN
        if user_role == "super_admin":
            documents = list(db.documents.find({}))
        else:
            # ── PHASE SUPPORT: Use enhanced involvement query ──
            documents = list(
                db.documents.find(
                    user_involvement_query_with_phases(user_email, user_directorate)
                )
            )

        # Add phase info
        for doc in documents:
            doc["_id"] = str(doc["_id"])
            doc["phase_info"] = get_phase_info_for_document(doc, user_directorate)

        # ONLY USERS INSIDE SAME DIRECTORATE
        all_users = list(
            db.users.find(
                {"directorate": user_directorate, "email": {"$ne": user_email}},
                {"name": 1, "appt": 1, "email": 1, "role": 1, "_id": 0},
            )
        )

        return render_template(
            "assigned_documents.html",
            documents=documents,
            all_users=all_users,
            active_page="assigned_documents",
        )

    # =========================
    # POST
    # =========================
    doc_id = request.form.get("doc_id")
    assigned_to = request.form.get("assigned_to")
    assigned_at = datetime.now()

    target_user = db.users.find_one({"email": assigned_to})
    if not target_user:
        flash("User not found", "danger")
        return redirect(request.referrer)

    recipient_dir = (target_user.get("directorate") or "").strip().upper()
    user_dir_norm = (user_directorate or "").strip().upper()
    if (
        user_role != "super_admin"
        and user_dir_norm
        and recipient_dir
        and recipient_dir != user_dir_norm
    ):
        flash(
            "Cannot assign document to a user in another directorate directly.",
            "danger",
        )
        return redirect(request.referrer)

    doc = db.documents.find_one({"_id": ObjectId(doc_id)})

    # ── PHASE SUPPORT: Get current phase ──
    current_phase_idx = doc.get("current_phase", 0)
    phase_path = f"phases.{current_phase_idx}"

    assignment_entry = assignment_history_entry(
        assigned_to, target_user, "", assigned_at
    )

    # ── PHASE SUPPORT: Add phase number to assignment ──
    assignment_entry["phase_number"] = current_phase_idx

    # ── PHASE SUPPORT: Update both phase and root ──
    db.documents.update_one(
        {"_id": ObjectId(doc_id)},
        {
            "$set": {
                "status": "assigned",
                "assigned_to": assigned_to,
                "assigned_by": user_email,
                "forwarded_by_directorate": user_directorate,
                "date_assigned": assigned_at,
                # ── PHASE SUPPORT ──
                f"{phase_path}.current_holder": assigned_to,
                f"{phase_path}.status": "assigned",
            },
            "$push": {
                "assignment_history": assignment_entry,
                # ── PHASE SUPPORT ──
                f"{phase_path}.assignment_history": assignment_entry,
                "full_assignment_history": assignment_entry,
            },
        },
    )

    flash("Document assigned successfully", "success")
    return redirect(request.referrer or url_for("assigned_document_routes.director_screen"))


@assigned_document_routes.route("/assigned_documents", methods=["GET"])
def assigned_documents():
    if "user_id" not in session:
        return redirect(url_for("assigned_document_routes.login"))

    user_role = session.get("role")
    user_email = session.get("email")
    user_directorate = session.get("directorate")
    user_appt = session.get("appt")
    user_rank = session.get("rank")

    search_query = request.args.get("q", "").strip()
    priority_filter = request.args.get("priority", "").strip()

    if user_role == "super_admin":
        base_query = {"status": {"$ne": "Saved"}}
    elif user_role in ["registry", "central_registry"]:
        registry_involvement = {
            "status": {"$ne": "Saved"},
            "$or": [
                {"origin_directorate": user_directorate},
                {"target_directorate": user_directorate},
                {"phases.directorate": user_directorate},
            ],
        }
        exclusions = get_lifecycle_exclusions(user_email, user_directorate)
        base_query = combine_queries(registry_involvement, exclusions)
    else:
        # ── PHASE SUPPORT: Use enhanced involvement query ──
        base_query = combine_queries(
            user_involvement_query_with_phases(user_email, user_directorate),
            {"status": {"$ne": "Saved"}},
        )

    query = combine_queries(
        base_query,
        document_search_query(search_query),
        {"priority": priority_filter} if priority_filter else {},
    )

    documents = list(db.documents.find(query).sort("created_at", -1))

    # ── PHASE SUPPORT: Add phase info ──
    documents = add_phase_info_to_documents(documents, user_directorate)

    return render_template(
        "assigned_documents.html",
        user_appt=user_appt,
        user_rank=user_rank,
        documents=documents,
        search_query=search_query,
        priority_filter=priority_filter,
        active_page="assigned_documents",
    )
