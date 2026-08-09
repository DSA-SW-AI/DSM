# leave_logic.py - COMPLETE CORRECTED VERSION
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum

class LeaveType(Enum):
    ANNUAL = "annual"
    COMPASSIONATE = "compassionate"
    CASUAL = "casual"
    SICK = "sick"
    MATERNITY = "maternity"
    PATERNITY = "paternity"
    DISEMBARKATION = "disembarkation"
    TERMINAL = "terminal"

@dataclass
class LeaveBalances:
    """Complete TACOS leave balances for a staff member."""
    
    # Annual Leave (08.02)
    annual_entitlement: int = 0    # 21 or 30 days based on grade
    annual_remaining: int = 0      # Remaining days
    
    # Compassionate Leave (08.03a) - Max 10 working days/year
    compassionate_used: int = 0    # Days used this year
    compassionate_remaining: int = 10  # Remaining (10 - used)
    
    # Casual Leave/Pass (08.03b) - Max 7 calendar days free
    casual_calendar_days_used: int = 0  # Calendar days used
    casual_calendar_days_remaining: int = 7  # Free calendar days remaining
    
    # Sick Leave (08.04)
    sick_this_year: int = 0            # Calendar year total
    sick_rolling_12m: int = 0          # Rolling 12-month total
    sick_this_year_remaining: int = 21  # Days before Medical Board trigger
    sick_rolling_remaining: int = 42   # Days before 6-week limit
    
    # Maternity Leave (08.06, 08.07)
    maternity_available: bool = True   # Available if not used
    maternity_start_date: Optional[datetime] = None
    
    # Paternity Leave (08.08)
    paternity_available: bool = True   # Available if not used
    paternity_days_used: int = 0
    
    # Disembarkation Leave (08.05)
    disembarkation_available: bool = True  # Available based on attachment
    
    # Terminal Leave (08.11)
    terminal_granted: bool = False     # One-time on retirement
    terminal_available: bool = True    # Available if not retired
    
    # Hospitalization (08.04c, 08.04e)
    hospitalization_days_used: int = 0
    first_hospitalization_used: bool = False
    
    # International travel permission
    has_international_permission: bool = False
    
    # Year this balance applies to
    year: int = 0
    
    # Staff info
    service_number: str = ""
    full_name: str = ""
    directorate: str = ""
    grade: int = 0

# Helper functions
def is_low_grade(grade: int) -> bool:
    return 2 <= grade <= 6

def get_annual_entitlement(grade: int) -> int:
    return 21 if is_low_grade(grade) else 30

def get_terminal_entitlement(grade: int) -> int:
    return 42 if is_low_grade(grade) else 90

def working_days_between(
    start_date: datetime,
    end_date: datetime,
    public_holidays: List[datetime] = None
) -> int:
    if public_holidays is None:
        public_holidays = []
    if start_date > end_date:
        return 0
    current = start_date
    count = 0
    while current <= end_date:
        if current.weekday() < 5 and current not in public_holidays:
            count += 1
        current += timedelta(days=1)
    return count

def calendar_days_between(start_date: datetime, end_date: datetime) -> int:
    if start_date > end_date:
        return 0
    return (end_date - start_date).days + 1

def is_hospitalized_period(
    start_date: datetime,
    end_date: datetime,
    hospitalization_records: List[Tuple[datetime, datetime]] = None
) -> bool:
    if not hospitalization_records:
        return False
    for hosp_start, hosp_end in hospitalization_records:
        if not (end_date < hosp_start or start_date > hosp_end):
            return True
    return False

# Compatibility wrapper for old field names
def get_legacy_fields(balances: LeaveBalances) -> dict:
    """Get old field names for backward compatibility."""
    return {
        'annual_remaining': balances.annual_remaining,
        'compassionate_used': balances.compassionate_used,
        'casual_calendar_days': balances.casual_calendar_days_used,  # Map to new name
        'sick_this_year': balances.sick_this_year,
        'sick_rolling_12m': balances.sick_rolling_12m,
        'terminal_granted': balances.terminal_granted
    }

