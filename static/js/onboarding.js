// static/js/onboarding.js

// 1. Keep track of step registration milestones and saved database payload
let savedOnboardingData = (typeof INITIAL_ONBOARDING_DATA !== 'undefined' && INITIAL_ONBOARDING_DATA) ? INITIAL_ONBOARDING_DATA : {};
let completedSteps = (typeof INITIAL_COMPLETED_STEPS !== 'undefined' && INITIAL_COMPLETED_STEPS) ? {
    1: !!INITIAL_COMPLETED_STEPS['1'],
    2: !!INITIAL_COMPLETED_STEPS['2'],
    3: !!INITIAL_COMPLETED_STEPS['3'],
    4: !!INITIAL_COMPLETED_STEPS['4'],
    5: !!INITIAL_COMPLETED_STEPS['5']
} : { 1: false, 2: false, 3: false, 4: false, 5: false };

// 2. Track which specific form block is currently active on the viewport canvas
let currentActiveStep = 0;

/**
 * Automatically generates offEmail from surname, middleName, and firstName inputs.
 */
function generateOfficialEmail() {
    const surname = document.getElementById('surname')?.value.trim().toLowerCase().replace(/\s+/g, '') || '';
    const firstName = document.getElementById('firstName')?.value.trim().toLowerCase().replace(/\s+/g, '') || '';
    const offEmailInput = document.getElementById('offEmail');

    if (offEmailInput) {
        if (firstName && surname) {
            offEmailInput.value = `${firstName}.${surname}@dsa.mil.ng`;
        } else if (firstName) {
            offEmailInput.value = `${firstName}@dsa.mil.ng`;
        } else if (surname) {
            offEmailInput.value = `${surname}@dsa.mil.ng`;
        } else {
            offEmailInput.value = '';
        }
    }
}

/**
 * Updates the visual indicators on Step 0 roadmap to reflect completed milestones.
 */
function updateChecklistIndicators() {
    for (let s = 1; s <= 5; s++) {
        if (completedSteps[s]) {
            const checkIndicator = document.getElementById(`chk_${s}`);
            if (checkIndicator) {
                checkIndicator.textContent = "✓";
                checkIndicator.classList.remove('number-icon');
                checkIndicator.classList.add('check-icon');
                checkIndicator.style.backgroundColor = "#38A169";
                checkIndicator.style.color = "#FFFFFF";
            }
        }
    }
}

/**
 * Attaches a visual indicator badge and link to a file input if a file already exists on server.
 */
function attachFileBadge(inputElement, fileUrl) {
    if (!inputElement || !fileUrl) return;

    inputElement.removeAttribute('required');

    let badgeId = `status_badge_${inputElement.id}`;
    let badge = document.getElementById(badgeId);
    if (!badge) {
        badge = document.createElement('div');
        badge.id = badgeId;
        badge.className = 'file-saved-badge';
        badge.style.cssText = 'margin-top: 6px; font-size: 12px; color: #15803d; font-weight: 500; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;';
        inputElement.parentNode.appendChild(badge);
    }

    badge.innerHTML = `
        <span style="display:inline-flex; align-items:center; gap:4px; background:#dcfce7; color:#166534; padding:2px 8px; border-radius:4px; font-weight:600;">
            ✓ Saved On File
        </span>
        <a href="${fileUrl}" target="_blank" style="color: #2563eb; text-decoration: underline; font-weight: 500;">
            View Current Document
        </a>
        <span style="color: #64748b; font-size: 11px;">(Select new file only if replacing)</span>
    `;

    // Listen for file changes to give real-time feedback when replacing
    inputElement.addEventListener('change', function () {
        if (this.files && this.files.length > 0) {
            badge.innerHTML = `
                <span style="display:inline-flex; align-items:center; gap:4px; background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-weight:600;">
                    ↺ New File Selected
                </span>
                <span style="color: #334155; font-size: 12px;">${this.files[0].name}</span>
            `;
        }
    });
}

/**
 * Pre-populates form inputs with previously saved details from database.
 */
