from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    session,
    current_app,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient

document_acknowledgment_routes = Blueprint("document_acknowledgment_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


from modules.documents.functions_helper import check_user_signing_password

@document_acknowledgment_routes.route(
    "/acknowledge_document/<doc_id>", methods=["POST"]
)
def acknowledge_document(doc_id):
    try:
        signing_password = request.form.get("signing_password", "").strip()
        if not signing_password:
            flash("Signing password is required to acknowledge.", "error")
            return redirect(request.referrer)

        user_id = session.get("user_id")
        user = None
        if user_id:
            try:
                user = db.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                pass
        if not user:
            user_email = session.get("email") or session.get("user_email")
            if user_email:
                user = db.users.find_one({"email": user_email})

        if not user:
            flash("User not found.", "error")
            return redirect(request.referrer)

        if not check_user_signing_password(user, signing_password):
            flash("Incorrect signing password.", "error")
            return redirect(request.referrer)

        doc = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not doc:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        user_email = session.get("email")
        now = datetime.now()
        user_signature = user.get("signature_image", "")

        # Find all document IDs that need to be updated (parent document and all cloned copies)
        parent_id = doc.get("parent_doc_id")
        is_valid_parent = False
        if parent_id and isinstance(parent_id, str) and len(parent_id.strip()) == 24:
            try:
                ObjectId(parent_id)
                is_valid_parent = True
            except Exception:
                pass
        elif isinstance(parent_id, ObjectId):
            is_valid_parent = True

        doc_ids_to_update = [ObjectId(doc_id)]
        if is_valid_parent:
            doc_ids_to_update.append(ObjectId(parent_id))
            clones = db.documents.find({"parent_doc_id": str(parent_id)}, {"_id": 1})
            for c in clones:
                doc_ids_to_update.append(c["_id"])
        else:
            clones = db.documents.find(
                {
                    "$or": [
                        {"parent_doc_id": str(doc_id)},
                        {"parent_doc_id": ObjectId(doc_id)},
                    ]
                },
                {"_id": 1},
            )
            for c in clones:
                doc_ids_to_update.append(c["_id"])

        doc_ids_to_update = list(set(doc_ids_to_update))

        # 1. Update seen_stamp_history assignments at root level
        try:
            db.documents.update_many(
                {
                    "_id": {"$in": doc_ids_to_update},
                    "seen_stamp_history": {"$exists": True, "$type": "array"},
                },
                {
                    "$set": {
                        "seen_stamp_history.$[stamp].assignments.$[assign].acknowledged": True,
                        "seen_stamp_history.$[stamp].assignments.$[assign].signature": user_signature,
                        "seen_stamp_history.$[stamp].assignments.$[assign].acknowledged_at": now,
                    }
                },
                array_filters=[
                    {"stamp.assignments.email": user_email},
                    {"assign.email": user_email},
                ],
            )
        except Exception as e_filter:
            print(f"ROOT SEEN STAMP UPDATE NOTE: {e_filter}")

        # 2. Update seen_stamp_history assignments inside phases and fallback iterate
        for d_id in doc_ids_to_update:
            try:
                doc_obj = db.documents.find_one({"_id": d_id})
                if not doc_obj:
                    continue

                dirty = False
                # Check root seen_stamp_history
                root_stamps = doc_obj.get("seen_stamp_history", [])
                if isinstance(root_stamps, list):
                    for stamp in root_stamps:
                        for assign in stamp.get("assignments", []):
                            if assign.get("email") == user_email and not assign.get("acknowledged"):
                                assign["acknowledged"] = True
                                assign["signature"] = user_signature
                                assign["acknowledged_at"] = now
                                dirty = True

                # Check phases
                phases = doc_obj.get("phases", [])
                if isinstance(phases, list):
                    for phase in phases:
                        phase_stamps = phase.get("seen_stamp_history", [])
                        if isinstance(phase_stamps, list):
                            for stamp in phase_stamps:
                                for assign in stamp.get("assignments", []):
                                    if assign.get("email") == user_email and not assign.get("acknowledged"):
                                        assign["acknowledged"] = True
                                        assign["signature"] = user_signature
                                        assign["acknowledged_at"] = now
                                        dirty = True

                if dirty:
                    db.documents.update_one(
                        {"_id": d_id},
                        {
                            "$set": {
                                "seen_stamp_history": root_stamps,
                                "phases": phases,
                            }
                        },
                    )
            except Exception as e_doc:
                print(f"DOCUMENT ACK RECOVERY ERROR on doc {d_id}: {e_doc}")

        # Record a remark in all copies
        db.documents.update_many(
            {"_id": {"$in": doc_ids_to_update}},
            {
                "$push": {
                    "remarks": {
                        "user": session.get("name"),
                        "email": user_email,
                        "role": session.get("role"),
                        "rank": session.get("rank"),
                        "appt": session.get("appt"),
                        "remark": "Document acknowledged.",
                        "timestamp": now,
                        "action": "acknowledge",
                    }
                }
            },
        )

        flash("Document acknowledged successfully.", "success")
        return redirect(
            url_for("open_document_routes.open_document", doc_id=doc_id)
        )

    except Exception as e:
        print(f"ACKNOWLEDGE DOCUMENT ERROR: {e}")
        import traceback

        traceback.print_exc()
        flash("An error occurred while acknowledging.", "error")
        return redirect(request.referrer or url_for("base_document_routes.documents_content"))
