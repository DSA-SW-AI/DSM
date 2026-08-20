// static/js/onboarding.js

// 1. Keep track of step registration milestones written to MongoDB
let completedSteps = { 1: false, 2: false, 3: false, 4: false, 5: false };

// 2. Track which specific form block is currently active on the viewport canvas
let currentActiveStep = 0;

/**
 * Automatically generates offEmail from surname, middleName, and firstName inputs.
 */
function generateOfficialEmail() {
    const surname = document.getElementById('surname')?.value.trim().toLowerCase() || '';
    const middleName = document.getElementById('middleName')?.value.trim().toLowerCase() || '';
    const firstName = document.getElementById('firstName')?.value.trim().toLowerCase() || '';
    const offEmailInput = document.getElementById('offEmail');

    if (offEmailInput) {
        if (surname) {
            let localPart = surname;
            if (middleName) {
                localPart += `.${middleName}`;
            } else if (firstName) {
                localPart += `.${firstName}`;
            }
            offEmailInput.value = `${localPart}@dsa.mil.ng`;
        } else {
            offEmailInput.value = '';
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

    const activeCategory = document.getElementById('userCategory')?.value || '';
    const isSpecialRole = (activeCategory === 'it' || activeCategory === 'nysc');

    if (currentActiveStep === 5 || (currentActiveStep === 4 && isSpecialRole)) {
        window.location.href = '/onboarding';
    } else {
        if (isSpecialRole && currentActiveStep === 1) goToStep(3);
        else goToStep(currentActiveStep + 1);
    }
}

// Bind email generation listeners when DOM completes loading
document.addEventListener('DOMContentLoaded', () => {
    const surnameInput = document.getElementById('surname');
    const middleNameInput = document.getElementById('middleName');
    const firstNameInput = document.getElementById('firstName');

    if (surnameInput) surnameInput.addEventListener('input', generateOfficialEmail);
    if (middleNameInput) middleNameInput.addEventListener('input', generateOfficialEmail);
    if (firstNameInput) firstNameInput.addEventListener('input', generateOfficialEmail);

    // Dynamic Junior Staff File Requirement Validation Setup
    const fileNo = document.getElementById('userFileNo')?.value || '';
    if (fileNo.includes('JNR')) {
        const optionalDocsForJnr = [
            'doc_first_degree',
            'doc_nysc',
            'doc_birth',
            'doc_lga',
            'doc_digital_id'
        ];
        optionalDocsForJnr.forEach(id => {
            const input = document.getElementById(id);
            if (input) {
                input.removeAttribute('required');

                // Also update the UI labels in the table from "YES" to "OPTIONAL"
                const tr = input.closest('tr');
                if (tr) {
                    const spanLabel = tr.querySelector('td:nth-child(2) span');
                    if (spanLabel) {
                        spanLabel.textContent = "OPTIONAL";
                        spanLabel.style.color = "#718096";
                    }
                    const selectEl = tr.querySelector('td:nth-child(3) select');
                    if (selectEl) {
                        selectEl.innerHTML = '<option value="no">NO</option><option value="yes">YES</option>';
                        selectEl.removeAttribute('disabled');

                        selectEl.addEventListener('change', function () {
                            if (this.value === 'yes') {
                                input.setAttribute('required', 'true');
                                input.style.opacity = '1';
                            } else {
                                input.removeAttribute('required');
                                input.value = '';
                                input.style.opacity = '0.4';
                            }
                        });

                        // Set UI default
                        selectEl.value = 'yes';
                    }
                }
            }
        });
    }
});

/**
 * Handles sliding between different multi-step forms on the screen layout
 * @param {number} stepNumber - The step index to show (0 is the main checklist)
 */
function goToStep(stepNumber) {
    const activeCategory = document.getElementById('userCategory')?.value || '';
    const isSpecialRole = (activeCategory === 'it' || activeCategory === 'nysc');

    // Intercept sidebar or programmatic navigations for IT and NYSC forms
    if (isSpecialRole && stepNumber === 2 && currentActiveStep === 3) {
        stepNumber = 1;
    }

    console.log(`Transitioning view space to Step: ${stepNumber}`);
    currentActiveStep = stepNumber; // Update active tracker memory

    // ================= DYNAMIC FIELD & AVATAR SYNCHRONIZER =================
    if (stepNumber === 3) {
        const title = document.getElementById('staffTitle')?.value || '';
        const surname = document.getElementById('surname')?.value || '';
        const firstName = document.getElementById('firstName')?.value || '';

        // Update ID Card text labels smoothly matching your design guidelines
        const compiledFullName = `${title} ${surname} ${firstName}`.trim().toUpperCase();
        const idCardElement = document.getElementById('idCardNameDisplay');
        if (idCardElement) idCardElement.textContent = compiledFullName || "NEW PERSONNEL STAFF";

        const appointmentVal = document.getElementById('appt')?.value || '';
        const appointmentDisplay = document.getElementById('idCardAppointmentDisplay');
        if (appointmentDisplay) {
            appointmentDisplay.innerHTML = `<strong>Appointment:</strong> ${appointmentVal.toUpperCase()}`;
        }

        // Inject the passport image file binary preview directly onto your ID Card block element
        const passportFiles = document.getElementById('uploadPassport')?.files;
        const avatarPreviewBox = document.querySelector('.id-avatar-box');

        if (passportFiles && passportFiles.length > 0 && avatarPreviewBox) {
            const fileReader = new FileReader();
            fileReader.onload = function (e) {
                avatarPreviewBox.innerHTML = `<img src="${e.target.result}" style="width:100%; height:100%; object-fit:cover; border-radius:6px;" />`;
            };
            fileReader.readAsDataURL(passportFiles[0]);
        }
    }
    // =======================================================================

    // Hide all multi-step form view wrappers completely
    document.querySelectorAll('.onboarding-view-wrapper').forEach(view => {
        view.style.display = 'none';
    });

    // Smoothly reveal the targeted step view container canvas
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

        // Only scan required elements contained STRICTLY within the current active form
        if (formElement) {
            const fieldsToValidate = formElement.querySelectorAll('input[required], select[required], textarea[required]');
            let isFormValid = true;

            fieldsToValidate.forEach(field => {
                if (!field.value.trim()) {
                    isFormValid = false;
                    field.reportValidity(); // Flags browser warning pop-ups on empty items
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

            // Gather standard text elements (Title, Surname, Appointment, etc.)
            const inputs = formElement.querySelectorAll('input:not([type="file"]), select:not(.doc-status-select), textarea');
            inputs.forEach(input => {
                if (input.type === 'email') {
                    submissionBody.append(input.id, input.value.trim().toLowerCase());
                } else {
                    submissionBody.append(input.id, input.value.trim().toUpperCase());
                }
            });

            // Gather structural profile identity biometrics files
            const passportInput = document.getElementById('uploadPassport');
            const signatureInput = document.getElementById('uploadSignature');

            if (passportInput && passportInput.files.length > 0) {
                submissionBody.append('uploadPassport', passportInput.files[0]);
            }
            if (signatureInput && signatureInput.files.length > 0) {
                submissionBody.append('uploadSignature', signatureInput.files[0]);
            }

            // Dynamically evaluate files depending on category requirements
            const activeCategory = document.getElementById('userCategory')?.value || '';
            const digitalIdInput = document.getElementById('doc_digital_id');

            if (activeCategory === 'it') {
                const siwesInput = document.getElementById('doc_siwes');
                if (siwesInput && siwesInput.files.length > 0) submissionBody.append('doc_siwes', siwesInput.files[0]);
                if (digitalIdInput && digitalIdInput.files.length > 0) submissionBody.append('doc_digital_id', digitalIdInput.files[0]);
            }
            else if (activeCategory === 'nysc') {
                const nyscPostingInput = document.getElementById('doc_nysc_posting');
                if (nyscPostingInput && nyscPostingInput.files.length > 0) submissionBody.append('doc_nysc_posting', nyscPostingInput.files[0]);
                if (digitalIdInput && digitalIdInput.files.length > 0) submissionBody.append('doc_digital_id', digitalIdInput.files[0]);
            }
            else {
                // Loop through and capture your standard 7 Compulsory Table Grid files safely
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

                // Conditionally pack optional Masters / PhD layers if toggled to 'yes'
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
            // --- STEPS 2 & 4 JSON PAYLOAD DATA PACKING PROCESSOR ---
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
        // Dispatch asynchronous network request to your Flask backend route handler
        const response = await fetch('/submit-onboarding-step', {
            method: 'POST',
            headers: headers,
            body: submissionBody
        });

        const result = await response.json();

        if (response.ok) {
            completedSteps[stepNumber] = true;

            // Turn the dashboard milestone checklist indicator from a number into a green checkmark
            const checkIndicator = document.getElementById(`chk_${stepNumber}`);
            if (checkIndicator) {
                checkIndicator.textContent = "✓";
                checkIndicator.classList.remove('number-icon');
                checkIndicator.classList.add('check-icon');
                checkIndicator.style.backgroundColor = "#38A169";
                checkIndicator.style.color = "#FFFFFF";
            }

            // Instantly reveal the signature white success alert popup modal confirmation dialog box
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




// Add these function configurations inside static/js/onboarding.js

function toggleOptionalUpload(selectElement, fileInputId) {
    const fileInput = document.getElementById(fileInputId);
    if (fileInput) {
        if (selectElement.value === 'yes') {
            fileInput.removeAttribute('disabled');
            fileInput.setAttribute('required', 'true');
            fileInput.style.opacity = '1';
        } else {
            fileInput.setAttribute('disabled', 'true');
            fileInput.removeAttribute('required');
            fileInput.value = ''; // Reset file trace string
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
