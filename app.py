import os
import time
import schedule
import threading
from flask import Flask, request, redirect
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv

app = Flask(__name__)

# Load environment variables
load_dotenv()

# Configuration
CONFIG_DIR = 'config'
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, 'credentials.json')
CHANNEL_ID = 'UC0Jl-TWrUBW7N-cNeACryXw'  # Using channel ID instead of handle
MESSAGE = "Block"
INTERVAL_MINUTES = 10

# Required OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/youtube.force-ssl',
    'https://www.googleapis.com/auth/youtube.readonly'
]

# Environment variables
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', 'https://bortexwizrd.onrender.com/auth')

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

class YouTubeBot:
    def __init__(self):
        self.youtube = None
        self.chat_id = None
        self.is_live = False
        self.credentials = self.load_credentials()
        
    def load_credentials(self):
        if os.path.exists(CREDENTIALS_PATH):
            return Credentials.from_authorized_user_file(CREDENTIALS_PATH)
        return None
    
    def save_credentials(self, creds):
        with open(CREDENTIALS_PATH, 'w') as token:
            token.write(creds.to_json())
    
    def authenticate(self):        
        creds = self.load_credentials()
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                client_config = {
                    "web": {
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                        "redirect_uris": [REDIRECT_URI],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"
                    }
                }
                
                flow = Flow.from_client_config(
                    client_config=client_config,
                    scopes=SCOPES,
                    redirect_uri=REDIRECT_URI
                )
                auth_url, _ = flow.authorization_url(prompt='consent')
                print(f"Auth URL: {auth_url}")
                return redirect(auth_url)
            
            self.save_credentials(creds)
        
        self.youtube = build('youtube', 'v3', credentials=creds)
        return creds
    
    def check_live_status(self):
        try:
            print(f"🔍 Checking live status for channel: {CHANNEL_ID}")
            search_response = self.youtube.search().list(
                channelId=CHANNEL_ID,
                eventType='live',
                type='video',
                part='id',
                maxResults=1
            ).execute()
            
            if not search_response.get('items'):
                print("No live streams found")
                return False
                
            video_id = search_response['items'][0]['id']['videoId']
            video_response = self.youtube.videos().list(
                id=video_id,
                part='liveStreamingDetails'
            ).execute()
            
            if video_response.get('items'):
                self.chat_id = video_response['items'][0]['liveStreamingDetails']['activeLiveChatId']
                print(f"🟢 LIVE! Chat ID: {self.chat_id}")
                return True
                
        except Exception as e:
            print(f"⚠️ Error checking live status: {str(e)}")
        return False
    
    def send_message(self):
        if not self.chat_id:
            print("❌ No active chat ID")
            return False
            
        try:
            self.youtube.liveChatMessages().insert(
                part="snippet",
                body={
                    "snippet": {
                        "liveChatId": self.chat_id,
                        "type": "textMessageEvent",
                        "textMessageDetails": {
                            "messageText": MESSAGE
                        }
                    }
                }
            ).execute()
            print(f"✉️ Message sent: {MESSAGE}")
            return True
        except Exception as e:
            print(f"⚠️ Error sending message: {str(e)}")
            return False
    
    def run_scheduled_messages(self):
        schedule.every(INTERVAL_MINUTES).minutes.do(self.send_message)
        
        while True:
            self.is_live = self.check_live_status()
            if self.is_live:
                print(f"🎥 Stream is LIVE! Starting message schedule...")
                while self.is_live:
                    schedule.run_pending()
                    time.sleep(1)
                    self.is_live = self.check_live_status()
            else:
                print(f"🔴 Stream OFFLINE. Checking again in 60s...")
                time.sleep(60)

bot = YouTubeBot()

@app.route('/')
def home():
    if not bot.credentials:
        return redirect('/auth')
    return "YouTube Bot is running. Check logs for activity."

@app.route('/auth')
def auth():
    if bot.credentials:
        return redirect('/')
    
    if 'code' in request.args:
        client_config = {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        }
        
        flow = Flow.from_client_config(
            client_config=client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        
        flow.fetch_token(code=request.args['code'])
        creds = flow.credentials
        bot.save_credentials(creds)
        bot.credentials = creds
        bot.youtube = build('youtube', 'v3', credentials=creds)
        return "Authentication successful! Bot is starting..."
    
    return bot.authenticate()

def run_scheduler():
    if bot.credentials:
        bot.run_scheduled_messages()

if __name__ == '__main__':
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    app.run(host='0.0.0.0', port=5000)
