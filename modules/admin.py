# modules/admin.py
# لوحة إدارة متكاملة — محدثة لضمان استجابة الزر (Reply Keyboard) وInline buttons.
# MAIN_BUTTON يضمن ظهور زر في القائمة الرئيسية (main) باسم "زر لوحة المشرف".
# متوافق مع python-telegram-bot v20+ و MongoDB (db.db)

import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, CommandHandler, filters

# تمكين استيراد الوحدات العليا (main, db, config)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db
from config import Config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------- الإعداد العام للملف ----------------
MAIN_BUTTON = "زر لوحة المشرف"   # هذا النص سيظهر في القائمة الرئيسية عبر main
ADMIN_ID = getattr(Config, "ADMIN_ID", None)

# إعدادات سريعة
MAX_LIST_DISPLAY = 100
BATCH_SEND_DELAY = 0.03  # وقت قصير بين الإرسالات في حال الحاجة (ثوانٍ)

# ---------------- مساعدات ----------------
def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and int(user_id) == int(ADMIN_ID)

async def ensure_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """إذا لم يكن المشرف، نرد برسالة ونرجع False"""
    user = update.effective_user
    if not user:
        return False
    if not is_admin(user.id):
        try:
            if update.callback_query:
                await update.callback_query.answer("❌ هذه الواجهة للمشرف فقط.", show_alert=True)
            else:
                await update.effective_message.reply_text("❌ هذه الواجهة مخصّصة للمشرف فقط.")
        except Exception:
            pass
        return False
    return True

def fmt_user(u: Dict[str, Any]) -> str:
    uname = u.get("username")
    if uname:
        return f"{u.get('first_name','-')} (@{uname}) — <code>{u.get('user_id')}</code>"
    return f"{u.get('first_name','-')} — <code>{u.get('user_id')}</code>"

