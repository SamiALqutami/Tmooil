# checker.py
import sys
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
)

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
    """سجل معالجات الأزرار وطلبات الانضمام"""
    # زر تحقق الاشتراك
    application.add_handler(CallbackQueryHandler(check_again_callback, pattern="^check_sub$"))
    # زر إلغاء طلب الانضمام: callback_data = "cancel_join:<chat_id_or_username>"
    application.add_handler(CallbackQueryHandler(cancel_join_callback, pattern=r"^cancel_join:"))
    # التقاط طلبات الانضمام (يجب أن يكون البوت أدمن في القناة لكي يتلقى هذه التحديثات)
    application.add_handler(ChatJoinRequestHandler(handle_chat_join_request))


# -----------------------
# مساعدات DB لطلبات الانضمام
# -----------------------
def add_join_request_record(chat_id: int, chat_username: Optional[str], user_id: int, user_name: str):
    try:
        db.db.join_requests.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": {
                    "chat_username": chat_username,
                    "user_name": user_name,
                    "status": "pending",
                    "requested_at": datetime.utcnow()
                }
            },
            upsert=True
        )
    except Exception:
        logger.exception("add_join_request_record: db write failed")


def set_join_request_status(chat_id: int, user_id: int, status: str):
    try:
        db.db.join_requests.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}}
        )
    except Exception:
        logger.exception("set_join_request_status: db write failed")


def is_join_request_pending_in_db(chat_id: int, user_id: int) -> bool:
    try:
        r = db.db.join_requests.find_one({"chat_id": chat_id, "user_id": user_id, "status": "pending"})
        return bool(r)
    except Exception:
        logger.exception("is_join_request_pending_in_db: db read failed")
        return False