# ──────────────────────────────────────────────────────────────
# MAIN VALIDATION FUNCTION - UPDATED FOR NEW LeaveBalances
# ──────────────────────────────────────────────────────────────
def validate_leave_request(
    request_data: dict,
    staff: Any,
    current_year_balances: LeaveBalances,
    public_holidays: List[datetime] = None,
    hospitalization_records: List[Tuple[datetime, datetime]] = None
) -> Tuple[bool, Optional[str], Optional[dict]]:
    
    if public_holidays is None:
        public_holidays = []
    if hospitalization_records is None:
        hospitalization_records = []

    leave_type = request_data.get('type')
    if not leave_type:
        return False, "Leave type is required", None

    # Extract dates
    leave_start_date = request_data.get('effective_date')
    leave_end_date = request_data.get('end_date')
    application_date = request_data.get('start_date')
    
    if not leave_start_date:
        leave_start_date = request_data.get('start_date')
    
    has_dates = leave_start_date and leave_end_date
    days = request_data.get('working_days_requested', 0)

    # Common metadata
    metadata = {
        'requires_approval': True,
        'deduct_from_annual': 0,
        'triggers_medical_board': False,
        'notes': ["All leave is subject to CDSA / director discretion"]
    }

    # ──────────────────────────────────────────────
    # GLOBAL CHECK: International travel
    # ──────────────────────────────────────────────
    if request_data.get('outside_nigeria', False):
        if not getattr(staff, 'has_cdsa_international_permission', False):
            return False, "CDSA permission required for leave/travel outside Nigeria", None
        metadata['notes'].append("International travel permission confirmed")

    # ──────────────────────────────────────────────
    # ANNUAL LEAVE
    # ──────────────────────────────────────────────
    if leave_type == 'annual':
        if not has_dates:
            return False, "Effective and end dates required for annual leave", None
        
        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != days:
            metadata['notes'].append(f"Adjusted from {days} to {calculated} working days")
            days = calculated
        
        if current_year_balances.annual_remaining < days:
            return False, f"Insufficient annual leave ({current_year_balances.annual_remaining} days remaining)", None
        
        metadata['deduct_from_annual'] = days
        return True, "Annual leave request valid", metadata

    # ──────────────────────────────────────────────
    # CASUAL LEAVE / PASS - UPDATED FOR NEW FIELD NAMES
    # ──────────────────────────────────────────────

    # ──────────────────────────────────────────────
