from flask import Blueprint, app, render_template, request, jsonify, redirect, url_for, flash, session, current_app
from bson import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename

from permissions import ROLE_PERMISSIONS 

support_routes = Blueprint('support_routes', __name__)


@support_routes.route('/support')
def support():
    if 'user_email' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('index'))
        
    user_role = session.get('role')
    user_email = session.get('user_email')

    support_tickets_coll = current_app.support_tickets_collection
    users_coll = current_app.users_collection
        
    # Mark support notifications as read
    try:
        notifications_coll = current_app.notifications_collection
        user_doc = users_coll.find_one({"email": user_email})
        if user_doc:
            service_number = user_doc.get("service_number")
            if service_number:
                if user_role == 'super_admin':
                    notifications_coll.update_many(
                        {"type": "new_support_ticket", "readBy": {"$ne": service_number}},
                        {"$addToSet": {"readBy": service_number}}
                    )
                else:
                    notifications_coll.update_many(
                        {"type": "support_response", "target.email": user_email, "readBy": {"$ne": service_number}},
                        {"$addToSet": {"readBy": service_number}}
                    )
    except Exception as e:
        print(f"Error marking support notifications as read: {e}")

    if user_role == 'super_admin':
        tickets = list(support_tickets_coll.find().sort("created_at", -1))
    else:
        tickets = list(support_tickets_coll.find({"user_email": user_email}).sort("created_at", -1))

    user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])
    return render_template(
        'support.html',
        tickets=tickets,
        role=user_role,
        active_page='support',
        permissions=user_allowed_features
    )


@support_routes.route('/support/create', methods=['POST'])
def create_support_ticket():
    if 'user_email' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('index'))
            
    subject = request.form.get('subject', '').strip()
    category = request.form.get('category', '').strip()
    priority = request.form.get('priority', '').strip()
    description = request.form.get('description', '').strip()

    support_tickets_coll = current_app.support_tickets_collection
    users_coll = current_app.users_collection
        
    if not subject or not description:
        flash("Please fill in all required fields.", "error")
        return redirect(url_for('support_routes.support'))
            
    user_email = session.get('user_email')
    user = users_coll.find_one({"email": user_email})
    user_name = user.get('name') if user else session.get('name', 'User')
    user_directorate = session.get('directorate', 'DIR')
    user_role = session.get('role')
    
    attachment_url = None
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file and file.filename != '':
            fs = current_app.fs
            file_id = fs.put(
                file,
                filename=secure_filename(file.filename),
                content_type=file.content_type or 'application/octet-stream',
                metadata={
                    "uploaded_by": user_email,
                    "upload_date": datetime.now()
                }
            )
            attachment_url = f"/attachment/{str(file_id)}"
        
    ticket = {
        "user_email": user_email,
        "user_name": user_name,
        "user_directorate": user_directorate,
        "user_role": user_role,
        "subject": subject,
        "category": category,
        "priority": priority,
        "description": description,
        "status": "Open",
        "attachment_url": attachment_url,
        "created_at": datetime.now(),
        "responses": []
    }
        
    support_tickets_coll.insert_one(ticket)
    
    # Store notification for super admin
    try:
        notifications_coll = current_app.notifications_collection
        notifications_coll.insert_one({
            "type": "new_support_ticket",
            "applicationId": ObjectId("000000000000000000000000"),
            "referenceId": str(ticket["_id"]),
            "target": {
                "type": "role",
                "role": "super_admin"
            },
            "message": f"New support ticket submitted by {user_name}: '{subject}'.",
            "status": "unread",
            "isActive": True,
            "is_active": True,
            "createdAt": datetime.utcnow()
        })
        
        # Socket emit to ROLE_super_admin
        from modules.extensions import socketio
        socketio.emit("new_notification", {
            "type": "new_support_ticket",
            "applicationId": "000000000000000000000000",
            "referenceId": str(ticket["_id"]),
            "message": f"New support ticket submitted by {user_name}: '{subject}'."
        }, room="ROLE_super_admin")
    except Exception as e:
        print(f"Error creating ticket notification: {e}")

    flash("Support ticket submitted successfully.", "success")
    return redirect(url_for('support_routes.support'))


@support_routes.route('/support/ticket/<ticket_id>')
def get_support_ticket(ticket_id):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    support_tickets_coll = current_app.support_tickets_collection
            
    ticket = support_tickets_coll.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
            
    ticket_data = {
        "_id": str(ticket["_id"]),
        "user_email": ticket["user_email"],
        "user_name": ticket["user_name"],
        "user_directorate": ticket["user_directorate"],
        "user_role": ticket["user_role"],
        "subject": ticket["subject"],
        "category": ticket["category"],
        "priority": ticket["priority"],
        "description": ticket["description"],
        "status": ticket["status"],
        "attachment_url": ticket.get("attachment_url"),
        "created_at": ticket["created_at"].isoformat() if ticket.get("created_at") else "",
        "responses": []
    }
        
    for r in ticket.get("responses", []):
        ticket_data["responses"].append({
            "author_name": r["author_name"],
            "author_email": r["author_email"],
            "message": r["message"],
            "attachment_url": r.get("attachment_url"),
            "timestamp": r["timestamp"].isoformat() if r.get("timestamp") else ""
        })
            
    return jsonify(ticket_data)


