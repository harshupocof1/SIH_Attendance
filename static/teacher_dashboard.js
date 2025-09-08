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
    document.querySelectorAll('.feature-card[data-section-target]').forEach(card => {
        card.addEventListener('click', () => {
            const sectionTarget = card.getAttribute('data-section-target');
            if (sectionTarget) {
                showSection(sectionTarget);
            }
        });
    });

    // --- Attendance QR Code Generation Logic ---
    let qrRefreshInterval = null;
    const socket = io('/teacher');
    const qrImage = document.getElementById('qr-image');
    const qrMessage = document.getElementById('qr-message');
    const checkpointSelect = document.getElementById('checkpoint-select');
    const lastRefreshTimeEl = document.getElementById('last-refresh-time');

    const generateQRCode = () => {
        const selectedCheckpoint = checkpointSelect.value;
        const today = new Date().toISOString().slice(0, 10);
        socket.emit('request_qr_code', { date: today, checkpoint: selectedCheckpoint });
    };

    const updateQRCode = (data) => {
        const imageSrc = `data:image/png;base64,${data.image}`;
        qrImage.src = imageSrc;
        lastRefreshTimeEl.textContent = `Last refreshed: ${new Date().toLocaleTimeString()}`;
        qrMessage.textContent = 'QR Code generated successfully. It will automatically refresh.';
    };

    const handleQRSectionVisibility = () => {
        const generateQrSection = document.getElementById('generate_qr');
        if (generateQrSection.classList.contains('active')) {
            generateQRCode();
            qrRefreshInterval = setInterval(generateQRCode, 10000); // Refresh every 10 seconds
        } else {
            clearInterval(qrRefreshInterval);
            qrRefreshInterval = null;
        }
    };
    
    // Listen for navigation changes to start/stop the QR refresh timer
    document.querySelectorAll('.nav-item, .feature-card').forEach(item => {
        item.addEventListener('click', handleQRSectionVisibility);
    });

    // Handle incoming QR code from the server
    socket.on('new_qr_code', (data) => {
        updateQRCode(data);
    });

    // Handle checkpoint change
    if (checkpointSelect) {
        checkpointSelect.addEventListener('change', generateQRCode);
    }
});