# checker.py
import sys
import os
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

# ضبط مسار المشروع للوصول لـ db و config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db
from config import OFFICIAL_CHANNEL_USERNAME, OFFICIAL_CHANNEL_URL  # تأكد أنها موجودة في config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# إعدادات
MAX_USER_CHANNELS = 5           # 4 قنوات/مجموعات + القناة الرسمية = 5
FUND_MONITOR_INTERVAL = 60      # ثانية بين فحص تغيّر membros في القنوات
POLLING_CONCURRENCY = 8

# --------------------- Helpers آمنة لاستدعاءات API ---------------------
async def safe_get_chat_member(bot, chat_id, user_id):
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.debug(f"safe_get_chat_member({chat_id},{user_id}) failed: {e}")
        return None

async def safe_get_chat(bot, chat_id):
    try:
        return await bot.get_chat(chat_id)
    except Exception as e:
        logger.debug(f"safe_get_chat({chat_id}) failed: {e}")
        return None

async def safe_send_message(bot, chat_id, *args, **kwargs):
    try:
        return await bot.send_message(chat_id, *args, **kwargs)
    except Exception as e:
        logger.warning(f"safe_send_message to {chat_id} failed: {e}")
        return None

async def safe_edit_message(bot, chat_id, message_id, *args, **kwargs):
    try:
        return await bot.edit_message_text(*args, chat_id=chat_id, message_id=message_id, **kwargs)
    except Exception as e:
        logger.debug(f"safe_edit_message {chat_id}#{message_id} failed: {e}")
        return None

async def safe_delete_message(bot, chat_id, message_id):
    try:
        return await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"safe_delete_message {chat_id}#{message_id} failed: {e}")
        return None

# --------------------- حالة عضوية المستخدم ---------------------
async def get_member_status(bot, chat_identifier, user_id) -> Optional[str]:
    """
    محاولة استرجاع status string للمستخدم في chat_identifier.
    تعود None عند عدم القدرة على التحقق (خاصة/البوت ليس عضو، خطأ network...).
    """
    m = await safe_get_chat_member(bot, chat_identifier, user_id)
    if m:
        return getattr(m, "status", None)
    # محاولة مع @username لو كانت سلسلة بدون @
    try:
        if isinstance(chat_identifier, str) and not chat_identifier.startswith("@"):
            m2 = await safe_get_chat_member(bot, "@" + chat_identifier, user_id)
            if m2:
                return getattr(m2, "status", None)
    except Exception:
        pass
    return None

async def is_user_member(bot, chat_identifier, user_id) -> Optional[bool]:
    """
    إرجاع:
      - True  => بالتأكيد عضو
      - False => بالتأكيد ليس عضو (left/kicked)
      - None  => غير مؤكد (private/ bot lacks permission / pending)
    """
    status = await get_member_status(bot, chat_identifier, user_id)
    if status in ("member", "administrator", "creator", "restricted"):
        return True
    if status in ("left", "kicked"):
        return False
    return None

# --------------------- فحص ما إذا البوت مشرف/يمكنه رؤية القناة ---------------------
async def bot_can_access_chat(bot, chat_identifier) -> bool:
    """
    نحتاج أن نتأكد من أن البوت يمكنه الوصول لعدد الأعضاء أو حالة الأعضاء للقناة:
    - نجرب get_chat ثم get_chat_member(bot.id)
    - إن فشلنا فهذا يعني أن القناة غير متاحة (bot أُعزل أو خاصة).
    """
    try:
        chat = await safe_get_chat(bot, chat_identifier)
        if not chat:
            return False
        # حاول الحصول على حالة البوت نفسه
        me = await safe_get_chat_member(bot, chat.id, (await bot.get_me()).id)
        if me and getattr(me, "status", None) in ("administrator", "creator", "member"):
            return True
        # لو لم يكن مشرفاً لكن chat.type == "channel" و هو عام، قد نتمكن من استخدام get_chat_member_count
        return True  # لا نمنع هنا - سنعالج فشل لاحقًا
    except Exception:
        return False

