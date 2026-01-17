import asyncio
import logging
import datetime
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter
from db import db

logger = logging.getLogger("AdsEngine")

async def setup(application):
    """تشغيل المحرك كخدمة خلفية"""
    # تسجيل معالج زر التجاهل ليعمل في كل مكان
    from telegram.ext import CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(handle_ignore_button, pattern="^ignore_ad$"))
    
    # بدء الدورة اللانهائية
    asyncio.create_task(run_ads_engine(application))

async def handle_ignore_button(update, context):
    """حل مشكلة زر التجاهل - يختفي الإعلان فوراً"""
    query = update.callback_query
    try:
        await query.message.delete()
        await query.answer("تم إخفاء الإعلان بنجاح.")
    except:
        await query.answer("لا يمكن حذف الإعلان، ربما انتهت صلاحيته.")

async def run_ads_engine(application):
    print("🚀 محرك التبادل الذكي قيد التشغيل (نظام الـ 6 ساعات)...")
    while True:
        try:
            # 1. جلب القنوات المفعلة
            active_channels = list(db.db.list_channels.find({"list_active": True}))
            
            if len(active_channels) < 2:
                # إذا كانت قناة واحدة فقط، لا ننشر لتجنب التكرار داخل نفس القناة
                await asyncio.sleep(60)
                continue

            for target_ch in active_channels:
                # فحص الصلاحيات قبل كل شيء
                if not await check_permissions_silent(application.bot, target_ch):
                    continue

                # البحث عن إعلان مناسب للنشر في هذه القناة (ليس إعلانها نفسه)
                # ويجب أن يكون مر 6 ساعات على آخر تبديل في هذه القناة
                last_update = target_ch.get('last_ad_update')
                if last_update:
                    time_passed = datetime.datetime.utcnow() - last_update
                    if time_passed.total_seconds() < 21600: # 6 ساعات
                        continue

                # اختيار قناة "مصدر" عشوائية ليست هي "الهدف"
                source_candidates = [c for c in active_channels if c['channel_id'] != target_ch['channel_id']]
                if not source_candidates: continue
                source_ch = random.choice(source_candidates)

                # تنفيذ عملية التبديل (حذف القديم ونشر الجديد)
                await rotate_ad(application.bot, source_ch, target_ch)
                
                # انتظار قصير بين كل قناة وأخرى (10 دقائق تدريجياً) لتجنب حظر تلجرام
                await asyncio.sleep(600) 

        except Exception as e:
            logger.error(f"Main Loop Error: {e}")
            await asyncio.sleep(30)

async def rotate_ad(bot, source, target):
    """حذف الإعلان القديم ونشر الجديد مع تنبيهات"""
    try:
        # 1. حذف أي إعلان سابق مسجل في هذه القناة (Target)
        old_ad = db.db.ads_history.find_one({"to_channel": target['channel_id']})
        if old_ad:
            try: await bot.delete_message(target['channel_id'], old_ad['msg_id'])
            except: pass
            db.db.ads_history.delete_one({"_id": old_ad["_id"]})

        # 2. بناء الإعلان الجديد بالرابط المخفي
        bot_user = (await bot.get_me()).username
        ad_text = (
            f"{source.get('ad_text', 'تابعوا هذه القناة المتميزة!')}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🚀 [يمكنك نشر قناتك هنا](https://t.me/{bot_user}) مجاناً!"
        )
        kb = [[InlineKeyboardButton("✅ انضمام للقناة", url=f"https://t.me/{source['username'].replace('@','')}")],
              [InlineKeyboardButton("❌ تجاهل الإعلان", callback_data="ignore_ad")]]

        # 3. النشر
        if source.get('ad_photo'):
            msg = await bot.send_photo(target['channel_id'], photo=source['ad_photo'], caption=ad_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            msg = await bot.send_message(target['channel_id'], text=ad_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

        # 4. تحديث الداتا
        db.db.ads_history.insert_one({
            "msg_id": msg.message_id,
            "from_channel": source['channel_id'],
            "to_channel": target['channel_id'],
            "timestamp": datetime.datetime.utcnow()
        })
        db.db.list_channels.update_one({"channel_id": target['channel_id']}, {"$set": {"last_ad_update": datetime.datetime.utcnow()}})
        
        # 5. إرسال تنبيهات للملاك
        try:
            await bot.send_message(source['owner_id'], f"✨ **بشارة!** تم نشر إعلان قناتك الآن في قناة: `{target['title']}`\nسيستمر العرض لمدة 6 ساعات ثم ينتقل لقناة أخرى.")
            await bot.send_message(target['owner_id'], f"🔄 **تبادل:** تم تحديث الإعلان في قناتك `{target['title']}` بنجاح.")
        except: pass

    except Exception as e:
        logger.error(f"Rotation Error: {e}")

async def check_permissions_silent(bot, channel):
    """فحص الصلاحيات بدون إزعاج متكرر"""
    try:
        member = await bot.get_chat_member(channel['channel_id'], bot.id)
        if member.status not in ['administrator', 'creator'] or not member.can_post_messages:
            raise Exception("No Perms")
        return True
    except:
        # إذا فشل، نعطل القناة وننبه مرة واحدة فقط
        db.db.list_channels.update_one({"channel_id": channel['channel_id']}, {"$set": {"list_active": False}})
        try:
            await bot.send_message(channel['owner_id'], f"🛑 **تنبيه:** توقف النشر لقناتك ({channel['title']}) لأنك قمت بإلغاء صلاحيات البوت أو طرده!")
        except: pass
        return False
