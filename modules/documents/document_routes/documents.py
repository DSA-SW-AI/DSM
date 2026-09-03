from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask import session
from pymongo import MongoClient
from modules.documents.functions_helper import (
    document_search_query_with_phases,
    ensure_assignment_history,
    combine_queries,
    user_involvement_query_with_phases,
    get_phase_info_for_document,
)

base_document_routes = Blueprint("base_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]


@base_document_routes.route("/documents_content", methods=["GET", "POST"])
def documents_content():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    user_role = session.get("role")
    user_email = session.get("email") or session.get("user_email")
    user_directorate = (session.get("directorate") or "").strip().upper()
    documents_collection = db["documents"]

    search_query = request.args.get("q", "").strip()
    priority_filter = request.args.get("priority", "").strip()

    # ── BUILD ACCESS CONTROL QUERY ──
    # Only includes documents user is directly involved with
    
    if user_role in ["super_admin", "cdsa", "dcdsa"]:
        # Global scope admins see everything
        base_query_raw = {}
    elif user_role in ["registry", "central_registry", "director"]:
        # Directorate scope admins see all documents in their directorate
        base_query_raw = {
            "$or": [
                {"origin_directorate": user_directorate},
                {"target_directorate": user_directorate},
                {"phases.directorate": user_directorate},
                {"sender_email": user_email},
                {"assigned_to": user_email},
                {"current_holder": user_email}
            ]
        }
    else:
        # Regular officers/users see only documents they are directly involved with
        base_query_raw = {
            "$or": [
                {"sender_email": user_email},
                {"assigned_to": user_email},
                {"current_holder": user_email},
                {"cc": user_email},
                {"assignment_history.assigned_to": user_email},
                {"assignment_history.assigned_by": user_email},
                {"phases.current_holder": user_email},
                {"phases.review_chain.email": user_email},
                {"phases.assignment_history.assigned_to": user_email},
                {"phases.assignment_history.assigned_by": user_email}
            ]
        }

    # Apply exclusions
    user_dir_upper = (user_directorate or "").strip().upper()
    clone_exclusion = {
        "$nor": [
            {
                "parent_doc_id": {"$ne": None, "$exists": True},
                "target_directorate": {"$ne": user_dir_upper},
            }
        ]
    }
    
    # Draft Filter: Drafts (Saved status) are only visible to their creator/sender
    draft_filter = {
        "$or": [
            {"status": {"$ne": "Saved"}},
            {"sender_email": user_email},
            {"created_by": user_email},
            {"created_by": session.get("name")}
        ]
    }
    
    from modules.documents.functions_helper import get_lifecycle_exclusions
    exclusions = get_lifecycle_exclusions(user_email, user_directorate)
    
    query_parts = [base_query_raw, clone_exclusion, draft_filter]
    if exclusions:
        query_parts.append(exclusions)
        
    base_query = {"$and": query_parts}

    # ── APPLY SEARCH FILTER ──
    # search_filter = document_search_query_with_phases(search_query) if search_query else {}

    search_query_filter = {}
    if search_query:
        search_query_filter = document_search_query_with_phases(search_query)

    # ── BUILD PRIORITY QUERY ──
    priority_query_filter = {}
    if priority_filter:
        priority_query_filter = {"priority": priority_filter}

    # ── COMBINE WITH AND LOGIC ──
    # Only show documents that match access control AND (search if provided) AND (priority if provided)
    query_parts = [base_query]

    if search_query_filter:
        query_parts.append(search_query_filter)
    
    if priority_query_filter:
        query_parts.append(priority_query_filter)


    

    # Use $and to ensure all conditions are met
    if len(query_parts) > 1:
        final_query = {"$and": query_parts}
    else:
        final_query = query_parts[0]

    # Write debug details to file for real-time tracking
    try:
        with open("debug_query.log", "w") as df:
            df.write(f"Logged User: {user_email}\n")
            df.write(f"Role: {user_role}\n")
            df.write(f"Directorate: {user_directorate}\n")
            df.write(f"Final Query: {final_query}\n")
    except Exception as ex:
        pass

    # print(f"[DEBUG] Final Query: {final_query}")  

    documents = list(
        documents_collection.find(final_query)
        .sort("created_at", -1)
        .limit(100))

    # ── PHASE SUPPORT: Format documents ──
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        doc["assignment_history"] = ensure_assignment_history(doc)
        doc["phase_info"] = get_phase_info_for_document(doc, user_directorate)

        # Current assignment info
        latest_assignment = None
        if doc.get("assignment_history"):
            latest_assignment = doc["assignment_history"][-1]

        doc["assigned_by"] = (
            latest_assignment.get("assigned_by_name", "N/A")
            if latest_assignment
            else "N/A"
        )
        doc["assigned_to_name"] = (
            latest_assignment.get("assigned_to_name", "N/A")
            if latest_assignment
            else "N/A"
        )
        doc["date_assigned_display"] = (
            latest_assignment.get("date_assigned", "") if latest_assignment else ""
        )

    # ── BUILD USER DICT FOR TEMPLATE ──
    user_data = {
        "name": session.get("name"),
        "role": session.get("role"),
        "directorate": session.get("directorate"),
        "email": session.get("email"),
    }

    return render_template(
        "documents.html",
        documents=documents,
        search_query=search_query,
        priority_filter=priority_filter,
        user=user_data,
        active_page="documents_content",
    )
