# # initialize_all_balances.py - UPDATED VERSION
# from pymongo import MongoClient
# from datetime import datetime
# import sys

# def initialize_all_staff_balances(delete_existing: bool = False, year: int = None):
#     """
#     Create default TACOS balances for ALL staff in the system.
    
#     Args:
#         delete_existing: If True, delete all balances and start fresh
#         year: Year to initialize (defaults to current year)
#     """
#     import os
#     client = MongoClient("mongodb://localhost:27017/")
#     db_name = os.getenv("DATABASE_NAME", "DSM")
#     db = client[db_name]
    
#     if year is None:
#         year = datetime.now().year
    
#     print("="*60)
#     print(f"TACOS LEAVE BALANCES INITIALIZATION - YEAR {year}")
#     print("="*60)
    
#     # ──────────────────────────────────────────────────────────────
#     # OPTION: DELETE EXISTING BALANCES
#     # ──────────────────────────────────────────────────────────────
#     if delete_existing:
#         print(f"\n⚠️  DELETING all existing leave balances for {year}...")
#         deleted_count = db.leave_balances.delete_many({"year": year}).deleted_count
#         print(f"✓ Deleted {deleted_count} existing balance records")
#     else:
#         print(f"\n✓ Keeping existing balances (adding missing fields)")
    
#     # ──────────────────────────────────────────────────────────────
#     # GET ALL STAFF (from users collection)
#     # EXCLUDES: Approval-only accounts (is_approval_role == True)
#     # ──────────────────────────────────────────────────────────────
#     all_staff = list(db.users.find({
#         "is_active": True,
#         "$or": [
#             {"is_approval_role": {"$ne": True}},
#             {"is_approval_role": {"$exists": False}}
#         ]
#     }))

#     if not all_staff:
#         all_staff = list(db.users.find({
#             "isActive": True,
#             "$or": [
#                 {"is_approval_role": {"$ne": True}},
#                 {"is_approval_role": {"$exists": False}}
#             ]                           
#     }))
    
#     if not all_staff:
#         print("\n✗ ERROR: No active users found in 'users' collection!")
#         client.close()
#         return
    
#     print(f"\nFound {len(all_staff)} active users")
    
#     # ──────────────────────────────────────────────────────────────
#     # MILITARY RANK TO GRADE MAPPING (IMPORTANT!)
#     # ──────────────────────────────────────────────────────────────
#     MILITARY_RANK_TO_GRADE = {
#         # Army ranks
#         "Maj Gen": 15, "Major General": 15,
#         "Brig Gen": 14, "Brigadier General": 14,
#         "Col": 13, "Colonel": 13,
#         "Lt Col": 12, "Lieutenant Colonel": 12,
#         "Maj": 11, "Major": 11,
#         "Capt": 10, "Captain": 10,
#         "Lt": 9, "Lieutenant": 9,
#         "2nd Lt": 8, "Second Lieutenant": 8,
        
#         # Navy ranks
#         "Rear Admiral": 15,
#         "Commodore": 14,
#         "Captain": 13,  # Navy Captain
#         "Commander": 12,
#         "Lt Commander": 11,
#         "Lieutenant": 10,  # Navy Lieutenant
#         "Sub Lieutenant": 9,
        
#         # Air Force ranks
#         "Air Vice Marshal": 15,
#         "Air Commodore": 14,
#         "Group Captain": 13,
#         "Wing Commander": 12,
#         "Squadron Leader": 11,
#         "Flight Lieutenant": 10,
#         "Flying Officer": 9,
#         "Pilot Officer": 8,
        
#         # NCOs and other ranks
#         "WO": 7, "Warrant Officer": 7,
#         "SSgt": 6, "Staff Sergeant": 6,
#         "Sgt": 5, "Sergeant": 5,
#         "Cpl": 4, "Corporal": 4,
#         "LCpl": 3, "Lance Corporal": 3,
#         "Pte": 2, "Private": 2,
#         "Recruit": 1
#     }
    
#     def extract_grade(staff_doc):
#         """Extract CONDSASS grade from staff document."""
#         rank_or_grade = staff_doc.get('rankOrGrade', '').strip() or staff_doc.get("onboarding_data", {}).get("step_1", {}).get("rankOrGrade"),
        
