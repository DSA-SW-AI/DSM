from flask import (
    Blueprint,
    request,
    session,
    jsonify,
    Response,
    redirect,
    url_for,
    flash,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from werkzeug.utils import secure_filename

check_document_routes = Blueprint("check_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@check_document_routes.route("/check_new_documents")
def check_new_documents():
    if "user_id" not in session:
        return jsonify([])

    user_email = session.get("email")
    if not user_email:
        return jsonify([])

    # Find documents assigned TO this user that haven't been viewed
    # AND exclude documents sent BY this user
    query = {
        "assigned_to": user_email,
        "sender_email": {"$ne": user_email},
        "viewed_by_target": False,
    }

    new_docs = list(db.documents.find(query))

    results = []
    for doc in new_docs:
        results.append(
            {
                "id": str(doc["_id"]),
                "reference_number": doc.get("reference_number"),
                "subject": doc.get("subject"),
                "sender": doc.get("sender") or doc.get("sender_email"),
            }
        )

    return jsonify(results)


@check_document_routes.route("/check_viewed_updates")
def check_viewed_updates():
    """
    Check if any documents SENT by this user have been VIEWED by the target.
    """
    if "user_id" not in session:
        return jsonify([])

    user_email = session.get("email")
    if not user_email:
        return jsonify([])

    # Find docs sent BY us, Viewed by target, but NOT yet notified to us
    query = {
        "sender_email": user_email,
        "viewed_by_target": True,
        "sender_notified": {"$ne": True},
    }

    updates = list(db.documents.find(query))
    results = []
    for doc in updates:
        results.append(
            {
                "id": str(doc["_id"]),
                "reference_number": doc.get("reference_number"),
                "target": doc.get("assigned_to"),
            }
        )

        # Mark as notified so we don't show popup again
        db.documents.update_one(
            {"_id": doc["_id"]}, {"$set": {"sender_notified": True}}
        )

    return jsonify(results)


@check_document_routes.route("/mark_document_viewed/<doc_id>", methods=["POST"])
def mark_document_viewed(doc_id):
    if "user_id" not in session:
        return jsonify({"status": "error"}), 401

    # Set viewed_by_target = True
    # sender_notified remains False (or missing) effectively
    db.documents.update_one(
        {"_id": ObjectId(doc_id)}, {"$set": {"viewed_by_target": True}}
    )
    return jsonify({"status": "success"})
