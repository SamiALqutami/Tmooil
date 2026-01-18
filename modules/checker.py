# checker.py
import sys
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

# اجعل المسار يشمل جذر المشروع للوصول إلى db و config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# استيراد قاعدة البيانات
from db import db

# محاولة استيراد الإعدادات من config إن وُجدت
try:
    from config import Config
    OFFICIAL_CHANNEL_ID = getattr(Config, "OFFICIAL_CHANNEL_ID", None)
    OFFICIAL_CHANNEL_URL = getattr(Config, "OFFICIAL_CHANNEL_URL", None)
except Exception:
    OFFICIAL_CHANNEL_ID = None
    OFFICIAL_CHANNEL_URL = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def setup(application):
    """سجل معالج زر التحقق"""
    application.add_handler(CallbackQueryHandler(check_again_callback, pattern="^check_sub$"))


async def _resolve_official_from_db() -> Dict[str, Any]:
    """حاول جلب إعداد القناة الرسمية من مجموعة settings أو channels"""
    try:
        s = db.db.settings.find_one({"key": "official_channel"})
        if s:
            return s
    except Exception:
        pass

    # محاولة العثور على قناة موسومة في collection channels بعلامة official:true
    try:
        ch = db.db.channels.find_one({"official": True})
        if ch:
            return ch
    except Exception:
        pass

    return {}


async def is_user_member(bot, chat_identifier, user_id) -> bool:
    """
    يحاول التحقق من كون user عضو في chat_identifier.
    يعمل مع int (مثل -100...) أو مع '@username' أو 'username'.
    كما يسجل النتيجة مؤقتًا في DB (caching بسيط).
    """
    # Normalize username strings
    tried = []
    # Helper to try check and log result
    async def try_check(target):
        try:
            tried.append(("try_get_chat_member", target))
            member = await bot.get_chat_member(target, user_id)
            status = getattr(member, "status", None)
            is_member = status in ("member", "administrator", "creator", "restricted")
            # سجّل حالة الاشتراك في الـ DB
            try:
                db.db.users.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "last_subscription_check": datetime.utcnow(),
                            f"last_check_result.{str(target)}": {
                                "status": status,
                                "checked_at": datetime.utcnow()
                            }
                        },
                        "$addToSet": {"subscriptions": str(target)} if is_member else {}
                    },
                    upsert=True
                )
            except Exception:
                logger.exception("is_user_member: failed to update DB membership record")
            return is_member
        except Exception as e:
            tried.append(("error_get_chat_member", target, str(e)))
            return None

    # 1) محاولة مباشرة
    res = await try_check(chat_identifier)
    if res is True:
        return True

    # 2) إذا كانت سلسلة وليست @، جرّب مع @
    if isinstance(chat_identifier, str) and not chat_identifier.startswith("@"):
        res = await try_check("@" + chat_identifier)
        if res is True:
            return True

    # 3) حاول جلب chat ثم استخدام id
    try:
        chat = await bot.get_chat(chat_identifier)
        if chat and getattr(chat, "id", None):
            res = await try_check(chat.id)
            if res is True:
                return True
    except Exception as e:
        tried.append(("error_get_chat", chat_identifier, str(e)))

    # لم نتمكن من تأكيد العضوية (جميع المحاولات فشلت أو أعطت False)
    logger.info(f"is_user_member: checked targets attempts: {tried}")
    return False


