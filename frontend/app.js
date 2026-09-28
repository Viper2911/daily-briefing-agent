const API_BASE = "http://localhost:8000";

let currentUserId = localStorage.getItem('userId');

function showSection(id) {
    document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get('connected') === 'true' && urlParams.get('user_id')) {
    currentUserId = urlParams.get('user_id');
    localStorage.setItem('userId', currentUserId);
    window.history.replaceState({}, document.title, "/");
}

if (!currentUserId) {
    showSection('section-register');
} else {
    showSection('section-dashboard');
    loadBriefing();
}

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
        alert("Registration failed. Email or NeoPAT ID might already exist.");
    }
});

document.getElementById('connectGoogleBtn').addEventListener('click', async () => {
    const res = await fetch(`${API_BASE}/api/auth/login/${currentUserId}`);
    const data = await res.json();
    if(data.auth_url) {
        window.location.href = data.auth_url;
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

document.getElementById('generateBtn').addEventListener('click', async () => {
    const btn = document.getElementById("generateBtn");
    const spinner = document.getElementById("spinner");
    const statusText = document.getElementById("statusText");

    btn.disabled = true; spinner.style.display = "inline-block";
    statusText.innerText = "Scanning your emails and updating calendar...";

    try {
        const res = await fetch(`${API_BASE}/api/briefing/${currentUserId}/generate`, {method: 'POST'});
        const data = await res.json();
        if (data.status === "success") {
            displayBriefing(data.briefing);
            statusText.innerText = "Briefing updated!";
        } else {
            statusText.innerText = "Failed: " + (data.error || "Unknown error");
        }
    } catch (err) {
        statusText.innerText = "Generation failed.";
    } finally {
        btn.disabled = false; spinner.style.display = "none";
    }
});