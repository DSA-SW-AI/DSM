// static/js/personnel.js
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('addStaffModal');
    const openBtn = document.querySelector('.add-staff-btn');
    const closeBtn = document.getElementById('closeModalBtn');
    const generateBtn = document.getElementById('generatePasswordBtn');
    const passwordInput = document.getElementById('staffPassword');
    const createStaffForm = document.getElementById('createStaffForm');

    // 1. OPEN MODAL ACTION CONTROL
    if (openBtn && modal) {
        openBtn.addEventListener('click', (e) => {
            e.preventDefault();
            modal.style.display = 'flex';
        });
    } else {
        console.log("Add button hidden or modal missing due to active account permissions logic block rules.");
    }

    // 2. CLOSE MODAL ACTION CONTROL
    if (closeBtn && modal && createStaffForm) {
        closeBtn.addEventListener('click', () => {
            modal.style.display = 'none';
            createStaffForm.reset(); 
        });
    }

    // 3. BACKGROUND MASK CLICK DISMISSAL
    window.addEventListener('click', (e) => {
        if (modal && e.target === modal) {
            modal.style.display = 'none';
            if (createStaffForm) createStaffForm.reset();
        }
    });

    // 4. RANDOM PASSWORD GENERATION INTERACTION ENGINE
    if (generateBtn && passwordInput) {
        generateBtn.addEventListener('click', (e) => {
            // Only generate password if the field is currently blank or containing placeholder values
            if (passwordInput.value === "" || passwordInput.value === passwordInput.placeholder) {
                e.preventDefault(); 
                
                const uppercase = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
                const lowercase = "abcdefghijklmnopqrstuvwxyz";
                const numbers = "0123456789";
                
                const randChar = (set) => set.charAt(Math.floor(Math.random() * set.length));
                
                // Construct a randomized military credential match string structure: e.g. @@Qabcde123???
                let pass = "@@"; 
                pass += randChar(uppercase);
                pass += randChar(lowercase) + randChar(lowercase) + randChar(lowercase) + randChar(lowercase) + randChar(lowercase);
                pass += randChar(numbers) + randChar(numbers) + randChar(numbers);
                pass += "???"; 
                
                passwordInput.value = pass;
                passwordInput.style.borderColor = "#E2A114"; 
                passwordInput.style.fontWeight = "bold";
                generateBtn.textContent = "Submit and Create Login Record";
            }
        });
    }

    // 5. ASYNCHRONOUS DATA CAPTURE AND POST TRANSACTION TO FLASK
    if (createStaffForm) {
        createStaffForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            if (passwordInput.value === "" || passwordInput.value === passwordInput.placeholder) {
                alert("Please click the Generate button to create a password first.");
                return;
            }

            const payload = {
                category: document.getElementById('staffCategory').value,
                directorate: document.getElementById('staffDirectorate').value,
                service_number: document.getElementById('staffServiceNumber').value.trim(),
                alternate_email: document.getElementById('staffAltEmail').value.trim(),
                password: passwordInput.value
            };

            const submitBtn = createStaffForm.querySelector('button[type="submit"]');
            const origBtnText = submitBtn ? submitBtn.textContent : '';
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Creating Account...';
            }

            try {
                const response = await fetch('/add-staff', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();

                if (response.ok) {
                    showAppToast(result.message || "Success! Credentials dispatched.", 'success');
                    modal.style.display = 'none';
                    createStaffForm.reset();
                    generateBtn.textContent = "Generate Login Password";
                    setTimeout(() => window.location.reload(), 600);
                } else {
                    showAppToast("Registration Error: " + result.message, 'error');
                }
            } catch (error) {
                console.error("Network Error:", error);
                showAppToast("An error occurred during database communications.", 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = origBtnText || 'Create Account';
                }
            }
        });
    }
}); // <-- FIXED: Added missing closing bracket for DOMContentLoaded block


function openReassignModal(email, name, serviceNo, category) {
    console.log("DSA Reassignment Engine Fired. Target:", email, name);

    const modal = document.getElementById('reassignModal');
    if (modal) {
        // 1. Inject values safely into the form inputs
        document.getElementById('reassignUserEmail').value = email;
        document.getElementById('reassignStaffName').value = name;
        document.getElementById('reassignServiceNo').value = serviceNo;
        document.getElementById('reassignCategory').value = category;
        
        // 2. Instantly reveal the hidden popup overlay panel
        modal.style.display = 'flex';
    } else {
        console.error("Critical Layout Error: Element container 'reassignModal' missing from DOM tree.");
    }
}

function closeReassignModal() {
    const modal = document.getElementById('reassignModal');
    if (modal) {
        modal.style.display = 'none';
    }
    const form = document.getElementById('reassignDirectorateForm');
    if (form) form.reset();
}

async function triggerAdditionalDocumentRequest(email) {
    if(!confirm(`Are you sure you want to request an additional document from this user (${email})?`)) return;
    try {
        const headers = { 'Content-Type': 'application/json' };
        const csrfElem = document.getElementById('csrf_token');
        if (csrfElem) headers['X-CSRFToken'] = csrfElem.value;

        const response = await fetch('/request-additional-document', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ email: email })
        });
        const data = await response.json();
        if (response.ok) {
            alert("Successfully flagged user for an additional document request.");
            window.location.reload();
        } else {
            alert(`Failed: ${data.message || 'Server rejected request'}`);
        }
    } catch(err) { 
        console.error(err);
        alert("Communication failure while requesting additional document."); 
    }
}


