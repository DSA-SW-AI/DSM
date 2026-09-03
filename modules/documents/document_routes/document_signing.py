from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    current_app,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from utils.constants import DocStage, CorrespondenceReview
from modules.documents.functions_helper import check_user_signing_password

document_signing_routes = Blueprint("document_signing_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@document_signing_routes.route("/verify_signing_password", methods=["POST"])
def verify_signing_password():
    """Verify user's signing password via AJAX"""
    try:
        data = request.get_json()
        password = data.get("password", "")
        user_email = session.get("email") or session.get("user_email")

        user = db.users.find_one({"email": user_email})
        if not user:
            return jsonify({"verified": False, "error": "User not found"})

        if check_user_signing_password(user, password):
            return jsonify({"verified": True})
        else:
            return jsonify({"verified": False, "error": "Invalid signing password"})

    except Exception as e:
        print(f"Password verification error: {str(e)}")
        return jsonify({"verified": False, "error": "Verification failed"})


@document_signing_routes.route("/append_final_signature/<doc_id>", methods=["POST"])
def append_final_signature(doc_id):
    try:
        document = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not document:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        # Get the sequence from the form
        sequence = request.form.get("sequence", 0)
        try:
            sequence = int(sequence)
        except ValueError:
            sequence = 0

        # Get current user from database
        user_email = session.get("email") or session.get("user_email")
        user = db.users.find_one({"email": user_email})

        # Get form data from modal
        signing_password = request.form.get("signing_password", "")
        name = request.form.get("name", "").strip() or (user.get("name") if user else "") or session.get("name", "")
        appt = request.form.get("appt", "").strip() or (user.get("appt") if user else "") or session.get("appt", "")
        rank = request.form.get("rank", "").strip()
        if not rank or rank == "None":
            rank = (
                (user.get("rank") if user else "")
                or (user.get("rankOrGrade") if user else "")
                or (user.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade") if user else "")
                or (user.get("onboarding_data", {}).get("step_1", {}).get("rank") if user else "")
                or session.get("rank")
                or session.get("rankOrGrade")
                or ""
            )
        if rank == "None":
            rank = ""

        if not user:
            flash("User not found.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        if not check_user_signing_password(user, signing_password):
            flash("Invalid signing password.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        signature_image = user.get("signature_image")
        if not signature_image:
            flash(
                "No signature found in your profile. Please upload a signature first.",
                "error",
            )
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        now = datetime.now()

        # Create signature entry
        signature_entry = {
            "signer": name,
            "signer_email": user_email,
            "signer_role": session.get("role"),
            "signer_rank": rank,
            "signer_appt": appt,
            "signature_image": signature_image,
            "signature_method": "stored",
            "timestamp": now,
        }

        remark_entry = {
            "user": name,
            "email": user_email,
            "role": session.get("role"),
            "rank": rank,
            "appt": appt,
            "remark": f"Document signed by ({appt})",
            "timestamp": now,
        }

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

        # ⭐ Update based on whether it's the original (sequence 0) or a reply
        if sequence == 0:
            # Update root document
            db.documents.update_one(
                {"_id": ObjectId(doc_id)},
                {
                    "$set": {
                        f"{phase_path}.status": "Treated",
                        f"{phase_path}.signed_at": now,
                        f"{phase_path}.signed_by": user_email,
                        f"{phase_path}.signed_by_name": name,
                        f"{phase_path}.is_editable": False,
                        f"{phase_path}.current_holder": None,
                        f"{phase_path}.forwarded_status": "Completed",
                        "status": "Treated",
                        "signed_at": now,
                        "signed_by": user_email,
                        "signed_by_name": name,
                        "is_editable": False,
                        "current_holder": None,
                        "forwarded_status": "Completed",
                        "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_TARGET,
                    },
                    "$push": {
                        f"{phase_path}.signatures": signature_entry,
                        f"{phase_path}.remarks": remark_entry,
                        "signatures": signature_entry,
                        "remarks": remark_entry,
                        "full_assignment_history": {
                            "action_type": "signature",
                            "signed_by": user_email,
                            "signed_by_name": name,
                            "timestamp": now,
                        },
                    },
                },
            )
        else:
            # Update the specific correspondence entry
            db.documents.update_one(
                {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
                {
                    "$set": {
                        "correspondence.$.status": "Signed",
                        "correspondence.$.is_editable": False,
                        "correspondence.$.signed_at": now,
                        "correspondence.$.signed_by": user_email,
                        "correspondence.$.signed_by_name": name,
                        "correspondence.$.review_status": CorrespondenceReview.SIGNED,
                        f"{phase_path}.status": "Signed",
                        f"{phase_path}.signed_at": now,
                        f"{phase_path}.signed_by": user_email,
                        f"{phase_path}.signed_by_name": name,
                        "doc_lifecycle_stage": DocStage.AWAITING_DISPATCH_TO_ORIGIN,
                    },
                    "$push": {
                        f"correspondence.$.signatures": signature_entry,
                        f"correspondence.$.remarks": remark_entry,
                        f"{phase_path}.signatures": signature_entry,
                        f"{phase_path}.remarks": remark_entry,
                        "remarks": remark_entry,
                        "full_assignment_history": {
                            "action_type": "signature",
                            "signed_by": user_email,
                            "signed_by_name": name,
                            "timestamp": now,
                            "sequence": sequence,
                        },
                    },
                },
            )

        flash(f"Document signed successfully by {name}.", "success")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    except Exception as e:
        print(f"FINAL SIGNATURE ERROR: {str(e)}")
        flash(f"An error occurred: {str(e)}", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))


@document_signing_routes.route("/verify_and_get_signature", methods=["POST"])
def verify_and_get_signature():
    if "user_id" not in session and "email" not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

        password = data.get("password", "").strip()
        if not password:
            return jsonify({"status": "error", "message": "Password is required"}), 400

        user = None
        user_id = session.get("user_id")
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
            return jsonify({"status": "error", "message": "User not found"}), 404

        stored_hash = user.get("signing_password_hash") or user.get("password_hash")
        if not stored_hash:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No signing password set. Please set one in your profile.",
                    }
                ),
                400,
            )

        if not check_user_signing_password(user, password):
            return (
                jsonify({"status": "error", "message": "Incorrect signing password"}),
                403,
            )

        signature_image = user.get("signature_image", "")
        if not signature_image:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No signature found. Please upload a signature in your profile.",
                    }
                ),
                404,
            )

        return jsonify(
            {
                "status": "success",
                "signature": signature_image,
                "name": user.get("name", ""),
                "appt": user.get("appt", ""),
                "rank": user.get("rank", ""),
            }
        )
    except Exception as e:
        print(f"Error in verify_and_get_signature: {e}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500


@document_signing_routes.route("/profile", methods=["GET"])
def profile():
    if "user_id" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))
    return redirect(url_for("setting_routes.get_settings"))


