import os
import json
import time
import schedule
import threading
from flask import Flask, request, redirect
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pytchat import LiveChat
from dotenv import load_dotenv

app = Flask(__name__)

# Load environment variables
load_dotenv()

# Configuration
CONFIG_DIR = 'config'
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, 'credentials.json')
CHANNEL_NAME = '@VortexWizrd'
MESSAGE = "Block"
INTERVAL_MINUTES = 10

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
        SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
        
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
                print(f"Please go to this URL: {auth_url}")
                return redirect(auth_url)
            
            self.save_credentials(creds)
        
        self.youtube = build('youtube', 'v3', credentials=creds)
        return creds
    
    def check_live_status(self):
        try:
            search_response = self.youtube.search().list(
                q=CHANNEL_NAME,
                type='channel',
                part='id'
            ).execute()
            
            if not search_response.get('items'):
                print(f"Channel {CHANNEL_NAME} not found")
                return False
                
            channel_id = search_response['items'][0]['id']['channelId']
            
            search_response = self.youtube.search().list(
                channelId=channel_id,
                eventType='live',
                type='video',
                part='id'
            ).execute()
            
            if search_response.get('items'):
                video_id = search_response['items'][0]['id']['videoId']
                video_response = self.youtube.videos().list(
                    id=video_id,
                    part='liveStreamingDetails'
                ).execute()
                
                if video_response.get('items'):
                    self.chat_id = video_response['items'][0]['liveStreamingDetails']['activeLiveChatId']
                    return True
        except Exception as e:
            print(f"Error checking live status: {e}")
        
        return False
    
    def send_message(self):
        if not self.chat_id:
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
            print(f"Message sent: {MESSAGE}")
            return True
        except Exception as e:
            print(f"Error sending message: {e}")
            return False
    
    def run_scheduled_messages(self):
        schedule.every(INTERVAL_MINUTES).minutes.do(self.send_message)
        
        while True:
            self.is_live = self.check_live_status()
            if self.is_live:
                print(f"{CHANNEL_NAME} is live! Starting message schedule...")
                while self.is_live:
                    schedule.run_pending()
                    time.sleep(1)
                    self.is_live = self.check_live_status()
            else:
                print(f"{CHANNEL_NAME} is not live. Checking again in 1 minute...")
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
    
    # Handle OAuth callback
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
            scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
            redirect_uri=REDIRECT_URI
        )
        
        flow.fetch_token(code=request.args['code'])
        creds = flow.credentials
        bot.save_credentials(creds)
        bot.credentials = creds
        bot.youtube = build('youtube', 'v3', credentials=creds)
        return "Authentication successful! You can now close this tab."
    
    return bot.authenticate()

@app.route('/auth/callback')
def auth_callback():
    return redirect('/')

def run_scheduler():
    if bot.credentials:
        bot.run_scheduled_messages()

if __name__ == '__main__':
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    app.run(host='0.0.0.0', port=5000)