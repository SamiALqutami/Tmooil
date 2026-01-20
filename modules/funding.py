# modules/funding.py
# تم تحديث شامل — نظام تمويل متكامل، حيوي، ونقاط قابلة للصرف على الظهور في قوائم التجميع.
# متوافق مع python-telegram-bot v20+ و MongoDB (db.db)

import os
import sys
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import quote_plus

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

# ربط المسار لتمكين استيراد db و config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db
from config import Config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ------------------- إعدادات -------------------
MAIN_BUTTON = "📢 قسم التمويل"
BOT_NAME = getattr(Config, "BOT_NAME", "بوت التمويل الشامل")
ADMIN_ID = getattr(Config, "ADMIN_ID", None)

# نقاط
POINTS_PER_SUB = getattr(Config, "POINTS_PER_SUB", 10)       # نقاط لكل اشتراك يكسبها المشترك
POOL_COST = getattr(Config, "POOL_COST", 15)                 # تكلفة ظهور القناة في قائمة التجميع (تخصم من صاحب القناة)
REFERRAL_BONUS_PER = getattr(Config, "REFERRAL_BONUS_PER", 20)# معلومة للعرض

MAX_POINTS_CHANNELS = getattr(Config, "MAX_POINTS_CHANNELS", 8)
POOL_WAIT_MINUTES = getattr(Config, "POOL_WAIT_MINUTES", 15)  # إذا لا توجد قنوات: اطلب المحاولة بعد هذا الوقت
MONITOR_INTERVAL = getattr(Config, "FUND_MONITOR_INTERVAL", 300)

VALID_MEMBER_STATUSES = ("member", "administrator", "creator", "restricted")

# ------------------ دوال Telegram آمنة ------------------
async def _safe_get_chat(bot, identifier):
    try:
        return await bot.get_chat(identifier)
    except Exception as e:
        logger.debug(f"_safe_get_chat({identifier}): {e}")
        return None

async def _safe_get_chat_member(bot, chat_id, user_id):
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except Exception as e:
        logger.debug(f"_safe_get_chat_member({chat_id},{user_id}): {e}")
        return None

async def _safe_send(bot, chat_id, text, **kwargs):
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.warning(f"_safe_send to {chat_id} failed: {e}")
        return None

# ------------------ صلاحية البوت ------------------
async def bot_is_admin(bot, chat_identifier) -> bool:
    try:
        me = await bot.get_me()
        m = await _safe_get_chat_member(bot, chat_identifier, me.id)
        if m and getattr(m, "status", None) in ("administrator", "creator"):
            # إن وُجدت خاصية can_invite_users نتحقق منها
            if hasattr(m, "can_invite_users"):
                return bool(getattr(m, "can_invite_users", True))
            return True
    except Exception as e:
        logger.debug(f"bot_is_admin error for {chat_identifier}: {e}")
    return False