# --------------------- تجميع قنوات الإلزام لكل مستخدم ---------------------
async def gather_required_channels(bot, user_id) -> List[Dict[str, Any]]:
    """
    المنطق:
    - نحصل أولاً على قنوات مخصصة للمستخدم (user_subs) حتى 4
    - إن كانت count < 4 نملأ من db.channels المتاحة (التي يستطيع البوت رؤيتها أو عامّة) وليس عند المالك نفسه
    - نزيل القنوات الغير متاحة (البوت أُزيل من الاشراف) من القوائم المطلوبة تلقائياً و نعلم المالك إن لزم
    - نضيف القناة الرسمية دائماً كعنصر خامس (ونتحقق من حالة العضوية فيها)
    """
    required: List[Dict[str, Any]] = []
    try:
        # 1) قنوات مخصصة للمستخدم (قد يكون تم تعيينها سابقاً)
        subs = list(db.db.user_subs.find({"user_id": user_id}))[: MAX_USER_CHANNELS - 1]
        for s in subs:
            ch_id = s.get("id") or s.get("channel_id") or s.get("chat_id")
            title = s.get("title") or s.get("username") or str(ch_id)
            url = s.get("url") or (("https://t.me/" + s.get("username").lstrip("@")) if s.get("username") else None)
            # هل البوت يستطيع الوصول للقناة؟ (لو لا: استبعدها)
            can_access = await bot_can_access_chat(bot, ch_id)
            if not can_access:
                # علامتها 'unavailable' — سنعلم صاحب القناة لاحقاً
                status = "unavailable"
            else:
                mem = await is_user_member(bot, ch_id, user_id)
                status = "ok" if mem is True else ("missing" if mem is False else "pending")
            required.append({"id": ch_id, "title": title, "url": url or f"https://t.me/{str(ch_id)}", "status": status})
    except Exception as e:
        logger.exception(f"gather_required_channels: user_subs read failed: {e}")

    # 2) اذا ما وصلنا لـ 4 entries (غير unavailable) نملأ من channels العامة/المسموح الوصول لها
    try:
        current_non_unavail = [r for r in required if r.get("status") != "unavailable"]
        need = max(0, (MAX_USER_CHANNELS - 1) - len([r for r in current_non_unavail if r.get("status") in ("ok","missing","pending")]))
        if need > 0:
            # انتقاء قنوات من db.channels التي ليست مملوكة للمستخدم ولا موجودة حالياً في required
            all_chs = list(db.db.channels.find({}))
            for ch in all_chs:
                if need <= 0:
                    break
                ch_id = ch.get("channel_id") or ch.get("id") or ch.get("chat_id") or ch.get("username")
                if not ch_id:
                    continue
                # لا نضيف القنوات التي بالفعل في required
                if any(str(r["id"]) == str(ch_id) for r in required):
                    continue
                # لا نضيف قنوات يملكها المستخدم نفسه
                if ch.get("owner_id") == user_id:
                    continue
                # التحقق من امكانية الوصول
                can_access = await bot_can_access_chat(bot, ch_id)
                if not can_access:
                    # لا نضيفها لقائمة الطلبات، لكن نحتفظ بها في DB كقناة تحتاج اصلاح
                    continue
                # تحقق إن كان المستخدم عضوًا بها
                mem = await is_user_member(bot, ch_id, user_id)
                status = "ok" if mem is True else ("missing" if mem is False else "pending")
                required.append({"id": ch_id, "title": ch.get("title") or ch.get("username") or str(ch_id), "url": ch.get("url") or (("https://t.me/" + str(ch.get("username")).lstrip("@")) if ch.get("username") else f"https://t.me/{ch_id}"), "status": status})
                need -= 1
    except Exception as e:
        logger.exception(f"gather_required_channels: fill from channels failed: {e}")

    # 3) أضف القناة الرسمية دائماً
    try:
        mem_off = await is_user_member(bot, OFFICIAL_CHANNEL_USERNAME, user_id)
        off_status = "ok" if mem_off is True else ("missing" if mem_off is False else "pending")
    except Exception:
        off_status = "pending"
    # إذا موجودة بالفعل في القائمة حدث حالتها، وإلا أضفها كأخير
    found = False
    for r in required:
        if str(r.get("id")) == str(OFFICIAL_CHANNEL_USERNAME) or (r.get("url") and OFFICIAL_CHANNEL_URL in r.get("url")):
            r["status"] = off_status
            found = True
            break
    if not found:
        required.append({"id": OFFICIAL_CHANNEL_USERNAME, "title": "القناة الرسمية للبوت", "url": OFFICIAL_CHANNEL_URL, "status": off_status})

    # 4) الآن نزيل العناصر التي علامتها unavailable من قائمة 'مطلوبة' (لا نزعج المستخدم بها)
    # لكن نخزن معلومات أن صاحب القناة بحاجة لإصلاح (notify owner) — سنعالج التنبيه في مكان منفصل
    final_required = [r for r in required if r.get("status") != "unavailable"]

    return final_required, [r for r in required if r.get("status") == "unavailable"]

