// static/js/responsive.js
document.addEventListener("DOMContentLoaded", function () {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) return; // Exit if there is no sidebar on the page

    // 1. Create and inject the overlay backdrop
    let overlay = document.querySelector(".sidebar-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.className = "sidebar-overlay";
        document.body.appendChild(overlay);
    }

    // 2. Create and inject the hamburger button inside the top-navbar
    const navbar = document.querySelector(".top-navbar");
    if (navbar && !document.getElementById("hamburgerBtn")) {
        const hamburgerBtn = document.createElement("button");
        hamburgerBtn.id = "hamburgerBtn";
        hamburgerBtn.className = "hamburger-btn";
        hamburgerBtn.setAttribute("aria-label", "Toggle Navigation Menu");
        hamburgerBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
            </svg>
        `;
        // Prepend as the first child of top-navbar so it appears on the far left
        navbar.insertBefore(hamburgerBtn, navbar.firstChild);

        // Bind hamburger toggle click event
        hamburgerBtn.addEventListener("click", function () {
            sidebar.classList.add("active");
            overlay.classList.add("active");
        });
    }

    // 3. Create and inject the sidebar close button inside the sidebar
    if (sidebar && !document.getElementById("sidebarCloseBtn")) {
        const closeBtn = document.createElement("button");
        closeBtn.id = "sidebarCloseBtn";
        closeBtn.className = "sidebar-close-btn";
        closeBtn.setAttribute("aria-label", "Close Menu");
        closeBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        `;
        // Prepend it inside the sidebar
        sidebar.insertBefore(closeBtn, sidebar.firstChild);

        // Bind close button click event
        closeBtn.addEventListener("click", function () {
            sidebar.classList.remove("active");
            overlay.classList.remove("active");
        });
    }

    // 4. Bind overlay click event (clicking outside the sidebar closes it)
    overlay.addEventListener("click", function () {
        sidebar.classList.remove("active");
        overlay.classList.remove("active");
    });
});