function populateFormsWithSavedData() {
    if (!savedOnboardingData || typeof savedOnboardingData !== 'object') return;

    // --- 1. Populate Step 1 (Employee Information & File Attachments) ---
    const step1Data = savedOnboardingData.step_1 || {};
    for (const [key, val] of Object.entries(step1Data)) {
        if (!val) continue;
        const input = document.getElementById(key);
        if (!input) continue;

        if (input.type === 'file') {
            if (typeof val === 'string' && (val.startsWith('/attachment/') || val.startsWith('/static/'))) {
                attachFileBadge(input, val);
            }
        } else if (input.tagName === 'SELECT') {
            input.value = val;
            if (key === 'has_masters') toggleOptionalUpload(input, 'doc_masters');
            if (key === 'has_phd') toggleOptionalUpload(input, 'doc_phd');
            if (key === 'has_first_degree') toggleOptionalUpload(input, 'doc_first_degree');
            if (key === 'has_ssce') toggleOptionalUpload(input, 'doc_ssce');
            if (key === 'has_nysc') toggleOptionalUpload(input, 'doc_nysc');
        } else if (input.type !== 'file') {
            // Populate value if input exists
            if (!input.value || input.value.trim() === '') {
                input.value = val;
            }
        }
    }

    // --- 2. Populate Step 2 (Additional Personal Info) ---
    const step2Data = savedOnboardingData.step_2 || {};
    for (const [key, val] of Object.entries(step2Data)) {
        if (!val) continue;
        const input = document.getElementById(key);
        if (input) {
            input.value = val;
        }
    }

    // --- 3. Populate Step 4 (Salary Emolument Record) ---
    const step4Data = savedOnboardingData.step_4 || {};
    for (const [key, val] of Object.entries(step4Data)) {
        if (!val) continue;
        const input = document.getElementById(key);
        if (input) {
            input.value = val;
        }
    }

    // --- 4. Populate Step 5 (Military or Civilian Registration Form) ---
    const step5Data = savedOnboardingData.step_5 || {};
    for (const [key, val] of Object.entries(step5Data)) {
        if (!val) continue;
        const input = document.getElementById(key);
        if (input) {
            input.value = val;
        }
    }
}

/**
 * Dismisses the success modal and transitions to the next step.
 */
function dismissSuccessAndRoute() {
    const successModal = document.getElementById('successModal');
    if (successModal) {
        successModal.style.display = 'none';
    }

    const activeCategory = (document.getElementById('userCategory')?.value || '').toLowerCase().trim();
    const isSpecialRole = (activeCategory === 'it' || activeCategory === 'nysc');
    const isMilitary = (activeCategory === 'military');

    if (currentActiveStep === 5 || (currentActiveStep === 4 && isSpecialRole)) {
        window.location.href = '/onboarding';
    } else {
        if ((isSpecialRole || isMilitary) && currentActiveStep === 1) {
            goToStep(3);
        } else {
            goToStep(currentActiveStep + 1);
        }
    }
}

// Bind listeners and hydrate saved state when DOM completes loading
document.addEventListener('DOMContentLoaded', () => {
    const surnameInput = document.getElementById('surname');
    const middleNameInput = document.getElementById('middleName');
    const firstNameInput = document.getElementById('firstName');

    if (surnameInput) surnameInput.addEventListener('input', generateOfficialEmail);
    if (middleNameInput) middleNameInput.addEventListener('input', generateOfficialEmail);
    if (firstNameInput) firstNameInput.addEventListener('input', generateOfficialEmail);

    // Dynamic Junior Staff File Requirement Validation Setup
    const fileNo = (document.getElementById('dsaFileNo')?.value || document.getElementById('userFileNo')?.value || '').toUpperCase();
    if (fileNo.includes('JNR')) {
        const optionalDocsForJnr = [
            'doc_first_degree',
            'doc_ssce',
            'doc_nysc'
        ];
        optionalDocsForJnr.forEach(id => {
            const input = document.getElementById(id);
            if (input) {
                const tr = input.closest('tr');
                if (tr) {
                    const spanLabel = tr.querySelector('td:nth-child(2) span');
                    if (spanLabel) {
                        spanLabel.textContent = "NO";
                        spanLabel.style.color = "#718096";
                    }
                    const selectEl = tr.querySelector('td:nth-child(3) select');
                    if (selectEl) {
                        selectEl.innerHTML = '<option value="no">NO</option><option value="yes">YES</option>';
                        selectEl.removeAttribute('disabled');
                        selectEl.style.cursor = 'pointer';

                        const hasSavedFile = !!(savedOnboardingData?.step_1?.[id]);
                        const savedSelectVal = savedOnboardingData?.step_1?.[selectEl.id] || (hasSavedFile ? 'yes' : 'no');

                        selectEl.value = savedSelectVal;
                        toggleOptionalUpload(selectEl, id);
                    }
                }
            }
        });
    }

    // 1. Pre-fill all fields with saved details
    populateFormsWithSavedData();

    // 2. Reflect completed milestones on Step 0 roadmap
    updateChecklistIndicators();

    // 3. Resume automatically where the user stopped
    const resumeStepVal = (typeof RESUME_STEP !== 'undefined') ? Number(RESUME_STEP) : 0;
    const hasAnyCompleted = Object.values(completedSteps).some(v => v === true);

    if (hasAnyCompleted && resumeStepVal > 0 && resumeStepVal <= 5) {
        console.log(`Auto-resuming onboarding session at Step ${resumeStepVal}`);
        goToStep(resumeStepVal);
    }
});

