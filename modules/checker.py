# modules/checker.py
# Checker (اشتراك إجباري محسن)
# لا يحتوي على MAIN_BUTTON (لن يغرز زر في main تلقائياً)
# استدعِ check_subscription(update, context) من main.start
# متوافق مع python-telegram-bot v20+ و MongoDB (db.db)

import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

# path fix: main, db, config موجودة بجانب modules/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db
from config import Config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- Configurable ----------------
BOT_NAME = getattr(Config, "BOT_NAME", "بوت التمويل")
BOT_USERNAME = getattr(Config, "BOT_USERNAME", None)
ADMIN_ID = getattr(Config, "ADMIN_ID", None)

# القناة الرسمية المطلوبة (مثال: "@ML5044" في Config.REQUIRED_GROUP)
OFFICIAL_CHANNEL_RAW = getattr(Config, "REQUIRED_GROUP", None) or getattr(Config, "REQUIRED_CHANNEL", None) or getattr(Config, "REQUIRED_GROUP", None)
OFFICIAL_CHANNEL = None
if OFFICIAL_CHANNEL_RAW:
    OFFICIAL_CHANNEL = str(OFFICIAL_CHANNEL_RAW).strip().lstrip("@")

FORCE_LIMIT = getattr(Config, "FORCE_SUB_LIMIT", 10)   # نعرض حتى 10 قنوات في الاشتراك الإجباري
REQUIRED_COUNT = getattr(Config, "REQUIRED_COUNT", FORCE_LIMIT) # مطلوب اشتراك (عادة 10)
SUB_COST = getattr(Config, "SUB_COST", 15)            # يُخصم من صاحب القناة عند انضمام مستخدم

# مكافآت الإحالة — عند إتمام إحالة كاملة
REF_BONUS_MEMBERS = getattr(Config, "REF_BONUS_MEMBERS", 20)  # كم عضو يعادل كل إحالة
REF_BONUS_POINTS = getattr(Config, "REF_BONUS_POINTS", 300)   # نقاط تُعطى للمحيل عند اكتمال إحالة

VALID_STATUSES = ("member", "administrator", "creator", "restricted")

# ---------------- تلغرام آمن helpers ----------------
async def _safe_get_chat(bot, identifier: Any):
    try:
        return await bot.get_chat(identifier)
    except Exception as e:
        logger.debug(f"_safe_get_chat({identifier}) -> {e}")
        return None

async def _safe_get_chat_member(bot, chat_id: Any, user_id: int):
    """
    نُعيد None في حالة أي استثناء لنعتبرها 'pending' لاحقاً إذا رغبت بذلك.
    """
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.debug(f"_safe_get_chat_member({chat_id},{user_id}) -> {e}")
        return None

async def bot_has_admin_permissions(bot, chat_identifier: Any) -> bool:
    """
    تحقق مرن لصلاحيات البوت في القناة/المجموعة (يقبل غياب بعض الأعلام).
    """
    try:
        me = await bot.get_me()
        m = await _safe_get_chat_member(bot, chat_identifier, me.id)
        if not m:
            return False
        status = getattr(m, "status", None)
        if status not in ("administrator", "creator"):
            return False
        # تحقق فقط إذا كانت الخاصيات موجودة
        if hasattr(m, "can_post_messages") and not getattr(m, "can_post_messages", True):
            return False
        if hasattr(m, "can_invite_users") and not getattr(m, "can_invite_users", True):
            return False
        return True
    except Exception as e:
        logger.debug(f"bot_has_admin_permissions error: {e}")
        return False

