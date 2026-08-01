# import_test_users.py
import json
from pymongo import MongoClient

def run_database_injection():
    # Connect directly to your DSM database instance
    client = MongoClient("mongodb://localhost:27017/")
    db = client["DSM"]
    
    # Clear out older testing accounts to maintain clean roster state parameters
    db.users.delete_many({})
    print("Cleaned out old user tracks successfully.")

    # Load file values matrix
    with open('test_users.json', 'r') as file:
        user_list = json.load(file)

    # Insert mock scenario documents upstream
    db.users.insert_many(user_list)
    print(f"Successfully injected {len(user_list)} test profiles across all Roles and Departments!")

if __name__ == "__main__":
    run_database_injection()
