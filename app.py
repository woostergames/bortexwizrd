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
CHANNEL_ID = 'UC0Jl-TWrUBW7N-cNeACryXw'  # Your channel ID
MESSAGE = "Block"
INTERVAL_MINUTES = 10

# OpenRouter AI Configuration
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
AI_MODEL = "deepseek/deepseek-r1"  # Using DeepSeek R1 model
MAX_RESPONSE_LENGTH = 100  # Characters
POLLING_INTERVAL = 3  # Seconds between chat checks

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
        self.last_messages = {}  # Track processed messages
        self.last_poll_time = 0
        
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
                print(f"🔑 Auth URL: {auth_url}")
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
                print("🔴 No live streams found")
                return False
                
            video_id = search_response['items'][0]['id']['videoId']
            video_response = self.youtube.videos().list(
                id=video_id,
                part='liveStreamingDetails'
            ).execute()
            
            if video_response.get('items'):
                new_chat_id = video_response['items'][0]['liveStreamingDetails']['activeLiveChatId']
                if new_chat_id != self.chat_id:
                    print(f"🟢 NEW LIVE SESSION! Chat ID: {new_chat_id}")
                    self.chat_id = new_chat_id
                    self.last_messages = {}  # Reset message history
                return True
                
        except Exception as e:
            print(f"⚠️ Error checking live status: {str(e)}")
        return False
    
    def generate_ai_response(self, prompt):
        """Generate an AI response using DeepSeek R1 via OpenRouter"""
        try:
            print(f"🧠 Generating AI response for: {prompt[:50]}...")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": REDIRECT_URI,
                "X-Title": "YouTube AI Bot"
            }
            
            data = {
                "model": AI_MODEL,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 80,
                "temperature": 0.7
            }
            
            start_time = time.time()
            response = requests.post(OPENROUTER_API_URL, json=data, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            ai_message = result['choices'][0]['message']['content'].strip()
            
            # Truncate if needed
            if len(ai_message) > MAX_RESPONSE_LENGTH:
                ai_message = ai_message[:MAX_RESPONSE_LENGTH-3] + "..."
                
            print(f"🤖 AI Response ({time.time()-start_time:.2f}s): {ai_message}")
            return ai_message
            
        except requests.exceptions.RequestException as e:
            print(f"🚨 AI API Error: {str(e)}")
            return None
        except Exception as e:
            print(f"⚠️ Unexpected AI Error: {str(e)}")
            return None
    
    def process_chat_messages(self):
        """Check for new messages and process AI commands"""
        if not self.chat_id:
            print("❌ No active chat ID - skipping message processing")
            return
            
        try:
            current_time = time.time()
            if current_time - self.last_poll_time < POLLING_INTERVAL:
                return
            self.last_poll_time = current_time
            
            print(f"🔍 Polling chat {self.chat_id}...")
            response = self.youtube.liveChatMessages().list(
                liveChatId=self.chat_id,
                part="snippet,authorDetails",
                maxResults=50
            ).execute()
            
            new_messages = 0
            ai_requests = 0
            
            for item in response.get('items', []):
                message_id = item['id']
                if message_id in self.last_messages:
                    continue
                    
                self.last_messages[message_id] = current_time
                new_messages += 1
                
                message = item['snippet']['displayMessage']
                author = item['authorDetails']['displayName']
                
                # Skip if message is from the bot itself
                if item['authorDetails'].get('isChatOwner', False):
                    continue
                
                # Process AI commands
                if message.startswith('!ai ') and len(message) > 4:
                    ai_requests += 1
                    prompt = message[4:].strip()
                    print(f"⚡ AI Request from {author}: {prompt[:50]}...")
                    
                    ai_response = self.generate_ai_response(prompt)
                    if ai_response:
                        response_message = f"!@{author} {ai_response}"
                        self.send_message(custom_message=response_message)
            
            if new_messages > 0:
                print(f"📨 Processed {new_messages} new messages ({ai_requests} AI requests)")
            
            # Clean up old message IDs
            self.last_messages = {
                msg_id: t for msg_id, t in self.last_messages.items()
                if current_time - t < 300  # 5 minute retention
            }
            
        except Exception as e:
            print(f"⚠️ Chat processing error: {str(e)}")
    
    def send_message(self, custom_message=None):
        if not self.chat_id:
            print("❌ No active chat ID - can't send message")
            return False
            
        try:
            message_text = custom_message if custom_message else MESSAGE
            print(f"✉️ Attempting to send: {message_text}")
            
            self.youtube.liveChatMessages().insert(
                part="snippet",
                body={
                    "snippet": {
                        "liveChatId": self.chat_id,
                        "type": "textMessageEvent",
                        "textMessageDetails": {
                            "messageText": message_text
                        }
                    }
                }
            ).execute()
            
            print("✅ Message sent successfully")
            return True
            
        except Exception as e:
            print(f"🚨 Failed to send message: {str(e)}")
            return False
    
    def run_scheduled_messages(self):
        schedule.every(INTERVAL_MINUTES).minutes.do(self.send_message)
        
        while True:
            self.is_live = self.check_live_status()
            if self.is_live:
                print(f"🎥 Stream is LIVE! Active chat: {self.chat_id}")
                while self.is_live:
                    self.process_chat_messages()
                    schedule.run_pending()
                    time.sleep(1)  # Faster loop when live
                    self.is_live = self.check_live_status()
            else:
                print(f"🔴 Stream OFFLINE. Next check in 60s...")
                time.sleep(60)

bot = YouTubeBot()

@app.route('/')
def home():
    if not bot.credentials:
        return redirect('/auth')
    return """
    YouTube Bot is running. Check logs for activity.<br>
    Endpoints:<br>
    - <a href="/test">/test</a> - Send test message<br>
    - <a href="/test-ai">/test-ai</a> - Test AI response
    """

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

@app.route('/test')
def test_endpoint():
    if not bot.credentials:
        return "Error: Not authenticated. Visit /auth first.", 401
    
    try:
        bot.is_live = bot.check_live_status()
        if not bot.chat_id:
            return "Error: No active chat ID. Stream may be offline.", 400
            
        success = bot.send_message(custom_message="!Test message from bot")
        return "✅ Test message sent!" if success else "❌ Failed to send message", 200
            
    except Exception as e:
        return f"⚠️ Error: {str(e)}", 500

@app.route('/test-ai')
def test_ai():
    if not bot.credentials:
        return "Error: Not authenticated. Visit /auth first.", 401
    
    test_prompt = "Hello, how are you?"
    ai_response = bot.generate_ai_response(test_prompt)
    return f"""
    <h1>AI Test</h1>
    <p><strong>Prompt:</strong> {test_prompt}</p>
    <p><strong>Response:</strong> {ai_response or 'No response generated'}</p>
    <p><a href="/">Back to home</a></p>
    """

def run_scheduler():
    if bot.credentials:
        print("🚀 Starting bot scheduler...")
        bot.run_scheduled_messages()

if __name__ == '__main__':
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    app.run(host='0.0.0.0', port=5000, debug=True)
