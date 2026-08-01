# permissions.py

ROLE_PERMISSIONS = {
    "director": {
        "sidebar": ["dashboard", "documents", "incoming", "outgoing", "filing_cabinets", "civilian_personnel", "recruitment_transfers", "leave_pass", "parade_states", "hr_reports", "analytics"],
        "modules": ["documents", "personnel_civilian", "promotions", "recruitment_transfer", "leave_pass", "reports"],
        "actions": ["approve_documents", "incoming_register", "outgoing_register"]
    },
    "dd": {  # Deputy Director
        "sidebar": ["dashboard", "documents", "incoming", "outgoing", "filing_cabinets", "civilian_personnel", "recruitment_transfers", "leave_pass", "parade_states", "hr_reports"],
        "modules": ["documents", "personnel_civilian", "recruitment_transfer", "leave_pass", "reports"],
        "actions": ["review_documents", "incoming_register", "outgoing_register"]
    },
    "ad": {  # Assistant Director
        "sidebar": ["dashboard", "documents", "incoming", "outgoing", "filing_cabinets", "civilian_personnel", "leave_pass", "parade_states"],
        "modules": ["documents", "personnel_civilian", "leave_pass"],
        "actions": ["review_documents", "incoming_register"]
    },
    "registry": {
        "sidebar": ["dashboard", "documents", "incoming","civilian_personnel" ,"outgoing", "filing_cabinets"],
        "modules": ["documents","personnel_civilian"],
        "actions": ["incoming_register", "outgoing_register", "archive_files"]
    },
    "personnel": {
        "sidebar": ["dashboard", "civilian_personnel", "recruitment_transfers", "leave_pass", "parade_states", "hr_reports"],
        "modules": ["personnel_civilian", "recruitment_transfer", "leave_pass", "reports"],
        "actions": ["update_roster", "process_leave"]
    },
    "civilian_head": {
        "sidebar": ["dashboard", "civilian_personnel", "leave_pass"],
        "modules": ["personnel_civilian", "leave_pass"],
        "actions": ["process_leave"]
    },
    "civilian": {
        "sidebar": ["dashboard", "documents", "leave_pass"],
        "modules": ["documents", "leave_pass"],
        "actions": ["submit_leave_request"]
    }
}

def has_permission(role, resource_category, item_name):
    """
    Utility checking tool to verify if a specific role string is allowed 
    to interact with a specific UI component layout segment.
    """
    role_rules = ROLE_PERMISSIONS.get(role, {})
    allowed_items = role_rules.get(resource_category, [])
    return item_name in allowed_items