#         # 1. Check for civilian "Grade Level X"
#         if 'Grade Level' in rank_or_grade:
#             try:
#                 grade_str = rank_or_grade.split('Grade Level')[-1].strip()
#                 return int(grade_str)
#             except:
#                 pass
        
#         # 2. Check military rank mapping
#         for rank, grade in MILITARY_RANK_TO_GRADE.items():
#             if rank.lower() in rank_or_grade.lower():
#                 return grade
        
#         # 3. Try to extract any number
#         try:
#             import re
#             numbers = re.findall(r'\d+', rank_or_grade)
#             if numbers:
#                 return int(numbers[0])
#         except:
#             pass
        
#         # 4. Default based on service number prefix
#         service_number = staff_doc.get('serviceNumber') or staff_doc.get('service_number', '')
#         if isinstance(service_number, str):  # Make sure it's a string
#             if service_number.startswith(('DSA/CIV/')):
#                 return 8  # Default civilian grade
#             elif service_number.startswith(('NA/', 'NN/', 'NAF/')):
#                 return 10  # Default military grade
        
#         return 0  # Unknown
    
#     # ──────────────────────────────────────────────────────────────
#     # INITIALIZE BALANCES
#     # ──────────────────────────────────────────────────────────────
#     initialized = 0
#     updated = 0
#     errors = 0
#     skipped_approval_accounts = 0
    
#     print(f"\nInitializing balances for year {year}...")
#     print("-" * 60)
    
#     for staff in all_staff:

#         # Double-check: Skip if this is an approval-only account
#         if staff.get('is_approval_role') == True or staff.get('is_approval_role') == 'true':
#             skipped_approval_accounts += 1
#             continue


#         service_number = (
#             staff.get('serviceNumber') or 
#             staff.get('service_number')
#         )
#         if not service_number:
#             service_number = str(staff.get('_id'))
        
#         if not service_number:
#             print(f"  ⚠️  Skipping staff without _id: {staff.get('fullName', 'Unknown')}")
#             errors += 1
#             continue
        
#         # Extract grade
#         grade = extract_grade(staff)
        
#         # Determine annual entitlement
#         if 2 <= grade <= 6:
#             annual_entitlement = 21
#         elif 7 <= grade <= 15:
#             annual_entitlement = 30
#         else:
#             annual_entitlement = 21  # Default
        
#         # Check if balance already exists
#         existing_balance = db.leave_balances.find_one({
#             "serviceNumber": service_number,
#             "year": year
#         })
        
#         # Create complete TACOS balance document
#         # NOTE: Using 'casualCalendarDays' instead of 'casualCalendarDaysUsed/Remaining' 
#         # to match schema validation requirements
#         balance_doc = {
#             "serviceNumber": service_number,
#             "fullName": staff.get('name') or staff.get('fullName', ''),
#             "directorate": staff.get('directorate', ''),
#             "grade": grade,
#             "year": year,
#             "isActive": True,
            
#             # Annual Leave (08.02)
#             "annualEntitlement": annual_entitlement,
#             "annualRemaining": annual_entitlement,
            
#             # Compassionate Leave (08.03a)
#             "compassionateUsed": 0,  # Required by schema
#             "compassionateRemaining": 10,
            
#             # Casual Leave/Pass (08.03b) - FIXED: Using 'casualCalendarDays' to match schema
#             "casualCalendarDays": 7,  # Changed to match schema requirement
#             "casualCalendarDaysUsed": 0,  # Additional field for tracking
#             "casualCalendarDaysRemaining": 7,  # Additional field for tracking
            
#             # Sick Leave (08.04)
#             "sickThisYear": 0,  # Required by schema
#             "sickRolling12m": 0,  # Required by schema
#             "sickThisYearRemaining": 21,
#             "sickRollingRemaining": 42,
            
#             # Maternity Leave (08.06, 08.07)
#             "maternityUsed": False,
#             "maternityAvailable": True,
#             "maternityStartDate": None,
            
#             # Paternity Leave (08.08)
#             "paternityUsed": False,
#             "paternityAvailable": True,
#             "paternityDaysUsed": 0,
            
#             # Disembarkation Leave (08.05)
#             "disembarkationAvailable": True,
#             "disembarkationDaysUsed": 0,
            
#             # Terminal Leave (08.11) - FIXED: Added required field
#             "terminalGranted": False,  # Required by schema
            
#             # Hospitalization (08.04c, 08.04e)
#             "hospitalizationDaysUsed": 0,
#             "firstHospitalizationUsed": False,
            