@support_routes.route('/support/reply/<ticket_id>', methods=['POST'])
def reply_support_ticket(ticket_id):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401
            
    if request.is_json:
        data = request.get_json() or {}
        message = data.get('message', '').strip()
    else:
        message = request.form.get('message', '').strip()

    users_coll = current_app.users_collection
    support_tickets_coll = current_app.support_tickets_collection
        
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
            
    user_email = session.get('user_email')
    user = users_coll.find_one({"email": user_email})
    user_name = user.get('name') if user else session.get('name', 'User')
    user_role = session.get('role')
    
    attachment_url = None
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file and file.filename != '':
            fs = current_app.fs
            file_id = fs.put(
                file,
                filename=secure_filename(file.filename),
                content_type=file.content_type or 'application/octet-stream',
                metadata={
                    "uploaded_by": user_email,
                    "upload_date": datetime.now()
                }
            )
            attachment_url = f"/attachment/{str(file_id)}"
        
    reply = {
        "author_name": user_name,
        "author_email": user_email,
        "message": message,
        "attachment_url": attachment_url,
        "timestamp": datetime.now()
    }
        
    update_doc = {"$push": {"responses": reply}}
    if user_role == 'super_admin':
        ticket = support_tickets_coll.find_one({"_id": ObjectId(ticket_id)})
        if ticket and ticket.get("status") == "Open":
            update_doc["$set"] = {"status": "In Progress"}
                
    support_tickets_coll.update_one({"_id": ObjectId(ticket_id)}, update_doc)
    
    # Store notification and emit socket event for replies
    try:
        ticket = support_tickets_coll.find_one({"_id": ObjectId(ticket_id)})
        if ticket:
            notifications_coll = current_app.notifications_collection
            # If a super admin replies, notify the ticket owner
            if user_role == 'super_admin':
                target_email = ticket.get("user_email")
                if target_email:
                    notifications_coll.insert_one({
                        "type": "support_response",
                        "applicationId": ObjectId("000000000000000000000000"),
                        "referenceId": str(ticket["_id"]),
                        "target": {
                            "type": "user",
                            "email": target_email
                        },
                        "message": f"New support response on your ticket '{ticket.get('subject')}'.",
                        "status": "unread",
                        "isActive": True,
                        "is_active": True,
                        "createdAt": datetime.utcnow()
                    })
                    
                    from modules.extensions import socketio
                    socketio.emit("new_notification", {
                        "type": "support_response",
                        "applicationId": "000000000000000000000000",
                        "referenceId": str(ticket["_id"]),
                        "message": f"New support response on your ticket '{ticket.get('subject')}'."
                    }, room=f"USER_{target_email.lower().strip()}")
            else:
                # If a user replies, notify super admins
                notifications_coll.insert_one({
                    "type": "new_support_ticket",
                    "applicationId": ObjectId("000000000000000000000000"),
                    "referenceId": str(ticket["_id"]),
                    "target": {
                        "type": "role",
                        "role": "super_admin"
                    },
                    "message": f"New reply from {user_name} on support ticket '{ticket.get('subject')}'.",
                    "status": "unread",
                    "isActive": True,
                    "is_active": True,
                    "createdAt": datetime.utcnow()
                })
                
                from modules.extensions import socketio
                socketio.emit("new_notification", {
                    "type": "new_support_ticket",
                    "applicationId": "000000000000000000000000",
                    "referenceId": str(ticket["_id"]),
                    "message": f"New reply from {user_name} on support ticket '{ticket.get('subject')}'."
                }, room="ROLE_super_admin")
    except Exception as e:
        print(f"Error creating reply notification: {e}")

    return jsonify({"success": True})


@support_routes.route('/support/status/<ticket_id>', methods=['POST'])
def update_support_status(ticket_id):
    if 'user_email' not in session or session.get('role') != 'super_admin':
        return redirect(url_for('index'))
            
    data = request.get_json() or {}
    status = data.get('status', '').strip()

    support_tickets_coll = current_app.support_tickets_collection
        
    if status not in ['Open', 'In Progress', 'Resolved']:
        return jsonify({"error": "Invalid status"}), 400
            
    support_tickets_coll.update_one(
        {"_id": ObjectId(ticket_id)},
        {"$set": {"status": status}}
    )
    return jsonify({"success": True})
