from flask import Blueprint, redirect, url_for, flash, session, send_file
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from werkzeug.utils import secure_filename
import io

from modules.documents.functions_helper import (
    get_document_file_path,
    create_watermarked_pdf,
)

download_document_routes = Blueprint("download_document_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@download_document_routes.route("/download_correspondence_pdf/<doc_id>/<int:sequence>")
def download_correspondence_pdf(doc_id, sequence):
    from utils.pdf_helper import generate_letterhead_pdf, extract_memo_body

    document = db.documents.find_one({"_id": ObjectId(doc_id)})
    if not document:
        flash("Document not found.", "error")
        return redirect(url_for("base_document_routes.documents_content"))

    content_html = ""
    last_sig = None

    if sequence > 0:
        correspondence_list = document.get("correspondence", [])
        for corr in correspondence_list:
            if corr.get("sequence") == sequence:
                content_html = corr.get("content_html", "")
                sig_list = corr.get("signatures", [])
                if sig_list:
                    last_sig = sig_list[-1]
                break
    else:
        correspondence_list = document.get("correspondence", [])
        seq0 = next((c for c in correspondence_list if c.get("sequence") == 0), None)
        if seq0 and seq0.get("content_html"):
            content_html = seq0.get("content_html", "")
            sig_list = seq0.get("signatures", [])
            if sig_list:
                last_sig = sig_list[-1]
        elif document.get("content_html"):
            content_html = document.get("content_html", "")
            sig_list = document.get("signatures", [])
            if sig_list:
                last_sig = sig_list[-1]

    content_html = extract_memo_body(content_html)

    date_str = datetime.now().strftime("%d %B %Y")
    if last_sig and last_sig.get("timestamp"):
        ts = last_sig.get("timestamp")
        if isinstance(ts, datetime):
            date_str = ts.strftime("%d %B %Y")
        elif isinstance(ts, dict) and "$date" in ts:
            try:
                from dateutil import parser as dt_parser

                dt = dt_parser.isoparse(ts["$date"])
                date_str = dt.strftime("%d %B %Y")
            except Exception:
                pass

    sender = document.get("sender", "")
    subject = document.get("memo_title") or document.get("subject", "")
    reference_number = document.get("reference_number", "")

    try:
        pdf_data = generate_letterhead_pdf(
            reference_number=reference_number,
            sender=sender,
            subject=subject,
            content_html=content_html,
            last_sig=last_sig,
            date_str=date_str,
        )

        download_name = secure_filename(f"{reference_number}_letterhead.pdf")

        return send_file(
            io.BytesIO(pdf_data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        print(f"[Letterhead PDF Gen Error]: {e}")
        flash("Could not generate letterhead PDF.", "error")
        return redirect(
            url_for("open_document_routes.open_document", doc_id=doc_id, view_seq=sequence)
        )


@download_document_routes.route("/download_document/<doc_id>")
def download_document(doc_id):
    document = db.documents.find_one({"_id": ObjectId(doc_id)})

    if not document:
        flash("Document not found.", "error")
        return redirect(url_for("base_document_routes.documents_content"))

    # ── Determine source: GridFS upload or file system (legacy) ──
    main_file_id = document.get("main_file_id")
    content_html = document.get("content_html", "")

    if not main_file_id and not content_html:
        # Legacy file system fallback
        file_path = get_document_file_path(document)
        if not file_path:
            flash("Document file not found.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))
    elif content_html and not main_file_id:
        flash("HTML documents cannot be downloaded as PDF yet.", "info")
        return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    # ── Watermark label ──
    username = session.get("name") or session.get("email") or "Unknown user"
    downloaded_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    watermark_text = f"Downloaded by: {username} | {downloaded_at}"

    reference_number = document.get("reference_number") or "document"
    download_name = (
        secure_filename(f"{reference_number}_watermarked.pdf")
        or "document_watermarked.pdf"
    )

    # ── GridFS path ──
    if main_file_id:
        main_filename = document.get("main_filename", "")

        if not main_filename.lower().endswith(".pdf"):
            flash(
                "Watermarked download is currently available for PDF documents only.",
                "error",
            )
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        try:
            # Read file bytes from GridFS into a BytesIO buffer
            grid_file = fs.get(ObjectId(main_file_id))
            pdf_bytes = grid_file.read()
            pdf_buffer = io.BytesIO(pdf_bytes)

            # Pass the BytesIO to your watermark function
            watermarked_pdf = create_watermarked_pdf(pdf_buffer, watermark_text)

        except Exception as e:
            print(f"[Download Watermark] GridFS PDF stamp error: {e}")
            flash("Could not prepare the watermarked document for download.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    # ── Legacy file system path ──
    else:
        if not file_path.lower().endswith(".pdf"):
            flash(
                "Watermarked download is currently available for PDF documents only.",
                "error",
            )
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

        try:
            watermarked_pdf = create_watermarked_pdf(file_path, watermark_text)
        except Exception as e:
            print(f"[Download Watermark] File system PDF stamp error: {e}")
            flash("Could not prepare the watermarked document for download.", "error")
            return redirect(url_for("open_document_routes.open_document", doc_id=doc_id))

    return send_file(
        watermarked_pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=download_name
    )
