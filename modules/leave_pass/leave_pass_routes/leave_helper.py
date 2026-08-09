# leave_helper.py - COMPLETE CORRECTED VERSION
from flask import current_app
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import holidays


class StaffObj:
    """Simple staff object class."""
    def __init__(self, grade=0, has_cdsa_international_permission=False, 
                 service_number="", full_name="", directorate=""):
        self.grade = grade
        self.has_cdsa_international_permission = has_cdsa_international_permission
        self.service_number = service_number
        self.full_name = full_name
        self.directorate = directorate

def get_staff_object(service_number: str) -> StaffObj:
    """Fetch staff details from database (users collection) and extract grade."""
    users_coll = getattr(current_app, 'users_collection', None)
    if users_coll is None:
        users_coll = getattr(current_app, 'staff_collection', None)
    
    staff = None
    if users_coll is not None:
        # Try service_number, email or serviceNumber
        staff = users_coll.find_one({
            "$or": [
                {"service_number": service_number},
                {"email": service_number},
                {"serviceNumber": service_number}
            ]
        })
    
    if not staff:
        raise ValueError(f"Staff not found in users collection: {service_number}")
    
    # Extract grade from rankOrGrade string
    grade = 0
    rank_or_grade = staff.get('rankOrGrade', '')
    
    # Try to extract grade from "Grade Level 8"
    if 'Grade Level' in rank_or_grade:
        try:
            grade_str = rank_or_grade.split('Grade Level')[-1].strip()
            grade = int(grade_str)
        except:
            grade = 0
    else:
        # Try to extract any number from the rank
        try:
            import re
            numbers = re.findall(r'\d+', rank_or_grade)
            if numbers:
                grade = int(numbers[0])
        except:
            grade = 0
    
    return StaffObj(
        grade=grade,
        has_cdsa_international_permission=staff.get('hasInternationalPermission', False),
        service_number=service_number,
        full_name=staff.get('name') or staff.get('name', ''),
        directorate=staff.get('directorate', '')
    )

# Now import LeaveBalances AFTER defining get_staff_object
from .leave_logic import LeaveBalances

def get_or_create_current_balance(service_number: str, year: int = None) -> LeaveBalances:
    """
    Get current balance for staff - ASSUMES pre-initialized.
    Creates emergency fallback only if missing.
    """
    if year is None:
        year = datetime.now().year
    
    # 1. Resolve canonical service number and check users collection first
    canonical_service_number = service_number
    users_coll = getattr(current_app, 'users_collection', None)
    if users_coll is None:
        users_coll = getattr(current_app, 'staff_collection', None)
        
    if users_coll is not None:
        user_doc = users_coll.find_one({
            "$or": [
                {"service_number": service_number},
                {"email": service_number},
                {"serviceNumber": service_number}
            ]
        })
        if user_doc:
            canonical_service_number = user_doc.get("service_number") or user_doc.get("serviceNumber") or service_number

    # 2. TRY TO GET EXISTING BALANCE (should exist if pre-initialized)
    balance_doc = current_app.leave_balances.find_one({
        "$or": [
            {"serviceNumber": canonical_service_number},
            {"service_number": canonical_service_number},
            {"serviceNumber": service_number},
            {"service_number": service_number}
        ],
        "year": year
    })
    
    if balance_doc:
        # Convert to LeaveBalances object
        return convert_doc_to_LeaveBalances(balance_doc, canonical_service_number, year)
    
    # ──────────────────────────────────────────────────────────────
    # EMERGENCY FALLBACK: Balance doesn't exist (shouldn't happen!)
    # ──────────────────────────────────────────────────────────────
    print(f"⚠️  EMERGENCY: No balance found for {service_number} (canonical: {canonical_service_number}) in {year}")
    print("   Creating emergency default balance...")
    
    try:
        staff = get_staff_object(canonical_service_number)
        
        # Determine annual entitlement
        if 2 <= staff.grade <= 6:
            annual_entitlement = 21
        elif 7 <= staff.grade <= 15:
            annual_entitlement = 30
        else:
            annual_entitlement = 21
        
        # Create emergency balance
        emergency_balance = {
            "serviceNumber": canonical_service_number,
            "service_number": canonical_service_number,
            "fullName": staff.full_name,
            "name": staff.full_name,
            "directorate": staff.directorate,
            "grade": staff.grade,
            "year": year,
            "isActive": True,
            "is_active": True,
            
            # Minimum required fields
            "annualEntitlement": annual_entitlement,
            "annualRemaining": annual_entitlement,
            "compassionateUsed": 0,
            "casualCalendarDaysUsed": 0,
            "sickThisYear": 0,
            "sickRolling12m": 0,
            "terminalGranted": False,
            
            # Set defaults for other required fields
            "compassionateRemaining": 10,
            "casualCalendarDaysRemaining": 7,
            "sickThisYearRemaining": 21,
            "sickRollingRemaining": 42,
            "maternityAvailable": True,
            "paternityAvailable": True,
            "disembarkationAvailable": True,
            "terminalAvailable": True,
            "hasInternationalPermission": staff.has_cdsa_international_permission,
            
            # Audit
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
            "notes": ["EMERGENCY: Created by fallback function"]
        }
        
        current_app.leave_balances.insert_one(emergency_balance)
        print(f"   ✓ Emergency balance created for {service_number}")
        
        return convert_doc_to_LeaveBalances(emergency_balance, service_number, year)
        
    except Exception as e:
        print(f"   ✗ ERROR creating emergency balance: {e}")
        # Return minimal defaults
        return LeaveBalances(
            annual_entitlement=21,
            annual_remaining=21,
            year=year,
            service_number=service_number,
            grade=0
        )