#             # International travel permission
#             "hasInternationalPermission": staff.get('hasInternationalPermission', False),
            
#             # Audit
#             "createdAt": datetime.utcnow(),
#             "updatedAt": datetime.utcnow(),
#             "notes": ["Initialized with TACOS defaults"]
#         }

#         # Gender-based adjustments
#         gender = staff.get("onboarding_data", {}).get("step_5", {}).get("gender", "").lower().strip()
        
#         if gender == 'female':
#             balance_doc["maternityAvailable"] = True
#             balance_doc["maternityUsed"] = False
#             balance_doc["paternityAvailable"] = False
#             balance_doc["paternityUsed"] = True  # Mark as used since not applicable
#         elif gender == 'male':
#             balance_doc["maternityAvailable"] = False
#             balance_doc["maternityUsed"] = True  # Mark as used since not applicable
#             balance_doc["paternityAvailable"] = True
#             balance_doc["paternityUsed"] = False
        
#         if existing_balance:
#             # Update existing balance with missing TACOS fields
#             update_data = {}
            
#             # Only add fields that don't exist
#             for field, value in balance_doc.items():
#                 if field not in existing_balance:
#                     update_data[field] = value
            
#             # Ensure required fields exist
#             required_fields = [
#                                 'casualCalendarDays', 'compassionateUsed', 
#                                 'sickThisYear', 'sickRolling12m', 'terminalGranted',
#                                 'maternityUsed', 'paternityUsed', 'disembarkationDaysUsed'  # Added these
#                             ]
#             for field in required_fields:
#                 if field not in existing_balance:
#                     update_data[field] = balance_doc[field]
            
#             if update_data:
#                 update_data['updatedAt'] = datetime.utcnow()
                
#                 # Handle notes properly
#                 notes = existing_balance.get('notes', [])
#                 if isinstance(notes, list):
#                     notes.append("Updated with TACOS defaults")
#                 else:
#                     notes = ["Updated with TACOS defaults"]
#                 update_data['notes'] = notes
                
#                 db.leave_balances.update_one(
#                     {"_id": existing_balance['_id']},
#                     {"$set": update_data}
#                 )
#                 updated += 1
#                 print(f"  ↻ Updated {service_number} ({staff.get('name')}) - Grade: {grade}")
#         else:
#             # Insert new balance
#             balance_doc['notes'] = ["Initialized with TACOS defaults"]
#             try:
#                 db.leave_balances.insert_one(balance_doc)
#                 initialized += 1
#                 print(f"  ✓ Created {service_number} ({staff.get('name')}) - Grade: {grade}, Annual: {annual_entitlement} days")
#             except Exception as e:
#                 print(f"  ❌ Failed to insert for {service_number}: {e}")
#                 errors += 1
    
#     # ──────────────────────────────────────────────────────────────
#     # SUMMARY
#     # ──────────────────────────────────────────────────────────────
#     print("\n" + "="*60)
#     print("INITIALIZATION COMPLETE - SUMMARY")
#     print("VALIDATION REPORT")
#     print("="*60)
    
#     total_balances = db.leave_balances.count_documents({"year": year})
    
#     print(f"\nYear: {year}")
#     print(f"Total staff processed: {len(all_staff)}")
#     print(f"Approval-only accounts skipped: {skipped_approval_accounts}")
#     print(f"New balances created: {initialized}")
#     print(f"Existing balances updated: {updated}")
#     print(f"Errors: {errors}")
#     print(f"Total balances in database: {total_balances}")
    
#     # Show sample of balances
#     print(f"\nSample of initialized balances:")
#     sample = db.leave_balances.find({"year": year}).limit(5)
#     for balance in sample:
#         print(f"  • {balance['serviceNumber']}: {balance['fullName']}")
#         print(f"    Grade: {balance['grade']}, Annual: {balance['annualRemaining']}/{balance['annualEntitlement']} days")
#         print(f"    Casual: {balance.get('casualCalendarDays', 'N/A')} days")
    
