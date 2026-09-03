from flask import Blueprint, request, redirect, url_for, flash, session, render_template
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview
from modules.documents.functions_helper import (
    user_involvement_query_with_phases,
    combine_queries,
    document_search_query,
    get_phase_info_for_user,
    add_assignment_history_to_documents,
)

incoming_outgoing_routes = Blueprint("incoming_outgoing_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@incoming_outgoing_routes.route("/incoming_correspondences", methods=["GET"])
def incoming_correspondences():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    user_role = session.get("role")
    user_email = session.get("email")
    user_appt = session.get("appt")
    user_rank = session.get("rank")
    user_directorate = session.get("directorate")

    search_query = request.args.get("q", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    active_folder = request.args.get("folder", "").strip() or None

    # ── PHASE SUPPORT: Enhanced involvement query ──
    base_query = {"correspondence_type": "incoming"}

    if user_role != "super_admin":
        involvement_query = user_involvement_query_with_phases(
            user_email, user_directorate
        )
        base_query = {"$and": [base_query, involvement_query]}

    query = combine_queries(
        base_query,
        document_search_query(search_query),
        {"priority": priority_filter} if priority_filter else {},
    )

    documents = list(db.documents.find(query).sort("created_at", -1))

    for doc in documents:
        doc["_id"] = str(doc["_id"])
        # ── PHASE SUPPORT: Add phase info to documents for display ──
        doc["phase_info"] = get_phase_info_for_user(doc, user_directorate)

    documents = add_assignment_history_to_documents(documents)

    # Handle folder grouping
    folders = []
    if active_folder:
        documents = [
            doc
            for doc in documents
            if doc.get("sender") == active_folder
            or doc.get("origin_directorate") == active_folder
        ]
    else:
        folders_map = {}
        for doc in documents:
            f_name = (
                doc.get("sender") or doc.get("origin_directorate") or "Unknown Sender"
            )
            if f_name not in folders_map:
                folders_map[f_name] = {
                    "name": f_name,
                    "count": 0,
                    "latest_date": doc.get("created_at") or datetime(1970, 1, 1),
                }
            folders_map[f_name]["count"] += 1
            if (
                doc.get("created_at")
                and doc["created_at"] > folders_map[f_name]["latest_date"]
            ):
                folders_map[f_name]["latest_date"] = doc["created_at"]

        folders = list(folders_map.values())
        folders.sort(key=lambda x: x["latest_date"], reverse=True)
        documents = []

    return render_template(
        "incoming_correspondences.html",
        documents=documents,
        folders=folders,
        active_folder=active_folder,
        user_appt=user_appt,
        user_rank=user_rank,
        search_query=search_query,
        priority_filter=priority_filter,
        active_page="incoming_correspondences",
    )


@incoming_outgoing_routes.route("/outgoing_correspondences", methods=["GET"])
def outgoing_correspondences():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    user_role = session.get("role")
    user_email = session.get("email")
    user_appt = session.get("appt")
    user_rank = session.get("rank")
    user_directorate = session.get("directorate")

    search_query = request.args.get("q", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    active_folder = request.args.get("folder", "").strip() or None

    # ── PHASE SUPPORT: Enhanced involvement query ──
    base_query = {
        "$or": [
            {"correspondence_type": "outgoing"},
            {
                "correspondence_type": "incoming",
                "correspondence.forwarded_status": {
                    "$in": ["Forwarded for Dispatch", "Dispatched"]
                },
            },
        ]
    }

    if user_role != "super_admin":
        involvement_query = user_involvement_query_with_phases(
            user_email, user_directorate
        )
        base_query = {"$and": [base_query, involvement_query]}

    query = combine_queries(
        base_query,
        document_search_query(search_query),
        {"priority": priority_filter} if priority_filter else {},
    )

    documents = list(db.documents.find(query).sort("created_at", -1))

    for doc in documents:
        doc["_id"] = str(doc["_id"])
        # ── PHASE SUPPORT: Add phase info to documents for display ──
        doc["phase_info"] = get_phase_info_for_user(doc, user_directorate)

        # Find the sequence of the dispatched reply (if any)
        if doc.get("correspondence_type") == "incoming":
            dispatched_seq = 0
            for c in doc.get("correspondence", []):
                if c.get("forwarded_status") in [
                    "Forwarded for Dispatch",
                    "Dispatched",
                ]:
                    dispatched_seq = c.get("sequence", 0)
            doc["dispatched_seq"] = dispatched_seq

    documents = add_assignment_history_to_documents(documents)

    def get_outgoing_folder(doc):
        if doc.get("folder_name"):
            return doc.get("folder_name")
        if doc.get("correspondence_type") == "incoming":
            return (
                doc.get("sender") or doc.get("origin_directorate") or "General Outgoing"
            )
        return doc.get("target_directorate") or "General Outgoing"

    # Handle folder grouping
    folders = []
    if active_folder:
        documents = [
            doc for doc in documents if get_outgoing_folder(doc) == active_folder
        ]
    else:
        folders_map = {}
        for doc in documents:
            f_name = get_outgoing_folder(doc)
            if f_name not in folders_map:
                folders_map[f_name] = {
                    "name": f_name,
                    "count": 0,
                    "latest_date": doc.get("created_at") or datetime(1970, 1, 1),
                }
            folders_map[f_name]["count"] += 1
            if (
                doc.get("created_at")
                and doc["created_at"] > folders_map[f_name]["latest_date"]
            ):
                folders_map[f_name]["latest_date"] = doc["created_at"]

        folders = list(folders_map.values())
        folders.sort(key=lambda x: x["latest_date"], reverse=True)
        documents = []

    return render_template(
        "outgoing_correspondences.html",
        documents=documents,
        folders=folders,
        active_folder=active_folder,
        user_appt=user_appt,
        user_rank=user_rank,
        search_query=search_query,
        priority_filter=priority_filter,
        active_page="outgoing_correspondences",
    )
