/**
 * Quick Chat Floating Widget Engine
 */
(function () {
  // Config
  const CURRENT_USER_EMAIL = window.currentUserEmail || '';
  let activeRecipientEmail = null;
  let activeRecipientName = null;
  let activeRecipientRole = null;
  let conversations = [];
  let staffList = [];
  let pollInterval = null;

  // Wait for document to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initQuickChat);
  } else {
    initQuickChat();
  }

  function initQuickChat() {
    // 1. Inject HTML markup dynamically into the page body
    injectChatMarkup();

    // 2. Set up DOM references
    const launcher = document.getElementById('quickChatLauncher');
    const badge = document.getElementById('quickChatBadge');
    const panel = document.getElementById('quickChatPanel');
    const closeBtn = document.getElementById('chatCloseBtn');
    const backBtn = document.getElementById('chatBackBtn');

    const conversationsView = document.getElementById('chatConversationsView');
    const staffView = document.getElementById('chatStaffView');
    const chatScreenView = document.getElementById('chatScreenView');

    const startNewChatBtn = document.getElementById('startNewChatBtn');
    const staffSearchInput = document.getElementById('staffSearchInput');
    const chatInputForm = document.getElementById('chatInputForm');
    const chatTextarea = document.getElementById('chatTextarea');



    let globalUnreadInterval = null;
    let pollInterval = null
    let conversations = [];
    let staffList = [];
    let activeRecipientEmail = null;
    let activeRecipientName = null;
    let activeRecipientRole = null;

    const csrfToken = document.getElementById('quick_chat_csrf_token')?.value || document.querySelector('input[name="csrf_token"]')?.value || document.getElementById('csrf_token')?.value || '';

    // 3. Bind Event Listeners
    launcher.addEventListener('click', () => {
      panel.classList.toggle('active');
      if (panel.classList.contains('active')) {
        showConversationsView();
        // Mark all active pollings or starts
        startGlobalUnreadPolling(false);
      } else {
        stopChatPolling();
        startGlobalUnreadPolling(true);
      }
    });

    closeBtn.addEventListener('click', () => {
      panel.classList.remove('active');
      stopChatPolling();
      startGlobalUnreadPolling(true);
    });

    backBtn.addEventListener('click', () => {
      showConversationsView();
    });

    startNewChatBtn.addEventListener('click', () => {
      showStaffView();
    });

    staffSearchInput.addEventListener('input', (e) => {
      filterStaffList(e.target.value);
    });

    chatInputForm.addEventListener('submit', (e) => {
      e.preventDefault();
      sendMessage();
    });

    chatTextarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // 4. Initial global unread polling (every 10s to update the launcher badge)
    startGlobalUnreadPolling(true);

    // 5. Connect SocketIO if available
    setupSocketIO();

    // --- VIEW TRIGGERS ---

    function showConversationsView() {
      backBtn.style.display = 'none';
      document.getElementById('chatHeaderTitle').style.display = 'block';
      document.getElementById('chatHeaderUserInfo').style.display = 'none';

      conversationsView.classList.add('active');
      staffView.classList.remove('active');
      chatScreenView.classList.remove('active');

      stopChatPolling();
      loadConversations();
    }

    function showStaffView() {
      backBtn.style.display = 'block';
      document.getElementById('chatHeaderTitle').textContent = 'Select Staff';
      document.getElementById('chatHeaderTitle').style.display = 'block';
      document.getElementById('chatHeaderUserInfo').style.display = 'none';

      conversationsView.classList.remove('active');
      staffView.classList.add('active');
      chatScreenView.classList.remove('active');

      staffSearchInput.value = '';
      loadStaff();
    }

    function showChatScreen(recipientEmail, recipientName, recipientRole) {
      activeRecipientEmail = recipientEmail;
      activeRecipientName = recipientName;
      activeRecipientRole = recipientRole;

      backBtn.style.display = 'block';
      document.getElementById('chatHeaderTitle').style.display = 'none';

      const userInfo = document.getElementById('chatHeaderUserInfo');
      userInfo.style.display = 'flex';
      document.getElementById('activeChatAvatar').textContent = recipientName.substring(0, 2).toUpperCase();
      document.getElementById('activeChatName').textContent = recipientName;
      document.getElementById('activeChatRole').textContent = recipientRole || 'Staff';

      conversationsView.classList.remove('active');
      staffView.classList.remove('active');
      chatScreenView.classList.add('active');

      chatTextarea.value = '';

      // Load history
      loadChatHistory(recipientEmail);
      markChatAsRead(recipientEmail);

      // Poll history every 4 seconds
      startChatPolling(recipientEmail);
    }

    // --- DATA FETCHERS ---

    function loadConversations() {
      const scroll = document.getElementById('conversationsListScroll');
      scroll.innerHTML = '<div style="text-align:center; padding:20px; color:#a0aec0;">Loading chats...</div>';

      fetch('/api/chats')
        .then(res => res.json())
        .then(data => {
          conversations = data;
          renderConversations();
          updateUnreadBadge();
        })
        .catch(err => {
          scroll.innerHTML = '<div style="text-align:center; padding:20px; color:#e53e3e;">Failed to load chats</div>';
        });
    }

    function renderConversations() {
      const scroll = document.getElementById('conversationsListScroll');
      scroll.innerHTML = '';

      if (conversations.length === 0) {
        scroll.innerHTML = `
          <div class="chat-empty-state">
            <svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" fill="none"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            <div>No conversations yet</div>
            <button class="chat-start-btn" id="emptyStartBtn">Quick Message</button>
          </div>
        `;
        document.getElementById('emptyStartBtn').addEventListener('click', showStaffView);
        return;
      }

      conversations.forEach(c => {
        const initials = c.other_name.substring(0, 2).toUpperCase();
        const badgeHTML = c.unread_count > 0 ? `<span class="chat-item-badge">${c.unread_count}</span>` : '';
        const rankAppt = c.other_rank && c.other_rank !== 'N/A' ? `${c.other_rank} - ${c.other_appt}` : c.other_appt;

        const item = document.createElement('div');
        item.className = 'chat-list-item';
        item.innerHTML = `
          <div class="chat-item-avatar">${initials}</div>
          <div class="chat-item-content">
            <div class="chat-item-name-row">
              <span class="chat-item-name">${c.other_name}</span>
              <span class="chat-item-time">${formatTime(c.updated_at)}</span>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="chat-item-lastmsg">${c.last_message || 'No messages yet'}</span>
              ${badgeHTML}
            </div>
          </div>
        `;

        item.addEventListener('click', () => {
          showChatScreen(c.other_email, c.other_name, rankAppt);
        });
        scroll.appendChild(item);
      });
    }

    function loadStaff() {
      const scroll = document.getElementById('staffListScroll');
      scroll.innerHTML = '<div style="text-align:center; padding:20px; color:#a0aec0;">Loading staff list...</div>';

      fetch('/api/staff')
        .then(res => res.json())
        .then(data => {
          staffList = data;
          renderStaff(staffList);
        })
        .catch(err => {
          scroll.innerHTML = '<div style="text-align:center; padding:20px; color:#e53e3e;">Failed to load staff</div>';
        });
    }

    function renderStaff(list) {
      const scroll = document.getElementById('staffListScroll');
      scroll.innerHTML = '';

      if (list.length === 0) {
        scroll.innerHTML = '<div style="text-align:center; padding:20px; color:#a0aec0;">No staff found</div>';
        return;
      }

      list.forEach(s => {
        const initials = s.name.substring(0, 2).toUpperCase();
        const rankAppt = s.rank && s.rank !== 'N/A' ? `${s.rank} - ${s.appt}` : s.appt;

        const item = document.createElement('div');
        item.className = 'chat-list-item';
        item.innerHTML = `
          <div class="chat-item-avatar" style="background:#e0e7ff; color:#4f46e5;">${initials}</div>
          <div class="chat-item-content">
            <div class="chat-item-name" style="font-size:13.5px; color:#1a202c;">${s.name}</div>
            <div style="font-size:12px; color:#718096; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
              ${rankAppt} (${s.directorate})
            </div>
          </div>
        `;

        item.addEventListener('click', () => {
          showChatScreen(s.email, s.name, rankAppt);
        });
        scroll.appendChild(item);
      });
    }

    function filterStaffList(query) {
      const q = query.toLowerCase().strip ? query.toLowerCase().strip() : query.toLowerCase().trim();
      const filtered = staffList.filter(s =>
        s.name.toLowerCase().includes(q) ||
        s.email.toLowerCase().includes(q) ||
        s.directorate.toLowerCase().includes(q)
      );
      renderStaff(filtered);
    }

    function loadChatHistory(recipientEmail) {
      fetch(`/api/chats/${recipientEmail}`)
        .then(res => res.json())
        .then(messages => {
          renderMessages(messages);
        });
    }

    function renderMessages(messages) {
      const area = document.getElementById('chatMessagesArea');
      const isAtBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 50;

      area.innerHTML = '';

      if (messages.length === 0) {
        area.innerHTML = '<div style="text-align:center; padding:20px; color:#a0aec0; font-size:12.5px; margin-top:auto;">Send a message to start quick chat!</div>';
        return;
      }

      messages.forEach(m => {
        const isOutgoing = m.sender_email !== activeRecipientEmail;
        const wrapper = document.createElement('div');
        wrapper.className = `chat-bubble-wrapper ${isOutgoing ? 'outgoing' : 'incoming'}`;

        wrapper.innerHTML = `
          <div class="chat-msg-bubble">${escapeHtml(m.text)}</div>
          <div class="chat-msg-meta">
            <span>${formatTime(m.timestamp)}</span>
          </div>
        `;
        area.appendChild(wrapper);
      });

      // Maintain scroll position or snap to bottom
      if (isAtBottom || area.scrollTop === 0) {
        area.scrollTop = area.scrollHeight;
      }
    }

    function sendMessage() {
      const text = chatTextarea.value.trim();
      if (!text || !activeRecipientEmail) return;

      chatTextarea.value = '';

      // Render outgoing bubble immediately for premium instant feedback
      appendOutgoingBubble(text);

      const activeSocket = window.socket || (typeof socket !== 'undefined' ? socket : null);
      if (activeSocket && activeSocket.connected) {
        activeSocket.emit('send_chat_message', {
          recipient_email: activeRecipientEmail,
          text: text
        });
      } else {
        fetch('/api/chats/send', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
          },
          body: JSON.stringify({
            recipient_email: activeRecipientEmail,
            text: text
          })
        })
          .then(res => res.json())
          .then(data => {
            if (data.status === 'success') {
              loadChatHistory(activeRecipientEmail);
            } else {
              console.error("Failed to send message:", data.message);
            }
          });
      }
    }

    function appendOutgoingBubble(text) {
      const area = document.getElementById('chatMessagesArea');

      // If empty state text is visible, remove it
      if (area.innerText.includes('Send a message to start')) {
        area.innerHTML = '';
      }

      const wrapper = document.createElement('div');
      wrapper.className = 'chat-bubble-wrapper outgoing';
      wrapper.innerHTML = `
        <div class="chat-msg-bubble">${escapeHtml(text)}</div>
        <div class="chat-msg-meta">
          <span>Just now</span>
        </div>
      `;
      area.appendChild(wrapper);
      area.scrollTop = area.scrollHeight;
    }

    function markChatAsRead(recipientEmail) {
      fetch(`/api/chats/read/${recipientEmail}`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken
        }
      })
        .then(res => res.json())
        .then(data => {
          if (data.status === 'success') {
            updateUnreadBadge();
          }
        });
    }

    // --- POLLING ENGINES ---

    function startChatPolling(recipientEmail) {
      stopChatPolling();
      pollInterval = setInterval(() => {
        loadChatHistory(recipientEmail);
        // Silently mark read
        fetch(`/api/chats/read/${recipientEmail}`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': csrfToken
          }
        })
          .then(res => res.json())
          .then(data => {
            if (data.status === 'success') {
              updateUnreadBadge();
            }
          });
      }, 4000);
    }

    function stopChatPolling() {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    }

    function startGlobalUnreadPolling(start) {
      if (globalUnreadInterval) clearInterval(globalUnreadInterval);
      if (!start) return;

      globalUnreadInterval = setInterval(updateUnreadBadge, 12000);
      updateUnreadBadge(); // Initial run
    }

    // function updateUnreadBadge() {
    //   fetch('/api/chats')
    //     .then(res => res.json())
    //     .then(chats => {
    //       const totalUnread = chats.reduce((sum, c) => sum + (c.unread_count || 0), 0);
    //       if (totalUnread > 0) {
    //         badge.textContent = totalUnread;
    //         badge.style.display = 'block';
    //       } else {
    //         badge.style.display = 'none';
    //       }
    //     })
    //     .catch(() => { });
    // }

    function updateUnreadBadge() {
      fetch('/api/chats')
        .then(res => res.json())
        .then(chats => {
          const totalUnread = chats.reduce((sum, c) => sum + (c.unread_count || 0), 0);
          console.log("[quick-chat] 📊 Badge update - Total unread:", totalUnread);

          if (totalUnread > 0) {
            badge.textContent = totalUnread;
            badge.style.display = 'block';
            console.log("[quick-chat] ✅ Badge showing:", totalUnread);
          } else {
            badge.style.display = 'none';
            console.log("[quick-chat] ✅ Badge hidden (no unread)");
          }
        })
        .catch(err => {
          console.error("[quick-chat] ❌ Badge fetch failed:", err);
        });
    }

    // --- SOCKET.IO LIVE HOOKS ---

    // function setupSocketIO() {
    //   // Look for a globally active socket object
    //   const activeSocket = window.socket || (typeof socket !== 'undefined' ? socket : null);
    //   if (activeSocket) {
    //     activeSocket.on('receive_chat_message', (msg) => {
    //       if (panel.classList.contains('active')) {
    //         if (chatScreenView.classList.contains('active') &&
    //           (msg.sender_email === activeRecipientEmail || msg.recipient_email === activeRecipientEmail)) {
    //           loadChatHistory(activeRecipientEmail);
    //           markChatAsRead(activeRecipientEmail);
    //         } else {
    //           loadConversations();  // Refresh all conversations + badge
    //           updateUnreadBadge();
    //         }
    //       } else {
    //         // ✅ CHANGE THIS: Load conversations instead of just badge
    //         loadConversations();  // This refreshes unread counts + badge
    //       }
    //     });

    //     activeSocket.on('chat_message_sent', (msg) => {
    //       if (panel.classList.contains('active') && chatScreenView.classList.contains('active') && msg.recipient_email === activeRecipientEmail) {
    //         loadChatHistory(activeRecipientEmail);
    //       }
    //     });
    //   }
    // }


    function setupSocketIO() {
      // Wait for socket to be available and connected
      function attachSocketListeners() {
        const activeSocket = window.socket || (typeof socket !== 'undefined' ? socket : null);

        if (!activeSocket) {
          console.warn("[quick-chat] Socket not available yet, retrying in 500ms...");
          setTimeout(attachSocketListeners, 500);
          return;
        }

        // ✅ Main message listener
        activeSocket.on('receive_chat_message', (msg) => {
          console.log("[quick-chat] 📨 Received message from:", msg.sender_email, "Panel active?", panel.classList.contains('active'));

          if (panel.classList.contains('active')) {
            // Panel is OPEN
            if (chatScreenView.classList.contains('active') &&
              (msg.sender_email === activeRecipientEmail || msg.recipient_email === activeRecipientEmail)) {
              // Currently viewing this conversation
              loadChatHistory(activeRecipientEmail);
              markChatAsRead(activeRecipientEmail);
            } else {
              // Panel open but viewing different conversation
              loadConversations();
              updateUnreadBadge();
            }
          } else {
            // Panel is CLOSED - THIS IS KEY
            console.log("[quick-chat] ✅ Panel closed, refreshing conversations + badge");
            loadConversations();  // This fetches new data and updates badge
          }
        });

        activeSocket.on('chat_message_sent', (msg) => {
          if (panel.classList.contains('active') && chatScreenView.classList.contains('active') && msg.recipient_email === activeRecipientEmail) {
            loadChatHistory(activeRecipientEmail);
          }
        });

        console.log("[quick-chat] ✅ Socket listeners attached successfully");
      }

      // Start attempting to attach listeners
      attachSocketListeners();
    }

    // --- HELPERS ---

    function escapeHtml(text) {
      const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
      };
      return text.replace(/[&<>"']/g, function (m) { return map[m]; });
    }

    function formatTime(isoStr) {
      if (!isoStr) return '';
      try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return '';

        // If today, show HH:MM, otherwise show MM/DD
        const now = new Date();
        if (d.toDateString() === now.toDateString()) {
          return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
      } catch (e) {
        return '';
      }
    }

    function injectChatMarkup() {
      // Check if it already exists to prevent duplicate injection
      if (document.getElementById('quickChatLauncher')) return;

      const container = document.createElement('div');
      container.innerHTML = `
        <!-- Floating chat trigger button -->
        <button class="quick-chat-launcher" id="quickChatLauncher" title="Quick Staff Chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
          </svg>
          <span class="quick-chat-badge" id="quickChatBadge" style="display: none;">0</span>
        </button>

        <!-- Quick Chat Floating Panel container -->
        <div class="quick-chat-panel" id="quickChatPanel">
          <div class="chat-panel-header">
            <div style="display: flex; align-items: center; gap: 10px;">
              <button class="chat-back-btn" id="chatBackBtn" style="display: none;" title="Back">
                <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
              </button>
              <h3 id="chatHeaderTitle">Quick Chat</h3>
              <div class="chat-header-user-info" id="chatHeaderUserInfo" style="display: none;">
                <div class="chat-header-avatar" id="activeChatAvatar">DS</div>
                <div class="chat-header-text">
                  <span class="name" id="activeChatName">Director General</span>
                  <span class="role" id="activeChatRole">Directorate of Administration</span>
                </div>
              </div>
            </div>
            <button class="chat-close-btn" id="chatCloseBtn" title="Close Panel">&times;</button>
          </div>
          
          <div class="chat-panel-body">
            <!-- View 1: Active Conversations List -->
            <div class="chat-panel-view active" id="chatConversationsView">
              <div style="padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #edf2f7; background: #ffffff;">
                <span style="font-weight: 700; font-size: 13px; color: #4a5568; text-transform: uppercase; letter-spacing: 0.05em;">Recent Chats</span>
                <button class="chat-start-btn" id="startNewChatBtn" style="margin-top: 0; padding: 4px 10px; font-size: 11px;">+ New Chat</button>
              </div>
              <div class="chat-list-scroll" id="conversationsListScroll">
                <!-- Loaded dynamically -->
              </div>
            </div>

            <!-- View 2: Staff Search/Selection List -->
            <div class="chat-panel-view" id="chatStaffView">
              <div class="chat-search-bar">
                <input type="text" id="staffSearchInput" placeholder="Search staff by name or department..." autocomplete="off">
              </div>
              <div class="chat-list-scroll" id="staffListScroll">
                <!-- Loaded dynamically -->
              </div>
            </div>

            <!-- View 3: Chat Screen Room -->
            <div class="chat-panel-view" id="chatScreenView">
              <div class="chat-messages-area" id="chatMessagesArea">
                <!-- Conversation flow loaded dynamically -->
              </div>
              
              <div class="chat-panel-footer">
                <form class="chat-input-form" id="chatInputForm">
                  <textarea id="chatTextarea" placeholder="Type message..." required></textarea>
                  <button type="submit" class="chat-send-icon-btn" title="Send Message">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <line x1="22" y1="2" x2="11" y2="13"></line>
                      <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                    </svg>
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(container);
    }
  }
})();