#     print("\n" + "="*60)
#     print("VERIFICATION QUERIES (run in MongoDB shell):")
#     print("="*60)
#     print(f"1. Check all balances for {year}:")
#     print(f'   db.leave_balances.find({{year: {year}}}).count()')
#     print(f"\n2. Check by directorate:")
#     print(f'   db.leave_balances.aggregate(['
#           f'     {{ $match: {{ year: {year} }} }},'
#           f'     {{ $group: {{ _id: "$directorate", count: {{ $sum: 1 }} }} }}'
#           f'   ])')
#     print(f"\n3. Check military vs civilian:")
#     print(f'   // Military prefixes: NA/, NN/, NAF/')
#     print(f'   db.leave_balances.find({{'
#           f'     year: {year},'
#           f'     serviceNumber: /^(NA\\/|NN\\/|NAF\\/)/'
#           f'   }}).count()')
    
#     client.close()
#     return True

# if __name__ == "__main__":
#     print("TACOS LEAVE BALANCES INITIALIZATION SYSTEM")
#     print("="*60)
    
#     # Ask user what they want to do
#     print("\nOPTIONS:")
#     print("1. DELETE all existing balances and start fresh")
#     print("2. KEEP existing balances (add missing TACOS fields)")
#     print("3. Initialize for specific year")
    
#     try:
#         choice = input("\nSelect option (1, 2, or 3): ").strip()
        
#         delete_existing = False
#         year = datetime.now().year
        
#         if choice == "1":
#             delete_existing = True
#             confirm = input(f"⚠️  WARNING: This will DELETE ALL balances for {year}. Continue? (y/n): ")
#             if confirm.lower() != 'y':
#                 print("Cancelled.")
#                 sys.exit(0)
#             print(f"\nStarting FRESH initialization for {year}...")
            
#         elif choice == "2":
#             delete_existing = False
#             print(f"\nStarting UPDATE initialization for {year}...")
            
#         elif choice == "3":
#             try:
#                 year = int(input("Enter year to initialize (e.g., 2024): "))
#                 delete_choice = input("Delete existing balances for this year? (y/n): ")
#                 delete_existing = (delete_choice.lower() == 'y')
#                 print(f"\nStarting initialization for {year}...")
#             except ValueError:
#                 print("Invalid year. Using current year.")
#                 year = datetime.now().year
        
#         # Run initialization
#         success = initialize_all_staff_balances(
#             delete_existing=delete_existing,
#             year=year
#         )
        
#         if success:
#             print(f"\n✅ INITIALIZATION SUCCESSFUL!")
#             print(f"\nNext steps:")
#             print("1. Test by applying for leave as a staff member")
#             print("2. Verify balances are being used correctly")
#             print("3. Run approval process to test deduction")
#         else:
#             print("\n❌ INITIALIZATION FAILED!")
            
#     except KeyboardInterrupt:
#         print("\n\nCancelled by user.")
#     except Exception as e:
#         print(f"\n❌ ERROR: {e}")
#         import traceback
#         traceback.print_exc()



# initialize_all_balances.py - UPDATED VERSION
from pymongo import MongoClient
from datetime import datetime
import sys

