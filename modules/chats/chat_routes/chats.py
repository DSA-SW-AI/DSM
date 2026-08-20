from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from bson import ObjectId
from datetime import datetime

from permissions import ROLE_PERMISSIONS 

chat_routes = Blueprint('chat_routes', __name__)


@chat_routes.route('/api/chats', methods=['GET'])
def api_get_chats():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    user_email = user_email.strip().lower()
    chats_coll = current_app.chats_collection
    conversations = list(chats_coll.find({
        "participants": {"$in": [user_email]}
        }).sort("updated_at", -1))
        
    results = []
    for chat in conversations:
        other_email = next((p for p in chat["participants"] if p != user_email), None)
        if not other_email:
            continue

        users_coll = current_app.users_collection
        other_user = users_coll.find_one({"email": other_email})
        other_name = other_user.get('name', other_email) if other_user else other_email
        other_rank = other_user.get('rankOrGrade', other_user.get('rank', 'N/A')) if other_user else 'N/A'
        other_appt = other_user.get('appt', 'N/A') if other_user else 'N/A'
        other_role = other_user.get('role', '') if other_user else ''
            
        safe_user_email = user_email.replace('.', '_')
        unread_count = chat.get("unread_count", {}).get(safe_user_email, 0)
            
        results.append({
            "chat_id": str(chat["_id"]),
            "other_email": other_email,
            "other_name": other_name,
            "other_rank": other_rank,
            "other_appt": other_appt,
            "other_role": other_role.replace('_', ' ').title() if other_role else '',
            "last_message": chat.get("last_message", ""),
            "unread_count": unread_count,
            "updated_at": chat.get("updated_at", datetime.utcnow()).isoformat()
        })
    return jsonify(results)


@chat_routes.route('/api/chats/<recipient_email>', methods=['GET'])
def api_get_chat_history(recipient_email):
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    user_email = user_email.strip().lower()
    recipient_email = recipient_email.strip().lower()

    chats_coll = current_app.chats_collection
    chat = chats_coll.find_one({
        "participants": {"$all": [user_email, recipient_email]}
    })
        
    if not chat:
        return jsonify([])
            
    messages = chat.get("messages", [])
    formatted_messages = []
    for m in messages[-50:]:
        formatted_messages.append({
            "sender_email": m.get("sender_email"),
            "sender_name": m.get("sender_name"),
            "text": m.get("text"),
            "timestamp": m.get("timestamp").isoformat() if isinstance(m.get("timestamp"), datetime) else m.get("timestamp")
        })
    return jsonify(formatted_messages)





@chat_routes.route('/api/chats/read/<recipient_email>', methods=['POST'])
def api_mark_chat_read(recipient_email):
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
            
    user_email = user_email.strip().lower()
    recipient_email = recipient_email.strip().lower()
    safe_user_email = user_email.replace('.', '_')
    
    chats_coll = current_app.chats_collection
    participants = sorted([user_email, recipient_email])
    
    result = chats_coll.update_one(
        {"participants": participants},
        {"$set": {f"unread_count.{safe_user_email}": 0}},
        upsert=False
    )
    
    # ✅ Debug: Check if update matched
    if result.matched_count == 0:
        print(f"⚠️ No chat found for {user_email} and {recipient_email}")
        return jsonify({"status": "error", "message": "Chat not found"}), 404
    
    if result.modified_count > 0:
        print(f"✅ Marked chat as read for {user_email}")
    
    return jsonify({"status": "success"})







@chat_routes.route('/api/chats/send', methods=['POST'])
def api_send_chat_message():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
        
    user_email = user_email.strip().lower()
    data = request.get_json() or {}
    recipient_email = data.get('recipient_email', '').strip().lower()
    text = data.get('text', '').strip()
    
    if not recipient_email or not text:
        return jsonify({"status": "error", "message": "Recipient and message text are required"}), 400
        
    chats_coll = current_app.chats_collection
    users_coll = current_app.users_collection
    
    recipient = users_coll.find_one({"email": recipient_email})
    if not recipient:
        return jsonify({"status": "error", "message": "Recipient user not found"}), 404
        
    sender = users_coll.find_one({"email": user_email})
    sender_name = sender.get('name') if sender else user_email
    
    message_doc = {
        "sender_email": user_email,
        "sender_name": sender_name,
        "text": text,
        "timestamp": datetime.utcnow()
    }
    
    safe_recipient_email = recipient_email.replace('.', '_')
    
    participants = sorted([user_email, recipient_email])
    chats_coll.update_one(
        {"participants": participants},
        {
            "$set": {
                "last_message": text,
                "updated_at": datetime.utcnow()
            },
            "$push": {
                "messages": message_doc
            },
            "$inc": {
                f"unread_count.{safe_recipient_email}": 1
            }
        },
        upsert=True
    )
    
    try:
        if hasattr(current_app, 'socketio'):
            payload = {
                'sender_email': user_email,
                'sender_name': sender_name,
                'text': text,
                'timestamp': message_doc['timestamp'].isoformat()
            }
            current_app.socketio.emit('receive_chat_message', payload, room=f"USER_{recipient_email}")
            current_app.socketio.emit('chat_message_sent', {
                'recipient_email': recipient_email,
                'text': text,
                'timestamp': message_doc['timestamp'].isoformat()
            }, room=f"USER_{user_email}")
    except Exception as e:
        print("SocketIO emit failed:", e)
        
    return jsonify({"status": "success", "message": "Message sent successfully"})


@chat_routes.route('/api/staff', methods=['GET'])
def api_get_staff():
    user_email = session.get('user_email')
    if not user_email:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
        
    user_email = user_email.strip().lower()
    users_coll = current_app.users_collection
    staff = list(users_coll.find({"email": {"$ne": user_email}}))
    
    results = []
    for s in staff:
        results.append({
            "email": s.get("email"),
            "name": s.get("name", s.get("email")),
            "rank": s.get("rankOrGrade", s.get("rank", "N/A")),
            "appt": s.get("appt", "N/A"),
            "directorate": s.get("directorate", "N/A")
        })
    return jsonify(results)