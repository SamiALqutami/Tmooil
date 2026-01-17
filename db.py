import logging
import certifi
import asyncio
import datetime
from pymongo import MongoClient
from config import Config
import dns.resolver

logger = logging.getLogger(__name__)

# حل مشكلة DNS في بعض البيئات
try:
    dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
    dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1']
except Exception as e:
    logger.warning(f"⚠️ DNS Setup: {e}")

class DatabaseManager:
    def __init__(self):
        self.uri = Config.MONGO_URL 
        self.client = None
        self.db = None
        self._connect()

    def _connect(self):
        try:
            self.client = MongoClient(
                self.uri, 
                tlsCAFile=certifi.where(),
                serverSelectionTimeoutMS=30000
            )
            self.client.admin.command('ping')
            self.db = self.client['TelegramBot']
            logger.info("✅ متصل بـ MongoDB Atlas - نظام شامل!")
        except Exception as e:
            logger.error(f"❌ فشل اتصال القاعدة: {e}")

    # --- [ نظام المستخدمين والإحالات ] ---
    def add_user(self, user_id, name, username):
        """إضافة مستخدم مع تهيئة كاملة لحقول الإحالة والتمويل"""
        if self.db is not None:
            self.db.users.update_one(
                {"user_id": user_id},
                {
                    "$set": {"first_name": name, "username": username},
                    "$setOnInsert": {
                        "referrals_count": 0,
                        "funded_remaining": 0,
                        "total_received_members": 0, # إجمالي الأعضاء المكتسبين
                        "join_date": datetime.datetime.now()
                    }
                },
                upsert=True
            )

    # --- [ نظام اللستة والتبادل المستقل ] ---
    def update_list_channel(self, channel_id, owner_id, title, username, member_count):
        """إضافة أو تحديث قناة في نظام اللستة المستقل"""
        if self.db is not None:
            self.db.list_channels.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "owner_id": owner_id,
                        "username": username,
                        "title": title,
                        "member_count": member_count,
                        "last_update": datetime.datetime.now()
                    },
                    "$setOnInsert": {
                        "list_active": False,      # حالة التشغيل
                        "custom_target": 10,       # الهدف المطلوب
                        "achieved_members": 0,     # أعضاء دخلوا للقناة
                        "yield_score": 0,          # إعلانات نشرتها القناة لغيرها
                        "ad_text": "لم يتم ضبط نص الإعلان بعد.",
                        "last_post_time": 0        # توقيت آخر نشر (كل 3 ساعات)
                    }
                },
                upsert=True
            )

    # --- [ محرك السجلات والإحصائيات ] ---
    def log_ad_event(self, from_ch_id, to_ch_id, message_id):
        """تسجيل عملية نشر إعلان بكل تفاصيلها"""
        if self.db is not None:
            log_data = {
                "from_channel": from_ch_id,     # القناة صاحبة الإعلان
                "to_channel": to_ch_id,         # القناة التي نُشر فيها الإعلان
                "message_id": message_id,
                "timestamp": asyncio.get_event_loop().time(),
                "date_str": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            # إضافة السجل
            self.db.ads_history.insert_one(log_data)
            
            # تحديث عداد "العطاء" للقناة التي نُشر فيها الإعلان (التي استقبلت)
            self.db.list_channels.update_one(
                {"channel_id": to_ch_id},
                {"$inc": {"yield_score": 1}}
            )

    def get_channel_history(self, channel_id, limit=10):
        """جلب تقرير أين نُشر إعلاني؟"""
        if self.db is not None:
            return list(self.db.ads_history.find({"from_channel": channel_id}).sort("timestamp", -1).limit(limit))
        return []

    def get_global_stats(self):
        """إحصائيات عامة للبوت ككل"""
        if self.db is not None:
            stats = {
                "users_count": self.db.users.count_documents({}),
                "channels_count": self.db.list_channels.count_documents({}),
                "total_ads_posted": self.db.ads_history.count_documents({}),
                "active_exchanges": self.db.list_channels.count_documents({"list_active": True})
            }
            return stats
        return {}

    # --- [ نظام التمويل (القديم لضمان التوافق) ] ---
    def update_funding_channel(self, channel_id, owner_id, username, title, member_count):
        if self.db is not None:
            self.db.channels.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "owner_id": owner_id,
                        "username": username,
                        "title": title,
                        "member_count": member_count
                    }
                },
                upsert=True
            )

# تصدير الكائن للاستخدام المباشر
db = DatabaseManager()
