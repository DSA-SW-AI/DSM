from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    session,
)
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview
from modules.documents.functions_helper import (
    resolve_delegate,
    emit_document_notification,
)

forward_returned_routes = Blueprint("forward_returned_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@forward_returned_routes.route("/forward_correspondence/<doc_id>", methods=["POST"])
def forward_correspondence(doc_id):
    try:
        doc = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not doc:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        current_email = session.get("email")
        sequence = request.args.get("seq", type=int)
        remark_text = request.form.get("forward_remark", "").strip()
        now = datetime.now()

        if doc.get("current_holder") != current_email:
            flash("You are not the current holder of this document.", "error")
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        entry = next(
            (c for c in doc.get("correspondence", []) if c.get("sequence") == sequence),
            None,
        )
        if not entry:
            flash("Correspondence entry not found.", "error")
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        if entry.get("forwarded_status") == "Completed":
            flash("This correspondence is already completed.", "warning")
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        # ── PHASE SUPPORT: Get chain from current phase ──
        current_phase_idx = doc.get("current_phase", 0)
        phases = doc.get("phases", [])

        if not phases:
            phases = [
                {
                    "phase_number": 0,
                    "directorate": doc.get("origin_directorate", ""),
                    "review_chain": doc.get("review_chain", []),
                    "current_holder": doc.get("current_holder"),
                }
            ]
            current_phase_idx = 0

        current_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )
        phase_path = f"phases.{current_phase_idx}"

        # Use phase chain, fallback to root
        chain = current_phase.get("review_chain", [])
        if not chain:
            chain = doc.get("review_chain", [])

        my_index = next(
            (i for i, m in enumerate(chain) if m.get("email") == current_email), -1
        )

        if my_index == -1:
            flash(
                "You are not part of the review chain for this correspondence.", "error"
            )
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        # ── TOP OF CHAIN: complete the review ──
        if my_index == 0:
            db.documents.update_one(
                {"_id": ObjectId(doc_id)},
                {
                    "$set": {
                        f"{phase_path}.forwarded_status": "Completed",
                        f"{phase_path}.status": "Completed",
                        f"{phase_path}.completed_at": now,
                        f"{phase_path}.completed_by": current_email,
                        "current_holder": None,
                        "status": "Completed",
                        "forwarded_status": "Completed",
                        "assigned_to": None,
                        "completed_at": now,
                        "completed_by": current_email,
                        "doc_lifecycle_stage": (
                            DocStage.AWAITING_DISPATCH_TO_ORIGIN
                            if current_phase_idx > 0
                            else DocStage.AWAITING_DISPATCH_TO_TARGET
                        ),
                    }
                },
            )

            # Also update correspondence entry
            db.documents.update_one(
                {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
                {
                    "$set": {
                        "correspondence.$.forwarded_status": "Completed",
                        "correspondence.$.status": "Completed",
                        "correspondence.$.completed_at": now,
                        "correspondence.$.completed_by": current_email,
                        "correspondence.$.completed_remark": remark_text,
                        "correspondence.$.review_status": CorrespondenceReview.COMPLETED,
                    }
                },
            )

            if remark_text:
                db.documents.update_one(
                    {"_id": ObjectId(doc_id)},
                    {
                        "$push": {
                            "remarks": {
                                "user": session.get("name"),
                                "email": current_email,
                                "role": session.get("role"),
                                "rank": session.get("rank"),
                                "appt": session.get("appt"),
                                "remark": remark_text,
                                "timestamp": now,
                                "action": "complete_review",
                            }
                        }
                    },
                )
            flash("Review completed successfully!", "success")
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        # ── NOT TOP: forward to the person one step UP ──
        target = chain[my_index - 1]
        recipient_email = target["email"]
        recipient_name = target.get("name") or recipient_email
        recipient_rank = target.get("rank", "")
        recipient_appt = target.get("appt", "")
        recipient_role = target.get("role", "")

        if not recipient_appt:
            u = db.users.find_one({"email": recipient_email})
            if u and u.get("appt"):
                recipient_appt = u.get("appt")
            elif u and u.get("role"):
                recipient_appt = u.get("role").replace("_", " ").title()
            elif recipient_role:
                recipient_appt = recipient_role.replace("_", " ").title()
            else:
                recipient_appt = recipient_name

        # Resolve delegation for recipient_email
        resolved_recipient, res_name, res_role, delegated, delegators = (
            resolve_delegate(recipient_email)
        )
        if delegated:
            orig_recipient = recipient_email
            orig_recipient_name = recipient_name
            recipient_email = resolved_recipient
            recipient_name = res_name or recipient_email
            recipient_appt = res_role or ""
            remark_text = f"{remark_text} (Auto-delegated from {orig_recipient_name} who is Out of Office)"

        assignment_entry = {
            "assigned_to": recipient_email,
            "assigned_to_name": recipient_name,
            "assigned_to_rank": recipient_rank,
            "assigned_to_appt": recipient_appt,
            "assigned_by": current_email,
            "assigned_by_name": session.get("name"),
            "assigned_by_role": session.get("role"),
            "assigned_by_rank": session.get("rank"),
            "assigned_by_appt": session.get("appt"),
            "remark": remark_text,
            "timestamp": now,
            "action_type": "forward_for_review",
            "chain_from_index": my_index,
            "chain_to_index": my_index - 1,
        }
        remark_entry = {
            "user": session.get("name"),
            "email": current_email,
            "role": session.get("role"),
            "rank": session.get("rank"),
            "appt": session.get("appt"),
            "remark": remark_text or f"Forwarded to {recipient_name} for review.",
            "timestamp": now,
            "action": "forward_for_review",
        }

        # ── PHASE SUPPORT: Update both phase and root ──
        db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    f"{phase_path}.current_holder": recipient_email,
                    f"{phase_path}.status": "Forwarded",
                    f"{phase_path}.forwarded_status": "Forwarded",
                    "current_holder": recipient_email,
                    "assigned_to": recipient_email,
                    "viewed_by_target": False,
                    "status": "Forwarded",
                    "forwarded_status": "Forwarded",
                    "forwarded_at": now,
                    "forwarded_by": session.get("name"),
                    "forwarded_by_email": current_email,
                    "forwarded_to": recipient_email,
                    "forwarded_to_name": recipient_name,
                    "doc_lifecycle_stage": DocStage.IN_REVIEW,
                },
                "$push": {
                    f"{phase_path}.assignment_history": assignment_entry,
                    "assignment_history": assignment_entry,
                    "remarks": remark_entry,
                    "full_assignment_history": assignment_entry,
                },
            },
        )

        # Update correspondence entry
        db.documents.update_one(
            {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
            {
                "$set": {
                    "correspondence.$.forwarded_status": "Forwarded",
                    "correspondence.$.status": "Forwarded",
                    "correspondence.$.current_holder": recipient_email,
                    "correspondence.$.forwarded_at": now,
                    "correspondence.$.forwarded_to": recipient_email,
                    "correspondence.$.forwarded_to_name": recipient_name,
                    "correspondence.$.review_status": CorrespondenceReview.IN_REVIEW,
                },
                "$push": {
                    "correspondence.$.assignments": assignment_entry,
                    "correspondence.$.remarks": remark_entry,
                },
            },
        )

        # Socket notification trigger
        if recipient_email:
            sender_desc = (
                session.get("appt")
                or session.get("role", "Sender").replace("_", " ").title()
            )
            emit_document_notification(
                recipient_email,
                doc_id,
                doc.get("subject", "No Subject"),
                sender_desc,
                remark_text,
                action="forwarded",
                doc_reference=doc.get("reference_number", ""),
            )

        flash(f"Correspondence forwarded to {recipient_name} for review.", "success")
        return redirect(
            url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
        )

    except Exception as e:
        print(f"FORWARD CORRESPONDENCE ERROR: {e}")
        import traceback

        traceback.print_exc()
        flash("An error occurred while forwarding.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))


# ─────────────────────────────────────────────────────────────────────────────
# RETURN FOR AMENDMENT — move current_holder DOWN the chain (toward last index)
# ─────────────────────────────────────────────────────────────────────────────


@forward_returned_routes.route("/return_for_amendment/<doc_id>", methods=["POST"])
def return_for_amendment(doc_id):
    try:
        doc = db.documents.find_one({"_id": ObjectId(doc_id)})
        if not doc:
            flash("Document not found.", "error")
            return redirect(url_for("base_document_routes.documents_content"))

        remark = request.form.get("return_remark", "").strip()
        now = datetime.now()
        sequence = request.args.get("seq", 0, type=int)
        current_email = session.get("email")

        if sequence == 0:
            flash("Cannot return the original document.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        entry = next(
            (c for c in doc.get("correspondence", []) if c.get("sequence") == sequence),
            None,
        )
        if not entry:
            flash("Correspondence entry not found.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        # ── PHASE SUPPORT: Get chain from current phase ──
        current_phase_idx = doc.get("current_phase", 0)
        phases = doc.get("phases", [])

        # Ensure phases exist (backward compatibility)
        if not phases:
            phases = [
                {
                    "phase_number": 0,
                    "directorate": doc.get("origin_directorate", "DCS"),
                    "review_chain": doc.get("review_chain", []),
                    "current_holder": doc.get("current_holder"),
                    "assignment_history": doc.get("assignment_history", []),
                }
            ]
            current_phase_idx = 0

        current_phase = (
            phases[current_phase_idx] if current_phase_idx < len(phases) else phases[0]
        )
        phase_path = f"phases.{current_phase_idx}"

        # Get chain from phase, fallback to root
        chain = current_phase.get("review_chain", [])
        if not chain:
            chain = doc.get("review_chain", [])

        my_index = next(
            (i for i, m in enumerate(chain) if m.get("email") == current_email), -1
        )

        if my_index == -1:
            flash(
                "You are not part of the review chain for this correspondence.", "error"
            )
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        # ── Cannot return if already at the bottom ──
        if my_index >= len(chain) - 1:
            flash(
                "You are at the bottom of the review chain; there is no one below to return to.",
                "error",
            )
            return redirect(
                url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
            )

        # ── Return to the person one step DOWN (index + 1) ──
        target = chain[my_index + 1]
        return_to_email = target["email"]
        return_to_name = target.get("name") or return_to_email
        return_to_rank = target.get("rank", "")
        return_to_appt = target.get("appt", "")
        return_to_role = target.get("role", "")

        if not return_to_appt:
            u = db.users.find_one({"email": return_to_email})
            if u and u.get("appt"):
                return_to_appt = u.get("appt")
            elif u and u.get("role"):
                return_to_appt = u.get("role").replace("_", " ").title()
            elif return_to_role:
                return_to_appt = return_to_role.replace("_", " ").title()
            else:
                return_to_appt = return_to_name

        assignment_entry = {
            "assigned_to": return_to_email,
            "assigned_to_name": return_to_name,
            "assigned_to_rank": return_to_rank,
            "assigned_to_appt": return_to_appt,
            "assigned_by": current_email,
            "assigned_by_name": session.get("name"),
            "assigned_by_role": session.get("role"),
            "assigned_by_rank": session.get("rank"),
            "assigned_by_appt": session.get("appt"),
            "remark": remark or "Returned for amendment.",
            "timestamp": now,
            "action_type": "return_for_amendment",
            "chain_from_index": my_index,
            "chain_to_index": my_index + 1,
        }
        remark_entry = {
            "user": session.get("name"),
            "email": current_email,
            "role": session.get("role"),
            "rank": session.get("rank"),
            "appt": session.get("appt"),
            "remark": remark or "Returned for amendment.",
            "timestamp": now,
            "action": "return_for_amendment",
        }

        # ── PHASE SUPPORT: Update both phase and root ──
        db.documents.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    f"{phase_path}.forwarded_status": "Not Forwarded",
                    f"{phase_path}.is_editable": True,
                    f"{phase_path}.status": "Returned",
                    f"{phase_path}.current_holder": return_to_email,
                    "current_holder": return_to_email,
                    "assigned_to": return_to_email,
                    "viewed_by_target": False,
                    "forwarded_status": "Returned",
                    "is_editable": True,
                    "status": "Returned for Amendment",
                    "forwarded_at": None,
                    "doc_lifecycle_stage": DocStage.IN_REVIEW,
                },
                "$push": {
                    f"{phase_path}.assignment_history": assignment_entry,
                    "assignment_history": assignment_entry,
                    "remarks": remark_entry,
                    "full_assignment_history": assignment_entry,
                },
            },
        )

        # ── Update correspondence entry ──
        db.documents.update_one(
            {"_id": ObjectId(doc_id), "correspondence.sequence": sequence},
            {
                "$set": {
                    "correspondence.$.forwarded_status": "Not Forwarded",
                    "correspondence.$.is_editable": True,
                    "correspondence.$.status": "Returned",
                    "correspondence.$.forwarded_at": None,
                    "correspondence.$.forwarded_to": None,
                    "correspondence.$.forwarded_to_name": None,
                    "correspondence.$.current_holder": return_to_email,
                    "correspondence.$.review_status": CorrespondenceReview.IN_REVIEW,
                    "correspondence.$.visibility_status": CorrespondenceVisibility.INTERNAL,
                },
                "$push": {
                    "correspondence.$.assignments": assignment_entry,
                    "correspondence.$.remarks": remark_entry,
                },
            },
        )

        # Socket notification trigger
        if return_to_email:
            sender_desc = (
                session.get("appt")
                or session.get("role", "Sender").replace("_", " ").title()
            )
            emit_document_notification(
                return_to_email,
                doc_id,
                doc.get("subject", "No Subject"),
                sender_desc,
                remark,
                action="returned",
                doc_reference=doc.get("reference_number", ""),
            )

        flash(f"Document returned to {return_to_name} for amendment.", "success")
        return redirect(
            url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
        )

    except Exception as e:
        print(f"RETURN FOR AMENDMENT ERROR: {e}")
        import traceback

        traceback.print_exc()
        flash("An error occurred.", "error")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))
