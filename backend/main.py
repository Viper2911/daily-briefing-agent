import os
import io
import json
import time
import base64
import datetime
import pandas as pd
import pdfplumber
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai.errors import APIError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from dotenv import load_dotenv

from backend.database import engine, Base, SessionLocal, User, get_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = FastAPI(title="Placement Briefing Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_key = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=api_key)

CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar.events'
]
REDIRECT_URI = "http://localhost:8000/api/auth/callback"

CANDIDATE_MODELS = ["gemini-3.6-flash", "gemini-3.6-pro", "gemini-2.5-flash"]

class UserCreate(BaseModel):
    name: str
    email: str
    reg_number: str
    neopat_id: str
    schedule: dict

def generate_with_retry(prompt: str, config=None, max_attempts=2):
    for model_name in CANDIDATE_MODELS:
        for attempt in range(max_attempts):
            try:
                return ai_client.models.generate_content(model=model_name, contents=prompt, config=config)
            except APIError as e:
                if "503" in str(e) or "429" in str(e):
                    time.sleep((attempt + 1) * 2)
                else:
                    break
            except Exception:
                break
    raise RuntimeError("All models exhausted.")

def get_google_services(user: User):
    if not user.google_token:
        raise ValueError("User has not connected Google account.")
    
    creds = Credentials.from_authorized_user_info(user.google_token, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        db = SessionLocal()
        db_user = db.query(User).filter(User.id == user.id).first()
        db_user.google_token = json.loads(creds.to_json())
        db.commit()
        db.close()
        
    return build('gmail', 'v1', credentials=creds), build('calendar', 'v3', credentials=creds)

def extract_calendar_details(text_content):
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    prompt = f"""
    Extract schedule details from the placement email. Current Date: {current_date}. 
    Return ONLY JSON with keys: "is_scheduled" (boolean), "title", "start_datetime" (ISO8601), "end_datetime" (ISO8601), "location".
    Email Content: {text_content[:2000]}
    """
    try:
        res = generate_with_retry(prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
        return json.loads(res.text.replace('```json', '').replace('```', '').strip())
    except:
        return {"is_scheduled": False}

def fetch_and_parse_emails(user: User, gmail_service, cal_service):
    today_str = datetime.datetime.now().strftime("%Y/%m/%d")
    query = f'from:("No Reply CDC Info" OR "noreply") after:{today_str}'
    
    results = gmail_service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    if not messages:
        return "No CDC placement emails found today for your account."

    summary_for_llm = ""
    for msg in messages:
        msg_id = msg['id']
        msg_data = gmail_service.users().messages().get(userId='me', id=msg_id).execute()
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
        
        excel_found, is_shortlisted, filename_found = False, False, ""
        for part in parts:
            filename = part.get('filename')
            if filename and filename.endswith(('.xlsx', '.xls', '.csv')):
                att_id = part['body'].get('attachmentId')
                if not att_id: continue
                att = gmail_service.users().messages().attachments().get(userId='me', messageId=msg_id, id=att_id).execute()
                file_bytes = io.BytesIO(base64.urlsafe_b64decode(att['data'].encode('UTF-8')))
                try:
                    df = pd.read_excel(file_bytes)
                    excel_found = True
                    filename_found = filename
                    if df.astype(str).apply(lambda col: col.str.contains(user.neopat_id, case=False).any()).any():
                        is_shortlisted = True
                except:
                    pass

        company_context = subject + "\n" + email_body
        if excel_found:
            if is_shortlisted:
                summary_for_llm += f"\n✅ SHORTLISTED: {subject} ({filename_found})\n"
                cal_details = extract_calendar_details(company_context)
                if cal_details.get("is_scheduled"):
                    event = {
                        'summary': cal_details.get('title'),
                        'location': cal_details.get('location'),
                        'start': {'dateTime': cal_details.get('start_datetime'), 'timeZone': 'Asia/Kolkata'},
                        'end': {'dateTime': cal_details.get('end_datetime'), 'timeZone': 'Asia/Kolkata'},
                        'colorId': '9'
                    }
                    cal_service.events().insert(calendarId='primary', body=event).execute()
                    summary_for_llm += f"📅 Added to Calendar: {cal_details.get('start_datetime')}.\n"
            else:
                summary_for_llm += f"\n❌ Not Shortlisted: {subject} ({filename_found})\n"
        else:
            summary_for_llm += f"\n📢 NOTICE: {subject}\nDetails: {email_body.replace(chr(10), ' ').strip()[:300]}...\n"

    return summary_for_llm

def generate_briefing_for_user(db: Session, user: User):
    try:
        gmail_service, cal_service = get_google_services(user)
    except Exception as e:
        return f"Error connecting to Google Services for {user.email}: {e}"

    today_dt = datetime.datetime.now()
    today_name = today_dt.strftime("%A")
    date_str = today_dt.strftime("%Y-%m-%d")
    
    user_schedule_map = user.schedule or {}
    day_schedule = user_schedule_map.get(today_name)
    class_msg, class_sync_msg = "No classes scheduled for today.", ""
    
    if day_schedule and day_schedule.get("start") and day_schedule.get("end"):
        start_time = day_schedule.get("start")
        end_time = day_schedule.get("end")
        class_msg = f"{start_time} - {end_time} class."
        
        try:
            time_min, time_max = f"{date_str}T00:00:00+05:30", f"{date_str}T23:59:59+05:30"
            events = cal_service.events().list(calendarId='primary', timeMin=time_min, timeMax=time_max, q='University Class', singleEvents=True).execute().get('items', [])
            if not events:
                event = {
                    'summary': 'University Class',
                    'start': {'dateTime': f"{date_str}T{start_time}:00+05:30", 'timeZone': 'Asia/Kolkata'},
                    'end': {'dateTime': f"{date_str}T{end_time}:00+05:30", 'timeZone': 'Asia/Kolkata'},
                    'colorId': '5'
                }
                cal_service.events().insert(calendarId='primary', body=event).execute()
                class_sync_msg = "✅ Synced recurring class to Calendar."
        except: pass

    email_context = fetch_and_parse_emails(user, gmail_service, cal_service)
    
    prompt = f"""
    Create a professional daily morning briefing for {user.name} (Email: {user.email}, NeoPAT ID: {user.neopat_id}).
    Include:
    1. Greeting ({today_name})
    2. Class Schedule: {class_msg} {class_sync_msg}
    3. Placement Updates for {user.email}: {email_context}
    Format strictly with markdown headers and bullets.
    """
    
    try:
        response = generate_with_retry(prompt)
        user.latest_briefing = response.text.strip()
        db.commit()
        return user.latest_briefing
    except Exception as e:
        return str(e)

def generate_all_briefings():
    db = SessionLocal()
    users = db.query(User).filter(User.google_token.isnot(None)).all()
    for user in users:
        generate_briefing_for_user(db, user)
    db.close()

@app.on_event("startup")
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(generate_all_briefings, 'cron', hour=7, minute=25)
    scheduler.start()

@app.post("/api/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = User(
        name=user.name, 
        email=user.email, 
        reg_number=user.reg_number, 
        neopat_id=user.neopat_id, 
        schedule=user.schedule
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return {"id": db_user.id, "message": "User registered successfully"}

@app.get("/api/auth/login/{user_id}")
def login(user_id: int):
    flow = Flow.from_client_secrets_file(CREDENTIALS_PATH, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline', state=str(user_id))
    return {"auth_url": auth_url}

@app.get("/api/auth/callback")
def auth_callback(state: str, code: str = None, error: str = None, db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(url=f"http://localhost:3000/?error={error}")
    
    user_id = int(state)
    flow = Flow.from_client_secrets_file(CREDENTIALS_PATH, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(code=code)
    creds = flow.credentials
    
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.google_token = json.loads(creds.to_json())
        db.commit()
    return RedirectResponse(url=f"http://localhost:3000/?user_id={user_id}&connected=true")

@app.get("/api/briefing/{user_id}")
def get_briefing(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: return JSONResponse(status_code=404, content={"error": "User not found"})
    if not user.latest_briefing: return JSONResponse(status_code=404, content={"error": "Briefing not generated yet"})
    return {"briefing": user.latest_briefing}

@app.post("/api/briefing/{user_id}/generate")
def force_generate(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: return JSONResponse(status_code=404, content={"error": "User not found"})
    if not user.google_token: return JSONResponse(status_code=400, content={"error": "Google account not connected"})
    
    briefing = generate_briefing_for_user(db, user)
    return {"status": "success", "briefing": briefing}