# -----------------------
# معالجة طلب الانضمام القادم من Telegram
# -----------------------
async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعمل عندما يرسل مستخدم طلب انضمام إلى قناة/مجموعة تحتوي على البوت كأدمن،
    نقوم بتخزينه في DB ونخطر المستخدم برسالة توضيحية (إن أمكن).
    """
    req = update.chat_join_request
    if not req:
        return

    chat = req.chat
    user = req.from_user

    chat_id = getattr(chat, "id", None)
    chat_username = getattr(chat, "username", None)
    user_id = getattr(user, "id", None)
    user_name = getattr(user, "full_name", str(user_id))

    logger.info(f"Received join request for chat {chat_id} ({chat_username}) from user {user_id}")

    # خزّن الطلب في DB
    add_join_request_record(chat_id, chat_username, user_id, user_name)

    # أرسل رسالة للمستخدم بأنه تم إرسال الطلب (لو كان ممكن)
    try:
        await context.bot.send_message(user_id, f"📨 تم إرسال طلب الانضمام إلى القناة {chat.title or chat_username}. حالته: *قيد الانتظار*.\nسوف يتم إعلامك عند القبول أو يمكنك الضغط على زر التحقق من الاشتراك.", parse_mode="Markdown")
    except Exception:
        # غالباً لا يمكن إرسال رسالة للمستخدم إن لم يبدأ المحادثة مع البوت سابقاً
        logger.info("handle_chat_join_request: could not message user (maybe hasn't started the bot)")


# -----------------------
# فحص العضوية
# -----------------------
async def is_user_member(bot, chat_identifier, user_id) -> bool:
    """
    يحاول التحقق من كون user عضو في chat_identifier.
    يعيد True إذا عضو، False إذا ليس عضوًا أو لا يمكن إثبات العضوية.
    (الحالة الخاصة 'pending' تُفحص من DB لاحقًا)
    """
    try:
        member = await bot.get_chat_member(chat_identifier, user_id)
        status = getattr(member, "status", None)
        is_member = status in ("member", "administrator", "creator", "restricted")
        return is_member
    except Exception as e:
        # قد نفشل لأن القناة خاصة أو لأن البوت ليس لديه صلاحية، أو لأن المستخدم غير عضو
        logger.info(f"is_user_member: get_chat_member failed for {chat_identifier} / user {user_id}: {e}")
        return False


async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    المحرك الرئيسي للتحقق من الاشتراك.
    يعيد True إذا المستخدم اشترك في كل القنوات المطلوبة.
    وإلا يعرض رسالة تعليمية/زرية ويعيد False.
    """
    user = update.effective_user
    if not user:
        logger.warning("check_subscription: no effective_user")
        return False
    user_id = user.id

    # تأكد وجود سجل للمستخدم
    try:
        db.db.users.update_one({"user_id": user_id}, {"$setOnInsert": {"user_id": user_id, "created_at": datetime.utcnow()}}, upsert=True)
    except Exception:
        logger.exception("check_subscription: upsert user failed")

    user_data = db.db.users.find_one({"user_id": user_id}) or {}

    # الحصول على معرّف القناة الرسمية (config -> settings -> db)
    official = {"id": OFFICIAL_CHANNEL_ID, "url": OFFICIAL_CHANNEL_URL}
    if not official["id"]:
        try:
            db_off = db.db.settings.find_one({"key": "official_channel"}) or {}
            if db_off:
                official["id"] = db_off.get("channel_id") or db_off.get("id")
                official["url"] = db_off.get("url") or db_off.get("username") or official["url"]
        except Exception:
            logger.exception("_resolve_official_from_db failed")

    required_channels: List[Dict[str, Any]] = []

    # افحص القناة الرسمية أولاً إن وُجدت
    if official.get("id"):
        try:
            member = await is_user_member(context.bot, official["id"], user_id)
        except Exception as e:
            logger.exception(f"check_subscription: error checking official channel membership: {e}")
            member = False

        if not member:
            # هل هناك طلب معلق في DB لهذه القناة؟
            pending = False
            try:
                # official["id"] قد يكون معرف أو username
                # حاول تحويله إلى رقم chat.id إن أمكن
                chat_obj = None
                try:
                    chat_obj = await context.bot.get_chat(official["id"])
                except Exception:
                    pass
                chat_id_for_db = chat_obj.id if chat_obj and getattr(chat_obj, "id", None) else official["id"]
                if isinstance(chat_id_for_db, int):
                    pending = is_join_request_pending_in_db(chat_id_for_db, user_id)
            except Exception:
                logger.exception("check_subscription: error checking pending for official channel")

            required_channels.append({
                "title": "قناة البوت الرسمية",
                "url": official.get("url") or f"https://t.me/{str(official.get('id'))}",
                "id": official.get("id"),
                "pending": pending
            })

    # الآن أضف قنوات التمويل (channels collection)
    try:
        stored_channels = list(db.db.channels.find({}))
        for ch in stored_channels:
            ch_id = ch.get("channel_id") or ch.get("id") or ch.get("username")
            if not ch_id:
                continue
            if official.get("id") and str(ch_id) == str(official.get("id")):
                continue
            try:
                is_mem = await is_user_member(context.bot, ch_id, user_id)
            except Exception as e:
                logger.info(f"check_subscription: error checking channel {ch_id}: {e}")
                is_mem = False

            pending = False
            # حاول تحويل ch_id إلى رقمه الحقيقي للتحقق من DB pending، إذا أمكن
            try:
                chat_obj = None
                try:
                    chat_obj = await context.bot.get_chat(ch_id)
                except Exception:
                    pass
                chat_id_for_db = chat_obj.id if chat_obj and getattr(chat_obj, "id", None) else ch_id
                if isinstance(chat_id_for_db, int):
                    pending = is_join_request_pending_in_db(chat_id_for_db, user_id)
            except Exception:
                logger.exception("check_subscription: pending check fail")

            if not is_mem:
                required_channels.append({
                    "title": ch.get("title", "قناة"),
                    "url": ch.get("url") or (("https://t.me/" + ch.get("username").lstrip("@")) if ch.get("username") else f"https://t.me/{ch_id}"),
                    "id": ch_id,
                    "pending": pending,
                    # نحتفظ برقم القناة الحقيقي لو وجد
                    "real_chat_id": chat_id_for_db if 'chat_id_for_db' in locals() else None,
                    "auto_approve": ch.get("auto_approve", False)
                })
            if len(required_channels) >= 5:
                break
    except Exception:
        logger.exception("check_subscription: failed to scan stored channels")

    # إن وُجدت قنوات مطلوبة - إرسال/تحرير رسالة التنبيه وتسجيلها في DB
    if required_channels:
        try:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"sub_prompt_sent": True, "sub_prompt_at": datetime.utcnow()}}, upsert=True)
        except Exception:
            logger.exception("check_subscription: failed to mark sub_prompt_sent")

        # إرسال رسالة توضّح حالة كل قناة (مفتوحة / لم تشترك / طلب قيد الانتظار)
        await send_sub_msg(update, context, required_channels[0], required_channels)
        return False

    # لم تتبق أي قنوات مطلوبة -> اعتبر المستخدم مُثبتا
    try:
        db.db.users.update_one({"user_id": user_id}, {"$set": {"is_verified": True, "verified_at": datetime.utcnow()}}, upsert=True)
        prev = user_data.get("sub_prompt_msg")
        if prev and isinstance(prev, dict):
            try:
                await context.bot.delete_message(prev.get("chat_id", user_id), prev.get("message_id"))
            except Exception:
                try:
                    await context.bot.edit_message_text("✅ تم التحقق — شكراً لاشتراكك!", chat_id=prev.get("chat_id", user_id), message_id=prev.get("message_id"))
                except Exception:
                    pass
        db.db.users.update_one({"user_id": user_id}, {"$unset": {"sub_prompt_sent": "", "sub_prompt_at": ""}})
    except Exception:
        logger.exception("check_subscription: failed to finalize verification in DB")

    return True


