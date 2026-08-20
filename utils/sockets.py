from flask import request, session as flask_session, current_app
from flask_socketio import emit, join_room
from modules.extensions import socketio
from datetime import datetime
from pymongo import MongoClient
import os


@socketio.on('connect')
def handle_connect():
    print(f"🔌 Socket connect from SID: {request.sid}")

    user_email = flask_session.get("user_email")
    if user_email:
        email_room = f"USER_{user_email.strip().lower()}"
        join_room(email_room)
        print(f"✅ Joined email room: {email_room}")

    service_number = flask_session.get("service_number")
    if service_number:
        safe_sn = service_number.replace("/", "_")
        user_room = f"USER_{safe_sn}"
        join_room(user_room)
        print(f"✅ Joined service room: {user_room}")
    else:
        print("ℹ️ No service_number in session — skipped service room join")

    # Join role‑based rooms so we can broadcast to all users of a certain role
    # (useful if you ever want to notify all SOs, DDs, etc.)
    if flask_session.get("is_so_approver"):
        join_room("ROLE_so")
        print("✅ Joined room: ROLE_so")
    if flask_session.get("is_dd_approver"):
        join_room("ROLE_dd")
        print("✅ Joined room: ROLE_dd")
    if flask_session.get("is_ad_approver"):
        join_room("ROLE_ad")
        print("✅ Joined room: ROLE_ad")
    if flask_session.get("is_final_approver"):
        join_room("ROLE_final_approver")
        print("✅ Joined room: ROLE_final_approver")

    # Also join a directorate‑specific room if needed
    directorate = flask_session.get("directorate")
    if directorate:
        dir_room = f"DIR_{directorate}"
        join_room(dir_room)
        print(f"✅ Joined room: {dir_room}")

    emit('connected', {'status': 'connected', 'room': user_room}, room=request.sid)

@socketio.on('join_rooms')
def handle_join_rooms(data):
    """Client‑side explicit room join (called from socket.js)."""
    service_number = data.get('service_number')
    role = data.get('role')
    directorate = data.get('directorate')

    print(f"📡 join_rooms — service: {service_number}, role: {role}")

    if service_number:
        room = f"USER_{service_number}"
        join_room(room)
        print(f"✅ Joined room: {room}")
        emit('room_joined', {'room': room}, room=request.sid)

    # Optionally join role rooms from the client data
    if role:
        join_room(f"ROLE_{role}")
        print(f"✅ Joined room: ROLE_{role}")
    if directorate:
        dir_room = f"DIR_{directorate}"
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


# Instantiate database access for socket events
db_name = os.getenv("DATABASE_NAME", "DSM")
mongo_client = MongoClient(f"mongodb://localhost:27017/{db_name}")
db = mongo_client[db_name]

@socketio.on('send_chat_message')
def handle_send_chat_message(data):
    sender_email = flask_session.get("user_email")
    if not sender_email:
        print("❌ Socket send_chat_message rejected: No sender session")
        return
        
    sender_email = sender_email.strip().lower()
    sender_name = flask_session.get("name", sender_email)
    recipient_email = data.get("recipient_email", "").strip().lower()
    text = data.get("text", "").strip()
    
    if not recipient_email or not text:
        print("❌ Socket send_chat_message rejected: Missing recipient or text")
        return
        
    now = datetime.now()
    participants = sorted([sender_email, recipient_email])
    safe_recipient = recipient_email.replace('.', '_')


    message_obj = {
        "sender_email": sender_email,
        "sender_name": sender_name,
        "text": text,
        "timestamp": now
    }
    
    chats_coll = current_app.chats_collection
    users_coll = current_app.users_collection

    chats_coll.update_one(
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
    recipient_room = f"USER_{recipient_email}"
    payload = {
        "sender_email": sender_email,
        "sender_name": sender_name,
        "text": text,
        "timestamp": now.isoformat()
    }
    emit("receive_chat_message", payload, room=recipient_room)
    print(f"📡 Chat message routed to: {recipient_room}")
        
    # Confirm sent back to sender room
    sender_room = f"USER_{sender_email}"
    emit("chat_message_sent", {
        "recipient_email": recipient_email,
        "text": text,
        "timestamp": now.isoformat()
    }, room=sender_room)
    print(f"📡 Sent confirmation routed to: {sender_room}")