# CASUAL LEAVE / PASS - CORRECTED DEDUCTION LOGIC
# ──────────────────────────────────────────────
    elif leave_type == 'casual':
        if not has_dates:
            return False, "Effective and end dates required for casual leave", None

        req_calendar = calendar_days_between(leave_start_date, leave_end_date)
        req_working = working_days_between(leave_start_date, leave_end_date, public_holidays)

        if req_calendar <= 0:
            return False, "Invalid date range for casual leave", None

        # Get remaining casual days
        casual_remaining = current_year_balances.casual_calendar_days_remaining
        
        if req_calendar <= casual_remaining:
            # ─────────────────────────────────
            # CASE 1: FULLY WITHIN CASUAL ALLOWANCE
            # ─────────────────────────────────
            metadata['notes'].append(f"✓ {req_calendar} calendar days within casual leave allowance")
            return True, "Casual leave approved (no annual deduction)", metadata
        
        else:
            # ─────────────────────────────────
            # CASE 2: PARTIALLY EXCEEDS CASUAL ALLOWANCE
            # ─────────────────────────────────
            
            # Free days = remaining casual days
            free_calendar_days = casual_remaining
            # Excess days = total - free days (these will be deducted from annual)
            excess_calendar_days = req_calendar - free_calendar_days
            
            # Calculate working days for the excess period
            # We need to find which dates are in excess to calculate working days accurately
            if free_calendar_days > 0:
                # Calculate end date of free period
                free_end_date = leave_start_date + timedelta(days=free_calendar_days - 1)
                excess_start_date = free_end_date + timedelta(days=1)
                
                # Calculate working days for excess period
                excess_working_days = working_days_between(excess_start_date, leave_end_date, public_holidays)
            else:
                # No free days left, entire leave is excess
                excess_working_days = req_working
            
            # Check if user has enough annual leave
            if current_year_balances.annual_remaining < excess_working_days:
                return False, (
                    f"Insufficient annual leave balance. "
                    f"Need {excess_working_days} working days for excess casual leave, "
                    f"but only {current_year_balances.annual_remaining} remaining."
                ), None
            
            # Set deduction amount
            metadata['deduct_from_annual'] = excess_working_days
            
            # Build detailed notes
            notes = []
            if free_calendar_days > 0:
                notes.append(f"✓ {free_calendar_days} calendar days covered by casual allowance")
            notes.append(f"⚠️  {excess_calendar_days} calendar days exceed casual limit")
            notes.append(f"📊 {excess_working_days} working days will be deducted from Annual Leave")
            notes.append(f"Annual Leave remaining after deduction: {current_year_balances.annual_remaining - excess_working_days} days")
            
            metadata['notes'] = notes
            
            return True, (
                f"Casual leave partially approved. "
                f"{free_calendar_days} days casual + {excess_working_days} days from annual leave"
            ), metadata

    # ──────────────────────────────────────────────
    # COMPASSIONATE LEAVE
    # ──────────────────────────────────────────────
    elif leave_type == 'compassionate':
        if not has_dates:
            return False, "Effective and end dates required", None
        
        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != days:
            metadata['notes'].append(f"Adjusted from {days} to {calculated} working days")
            days = calculated
        
        # Check against new field: compassionate_remaining
        if current_year_balances.compassionate_remaining < days:
            return False, f"Maximum 10 working days compassionate leave per year (used: {current_year_balances.compassionate_used}, remaining: {current_year_balances.compassionate_remaining})", None
        
        metadata['notes'].append("Pending director recommendation & CDSA approval")
        return True, "Compassionate leave request valid", metadata

    # ──────────────────────────────────────────────
    # SICK LEAVE - UPDATED FOR NEW FIELDS
    # ──────────────────────────────────────────────
    elif leave_type == 'sick':
        if not request_data.get('has_medical_certificate', False):
            return False, "Government medical officer certificate required", None
        if not has_dates:
            return False, "Effective and end dates required", None

        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != days:
            metadata['notes'].append(f"Adjusted from {days} to {calculated} working days")
            days = calculated

        hospitalized = is_hospitalized_period(leave_start_date, leave_end_date, hospitalization_records)

        if hospitalized:
            calendar_days = calendar_days_between(leave_start_date, leave_end_date)
            if request_data.get('first_hospitalization', False) and calendar_days > 90:
                return False, "First hospitalization limited to approximately 3 months (~90 calendar days)", None
            metadata['notes'].append("Hospitalization period – does not count against sick leave quota")
            return True, "Hospital sick leave approved", metadata

        # NON-HOSPITALIZED SICK LEAVE
        if days > 21:
            metadata['notes'].append(
                f"Warning: requested sick leave ({days} working days) exceeds the normal 21-day guideline."
            )
            metadata['notes'].append(
                "Approval is still possible at the discretion of the approving authority."
            )

        # Check against new fields
        if current_year_balances.sick_rolling_remaining < days:
            return False, (
                f"Exceeds maximum 6 weeks (42 working days) in rolling 12 months "
                f"(current rolling total: {current_year_balances.sick_rolling_12m}, remaining: {current_year_balances.sick_rolling_remaining})"
            ), None

        # Calendar year threshold → Medical Board required
        if current_year_balances.sick_this_year_remaining < days:
            metadata['triggers_medical_board'] = True
            metadata['notes'].append(
                "Note: total sick leave this calendar year will exceed 21 days → "
                "Medical Board proceedings must be initiated after approval"
            )

        return True, "Non-hospitalized sick leave request valid (subject to approval)", metadata

    # ──────────────────────────────────────────────
    # MATERNITY LEAVE - UPDATED
    # ──────────────────────────────────────────────
    elif leave_type == 'maternity':
        if not has_dates:
            return False, "Effective and end dates required for maternity leave", None
        
        edd = request_data.get('expected_delivery_date')
        if not edd:
            return False, "Expected delivery date (EDD) is required for maternity leave", None

        if not current_year_balances.maternity_available:
            return False, "Maternity leave already used", None

        if leave_start_date > edd:
            return False, "Effective date cannot be after expected delivery date", None

        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != 112:
            return False, f"Maternity leave must be exactly 112 working days (calculated: {calculated})", None

        days_before_edd = working_days_between(leave_start_date, edd, public_holidays)
        if days_before_edd > 28:
            return False, f"Maternity leave must start within 28 working days before EDD (currently {days_before_edd})", None

        metadata['notes'].append("Maternity leave – 112 working days continuous block")
        metadata['notes'].append("Follows Public Service Rules provisions")
        return True, "Maternity leave request valid", metadata

    # ──────────────────────────────────────────────
    # PATERNITY LEAVE - UPDATED
    # ──────────────────────────────────────────────
    elif leave_type == 'paternity':
        if not has_dates:
            return False, "Effective and end dates required for paternity leave", None

        if not current_year_balances.paternity_available:
            return False, "Paternity leave already used", None

        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != 14:
            return False, f"Paternity leave must be exactly 14 working days (calculated: {calculated})", None

        metadata['notes'].append("Paternity leave – 14 working days (per PSR 2021)")
        return True, "Paternity leave request valid", metadata

    # ──────────────────────────────────────────────
    # DISEMBARKATION LEAVE
    # ──────────────────────────────────────────────
    elif leave_type == 'disembarkation':
        months = request_data.get('attachment_months', 0)
        if months < 3:
            return False, "Attachment must be at least 3 continuous months", None
        if not has_dates:
            return False, "Effective and end dates required for disembarkation leave", None

        expected_days = 14 if months > 6 else 7

        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != expected_days:
            return False, f"Disembarkation leave should be {expected_days} working days for {months} months attachment (calculated: {calculated})", None

        metadata['notes'].append(f"Disembarkation leave for {months} months course/operational attachment")
        return True, f"Disembarkation leave approved – {expected_days} working days", metadata

    # ──────────────────────────────────────────────
    # TERMINAL LEAVE - UPDATED
    # ──────────────────────────────────────────────
    elif leave_type == 'terminal':
        if not has_dates:
            return False, "Effective and end dates required for terminal leave", None

        if current_year_balances.terminal_granted:
            return False, "Terminal leave already granted", None
        if not current_year_balances.terminal_available:
            return False, "Terminal leave not available", None

        expected_days = get_terminal_entitlement(staff.grade)

        calculated = working_days_between(leave_start_date, leave_end_date, public_holidays)
        if calculated != expected_days:
            return False, f"Terminal leave should be {expected_days} working days for grade {staff.grade} (calculated: {calculated})", None

        metadata['notes'].append(f"Terminal leave on retirement – {expected_days} working days")
        metadata['notes'].append("Final leave – non-extendable")
        return True, f"Terminal leave request valid", metadata

    # ──────────────────────────────────────────────
    # Unknown type
    # ──────────────────────────────────────────────
    return False, f"Unsupported leave type: {leave_type}", None