def update_leave_balance(
    service_number: str, 
    leave_type: str, 
    calendar_days: int = 0, 
    working_days: int = 0,
    year: int = None,
    deduct_from_annual: int = 0
) -> bool:
    """
    Update leave balances after approval/issuance.
    
    Args:
        service_number: Staff service number
        leave_type: Type of leave (annual, casual, compassionate, etc.)
        calendar_days: Calendar days used (for casual leave)
        working_days: Working days used (for most leave types)
        year: Balance year (defaults to current year)
        deduct_from_annual: Working days to deduct from annual (for excess casual)
    
    Returns:
        bool: True if update successful
    """
    if year is None:
        year = datetime.now().year
    
    # Get current balance
    balances = get_or_create_current_balance(service_number, year)
    
    # Prepare update operations
    update_ops = {}
    
    # ──────────────────────────────────────────────
    # ANNUAL LEAVE DEDUCTION (from excess casual or direct annual)
    # ──────────────────────────────────────────────
    if deduct_from_annual > 0:
        update_ops["annualRemaining"] = balances.annual_remaining - deduct_from_annual
    
    # ──────────────────────────────────────────────
    # LEAVE TYPE SPECIFIC UPDATES
    # ──────────────────────────────────────────────
    if leave_type == 'annual':
        # Direct annual leave application
        if working_days > 0:
            update_ops["annualRemaining"] = balances.annual_remaining - working_days
    
    elif leave_type == 'casual':
        # Update casual calendar days used
        if calendar_days > 0:
            new_casual_used = balances.casual_calendar_days_used + calendar_days
            update_ops["casualCalendarDaysUsed"] = new_casual_used
            update_ops["casualCalendarDaysRemaining"] = max(0, 7 - new_casual_used)
    
    elif leave_type == 'compassionate':
        # Update compassionate leave
        if working_days > 0:
            new_compassionate_used = balances.compassionate_used + working_days
            update_ops["compassionateUsed"] = new_compassionate_used
            update_ops["compassionateRemaining"] = max(0, 10 - new_compassionate_used)
    
    elif leave_type == 'sick':
        # Update sick leave (non-hospitalized)
        if working_days > 0:
            # Update calendar year total
            new_sick_year = balances.sick_this_year + working_days
            update_ops["sickThisYear"] = new_sick_year
            update_ops["sickThisYearRemaining"] = max(0, 21 - new_sick_year)
            
            # Update rolling 12-month total
            new_sick_rolling = balances.sick_rolling_12m + working_days
            update_ops["sickRolling12m"] = new_sick_rolling
            update_ops["sickRollingRemaining"] = max(0, 42 - new_sick_rolling)
    
    elif leave_type == 'paternity':
        # Mark paternity leave as used
        update_ops["paternityAvailable"] = False
        if working_days > 0:
            update_ops["paternityDaysUsed"] = balances.paternity_days_used + working_days
    
    elif leave_type == 'maternity':
        # Mark maternity leave as used
        update_ops["maternityAvailable"] = False
        if working_days > 0:
            # Store start date if provided
            # This would need to be passed from the application
            pass
    
    elif leave_type == 'disembarkation':
        # Mark disembarkation leave as used (one-time per attachment)
        update_ops["disembarkationAvailable"] = False
    
    elif leave_type == 'terminal':
        # Mark terminal leave as granted
        update_ops["terminalGranted"] = True
        update_ops["terminalAvailable"] = False
    
    # ──────────────────────────────────────────────
    # APPLY UPDATES TO DATABASE
    # ──────────────────────────────────────────────
    if update_ops:
        # Add audit fields
        update_ops["updatedAt"] = datetime.utcnow()
        
        # Update in database
        result = current_app.leave_balances.update_one(
            {"service_number": service_number, "year": year},
            {"$set": update_ops}
        )
        
        if result.modified_count > 0:
            print(f"✓ Balance updated for {service_number}: {update_ops}")
            return True
        else:
            print(f"✗ No balance document found for {service_number}")
            return False
    
    return False


