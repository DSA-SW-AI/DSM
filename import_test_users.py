import json
from werkzeug.security import generate_password_hash
from pymongo import MongoClient
from datetime import datetime


def hash_password(password: str) -> str:
    """
    Hash a password using scrypt with optimized parameters.
    
    Scrypt format: scrypt:N:r:p$salt$hash
    - N=32768 (2^15): Memory cost - higher is more secure but slower
    - r=8: Block size - standard value
    - p=1: Parallelization - standard value
    """
    return generate_password_hash(
        password,
        method='scrypt',
        salt_length=16
    )


def run_database_injection():
    """Inject test users into MongoDB with scrypt-hashed passwords"""
    
    # Connect directly to your DSM database instance
    client = MongoClient("mongodb://localhost:27017/")
    db = client["DSM"]
    
    # Clear out older testing accounts to maintain clean roster state parameters
    db.users.delete_many({})
    print("Cleaned out old user tracks successfully.")

    # Load file values matrix
    with open('test_users.json', 'r') as file:
        user_list = json.load(file)

    # Hash passwords before inserting
    for user in user_list:
        if 'password_hash' in user:
            # Hash the plain text password using scrypt
            user['password_hash'] = hash_password(user['password_hash'])
        
        # Add timestamp if empty
        if not user.get('createdAt'):
            user['createdAt'] = datetime.utcnow().isoformat()

    # Insert mock scenario documents upstream
    db.users.insert_many(user_list)
    print(f"Successfully injected {len(user_list)} test profiles with scrypt hashing!")
    
    # Verify injection
    sample_user = db.users.find_one({})
    if sample_user:
        print(f"\nSample hash format: {sample_user['password_hash'][:50]}...")
        print(f"Hash format confirmed: scrypt")

if __name__ == "__main__":
    run_database_injection()