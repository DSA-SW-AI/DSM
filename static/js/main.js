

// static/js/main.js

document.getElementById('signinBtn').addEventListener('click', async (e) => {
    e.preventDefault();

    // 1. Extract values, enforce strict lowercase, and clean trailing space text fields
    const emailInput = document.getElementById('email').value.trim().toLowerCase();
    const passwordInput = document.getElementById('password').value;
    const errorDisplay = document.getElementById('errorMessage');

    // Clear visible alert labels safely
    errorDisplay.style.display = 'none';

    if (!emailInput || !passwordInput) {
        errorDisplay.textContent = "All operational fields must be populated.";
        errorDisplay.style.display = 'block';
        return;
    }

    // 2. Strict Domain Validation Rule Check
    if (!emailInput.endsWith('@dsa.mil.ng')) {
        errorDisplay.textContent = "Unauthorized corporate email domain access signature.";
        errorDisplay.style.display = 'block';
        return;
    }

    console.log(`[DSA AUTH ENGINE] Dispatching verification handshake for: ${emailInput}`);

    try {
        // 3. Dispatch data stream directly upstream to Flask /login endpoint route
        const response = await fetch('/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.getElementById('csrf_token').value
            },
            body: JSON.stringify({ 
                email: emailInput, 
                password: passwordInput 
            })
        });

        const result = await response.json();

        if (response.ok) {
            console.log("[DSA AUTH ENGINE] Handshake successful! Initializing redirection parameters.");
            // Force the window frame to reload into the gatekeeper dashboard router
            window.location.href = "/dashboard";
        } else {
            // Print exact error rejection parameters returned by Flask (401, 403, etc.)
            errorDisplay.textContent = result.message;
            errorDisplay.style.display = 'block';
        }
    } catch (error) {
        console.error("[DSA AUTH ENGINE ERROR] Connection interrupted:", error);
        errorDisplay.textContent = "Network communication fail. Check local server execution logs.";
        errorDisplay.style.display = 'block';
    }
});

// Password input visibility eye-toggle module tracking controls
document.querySelector('.toggle-password-eye').addEventListener('click', function () {
    const passwordInput = document.getElementById('password');
    if (passwordInput && passwordInput.type === 'password') {
        passwordInput.type = 'text';
        this.textContent = '🙈'; 
    } else if (passwordInput) {
        passwordInput.type = 'password';
        this.textContent = '👁';
    }
});


// Add this dynamic helper snippet to automatically generate initials inside your navigation bar circle
const nameString = "{{ user.name }}"; 
const avatarTarget = document.getElementById('navAvatarIcon');

if (avatarTarget && nameString && nameString !== "NEW USER" && nameString !== "Officer") {
    const nameParts = nameString.trim().split(" ");
    let initials = "";
    
    if (nameParts.length >= 2) {
        initials = (nameParts[0].charAt(0) + nameParts[1].charAt(0)).toUpperCase();
    } else if (nameParts.length === 1) {
        initials = nameParts[0].substring(0, 2).toUpperCase();
    }
    
    if (initials) {
        avatarTarget.textContent = initials;
    }
}

/**
 * Utility function to download a QR Code as a PNG image file
 * Handles <canvas>, <img>, or <svg> elements inside containerId
 */
function downloadQrCode(containerId, filename) {
  try {
    const container = document.getElementById(containerId);
    if (!container) {
      alert("QR code element not found.");
      return;
    }

    const fn = filename || "qrcode.png";

    // 1. Try finding <canvas> tag inside container
    const canvasElem = container.querySelector('canvas');
    if (canvasElem) {
      try {
        const dataUrl = canvasElem.toDataURL('image/png');
        if (dataUrl && dataUrl.length > 100) {
          triggerDownload(dataUrl, fn);
          return;
        }
      } catch (e) {
        console.warn("Canvas toDataURL error:", e);
      }
    }

    // 2. Try finding <img> tag inside container
    const imgElem = container.querySelector('img');
    if (imgElem && imgElem.src && imgElem.src.length > 50) {
      if (imgElem.src.startsWith('data:image')) {
        triggerDownload(imgElem.src, fn);
        return;
      } else {
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = imgElem.naturalWidth || imgElem.width || 200;
        tempCanvas.height = imgElem.naturalHeight || imgElem.height || 200;
        const ctx = tempCanvas.getContext('2d');
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
        ctx.drawImage(imgElem, 0, 0);
        triggerDownload(tempCanvas.toDataURL('image/png'), fn);
        return;
      }
    }

    // 3. Try finding <svg> tag inside container
    const svgElem = container.querySelector('svg');
    if (svgElem) {
      const svgData = new XMLSerializer().serializeToString(svgElem);
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const img = new Image();
      const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);

      img.onload = function() {
        canvas.width = img.width || 300;
        canvas.height = img.height || 300;
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        triggerDownload(canvas.toDataURL('image/png'), fn);
      };
      img.src = url;
      return;
    }

    alert("Could not extract QR code image for download.");
  } catch (err) {
    console.error("downloadQrCode error:", err);
    alert("Download failed: " + err.message);
  }
}

function triggerDownload(dataUrl, filename) {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    if (a.parentNode) {
      a.parentNode.removeChild(a);
    }
  }, 200);
}

/**
 * Renders and downloads a full HTML card container (including images, SVG barcodes, QR codes, and text)
 * as a high-definition PNG image file.
 */
async function downloadFullCardAsImage(targetSelector, filename) {
  try {
    let cardElem = null;
    if (typeof targetSelector === 'string') {
      if (targetSelector.startsWith('.') || targetSelector.startsWith('#')) {
        cardElem = document.querySelector(targetSelector);
      } else {
        cardElem = document.getElementById(targetSelector) || document.querySelector(targetSelector);
      }
    } else {
      cardElem = targetSelector;
    }

    if (!cardElem) {
      alert("Card element not found for download.");
      return;
    }

    const fn = filename || "digital_pass_card.png";

    if (typeof html2canvas !== 'undefined') {
      const canvas = await html2canvas(cardElem, {
        scale: 2,
        useCORS: true,
        allowTaint: true,
        backgroundColor: null,
        logging: false
      });
      const dataUrl = canvas.toDataURL('image/png');
      triggerDownload(dataUrl, fn);
    } else {
      alert("Image rendering library is loading. Please try clicking again in a moment.");
    }
  } catch (err) {
    console.error("downloadFullCardAsImage error:", err);
    alert("Could not generate card image download: " + err.message);
  }
}