/**
 * Handles sliding between different multi-step forms on the screen layout
 * @param {number} stepNumber - The step index to show (0 is the main checklist)
 */
function goToStep(stepNumber) {
    const activeCategory = (document.getElementById('userCategory')?.value || '').toLowerCase().trim();
    const isSpecialRole = (activeCategory === 'it' || activeCategory === 'nysc');
    const isMilitary = (activeCategory === 'military');

    // Intercept backward navigations for forms without Step 2 (IT, NYSC, Military)
    if ((isSpecialRole || isMilitary) && stepNumber === 2 && currentActiveStep === 3) {
        stepNumber = 1;
    }

    console.log(`Transitioning view space to Step: ${stepNumber}`);
    currentActiveStep = stepNumber;

    // ================= DYNAMIC FIELD & AVATAR SYNCHRONIZER =================
    if (stepNumber === 3) {
        const title = document.getElementById('staffTitle')?.value || savedOnboardingData?.step_1?.staffTitle || '';
        const surname = document.getElementById('surname')?.value || savedOnboardingData?.step_1?.surname || '';
        const firstName = document.getElementById('firstName')?.value || savedOnboardingData?.step_1?.firstName || '';

        const compiledFullName = `${title} ${surname} ${firstName}`.trim().toUpperCase();
        const idCardElement = document.getElementById('idCardNameDisplay');
        if (idCardElement) {
            idCardElement.textContent = compiledFullName || "NEW PERSONNEL STAFF";
        }

        const appointmentVal = document.getElementById('appt')?.value || savedOnboardingData?.step_1?.appt || '';
        const appointmentDisplay = document.getElementById('idCardAppointmentDisplay');
        if (appointmentDisplay) {
            appointmentDisplay.innerHTML = `<strong>Appointment:</strong> ${appointmentVal.toUpperCase()}`;
        }

        // Preview passport image: from newly selected file or existing saved file attachment
        const passportFiles = document.getElementById('uploadPassport')?.files;
        const avatarPreviewBox = document.querySelector('.id-avatar-box');

        if (passportFiles && passportFiles.length > 0 && avatarPreviewBox) {
            const fileReader = new FileReader();
            fileReader.onload = function (e) {
                avatarPreviewBox.innerHTML = `<img src="${e.target.result}" style="width:100%; height:100%; object-fit:cover; border-radius:6px;" />`;
            };
            fileReader.readAsDataURL(passportFiles[0]);
        } else if (savedOnboardingData?.step_1?.uploadPassport && avatarPreviewBox) {
            avatarPreviewBox.innerHTML = `<img src="${savedOnboardingData.step_1.uploadPassport}" style="width:100%; height:100%; object-fit:cover; border-radius:6px;" />`;
        }
    }
    // =======================================================================

    // Hide all multi-step form view wrappers
    document.querySelectorAll('.onboarding-view-wrapper').forEach(view => {
        view.style.display = 'none';
    });

    // Reveal the targeted step view container
    const targetView = document.getElementById(`stepView_${stepNumber}`);
    if (targetView) {
        targetView.style.display = 'block';
    } else {
        console.error(`Execution error: Step view container 'stepView_${stepNumber}' missing from DOM.`);
    }
}

