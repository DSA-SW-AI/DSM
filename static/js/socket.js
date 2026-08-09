// socket.js — harmonized notification handler

if (!window.socket) {
    window.socket = io();
}
const socket = window.socket;
const user = window.CURRENT_USER;
const DASHBOARD_TYPE = window.DASHBOARD_TYPE || "all"; // "leave" | "parade" | "all"


// ── Dedup guard (prevents showing same notification twice in 5 s) ──────────
const shownNotifications = new Set();

function isDuplicate(data) {
    const key = `${data.type}_${data._id || data.parade_id || data.referenceId || Date.now()}`;
    if (shownNotifications.has(key)) return true;
    shownNotifications.add(key);
    setTimeout(() => shownNotifications.delete(key), 5000);
    return false;
}

// ── Audio Notification Beep (Synthesized via Web Audio API) ──────────────────
function playBeepSound() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();

        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // A5 note (880Hz)

        gainNode.gain.setValueAtTime(0.12, audioCtx.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35); // Decay over 350ms

        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        oscillator.start();
        oscillator.stop(audioCtx.currentTime + 0.35);
    } catch (e) {
        console.warn("[socket.js] Audio Context error / block:", e);
    }
}

// ── Browser Native Desktop Notification ─────────────────────────────────────
function showBrowserNotification(title, message, clickUrl = null) {
    if (!("Notification" in window)) return;

    if (Notification.permission === "granted") {
        const options = {
            body: message,
            icon: "/static/favicon.ico",
            requireInteraction: false
        };
        const notification = new Notification(title, options);
        if (clickUrl) {
            notification.onclick = function(event) {
                event.preventDefault();
                window.focus();
                window.location.href = clickUrl;
            };
        }
    }
}