# ---------------- واجهة الإدارة (عرض رئيسي) ----------------
async def show_admin_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الواجهة الرئيسية للوحة الإدارة"""
    if not await ensure_admin(update, context):
        return

    text = (
        "<b>🛠️ لوحة المشرف — إدارة البوت</b>\n\n"
        "اختر إجراء:\n"
        "• منح نقاط — إرسال رسائل — إحصائيات — عرض قنوات — نشر في القنوات"
    )
    kb = [
        [InlineKeyboardButton("➕ منح نقاط لمستخدم", callback_data="adm_grant_user"),
         InlineKeyboardButton("➕ منح نقاط للجميع", callback_data="adm_grant_all")],
        [InlineKeyboardButton("📩 مراسلة مستخدم", callback_data="adm_msg_user"),
         InlineKeyboardButton("📢 مراسلة الجميع", callback_data="adm_broadcast")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="adm_stats"),
         InlineKeyboardButton("📂 عرض قنوات/مجموعات", callback_data="adm_list_channels")],
        [InlineKeyboardButton("📣 نشر في كل القنوات", callback_data="adm_broadcast_channels"),
         InlineKeyboardButton("📣 نشر في قناة/مجموعة", callback_data="adm_broadcast_single")],
        [InlineKeyboardButton("👥 عرض المستخدمين", callback_data="adm_list_users")]
    ]

    # إن جاء الطلب عن طريق زر قائمة Reply Keyboard (نص) فإن update.message موجود
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            return
        except Exception:
            pass

    # رد كرسالة عادية (عند الضغط على زر من قائمة main يتم استدعاء show_main -> show_admin_main)
    try:
        # أرسِل زر Reply Keyboard بسيط للمشرف يسهّل العودة
        reply_kb = ReplyKeyboardMarkup([[KeyboardButton(MAIN_BUTTON)]], resize_keyboard=True)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        try:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("failed to send admin main")

# هذا wrapper يستخدمه main (عند ضغط زر القائمة)
async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_admin_main(update, context)

# ---------------- معالجات الإدخال النصي (حالات المشرف) ----------------
async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة نصية لجميع الحالات الإدارية التي تنتظر إدخال نص من المشرف"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    action = context.user_data.get("admin_action")
    text = (update.message.text or "").strip()

    # منح نقاط لمستخدم (context.user_data['admin_action']=='grant_user_wait')
    if action == "grant_user_wait":
        # دعم: reply with number OR "id points" OR "@username points"
        target_id = None
        points = None
        replied = update.message.reply_to_message
        if replied and text.isdigit():
            # نأخذ آي دي المستخدم من الرسالة المردودة
            points = int(text)
            if replied.from_user:
                target_id = replied.from_user.id
        else:
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                who, pts = parts[0], parts[1]
                try:
                    points = int(pts)
                except:
                    points = None
                if who.startswith("@"):
                    uname = who.lstrip("@")
                    udoc = db.db.users.find_one({"username": uname})
                    if udoc:
                        target_id = udoc.get("user_id")
                else:
                    try:
                        target_id = int(who)
                    except:
                        target_id = None
        if not target_id or points is None:
            await update.message.reply_text("❌ المدخل غير صالح. ارسل: `@username 50` أو `12345 50` أو قم بالرد على رسالة المستخدم و اكتب `50`.", parse_mode=ParseMode.HTML)
            context.user_data.pop("admin_action", None)
            return
        db.db.users.update_one({"user_id": target_id}, {"$inc": {"points": points}}, upsert=True)
        await update.message.reply_text(f"✅ تم منح {points} نقطة للمستخدم <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
        try:
            await context.bot.send_message(target_id, f"🎁 تم إضافة {points} نقطة لحسابك بواسطة المشرف.")
        except Exception:
            pass
        context.user_data.pop("admin_action", None)
        return

    # منح نقاط للجميع
    if action == "grant_all_wait":
        try:
            pts = int(text)
        except:
            await update.message.reply_text("❌ ارسل عدد صحيح من النقاط (مثال: 20).")
            context.user_data.pop("admin_action", None)
            return
        res = db.db.users.update_many({}, {"$inc": {"points": pts}})
        count = res.matched_count if res else 0
        await update.message.reply_text(f"✅ تم منح {pts} نقطة إلى {count} مستخدمًا.")
        context.user_data.pop("admin_action", None)
        return

    # مراسلة مستخدم واحد
    if action == "msg_user_wait":
        replied = update.message.reply_to_message
        msg_text = None
        target = None
        if replied:
            target = replied.from_user.id if replied.from_user else None
            msg_text = text
        else:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("❌ ارسل: `@username نص الرسالة` أو قم بالرد على رسالة المستخدم مع نص الرسالة.")
                context.user_data.pop("admin_action", None)
                return
            who, msg_text = parts[0], parts[1]
            if who.startswith("@"):
                uname = who.lstrip("@")
                udoc = db.db.users.find_one({"username": uname})
                if udoc:
                    target = udoc.get("user_id")
            else:
                try:
                    target = int(who)
                except:
                    target = None
        if not target:
            await update.message.reply_text("❌ لم أجد المستخدم المستهدف. تأكد من @username أو استخدم الرد على رسالة المستخدم.")
            context.user_data.pop("admin_action", None)
            return
        try:
            await context.bot.send_message(target, msg_text, parse_mode=ParseMode.HTML)
            await update.message.reply_text("✅ تم إرسال الرسالة.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إرسال الرسالة: {e}")
        context.user_data.pop("admin_action", None)
        return

    # مراسلة الجميع
    if action == "broadcast_wait":
        body = text
        if not body:
            await update.message.reply_text("❌ اكتب نص الرسالة لإرسالها لجميع المستخدمين.")
            context.user_data.pop("admin_action", None)
            return
        sent = 0
        failed = 0
        cursor = db.db.users.find({})
        for u in cursor:
            uid = u.get("user_id")
            try:
                await context.bot.send_message(uid, body, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"✅ الإرسال اكتمل. نجح: {sent} — فشل: {failed}")
        context.user_data.pop("admin_action", None)
        return

    # نشر في كل القنوات
    if action == "broadcast_channels_wait":
        body = text
        if not body:
            await update.message.reply_text("❌ اكتب نص الرسالة للنشر في القنوات.")
            context.user_data.pop("admin_action", None)
            return
        channels = list(db.db.channels.find({"active": True}))
        sent = 0
        failed = 0
        for ch in channels:
            ch_id = ch.get("channel_id")
            try:
                await context.bot.send_message(ch_id, body, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"✅ النشر اكتمل. نجح: {sent} — فشل: {failed}")
        context.user_data.pop("admin_action", None)
        return

    # نشر في قناة مفردة (context.user_data['admin_target_channel'])
    if action == "broadcast_single_wait":
        ch_id = context.user_data.pop("admin_target_channel", None)
        body = text
        if not ch_id or not body:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد القناة أو نص الرسالة.")
            context.user_data.pop("admin_action", None)
            return
        try:
            await context.bot.send_message(ch_id, body, parse_mode=ParseMode.HTML)
            await update.message.reply_text("✅ تم النشر في القناة المحددة.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل النشر: {e}")
        context.user_data.pop("admin_action", None)
        return

    # لا حالة إدارية حالية -> تجاهل
    return

# ---------------- Callback handler لإدارة النقرات ----------------
async def manage_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = query.from_user
    if not is_admin(user.id):
        try:
            await query.edit_message_text("❌ هذه الواجهة للمشرف فقط.")
        except Exception:
            pass
        return

    data = query.data

    # الصفحة الرئيسية
    if data == "adm_home":
        return await show_admin_main(update, context)

    # منح نقاط لمستخدم
    if data == "adm_grant_user":
        context.user_data['admin_action'] = 'grant_user_wait'
        await query.edit_message_text("📌 أرسل الآن: `@username 50` أو `12345 50` أو قم بالرد على رسالة المستخدم واكتب `50`.")
        return

    # منح نقاط للجميع
    if data == "adm_grant_all":
        context.user_data['admin_action'] = 'grant_all_wait'
        await query.edit_message_text("📌 أرسل الآن عدد النقاط لمنحها لكل المستخدمين (مثال: `20`).")
        return

    # مراسلة مستخدم
    if data == "adm_msg_user":
        context.user_data['admin_action'] = 'msg_user_wait'
        await query.edit_message_text("📌 أرسل الآن: `@username رسالة` أو قم بالرد على رسالة المستخدم واكتب نص الرسالة.")
        return

    # مراسلة الجميع
    if data == "adm_broadcast":
        context.user_data['admin_action'] = 'broadcast_wait'
        await query.edit_message_text("📣 أرسل الآن نص الرسالة التي تريد إرسالها إلى كل المستخدمين.")
        return

    # إحصائيات
    if data == "adm_stats":
        users_count = db.db.users.count_documents({})
        channels_count = db.db.channels.count_documents({})
        active_channels = db.db.channels.count_documents({"active": True})
        total_members = 0
        for ch in db.db.channels.find({}):
            total_members += int(ch.get("member_count", 0))
        text = (
            "<b>📊 إحصائيات البوت</b>\n\n"
            f"👥 عدد مستخدمي البوت: <b>{users_count}</b>\n"
            f"📂 عدد القنوات/المجموعات المسجلة: <b>{channels_count}</b>\n"
            f"✅ عدد القنوات/المجموعات الفعّالة: <b>{active_channels}</b>\n"
            f"👥 إجمالي أعضاء القنوات (مجموع): <b>{total_members}</b>\n"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
        return

    # عرض القنوات/المجموعات
    if data == "adm_list_channels":
        channels = list(db.db.channels.find({}).limit(MAX_LIST_DISPLAY))
        if not channels:
            await query.edit_message_text("⚠️ لا توجد قنوات مسجلة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
            return
        lines = ["<b>📂 قنوات/مجموعات مسجلة (موجز)</b>\n"]
        kb = []
        for ch in channels:
            title = ch.get("title") or ch.get("username") or str(ch.get("channel_id"))
            ch_id = ch.get("channel_id")
            owner = ch.get("owner_id") or "-"
            members = ch.get("member_count", 0)
            active = "✅" if ch.get("active") else "❌"
            lines.append(f"• {title} — {active} — <code>{members}</code> عضو — مالك: <code>{owner}</code>")
            kb.append([InlineKeyboardButton(f"عرض: {title}", callback_data=f"adm_channel_{ch_id}")])
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")])
        await query.edit_message_text("\n".join(lines[:4000]), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    # عرض تفاصيل قناة معينة
    if data.startswith("adm_channel_"):
        ch_raw = data.replace("adm_channel_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        ch = db.db.channels.find_one({"channel_id": ch_id}) or db.db.channels.find_one({"username": ch_raw})
        if not ch:
            await query.edit_message_text("⚠️ القناة غير موجودة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
            return
        title = ch.get("title") or ch.get("username") or str(ch.get("channel_id"))
        uname = ch.get("username") or "-"
        owner = ch.get("owner_id") or "-"
        members = ch.get("member_count", 0)
        active = "✅ مفعل" if ch.get("active") else "❌ غير مفعل"
        text = (
            f"<b>تفاصيل القناة/المجموعة</b>\n\n"
            f"• الاسم: <b>{title}</b>\n"
            f"• يوزر: {uname}\n"
            f"• آيدي: <code>{ch.get('channel_id')}</code>\n"
            f"• المالك: <code>{owner}</code>\n"
            f"• الأعضاء: <code>{members}</code>\n"
            f"• الحالة: {active}\n"
        )
        kb = [
            [InlineKeyboardButton("🔁 نشر هنا", callback_data=f"adm_pub_here_{ch.get('channel_id')}"),
             InlineKeyboardButton("🛑 تعطيل/حذف", callback_data=f"adm_disable_{ch.get('channel_id')}")],
            [InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]
        ]
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    # تعطيل قناة
    if data.startswith("adm_disable_"):
        ch_raw = data.replace("adm_disable_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        db.db.channels.update_one({"channel_id": ch_id}, {"$set": {"active": False, "deactivated_reason": "admin_disabled", "deactivated_at": datetime.utcnow()}})
        await query.edit_message_text("✅ تم تعطيل/حذف القناة من النظام.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
        return

    # نشر في القناة المحددة
    if data.startswith("adm_pub_here_"):
        ch_raw = data.replace("adm_pub_here_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        context.user_data['admin_action'] = 'broadcast_single_wait'
        context.user_data['admin_target_channel'] = ch_id
        await query.edit_message_text("📣 الآن أرسل نص الرسالة التي تريد نشرها في هذه القناة/المجموعة:")
        return

    # نشر في كل القنوات
    if data == "adm_broadcast_channels":
        context.user_data['admin_action'] = 'broadcast_channels_wait'
        await query.edit_message_text("📣 أرسل الآن نص الرسالة للنشر في كل القنوات/المجموعات المسجلة:")
        return

    # نشر في قناة/مجموعة واحدة (اختيار)
    if data == "adm_broadcast_single":
        channels = list(db.db.channels.find({"active": True}).limit(MAX_LIST_DISPLAY))
        if not channels:
            await query.edit_message_text("⚠️ لا توجد قنوات/مجموعات فعّالة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
            return
        kb = []
        for ch in channels:
            title = ch.get("title") or ch.get("username") or str(ch.get("channel_id"))
            kb.append([InlineKeyboardButton(title, callback_data=f"adm_choose_pub_{ch.get('channel_id')}")])
        kb.append([InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")])
        await query.edit_message_text("اختر القناة/المجموعة للنشر فيها:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_choose_pub_"):
        ch_raw = data.replace("adm_choose_pub_", "")
        try:
            ch_id = int(ch_raw)
        except:
            ch_id = ch_raw
        context.user_data['admin_action'] = 'broadcast_single_wait'
        context.user_data['admin_target_channel'] = ch_id
        await query.edit_message_text("📣 الآن أرسل نص الرسالة التي تريد نشرها في القناة/المجموعة المحددة:")
        return

    # عرض المستخدمين
    if data == "adm_list_users":
        users = list(db.db.users.find({}).limit(MAX_LIST_DISPLAY))
        if not users:
            await query.edit_message_text("⚠️ لا يوجد مستخدمين مسجلين.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]))
            return
        lines = ["<b>👥 مستخدمو البوت (عرض موجز)</b>\n"]
        for u in users:
            lines.append(fmt_user(u))
        lines.append("\n")
        kb = [[InlineKeyboardButton("🏠 رجوع", callback_data="adm_home")]]
        await query.edit_message_text("\n".join(lines[:4000]), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    # افتراضي
    await query.answer()

# ---------------- إعداد الموديول (ربط handlers) ----------------
async def setup(application):
    # Handlers للضغطات ضمن لوحة الإدارة
    application.add_handler(CallbackQueryHandler(manage_admin_callbacks, pattern="^adm_"))

    # معالج للمدخلات النصية المتعلقة بالمشرف (يعمل بمجموعة عالية لالتقاطها قبل معالجات أخرى)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_text), group=3)

    # أمر احتياطي لفتح لوحة الإدارة
    application.add_handler(CommandHandler("admin", show_admin_main))

    logger.info("admin module loaded — MAIN_BUTTON='%s' (appears in main)", MAIN_BUTTON)