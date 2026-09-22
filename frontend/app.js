const API_BASE = "http://localhost:8000";

function displayBriefing(text) {
    const placeholder = document.getElementById("placeholderText");
    const content = document.getElementById("briefingContent");
    placeholder.style.display = "none";
    content.style.display = "block";
    content.textContent = text;
}

async function checkStatus() {
    const statusText = document.getElementById("statusText");
    try {
        const res = await fetch(`${API_BASE}/api/briefing/status`);
        const data = await res.json();
        if (data.exists && data.briefing) {
            displayBriefing(data.briefing);
            statusText.innerText = "Today's briefing loaded.";
        } else {
            statusText.innerText = "No briefing found for today. Click below to generate.";
        }
    } catch (e) {
        statusText.innerText = "Cannot connect to backend. Ensure FastAPI is running on port 8000.";
    }
}

async function triggerManualGeneration() {
    const btn = document.getElementById("generateBtn");
    const btnText = document.getElementById("btnText");
    const spinner = document.getElementById("spinner");
    const statusText = document.getElementById("statusText");

    btn.disabled = true;
    spinner.style.display = "inline-block";
    btnText.innerText = "Processing Emails & Schedule...";
    statusText.innerText = "Contacting Gmail API and Gemini 2.0 Flash...";

    try {
        const res = await fetch(`${API_BASE}/api/test-generate`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.status === "success" && data.briefing) {
            displayBriefing(data.briefing);
            statusText.innerText = "Briefing updated successfully!";
        } else {
            statusText.innerText = data.message || "Failed to generate briefing.";
        }
    } catch (err) {
        statusText.innerText = "Generation failed. Check terminal logs.";
    } finally {
        btn.disabled = false;
        spinner.style.display = "none";
        btnText.innerText = "Generate Briefing Now";
    }
}

window.onload = checkStatus;