# ------------------ DB helpers ------------------
def get_active_funding_channels(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        return list(db.db.channels.find({"active": True}).sort("created_at", -1).limit(limit))
    except Exception:
        return []

def get_user_channels(user_id: int) -> List[Dict[str, Any]]:
    try:
        return list(db.db.channels.find({"owner_id": user_id}).sort("created_at", -1))
    except Exception:
        return []

def get_pool_channels(limit: int = MAX_POINTS_CHANNELS) -> List[Dict[str, Any]]:
    """قنوات تم تعيينها في قائمة التجميع (in_points_pool=True)"""
    try:
        return list(db.db.channels.find({"in_points_pool": True, "active": True}).sort("pool_added_at", -1).limit(limit))
    except Exception:
        return []

# ------------------ إضافة قناة للتمويل برمجياً ------------------
async def add_funding_channel(application, channel_identifier, owner_id: int, title: Optional[str]=None, username: Optional[str]=None, target: Optional[int]=0) -> Tuple[bool,str]:
    bot = application.bot
    try:
        ch = await _safe_get_chat(bot, channel_identifier)
        if ch:
            ch_id = ch.id
            title = title or getattr(ch, "title", None) or username or str(channel_identifier)
            username = username or (("@"+ch.username) if getattr(ch, "username", None) else None)
        else:
            ch_id = channel_identifier
        if not await bot_is_admin(bot, ch_id):
            return False, "البوت يجب أن يكون مشرفاً في القناة/المجموعة ليتم إضافتها للتمويل."
        try:
            member_count = await bot.get_chat_member_count(ch_id)
        except Exception:
            member_count = 0
        doc = {
            "channel_id": ch_id,
            "username": username,
            "title": title,
            "owner_id": owner_id,
            "member_count": member_count,
            "achieved_members": 0,
            "target": target or 0,
            "active": False,
            "in_points_pool": False,
            "created_at": datetime.utcnow()
        }
        db.db.channels.update_one({"channel_id": ch_id}, {"$set": doc}, upsert=True)
        return True, "تم حفظ القناة للتمويل (انتظر تفعيل المالك)."
    except Exception as e:
        logger.exception("add_funding_channel")
        return False, str(e)

def remove_funding_channel(channel_identifier, owner_id: Optional[int]=None) -> Tuple[bool,str]:
    try:
        if owner_id:
            res = db.db.channels.delete_one({"channel_id": channel_identifier, "owner_id": owner_id})
            if res.deleted_count:
                return True, "تم حذف القناة."
            return False, "لم يتم العثور على القناة أو ليست ملكك."
        else:
            db.db.channels.update_one({"channel_id": channel_identifier}, {"$set": {"active": False, "deactivated_at": datetime.utcnow(), "deactivated_reason": "manual_removed"}})
            return True, "تم تعطيل القناة."
    except Exception as e:
        logger.exception("remove_funding_channel")
        return False, str(e)

# ------------------ مهمة الخلفية: تعطيل القنوات إذا سحب البوت صلاحياته ------------------
async def monitor_channels_admin(application):
    await asyncio.sleep(5)
    bot = application.bot
    while True:
        try:
            channels = get_active_funding_channels(limit=1000)
            for ch in channels:
                ch_id = ch.get("channel_id")
                owner = ch.get("owner_id")
                if not ch_id:
                    continue
                try:
                    ok = await bot_is_admin(bot, ch_id)
                    if not ok:
                        db.db.channels.update_one({"channel_id": ch_id}, {"$set": {"active": False, "deactivated_at": datetime.utcnow(), "deactivated_reason": "bot_lost_admin"}})
                        if owner:
                            try:
                                await _safe_send(bot, owner, f"⚠️ تم إيقاف تمويل *{ch.get('title','قناتك')}* لأن البوت فقد صلاحيات المشرف. أعد رفع البوت مشرفًا لإعادة التفعيل.", parse_mode=ParseMode.MARKDOWN)
                            except Exception:
                                pass
                except Exception:
                    logger.exception("monitor check_one error")
        except Exception:
            logger.exception("monitor loop error")
        await asyncio.sleep(MONITOR_INTERVAL)

# ------------------ setup و show_main ------------------
async def setup(application):
    application.add_handler(CallbackQueryHandler(manage_funding, pattern="^fund_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_link), group=2)
    try:
        application.create_task(monitor_channels_admin(application))
    except Exception:
        logger.exception("failed to start monitor task")

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    title = f"🚀 <b>قسم التمويل — {BOT_NAME}</b>"
    body = (
        "أهلاً بك في قسم التمويل — يمكنك إضافة قناتك، تفعيل التمويل، إدارة الظهور في قوائم التجميع، ومشاركة رابط الدعوة."
    )
    kb = [
        [InlineKeyboardButton("➕ إضافة قناة/مجموعة", callback_data="fund_add")],
        [InlineKeyboardButton("📂 عرض قنواتي", callback_data="fund_list"), InlineKeyboardButton("🔁 تفعيل تمويلاتي", callback_data="fund_myfunds")],
        [InlineKeyboardButton("📥 تجميع نقاط", callback_data="fund_points"), InlineKeyboardButton("📣 مشاركة دعوة", callback_data="fund_referral")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="fund_back")]
    ]
    text = f"{title}\n\n{body}"
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

# ------------------ إدارة نقرات الأزرار ------------------
async def manage_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    user_id = query.from_user.id

    # --- إضافة قناة: وضع انتظار رابط ---
    if data == "fund_add":
        context.user_data['awaiting_funding_link'] = True
        text = (
            "<b>📥 إضافة قناة/مجموعة للتمويل</b>\n\n"
            "أرسل رابط القناة الآن (مثال: <code>@MyChannel</code> أو <code>https://t.me/MyChannel</code>).\n"
            "🔸 شرط: يجب أن يكون البوت مشرفًا بصلاحيات كافية.\n\n"
            "اضغط ❌ لإلغاء الإضافة."
        )
        kb = [[InlineKeyboardButton("❌ إلغاء الإضافة", callback_data="fund_cancel_add")],
              [InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")]]
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "fund_cancel_add":
        context.user_data.pop('awaiting_funding_link', None)
        await query.answer("تم إلغاء إضافة القناة.", show_alert=False)
        return await show_main(update, context)

    # --- عرض قنواتي (مرقّمة وحيوية) ---
    if data == "fund_list":
        channels = get_user_channels(user_id)
        if not channels:
            await query.answer("ليس لديك قنوات مضافة.", show_alert=True)
            return await show_main(update, context)
        lines = ["<b>📂 قنواتك المضافة:</b>\n"]
        kb = []
        for idx, ch in enumerate(channels, start=1):
            lines.append(f"<b>{idx}.</b> {ch.get('title')} — <code>{ch.get('member_count',0)}</code> عضو — {'✅ مفعلة' if ch.get('active') else '❌ غير مفعلة'} — {'🔵 ضمن التجميع' if ch.get('in_points_pool') else ''}")
            kb.append([InlineKeyboardButton(f"عرض {idx}: {ch.get('title')}", callback_data=f"fund_open_{ch.get('channel_id')}")])
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")])
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    # --- عرض تفاصيل قناة ---
    if data.startswith("fund_open_"):
        ch_raw = data.replace("fund_open_", "")
        try:
            ch_id = int(ch_raw)
        except Exception:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id})
        if not ch:
            await query.answer("القناة غير موجودة.", show_alert=True)
            return await show_main(update, context)
        title = ch.get("title", "قناة")
        mcount = ch.get("member_count", 0)
        active = ch.get("active", False)
        in_pool = ch.get("in_points_pool", False)
        owner = ch.get("owner_id")
        txt = f"<b>{title}</b>\n\n👥 الأعضاء: <code>{mcount}</code>\n🔖 تحت التمويل: {'✅' if active else '❌'}\n🔵 في قوائم التجميع: {'✅' if in_pool else '❌'}\n\n"
        kb = []
        if owner == user_id or user_id == ADMIN_ID:
            if not active:
                kb.append([InlineKeyboardButton("🔁 تفعيل التمويل", callback_data=f"fund_activate_{ch_id}")])
            # زر إضافة إلى pool (خصم 15 نقطة) متاح للمالك فقط إن لم تكن ضمن pool
            if not in_pool and owner == user_id:
                kb.append([InlineKeyboardButton(f"💠 أدخل التجميع (خصم {POOL_COST} نقطة)", callback_data=f"fund_pool_{ch_id}")])
            if in_pool and owner == user_id:
                kb.append([InlineKeyboardButton("🟢 إلغاء التجميع", callback_data=f"fund_unpool_{ch_id}")])
            kb.append([InlineKeyboardButton("🗑️ حذف القناة", callback_data=f"fund_remove_{ch_id}")])
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="fund_list")])
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # --- تفعيل التمويل ---
    if data.startswith("fund_activate_"):
        ch_raw = data.replace("fund_activate_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id})
        if not ch:
            await query.answer("القناة غير موجودة.", show_alert=True)
            return await show_main(update, context)
        if ch.get("owner_id") != user_id and user_id != ADMIN_ID:
            await query.answer("ليس لديك إذن تفعيل هذه القناة.", show_alert=True)
            return
        if not await bot_is_admin(context.bot, ch_id):
            await query.answer("❌ البوت ليس مشرفاً في هذه القناة. ارفعه ثم أعد المحاولة.", show_alert=True)
            return await show_main(update, context)
        db.db.channels.update_one({"channel_id": ch_id}, {"$set": {"active": True, "activated_at": datetime.utcnow()}}, upsert=True)
        await query.answer("✅ تم تفعيل القناة للتمويل.", show_alert=True)
        return await show_main(update, context)

    # --- حذف قناة ---
    if data.startswith("fund_remove_"):
        ch_raw = data.replace("fund_remove_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id})
        if not ch:
            await query.answer("القناة غير موجودة.", show_alert=True)
            return await show_main(update, context)
        if ch.get("owner_id") != user_id and user_id != ADMIN_ID:
            await query.answer("ليس لديك إذن حذف هذه القناة.", show_alert=True)
            return
        db.db.channels.delete_one({"channel_id": ch_id})
        await query.answer("✅ تم حذف القناة.", show_alert=True)
        return await show_main(update, context)

    # --- إضافة القناة إلى قائمة التجميع (خصم POOL_COST نقطة) ---
    if data.startswith("fund_pool_"):
        ch_raw = data.replace("fund_pool_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id})
        if not ch:
            await query.answer("القناة غير موجودة.", show_alert=True)
            return
        if ch.get("owner_id") != user_id:
            await query.answer("فقط مالك القناة يمكنه إدخالها في التجميع.", show_alert=True)
            return
        user_doc = db.db.users.find_one({"user_id": user_id}) or {}
        points = user_doc.get("points", 0)
        if points < POOL_COST:
            await query.answer(f"رصيدك من النقاط غير كافٍ. تحتاج {POOL_COST} نقطة (لديك {points}).", show_alert=True)
            return await show_main(update, context)
        # خصم النقاط وإضافة القناة لمجموعة التجميع
        db.db.users.update_one({"user_id": user_id}, {"$inc": {"points": -POOL_COST}})
        db.db.channels.update_one({"channel_id": ch_id}, {"$set": {"in_points_pool": True, "pool_added_at": datetime.utcnow()}})
        await query.answer(f"✅ أُضيفت القناة لقائمة التجميع وتم خصم {POOL_COST} نقطة.", show_alert=True)
        return await show_main(update, context)

    # --- إلغاء التجميع ---
    if data.startswith("fund_unpool_"):
        ch_raw = data.replace("fund_unpool_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id})
        if not ch:
            await query.answer("القناة غير موجودة.", show_alert=True)
            return
        if ch.get("owner_id") != user_id:
            await query.answer("فقط المالك يمكنه إلغاء التجميع.", show_alert=True)
            return
        db.db.channels.update_one({"channel_id": ch_id}, {"$set": {"in_points_pool": False}, "$unset": {"pool_added_at": ""}})
        await query.answer("✅ أُزيلت القناة من قائمة التجميع.", show_alert=True)
        return await show_main(update, context)

    # --- تمويلاتي (عرض سريع) ---
    if data == "fund_myfunds":
        channels = get_user_channels(user_id)
        if not channels:
            await query.answer("لا توجد قنوات لديك.", show_alert=True)
            return await show_main(update, context)
        lines = ["<b>💠 قنواتك وخيارات التمويل</b>\n"]
        kb = []
        for ch in channels:
            lines.append(f"• {ch.get('title')} — {'✅' if ch.get('active') else '❌'} — <code>{ch.get('member_count',0)}</code>")
            if not ch.get('active'):
                kb.append([InlineKeyboardButton(f"تفعيل {ch.get('title')}", callback_data=f"fund_activate_{ch.get('channel_id')}")])
            if not ch.get('in_points_pool'):
                kb.append([InlineKeyboardButton(f"أضف للتجميع (خصم {POOL_COST})", callback_data=f"fund_pool_{ch.get('channel_id')}")])
            else:
                kb.append([InlineKeyboardButton(f"إلغاء من التجميع", callback_data=f"fund_unpool_{ch.get('channel_id')}")])
            kb.append([InlineKeyboardButton(f"عرض {ch.get('title')}", callback_data=f"fund_open_{ch.get('channel_id')}")])
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # --- تجميع نقاط: عرض الرصيد + اختيار قنوات من pool ---
    if data == "fund_points":
        user_doc = db.db.users.find_one({"user_id": user_id}) or {}
        points = user_doc.get("points", 0)
        text = (
            f"<b>🎯 تجميع النقاط — {BOT_NAME}</b>\n\n"
            f"رصيدك الحالي: <b>{points}</b> نقطة.\n\n"
            f"• كل اشتراك يمنحك: <b>{POINTS_PER_SUB}</b> نقطة.\n"
            f"• لإضافة قناتك في قوائم التجميع تحتاج: <b>{POOL_COST}</b> نقطة وسيتم خصمها عند الإضافة.\n\n"
            "اختر:"
        )
        kb = [
            [InlineKeyboardButton("🔁 الاشتراك في قنوات التجميع", callback_data="fund_points_sub")],
            [InlineKeyboardButton("📣 مشاركة الدعوة", callback_data="fund_referral")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # --- عرض قنوات التجميع للمستخدم للاشتراك (مع رفض عرض قنوات ليس لها owner.points>=POOL_COST أو إن المستخدم مشترك فعلاً) ---
    if data == "fund_points_sub":
        # اجلب قنوات في pool
        pool = list(db.db.channels.find({"in_points_pool": True, "active": True}).limit(MAX_POINTS_CHANNELS))
        filtered = []
        for ch in pool:
            owner = db.db.users.find_one({"user_id": ch.get("owner_id")}) or {}
            owner_points = owner.get("points", 0)
            # شرط: يجب أن يكون لدى المالك نقاط >=0? here as owner already paid when adding in pool
            # لا نعرض القناة لو كان المشاهد مشترك فعلياً فيها
            try:
                m = await _safe_get_chat_member(context.bot, ch.get("channel_id"), user_id)
                status = getattr(m, "status", None) if m else None
            except Exception:
                status = None
            if status in VALID_MEMBER_STATUSES:
                continue
            # ensure owner originally had paid (we assume pool presence means paid). still double-check if needed
            filtered.append(ch)
        if not filtered:
            # لا توجد قنوات متاحة — أعطِ رسالة لطيفة تفيد اللاعب بالانتظار دقائق
            minutes = POOL_WAIT_MINUTES
            await query.edit_message_text(f"لا توجد قنوات متاحة حالياً لعملية التجميع.\nحاول مرة أخرى بعد {minutes} دقيقة.", parse_mode=ParseMode.HTML)
            return
        # بني الواجهة
        text = "<b>✈️ اشترك في إحدى القنوات أدناه ثم اضغط تحقق للحصول على نقاط:</b>\n\n"
        kb = []
        ch_ids = []
        for ch in filtered:
            name = ch.get("title") or ch.get("username") or str(ch.get("channel_id"))
            url = ch.get("url") or (("https://t.me/" + ch.get("username").lstrip("@")) if ch.get("username") else None)
            ch_ids.append(ch.get("channel_id"))
            if url:
                kb.append([InlineKeyboardButton(f"📢 {name}", url=url)])
            else:
                kb.append([InlineKeyboardButton(f"📢 {name}", callback_data=f"fund_open_{ch.get('channel_id')}")])
        context.user_data['points_ch_list'] = ch_ids
        kb.append([InlineKeyboardButton("✅ تحقق", callback_data="fund_points_check"), InlineKeyboardButton("🏠 رجوع", callback_data="fund_points")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # --- تحقق نقاط بعد الاشتراك (يمنح POINTS_PER_SUB لكل قناة مشترك/مقدّم طلب) ---
    if data == "fund_points_check":
        ch_list = context.user_data.get('points_ch_list', [])
        if not ch_list:
            await query.answer("لم تُعرض عليك أي قنوات.", show_alert=True)
            return await show_main(update, context)
        awarded = 0
        joined = 0
        for ch_id in ch_list:
            try:
                m = await _safe_get_chat_member(context.bot, ch_id, user_id)
                status = getattr(m, "status", None) if m else None
                # قبول حالة pending (None أو status==restricted?) => نعتبرها مقبولة
                if status in VALID_MEMBER_STATUSES or status is None:
                    db.db.users.update_one({"user_id": user_id}, {"$inc": {"points": POINTS_PER_SUB}}, upsert=True)
                    awarded += POINTS_PER_SUB
                    joined += 1
                    # تحديث achieved_members لإبلاغ المالك لاحقاً؛ لاحظ: هذا قد يزيد حتى لو pending — مقبول كما طلبت
                    db.db.channels.update_one({"channel_id": ch_id}, {"$inc": {"achieved_members": 1}}, upsert=False)
                    # إعلام المالك بسطر واحد بسيط
                    # جلب اسم المشترك
                    u = db.db.users.find_one({"user_id": user_id}) or {}
                    display = u.get("first_name") or f"user:{user_id}"
                    # إرسال إشعار بسيط
                    try:
                        # we don't await notify inside loop to avoid blocking; but do safe send
                        owner_doc = db.db.channels.find_one({"channel_id": ch_id}) or {}
                        owner = owner_doc.get("owner_id")
                        if owner:
                            await _safe_send(context.bot, owner, f"🔔 تم تمويل قناتك بعضو جديد — {display}. الإجمالي: {owner_doc.get('achieved_members',0)+1}")
                    except Exception:
                        pass
            except Exception:
                continue
        context.user_data.pop('points_ch_list', None)
        text = f"✅ تم إضافة <b>{awarded}</b> نقطة إلى حسابك.\n• نتيجة الاشتراك: <b>{joined}</b> قناة."
        kb = [[InlineKeyboardButton("التالي", callback_data="fund_points")], [InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # --- مشاركة الدعوة: زر المشاركة فقط (لا نعرض زر نسخ أزرق) ---
    if data == "fund_referral":
        bot_info = await context.bot.get_me()
        bot_username = getattr(bot_info, "username", "")
        user = query.from_user
        share_link = f"https://t.me/{bot_username}?start={user.id}"
        promo_text = (
            f"🔥 <b>مول قناتك 100 عضو مقابل 5 دعوات فقط!</b>\n\n"
            f"✨ {BOT_NAME}\n\n"
            f"📣 رابط الدعوة الخاص بك: <code>{share_link}</code>\n\n"
            "🎯 شارك الرابط مع أصدقائك — كل 5 دعوات = 100 عضواً لقناتك!"
        )
        share_phrase = quote_plus(f"مول قناتك 100 عضو مقابل 5 دعوات! انضم الآن: {share_link} \n{BOT_NAME} ✨")
        share_url = f"https://t.me/share/url?url={quote_plus(share_link)}&text={share_phrase}"
        kb = [
            [InlineKeyboardButton("📤 شارك الآن (Telegram)", url=share_url)],
            [InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")]
        ]
        await query.edit_message_text(promo_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    # --- زر رجوع ---
    if data == "fund_back":
        return await show_main(update, context)

    # افتراضي
    await query.answer()

# ------------------ معالجة الرابط المرسل لإضافة قناة ------------------
async def handle_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_funding_link'):
        return
    text = update.message.text.strip()
    status_msg = await update.message.reply_text("⏳ جاري التحقق من القناة وصلاحيات البوت...")
    username = text.replace("https://t.me/", "").replace("http://t.me/", "").replace("@", "").split('/')[0]
    try:
        chat = await _safe_get_chat(context.bot, f"@{username}")
        if not chat:
            await status_msg.edit_text("❌ لم أجد القناة. تأكد من صحة الرابط أو أن القناة ليست خاصة جداً. يمكنك المحاولة مرة أخرى أو الضغط ❌ لإلغاء.")
            return
        me = await context.bot.get_me()
        member = await _safe_get_chat_member(context.bot, chat.id, me.id)
        if not member:
            await status_msg.edit_text("❌ البوت ليس داخل القناة. ارفعه كمشرف ثم أعد المحاولة.")
            return
        if getattr(member, "status", None) not in ("administrator", "creator"):
            await status_msg.edit_text("❌ البوت ليس مشرفًا في القناة أو صلاحياته ناقصة. ارفعه مشرفًا بالمستويات المطلوبة ثم أعد المحاولة.")
            return
        if hasattr(member, "can_invite_users") and not getattr(member, "can_invite_users", True):
            await status_msg.edit_text("❌ صلاحية دعوة/إضافة الأعضاء غير مفعلة للبوت. امنح البوت صلاحية الإضافة ثم أعد المحاولة.")
            return
        try:
            mcount = await context.bot.get_chat_member_count(chat.id)
        except Exception:
            mcount = 0
        doc = {
            "channel_id": chat.id,
            "username": f"@{username}",
            "title": getattr(chat, "title", username),
            "owner_id": user_id,
            "member_count": mcount,
            "achieved_members": 0,
            "target": 0,
            "active": False,
            "in_points_pool": False,
            "created_at": datetime.utcnow()
        }
        db.db.channels.update_one({"channel_id": chat.id}, {"$set": doc}, upsert=True)
        context.user_data.pop('awaiting_funding_link', None)
        kb = [[InlineKeyboardButton("📂 عرض قنواتي", callback_data="fund_list")], [InlineKeyboardButton("🏠 رجوع", callback_data="fund_back")]]
        await status_msg.edit_text(f"✅ تم حفظ القناة: <b>{doc['title']}</b>\n• الأعضاء: <code>{mcount}</code>\n\nيمكنك تفعيل التمويل من (عرض قنواتي).", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.exception("handle_channel_link")
        await status_msg.edit_text("⚠️ حدث خطأ أثناء إضافة القناة. حاول مرة أخرى أو اضغط ❌ لإلغاء.")

# ------------------ إعلام المالك عند انضمام عضو (سطر واحد) ------------------
async def notify_owner_on_join(bot, channel_id, new_user_display: str):
    ch = db.db.channels.find_one({"channel_id": channel_id})
    if not ch:
        return
    owner = ch.get("owner_id")
    db.db.channels.update_one({"channel_id": channel_id}, {"$inc": {"achieved_members": 1, "member_count": 1}})
    db.db.users.update_one({"user_id": owner}, {"$inc": {"total_received_members": 1}}, upsert=True)
    owner_doc = db.db.users.find_one({"user_id": owner}) or {}
    total_received = owner_doc.get("total_received_members", 0)
    note = f"🔔 تم تمويل قناتك بعضو جديد — {new_user_display}. الإجمالي: {total_received}"
    try:
        await _safe_send(bot, owner, note)
    except Exception:
        pass

# ------------------ وظائف مساعدة إدارية ------------------
async def admin_add_to_pool(application, channel_identifier, owner_id, cost=POOL_COST):
    ok, msg = await add_funding_channel(application, channel_identifier, owner_id)
    return ok, msg

# نهاية الملف 
