const API_BASE = "http://localhost:8000";

function showSection(id) {
    document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// Handle URL parameters coming back from Google OAuth Callback
const urlParams = new URLSearchParams(window.location.search);
const paramUserId = urlParams.get('user_id');
const isConnected = urlParams.get('connected');
const authError = urlParams.get('error');

if (authError) {
    alert("Google Authorization Error: " + authError);
    window.history.replaceState({}, document.title, "/");
}

if (paramUserId && isConnected === 'true') {
    localStorage.setItem('userId', paramUserId);
    window.history.replaceState({}, document.title, "/"); // Clean URL
}

let currentUserId = localStorage.getItem('userId');

// Routing Logic
if (!currentUserId) {
    showSection('section-register');
} else {
    // Verify if this user has a token, otherwise prompt connect
    checkUserSession(currentUserId);
}

async function checkUserSession(userId) {
    try {
        const res = await fetch(`${API_BASE}/api/briefing/${userId}`);
        if (res.status === 404) {
            // Check if briefing doesn't exist yet vs user doesn't exist
            showSection('section-dashboard');
            loadBriefing();
        } else if (res.ok) {
            showSection('section-dashboard');
            loadBriefing();
        } else {
            showSection('section-connect');
        }
    } catch (e) {
        showSection('section-dashboard');
        loadBriefing();
    }
}

// Registration Submit
document.getElementById('registerForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
    const schedule = {};

    days.forEach(day => {
        const start = document.getElementById(`${day}_start`).value;
        const end = document.getElementById(`${day}_end`).value;
        if (start && end) {
            schedule[day] = { start: start, end: end };
        }
    });

    const payload = {
        name: document.getElementById('r_name').value,
        email: document.getElementById('r_email').value,
        reg_number: document.getElementById('r_reg').value,
        neopat_id: document.getElementById('r_neopat').value,
        schedule: schedule
    };

    try {
        const res = await fetch(`${API_BASE}/api/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await res.json();
        if (data.id) {
            currentUserId = data.id;
            localStorage.setItem('userId', currentUserId);
            showSection('section-connect');
        } else {
            alert("Registration failed. Email or NeoPAT ID might already exist in the database.");
        }
    } catch (err) {
        alert("Failed to connect to backend server.");
    }
});

// Connect Google Button
document.getElementById('connectGoogleBtn').addEventListener('click', async () => {
    if (!currentUserId) {
        alert("User ID missing. Please register again.");
        showSection('section-register');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/auth/login/${currentUserId}`);
        const data = await res.json();
        if(data.auth_url) {
            window.location.href = data.auth_url;
        } else {
            alert("Failed to initialize Google login.");
        }
    } catch (e) {
        alert("Network error reaching backend login endpoint.");
    }
});

async function loadBriefing() {
    try {
        const res = await fetch(`${API_BASE}/api/briefing/${currentUserId}`);
        if(res.ok) {
            const data = await res.json();
            displayBriefing(data.briefing);
        }
    } catch(e) { console.error(e); }
}

function displayBriefing(text) {
    document.getElementById("placeholderText").style.display = "none";
    const content = document.getElementById("briefingContent");
    content.style.display = "block";
    content.textContent = text;
}

// Generate Briefing Button
document.getElementById('generateBtn').addEventListener('click', async () => {
    const btn = document.getElementById("generateBtn");
    const spinner = document.getElementById("spinner");
    const statusText = document.getElementById("statusText");

    btn.disabled = true; 
    spinner.style.display = "inline-block";
    statusText.innerText = "Scanning your emails and updating calendar...";

    try {
        const res = await fetch(`${API_BASE}/api/briefing/${currentUserId}/generate`, {method: 'POST'});
        const data = await res.json();
        if (res.ok && data.status === "success") {
            displayBriefing(data.briefing);
            statusText.innerText = "Briefing updated successfully!";
        } else {
            if (data.error && data.error.includes("Google account not connected")) {
                alert("Your Google account session expired or wasn't authorized. Please reconnect.");
                showSection('section-connect');
            } else {
                statusText.innerText = "Failed: " + (data.error || "Unknown error");
            }
        }
    } catch (err) {
        statusText.innerText = "Generation failed due to network error.";
    } finally {
        btn.disabled = false; 
        spinner.style.display = "none";
    }
});