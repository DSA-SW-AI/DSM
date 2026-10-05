from datetime import datetime
from utils.email_helper import send_leave_final_approval_email, send_leave_rejection_email


def send_final_approval_email(applicant, application, receipt_number):
    """
    Sends final approval email using GovMail SMTP.
    """
    try:
        target_email = applicant.get("email")
        if not target_email:
            return False

        applicant_name = applicant.get("fullName") or application.get("applicantName") or "Staff"
        service_number = applicant.get("service_number") or application.get("applicantId") or "N/A"
        reference_id = application.get("referenceId") or "N/A"
        leave_type = application.get("leave_type") or "Leave/Pass"
        start_date = str(application.get("startDate") or "N/A")
        end_date = str(application.get("endDate") or "N/A")
        number_of_days = application.get("numberOfDays") or application.get("days") or 1

        return send_leave_final_approval_email(
            target_email=target_email,
            applicant_name=applicant_name,
            service_number=service_number,
            reference_id=reference_id,
            receipt_number=receipt_number,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            number_of_days=number_of_days,
            async_dispatch=True
        )
    except Exception as e:
        print(f"[Email Service] send_final_approval_email error: {e}")
        return False


def send_rejection_email(applicant_email, applicant_name, application, rejected_by, comments):
    """
    Sends leave/pass rejection email using GovMail SMTP.
    """
    try:
        if not applicant_email:
            return False

        service_number = application.get("applicantId") or "N/A"
        reference_id = application.get("referenceId") or str(application.get("_id"))
        leave_type = application.get("leave_type") or "Leave/Pass"

        return send_leave_rejection_email(
            target_email=applicant_email,
            applicant_name=applicant_name or "Staff",
            service_number=service_number,
            reference_id=reference_id,
            leave_type=leave_type,
            rejected_by_name=rejected_by or "Approver",
            rejected_by_role="Approving Authority",
            rejection_reason=comments,
            async_dispatch=True
        )
    except Exception as e:
        print(f"[Email Service] send_rejection_email error: {e}")
        return False