async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    المحرك الرئيسي للتحقق من الاشتراك.
    يعيد True إذا المستخدم اشترك في القناة الرسمية (وباقي القنوات المطلوبة إن وُجدت).
    يعيد False ويعرض رسالة الاشتراك المطلوبة إن لم يكن مشتركًا.
    """
    user = update.effective_user
    if not user:
        logger.warning("check_subscription: no effective_user")
        return False
    user_id = user.id

    # تأكد أن هناك صف للمستخدم
    try:
        db.db.users.update_one({"user_id": user_id}, {"$setOnInsert": {"user_id": user_id, "created_at": datetime.utcnow()}}, upsert=True)
    except Exception:
        logger.exception("check_subscription: upsert user failed")

    # جلب بيانات المستخدم من DB
    user_data = db.db.users.find_one({"user_id": user_id}) or {}

    # الحصول على معرّف القناة الرسمية (from config -> settings -> channels)
    official = {"id": OFFICIAL_CHANNEL_ID, "url": OFFICIAL_CHANNEL_URL}
    if not official["id"]:
        db_off = await _resolve_official_from_db()
        if db_off:
            official["id"] = db_off.get("channel_id") or db_off.get("id")
            official["url"] = db_off.get("url") or db_off.get("username") or official["url"]

    required_channels: List[Dict[str, Any]] = []

    # افحص القناة الرسمية أولاً إن وُجدت
    if official.get("id"):
        try:
            member = await is_user_member(context.bot, official["id"], user_id)
        except Exception as e:
            logger.exception(f"check_subscription: error checking official channel membership: {e}")
            member = False

        if not member:
            required_channels.append({
                "title": "قناة البوت الرسمية",
                "url": official.get("url") or f"https://t.me/{str(official.get('id'))}",
                "id": official.get("id")
            })

    # الآن أضف قنوات التمويل (channels collection) - إجبارية إذا لم يكتمل مكافأة الإحالة
    # إذا أردت جعل الاشتراك في قنوات التمويل اختياري يمكن تعديل المنطق هنا
    try:
        # جلب كل القنوات المسجلة (ما عدا الرسمية)
        stored_channels = list(db.db.channels.find({}))
        for ch in stored_channels:
            ch_id = ch.get("channel_id") or ch.get("id") or ch.get("username")
            if not ch_id:
                continue
            # لا نفحص القناة الرسمية مرتين
            if official.get("id") and str(ch_id) == str(official.get("id")):
                continue
            # فقط أضف القناة إن لم يكن المستخدم مشتركاً بها
            try:
                is_mem = await is_user_member(context.bot, ch_id, user_id)
            except Exception as e:
                logger.info(f"check_subscription: error checking channel {ch_id}: {e}")
                is_mem = False
            if not is_mem:
                required_channels.append({
                    "title": ch.get("title", "قناة"),
                    "url": ch.get("url") or (("https://t.me/" + ch.get("username").lstrip("@")) if ch.get("username") else f"https://t.me/{ch_id}"),
                    "id": ch_id
                })
            # لا نضيف أكثر من 5 قنوات لعدم إغراق المستخدم
            if len(required_channels) >= 5:
                break
    except Exception:
        logger.exception("check_subscription: failed to scan stored channels")

    # إن وُجدت قنوات مطلوبة - إرسال/تحرير رسالة التنبيه وتسجيلها في DB
    if required_channels:
        # سجّل أننا أرسلنا طلب الاشتراك (لتجنب الإغراق)
        try:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"sub_prompt_sent": True, "sub_prompt_at": datetime.utcnow()}}, upsert=True)
        except Exception:
            logger.exception("check_subscription: failed to mark sub_prompt_sent")

        # أرسل أو حرّر رسالة التنبيه (نستخدم القناة الأولى كأهم)
        await send_sub_msg(update, context, required_channels[0], required_channels)
        return False

    # هنا: لم تتبق أي قنوات مطلوبة -> اعتبر المستخدم مُثبتا
    try:
        db.db.users.update_one({"user_id": user_id}, {"$set": {"is_verified": True, "verified_at": datetime.utcnow()}}, upsert=True)
        # إن وُجدت رسالة تنبيه سابقة فحاول حذفها / تحريرها لإزالة الأزرار
        prev = user_data.get("sub_prompt_msg")
        if prev and isinstance(prev, dict):
            try:
                await context.bot.delete_message(prev.get("chat_id", user_id), prev.get("message_id"))
            except Exception:
                # محاولة تحرير بدلاً من الحذف
                try:
                    await context.bot.edit_message_text("✅ تم التحقق — شكراً لاشتراكك!", chat_id=prev.get("chat_id", user_id), message_id=prev.get("message_id"))
                except Exception:
                    pass
        # إزالة علم إرسال التنبيه
        db.db.users.update_one({"user_id": user_id}, {"$unset": {"sub_prompt_sent": "", "sub_prompt_at": ""}})
    except Exception:
        logger.exception("check_subscription: failed to finalize verification in DB")

    return True


async def send_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, channel: Dict[str, Any], all_required: List[Dict[str, Any]]):
    """
    إرسال رسالة تحتوي على زر للاشتراك وزر تحقق.
    نخزن معرف الرسالة في DB حتى نعدلها أو نحذفها لاحقًا.
    """
    user = update.effective_user
    user_id = user.id
    required_count = len(all_required) if all_required else 1

    text = (
        f"⚠️ *مطلوب اشتراك* — يجب عليك الاشتراك في {required_count} قناة/قنوات للاستمرار.\n\n"
        f"📍 *القناة الحالية:* {channel.get('title')}\n\n"
        "1) اضغط زر (اشترك هنا) لفتح القناة.\n"
        "2) بعد الاشتراك اضغط زر (التحقق من الاشتراك).\n\n"
        "إذا واجهت مشكلة اضغط /start لإعادة الفحص."
    )

    kb = [
        [InlineKeyboardButton(f"📢 اشترك في {channel.get('title')}", url=channel.get("url"))],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="check_sub")]
    ]
    markup = InlineKeyboardMarkup(kb)

    sent_msg = None
    # حاول تحرير رسالة الـ callback إذا جاء التحديث من CallbackQuery
    try:
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": update.callback_query.message.chat.id, "message_id": update.callback_query.message.message_id}
            except Exception:
                # لو فشل التحرير نرسل رسالة جديدة
                sent = await context.bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
        else:
            # لو كانت رسالة عادية
            if update.message:
                sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
            else:
                sent = await context.bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
    except Exception as e:
        logger.exception(f"send_sub_msg: failed to send sub message: {e}")
        # كحل احتياطي: أرسل رسالة نصية بدون أزرار
        try:
            fallback = f"{text}\n\n{channel.get('url')}"
            await context.bot.send_message(user_id, fallback)
        except Exception:
            logger.exception("send_sub_msg: fallback also failed")

    # سجّل مرجع الرسالة في DB لتمكين الحذف/التحرير لاحقاً
    if sent_msg:
        try:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"sub_prompt_msg": sent_msg, "sub_prompt_required": required_count, "sub_required_list": all_required}}, upsert=True)
        except Exception:
            logger.exception("send_sub_msg: failed to save sent_msg in DB")


async def check_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج زر التحقق من الاشتراك"""
    query = update.callback_query
    await query.answer()  # اغلاق حالة الانتظار

    user = query.from_user
    user_id = user.id

    ok = await check_subscription(update, context)
    if ok:
        # حاول حذف رسالة التنبيه
        try:
            # حذف رسالة الزر المنسوخة
            try:
                await query.message.delete()
            except Exception:
                pass
            # إرسال لوحة رئيسية (استدعاء دالة من main)
            try:
                from main import get_main_reply_keyboard
                kb = await get_main_reply_keyboard(user_id)
                await context.bot.send_message(user_id, "✅ تم التحقق بنجاح — أهلاً بك!", reply_markup=kb)
            except Exception:
                await context.bot.send_message(user_id, "✅ تم التحقق بنجاح — أهلاً بك!")
        except Exception:
            logger.exception("check_again_callback: error after successful check")
    else:
        try:
            await query.answer("❌ لم تشترك بعد في القنوات المطلوبة. تأكد ثم أعد المحاولة.", show_alert=True)
        except Exception:
            pass
