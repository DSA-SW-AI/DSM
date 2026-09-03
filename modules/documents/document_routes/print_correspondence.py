from flask import Blueprint, render_template, redirect, url_for, flash
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient

print_correspondence_routes = Blueprint("print_correspondence_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@print_correspondence_routes.route("/print_correspondence/<doc_id>/<int:sequence>")
def print_correspondence(doc_id, sequence):
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

    from utils.pdf_helper import extract_memo_body

    content_html = extract_memo_body(content_html)

    # Convert date to standard string
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

    return render_template(
        "print_correspondence.html",
        document=document,
        content_html=content_html,
        sequence=sequence,
        date_str=date_str,
        last_sig=last_sig,
    )
