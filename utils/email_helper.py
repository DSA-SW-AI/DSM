import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ssl
import threading
from datetime import datetime

# ================= GALAXY BACKBONE IMPLICIT PRODUCTION SETTINGS =================
SMTP_SERVER = "mail.govmail.gbb.com.ng"      # Public GovMail domain handle
SMTP_PORT = 465                               # Secure Outgoing channel
GOVMAIL_USER = "paul.ikeh@dsa.mil.ng"         # Authorized sender account handle
GOVMAIL_PASS = "Gunnexzy4!!!!"                # Password or App-Specific Token Key
PORTAL_BASE_URL = "http://localhost:5000"     # Base portal URL
# =================================================================================


def _send_smtp_email_sync(target_email, subject, plain_text, html_content=None):
    """
    Synchronously transmits an email via SSL to the Galaxy Backbone GovMail gateway.
    """
    if not target_email or "@" not in str(target_email):
        print(f"[GovMail Dispatcher] Skipped: Invalid recipient email: '{target_email}'")
        return False

    clean_email = str(target_email).strip().lower()

    try:
        msg = MIMEMultipart("alternative")
        msg['From'] = f"DSA Leave & Pass Portal <{GOVMAIL_USER}>"
        msg['To'] = clean_email
        msg['Subject'] = subject

        # Attach plain text version
        msg.attach(MIMEText(plain_text, 'plain', 'utf-8'))

        # Attach HTML version if provided
        if html_content:
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=20)
        server.ehlo()
        server.login(GOVMAIL_USER, GOVMAIL_PASS)
        server.sendmail(GOVMAIL_USER, clean_email, msg.as_string())
        server.quit()

        print(f"[GovMail Dispatcher] Successfully sent '{subject}' to {clean_email}")
        return True
    except Exception as e:
        print(f"[GovMail Dispatcher] CRITICAL: Transmission failed for {clean_email}: {str(e)}")
        return False


def dispatch_email(target_email, subject, plain_text, html_content=None, async_dispatch=True):
    """
    Dispatches email either asynchronously (in a daemon thread to avoid blocking HTTP requests)
    or synchronously.
    """
    if not target_email:
        return False

    if async_dispatch:
        t = threading.Thread(
            target=_send_smtp_email_sync,
            args=(target_email, subject, plain_text, html_content),
            daemon=True
        )
        t.start()
        return True
    else:
        return _send_smtp_email_sync(target_email, subject, plain_text, html_content)


def send_credentials_email(target_alt_email, official_login_email, plain_password, service_number, async_dispatch=True):
    """
    Delivers newly generated account credentials to user's alternate/contact email.
    """
    subject = f"RESTRICTED: Official Account Credentials - {str(service_number).upper()}"

    plain_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
PORTAL REGISTRY CONTROL SECTOR

Acknowledge,

An official user login profile has been successfully generated for your service track parameters within the DSA system ecosystem.

Please find your secure network deployment access credentials detailed below:

------------------------------------------------------------
OFFICIAL SIGN-IN EMAIL: {official_login_email}
GENERATED TEMPORARY PASSWORD: {plain_password}
SERVICE / STAFF NUMBER: {service_number}
------------------------------------------------------------

