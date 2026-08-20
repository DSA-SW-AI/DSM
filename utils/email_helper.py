import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ssl






# ================= GALAXY BACKBONE IMPLICIT PRODUCTION SETTINGS =================
SMTP_SERVER = "mail.govmail.gbb.com.ng"      # Public GovMail domain handle
SMTP_PORT = 465                               # Secure Outgoing channel
GOVMAIL_USER = "paul.ikeh@dsa.mil.ng"    # Your authorized sender account handle
GOVMAIL_PASS = "Gunnexzy4!!!!"      # Your password or App-Specific Token Key
# =================================================================================

def send_credentials_email(target_alt_email, official_login_email, plain_password, service_number):
    """
    Establishes an immediate, Implicit SSL session with the Galaxy Backbone GovMail 
    Gateway over the public internet to securely deliver account credentials.
    """
    try:
        # 1. Initialize the multipart MIME envelope
        msg = MIMEMultipart()
        msg['From'] = GOVMAIL_USER  
        msg['To'] = target_alt_email
        msg['Subject'] = f"RESTRICTED: Official Account Credentials - {service_number.upper()}"

        body_text = f"""DEFENCE SPACE ADMINISTRATION (DSA)
PORTAL REGISTRY CONTROL SECTOR

Acknowledge,

An official user login profile has been successfully generated for your service track parameters within the DSA system ecosystem.

Please find your secure network deployment access credentials detailed below:

------------------------------------------------------------
OFFICIAL SIGN-IN EMAIL: {official_login_email}
GENERATED TEMPORARY PASSWORD: {plain_password}
------------------------------------------------------------

SECURITY DIRECTIVE:
1. Access the portal landing interface framework via your local intranet workstation.
2. Input these credentials to access the Step-by-Step Staff/Personnel Onboarding Checklist.
3. Do not distribute, store in plain text, or share these system access markers with unauthorized personnel.

Respectfully,
DSA Directorate of Administration (DOA) Registry Control.
"""
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))

        # 2. FIXED: Create a secure default SSL context required for public internet transport lines
        context = ssl.create_default_context()

        # 3. FIXED: Open connection via SMTP_SSL passing the internet verification context
        # Increased the timeout margin to 20 seconds to give the public DNS routing plenty of time to resolve
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=20)
        
        # 4. Perform public encrypted handshake immediately
        server.ehlo() 
        
        # 5. Authenticate credentials cleanly against the public gateway cluster
        server.login(GOVMAIL_USER, GOVMAIL_PASS)
        
        # 6. Push payload package downstream to target recipient mailbox
        server.sendmail(GOVMAIL_USER, target_alt_email, msg.as_string())
        server.quit()
        
        print(f"GovMail Dispatch System: Verification parameters successfully sent to {target_alt_email}")
        return True
        
    except Exception as e:
        # Prints out the EXACT network trace error returned by the public internet routing lines directly to your VS Code terminal
        print(f"CRITICAL: GovMail SMTP Public Internet transmission failed: {str(e)}")
        return False