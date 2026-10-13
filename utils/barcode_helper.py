import uuid

def generate_secure_code(user_doc=None):
    """
    Generates a unique secureCode tied to a single user.
    Format: SEC-XXXX-XXXX-XXXX
    """
    raw_uuid = uuid.uuid4().hex.upper()
    sec_code = f"SEC-{raw_uuid[:4]}-{raw_uuid[4:8]}-{raw_uuid[8:12]}"
    return sec_code

def ensure_user_secure_code(user_doc, users_coll):
    """
    Ensures that a user document has a secureCode/secure_code set.
    If missing, generates one and updates MongoDB.
    """
    if not user_doc:
        return user_doc

    code = user_doc.get("secure_code") or user_doc.get("secureCode")
    if not code:
        code = generate_secure_code(user_doc)
        users_coll.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"secure_code": code, "secureCode": code}}
        )
        user_doc["secure_code"] = code
        user_doc["secureCode"] = code
    elif "secure_code" not in user_doc or "secureCode" not in user_doc:
        users_coll.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"secure_code": code, "secureCode": code}}
        )
        user_doc["secure_code"] = code
        user_doc["secureCode"] = code

    return user_doc

def backfill_all_users_secure_code(users_coll):
    """
    Scans the database and ensures ALL existing (old) users have a secureCode/secure_code.
    """
    try:
        cursor = users_coll.find({
            "$or": [
                {"secure_code": {"$exists": False}},
                {"secure_code": None},
                {"secure_code": ""},
                {"secureCode": {"$exists": False}},
                {"secureCode": None},
                {"secureCode": ""}
            ]
        })
        count = 0
        for user in cursor:
            code = user.get("secure_code") or user.get("secureCode") or generate_secure_code(user)
            users_coll.update_one(
                {"_id": user["_id"]},
                {"$set": {"secure_code": code, "secureCode": code}}
            )
            count += 1
        if count > 0:
            print(f"[BARCODE HELPER] Successfully updated {count} users with automatic secureCode.")
    except Exception as e:
        print(f"[BARCODE HELPER] Error backfilling secure_code for users: {e}")