SECURITY DIRECTIVE:
1. Access the portal landing interface via: {PORTAL_BASE_URL}
2. Input these credentials to access your dashboard.
3. Do not distribute, store in plain text, or share these system access credentials with unauthorized personnel.

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #212529; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
  .header {{ background-color: #0b2545; color: #ffffff; padding: 25px 20px; text-align: center; border-bottom: 4px solid #134074; }}
  .header h1 {{ margin: 0; font-size: 20px; letter-spacing: 1px; text-transform: uppercase; font-weight: 700; }}
  .header p {{ margin: 5px 0 0 0; font-size: 12px; color: #cbd5e1; letter-spacing: 0.5px; }}
  .body {{ padding: 30px 25px; }}
  .badge {{ display: inline-block; background-color: #eef2ff; color: #1e40af; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; margin-bottom: 15px; }}
  .creds-box {{ background-color: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #0b2545; padding: 15px 20px; border-radius: 4px; margin: 20px 0; }}
  .creds-row {{ margin: 8px 0; font-size: 14px; }}
  .creds-label {{ font-weight: 600; color: #475569; width: 140px; display: inline-block; }}
  .creds-val {{ font-family: 'Courier New', Courier, monospace; font-weight: 700; color: #0b2545; }}
  .btn {{ display: inline-block; background-color: #0b2545; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 14px; margin-top: 15px; }}
  .notice {{ background-color: #fffbeb; border: 1px solid #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 15px; font-size: 12px; color: #92400e; margin-top: 20px; border-radius: 4px; }}
  .footer {{ background-color: #f1f5f9; padding: 15px 20px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Defence Space Administration</h1>
    <p>Portal Registry Control Sector &bull; Official Communication</p>
  </div>
  <div class="body">
    <div class="badge">OFFICIAL ACCOUNT CREDENTIALS</div>
    <p>Acknowledge,</p>
    <p>An official user login profile has been generated for you within the DSA System Ecosystem. Please find your secure deployment credentials below:</p>
    
    <div class="creds-box">
      <div class="creds-row"><span class="creds-label">Staff / Service No:</span> <span class="creds-val">{service_number}</span></div>
      <div class="creds-row"><span class="creds-label">Sign-in Email:</span> <span class="creds-val">{official_login_email}</span></div>
      <div class="creds-row"><span class="creds-label">Temporary Password:</span> <span class="creds-val">{plain_password}</span></div>
    </div>

    <div style="text-align: center;">
      <a href="{PORTAL_BASE_URL}" class="btn">Log In To Portal</a>
    </div>

    <div class="notice">
      <strong>SECURITY DIRECTIVE:</strong>
      Do not share or distribute these credentials. You will be prompted to verify your profile on initial sign-in.
    </div>
  </div>
  <div class="footer">
    DSA Directorate of Administration (DOA) Registry Control &bull; Restricted / Official
  </div>
</div>
</body>
</html>
"""
    return dispatch_email(target_alt_email, subject, plain_text, html_content, async_dispatch=async_dispatch)


def send_approval_action_email(
    target_email,
    approver_name,
    approver_role,
    applicant_name,
    service_number,
    rank_or_grade,
    directorate,
    leave_type,
    start_date,
    end_date,
    number_of_days,
    reference_id,
    prev_approver_name=None,
    prev_approver_role=None,
    prev_comments=None,
    reason=None,
    async_dispatch=True
):
    """
    Sends an immediate action-required notification email to an approver when a leave/pass
    application is submitted or advanced to their approval step.
    """
    role_display = str(approver_role or "Approver").replace('_', ' ').title()
    subject = f"ACTION REQUIRED: Leave Application {reference_id} - {applicant_name}"

    prev_info_text = ""
    if prev_approver_name:
        prev_role_disp = str(prev_approver_role or "Previous Approver").replace('_', ' ').title()
        prev_info_text = f"\nForwarded / Recommended by: {prev_approver_name} ({prev_role_disp})"
        if prev_comments:
            prev_info_text += f"\nApprover Remarks: \"{prev_comments}\""

    plain_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
DIRECTORATE OF ADMINISTRATION (DOA) - LEAVE & PASS PORTAL

Dear {approver_name or role_display},

A leave/pass application requires your review and approval action.

APPLICATION DETAILS:
------------------------------------------------------------
Reference ID:       {reference_id}
Applicant Name:     {applicant_name}
Service / Staff No: {service_number}
Rank / Grade:       {rank_or_grade or 'N/A'}
Directorate:        {directorate or 'N/A'}
Leave / Pass Type:  {leave_type}
Duration:           {number_of_days} Day(s) ({start_date} to {end_date})
{f"Reason / Purpose:   {reason}" if reason else ""}{prev_info_text}
------------------------------------------------------------

ACTION DIRECTIVE:
Please sign in to the DSA Leave & Pass Portal to review the application parameters and take appropriate approval action.

Portal Link: {PORTAL_BASE_URL}

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""

    prev_html = ""
    if prev_approver_name:
        prev_role_disp = str(prev_approver_role or "Previous Approver").replace('_', ' ').title()
        prev_html = f"""
        <div style="background-color: #f1f5f9; border-left: 4px solid #3b82f6; padding: 10px 15px; margin: 15px 0; border-radius: 4px; font-size: 13px;">
          <strong>Recommended / Forwarded By:</strong> {prev_approver_name} <em>({prev_role_disp})</em>
          {"<br><strong>Remarks:</strong> <em>" + str(prev_comments) + "</em>" if prev_comments else ""}
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #212529; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
  .header {{ background-color: #0b2545; color: #ffffff; padding: 22px 20px; text-align: center; border-bottom: 4px solid #134074; }}
  .header h1 {{ margin: 0; font-size: 19px; letter-spacing: 0.5px; text-transform: uppercase; font-weight: 700; }}
  .header p {{ margin: 4px 0 0 0; font-size: 12px; color: #cbd5e1; }}
  .body {{ padding: 25px; }}
  .badge {{ display: inline-block; background-color: #fef3c7; color: #92400e; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; margin-bottom: 15px; letter-spacing: 0.5px; }}
  .table-box {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
  .table-box td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }}
  .table-box td.label {{ font-weight: 600; color: #475569; width: 35%; background-color: #f8fafc; }}
  .table-box td.val {{ color: #0b2545; font-weight: 500; }}
  .btn {{ display: inline-block; background-color: #0b2545; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 14px; margin-top: 15px; }}
  .footer {{ background-color: #f8fafc; padding: 15px 20px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Defence Space Administration</h1>
    <p>Directorate of Administration &bull; Leave & Pass System</p>
  </div>
  <div class="body">
    <div class="badge">&#9888; ACTION REQUIRED: PENDING APPROVAL</div>
    <p>Dear <strong>{approver_name or role_display}</strong>,</p>
    <p>A leave/pass application has reached your workflow stage and is awaiting your review and approval action.</p>

    <table class="table-box">
      <tr>
        <td class="label">Reference ID:</td>
        <td class="val"><strong>{reference_id}</strong></td>
      </tr>
      <tr>
        <td class="label">Applicant:</td>
        <td class="val">{applicant_name} ({service_number})</td>
      </tr>
      <tr>
        <td class="label">Rank / Grade:</td>
        <td class="val">{rank_or_grade or 'N/A'}</td>
      </tr>
      <tr>
        <td class="label">Directorate:</td>
        <td class="val">{directorate or 'N/A'}</td>
      </tr>
      <tr>
        <td class="label">Leave Type:</td>
        <td class="val"><span style="background-color:#e0f2fe;color:#0369a1;padding:2px 6px;border-radius:3px;font-weight:600;">{leave_type}</span></td>
      </tr>
      <tr>
        <td class="label">Duration:</td>
        <td class="val"><strong>{number_of_days} Day(s)</strong> ({start_date} &rarr; {end_date})</td>
      </tr>
      {"<tr><td class='label'>Reason:</td><td class='val'>" + str(reason) + "</td></tr>" if reason else ""}
    </table>

    {prev_html}

    <div style="text-align: center; margin-top: 20px;">
      <a href="{PORTAL_BASE_URL}" class="btn">Open Approver Dashboard</a>
    </div>
  </div>
  <div class="footer">
    Defence Space Administration &bull; Automated Workflow Notification &bull; Confidential
  </div>
</div>
</body>
</html>
"""
    return dispatch_email(target_email, subject, plain_text, html_content, async_dispatch=async_dispatch)


def send_leave_final_approval_email(
    target_email,
    applicant_name,
    service_number,
    reference_id,
    receipt_number,
    leave_type,
    start_date,
    end_date,
    number_of_days,
    async_dispatch=True
):
    """
    Sends a confirmation email with the receipt number to the applicant when their leave
    application receives final approval and receipt issuance.
    """
    subject = f"LEAVE APPROVED & RECEIPT ISSUED: {reference_id} ({receipt_number})"

    plain_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
DIRECTORATE OF ADMINISTRATION (DOA) - LEAVE & PASS PORTAL

Dear {applicant_name},

Your leave/pass application has been FULLY APPROVED and an official leave receipt has been issued.

APPROVAL DETAILS:
------------------------------------------------------------
Reference ID:       {reference_id}
Receipt Number:     {receipt_number}
Service / Staff No: {service_number}
Leave / Pass Type:  {leave_type}
Approved Duration:  {number_of_days} Day(s) ({start_date} to {end_date})
Approval Status:    APPROVED & ISSUED
------------------------------------------------------------

You may sign in to the DSA Portal at any time to view or print your official Leave Receipt Certificate.

Portal Link: {PORTAL_BASE_URL}

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #212529; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
  .header {{ background-color: #0b2545; color: #ffffff; padding: 22px 20px; text-align: center; border-bottom: 4px solid #10b981; }}
  .header h1 {{ margin: 0; font-size: 19px; letter-spacing: 0.5px; text-transform: uppercase; font-weight: 700; }}
  .header p {{ margin: 4px 0 0 0; font-size: 12px; color: #cbd5e1; }}
  .body {{ padding: 25px; }}
  .badge {{ display: inline-block; background-color: #d1fae5; color: #065f46; padding: 5px 12px; border-radius: 4px; font-size: 12px; font-weight: 700; margin-bottom: 15px; }}
  .receipt-box {{ background-color: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #10b981; padding: 12px 16px; border-radius: 4px; margin: 15px 0; }}
  .receipt-title {{ font-size: 11px; text-transform: uppercase; color: #166534; font-weight: 700; letter-spacing: 0.5px; }}
  .receipt-num {{ font-size: 18px; font-weight: 700; color: #065f46; font-family: 'Courier New', monospace; margin-top: 4px; }}
  .table-box {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
  .table-box td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }}
  .table-box td.label {{ font-weight: 600; color: #475569; width: 35%; background-color: #f8fafc; }}
  .table-box td.val {{ color: #0b2545; font-weight: 500; }}
  .btn {{ display: inline-block; background-color: #0b2545; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 14px; margin-top: 15px; }}
  .footer {{ background-color: #f8fafc; padding: 15px 20px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Defence Space Administration</h1>
    <p>Directorate of Administration &bull; Leave & Pass System</p>
  </div>
  <div class="body">
    <div class="badge">&#10004; APPLICATION APPROVED</div>
    <p>Dear <strong>{applicant_name}</strong>,</p>
    <p>We are pleased to notify you that your leave/pass application has received final approval and your official receipt has been issued.</p>

    <div class="receipt-box">
      <div class="receipt-title">Official Receipt Number</div>
      <div class="receipt-num">{receipt_number}</div>
    </div>

    <table class="table-box">
      <tr>
        <td class="label">Reference ID:</td>
        <td class="val"><strong>{reference_id}</strong></td>
      </tr>
      <tr>
        <td class="label">Leave Type:</td>
        <td class="val">{leave_type}</td>
      </tr>
      <tr>
        <td class="label">Approved Days:</td>
        <td class="val"><strong>{number_of_days} Day(s)</strong></td>
      </tr>
      <tr>
        <td class="label">Period:</td>
        <td class="val">{start_date} &rarr; {end_date}</td>
      </tr>
    </table>

    <div style="text-align: center; margin-top: 20px;">
      <a href="{PORTAL_BASE_URL}" class="btn">View &amp; Print Receipt</a>
    </div>
  </div>
  <div class="footer">
    Defence Space Administration &bull; Directorate of Administration (DOA) Registry Control
  </div>
</div>
</body>
</html>
"""
    return dispatch_email(target_email, subject, plain_text, html_content, async_dispatch=async_dispatch)


def send_leave_rejection_email(
    target_email,
    applicant_name,
    service_number,
    reference_id,
    leave_type,
    rejected_by_name,
    rejected_by_role,
    rejection_reason,
    async_dispatch=True
):
    """
    Sends an immediate rejection notification email to the applicant detailing reasons for disapproval.
    """
    rejected_role_disp = str(rejected_by_role or "Approving Officer").replace('_', ' ').title()
    subject = f"DECISION: Leave Application Disapproved - {reference_id}"

    plain_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
DIRECTORATE OF ADMINISTRATION (DOA) - LEAVE & PASS PORTAL

Dear {applicant_name},

Your leave/pass application {reference_id} ({leave_type}) has been disapproved.

DISAPPROVAL DETAILS:
------------------------------------------------------------
Reference ID:       {reference_id}
Service / Staff No: {service_number}
Leave / Pass Type:  {leave_type}
Disapproved By:     {rejected_by_name} ({rejected_role_disp})
Date:               {datetime.utcnow().strftime('%d %B %Y')}
Reason / Remarks:
{rejection_reason or 'No additional remarks provided.'}
------------------------------------------------------------

Please log in to the DSA Leave & Pass Portal if you need to review your application status or submit an updated request.

Portal Link: {PORTAL_BASE_URL}

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #212529; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
  .header {{ background-color: #0b2545; color: #ffffff; padding: 22px 20px; text-align: center; border-bottom: 4px solid #ef4444; }}
  .header h1 {{ margin: 0; font-size: 19px; letter-spacing: 0.5px; text-transform: uppercase; font-weight: 700; }}
  .header p {{ margin: 4px 0 0 0; font-size: 12px; color: #cbd5e1; }}
  .body {{ padding: 25px; }}
  .badge {{ display: inline-block; background-color: #fee2e2; color: #991b1b; padding: 5px 12px; border-radius: 4px; font-size: 12px; font-weight: 700; margin-bottom: 15px; }}
  .reason-box {{ background-color: #fff1f2; border: 1px solid #fecdd3; border-left: 4px solid #ef4444; padding: 12px 16px; border-radius: 4px; margin: 15px 0; font-size: 13px; color: #881337; }}
  .table-box {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
  .table-box td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }}
  .table-box td.label {{ font-weight: 600; color: #475569; width: 35%; background-color: #f8fafc; }}
  .table-box td.val {{ color: #0b2545; font-weight: 500; }}
  .btn {{ display: inline-block; background-color: #0b2545; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 14px; margin-top: 15px; }}
  .footer {{ background-color: #f8fafc; padding: 15px 20px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Defence Space Administration</h1>
    <p>Directorate of Administration &bull; Leave & Pass System</p>
  </div>
  <div class="body">
    <div class="badge">&#10008; APPLICATION DISAPPROVED</div>
    <p>Dear <strong>{applicant_name}</strong>,</p>
    <p>Your leave/pass application has been reviewed and disapproved.</p>

    <div class="reason-box">
      <strong>Disapproval Remarks / Reason:</strong><br>
      {rejection_reason or 'No remarks provided.'}
    </div>

    <table class="table-box">
      <tr>
        <td class="label">Reference ID:</td>
        <td class="val"><strong>{reference_id}</strong></td>
      </tr>
      <tr>
        <td class="label">Leave Type:</td>
        <td class="val">{leave_type}</td>
      </tr>
      <tr>
        <td class="label">Reviewed By:</td>
        <td class="val">{rejected_by_name} ({rejected_role_disp})</td>
      </tr>
      <tr>
        <td class="label">Date:</td>
        <td class="val">{datetime.utcnow().strftime('%d %B %Y')}</td>
      </tr>
    </table>

    <div style="text-align: center; margin-top: 20px;">
      <a href="{PORTAL_BASE_URL}" class="btn">View Application Status</a>
    </div>
  </div>
  <div class="footer">
    Defence Space Administration &bull; Directorate of Administration (DOA) Registry Control
  </div>
</div>
</body>
</html>
"""
    return dispatch_email(target_email, subject, plain_text, html_content, async_dispatch=async_dispatch)