/**
 * Validates, compiles, and sends form data (including files) to the Flask backend
 * @param {number} stepNumber - The active step form being submitted
 */
async function submitStepForm(stepNumber) {
    console.log(`Processing submission request block for Step: ${stepNumber}`);

    let submissionBody;
    let headers = {
        'X-CSRFToken': document.getElementById('csrf_token').value
    };

    // 1. ISOLATED CONTAINER BOUNDARY VALIDATOR CHECK RULE
    if (stepNumber !== 3) {
        const formElement = document.getElementById(`form_step_${stepNumber}`);

        if (formElement) {
            const fieldsToValidate = formElement.querySelectorAll('input[required], select[required], textarea[required]');
            let isFormValid = true;

            fieldsToValidate.forEach(field => {
                // If this is a file input that already has an uploaded file saved on the server, skip requirement check
                if (field.type === 'file' && savedOnboardingData?.step_1?.[field.id]) {
                    return;
                }

                if (!field.value.trim()) {
                    isFormValid = false;
                    field.reportValidity();
                }
            });

            if (!isFormValid) {
                console.warn(`Validation block triggered for form_step_${stepNumber}. Post stalled.`);
                return;
            }
        }

        // --- STEP 1 MULTI-FILE ATTACHMENT AND TEXT DATA GATHERING PROCESSOR ---
        if (stepNumber === 1) {
            submissionBody = new FormData();
            submissionBody.append('step', stepNumber);

            // Gather all text elements (Title, Surname, Appointment, Rank, etc.)
            const inputs = formElement.querySelectorAll('input:not([type="file"]), select:not(.doc-status-select), textarea');
            inputs.forEach(input => {
                if (input.type === 'email') {
                    submissionBody.append(input.id, input.value.trim().toLowerCase());
                } else {
                    submissionBody.append(input.id, input.value.trim().toUpperCase());
                }
            });

            // Gather structural profile identity biometrics files if selected
            const passportInput = document.getElementById('uploadPassport');
            const signatureInput = document.getElementById('uploadSignature');

            if (passportInput && passportInput.files.length > 0) {
                submissionBody.append('uploadPassport', passportInput.files[0]);
            }
            if (signatureInput && signatureInput.files.length > 0) {
                submissionBody.append('uploadSignature', signatureInput.files[0]);
            }

            const activeCategory = (document.getElementById('userCategory')?.value || '').toLowerCase().trim();
            const digitalIdInput = document.getElementById('doc_digital_id');

            if (activeCategory === 'it') {
                const siwesInput = document.getElementById('doc_siwes');
                if (siwesInput && siwesInput.files.length > 0) submissionBody.append('doc_siwes', siwesInput.files[0]);
                if (digitalIdInput && digitalIdInput.files.length > 0) submissionBody.append('doc_digital_id', digitalIdInput.files[0]);
            } else if (activeCategory === 'nysc') {
                const nyscPostingInput = document.getElementById('doc_nysc_posting');
                if (nyscPostingInput && nyscPostingInput.files.length > 0) submissionBody.append('doc_nysc_posting', nyscPostingInput.files[0]);
                if (digitalIdInput && digitalIdInput.files.length > 0) submissionBody.append('doc_digital_id', digitalIdInput.files[0]);
            } else if (activeCategory === 'military') {
                const postingInput = document.getElementById('doc_posting_letter');
                const expInput = document.getElementById('doc_experiences');
                const certInput = document.getElementById('doc_certificate');

                if (postingInput && postingInput.files.length > 0) submissionBody.append('doc_posting_letter', postingInput.files[0]);
                if (expInput && expInput.files.length > 0) submissionBody.append('doc_experiences', expInput.files[0]);
                if (certInput && certInput.files.length > 0) submissionBody.append('doc_certificate', certInput.files[0]);
            } else {
                const matrixDocumentIds = [
                    'doc_first_degree', 'doc_ssce', 'doc_primary',
                    'doc_nysc', 'doc_birth', 'doc_lga', 'doc_digital_id'
                ];

                matrixDocumentIds.forEach(id => {
                    const fileInput = document.getElementById(id);
                    if (fileInput && fileInput.files.length > 0) {
                        submissionBody.append(id, fileInput.files[0]);
                    }
                });

                const mastersInput = document.getElementById('doc_masters');
                if (document.getElementById('has_masters')?.value === 'yes' && mastersInput && mastersInput.files.length > 0) {
                    submissionBody.append('doc_masters', mastersInput.files[0]);
                }

                const phdInput = document.getElementById('doc_phd');
                if (document.getElementById('has_phd')?.value === 'yes' && phdInput && phdInput.files.length > 0) {
                    submissionBody.append('doc_phd', phdInput.files[0]);
                }
            }
        } else {
            // --- STEPS 2, 4, & 5 JSON PAYLOAD DATA PACKING PROCESSOR ---
            let dataPayload = {};
            const inputs = formElement.querySelectorAll('input, select, textarea');
            inputs.forEach(input => {
                dataPayload[input.id] = input.value.trim().toUpperCase();
            });

            headers['Content-Type'] = 'application/json';
            submissionBody = JSON.stringify({ step: stepNumber, formData: dataPayload });
        }
    } else {
        // --- STEP 3 ID CARD PREVIEW VIEW VERIFICATION TOKEN ---
        headers['Content-Type'] = 'application/json';
        submissionBody = JSON.stringify({ step: stepNumber, formData: { id_card_status: "GENERATED_AND_VERIFIED" } });
    }

    try {
        const response = await fetch('/submit-onboarding-step', {
            method: 'POST',
            headers: headers,
            body: submissionBody
        });

        const result = await response.json();

        if (response.ok) {
            completedSteps[stepNumber] = true;

            // Update in-memory savedOnboardingData state
            if (!savedOnboardingData) savedOnboardingData = {};
            if (!savedOnboardingData[`step_${stepNumber}`]) savedOnboardingData[`step_${stepNumber}`] = {};

            if (stepNumber !== 1 && stepNumber !== 3) {
                const formElement = document.getElementById(`form_step_${stepNumber}`);
                if (formElement) {
                    const inputs = formElement.querySelectorAll('input, select, textarea');
                    inputs.forEach(input => {
                        savedOnboardingData[`step_${stepNumber}`][input.id] = input.value.trim().toUpperCase();
                    });
                }
            }

            // Update checklist indicator on Step 0 roadmap
            const checkIndicator = document.getElementById(`chk_${stepNumber}`);
            if (checkIndicator) {
                checkIndicator.textContent = "✓";
                checkIndicator.classList.remove('number-icon');
                checkIndicator.classList.add('check-icon');
                checkIndicator.style.backgroundColor = "#38A169";
                checkIndicator.style.color = "#FFFFFF";
            }

            // Show success confirmation popup
            const successModal = document.getElementById('successModal');
            if (successModal) {
                successModal.style.display = 'flex';
            }
        } else {
            alert(`Error saving onboarding state: ${result.message}`);
        }
    } catch (error) {
        console.error("Onboarding server communication fail:", error);
        alert("An error occurred while connecting to the database server.");
    }
}