# --------------------- تنسيق سطر القناة ---------------------
def format_channel_line(ch: Dict[str, Any], idx: int) -> str:
    st = ch.get("status", "missing")
    icon = {"ok": "✅", "pending": "⏳", "missing": "❌"}.get(st, "❌")
    note = " (في انتظار قبول المدير)" if st == "pending" else ""
    return f"{icon} *{idx}.* {ch.get('title')}{note}"

# --------------------- إرسال/تحديث رسالة الاشتراك ---------------------
async def send_or_update_sub_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, required: List[Dict[str, Any]]):
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    total = len(required)
    remaining = len([c for c in required if c.get("status") != "ok"])

    header = f"⚠️ *الاشتراك الإجباري* — عليك الاشتراك في *{total}* قناة/مجموعة (4 + القناة الرسمية)."
    progress = f"🔢 التقدم: *{total - remaining}/{total}*"
    lines = [header, progress, ""]
    for idx, ch in enumerate(required, start=1):
        lines.append(format_channel_line(ch, idx))
    lines.append("")
    lines.append("1) اضغط زر فتح القناة/المجموعة للذهاب.\n2) إن كانت خاصة: أرسل طلب الانضمام وانتظر القبول.\n3) بعد الانضمام اضغط زر (✅ التحقق) أدناه.")

    text = "\n".join(lines)

    # أزرار للقنوات + زر تحقق
    kb = []
    for ch in required:
        url = ch.get("url") or f"https://t.me/{str(ch.get('id')).lstrip('@')}"
        kb.append([InlineKeyboardButton(f"📢 افتح {ch.get('title')}", url=url)])
    kb.append([InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="check_sub")])
    markup = InlineKeyboardMarkup(kb)

    # محاولة تعديل الرسالة السابقة أو إرسال رسالة جديدة
    user_doc = db.db.users.find_one({"user_id": user_id}) or {}
    prev = user_doc.get("sub_prompt_msg")
    try:
        if prev and prev.get("chat_id") and prev.get("message_id"):
            await safe_edit_message(context.bot, prev["chat_id"], prev["message_id"], text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            sent = prev
        else:
            sent_obj = await safe_send_message(context.bot, user_id, text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            if sent_obj:
                sent = {"chat_id": sent_obj.chat.id, "message_id": sent_obj.message_id}
            else:
                sent = None
        if sent:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"sub_prompt_msg": sent, "sub_required_list": required, "sub_prompt_at": datetime.utcnow()}}, upsert=True)
    except Exception as e:
        logger.exception(f"send_or_update_sub_msg error: {e}")

