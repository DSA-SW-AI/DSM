# seed.py
import bcrypt
from pymongo import MongoClient

# Connect to your local MongoDB instance
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]
users_collection = db["users"]

from utils.barcode_helper import generate_secure_code

def create_test_user():
    email = "admin@dsa.mil.ng"
    plain_password = "SecurePassword123"

    # Check if user already exists
    if users_collection.find_one({"email": email}):
        print("Test user already exists.")
        return

    # Hash the password for security
    hashed_password = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt())
    sec_code = generate_secure_code()

    # Insert user data
    user_data = {
        "email": email,
        "password": hashed_password,  # Stored as binary data
        "secure_code": sec_code,
        "secureCode": sec_code
    }
    users_collection.insert_one(user_data)
    print(f"Successfully seeded user: {email}")

if __name__ == "__main__":
    create_test_user()
