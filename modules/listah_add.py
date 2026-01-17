import sys, os, asyncio, re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

# الزر الذي سيظهر في القائمة الرئيسية تلقائياً
MAIN_BUTTON = "➕إضافة قناة للاعلان"

async def setup(application):
    # ربط ضغطة الزر
    application.add_handler(CallbackQueryHandler(start_add_process, pattern="^add_to_list$"))
    # ربط مستقبل الروابط بأولوية عالية جداً (Group -1 لضمان الاستجابة قبل الموديولات الأخرى)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_link), group=-1)

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تظهر عند الضغط على الزر من القائمة الرئيسية (Keyboard)"""
    user_id = update.effective_user.id
    context.user_data['waiting_for_list_link'] = True # تفعيل وضع الانتظار
    
    text = (
        "➕ **إضافة قناة لنظام اللستة**\n"
        "━━━━━━━━━━━━━━━\n"
        "1️⃣ ارفع البوت مشرفاً في قناتك.\n"
        "2️⃣ ارفع صلاحيات (النشر + دعوة المستخدمين).\n"
        "3️⃣ أرسل رابط القناة الآن (أو اليوزر @).\n\n"
        "⚠️ البوت سيفحص الرابط تلقائياً."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def start_add_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تظهر عند الضغط على الزر المدمج (Inline)"""
    query = update.callback_query
    context.user_data['waiting_for_list_link'] = True
    await query.edit_message_text("📥 أرسل رابط القناة الآن (أو اليوزر @) لإضافتها للستة:")

async def handle_incoming_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المعالج الرئيسي الذي يفرض نفسه عند إرسال رابط"""
    # التحقق هل المستخدم ضغط على زر الإضافة أولاً؟
    if not context.user_data.get('waiting_for_list_link'):
        return # إذا لم يضغط الزر، نتجاهل الرسالة لتذهب لموديولات أخرى مثل التمويل

    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # رسالة الانتظار
    wait_msg = await update.message.reply_text("⏳ **انتظر 5 ثواني.. جاري فحص الرابط وصلاحيات البوت...**")
    await asyncio.sleep(2) # محاكاة الفحص

    # استخراج اليوزر من الرابط
    username = text.replace("https://t.me/", "").replace("@", "").split('/')[0]
    
    if not username:
        await wait_msg.edit_text("❌ **الرابط الذي أرسلته غير صحيح!**\nيرجى إرسال رابط صالح مثل: `https://t.me/example`")
        return

    try:
        # محاولة جلب معلومات القناة
        chat = await context.bot.get_chat(f"@{username}")
        
        # التأكد أنها قناة
        if chat.type != "channel":
            await wait_msg.edit_text("⚠️ **عذراً، يجب أن يكون الرابط لقناة عامة وليس مجموعة أو حساب شخصي.**")
            return

        # فحص الصلاحيات
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if member.status not in ['administrator', 'creator']:
            await wait_msg.edit_text("❌ **البوت ليس مشرفاً!**\nارفع البوت مشرفاً في القناة أولاً ثم أعد المحاولة.")
            return

        # فحص صلاحيات محددة (النشر ودعوة المستخدمين)
        if not (member.can_post_messages and member.can_invite_users):
            await wait_msg.edit_text("⚠️ **نقص في الصلاحيات!**\nيرجى منح البوت صلاحية (نشر الرسائل) و (دعوة المستخدمين عبر الرابط).")
            return

        # جلب عدد الأعضاء
        members_count = await context.bot.get_chat_member_count(chat.id)
        
        # حفظ البيانات في قاعدة البيانات
        db.db.list_channels.update_one(
            {"channel_id": chat.id},
            {"$set": {
                "owner_id": user_id,
                "username": f"@{username}",
                "title": chat.title,
                "member_count": members_count,
                "list_active": False, # تبدأ غير مفعلة حتى يفعلها المستخدم
                "yield_score": 0,
                "total_clicks": 0,
                "ad_text": "لم يتم ضبط نص الإعلان بعد"
            }}, upsert=True
        )

        # إيقاف وضع الانتظار
        context.user_data['waiting_for_list_link'] = False

        # رسالة النجاح النهائية
        success_text = (
            f"✅ **تم إضافة قناتك بنجاح!**\n\n"
            f"📢 **القناة:** {chat.title}\n"
            f"🔗 **الرابط:** @{username}\n"
            f"👥 **الأعضاء:** `{members_count}`\n"
            f"👀 **مشاهدات القناة:** `{int(members_count * 0.4)}` (تقديري)\n\n"
            f"⚙️ **الخطوة التالية:**\n"
            f"اذهب الآن إلى زر **(🔄 إدارة اللستة)** لتفعيل القناة وضبط نص الإعلان الخاص بك."
        )
        
        kb = [[InlineKeyboardButton("🔄 اذهب للإدارة", callback_data=f"manage_list_{chat.id}")]]
        await wait_msg.edit_text(success_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    except Exception as e:
        await wait_msg.edit_text(f"❌ **فشل الربط!**\nتأكد أن القناة عامة (@) وأن البوت مشرف فيها.\n_الخطأ: {str(e)[:50]}_")