def deduct_casual_with_annual(
    service_number: str,
    total_calendar_days: int,
    free_calendar_days: int,
    excess_working_days: int,
    year: int = None
) -> bool:
    """
    Deduct casual leave with partial annual deduction.
    This is a convenience wrapper for update_leave_balance.
    """
    return update_leave_balance(
        service_number=service_number,
        leave_type='casual',
        calendar_days=total_calendar_days,  # Total calendar days used
        working_days=0,  # No direct working days for casual
        deduct_from_annual=excess_working_days,  # Excess deducted from annual
        year=year
    )


def batch_update_balances(updates: List[dict]) -> bool:
    """
    Update multiple balances in one operation.
    Useful for year-end rollover or bulk corrections.
    """
    success = True
    for update in updates:
        try:
            result = update_leave_balance(**update)
            if not result:
                success = False
        except Exception as e:
            print(f"Error in batch update: {e}")
            success = False
    
    return success




def convert_doc_to_LeaveBalances(doc: dict, service_number: str, year: int) -> LeaveBalances:
    """Convert MongoDB document to LeaveBalances object."""
    return LeaveBalances(
        # Annual
        annual_entitlement=doc.get('annualEntitlement', 0),
        annual_remaining=doc.get('annualRemaining', 0),
        
        # Compassionate
        compassionate_used=doc.get('compassionateUsed', 0),
        compassionate_remaining=doc.get('compassionateRemaining', 10),
        
        # Casual
        casual_calendar_days_used=doc.get('casualCalendarDaysUsed', 0),
        casual_calendar_days_remaining=doc.get('casualCalendarDaysRemaining', 7),
        
        # Sick
        sick_this_year=doc.get('sickThisYear', 0),
        sick_rolling_12m=doc.get('sickRolling12m', 0),
        sick_this_year_remaining=doc.get('sickThisYearRemaining', 21),
        sick_rolling_remaining=doc.get('sickRollingRemaining', 42),
        
        # Maternity
        maternity_available=doc.get('maternityAvailable', True),
        maternity_start_date=doc.get('maternityStartDate'),
        
        # Paternity
        paternity_available=doc.get('paternityAvailable', True),
        paternity_days_used=doc.get('paternityDaysUsed', 0),
        
        # Disembarkation
        disembarkation_available=doc.get('disembarkationAvailable', True),
        
        # Terminal
        terminal_granted=doc.get('terminalGranted', False),
        terminal_available=doc.get('terminalAvailable', True),
        
        # Hospitalization
        hospitalization_days_used=doc.get('hospitalizationDaysUsed', 0),
        first_hospitalization_used=doc.get('firstHospitalizationUsed', False),
        
        # International
        has_international_permission=doc.get('hasInternationalPermission', False),
        
        # Metadata
        year=year,
        service_number=service_number,
        full_name=doc.get('name', ''),
        directorate=doc.get('directorate', ''),
        grade=doc.get('grade', 0)
    )

