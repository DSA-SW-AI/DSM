from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from bson import ObjectId
from werkzeug.utils import secure_filename
import json
import bleach
from gridfs import GridFS
from pymongo import MongoClient

from utils.constants import DocStage, CorrespondenceVisibility, CorrespondenceReview

subject_matter_routes = Blueprint("subject_matter_routes", __name__)


# Connect to MongoDB
MONGO_URI = "mongodb://localhost:27017/DSM"
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["DSM"]
fs = GridFS(db)


@subject_matter_routes.route("/subject_matter_filing")
def subject_matter_filing():
    if "user_id" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    user_role = session.get("role")
    if user_role not in ["registry", "central_registry", "super_admin"]:
        flash("Unauthorized access.", "error")
        return redirect(url_for("base_document_routes.documents_content"))

    suffix = request.args.get("suffix")

    if suffix:
        # View documents inside a specific folder
        folder = db.filing_folders.find_one({"suffix": suffix})
        if not folder:
            # Fallback to subject matters if not yet created as a filing folder
            sm = db.subject_matters.find_one({"suffix": suffix})
            if sm:
                folder = {"suffix": sm["suffix"], "subject": sm["subject"]}
            else:
                flash("Folder not found.", "error")
                return redirect(url_for("subject_matter_routes.subject_matter_filing"))

        # Find documents matching the folder_suffix (filtered by involvement for registry users)
        doc_query = {"folder_suffix": suffix}
        user_directorate = session.get("directorate")
        if user_role in ["registry", "central_registry"] and user_directorate:
            doc_query["$or"] = [
                {"origin_directorate": user_directorate},
                {"target_directorate": user_directorate},
                {"phases.directorate": user_directorate},
            ]

        documents = list(db.documents.find(doc_query).sort("created_at", -1))

        # Format object IDs and compute phase info
        from modules.documents.functions_helper import add_phase_info_to_documents
        documents = add_phase_info_to_documents(documents, user_directorate)
        for doc in documents:
            doc["_id"] = str(doc["_id"])

        return render_template(
            "subject_matter_docs.html",
            folder=folder,
            documents=documents,
            active_page="subject_matter_filing",
        )

    else:
        # View all folders (based on all subject matters)
        # If subject_matters collection is empty, make sure to initialize it first
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
                {
                    "suffix": "9/1/TRG",
                    "subject": "COURSESLDRS/ RATING / AIRMEN / AIRWOMEN",
                },
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
                {
                    "suffix": "30/2/A",
                    "subject": "DISCIPLINE SLDRS/RATINGS/AIRMEN/AIRWOMEN",
                },
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
                {
                    "suffix": "101/1/A",
                    "subject": "DOCUMENT SLDRS/RATINGS/AIRMEN/AIRWOMEN",
                },
                {"suffix": "102/A", "subject": "CONVERSION OFFICERS"},
                {"suffix": "103/A", "subject": "RESEARCH/DEVELOPMENT"},
                {
                    "suffix": "104/A",
                    "subject": "DSA DISPATCH MOTORCYCLE REPAIRS/MAINTENANCE",
                },
                {"suffix": "105/A", "subject": "DSA GOVERNING COUNCIL"},
            ]
            db.subject_matters.insert_many(predefined_list)

        subject_matters = list(db.subject_matters.find().sort("suffix", 1))

        # Efficient aggregation query to get doc count for all folder_suffixes (filtered by involvement for registry users)
        user_directorate = session.get("directorate")
        match_stage = {"folder_suffix": {"$ne": None}}
        if user_role in ["registry", "central_registry"] and user_directorate:
            match_stage["$or"] = [
                {"origin_directorate": user_directorate},
                {"target_directorate": user_directorate},
                {"phases.directorate": user_directorate},
            ]

        pipeline = [
            {"$match": match_stage},
            {"$group": {"_id": "$folder_suffix", "count": {"$sum": 1}}},
        ]
        counts = {r["_id"]: r["count"] for r in db.documents.aggregate(pipeline)}

        folders_with_counts = []
        for sm in subject_matters:
            suffix_code = sm["suffix"]
            folders_with_counts.append(
                {
                    "suffix": suffix_code,
                    "subject": sm["subject"],
                    "count": counts.get(suffix_code, 0),
                }
            )

        return render_template(
            "subject_matter_folders.html",
            folders=folders_with_counts,
            active_page="subject_matter_filing",
        )
