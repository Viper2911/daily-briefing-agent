import os
import io
import json
import base64
import datetime
import pandas as pd
import pdfplumber
from fastapi import FastAPI
from fastapi.responses import FileResponse
from apscheduler.schedulers.background import BackgroundScheduler
from google import genai
from google.genai import types
from gtts import gTTS
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = FastAPI()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("CRITICAL: GEMINI_API_KEY is not loaded. Check your .env file name and contents.")

ai_client = genai.Client(api_key=api_key)

AUDIO_FILE_PATH = os.path.join(BASE_DIR, "today_briefing.mp3")
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def check_eligibility(text_content):
    truncated_text = text_content[:2000] 
    prompt = f"""
    Evaluate if the following student is eligible for the job described below.
    
    Student Profile:
    - Degree: B.Tech Computer Science and Engineering (CSE)
    - CGPA: 8.14
    - 10th Grade: 95.6%
    - 12th Grade: 89.5%
    
    Rules:
    1. Check if B.Tech CSE is an allowed branch.
    2. Check if the CGPA, 10th, and 12th marks meet the minimum thresholds.
    3. If the text DOES NOT mention specific branch or academic criteria, assume they are eligible (return true).
    
    Job Details:
    {truncated_text}
    
    Return ONLY a JSON object in this format:
    {{"is_eligible": true, "reason": "brief explanation"}}
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        result = json.loads(response.text)
        return result.get("is_eligible", False), result.get("reason", "Error parsing")
    except Exception as e:
        print(f"Eligibility API error: {e}")
        return True, "Fallback: API error"

def fetch_and_parse_emails():
    service = get_gmail_service()
    query = 'subject:("placement" OR "shortlist" OR "PAT" OR "JD") is:unread'
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    
    if not messages:
        return "No unread placement emails found."

    summary_for_llm = ""
    processed_message_ids = []

    for msg in messages:
        msg_id = msg['id']
        processed_message_ids.append(msg_id)
        msg_data = service.users().messages().get(userId='me', id=msg_id).execute()
        payload = msg_data.get('payload', {})
        headers = payload.get('headers', [])
        
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
        
        email_body = ""
        parts = payload.get('parts', [])
        if not parts: 
            data = payload.get('body', {}).get('data')
            if data: email_body = base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8')
        else:
            for part in parts:
                if part.get('mimeType') == 'text/plain':
                    data = part.get('body', {}).get('data')
                    if data: email_body += base64.urlsafe_b64decode(data.encode('UTF-8')).decode('utf-8')
        
        company_context_text = email_body
        excel_shortlist_found = False
        excel_result_text = ""

        for part in parts:
            filename = part.get('filename')
            if filename:
                att_id = part['body'].get('attachmentId')
                if not att_id: continue
                
                att = service.users().messages().attachments().get(userId='me', messageId=msg_id, id=att_id).execute()
                file_bytes = io.BytesIO(base64.urlsafe_b64decode(att['data'].encode('UTF-8')))
                
                if filename.endswith(('.xlsx', '.xls', '.csv')):
                    try:
                        df = pd.read_excel(file_bytes)
                        excel_shortlist_found = True
                        if df.astype(str).apply(lambda col: col.str.contains('h3l3l9d0|5000|Aadil', case=False).any()).any():
                            excel_result_text = f"You HAVE BEEN SHORTLISTED in '{filename}'! "
                        else:
                            excel_result_text = f"You are NOT on the shortlist for '{filename}'. "
                    except: pass
                elif filename.endswith('.pdf'):
                    try:
                        with pdfplumber.open(file_bytes) as pdf:
                            company_context_text += f"\nPDF JD details: {pdf.pages[0].extract_text()}"
                    except: pass

        if excel_shortlist_found:
            summary_for_llm += f"\n--- {subject} ---\n{excel_result_text}\n"
        else:
            is_eligible, reason = check_eligibility(company_context_text)
            if is_eligible:
                summary_for_llm += f"\n--- New Role: {subject} ---\nYou are eligible! ({reason}).\n"

    if processed_message_ids:
        service.users().messages().batchModify(
            userId='me', body={'ids': processed_message_ids, 'removeLabelIds': ['UNREAD']}
        ).execute()

    return summary_for_llm if summary_for_llm.strip() else "New emails arrived, but you did not meet the branch or academic criteria."

def generate_daily_audio():
    print("Executing generation task...")
    today_dt = datetime.datetime.now()
    today_name = today_dt.strftime("%A")
    weekday_idx = today_dt.weekday()
    
    if weekday_idx == 0: schedule = "10:45 AM - 11:35 AM class."
    elif weekday_idx == 1: schedule = "8:00 AM - 8:50 AM class. Need to hurry today!"
    elif weekday_idx == 3: schedule = "8:55 AM - 9:45 AM class."
    else: schedule = "No classes today."

    email_context = fetch_and_parse_emails()
    
    prompt = f"""
    Write a morning briefing script for Aadil. Read him his schedule and placement updates.
    Make it conversational, professional, and under 1 minute. Do not use asterisks or emojis, just raw spoken text.
    
    Context for Today ({today_name}):
    - Classes: {schedule}
    - Evening Routine: Gym at 5:30 PM, Basketball at 6:40 PM, Dinner at 8:30 PM.
    - Placement Updates: {email_context}
    """

    try:
        response = ai_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        script_text = response.text.replace('*', '')

        print("Text generated, converting to audio...")
        tts = gTTS(text=script_text, lang='en', tld='co.in')
        tts.save(AUDIO_FILE_PATH)
        print("Briefing cached successfully.")
    except Exception as e:
        print(f"Failed to generate briefing: {e}")

@app.on_event("startup")
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(generate_daily_audio, 'cron', hour=7, minute=25)
    scheduler.start()

@app.get("/api/briefing/today")
def get_cached_audio():
    if os.path.exists(AUDIO_FILE_PATH):
        return FileResponse(AUDIO_FILE_PATH, media_type="audio/mpeg")
    return {"error": "Audio not generated yet"}

@app.get("/api/test-generate")
def force_generate():
    """Manual trigger to test the AI and Gmail auth without waiting for 7:25 AM"""
    generate_daily_audio()
    return {"status": "success", "message": "Briefing generated! Check your folder for today_briefing.mp3"}