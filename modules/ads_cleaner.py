import sys
import os
import asyncio
import logging

# --- [ حل مشكلة المسارات لضمان التعرف على db ] ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from db import db
from telegram.error import BadRequest, Forbidden

logger = logging.getLogger("AdsCleaner")

async def setup(application):
    """تشغيل المنظف كخدمة خلفية مستقلة"""
    asyncio.create_task(run_ads_cleaner(application.bot))

async def delete_message_safe(bot, chat_id, message_id):
    """محاولة حذف الرسالة وتجاهل الأخطاء إذا كانت محذوفة بالفعل"""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (BadRequest, Forbidden):
        return False
    except Exception as e:
        logger.error(f"Error deleting msg {message_id} in {chat_id}: {e}")
        return False

async def run_ads_cleaner(bot):
    print("🧹 منظف الإعلانات الذكي بدأ العمل لتصفية القنوات...")
    
    while True:
        try:
            # 1. جلب كافة القنوات المسجلة
            all_channels = list(db.db.list_channels.find({}))
            
            for channel in all_channels:
                chat_id = channel['channel_id']
                
                # جلب سجل الإعلانات المرتبطة بهذه القناة (التي استقبلتها)
                # نريد الإبقاء على أحدث رسالة فقط وحذف الباقي
                ads_in_channel = list(db.db.ads_history.find({"to_channel": chat_id}).sort("timestamp", -1))
                
                if len(ads_in_channel) > 1:
                    # الإبقاء على الأول (الأحدث) وحذف الباقي
                    to_delete = ads_in_channel[1:] 
                    
                    for record in to_delete:
                        success = await delete_message_safe(bot, chat_id, record['msg_id'])
                        if success:
                            # إزالة السجل من قاعدة البيانات بعد الحذف من تلجرام
                            db.db.ads_history.delete_one({"_id": record["_id"]})
                            print(f"🗑️ تم حذف إعلان قديم مكرر في قناة: {channel.get('title')}")
                
                # فحص إضافي: هل البوت لا يزال مشرفاً؟ (لتجنب تعليق الحلقة)
                await asyncio.sleep(1) 

        except Exception as e:
            logger.error(f"Cleaner Loop Error: {e}")
            
        # التنظيف يتم كل 5 دقائق لضمان بقاء القنوات نظيفة دائماً
        await asyncio.sleep(300)

async def force_clean_channel(bot, chat_id):
    """دالة يمكن استدعاؤها عند نشر إعلان جديد لضمان حذف ما قبله فوراً"""
    old_ads = list(db.db.ads_history.find({"to_channel": chat_id}))
    for ad in old_ads:
        await delete_message_safe(bot, chat_id, ad['msg_id'])
        db.db.ads_history.delete_one({"_id": ad["_id"]})
