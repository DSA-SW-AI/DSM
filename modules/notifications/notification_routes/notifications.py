from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from bson import ObjectId
from datetime import datetime

from permissions import ROLE_PERMISSIONS 

notification_routes = Blueprint('notification_routes', __name__)


@notification_routes.route('/notifications')
def get_notifications():
    if 'user_email' not in session:
        flash("Please log in to view notifications.")
        return redirect(url_for('index'))
            
    user_id = session.get('user_id')
    users_coll = current_app.users_collection
    
    if not user_id:
        user = users_coll.find_one({"email": session.get('user_email')})
        if user:
            session['user_id'] = str(user['_id'])
            user_id = str(user['_id'])
        else:
            flash("User not found.")
            return redirect(url_for('login'))
    else:
        user = users_coll.find_one({"_id": ObjectId(user_id)})
        if not user:
            flash("User not found.")
            return redirect(url_for('login'))
            
    user_role = user.get('role', '')
    service_number = user.get('service_number')
    role = user.get('role')
    directorate = user.get('directorate')
        
    query = {
        "$and": [
            {
                "$or": [
                    {"isActive": True},
                    {"is_active": True}
                ]
            },
            {
                "$or": [
                    {"target.type": "role", "target.role": "broadcast"},
                    {"target.userId": service_number},
                    {"target.email": session.get('user_email')},
                    {"target.userId": session.get('user_email')},
                    {
                        "target.type": "role",
                        "target.role": role,
                        "$or": [
                            {"target.directorate": directorate},
                            {"target.directorate": {"$exists": False}},
                            {"target.directorate": None}
                        ]
                    }
                ]
            }
        ]
    }

    notification_coll = current_app.notifications_collection
        
    notifications_list = list(notification_coll.find(query).sort("createdAt", -1).limit(100))
    for n in notifications_list:
        n['_id'] = str(n['_id'])
        if 'applicationId' in n:
            n['applicationId'] = str(n['applicationId'])
        n['is_read'] = service_number in n.get('readBy', [])

    user_allowed_features = ROLE_PERMISSIONS.get(user_role, ROLE_PERMISSIONS['civilian'])
    return render_template(
        'notifications.html',
        notifications=notifications_list,
        service_number=service_number,
        permissions=user_allowed_features,
        active_page='notifications'
    )


@notification_routes.route('/notifications/read/<notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401
            
    user_id = session.get('user_id')
    users_coll = current_app.users_collection
    notification_coll = current_app.notifications_collection

    if not user_id:
        user = users_coll.find_one({"email": session.get('user_email')})
        if user:
            session['user_id'] = str(user['_id'])
            user_id = str(user['_id'])
        else:
            return jsonify({"error": "User not found"}), 404
    else:
        user = users_coll.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
    service_number = user.get('service_number')
        
    notification_coll.update_one(
        {"_id": ObjectId(notification_id)},
        {"$addToSet": {"readBy": service_number}}
    )
    return jsonify({"success": True})


@notification_routes.route('/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    users_coll = current_app.users_collection
    notification_coll = current_app.notifications_collection
            
    user_id = session.get('user_id')
    if not user_id:
        user = users_coll.find_one({"email": session.get('user_email')})
        if user:
            session['user_id'] = str(user['_id'])
            user_id = str(user['_id'])
        else:
            return jsonify({"error": "User not found"}), 404
    else:
        user = users_coll.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
            
    service_number = user.get('service_number')
    role = user.get('role')
    directorate = user.get('directorate')
        
    query = {
        "is_active": True,
        "readBy": {"$ne": service_number},
        "$or": [
            {"target.type": "role", "target.role": "broadcast"},
            {"target.userId": service_number},
            {
                "target.type": "role",
                "target.role": role,
                "$or": [
                    {"target.directorate": directorate},
                    {"target.directorate": {"$exists": False}},
                    {"target.directorate": None}
                ]
            }
        ]
    }
        
    notification_coll.update_many(
        query,
        {"$addToSet": {"readBy": service_number}}
    )
    return jsonify({"success": True})


@notification_routes.route('/notifications/delete/<notification_id>', methods=['POST'])
def delete_notification(notification_id):
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    notification_coll = current_app.notifications_collection
            
    notification_coll.update_one(
        {"_id": ObjectId(notification_id)},
        {"$set": {"is_active": False}}
    )
    return jsonify({"success": True})