function toggleOptionalUpload(selectElement, fileInputId) {
    const fileInput = document.getElementById(fileInputId);
    if (fileInput) {
        if (selectElement.value === 'yes') {
            fileInput.removeAttribute('disabled');
            // Only require if not already saved
            if (!savedOnboardingData?.step_1?.[fileInputId]) {
                fileInput.setAttribute('required', 'true');
            }
            fileInput.style.opacity = '1';
        } else {
            fileInput.setAttribute('disabled', 'true');
            fileInput.removeAttribute('required');
            fileInput.value = '';
            fileInput.style.opacity = '0.4';
        }
    }
}



// Handler to process separate, hidden Training Appends
async function submitTrainingFile() {
    const name = document.getElementById('trainingName').value.trim();
    const period = document.getElementById('trainingPeriod').value.trim();
    const fileInput = document.getElementById('uploadTrainingFile').files[0];

    if (!name || !period || !fileInput) {
        alert("All additional training inputs are required.");
        return;
    }

    const payload = new FormData();
    payload.append('trainingName', name);
    payload.append('trainingPeriod', period);
    payload.append('trainingFile', fileInput);

    try {
        const response = await fetch('/append-training-record', { method: 'POST', body: payload });
        if (response.ok) {
            alert("Success: Training record securely appended to your military ledger profile!");
            window.location.reload();
        } else {
            alert("Rejection error pushing custom track.");
        }
    } catch (err) {
        alert("Network failure processing request.");
    }
}
