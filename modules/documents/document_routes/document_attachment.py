from flask import Blueprint, request, Response, redirect, url_for, flash
from datetime import datetime
from bson import ObjectId
from gridfs import GridFS
from pymongo import MongoClient
from werkzeug.utils import secure_filename

document_attachment_routes = Blueprint("document_attachment_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@document_attachment_routes.route("/view_attachment/<file_id>")
def view_attachment(file_id):
    try:
        grid_file = fs.get(ObjectId(file_id))
        return Response(
            grid_file.read(),
            mimetype=grid_file.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'inline; filename="{grid_file.filename}"'},
        )
    except Exception as e:
        print(f"VIEW ATTACHMENT ERROR: {str(e)}")
        flash("Attachment not found.", "error")
        return redirect(request.referrer or url_for("base_document_routes.documents_content"))


@document_attachment_routes.route("/download_attachment/<file_id>/<filename>")
def download_attachment(file_id, filename):
    try:
        grid_file = fs.get(ObjectId(file_id))
        safe_filename = secure_filename(filename or grid_file.filename)
        return Response(
            grid_file.read(),
            mimetype=grid_file.content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    except Exception as e:
        print(f"DOWNLOAD ATTACHMENT ERROR: {str(e)}")
        flash("Attachment could not be downloaded.", "error")
        return redirect(request.referrer or url_for("base_document_routes.documents_content"))