# -----------------------
# إرسال رسالة الاشتراك مع التعامل مع حالة 'pending'
# -----------------------
async def send_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, channel: Dict[str, Any], all_required: List[Dict[str, Any]]):
    user = update.effective_user
    user_id = user.id
    required_count = len(all_required) if all_required else 1

    # جهّز نص عام مفصل للقنوات المطلوبة
    lines = [
        f"⚠️ *مطلوب اشتراك* — يجب عليك الاشتراك في {required_count} قناة/قنوات للاستمرار.",
        ""
    ]
    for idx, ch in enumerate(all_required, start=1):
        state = "❌ غير مشترك"
        if ch.get("pending"):
            state = "⏳ طلب الانضمام قيد الانتظار"
        lines.append(f"{idx}) *{ch.get('title', 'قناة')}* — {state}\n{ch.get('url')}")
        lines.append("")

    lines.append("إجراءات:")
    lines.append("1) اضغط زر (فتح القناة) لفتح القناة وإرسال طلب الانضمام إن كانت خاصة.")
    lines.append("2) بعد الاشتراك أو قبول الطلب اضغط زر (التحقق من الاشتراك).")
    lines.append("إذا واجهت مشكلة اضغط /start لإعادة الفحص.")
    text = "\n".join(lines)

    # بناء أزرار: زر فتح القناة + زر تحقق، وإذا كانت القناة الحالية بها طلب معلق أضف زر إلغاء
    kb = []
    # زر لفتح القناة الأساسية (القناة الحالية التي نركز عليها)
    kb.append([InlineKeyboardButton(f"📢 افتح {channel.get('title')}", url=channel.get('url'))])
    # إذا القناة الحالية يوجد بها طلب معلق، أضف زر إلغاء
    real_id = channel.get("real_chat_id") or channel.get("id")
    if channel.get("pending"):
        # تأكد من تمثيل real_id كقيمة مناسبة لسلسلة callback
        kb.append([InlineKeyboardButton("❌ إلغاء طلب الانضمام", callback_data=f"cancel_join:{real_id}")])
    # زر التحقق
    kb.append([InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="check_sub")])
    markup = InlineKeyboardMarkup(kb)

    sent_msg = None
    try:
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": update.callback_query.message.chat.id, "message_id": update.callback_query.message.message_id}
            except Exception:
                sent = await context.bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
        else:
            if update.message:
                sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
            else:
                sent = await context.bot.send_message(user_id, text, reply_markup=markup, parse_mode="Markdown")
                sent_msg = {"chat_id": sent.chat.id, "message_id": sent.message_id}
    except Exception as e:
        logger.exception(f"send_sub_msg: failed to send sub message: {e}")
        try:
            fallback = f"{text}\n\n{channel.get('url')}"
            await context.bot.send_message(user_id, fallback)
        except Exception:
            logger.exception("send_sub_msg: fallback also failed")

    # سجّل مرجع الرسالة في DB
    if sent_msg:
        try:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"sub_prompt_msg": sent_msg, "sub_prompt_required": required_count, "sub_required_list": all_required}}, upsert=True)
        except Exception:
            logger.exception("send_sub_msg: failed to save sent_msg in DB")


