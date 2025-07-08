import os
import time
import schedule
import threading
import requests
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
CHANNEL_ID = os.getenv('YOUTUBE_CHANNEL_ID', 'UC0Jl-TWrUBW7N-cNeACryXw')
MESSAGE = "Block"
INTERVAL_MINUTES = 10
MAX_RESPONSE_LENGTH = 100  # Characters
POLLING_INTERVAL = 3  # Seconds between chat checks

# AI Configuration
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
AI_SYSTEM_PROMPT = "You're a helpful assistant in a YouTube live chat. Keep responses short (under 100 characters), casual, and appropriate for stream chat."

# Required OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/youtube.force-ssl',
    'https://www.googleapis.com/auth/youtube.readonly'
]

# Environment variables
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', 'https://bortexwizrd.onrender.com/auth')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')  # Optional API key

os.makedirs(CONFIG_DIR, exist_ok=True)

class YouTubeBot:
    def __init__(self):
        self.youtube = None
        self.chat_id = None
        self.is_live = False
        self.credentials = self.load_credentials()
        self.last_messages = {}
        self.last_poll_time = 0
        self.ai_fail_count = 0

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
                flow = Flow.from_client_config(
                    client_config={
                        "web": {
                            "client_id": CLIENT_ID,
                            "client_secret": CLIENT_SECRET,
                            "redirect_uris": [REDIRECT_URI],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token"
                        }
                    },
                    scopes=SCOPES,
                    redirect_uri=REDIRECT_URI
                )
                auth_url, _ = flow.authorization_url(prompt='consent')
                return redirect(auth_url)
            self.save_credentials(creds)
        self.youtube = build('youtube', 'v3', credentials=creds)
        return creds
    
    def check_live_status(self):
        try:
            search_response = self.youtube.search().list(
                channelId=CHANNEL_ID,
                eventType='live',
                type='video',
                part='id',
                maxResults=1
            ).execute()
            
            if not search_response.get('items'):
                if self.is_live:
                    print("🔴 Stream went offline")
                    self.is_live = False
                return False
                
            video_id = search_response['items'][0]['id']['videoId']
            video_response = self.youtube.videos().list(
                id=video_id,
                part='liveStreamingDetails'
            ).execute()
            
            if video_response.get('items'):
                new_chat_id = video_response['items'][0]['liveStreamingDetails']['activeLiveChatId']
                if new_chat_id != self.chat_id:
                    print(f"🟢 New live chat detected: {new_chat_id}")
                    self.chat_id = new_chat_id
                    self.last_messages = {}
                self.is_live = True
                return True
        except Exception as e:
            print(f"⚠️ Live check error: {str(e)}")
        return False
    
    def generate_ai_response(self, prompt):
        """Generate response with enhanced error handling"""
        try:
            headers = {
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {DEEPSEEK_API_KEY}"} if DEEPSEEK_API_KEY else {})
            }
            
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": AI_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 80,
                "temperature": 0.7
            }
            
            response = requests.post(
                DEEPSEEK_API_URL,
                json=data,
                headers=headers,
                timeout=10
            )
            
            # Debug raw response
            if response.status_code != 200:
                print(f"🚨 API Error {response.status_code}: {response.text[:200]}")
                return None
                
            result = response.json()
            if not result.get('choices'):
                print("⚠️ No choices in response")
                return None
                
            ai_message = result['choices'][0]['message']['content'].strip()
            ai_message = ' '.join(ai_message.split())  # Clean whitespace
            
            # Ensure the response is appropriate for chat
            if len(ai_message) > MAX_RESPONSE_LENGTH:
                ai_message = ai_message[:MAX_RESPONSE_LENGTH-3] + "..."
                
            return ai_message
            
        except Exception as e:
            print(f"⚠️ AI Generation Error: {type(e).__name__} - {str(e)}")
            return None
    
    def process_chat_messages(self):
        if not self.chat_id or time.time() - self.last_poll_time < POLLING_INTERVAL:
            return
            
        self.last_poll_time = time.time()
        
        try:
            response = self.youtube.liveChatMessages().list(
                liveChatId=self.chat_id,
                part="snippet,authorDetails",
                maxResults=50
            ).execute()
            
            for item in response.get('items', []):
                message_id = item['id']
                if message_id in self.last_messages:
                    continue
                    
                self.last_messages[message_id] = time.time()
                
                # Skip bot's own messages and non-commands
                if (item['authorDetails'].get('isChatOwner', False) or 
                    not item['snippet']['displayMessage'].startswith('!ai ')):
                    continue
                
                prompt = item['snippet']['displayMessage'][4:].strip()
                author = item['authorDetails']['displayName']
                
                print(f"⚡ Processing AI request from {author}: {prompt[:50]}...")
                ai_response = self.generate_ai_response(prompt)
                
                if ai_response:
                    self.send_message(f"!@{author} {ai_response}")
                    self.ai_fail_count = 0
                else:
                    self.ai_fail_count += 1
                    if self.ai_fail_count >= 3:
                        self.send_message("⚠️ AI service is currently unavailable. Please try again later!")
            
            # Clean old messages
            self.last_messages = {
                k: v for k, v in self.last_messages.items() 
                if time.time() - v < 300
            }
            
        except Exception as e:
            print(f"⚠️ Chat processing error: {str(e)}")
    
    def send_message(self, message):
        try:
            self.youtube.liveChatMessages().insert(
                part="snippet",
                body={
                    "snippet": {
                        "liveChatId": self.chat_id,
                        "type": "textMessageEvent",
                        "textMessageDetails": {
                            "messageText": message
                        }
                    }
                }
            ).execute()
            print(f"💬 Sent: {message[:50]}...")
            return True
        except Exception as e:
            print(f"🚨 Failed to send message: {str(e)}")
            return False
    
    def run_scheduled_messages(self):
        schedule.every(INTERVAL_MINUTES).minutes.do(
            lambda: self.send_message(MESSAGE)
        )
        
        while True:
            if self.check_live_status():
                self.process_chat_messages()
                schedule.run_pending()
            time.sleep(1)

bot = YouTubeBot()

@app.route('/')
def home():
    if not bot.credentials:
        return redirect('/auth')
    return """
    YouTube AI Bot is running.<br>
    <a href="/test-ai">Test AI</a> | 
    <a href="/test-chat">Test Chat</a>
    """

@app.route('/auth')
def auth():
    if bot.credentials:
        return redirect('/')
    
    if 'code' in request.args:
        flow = Flow.from_client_config(
            client_config={
                "web": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "redirect_uris": [REDIRECT_URI],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token"
                }
            },
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        flow.fetch_token(code=request.args['code'])
        bot.save_credentials(flow.credentials)
        bot.youtube = build('youtube', 'v3', credentials=flow.credentials)
        return "Authentication successful! Bot is starting..."
    return bot.authenticate()

@app.route('/test-ai')
def test_ai():
    test_prompt = "Hello, how are you?"
    response = bot.generate_ai_response(test_prompt)
    return f"""
    <h1>AI Test</h1>
    <p><strong>Prompt:</strong> {test_prompt}</p>
    <p><strong>Response:</strong> {response or 'No response generated'}</p>
    <p>Check console for detailed logs</p>
    """

@app.route('/test-chat')
def test_chat():
    if not bot.chat_id:
        return "No active chat session", 400
    bot.send_message("!Bot test message")
    return "Test message sent to chat"

if __name__ == '__main__':
    threading.Thread(
        target=bot.run_scheduled_messages,
        daemon=True
    ).start()
    app.run(host='0.0.0.0', port=5000)