def get_current_balances(service_number: str, year: int = None) -> LeaveBalances:
    """Alias for get_or_create_current_balance for backward compatibility."""
    return get_or_create_current_balance(service_number, year)

def get_hospitalization_records(service_number: str) -> List[Tuple[datetime, datetime]]:
    """Get hospitalization records for sick leave validation."""
    medical_coll = current_app.medical_records
    records = medical_coll.find({
        "service_number": service_number,
        "recordType": "hospitalization"
    }).sort("admissionDate", 1)
    
    hospitalization_records = []
    for record in records:
        admission = record.get('admissionDate')
        discharge = record.get('dischargeDate') or datetime.utcnow()
        if admission:
            hospitalization_records.append((admission, discharge))
    
    return hospitalization_records

def get_public_holidays(year: int = None) -> List[datetime]:
    """Fetch public holidays for working day calculations."""
    if year is None:
        year = datetime.now().year
    
    # Get holidays using holidays library
    nigeria_holidays = holidays.country_holidays('NG', years=year)
    
    # Convert to list of datetime objects
    holiday_dates = []
    for date_obj, name in nigeria_holidays.items():
        if isinstance(date_obj, datetime):
            holiday_dates.append(date_obj)
        else:
            holiday_dates.append(datetime.combine(date_obj, datetime.min.time()))
    
    return holiday_dates

def calendar_days_between(start_date: datetime, end_date: datetime) -> int:
    """Helper to calculate calendar days."""
    if start_date > end_date:
        return 0
    return (end_date - start_date).days + 1

# Legacy compatibility function
def get_legacy_balance(service_number: str, year: int = None) -> dict:
    """Get balance in old format for backward compatibility."""
    balances = get_or_create_current_balance(service_number, year)
    
    return {
        'annual_remaining': balances.annual_remaining,
        'compassionate_used': balances.compassionate_used,
        'casual_calendar_days': balances.casual_calendar_days_used,  # Map to new
        'sick_this_year': balances.sick_this_year,
        'sick_rolling_12m': balances.sick_rolling_12m,
        'terminal_granted': balances.terminal_granted
    }