@document_signing_routes.route("/set_signing_password", methods=["POST"])
def set_signing_password():

    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    from flask_bcrypt import Bcrypt

    bcrypt = Bcrypt(current_app)

    current_password = request.form.get("current_password", "").strip()

    new_signing_password = request.form.get("new_signing_password", "").strip()

    confirm_signing_password = request.form.get("confirm_signing_password", "").strip()

    user = db.users.find_one({"_id": ObjectId(session["user_id"])})

    if not user:
        flash("User not found.", "error")
        return redirect(url_for("setting_routes.get_settings"))

    # VERIFY LOGIN PASSWORD
    if not bcrypt.check_password_hash(user.get("password", ""), current_password):

        flash("Incorrect login password.", "error")

        return redirect(url_for("setting_routes.get_settings"))

    # CHECK MATCH
    if new_signing_password != confirm_signing_password:

        flash("Signature passwords do not match.", "error")

        return redirect(url_for("setting_routes.get_settings"))

    # MIN LENGTH
    if len(new_signing_password) < 4:

        flash("Signature password must be at least 4 characters.", "error")

        return redirect(url_for("setting_routes.get_settings"))

    # HASH SIGNATURE PASSWORD
    hashed_signature_password = bcrypt.generate_password_hash(
        new_signing_password
    ).decode("utf-8")

    # SAVE TO USERS COLLECTION
    db.users.update_one(
        {"_id": ObjectId(session["user_id"])},
        {"$set": {"signing_password_hash": hashed_signature_password}},
    )

    flash("Signature password saved successfully.", "success")

    return redirect(url_for("setting_routes.get_settings"))


@document_signing_routes.route("/save_signature", methods=["POST"])
def save_signature():

    if "user_id" not in session:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    from flask_bcrypt import Bcrypt

    bcrypt = Bcrypt(current_app)

    # ── Parse JSON body ──
    data = request.get_json(force=True, silent=True)

    if not data:
        return (
            jsonify({"status": "error", "message": "Invalid or missing JSON body"}),
            400,
        )

    signature_data = data.get("signature_data", "").strip()
    password = data.get("password", "").strip()

    if not password:
        return jsonify({"status": "error", "message": "Password is required"}), 400

    if not signature_data or "base64" not in signature_data:
        return (
            jsonify({"status": "error", "message": "Empty or invalid signature"}),
            400,
        )

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
        return jsonify({"status": "error", "message": "User not found"}), 404

    if not check_user_signing_password(user, password):
        return (
            jsonify({"status": "error", "message": "Incorrect signing password"}),
            403,
        )

    db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"signature_image": signature_data}},
    )

    return jsonify({"status": "success"})


@document_signing_routes.route("/get_my_signature")
def get_my_signature():
    if "user_id" not in session:
        return jsonify({"signature": None})
    user = db.users.find_one(
        {"_id": ObjectId(session["user_id"])}, {"saved_signature": 1}
    )
    return jsonify({"signature": user.get("saved_signature") if user else None})
