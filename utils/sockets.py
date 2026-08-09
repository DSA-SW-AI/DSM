# sockets.py — single source of truth for ALL socket event handlers
# Import this in your app factory (create_app) AFTER socketio.init_app(app)
# e.g.  from sockets import *   OR   import sockets  (just needs to be imported)
#
# REMOVE all @socketio.on('connect'), @socketio.on('join_rooms'), @socketio.on('join')
# from parade_state.py and approver_dashboard.py — they now live here only.

from flask import request, session as flask_session
from flask_socketio import emit, join_room
from modules.extensions import socketio


@socketio.on('connect')
def handle_connect():
    print(f"🔌 Socket connect from SID: {request.sid}")

    service_number = flask_session.get("service_number")
    if not service_number:
        print("❌ No service_number in session — socket connected but no room joined")
        return

    directorate = flask_session.get("directorate")
    roles = flask_session.get("roles", [])

    safe_sn = service_number.replace("/", "_")
    user_room = f"USER_{safe_sn}"
    join_room(user_room)
    print(f"✅ Joined room: {user_room}")

    for role in roles:
        if role == "so1_doa":
            join_room(f"ROLE_{role}")
            print(f"✅ Joined room: ROLE_{role}")
        elif directorate:
            dir_room = f"DIR_{directorate}_{role}"
            join_room(dir_room)
            print(f"✅ Joined room: {dir_room}")

    emit('connected', {'status': 'connected', 'room': user_room}, room=request.sid)


@socketio.on('join_rooms')
def handle_join_rooms(data):
    """Client-side explicit room join (called on socket connect in socket.js)."""
    service_number = data.get('service_number')
    roles = data.get('roles', [])
    directorate = data.get('directorate')

    print(f"📡 join_rooms — service: {service_number}, roles: {roles}")

    if service_number:
        room = f"USER_{service_number}"
        join_room(room)
        print(f"✅ Joined room: {room}")
        emit('room_joined', {'room': room}, room=request.sid)

    for role in roles:
        if role == "so1_doa":
            join_room(f"ROLE_{role}")
        elif directorate:
            dir_room = f"DIR_{directorate}_{role}"
            join_room(dir_room)
            print(f"✅ Joined room: {dir_room}")


@socketio.on('join')
def handle_join(data):
    """Fallback manual room join."""
    room = data.get('room')
    if room:
        join_room(room)
        print(f"✅ Manual join: {room}")
        emit('room_joined', {'room': room}, room=request.sid)

# ── Real-Time Messaging event handlers ──────────────────────────────────────
from datetime import datetime
from pymongo import MongoClient
import os

# Instantiate database access for socket events
db_name = os.getenv("DATABASE_NAME", "DSM")
mongo_client = MongoClient(f"mongodb://localhost:27017/{db_name}")
db = mongo_client[db_name]

@socketio.on('send_chat_message')
def handle_send_chat_message(data):
    sender_email = flask_session.get("email")
    sender_name = flask_session.get("name", sender_email)
    
    if not sender_email:
        print("❌ Socket send_chat_message rejected: No sender session")
        return
        
    recipient_email = data.get("recipient_email")
    text = data.get("text", "").strip()
    
    if not recipient_email or not text:
        print("❌ Socket send_chat_message rejected: Missing recipient or text")
        return
        
    now = datetime.now()
    participants = sorted([sender_email, recipient_email])
    
    message_obj = {
        "sender_email": sender_email,
        "sender_name": sender_name,
        "text": text,
        "timestamp": now
    }
    
    safe_recipient = recipient_email.replace('.', '_')
    db.chats.update_one(
        {"participants": participants},
        {
            "$push": {"messages": message_obj},
            "$set": {
                "last_message": text,
                "updated_at": now
            },
            "$inc": {f"unread_count.{safe_recipient}": 1}
        },
        upsert=True
    )
    
    # Broadcast to recipient room
    recipient_user = db.users.find_one({"email": recipient_email})
    if recipient_user and recipient_user.get("service_number"):
        recipient_safe_sn = recipient_user["service_number"].replace("/", "_")
        
        payload = {
            "sender_email": sender_email,
            "sender_name": sender_name,
            "text": text,
            "timestamp": now.isoformat()
        }
        emit("receive_chat_message", payload, room=f"USER_{recipient_safe_sn}")
        print(f"📡 Chat message routed to USER_{recipient_safe_sn}")
        
    # Confirm sent back to sender room
    sender_user = db.users.find_one({"email": sender_email})
    if sender_user and sender_user.get("service_number"):
        sender_safe_sn = sender_user["service_number"].replace("/", "_")
        emit("chat_message_sent", {
            "recipient_email": recipient_email,
            "text": text,
            "timestamp": now.isoformat()
        }, room=f"USER_{sender_safe_sn}")
