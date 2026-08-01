# seed.py
import bcrypt
from pymongo import MongoClient

# Connect to your local MongoDB instance
client = MongoClient("mongodb://localhost:27017/")
db = client["DSM"]
users_collection = db["users"]

def create_test_user():
    email = "admin@dsa.mil.ng"
    plain_password = "SecurePassword123"

    # Check if user already exists
    if users_collection.find_one({"email": email}):
        print("Test user already exists.")
        return

    # Hash the password for security
    hashed_password = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt())

    # Insert user data
    user_data = {
        "email": email,
        "password": hashed_password  # Stored as binary data
    }
    users_collection.insert_one(user_data)
    print(f"Successfully seeded user: {email}")

if __name__ == "__main__":
    create_test_user()