def initialize_all_staff_balances(delete_existing: bool = False, year: int = None):
    """
    Create default TACOS balances for ALL staff in the system.
    
    EXCLUDES: Approval-only accounts (is_approval_role == True)
    
    Args:
        delete_existing: If True, delete all balances and start fresh
        year: Year to initialize (defaults to current year)
    """
    import os
    client = MongoClient("mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "DSM")
    db = client[db_name]
    
    if year is None:
        year = datetime.now().year
    
    print("="*60)
    print(f"TACOS LEAVE BALANCES INITIALIZATION - YEAR {year}")
    print("="*60)
    
    # ──────────────────────────────────────────────────────────────
    # OPTION: DELETE EXISTING BALANCES
    # ──────────────────────────────────────────────────────────────
    if delete_existing:
        print(f"\n⚠️  DELETING all existing leave balances for {year}...")
        deleted_count = db.leave_balances.delete_many({"year": year}).deleted_count
        print(f"✓ Deleted {deleted_count} existing balance records")
    else:
        print(f"\n✓ Keeping existing balances (adding missing fields)")
    
    # ──────────────────────────────────────────────────────────────
    # GET ALL STAFF (from users collection)
    # EXCLUDES: Approval-only accounts (is_approval_role == True)
    # ──────────────────────────────────────────────────────────────
    all_staff = list(db.users.find({
        "is_active": True,
        "$or": [
            {"is_approval_role": {"$ne": True}},
            {"is_approval_role": {"$exists": False}}
        ]
    }))
    if not all_staff:
        all_staff = list(db.users.find({
            "isActive": True,
            "$or": [
                {"is_approval_role": {"$ne": True}},
                {"is_approval_role": {"$exists": False}}
            ]
        }))
    
    if not all_staff:
        print("\n✗ ERROR: No active users found in 'users' collection!")
        client.close()
        return
    
    print(f"\nFound {len(all_staff)} active users (excluding approval-only accounts)")
    
    # ──────────────────────────────────────────────────────────────
    # MILITARY RANK TO GRADE MAPPING (IMPORTANT!)
    # ──────────────────────────────────────────────────────────────
    MILITARY_RANK_TO_GRADE = {
        # Army ranks
        "Maj Gen": 15, "Major General": 15,
        "Brig Gen": 14, "Brigadier General": 14,
        "Col": 13, "Colonel": 13,
        "Lt Col": 12, "Lieutenant Colonel": 12,
        "Maj": 11, "Major": 11,
        "Capt": 10, "Captain": 10,
        "Lt": 9, "Lieutenant": 9,
        "2Lt": 8, "Second Lieutenant": 8,
        
        # Navy ranks
        "Rear Admiral": 15,
        "Commodore": 14,
        "Captain": 13,  # Navy Captain
        "Commander": 12,
        "Lt Commander": 11,
        "Lieutenant": 10,  # Navy Lieutenant
        "Sub Lieutenant": 9,
        
        # Air Force ranks
        "Air Vice Marshal": 15,
        "Air Commodore": 14,
        "Group Captain": 13,
        "Wing Commander": 12,
        "Squadron Leader": 11,
        "Flight Lieutenant": 10,
        "Flying Officer": 9,
        "Pilot Officer": 8,
        
        # NCOs and other ranks
        "WO": 7, "Warrant Officer": 7,
        "SSgt": 6, "Staff Sergeant": 6,
        "Sgt": 5, "Sergeant": 5,
        "Cpl": 4, "Corporal": 4,
        "LCpl": 3, "Lance Corporal": 3,
        "Pte": 2, "Private": 2,
        "Recruit": 1
    }
    
    def extract_grade(staff_doc):
        """Extract CONDSASS grade from staff document."""
        rank_or_grade = staff_doc.get('rankOrGrade', '').strip()
        
        # 1. Check for civilian "Grade Level X"
        if 'Grade Level' in rank_or_grade:
            try:
                grade_str = rank_or_grade.split('Grade Level')[-1].strip()
                return int(grade_str)
            except:
                pass
        
        # 2. Check military rank mapping
        for rank, grade in MILITARY_RANK_TO_GRADE.items():
            if rank.lower() in rank_or_grade.lower():
                return grade
        
        # 3. Try to extract any number
        try:
            import re
            numbers = re.findall(r'\d+', rank_or_grade)
            if numbers:
                return int(numbers[0])
        except:
            pass
        
        # 4. Default based on service number prefix
        service_number = staff_doc.get('serviceNumber') or staff_doc.get('service_number', '')
        if isinstance(service_number, str):  # Make sure it's a string
            if service_number.startswith(('DSA/CIV/')):
                return 8  # Default civilian grade
            elif service_number.startswith(('NA/', 'NN/', 'NAF/')):
                return 10  # Default military grade
        
        return 0  # Unknown
    
    # ──────────────────────────────────────────────────────────────
    # INITIALIZE BALANCES
    # ──────────────────────────────────────────────────────────────
    initialized = 0
    updated = 0
    errors = 0
    skipped_approval_accounts = 0
    
    print(f"\nInitializing balances for year {year}...")
    print("-" * 60)
    
    for staff in all_staff:
        # Double-check: Skip if this is an approval-only account
        if staff.get('is_approval_role') == True or staff.get('is_approval_role') == 'true':
            skipped_approval_accounts += 1
            continue
        
        service_number = (
            staff.get('serviceNumber') or 
            staff.get('service_number')
        )
        if not service_number:
            service_number = str(staff.get('_id'))
        
        if not service_number:
            print(f"  ⚠️  Skipping staff without _id: {staff.get('fullName', 'Unknown')}")
            errors += 1
            continue
        
        # Extract grade
        grade = extract_grade(staff)
        
        # Determine annual entitlement
        if 2 <= grade <= 6:
            annual_entitlement = 21
        elif 7 <= grade <= 15:
            annual_entitlement = 30
        else:
            annual_entitlement = 21  # Default
        
        # Check if balance already exists
        existing_balance = db.leave_balances.find_one({
            "serviceNumber": service_number,
            "year": year
        })
        
        # Create complete TACOS balance document
        # NOTE: Using 'casualCalendarDays' instead of 'casualCalendarDaysUsed/Remaining' 
        # to match schema validation requirements
        balance_doc = {
            "serviceNumber": service_number,
            "fullName": staff.get('name') or staff.get('fullName', ''),
            "directorate": staff.get('directorate', ''),
            "grade": grade,
            "year": year,
            "isActive": True,
            
            # Annual Leave (08.02)
            "annualEntitlement": annual_entitlement,
            "annualRemaining": annual_entitlement,
            
            # Compassionate Leave (08.03a)
            "compassionateUsed": 0,  # Required by schema
            "compassionateRemaining": 10,
            
            # Casual Leave/Pass (08.03b) - FIXED: Using 'casualCalendarDays' to match schema
            "casualCalendarDays": 7,  # Changed to match schema requirement
            "casualCalendarDaysUsed": 0,  # Additional field for tracking
            "casualCalendarDaysRemaining": 7,  # Additional field for tracking
            
            # Sick Leave (08.04)
            "sickThisYear": 0,  # Required by schema
            "sickRolling12m": 0,  # Required by schema
            "sickThisYearRemaining": 21,
            "sickRollingRemaining": 42,
            
            # Maternity Leave (08.06, 08.07)
            "maternityUsed": False,
            "maternityAvailable": True,
            "maternityStartDate": None,
            
            # Paternity Leave (08.08)
            "paternityUsed": False,
            "paternityAvailable": True,
            "paternityDaysUsed": 0,
            
            # Disembarkation Leave (08.05)
            "disembarkationAvailable": True,
            "disembarkationDaysUsed": 0,
            
            # Terminal Leave (08.11) - FIXED: Added required field
            "terminalGranted": False,  # Required by schema
            
            # Hospitalization (08.04c, 08.04e)
            "hospitalizationDaysUsed": 0,
            "firstHospitalizationUsed": False,
            
            # International travel permission
            "hasInternationalPermission": staff.get('hasInternationalPermission', False),
            
            # Audit
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
            "notes": ["Initialized with TACOS defaults"]
        }

        # Gender-based adjustments
        # gender = staff.get("onboarding_data", {}).get("step_5", {}).get("gender", "").lower()


        # OLD (fails when values are None):
        gender = staff.get("onboarding_data", {}).get("step_5", {}).get("gender", "").lower().strip()

        # NEW (handles None values):
        # onboarding_data = staff.get("onboarding_data") or {}  # Convert None to {}
        # step_5 = onboarding_data.get("step_5") or {}          # Convert None to {}
        # gender = step_5.get("gender", "").lower().strip()

        
        if gender == 'female':
            balance_doc["maternityAvailable"] = True
            balance_doc["maternityUsed"] = False
            balance_doc["paternityAvailable"] = False
            balance_doc["paternityUsed"] = True  # Mark as used since not applicable
        elif gender == 'male':
            balance_doc["maternityAvailable"] = False
            balance_doc["maternityUsed"] = True  # Mark as used since not applicable
            balance_doc["paternityAvailable"] = True
            balance_doc["paternityUsed"] = False
        
        if existing_balance:
            # Update existing balance with missing TACOS fields
            update_data = {}
            
            # Only add fields that don't exist
            for field, value in balance_doc.items():
                if field not in existing_balance:
                    update_data[field] = value
            
            # Ensure required fields exist
            required_fields = [
                                'casualCalendarDays', 'compassionateUsed', 
                                'sickThisYear', 'sickRolling12m', 'terminalGranted',
                                'maternityUsed', 'paternityUsed', 'disembarkationDaysUsed'
                            ]
            for field in required_fields:
                if field not in existing_balance:
                    update_data[field] = balance_doc[field]
            
            if update_data:
                update_data['updatedAt'] = datetime.utcnow()
                
                # Handle notes properly
                notes = existing_balance.get('notes', [])
                if isinstance(notes, list):
                    notes.append("Updated with TACOS defaults")
                else:
                    notes = ["Updated with TACOS defaults"]
                update_data['notes'] = notes
                
                db.leave_balances.update_one(
                    {"_id": existing_balance['_id']},
                    {"$set": update_data}
                )
                updated += 1
                print(f"  ↻ Updated {service_number} ({staff.get('name')}) - Grade: {grade}")
        else:
            # Insert new balance
            balance_doc['notes'] = ["Initialized with TACOS defaults"]
            try:
                db.leave_balances.insert_one(balance_doc)
                initialized += 1
                print(f"  ✓ Created {service_number} ({staff.get('name')}) - Grade: {grade}, Annual: {annual_entitlement} days")
            except Exception as e:
                print(f"  ❌ Failed to insert for {service_number}: {e}")
                errors += 1
    
    # ──────────────────────────────────────────────────────────────
    # SUMMARY
    # ──────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("INITIALIZATION COMPLETE - SUMMARY")
    print("VALIDATION REPORT")
    print("="*60)
    
    total_balances = db.leave_balances.count_documents({"year": year})
    
    print(f"\nYear: {year}")
    print(f"Total active users found: {len(all_staff)}")
    print(f"Approval-only accounts skipped: {skipped_approval_accounts}")
    print(f"New balances created: {initialized}")
    print(f"Existing balances updated: {updated}")
    print(f"Errors: {errors}")
    print(f"Total balances in database: {total_balances}")
    
    # Show sample of balances
    print(f"\nSample of initialized balances:")
    sample = db.leave_balances.find({"year": year}).limit(5)
    for balance in sample:
        print(f"  • {balance['serviceNumber']}: {balance['fullName']}")
        print(f"    Grade: {balance['grade']}, Annual: {balance['annualRemaining']}/{balance['annualEntitlement']} days")
        print(f"    Casual: {balance.get('casualCalendarDays', 'N/A')} days")
    
    print("\n" + "="*60)
    print("VERIFICATION QUERIES (run in MongoDB shell):")
    print("="*60)
    print(f"1. Check all balances for {year}:")
    print(f'   db.leave_balances.find({{year: {year}}}).count()')
    print(f"\n2. Check by directorate:")
    print(f'   db.leave_balances.aggregate(['
          f'     {{ $match: {{ year: {year} }} }},'
          f'     {{ $group: {{ _id: "$directorate", count: {{ $sum: 1 }} }} }}'
          f'   ])')
    print(f"\n3. Check military vs civilian:")
    print(f'   // Military prefixes: NA/, NN/, NAF/')
    print(f'   db.leave_balances.find({{'
          f'     year: {year},'
          f'     serviceNumber: /^(NA\\/|NN\\/|NAF\\/)/'
          f'   }}).count()')
    
    client.close()
    return True

if __name__ == "__main__":
    print("TACOS LEAVE BALANCES INITIALIZATION SYSTEM")
    print("="*60)
    
    # Ask user what they want to do
    print("\nOPTIONS:")
    print("1. DELETE all existing balances and start fresh")
    print("2. KEEP existing balances (add missing TACOS fields)")
    print("3. Initialize for specific year")
    
    try:
        choice = input("\nSelect option (1, 2, or 3): ").strip()
        
        delete_existing = False
        year = datetime.now().year
        
        if choice == "1":
            delete_existing = True
            confirm = input(f"⚠️  WARNING: This will DELETE ALL balances for {year}. Continue? (y/n): ")
            if confirm.lower() != 'y':
                print("Cancelled.")
                sys.exit(0)
            print(f"\nStarting FRESH initialization for {year}...")
            
        elif choice == "2":
            delete_existing = False
            print(f"\nStarting UPDATE initialization for {year}...")
            
        elif choice == "3":
            try:
                year = int(input("Enter year to initialize (e.g., 2024): "))
                delete_choice = input("Delete existing balances for this year? (y/n): ")
                delete_existing = (delete_choice.lower() == 'y')
                print(f"\nStarting initialization for {year}...")
            except ValueError:
                print("Invalid year. Using current year.")
                year = datetime.now().year
        
        # Run initialization
        success = initialize_all_staff_balances(
            delete_existing=delete_existing,
            year=year
        )
        
        if success:
            print(f"\n✅ INITIALIZATION SUCCESSFUL!")
            print(f"\nNext steps:")
            print("1. Test by applying for leave as a staff member")
            print("2. Verify balances are being used correctly")
            print("3. Run approval process to test deduction")
        else:
            print("\n❌ INITIALIZATION FAILED!")
            
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()