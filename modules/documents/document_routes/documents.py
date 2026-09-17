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
    
    if user_role == "super_admin":
        # Global scope admins see everything
        base_query_raw = {}
    elif user_role in ["cdsa", "dcdsa"]:
        # CDSA / DCDSA see only documents forwarded to them where they are the current holder,
        # and documents they have worked on (not everything)
        user_emails = list(set(filter(None, [user_email, user_email.lower() if user_email else None, user_email.upper() if user_email else None])))
        user_name = session.get("name")
        cdsa_or_conditions = [
            # 1. Forwarded to him and he is the current holder
            {"current_holder": {"$in": user_emails}},
            {"phases.current_holder": {"$in": user_emails}},
            # 2. Documents he has worked on:
            # - Assigned / Forwarded by him
            {"assignment_history.assigned_by": {"$in": user_emails}},
            {"phases.assignment_history.assigned_by": {"$in": user_emails}},
            {"full_assignment_history.assigned_by": {"$in": user_emails}},
            {"forwarded_by_email": {"$in": user_emails}},
            {"target_forwarded_by_email": {"$in": user_emails}},
            {"dispatched_by_email": {"$in": user_emails}},
            # - Signed by him
            {"signed_by": {"$in": user_emails}},
            {"phases.signed_by": {"$in": user_emails}},
            {"signatures.signed_by": {"$in": user_emails}},
            {"signatures.email": {"$in": user_emails}},
            {"phases.signatures.signed_by": {"$in": user_emails}},
            {"phases.signatures.email": {"$in": user_emails}},
            # - Remarks / Minutes added by him
            {"remarks.email": {"$in": user_emails}},
            {"phases.remarks.email": {"$in": user_emails}},
            {"correspondence.remarks.email": {"$in": user_emails}},
            # - Stamped by him (seen stamp)
            {"seen_stamp_history.stamped_email": {"$in": user_emails}},
            {"phases.seen_stamp_history.stamped_email": {"$in": user_emails}},
            # - Concluded / Closed by him
            {"concluded_by": {"$in": user_emails}},
            {"phases.concluded_by": {"$in": user_emails}},
            # - Created / Authored by him
            {"sender_email": {"$in": user_emails}},
            {"created_by": {"$in": user_emails}},
            {"phases.started_by": {"$in": user_emails}},
            {"correspondence.author.email": {"$in": user_emails}},
            # - Review chain participation
            {"phases.review_chain.email": {"$in": user_emails}},
            {"review_chain.email": {"$in": user_emails}},
            {"full_review_chain.email": {"$in": user_emails}},
        ]
        if user_name:
            cdsa_or_conditions.append({"created_by": user_name})
        base_query_raw = {"$or": cdsa_or_conditions}
    elif user_role in ["registry", "central_registry"]:
        # Directorate scope registry sees all non-confidential documents in their directorate (inbound & outbound)
        base_query_raw = {
            "$or": [
                {
                    "origin_directorate": user_directorate,
                    "is_confidential": {"$ne": True},
                },
                {
                    "target_directorate": user_directorate,
                    "is_confidential": {"$ne": True},
                },
                {
                    "phases.directorate": user_directorate,
                    "is_confidential": {"$ne": True},
                },
                {"sender_email": user_email},
                {"assigned_to": user_email},
                {"current_holder": user_email},
            ]
        }
    elif user_role == "director":
        # Director sees:
        # 1. All documents originating in their directorate
        # 2. Incoming documents dispatched to their directorate that have been officially forwarded
        #    by the registry (target_forwarded_at exists)
        # 3. Documents where the director is directly involved (current holder, assigned, creator, review chain, assignment history, stamps)
        base_query_raw = {
            "$or": [
                {"origin_directorate": user_directorate},
                {
                    "target_directorate": user_directorate,
                    "target_forwarded_at": {"$ne": None, "$exists": True}
                },
                {
                    "phases.directorate": user_directorate,
                    "target_forwarded_at": {"$ne": None, "$exists": True}
                },
                {"sender_email": user_email},
                {"created_by": user_email},
                {"assigned_to": user_email},
                {"current_holder": user_email},
                {"cc": user_email},
                {"assignment_history.assigned_to": user_email},
                {"assignment_history.assigned_by": user_email},
                {"phases.current_holder": user_email},
                {"phases.review_chain.email": user_email},
                {"phases.assignment_history.assigned_to": user_email},
                {"phases.assignment_history.assigned_by": user_email},
                {"seen_stamp_history.stamped_email": user_email},
                {"seen_stamp_history.assignments.email": user_email},
                {"phases.seen_stamp_history.stamped_email": user_email},
                {"phases.seen_stamp_history.assignments.email": user_email}
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