// ── Room join ─────────────────────────────────────────────────────────────
socket.on("connect", () => {
    // Request desktop notification permission on connection
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    if (user && user.service_number) {
        const safeId = user.service_number.replace(/\//g, "_");

        socket.emit("join_rooms", {
            service_number: safeId,
            role: user.role,
            directorate: user.directorate,
        });

        socket.emit("join", { room: `USER_${safeId}` });
    } else {
        console.warn("[socket.js] No user data — socket connected but no room joined");
    }
});

socket.on("room_joined", (data) => console.log("📌 Joined room:", data.room));
socket.on("connect_error", (err) => console.error("Socket error:", err));
socket.on("disconnect", () => console.log("❌ Socket disconnected"));

// ── Main notification router ──────────────────────────────────────────────
socket.on("new_notification", (data) => {
    if (isDuplicate(data)) {
        console.log("[socket.js] Duplicate — skipping:", data.type);
        return;
    }

    // Play notification beep sound
    playBeepSound();

    // Trigger browser native alert notification
    let title = "DCS Paperless Notification";
    let body = data.message || "You have a new action item pending.";
    let clickUrl = "/";

    if (data.type === "parade") {
        title = "📋 Parade State Action Required";
        const paradeId = data.parade_id || data._id;
        clickUrl = paradeId ? `/view_parade_state/${paradeId}` : "/dashboard_parade_state";
    } else if (
        data.type === "leave_approval" ||
        data.type === "application_approved_step" ||
        data.type === "action_required" ||
        data.type === "receipt_issued"
    ) {
        title = "📋 Leave & Pass Action Required";
        const id = data._id || data.applicationId;
        clickUrl = id ? `/view/${id}` : "/dashboard_main";
    } else if (data.type === "document") {
        title = "📄 New Document Forwarded";
        const id = data._id;
        clickUrl = id ? `/open_document/${id}` : "/documents_content";
    }

    showBrowserNotification(title, body, clickUrl);

    routeNotification(data);
});

function routeNotification(data) {
    const type = data.type;

    if (type === "parade") {
        if (DASHBOARD_TYPE === "leave") {
            console.log("[socket.js] Parade notification ignored — leave dashboard active");
            return;
        }
        showParadeApprovalModal(data);

    } else if (
        type === "leave_approval" ||
        type === "application_approved_step" ||
        type === "action_required" ||
        type === "receipt_issued"
    ) {
        if (DASHBOARD_TYPE === "parade") {
            return;
        }
        showPendingApprovalModal(data);

    } else if (type === "document") {
        showDocumentNotificationModal(data);
    } else {
        console.log("[socket.js] Unknown notification type:", type);
        // Don't show anything for unknown types — prevents cross-modal bleed
    }
}

// ─────────────────────────────────────────────────────────────────────────
// LEAVE / PASS MODAL
// ─────────────────────────────────────────────────────────────────────────
function showPendingApprovalModal(data) {
    console.log("🎯 showPendingApprovalModal:", data.referenceId);

    document.getElementById("pendingApprovalModal")?.remove();

    const dateDisplay = data.date || new Date().toISOString().split("T")[0];
    const leaveTypeDisplay = data.leave_type || "N/A";
    const dirDisplay = data.directorate || "N/A";
    const refId = data.referenceId || "N/A";

    document.body.insertAdjacentHTML("beforeend", `
        <div id="pendingApprovalModal" style="
            position:fixed;top:0;left:0;right:0;bottom:0;
            background:rgba(0,0,0,0.6);
            display:flex;align-items:center;justify-content:center;
            z-index:9999;">
            <div style="
                background:#fff;padding:30px 25px;border-radius:10px;
                max-width:400px;width:90%;text-align:center;
                box-shadow:0 4px 12px rgba(0,0,0,0.2);
                font-family:'Inter',Arial,sans-serif;
                border-left:5px solid #ffc107;">

                <div style="margin-bottom:15px;">
                    <span style="background:#ffc107;color:#000;padding:5px 15px;
                        border-radius:20px;font-size:12px;font-weight:bold;">
                        📋 LEAVE &amp; PASS
                    </span>
                </div>

                <h3 style="margin-bottom:10px;color:#333;">Pending Approval</h3>
                <p style="margin-bottom:5px;color:#666;font-size:13px;">
                    <strong>Ref:</strong> ${refId}
                </p>
                <p style="margin-bottom:20px;color:#555;font-size:14px;">${data.message}</p>

                <div style="background:#f8f9fa;padding:15px;border-radius:8px;
                    margin-bottom:20px;text-align:left;">
                    <div style="display:grid;grid-template-columns:1fr 2fr;
                        gap:8px;font-size:13px;">
                        <span style="color:#666;">📅 Date:</span>
                        <span style="color:#333;font-weight:500;">${dateDisplay}</span>
                        <span style="color:#666;">📋 Leave type:</span>
                        <span style="color:#333;font-weight:500;text-transform:capitalize;">
                            ${leaveTypeDisplay}
                        </span>
                        <span style="color:#666;">🏢 Directorate:</span>
                        <span style="color:#333;font-weight:500;">${dirDisplay}</span>
                        <span style="color:#666;">👤 From:</span>
                        <span style="color:#333;font-weight:500;">
                            ${data.triggeredBy || "System"}
                        </span>
                    </div>
                </div>

                <div style="display:flex;gap:10px;justify-content:center;">
                    <button id="_leaveModalViewBtn" style="
                        padding:12px 20px;flex:1;cursor:pointer;font-size:14px;
                        font-weight:600;border:none;border-radius:8px;color:#fff;
                        background:linear-gradient(135deg,#28a745,#20c997);">
                        👁️ View details
                    </button>
                    <button id="_leaveModalLaterBtn" style="
                        padding:12px 20px;flex:1;cursor:pointer;font-size:14px;
                        font-weight:600;border:none;border-radius:8px;color:#fff;
                        background:linear-gradient(135deg,#6c757d,#5a6268);">
                        ⏰ Later
                    </button>
                </div>
            </div>
        </div>
    `);

    document.getElementById("_leaveModalViewBtn").addEventListener("click", () => {
        const id = data._id || data.applicationId;
        sessionStorage.setItem("notificationHandled", "true");
        window.location.href = id ? `/view/${id}` : "/dashboard_main";
    });

    document.getElementById("_leaveModalLaterBtn").addEventListener("click", () => {
        // document.getElementById("pendingApprovalModal")?.remove();
        sessionStorage.setItem("notificationHandled", "true");
        window.location.href = "/dashboard_main";
    });
}

// ─────────────────────────────────────────────────────────────────────────
// PARADE STATE MODAL
// ─────────────────────────────────────────────────────────────────────────
function showParadeApprovalModal(data) {
    console.log("🎯 showParadeApprovalModal:", data.action, data.referenceId);

    document.getElementById("paradeApprovalModal")?.remove();

    const actionConfig = {
        submitted: { title: "New parade state submitted", color: "#17a2b8", badge: "SUBMITTED" },
        pending_officer: { title: "Awaiting your approval (SO2)", color: "#fd7e14", badge: "SO2 REVIEW" },
        pending_dd: { title: "Awaiting your approval (DD)", color: "#6f42c1", badge: "DD REVIEW" },
        documentation: { title: "Documentation required", color: "#6c757d", badge: "DOCUMENTATION" },
    };

    const cfg = actionConfig[data.action] || {
        title: "Parade state notification",
        color: "#ffc107",
        badge: "PARADE STATE",
    };

    const dateDisplay = data.date || "N/A";
    const batchDisplay = data.batch || "N/A";
    const dirDisplay = data.directorate || "N/A";
    const notifId = data.notification_id || data._id;

    document.body.insertAdjacentHTML("beforeend", `
        <div id="paradeApprovalModal" style="
            position:fixed;top:0;left:0;right:0;bottom:0;
            background:rgba(0,0,0,0.6);
            display:flex;align-items:center;justify-content:center;
            z-index:9999;">
            <div style="
                background:#fff;padding:30px 25px;border-radius:10px;
                max-width:450px;width:90%;text-align:center;
                box-shadow:0 4px 12px rgba(0,0,0,0.2);
                font-family:Arial,sans-serif;
                border-left:5px solid ${cfg.color};">

                <div style="margin-bottom:15px;">
                    <span style="background:${cfg.color};color:#fff;padding:5px 12px;
                        border-radius:20px;font-size:12px;font-weight:bold;
                        letter-spacing:0.5px;">
                        ${cfg.badge}
                    </span>
                </div>

                <h3 style="margin-bottom:15px;color:#333;">${cfg.title}</h3>
                <p style="margin-bottom:10px;color:#555;font-size:14px;">${data.message}</p>

                <div style="background:#f8f9fa;padding:12px;border-radius:5px;
                    margin-bottom:15px;text-align:left;">
                    <p style="margin:4px 0;color:#666;font-size:13px;">
                        <strong>📅 Date:</strong> ${dateDisplay}
                    </p>
                    <p style="margin:4px 0;color:#666;font-size:13px;">
                        <strong>🔄 Batch:</strong> ${batchDisplay}
                    </p>
                    <p style="margin:4px 0;color:#666;font-size:13px;">
                        <strong>🏢 Directorate:</strong> ${dirDisplay}
                    </p>
                </div>

                <p style="margin-bottom:20px;color:#999;font-size:12px;">
                    From: ${data.triggeredBy || "System"}
                </p>

                <div style="display:flex;gap:10px;justify-content:center;">
                    <button id="_paradeModalViewBtn" style="
                        padding:10px 20px;flex:1;cursor:pointer;font-size:14px;
                        font-weight:600;border:none;border-radius:5px;
                        color:#fff;background:${cfg.color};">
                        View parade state
                    </button>
                    <button id="_paradeModalLaterBtn" style="
                        padding:10px 20px;flex:1;cursor:pointer;font-size:14px;
                        border:none;border-radius:5px;
                        background:#e9ecef;color:#333;">
                        Later
                    </button>
                </div>
            </div>
        </div>
    `);

    document.getElementById("_paradeModalViewBtn").addEventListener("click", () => {
        const paradeId = data.parade_id || data._id;
        sessionStorage.setItem("notificationHandled", "true");
        window.location.href = paradeId ? `/view_parade_state/${paradeId}` : "/dashboard_parade_state";
    });

    document.getElementById("_paradeModalLaterBtn").addEventListener("click", () => {
        //    document.getElementById("paradeApprovalModal")?.remove();
        sessionStorage.setItem("notificationHandled", "true");
        window.location.href = "/dashboard_parade_state";

    });
}

// ─────────────────────────────────────────────────────────────────────────
// DOCUMENT NOTIFICATION MODAL
// ─────────────────────────────────────────────────────────────────────────
function showDocumentNotificationModal(data) {
    console.log("🎯 showDocumentNotificationModal:", data._id);

    document.getElementById("documentNotificationModal")?.remove();

    const subjectDisplay = data.message || "New document received";
    const remarkDisplay = data.remark || "";
    const senderDisplay = data.triggeredBy || "System";
    const docId = data._id;

    document.body.insertAdjacentHTML("beforeend", `
        <div id="documentNotificationModal" style="
            position:fixed;top:0;left:0;right:0;bottom:0;
            background:rgba(0,0,0,0.6);
            display:flex;align-items:center;justify-content:center;
            z-index:9999;">
            <div style="
                background:#fff;padding:30px 25px;border-radius:10px;
                max-width:400px;width:90%;text-align:center;
                box-shadow:0 4px 12px rgba(0,0,0,0.2);
                font-family:'Inter',Arial,sans-serif;
                border-left:5px solid #3b82f6;">

                <div style="margin-bottom:15px;">
                    <span style="background:#dbeafe;color:#1e40af;padding:5px 15px;
                        border-radius:20px;font-size:12px;font-weight:bold;">
                        📄 DOCUMENT INBOX
                    </span>
                </div>

                <h3 style="margin-bottom:10px;color:#333;">New Document Received</h3>
                <p style="margin-bottom:20px;color:#555;font-size:14px;line-height:1.4;">${subjectDisplay}</p>

                ${remarkDisplay ? `
                <div style="background:#f8f9fa;padding:12px;border-radius:6px;
                    margin-bottom:20px;text-align:left;font-size:13px;color:#4b5563;border-left:3px solid #cbd5e1;">
                    <strong>Remark:</strong> "${remarkDisplay}"
                </div>` : ''}

                <p style="margin-bottom:20px;color:#9ca3af;font-size:12px;">
                    Forwarded by: ${senderDisplay}
                </p>

                <div style="display:flex;gap:10px;justify-content:center;">
                    <button id="_docModalOpenBtn" style="
                        padding:12px 20px;flex:1;cursor:pointer;font-size:14px;
                        font-weight:600;border:none;border-radius:8px;color:#fff;
                        background:linear-gradient(135deg,#3b82f6,#2563eb);">
                        👁️ Open Document
                    </button>
                    <button id="_docModalCloseBtn" style="
                        padding:12px 20px;flex:1;cursor:pointer;font-size:14px;
                        font-weight:600;border:none;border-radius:8px;color:#374151;
                        background:#f3f4f6;">
                        Dismiss
                    </button>
                </div>
            </div>
        </div>
    `);

    document.getElementById("_docModalOpenBtn").addEventListener("click", () => {
        document.getElementById("documentNotificationModal")?.remove();
        window.location.href = docId ? `/open_document/${docId}` : "/documents_content";
    });

    document.getElementById("_docModalCloseBtn").addEventListener("click", () => {
        document.getElementById("documentNotificationModal")?.remove();
    });
}