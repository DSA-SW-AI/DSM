from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from bson import ObjectId
from werkzeug.utils import secure_filename
import json
import bleach
from gridfs import GridFS
from pymongo import MongoClient

from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview

add_document_routes = Blueprint("add_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@add_document_routes.route("/add_document", methods=["GET", "POST"])
def add_document():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        try:
            doc_mode = request.form.get("doc_mode", "create")
            reference_number = request.form.get("reference_number", "").strip()
            subject = request.form.get("subject", "").strip()
            sender = request.form.get("sender", "").strip()
            priority = request.form.get("priority", "").strip()
            correspondence_type = (
                request.form.get("correspondence_type", "").strip() or None
            )
            precedence = request.form.get("precedence", "").strip() or None
            memo_title = request.form.get("memo_title", "").strip().upper()
            comment = request.form.get("Comment", "").strip()
            name = request.form.get("name", "").strip()
            rank = request.form.get("rank", "").strip()
            appt = request.form.get("appt", "").strip()
            memo_to = request.form.get("memo_to", "").strip()
            parent_doc_id = request.form.get("parent_doc_id", "").strip() or None
            folder_name = request.form.get("folder_name", "").strip() or None
            created_at = datetime.now()
            created_by = session.get("name")
            sender_email = session.get("email")
            origin_directorate = session.get("directorate")
            clean_comment = bleach.clean(comment) if comment else ""

            if not reference_number:
                flash("Reference number is required.", "error")
                return redirect(url_for("add_document_routes.add_document"))
            if not subject:
                flash("Subject is required.", "error")
                return redirect(url_for("add_document_routes.add_document"))

            # --------------------------------------------------
            # ATTACHMENTS (unchanged)
            # --------------------------------------------------
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

            for index, file in enumerate(uploaded_files):
                if not file or file.filename == "":
                    continue
                filename = secure_filename(file.filename)
                if not filename.lower().endswith(allowed_extensions):
                    flash(f"Invalid attachment type: {filename}", "error")
                    return redirect(url_for("add_document_routes.add_document"))
                metadata = (
                    attachments_metadata[index]
                    if index < len(attachments_metadata)
                    else {}
                )
                file_id = fs.put(
                    file, filename=filename, content_type=file.content_type
                )
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
                        "uploaded_at": created_at,
                    }
                )

            # --------------------------------------------------
            # REMARKS
            # --------------------------------------------------
            remarks = []
            if clean_comment:
                remarks.append(
                    {
                        "user": created_by,
                        "email": sender_email,
                        "role": session.get("role"),
                        "remark": clean_comment,
                        "timestamp": created_at,
                    }
                )

            # ==================================================
            # CASE A: THIS IS A REPLY (parent_doc_id exists)
            # Push a new correspondence entry INTO the parent doc
            # ==================================================
            if parent_doc_id:
                try:
                    parent_doc = db.documents.find_one({"_id": ObjectId(parent_doc_id)})
                    if not parent_doc:
                        flash("Parent document not found.", "error")
                        return redirect(url_for("add_document_routes.add_document"))

                    existing_correspondence = parent_doc.get("correspondence", [])
                    next_sequence = len(existing_correspondence)

                    # Find which assignment triggered this reply
                    triggered_by = None
                    for entry in parent_doc.get("assignment_history", []):
                        if entry.get("assigned_to") == sender_email:
                            triggered_by = {
                                "assigned_to": entry.get("assigned_to"),
                                "assigned_to_name": entry.get("assigned_to_name"),
                                "assigned_by": entry.get("assigned_by"),
                                "assigned_by_name": entry.get("assigned_by_name"),
                                "assigned_by_role": entry.get("assigned_by_role"),
                                "assigned_by_rank": entry.get("assigned_by_rank"),
                                "assigned_by_appt": entry.get("assigned_by_appt"),
                                "assignment_timestamp": entry.get("timestamp"),
                            }
                            break

                    # Build content for this reply (unchanged)
                    reply_content_html = None
                    reply_main_file_id = None
                    reply_main_filename = None
                    reply_is_editable = False
                    reply_source = None

                    if doc_mode == "create":
                        content_html = request.form.get("content_html", "")
                        if not content_html or content_html.strip() in (
                            "",
                            "<p><br></p>",
                        ):
                            flash("Please enter document content.", "error")
                            return redirect(url_for("add_document_routes.add_document"))

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
                            "svg",
                            "circle",
                            "path",
                            "g",
                            "ellipse",
                            "text",
                            "rect",
                            "line",
                            "polyline",
                            "polygon",
                            "defs",
                        ]
                        allowed_attrs = {
                            "*": ["style", "class", "id"],
                            "a": ["href", "target"],
                            "img": ["src", "alt", "onerror", "width", "height"],
                            "svg": ["viewBox", "xmlns", "width", "height", "display"],
                            "circle": [
                                "cx",
                                "cy",
                                "r",
                                "fill",
                                "stroke",
                                "stroke-width",
                            ],
                            "path": ["d", "fill", "stroke", "stroke-width", "opacity"],
                            "ellipse": ["cx", "cy", "rx", "ry", "fill", "opacity"],
                            "g": ["fill", "opacity", "font-size"],
                            "text": [
                                "x",
                                "y",
                                "text-anchor",
                                "font-family",
                                "font-size",
                                "fill",
                                "letter-spacing",
                            ],
                        }
                        reply_content_html = bleach.clean(
                            content_html,
                            tags=allowed_tags,
                            attributes=allowed_attrs,
                            protocols=["http", "https", "data"],
                            strip=True,
                        )
                        reply_is_editable = True
                        reply_source = "created_in_system"

                    else:  # upload mode
                        main_file = request.files.get("pdf_file")
                        if not main_file or main_file.filename == "":
                            flash("Please upload a document.", "error")
                            return redirect(url_for("add_document_routes.add_document"))
                        filename = secure_filename(main_file.filename)
                        if not filename.lower().endswith((".pdf", ".doc", ".docx")):
                            flash("Only PDF, DOC, and DOCX are allowed.", "error")
                            return redirect(url_for("add_document_routes.add_document"))
                        main_file_id = fs.put(
                            main_file,
                            filename=filename,
                            content_type=main_file.content_type,
                        )
                        reply_main_file_id = str(main_file_id)
                        reply_main_filename = filename
                        reply_is_editable = False
                        reply_source = "uploaded"

                    # Build the correspondence entry (unchanged)
                    correspondence_entry = {
                        "sequence": next_sequence,
                        "type": "reply",
                        "memo_title": memo_title,
                        "author": {
                            "name": created_by,
                            "email": sender_email,
                            "rank": session.get("rank", ""),
                            "appt": session.get("appt", ""),
                            "role": session.get("role", ""),
                        },
                        "created_at": created_at,
                        "created_by": created_by,
                        "name": name,
                        "rank": rank,
                        "appt": appt,
                        "content_html": reply_content_html,
                        "main_file_id": reply_main_file_id,
                        "main_filename": reply_main_filename,
                        "is_editable": reply_is_editable,
                        "source": reply_source,
                        "attachments": saved_attachments,
                        "remarks": remarks,
                        "assignments": [],
                        "signatures": [],
                        "versions": [],
                        "triggered_by": triggered_by,
                        "forwarded_status": "Not Forwarded",
                        "status": "Saved",
                        "visibility_status": CorrespondenceVisibility.INTERNAL,
                        "review_status": CorrespondenceReview.DRAFT,
                    }

                    # ==============================================================
                    # PHASE SUPPORT: Get current phase and add assignment
                    # ==============================================================
                    phase_idx = parent_doc.get("current_phase", 0)
                    phase_path = f"phases.{phase_idx}"

                    # Add to phase's assignment history
                    assignment_entry = {
                        "assigned_to": sender_email,
                        "assigned_to_name": created_by,
                        "assigned_by": session.get("email"),
                        "assigned_by_name": session.get("name"),
                        "assigned_by_role": session.get("role"),
                        "assigned_by_rank": session.get("rank"),
                        "assigned_by_appt": session.get("appt"),
                        "remark": f"Reply added to sequence {next_sequence}",
                        "timestamp": created_at,
                        "action_type": "reply_created",
                    }

                    db.documents.update_one(
                        {"_id": ObjectId(parent_doc_id)},
                        {
                            "$push": {
                                "correspondence": correspondence_entry,
                                f"{phase_path}.assignment_history": assignment_entry,
                                "full_assignment_history": assignment_entry,
                            },
                            "$set": {
                                "current_holder": sender_email,
                                "active_correspondence": next_sequence,
                                "status": "In Progress",
                                "doc_lifecycle_stage": DocStage.AT_TARGET,
                                f"{phase_path}.status": "In Progress",
                            },
                        },
                    )

                    flash("Correspondence added successfully.", "success")
                    return redirect(
                        url_for("open_document_routes.open_document", doc_id=parent_doc_id)
                    )

                except Exception as e:
                    print(f"ADD CORRESPONDENCE ERROR: {str(e)}")
                    flash("An error occurred while adding the correspondence.", "error")
                    return redirect(url_for("add_document_routes.add_document"))

            # ==================================================
            # CASE B: ROOT DOCUMENT (no parent_doc_id)
            # Create new document with PHASES initialized
            # ==================================================
            # ── Folder Matching/Creation ──
            matched_subject_matter = None
            ref_num_upper = reference_number.upper()
            subject_matters = list(db.subject_matters.find({}))
            subject_matters.sort(key=lambda x: len(x["suffix"]), reverse=True)
            for sm in subject_matters:
                suffix_upper = sm["suffix"].upper()
                if (
                    ref_num_upper.endswith(f"/{suffix_upper}")
                    or f"/{suffix_upper}/" in ref_num_upper
                ):
                    matched_subject_matter = sm
                    break

            folder_suffix = None
            folder_subject = None
            if matched_subject_matter:
                folder_suffix = matched_subject_matter["suffix"]
                folder_subject = matched_subject_matter["subject"]
                # check if folder exists, if not create
                db.filing_folders.update_one(
                    {"suffix": folder_suffix},
                    {
                        "$setOnInsert": {
                            "suffix": folder_suffix,
                            "subject": folder_subject,
                            "created_at": datetime.now(),
                            "created_by": sender_email,
                        }
                    },
                    upsert=True,
                )
            # Build content (unchanged)
            root_content_html = None
            root_main_file_id = None
            root_main_filename = None
            root_is_editable = False
            root_source = None
            root_main_content_type = None

            if doc_mode == "create":
                content_html = request.form.get("content_html", "")
                if not content_html or content_html.strip() in ("", "<p><br></p>"):
                    flash("Please enter document content.", "error")
                    return redirect(url_for("add_document_routes.add_document"))

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
                    "svg",
                    "circle",
                    "path",
                    "g",
                    "ellipse",
                    "text",
                    "rect",
                    "line",
                    "polyline",
                    "polygon",
                    "defs",
                ]
                allowed_attrs = {
                    "*": ["style", "class", "id"],
                    "a": ["href", "target"],
                    "img": ["src", "alt", "onerror", "width", "height"],
                    "svg": ["viewBox", "xmlns", "width", "height", "display"],
                    "circle": ["cx", "cy", "r", "fill", "stroke", "stroke-width"],
                    "path": ["d", "fill", "stroke", "stroke-width", "opacity"],
                    "ellipse": ["cx", "cy", "rx", "ry", "fill", "opacity"],
                    "g": ["fill", "opacity", "font-size"],
                    "text": [
                        "x",
                        "y",
                        "text-anchor",
                        "font-family",
                        "font-size",
                        "fill",
                        "letter-spacing",
                    ],
                }
                root_content_html = bleach.clean(
                    content_html,
                    tags=allowed_tags,
                    attributes=allowed_attrs,
                    protocols=["http", "https", "data"],
                    strip=True,
                )
                root_is_editable = True
                root_source = "created_in_system"

            else:
                main_file = request.files.get("pdf_file")
                if not main_file or main_file.filename == "":
                    flash("Please upload a document.", "error")
                    return redirect(url_for("add_document_routes.add_document"))
                filename = secure_filename(main_file.filename)
                if not filename.lower().endswith((".pdf", ".doc", ".docx")):
                    flash("Only PDF, DOC, and DOCX are allowed.", "error")
                    return redirect(url_for("add_document_routes.add_document"))
                main_file_id = fs.put(
                    main_file, filename=filename, content_type=main_file.content_type
                )
                root_main_file_id = str(main_file_id)
                root_main_filename = filename
                root_main_content_type = main_file.content_type
                root_is_editable = False
                root_source = "uploaded"

            # Original correspondence (sequence 0)
            original_correspondence = {
                "sequence": 0,
                "type": correspondence_type or "incoming",
                "memo_title": memo_title,
                "author": {
                    "name": created_by,
                    "email": sender_email,
                    "rank": session.get("rank", ""),
                    "appt": session.get("appt", ""),
                    "role": session.get("role", ""),
                },
                "created_at": created_at,
                "created_by": created_by,
                "name": name,
                "rank": rank,
                "appt": appt,
                "content_html": root_content_html,
                "main_file_id": root_main_file_id,
                "main_filename": root_main_filename,
                "is_editable": root_is_editable,
                "source": root_source,
                "attachments": saved_attachments,
                "remarks": remarks,
                "assignments": [],
                "signatures": [],
                "versions": [],
                "triggered_by": None,
                "forwarded_status": "Not Forwarded",
                "status": "Saved",
                "visibility_status": CorrespondenceVisibility.VISIBLE,
                "review_status": CorrespondenceReview.DRAFT,
            }

            # ==============================================================
            # PHASE SUPPORT: Create root document with Phase 0
            # ==============================================================
            initial_phase = {
                "phase_number": 0,
                "directorate": origin_directorate,
                "type": "origin",
                "started_at": created_at,
                "started_by": sender_email,
                "current_holder": sender_email,
                "review_chain": [],  # Will be populated when users are added
                "assignment_history": [],  # Will be populated when assignments happen
                "seen_stamp_history": [],
            }

            target_user = db.users.find_one({"email": ""})  # no recipient yet
            target_directorate = None

            new_doc = {
                # Core identity (unchanged)
                "reference_number": reference_number,
                "subject": subject,
                "memo_title": memo_title,
                "sender": sender,
                "priority": priority,
                "correspondence_type": correspondence_type,
                "folder_name": folder_name,
                "folder_suffix": folder_suffix,
                "folder_subject": folder_subject,
                "precedence": precedence,
                "name": name,
                "rank": rank,
                "appt": appt,
                "sender_email": sender_email,
                "origin_directorate": origin_directorate,
                "target_directorate": target_directorate,
                "memo_to": memo_to,
                # Routing
                "assigned_to": None,
                "current_holder": sender_email,
                "viewed_by_target": False,
                "active_correspondence": 0,
                # ==============================================================
                # PHASE SUPPORT: New fields
                # ==============================================================
                "phases": [initial_phase],
                "current_phase": 0,
                "full_assignment_history": [],
                "full_review_chain": [],
                # Legacy fields (kept for backward compatibility)
                "assignment_history": [],
                "seen_stamp_history": [],
                "remarks": remarks,
                # Correspondence thread
                "correspondence": [original_correspondence],
                # Legacy fields
                "content_html": root_content_html or "",
                "main_file_id": root_main_file_id,
                "main_filename": root_main_filename,
                "attachments": saved_attachments,
                "is_editable": root_is_editable,
                "source": root_source,
                # Status
                "created_at": created_at,
                "created_by": created_by,
                "status": "Saved",
                "forwarded_status": "Not Forwarded",
                "parent_doc_id": None,
                "thread_id": None,
                "thread_sequence": 0,
                "triggered_by": None,
                "doc_lifecycle_stage": DocStage.DRAFT,
            }

            result = db.documents.insert_one(new_doc)

            # Patch thread_id = own _id for root documents
            db.documents.update_one(
                {"_id": result.inserted_id},
                {"$set": {"thread_id": str(result.inserted_id)}},
            )

            flash("Document created successfully.", "success")
            return redirect(url_for("base_document_routes.documents_content"))

        except Exception as e:
            print(f"ADD DOCUMENT ERROR: {str(e)}")
            flash("An error occurred while saving the document.", "error")
            return redirect(url_for("add_document_routes.add_document"))

    # ── GET ── (unchanged)
    current_email = session.get("email")
    users = list(
        db.users.find(
            {"email": {"$ne": current_email}},
            {"name": 1, "email": 1, "directorate": 1, "role": 1, "_id": 0},
        ).sort("name", 1)
    )

    parent_document = None
    parent_doc_id = request.args.get("parent_doc_id", "").strip()
    if parent_doc_id:
        try:
            parent_doc = db.documents.find_one({"_id": ObjectId(parent_doc_id)})
            if parent_doc:
                parent_doc["_id"] = str(parent_doc["_id"])
                parent_document = parent_doc
        except Exception:
            pass

    # Get predefined subject matters from db
    # If collection is empty, initialize it
    if db.subject_matters.count_documents({}) == 0:
        predefined_list = [
            {"suffix": "1/A", "subject": "POSTING AND APPOINTMENT OFFICERS"},
            {"suffix": "2/A", "subject": "POSTING SLDRS/RATINGS/AIRMEN/AIRWOMEN"},
            {"suffix": "3/A", "subject": "ESTABLISHMENT GENERAL"},
            {"suffix": "4/FIN", "subject": "RELEASE OF FUND"},
            {"suffix": "5/LOG", "subject": "Q MATTERS"},
            {"suffix": "6/OPS", "subject": "OPERATIONAL GENERAL"},
            {
                "suffix": "7/A",
                "subject": "ESTABLISHMENT OF NIGERIAN ARMED FORCES RESERVE",
            },
            {"suffix": "8/A", "subject": "DOCUMENT GENERAL"},
            {"suffix": "8/1/A", "subject": "TRI-SERVICE MATTERS"},
            {"suffix": "9/TRG", "subject": "COURSES OFFICERS"},
            {"suffix": "9/1/TRG", "subject": "COURSESLDRS/ RATING / AIRMEN / AIRWOMEN"},
            {"suffix": "9/2/TRG", "subject": "COURSES GENERAL"},
            {"suffix": "10/A", "subject": "EDUCATIONAL GENERAL"},
            {"suffix": "11/A", "subject": "MOD MATTERS"},
            {"suffix": "11/1/A", "subject": "NA MATTERS"},
            {"suffix": "11/2/A", "subject": "NN MATTERS"},
            {"suffix": "11/3/A", "subject": "NAF MATTERS"},
            {"suffix": "11/4/A", "subject": "NYSC MATTERS"},
            {"suffix": "12/A", "subject": "NATIONAL DEFENCE AND SECURITY POLICY"},
            {"suffix": "13/A", "subject": "ROUTINE ORDERS MATTERS"},
            {"suffix": "14/FIN", "subject": "BUDGET MATTERS"},
            {"suffix": "15/A", "subject": "FORCAST OF EVENTS"},
            {"suffix": "16/A", "subject": "AUDIT INSPECTION"},
            {"suffix": "17/A", "subject": "CORRESPONDENCE GENERAL"},
            {"suffix": "18/COMM", "subject": "ICT MATTERS"},
            {"suffix": "19/A", "subject": "GUARDS DUTIES GENERAL"},
            {"suffix": "20/A", "subject": "MONITORING TEAM"},
            {"suffix": "21/A", "subject": "DISMISSAL GENERAL"},
            {"suffix": "22/A", "subject": "LEAVE OFFICERS"},
            {"suffix": "22/1/A", "subject": "LEAVE SLDRS/RATING/AIRMEN/AIRWOMEN"},
            {"suffix": "22/2/A", "subject": "LEAVE CIVILIAN"},
            {"suffix": "23/A", "subject": "PROMOTION OFFICERS"},
            {"suffix": "24/A", "subject": "PROMOTION SLDRS/RATING/AIRMEN/AIRWOMEN"},
            {"suffix": "25/A", "subject": "RETIREMENT OFFICERS"},
            {"suffix": "26/A", "subject": "DISCHARGESLDRS/RATING/AIRMEN/AIRWOMEN"},
            {"suffix": "27/FIN", "subject": "PAY AND ALLOWANCE GENERAL"},
            {"suffix": "28/A", "subject": "PENSION/GRATUITY GENERAL"},
            {"suffix": "29/A", "subject": "VETERAN AFFAIRS/LEGION GENERAL"},
            {"suffix": "30/A", "subject": "DISCIPLINE GENERAL"},
            {"suffix": "30/1/A", "subject": "DISCIPLINE OFFICERS"},
            {"suffix": "30/2/A", "subject": "DISCIPLINE SLDRS/RATINGS/AIRMEN/AIRWOMEN"},
            {"suffix": "31/A", "subject": "POLICY AND PLANING"},
            {"suffix": "32/A", "subject": "TOURS & VISITS"},
            {"suffix": "33/A", "subject": "ADMINISTRATIVE INSTRUCTION GENERAL"},
            {"suffix": "34/A", "subject": "MEETINGS"},
            {"suffix": "35/A", "subject": "DSA TENDER BOARD"},
            {"suffix": "36/A", "subject": "CIVILIAN MATTERS"},
            {"suffix": "37/A", "subject": "NOTICAS"},
            {"suffix": "37/1/A", "subject": "CONDOLENCE"},
            {"suffix": "37/2/A", "subject": "BURIAL"},
            {"suffix": "38/A", "subject": "MOVEMENT GENERAL"},
            {"suffix": "39/A", "subject": "DEPOWA MATTERS"},
            {"suffix": "40/TRG", "subject": "SEMINAR & WORKSHOP"},
            {"suffix": "41/A", "subject": "COMMISSION GENERAL"},
            {"suffix": "42/A", "subject": "MEDICAL GENERAL"},
            {"suffix": "43/A", "subject": "HONOUR AND AWARDS"},
            {"suffix": "44/A", "subject": "CIVILIAN MATTERS"},
            {"suffix": "45/A", "subject": "GAZETTES GENERAL"},
            {"suffix": "46/LOG", "subject": "ACCOMMODATION GENERAL"},
            {"suffix": "47/A", "subject": "LITIGATION AND CIVIL SUITS"},
            {
                "suffix": "48/A",
                "subject": "ARMED FORCES WELFARE BOARD POST RETIREMENT HOUSING SCHEME",
            },
            {"suffix": "49/A", "subject": "WELFARE GENERAL"},
            {"suffix": "50/A", "subject": "RELIGIOUS MATTERS"},
            {"suffix": "50/1/A", "subject": "NAWIS/DEATH BENEFIT/LIFE INSURANCE"},
            {"suffix": "51/A", "subject": "PIONEER CONSUMER CAR LOAN SCHEME"},
            {"suffix": "52/PROC", "subject": "REQUISITION/AUTHORISATION"},
            {"suffix": "53/A", "subject": "COMPLAINT GENERAL"},
            {"suffix": "54/1/A", "subject": "CELEBRATION GENERAL"},
            {"suffix": "55/A", "subject": "PUBLIC HOLIDAY"},
            {"suffix": "56/A", "subject": "ARMED FORCES DAY CELEBRATION"},
            {"suffix": "56/1/A", "subject": "PASSPORT/VISA/OVERSEA TRAVELS"},
            {"suffix": "56/2/A", "subject": "SENIORITY ROLL AWO/MWO/WO"},
            {"suffix": "56/3/A", "subject": "STRENGTH RETURNS GENERAL"},
            {"suffix": "57/A", "subject": "ROAD TRAFFIC ACCIDENT"},
            {"suffix": "58/A", "subject": "INVESTIGATION GENERAL"},
            {"suffix": "58/1/A", "subject": "PARADE STATE"},
            {"suffix": "59/A", "subject": "COMPLAINT GENERAL"},
            {"suffix": "60/A", "subject": "AWOL OFFICERS"},
            {
                "suffix": "61/A",
                "subject": "PERFORMANCE EVALUATION REPORT OFFICERS (PER)",
            },
            {
                "suffix": "62/A",
                "subject": "CONFIDENTIAL REPORT SLDRS/RATING/AIRMEN/AIRWOMEN",
            },
            {"suffix": "62/1/A", "subject": "SENIORITY ROLL OFFICERS"},
            {"suffix": "63/A", "subject": "STRENGTH RETURNS FOREIGN ATTACHES"},
            {"suffix": "64/A", "subject": "COURT MARTIAL"},
            {"suffix": "64/1/A", "subject": "PARADE STATE"},
            {"suffix": "65/A", "subject": "DRESS REGULATIONS"},
            {"suffix": "65/1/A", "subject": "PROTOCOL LIST"},
            {"suffix": "66/A", "subject": "ID CARD MATTERS"},
            {"suffix": "67/A", "subject": "PRINTING/PUBLICATION"},
            {"suffix": "67/1/A", "subject": "EXTENSION OF SERVICE"},
            {"suffix": "68/A", "subject": "BRIEFING/INTERVIEW"},
            {"suffix": "69/A", "subject": "RECRUITMENT GENERAL"},
            {"suffix": "70/A", "subject": "COURT MARTIAL"},
            {"suffix": "71/LOG", "subject": "VEHICLE/TRANSPORT GENERAL"},
            {"suffix": "72/A", "subject": "MESS MATTERS"},
            {"suffix": "73/A", "subject": "INVITATION GENERAL"},
            {"suffix": "74/LOG", "subject": "SETTLEMENT OF BILLS"},
            {"suffix": "75/A", "subject": "C-IN-C ORDER OF THE DAY"},
            {"suffix": "76/A", "subject": "COMMITTEE GENERAL"},
            {"suffix": "77/A", "subject": "TACOS GENERAL"},
            {"suffix": "78/A", "subject": "MEMORANDUM OF UNDERSTANDING (MOU)"},
            {"suffix": "79/A", "subject": "NATIONAL DEVELOPMENT PLAN/CENSUS"},
            {"suffix": "80/A", "subject": "GOODWILL MESSAGE"},
            {
                "suffix": "81/A",
                "subject": "CORRESPONDENCE WITH FOREIGN MISSION IN NIGERIA",
            },
            {"suffix": "82/A", "subject": "PROGRESS REPORT"},
            {"suffix": "82/1/A", "subject": "SPACE MATTERS"},
            {"suffix": "83/A", "subject": "BOARD OF INQUIRY (BOI)"},
            {
                "suffix": "84/A",
                "subject": "CODE OF CONDUCT/ETHNIC AND CUSTOMS ARMED FORCES",
            },
            {
                "suffix": "84/1/A",
                "subject": "PART 2 ORDERS SLDRS/RATINGS/AIRMEN/AIRWOMEN",
            },
            {"suffix": "84/2/A", "subject": "PART X ORDERS GENERAL"},
            {"suffix": "85/A", "subject": "GAMES/SPORT GENERAL"},
            {"suffix": "86/A", "subject": "CONVENING ORDER GENERAL"},
            {"suffix": "87/A", "subject": "HUMAN RIGHTS"},
            {"suffix": "88/A", "subject": "POLICE/PARA MILITARY AFFAIRS"},
            {"suffix": "89/A", "subject": "NATIONAL ASSEMBLY"},
            {"suffix": "90/A", "subject": "PART 2 ORDERS OFFICERS"},
            {"suffix": "90/1/A", "subject": "DURBAR MATTERS"},
            {"suffix": "91/A", "subject": "NATIONAL MILITARY CEMETERY"},
            {"suffix": "91/1/A", "subject": "GROUP LIFE INSURANCE SCHEME"},
            {"suffix": "91/2/A", "subject": "SITREP GENERAL"},
            {"suffix": "91/3/A", "subject": "SAFETY STANDARD MEASURED"},
            {"suffix": "92/G", "subject": "ARMS & AMMUNITION"},
            {
                "suffix": "93/A",
                "subject": "INTERNATIONAL HUMANITARIAN LAW/LAW OF ARMED CONFLICT",
            },
            {"suffix": "93/1/A", "subject": "CORRESPONDENCE WITH BPP"},
            {"suffix": "94/TRG", "subject": "RANGE CLASSIFICATION"},
            {"suffix": "95/A", "subject": "DOCUMENT OFFICERS"},
            {"suffix": "95/LOG", "subject": "LAND MATTERS GENERAL"},
            {"suffix": "96/LOG", "subject": "CANTEEN MATTERS"},
            {"suffix": "97/A", "subject": "MUSEUM AND MONUMENTS"},
            {"suffix": "98/TRG", "subject": "SEARCH AND RESCUE"},
            {"suffix": "99/LOG", "subject": "CONTRACT GENERAL"},
            {"suffix": "100/LOG", "subject": "WORKS AND SERVICES"},
            {"suffix": "101/A", "subject": "DOCUMENT OFFICERS"},
            {"suffix": "101/1/A", "subject": "DOCUMENT SLDRS/RATINGS/AIRMEN/AIRWOMEN"},
            {"suffix": "102/A", "subject": "CONVERSION OFFICERS"},
            {"suffix": "103/A", "subject": "RESEARCH/DEVELOPMENT"},
            {
                "suffix": "104/A",
                "subject": "DSA DISPATCH MOTORCYCLE REPAIRS/MAINTENANCE",
            },
            {"suffix": "105/A", "subject": "DSA GOVERNING COUNCIL"},
        ]
        db.subject_matters.insert_many(predefined_list)

    predefined_docs = list(
        db.subject_matters.find({}, {"_id": 0, "suffix": 1, "subject": 1})
    )
    existing_folders = [f for f in db.documents.distinct("folder_name") if f]

    standard_directorates = [
        {"code": "CDSA", "name": "Chief of Defence Space Administration (CDSA)"},
        {"code": "DCDSA", "name": "Chief of Defence Space Administration (DCDSA)"},
        {"code": "DOA", "name": "Directorate of Administration (DOA)"},
        {"code": "DCS", "name": "Directorate of Communications Satellite (DCS)"},
        {"code": "DNPT", "name": "Directorate of Navigation & Positioning (DNPT)"},
        {"code": "DFA", "name": "Directorate of Finance (DFA)"},
        {"code": "DLSO", "name": "Directorate of Launch Services & Space Operations (DLSO)"},
        {"code": "DEO", "name": "Directorate of Earth Observation (DEO)"},
        {"code": "DCYBER", "name": "Directorate of Cyber Security (DCYBER)"},
        {"code": "DPPR", "name": "Directorate of Policy Planning and Research (DPPR)"},
        {"code": "DLOG", "name": "Directorate of Logistics (DLOG)"},
        {"code": "DELSPACE", "name": "Delspace"},
    ]
    existing_dir_codes = {d["code"].upper() for d in standard_directorates}
    db_dirs = set(filter(None, db.users.distinct("directorate") + db.personnel.distinct("directorate")))
    for d in sorted(db_dirs):
        d_clean = str(d).strip().upper()
        if d_clean and d_clean not in existing_dir_codes:
            standard_directorates.append({"code": d_clean, "name": d_clean})
            existing_dir_codes.add(d_clean)

    return render_template(
        "add_document.html",
        user_directorate=session.get("directorate"),
        users=users,
        parent_document=parent_document,
        parent_doc_id=parent_doc_id,
        active_page="add_document",
        existing_folders=existing_folders,
        predefined_docs=predefined_docs,
        directorates=standard_directorates,
    )