# -----------------------
# معالج زر التحقق
# -----------------------
async def check_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    ok = await check_subscription(update, context)
    if ok:
        try:
            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                from main import get_main_reply_keyboard
                kb = await get_main_reply_keyboard(user_id)
                await context.bot.send_message(user_id, "✅ تم التحقق بنجاح — أهلاً بك!", reply_markup=kb)
            except Exception:
                await context.bot.send_message(user_id, "✅ تم التحقق بنجاح — أهلاً بك!")
        except Exception:
            logger.exception("check_again_callback: error after successful check")
    else:
        # توضيح إضافي: إذا ما زال هناك قنوات بــ pending سيظهر النص من send_sub_msg
        try:
            await query.answer("❌ لم تكتمل متطلبات الاشتراك. تأكد من الاشتراك أو انتظار قبول طلب الانضمام.", show_alert=True)
        except Exception:
            pass


# -----------------------
# معالج إلغاء طلب الانضمام
# -----------------------
async def cancel_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    data = query.data or ""
    # صيغة: cancel_join:<chat_id_or_username>
    try:
        _, raw_chat = data.split(":", 1)
    except Exception:
        await query.answer("خطأ في بيانات الزر.", show_alert=True)
        return

    # حاول تحويل raw_chat إلى int إن أمكن
    try:
        chat_id = int(raw_chat)
    except Exception:
        chat_id = raw_chat

    # تسجيل إلغاء في DB و(إن أمكن) استدعاء decline_chat_join_request
    try:
        # decline فقط إن عرفنا chat_id رقمي (معرّف القناة الحقيقي)
        if isinstance(chat_id, int):
            try:
                await context.bot.decline_chat_join_request(chat_id, user_id)
                set_join_request_status(chat_id, user_id, "cancelled_by_user")
                await query.answer("تم إلغاء طلب الانضمام.", show_alert=True)
                # حدث رسالة توضيحية للمستخدم
                try:
                    await context.bot.send_message(user_id, "تم إلغاء طلب الانضمام إلى القناة. يمكنك المحاولة مرة أخرى لاحقًا.")
                except Exception:
                    pass
                # أعد تشغيل فحص الاشتراك ليعرض الحالة المحدثة
                await check_subscription(update, context)
                return
            except Exception as e:
                logger.exception(f"cancel_join_callback: decline failed: {e}")
                # نتابع ونعطي رسالة خطأ للمستخدم
                await query.answer("لم أتمكن من إلغاء الطلب تلقائياً (قد لا أملك صلاحية).", show_alert=True)
        else:
            # لو لم نعرف معرف رقمي، نضع علامة في DB بالـ chat username إن وجد
            set_join_request_status(chat_id, user_id, "cancelled_by_user")
            await query.answer("تم وضع علامة إلغاء الطلب. إن كنت ترغب، تواصل مع مدير القناة لإلغاء الطلب.", show_alert=True)
            await check_subscription(update, context)
            return
    except Exception:
        logger.exception("cancel_join_callback: error")
        try:
            await query.answer("حدث خطأ أثناء محاولة إلغاء الطلب.", show_alert=True)
        except Exception:
            pass


# -----------------------
#موافقة حول الموافقة التلقائية (اختياري)
-----------------------
#إذا أردت أن تختار البوت للموافقة على طلبات الأعضاء لقناة معينة:
# في مجموعة القنوات في DB لكل الوثيقة حقل حقل: "auto_approve": True
#شرط أن يكون البوت أدمن في القناة وأن تملك الموافقة على طلبات الاشتراك.
# عند الوصول إلى معالج طلب الانضمام chat_join_request وعندما يتصل بها يتصل:
# انتظر context.bot.approve_chat_join_request(chat_id, user_id)
# وفي هذه الشيفرة تركنا الموافقة التلقائية كإمكانية الاشتراكا بسهولة
# داخل Handle_chat_join_request بعد إضافة السجل في قاعدة البيانات.


-----------------------
# ملف
-----------------------
