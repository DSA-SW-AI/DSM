

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
