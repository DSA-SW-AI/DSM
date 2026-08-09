from flask_mail import Message
from flask import current_app
from datetime import datetime


def send_final_approval_email(applicant, application, receipt_number):
    mail = current_app.mail
    
    subject = "Leave Application Approved"
    
    body = f"""
Dear {applicant.get('fullName')},

Your leave application has been APPROVED.

Application Reference ID: {application.get('referenceId')}
Leave Type: {application.get('leave_type')}
Number of Days: {application.get('numberOfDays')}
Receipt Number: {receipt_number}


View your application status here: http://localhost:5009/track_application

Regards,
Directorate of Administration
"""

    msg = Message(
        subject=subject,
        recipients=[applicant["email"]],
        body=body
    )

    mail.send(msg)




def send_rejection_email(applicant_email, applicant_name, application, rejected_by, comments):

    mail = current_app.mail

    subject = "Your Leave/Pass Application Was Rejected"

    body = f"""
Dear {applicant_name},

Your leave/pass application has been rejected.

Application ID: {application.get('_id')}
Rejected By: {rejected_by}
Date: {datetime.utcnow().strftime('%d %B %Y')}
Comments:
{comments}

Please log into the system for more details.

Regards,
Leave Management System
"""

    msg = Message(
        subject=subject,
        recipients=[applicant_email],
        body=body
    )

    mail.send(msg)