def refund_leave_balance(
    service_number: str,
    application: dict,
    year: int = None
) -> bool:
    """
    Refund leave balance when application is rejected.
    This reverses the deduction that was made at final approval.
    """
    if year is None:
        year = datetime.now().year
    
    # Get current balance
    balance = get_or_create_current_balance(service_number, year)
    balance_doc = current_app.leave_balances.find_one({
        "service_number": service_number,
        "year": year
    })
    
    if not balance_doc:
        print(f"✗ No balance found for {service_number}")
        return False
    
    leave_type = application.get("leave_type")
    days_requested = application.get("numberOfDays", 0)
    
    # Get validation metadata to know what was deducted
    validation = application.get("validation", {})
    metadata = validation.get("metadata", {})
    deduct_from_annual = metadata.get("deduct_from_annual", 0)
    
    update_fields = {}
    
    # ============ REFUND BASED ON LEAVE TYPE ============
    if leave_type == "annual":
        # Refund annual leave days
        current_annual = balance_doc.get("annualRemaining", 0)
        update_fields["annualRemaining"] = current_annual + days_requested
        update_fields["notes"] = balance_doc.get("notes", []) + [
            f"{datetime.utcnow().isoformat()}: REFUNDED {days_requested} annual days - Application {application.get('referenceId')} rejected"
        ]
    
    elif leave_type == "compassionate":
        # Refund compassionate leave
        current_used = balance_doc.get("compassionateUsed", 0)
        current_remaining = balance_doc.get("compassionateRemaining", 0)
        
        update_fields["compassionateUsed"] = max(0, current_used - days_requested)
        update_fields["compassionateRemaining"] = current_remaining + days_requested
        update_fields["notes"] = balance_doc.get("notes", []) + [
            f"{datetime.utcnow().isoformat()}: REFUNDED {days_requested} compassionate days - Application {application.get('referenceId')} rejected"
        ]
    
    elif leave_type == "casual":
        # Refund casual leave and any annual deduction
        calendar_days = 0
        
        # Calculate calendar days from application
        effective_date = application.get("effectiveDate")
        end_date = application.get("endDate")
        if isinstance(effective_date, datetime) and isinstance(end_date, datetime):
            calendar_days = (end_date - effective_date).days + 1
        
        # Refund casual calendar days
        current_casual_used = balance_doc.get("casualCalendarDaysUsed", 0)
        new_casual_used = max(0, current_casual_used - calendar_days)
        update_fields["casualCalendarDaysUsed"] = new_casual_used
        update_fields["casualCalendarDaysRemaining"] = 7 - new_casual_used
        
        # Refund annual deduction if any
        if deduct_from_annual > 0:
            current_annual = balance_doc.get("annualRemaining", 0)
            update_fields["annualRemaining"] = current_annual + deduct_from_annual
        
        update_fields["notes"] = balance_doc.get("notes", []) + [
            f"{datetime.utcnow().isoformat()}: REFUNDED {calendar_days} casual days, {deduct_from_annual} annual days - Application {application.get('referenceId')} rejected"
        ]
    
    elif leave_type == "sick":
        # Refund sick leave tracking (not actual leave days)
        current_sick_year = balance_doc.get("sickThisYear", 0)
        current_sick_rolling = balance_doc.get("sickRolling12m", 0)
        
        update_fields["sickThisYear"] = max(0, current_sick_year - days_requested)
        update_fields["sickThisYearRemaining"] = 21 - (current_sick_year - days_requested)
        update_fields["sickRolling12m"] = max(0, current_sick_rolling - days_requested)
        update_fields["sickRollingRemaining"] = 42 - (current_sick_rolling - days_requested)
        update_fields["notes"] = balance_doc.get("notes", []) + [
            f"{datetime.utcnow().isoformat()}: REFUNDED {days_requested} sick days - Application {application.get('referenceId')} rejected"
        ]
    
    elif leave_type == "disembarkation":
        # Refund annual leave used for disembarkation
        tacos_details = application.get("tacosDetails", {})
        attachment_months = tacos_details.get("attachmentMonths", 0)
        days_to_refund = 14 if attachment_months > 6 else 7
        
        current_annual = balance_doc.get("annualRemaining", 0)
        update_fields["annualRemaining"] = current_annual + days_to_refund
        update_fields["disembarkationAvailable"] = True
        update_fields["notes"] = balance_doc.get("notes", []) + [
            f"{datetime.utcnow().isoformat()}: REFUNDED {days_to_refund} annual days (disembarkation) - Application {application.get('referenceId')} rejected"
        ]
    
    # Add audit timestamp
    update_fields["updatedAt"] = datetime.utcnow()
    
    # Apply updates
    if update_fields:
        result = current_app.leave_balances.update_one(
            {"_id": balance_doc["_id"]},
            {"$set": update_fields}
        )
        return result.modified_count > 0
    
    return False