def normalize_username(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("@"):
        s = s[1:]
    return s or None

# ---------------- DB helpers ----------------
def get_force_channels_from_db(limit: int = FORCE_LIMIT) -> List[Dict]:
    try:
        return list(db.db.channels.find({"force_sub": True, "active": True}).limit(limit))
    except Exception:
        logger.exception("get_force_channels_from_db")
        return []

def mark_channel_deactivated(channel_id: Any, reason: str = "bot_lost_admin"):
    try:
        db.db.channels.update_one({"channel_id": channel_id}, {"$set": {"active": False, "deactivated_reason": reason, "deactivated_at": datetime.utcnow()}})
    except Exception:
        logger.exception("mark_channel_deactivated")

def get_active_funding_channels(limit: int = 5) -> List[Dict]:
    try:
        return list(db.db.channels.find({"active": True}).sort("created_at", -1).limit(limit))
    except Exception:
        return []

# ---------------- بناء قائمة الاشتراك للمستخدم ----------------
async def build_force_queue_for_user(bot, user_id: int) -> List[Dict]:
    """
    - تضم القناة الرسمية أولاً إن وُجدت.
    - تجلب قنوات force_sub من DB وتستبعد:
        * القنوات التي فقد فيها البوت صلاحياته (وَتُعلّم inactive)
        * القنوات التي المستخدم مشترك فيها (status in VALID_STATUSES) -> لا نعرضها
    - تُعيد حتى FORCE_LIMIT عناصر.
    """
    queue: List[Dict] = []

    # 1) official channel (نضيفها أولاً إن وُجدت)
    if OFFICIAL_CHANNEL:
        try:
            chat = await _safe_get_chat(bot, f"@{OFFICIAL_CHANNEL}")
            if chat:
                queue.append({
                    "title": getattr(chat, "title", "القناة الرسمية"),
                    "username": f"@{OFFICIAL_CHANNEL}",
                    "channel_id": f"@{OFFICIAL_CHANNEL}",
                    "owner_id": None
                })
        except Exception:
            logger.debug("official channel not reachable (skipped)")

    # 2) قنوات من DB
    force_chs = get_force_channels_from_db(limit=FORCE_LIMIT * 2)
    for ch in force_chs:
        ch_id = ch.get("channel_id")
        # تحقق صلاحيات البوت إذا كان ID رقمي (مجموعات/قنوات خاصة)
        try:
            if isinstance(ch_id, int):
                ok = await bot_has_admin_permissions(bot, ch_id)
                if not ok:
                    mark_channel_deactivated(ch_id, "bot_lost_admin")
                    continue
        except Exception:
            logger.debug("bot admin check error; continuing")

        # تحقق إن المستخدم مشترك حالياً => لا نعرض القناة
        try:
            member = await _safe_get_chat_member(bot, ch_id, user_id)
            status = getattr(member, "status", None) if member else None
            if status in VALID_STATUSES:
                continue  # المستخدم مشترك حالياً -> لا نعرضها
            # إذا status == 'left' أو 'kicked' -> نعرض (المستخدم غادر مسبقاً)
            # إذا member is None -> نعرض (يعني نحتاج فحص/قدّم طلب انضمام)
        except Exception:
            pass

        queue.append({
            "title": ch.get("title") or ch.get("username") or str(ch_id),
            "username": ch.get("username"),
            "channel_id": ch_id,
            "owner_id": ch.get("owner_id")
        })
        if len(queue) >= FORCE_LIMIT:
            break

    # dedupe & limit
    seen = set()
    out = []
    for it in queue:
        key = str(it.get("channel_id") or it.get("username"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= FORCE_LIMIT:
            break
    return out

# ---------------- رسائل / UI ----------------
def welcome_intro_text() -> str:
    return (
        "👋 <b>أهلاً بك في بوت التمويل</b> 🎁\n\n"
        "هنا يمكنك:\n"
        "• زيادة أعضاء قناتك\n"
        "• كسب نقاط حقيقية\n"
        "• الحصول على <b>100 عضو مقابل 5 دعوات فقط</b>\n\n"
        "⚠️ قبل البدء، اشترك في القنوات التالية لتفعيل حسابك:"
    )

def channel_card_text(channel: Dict, remaining: int) -> str:
    title = channel.get("title", "قناة")
    username = channel.get("username")
    header = f"🔔 القناة التالية للانضمام — تبقّى <b>{remaining}</b>"
    body = f"\n\n• <b>{title}</b>\n"
    if username:
        body += f"رابط: @{username.lstrip('@')}\n"
    return header + body

# ---------------- إرسال الواجهة للمستخدم ----------------
async def send_subscription_prompt_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot = context.bot

    queue = await build_force_queue_for_user(bot, user.id)
    if not queue:
        # لا توجد قنوات لعرضها الآن
        text = f"{welcome_intro_text()}\n\n⚠️ حالياً لا توجد قنوات للاشتراك. حاول لاحقًا."
        kb = [[InlineKeyboardButton("🔙 رجوع", callback_data="sub_back")]]
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    required = min(REQUIRED_COUNT, max(1, len(queue)))
    queue = queue[:required]
    context.user_data['force_queue'] = queue
    context.user_data['force_required'] = required

    first = queue[0]
    remaining = required - 0
    text = welcome_intro_text()
    card = channel_card_text(first, remaining)
    kb = []
    if first.get("username"):
        kb.append([InlineKeyboardButton("📢 افتح القناة للاشتراك", url=f"https://t.me/{first['username'].lstrip('@')}")])
    else:
        kb.append([InlineKeyboardButton("🔍 فتح (لا يوجد يوزر)", callback_data="sub_no_link")])
    kb.append([InlineKeyboardButton("✅ تحقق", callback_data="sub_verify")])
    kb.append([InlineKeyboardButton("🔙 إلغاء/رجوع", callback_data="sub_back")])

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(f"{text}\n\n{card}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
            return
        except Exception:
            pass
    await update.effective_message.reply_text(f"{text}\n\n{card}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)

# ---------------- الدالة العامة check_subscription (لـ main) ----------------
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    تُستدعى من main.start.
    => True  : المستخدم مفعل ويمكنه الاستمرار.
    => False : أرسلنا له واجهة الاشتراك التفاعلية (وليس مفعل بعد).
    """
    user = update.effective_user
    if not user:
        return False

    user_doc = db.db.users.find_one({"user_id": user.id}) or {}
    if user_doc.get("force_sub_done"):
        return True

    # لم يُفعّل بعد -> أعرض له واجهة الاشتراك
    await send_subscription_prompt_for_user(update, context)
    return False

# ---------------- Verify callback (عند الضغط على ✅) ----------------
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    bot = context.bot

    queue: List[Dict] = context.user_data.get('force_queue', [])
    required: int = context.user_data.get('force_required', REQUIRED_COUNT)

    if not queue:
        try:
            await query.edit_message_text("لا توجد قنوات للتحقق منها حالياً. استخدم /start للبدء.")
        except Exception:
            await query.answer("لا توجد قنوات.", show_alert=True)
        return

    current = queue[0]
    chat_identifier = current.get("channel_id") or current.get("username")
    chat_id_real = None
    try:
        if isinstance(chat_identifier, str) and str(chat_identifier).startswith("@"):
            chat = await _safe_get_chat(bot, chat_identifier)
            if not chat:
                await query.answer("لم أتمكن من الوصول للقناة الآن. تأكد من صحة الرابط.", show_alert=True)
                return
            chat_id_real = chat.id
        else:
            chat_id_real = chat_identifier
    except Exception:
        chat_id_real = chat_identifier

    # فحص صلاحيات البوت للقناة (إن كانت ID رقمية)
    try:
        if isinstance(chat_id_real, int):
            ok = await bot_has_admin_permissions(bot, chat_id_real)
            if not ok:
                mark_channel_deactivated(chat_id_real, "bot_lost_admin")
                # اسحب هذه القناة من القائمة وتابع التالي
                queue.pop(0)
                context.user_data['force_queue'] = queue
                if queue:
                    next_ch = queue[0]
                    remaining = max(0, required - (required - len(queue)))
                    card = channel_card_text(next_ch, remaining)
                    kb = []
                    if next_ch.get("username"):
                        kb.append([InlineKeyboardButton("📢 افتح القناة للاشتراك", url=f"https://t.me/{next_ch['username'].lstrip('@')}")])
                    else:
                        kb.append([InlineKeyboardButton("🔍 فتح (لا يوجد يوزر)", callback_data="sub_no_link")])
                    kb.append([InlineKeyboardButton("✅ تحقق", callback_data="sub_verify")])
                    kb.append([InlineKeyboardButton("🔙 إلغاء/رجوع", callback_data="sub_back")])
                    await query.edit_message_text(f"⚠️ تم استبعاد قناة لأن البوت فقد صلاحياته.\n\n{card}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
                    return
                else:
                    await query.edit_message_text("⚠️ لا توجد قنوات متبقية بعد استبعاد القنوات التي فقدت صلاحيات البوت.")
                    return
    except Exception:
        logger.debug("bot admin permissions check failed in verify")

    # فحص عضوية المستخدم
    try:
        member = await _safe_get_chat_member(bot, chat_id_real, user.id)
        status = getattr(member, "status", None) if member else None
    except Exception:
        member = None
        status = None

    # قبول: إذا كان status في VALID_STATUSES أو member is None (نعامل تقديم الطلب كاشتراك)
    if status in VALID_STATUSES or member is None:
        # قبول الاشتراك: تحديث DB، خصم SUB_COST، إشعار المالك والمستخدم
        try:
            ch_doc = None
            if isinstance(chat_id_real, int):
                ch_doc = db.db.channels.find_one({"channel_id": chat_id_real})
            else:
                uname = normalize_username(current.get("username") or chat_identifier)
                if uname:
                    ch_doc = db.db.channels.find_one({"username": "@" + uname}) or db.db.channels.find_one({"username": uname})
            if ch_doc:
                owner = ch_doc.get("owner_id")
                if owner:
                    db.db.users.update_one({"user_id": owner}, {"$inc": {"points": -SUB_COST}}, upsert=True)
                db.db.channels.update_one({"channel_id": ch_doc.get("channel_id")}, {"$inc": {"achieved_members": 1, "member_count": 1}}, upsert=False)
                # notify owner (one-line)
                try:
                    display = user.first_name or f"user:{user.id}"
                    await bot.send_message(owner, f"🔔 انضم مستخدم جديد إلى قناتك: {display}")
                except Exception:
                    logger.debug("notify owner failed")
                # notify joining user
                try:
                    await bot.send_message(user.id, f"✅ تم انضمامك إلى {ch_doc.get('title') or ch_doc.get('username')}")
                except Exception:
                    logger.debug("notify joining user failed")
        except Exception:
            logger.exception("processing accepted join")

        # ازالة القناة من قائمة المستخدم
        queue.pop(0)
        context.user_data['force_queue'] = queue

        # إظهار التالي أو إتمام
        if queue:
            next_ch = queue[0]
            remaining = max(0, required - (required - len(queue)))
            card = channel_card_text(next_ch, remaining)
            kb = []
            if next_ch.get("username"):
                kb.append([InlineKeyboardButton("📢 افتح القناة للاشتراك", url=f"https://t.me/{next_ch['username'].lstrip('@')}")])
            else:
                kb.append([InlineKeyboardButton("🔍 فتح (لا يوجد يوزر)", callback_data="sub_no_link")])
            kb.append([InlineKeyboardButton("✅ تحقق", callback_data="sub_verify")])
            kb.append([InlineKeyboardButton("🔙 إلغاء/رجوع", callback_data="sub_back")])
            try:
                await query.edit_message_text(f"✅ تم احتساب اشتراكك في هذه القناة!\n\n{card}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
            except Exception:
                await query.answer("تم الاشتراك — انتقل للقناة التالية.", show_alert=True)
            return
        else:
            # اكتمال القائمة
            try:
                db.db.users.update_one({"user_id": user.id}, {"$set": {"force_sub_done": True, "force_sub_at": datetime.utcnow()}}, upsert=True)
            except Exception:
                logger.exception("mark force_sub_done failed")
            # احساب الإحالة الآن
            ref = context.user_data.pop("referrer", None)
            if ref:
                try:
                    db.db.users.update_one({"user_id": ref}, {"$inc": {"referrals_count": 1, "points": REF_BONUS_POINTS, "total_received_members": REF_BONUS_MEMBERS}}, upsert=True)
                    try:
                        await bot.send_message(ref, f"🎉 تم احتساب إحالتك! لقد كُسبت {REF_BONUS_POINTS} نقطة و {REF_BONUS_MEMBERS} عضوًا افتراضيًا كمكافأة.")
                    except Exception:
                        pass
                except Exception:
                    logger.exception("process referral error")

            # رسالة النجاح النهائية مع عرض قنوات التمويل النشطة
            active_channels = get_active_funding_channels(limit=5)
            kb = []
            if BOT_USERNAME:
                kb.append([InlineKeyboardButton("/start", url=f"https://t.me/{BOT_USERNAME}?start={user.id}")])
            for ch in active_channels:
                uname = normalize_username(ch.get("username"))
                title = ch.get("title") or ch.get("username") or "قناة"
                if uname:
                    kb.append([InlineKeyboardButton(f"📢 {title}", url=f"https://t.me/{uname}")])

            success_text = (
                "✅ <b>تم تفعيل حسابك بنجاح!</b>\n\n"
                "🎉 يمكنك الآن استخدام جميع ميزات البوت والبدء بالربح.\n\n"
                "👋 <b>أهلاً بك في بوت التمويل</b> 🎁\n\n"
                "هنا يمكنك:\n"
                "• زيادة أعضاء قناتك\n"
                "• كسب نقاط حقيقية\n"
                "• الحصول على <b>100 عضو مقابل 5 دعوات فقط</b>\n\n"
                "⚠️ قبل البدء، اشترك في القنوات التالية لتفعيل حسابك:\n\n"
            )
            try:
                await query.edit_message_text(success_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb) if kb else None, disable_web_page_preview=True)
            except Exception:
                try:
                    await bot.send_message(user.id, success_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb) if kb else None)
                except Exception:
                    logger.exception("send final success failed")
            return
    else:
        # لم يُشترك بعد
        await query.answer("❌ لم نر أنك مشترك بعد. افتح القناة واضغط طلب انضمام/اشتراك ثم اضغط تحقق.", show_alert=True)
        return

# إلغاء / رجوع
async def back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_text("تم إلغاء العملية. استخدم /start للبدء من جديد.")
    except Exception:
        await query.answer("تم الإلغاء.", show_alert=True)

# expose show_main (يمكن main استدعاؤها إن رغبت لعرض الموديول يدوياً)
async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_prompt_for_user(update, context)

# تسجيل الهاندلرز
async def setup(application):
    # تسجيل callback handlers
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^sub_verify$"))
    application.add_handler(CallbackQueryHandler(back_callback, pattern="^sub_back$"))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^sub_no_link$"))  # إذا ضغط فتح بدون يوزر
    logger.info("checker module loaded (no MAIN_BUTTON)")

# تصدير الدوال العامة لاستخدامها من main
# check_subscription(update, context) -> استدعاؤها من main.start
# show_main(update, context) -> إذا رغبت في عرض واجهة الموديول يدوياً من main
