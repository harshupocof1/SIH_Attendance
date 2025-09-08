document.addEventListener('DOMContentLoaded', () => {

    // --- Theme Management ---
    const themeToggle = document.getElementById('themeToggle');
    const themeIcon = document.getElementById('themeIcon');
    const applyTheme = (theme) => {
        if (theme === 'dark') {
            document.body.classList.add('dark-mode');
            themeIcon.textContent = '🌞';
        } else {
            document.body.classList.remove('dark-mode');
            themeIcon.textContent = '🌙';
        }
    };
    const savedTheme = localStorage.getItem('theme') || 'light';
    applyTheme(savedTheme);
    themeToggle.addEventListener('click', () => {
        const newTheme = document.body.classList.contains('dark-mode') ? 'light' : 'dark';
        localStorage.setItem('theme', newTheme);
        applyTheme(newTheme);
    });

    // --- Navigation ---
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.section');
    const showSection = (sectionId) => {
        sections.forEach(s => s.classList.remove('active'));
        navItems.forEach(i => i.classList.remove('active'));
        const targetSection = document.getElementById(sectionId);
        const targetNavItem = document.querySelector(`.nav-item[data-section="${sectionId}"]`);
        if (targetSection) targetSection.classList.add('active');
        if (targetNavItem) targetNavItem.classList.add('active');
    };
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const sectionId = item.getAttribute('data-section');
            if (sectionId) showSection(sectionId);
        });
    });

    // --- Feature Card Click Handlers ---
    // FIX: Simplified the logic. Clicks on cards that are now `<a>` tags with `href` attributes are handled
    // automatically by the browser. This JS now only handles cards that switch sections on the same page.
    document.querySelectorAll('.feature-card[data-section-target]').forEach(card => {
        card.addEventListener('click', () => {
            const sectionTarget = card.getAttribute('data-section-target');
            if (sectionTarget) {
                showSection(sectionTarget);
            }
        });
    });

    // --- QR Code Scanner ---
    const startScanBtn = document.getElementById('start-scan-btn');
    const qrReaderContainer = document.getElementById('qr-reader-container');
    const qrResultEl = document.getElementById('qr-scan-result');
    let html5QrCode = null;

    startScanBtn.addEventListener('click', () => {
        qrReaderContainer.style.display = 'block';
        startScanBtn.style.display = 'none';

        if (!html5QrCode) {
            html5QrCode = new Html5Qrcode("qr-reader");
        }

        const qrCodeSuccessCallback = (decodedText, decodedResult) => {
            qrResultEl.textContent = `Success! Scanned: ${decodedText}`;
            qrResultEl.className = 'mt-2 font-bold success';
            html5QrCode.stop().then(() => {
                qrReaderContainer.style.display = 'none';
                startScanBtn.style.display = 'inline-flex';
            }).catch(err => console.error("Failed to stop QR code scanner.", err));
        };

        const config = { fps: 10, qrbox: { width: 250, height: 250 } };
        html5QrCode.start({ facingMode: "environment" }, config, qrCodeSuccessCallback)
            .catch(err => {
                // FIX: Added console.error to log the specific error, making it much easier to debug camera/permission issues.
                console.error("QR Scanner failed to start:", err);
                qrResultEl.textContent = "Error: Unable to start scanner. Please grant camera permissions.";
                qrResultEl.className = 'mt-2 font-bold error';
                 setTimeout(() => {
                    qrReaderContainer.style.display = 'none';
                    startScanBtn.style.display = 'inline-flex';
                }, 3000);
            });
    });

    // --- Chatbot Logic ---
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const sendChatBtn = document.getElementById('send-chat-btn');

    const addMessage = (text, sender) => {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;
        const p = document.createElement('p');
        p.textContent = text;
        messageDiv.appendChild(p);
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    const getBotResponse = (userInput) => {
        userInput = userInput.toLowerCase();
        if (userInput.includes('attendance')) {
            return "You can mark your attendance by navigating to the 'Attendance' tab and scanning the QR code provided by your instructor.";
        } else if (userInput.includes('emergency') || userInput.includes('help')) {
            return "For emergencies, please go to the 'Emergency' tab to find contact numbers for police, fire, medical, and campus security.";
        } else if (userInput.includes('safety') || userInput.includes('safe')) {
            return "Campus Guardian offers features like Safe Walks and quick access to emergency contacts to enhance your safety on campus.";
        } else if (userInput.includes('hello') || userInput.includes('hi')) {
            return "Hello there! How can I assist you with campus safety today?";
        }
        return "I'm not sure how to answer that. You can ask me about attendance, emergency services, or campus safety tips.";
    };

    const handleChatSend = () => {
        const userInput = chatInput.value.trim();
        if (userInput) {
            addMessage(userInput, 'user');
            chatInput.value = '';
            setTimeout(() => {
                const botResponse = getBotResponse(userInput);
                addMessage(botResponse, 'bot');
            }, 500);
        }
    };
    
    sendChatBtn.addEventListener('click', handleChatSend);
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            handleChatSend();
        }
    });


    // --- Initial Load ---
    showSection('dashboard');
});