# --------------------- التحقق من اكتمال الاشتراك ---------------------
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    user_id = user.id

    required, unavailable = await gather_required_channels(context.bot, user_id)

    # نبلغ مالكي القنوات الغير متاحة لتصليح البوت مشرفاً إذا لزم
    try:
        for u in unavailable:
            # إذا يوجد owner info في db.channels نرسله له
            ch_doc = db.db.channels.find_one({"$or": [{"channel_id": u.get("id")}, {"username": u.get("id")}]})
            owner = ch_doc.get("owner_id") if ch_doc else None
            if owner:
                note = f"⚠️ ملاحظة: لا يمكن الوصول لقناتك/مجمعك *{u.get('title')}* لأن البوت ربما أُزيل من الإشراف أو الإعدادات تمنعه. أعد رفع البوت مشرفاً ليعمل النظام بشكل صحيح."
                await safe_send_message(context.bot, owner, note, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("notify owners of unavailable channels failed")

    not_ok = [c for c in required if c.get("status") != "ok"]
    if not_ok:
        # أرسل/حدّث رسالة الاشتراك
        await send_or_update_sub_msg(update, context, required)
        return False

    # إن وصلنا هنا => كل المطلوبات OK
    try:
        db.db.users.update_one({"user_id": user_id}, {"$set": {"is_verified": True, "verified_at": datetime.utcnow()}, "$unset": {"sub_prompt_msg": "", "sub_required_list": ""}}, upsert=True)
    except Exception:
        logger.exception("check_subscription: failed DB update verified")

    # حذف رسالة التنبيه السابقة إن وُجدت
    user_doc = db.db.users.find_one({"user_id": user_id}) or {}
    prev = user_doc.get("sub_prompt_msg")
    if prev:
        await safe_delete_message(context.bot, prev.get("chat_id"), prev.get("message_id"))

    # إرسال رسالة نجاح مع زر فتح البوت وزر start deep link
    try:
        bot_info = await context.bot.get_me()
        bot_username = getattr(bot_info, "username", None)
        bot_link = f"https://t.me/{bot_username}?start" if bot_username else None
        text = "✅ تم التحقق بنجاح — أهلاً بك! يمكنك الآن فتح البوت للاستفادة من المميزات."
        kb = []
        if bot_link:
            kb.append([InlineKeyboardButton("🔗 افتح البوت", url=bot_link)])
        kb.append([InlineKeyboardButton("▶️ /start", callback_data="main_menu")])
        markup = InlineKeyboardMarkup(kb)
        await safe_send_message(context.bot, user_id, text, reply_markup=markup)
    except Exception:
        logger.exception("check_subscription: sending success message failed")

    return True

# --------------------- زر التحقق ---------------------
async def check_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    ok = await check_subscription(update, context)
    if query:
        if ok:
            try:
                # محاولة حذف رسالة الزر الحالية بأمان
                await safe_delete_message(context.bot, query.message.chat.id, query.message.message_id)
            except Exception:
                pass
            try:
                await query.message.reply_text("✅ تم التحقق — افتح البوت أو اضغط /start.", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        else:
            await query.answer("❌ لم تكتمل الاشتراكات بعد. تأكد ثم أعد المحاولة.", show_alert=True)

# --------------------- معالجة انضمام أعضاء في مجموعات (new_chat_members) ---------------------
async def on_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = update.effective_chat
        new_members = update.message.new_chat_members or []
        if not new_members:
            return
        # هل هذه الدردشة ضمن قنوات/مجموعات التمويل؟
        ch_doc = db.db.channels.find_one({"channel_id": chat.id}) or db.db.list_channels.find_one({"channel_id": chat.id})
        if not ch_doc:
            return
        owner_id = ch_doc.get("owner_id")
        if not owner_id:
            return

        added = 0
        for u in new_members:
            if getattr(u, "is_bot", False):
                continue
            added += 1
            # تحديث إحصاءات
            db.db.channels.update_one({"channel_id": chat.id}, {"$inc": {"member_count": 1, "achieved_members": 1}})
            db.db.users.update_one({"user_id": owner_id}, {"$inc": {"total_received_members": 1}}, upsert=True)

        # إرسال إشعار لمالك القناة
        owner_doc = db.db.users.find_one({"user_id": owner_id}) or {}
        total_received = owner_doc.get("total_received_members", 0)
        target = ch_doc.get("custom_target") or ch_doc.get("target")
        remain = max((target - total_received), 0) if target else None

        note = f"🔔 انضم {added} عضو جديد إلى قناتك/مجمعك *{ch_doc.get('title')}*.\n\n📈 إجمالي المكتسب: *{total_received}*"
        if remain is not None:
            note += f"\n🎯 المتبقي للوصول للهدف: *{remain}*"

        await safe_send_message(context.bot, owner_id, note, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception(f"on_new_chat_members failed: {e}")

# --------------------- مراقبة تغيّر member_count للقنوات (خلفية) ---------------------
async def monitor_channel_counts(application):
    """
    مهمة خلفية تفحص قنوات db.db.channels وتكتشف زيادات في عدد الأعضاء.
    عند زيادة: تحدّث DB وترسل إشعار لصاحب القناة.
    """
    await asyncio.sleep(3)
    bot = application.bot
    while True:
        try:
            channels = list(db.db.channels.find({}))
            if not channels:
                await asyncio.sleep(FUND_MONITOR_INTERVAL)
                continue
            sem = asyncio.Semaphore(POLLING_CONCURRENCY)

            async def check_one(ch):
                async with sem:
                    ch_id = ch.get("channel_id") or ch.get("id") or ch.get("chat_id") or ch.get("username")
                    owner = ch.get("owner_id")
                    if not ch_id or not owner:
                        return
                    try:
                        count = await bot.get_chat_member_count(ch_id)
                    except Exception as e:
                        logger.debug(f"monitor_channel_counts: cannot get count for {ch_id}: {e}")
                        return
                    prev = ch.get("member_count", 0)
                    if count > prev:
                        delta = count - prev
                        db.db.channels.update_one({"_id": ch["_id"]}, {"$set": {"member_count": count}, "$inc": {"achieved_members": delta}})
                        db.db.users.update_one({"user_id": owner}, {"$inc": {"total_received_members": delta}}, upsert=True)
                        total_received = db.db.users.find_one({"user_id": owner}).get("total_received_members", 0)
                        target = ch.get("custom_target") or ch.get("target")
                        remain = max((target - total_received), 0) if target else None
                        note = f"🔔 رصد دخول {delta} عضو جديد لقناتك *{ch.get('title')}*.\n\n📈 إجمالي المكتسب: *{total_received}*"
                        if remain is not None:
                            note += f"\n🎯 المتبقي للوصول للهدف: *{remain}*"
                        await safe_send_message(bot, owner, note, parse_mode=ParseMode.MARKDOWN)
                    elif count != prev:
                        # تحديث بدون إشعار على الانخفاض
                        db.db.channels.update_one({"_id": ch["_id"]}, {"$set": {"member_count": count}})
            await asyncio.gather(*(check_one(ch) for ch in channels))
        except Exception as e:
            logger.exception(f"monitor loop error: {e}")
        await asyncio.sleep(FUND_MONITOR_INTERVAL)

# --------------------- تسجيل handlers وبدء المونيتور ---------------------
async def setup(application):
    """
    استدعِ هذه الدالة مرة واحدة من main.py بعد إنشاء Application:
      await checker.setup(application)
    """
    # handlers
    application.add_handler(CallbackQueryHandler(check_again_callback, pattern="^check_sub$"))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat_members))
    # ابدأ مهمة المراقبة (خلفية)
    try:
        application.create_task(monitor_channel_counts(application))
    except Exception:
        logger.exception("failed to start monitor task")

    logger.info("checker.setup: handlers registered and monitor started.")
