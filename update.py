
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
    redirect,
    url_for,
    send_file,
)
from pymongo import MongoClient


# MongoDB Connection Configuration (DSM Database)
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]


# DATABASE COLLECTIONS
users_collection = db["users"]

result = users_collection.update_many(
    {"is_final_approver":"false"},
    {"$set": {"is_final_approver": False}}
)

print(f"Updated {result.modified_count} documents where 'is_final_approver' was 'false' to False.")