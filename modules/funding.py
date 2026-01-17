import sys, os, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

# تأمين استيراد قاعدة البيانات
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

# المسمى الجديد للزر في القائمة الرئيسية
MAIN_BUTTON = "📢 إضافة تمويل"

async def setup(application):
    # ربط عمليات الأزرار
    application.add_handler(CallbackQueryHandler(manage_funding, pattern="^(add_ch|list_ch|del_ch_|nav_funding|main_menu)$"))
    # ربط مستقبل روابط التمويل بأولوية معينة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_channel), group=2)

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الواجهة الرئيسية عند الضغط على زر 'إضافة تمويل'"""
    user_id = update.effective_user.id
    
    text = (
        "🚀 **مركز تمويل ونمو القنوات**\n"
        "━━━━━━━━━━━━━━━\n\n"
        "هنا يمكنك إضافة قناتك لزيادة أعضائها بشكل حقيقي.\n\n"
        "• **الإضافة:** ربط قناة جديدة لبدء التمويل.\n"
        "• **العرض:** متابعة قنواتك الحالية أو حذفها.\n"
    )
    keyboard = [
        [InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="add_ch")],
        [InlineKeyboardButton("📂 قنواتي المضافة", callback_data="list_ch")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="main_menu")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def manage_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if data == "main_menu":
        # الرجوع للقائمة الرئيسية (يتم معالجته في main.py عادةً، ولكن هنا نوجهه لإعادة تشغيل start)
        from main import get_main_reply_keyboard
        kb = await get_main_reply_keyboard(user_id)
        await query.message.delete()
        await context.bot.send_message(user_id, "🏠 عدت إلى القائمة الرئيسية:", reply_markup=kb)
        return

    if data == "add_ch":
        context.user_data['waiting_for_funding_link'] = True
        await query.edit_message_text(
            "📥 **أرسل رابط قناتك الآن (أو اليوزر @):**\n\n"
            "⚠️ تأكد من رفع البوت مشرفاً في القناة أولاً لضمان عمل نظام التمويل.",
            parse_mode="Markdown"
        )

    elif data == "list_ch":
        user_channels = list(db.db.channels.find({"owner_id": user_id}))
        if not user_channels:
            return await query.answer("❌ ليس لديك قنوات مضافة حالياً.", show_alert=True)
        
        text = "📂 **قنواتك المسجلة في نظام التمويل:**\n\n"
        keyboard = []
        for ch in user_channels:
            text += f"▪️ {ch['title']} (@{ch['username'].replace('@','')})\n"
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف {ch['title']}", callback_data=f"del_ch_{ch['channel_id']}")])
        
        keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data="nav_funding")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("del_ch_"):
        ch_id = int(data.replace("del_ch_", ""))
        db.db.channels.delete_one({"channel_id": ch_id, "owner_id": user_id})
        await query.answer("✅ تم حذف القناة من نظام التمويل.")
        await show_main(update, context)

    elif data == "nav_funding":
        await show_main(update, context)

async def handle_new_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرابط المرسل للتمويل"""
    # التحقق هل المستخدم ضغط على زر إضافة تمويل؟
    if not context.user_data.get('waiting_for_funding_link'):
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # استخراج اليوزر
    username = text.replace("https://t.me/", "").replace("@", "").split('/')[0]
    
    try:
        # فحص القناة
        chat = await context.bot.get_chat(f"@{username}")
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        
        if member.status not in ['administrator', 'creator']:
            # نرسل التنبيه مرة واحدة فقط وننهي الانتظار إذا رغبت، أو نتركه يحاول مرة أخرى
            return await update.message.reply_text("❌ البوت ليس مشرفاً! ارفعه مشرفاً ثم أرسل الرابط مرة أخرى.")

        m_count = await context.bot.get_chat_member_count(chat.id)
        
        # حفظ في قاعدة البيانات (جدول التمويل المنفصل عن اللستة)
        db.db.channels.update_one(
            {"channel_id": chat.id},
            {"$set": {
                "owner_id": user_id,
                "username": f"@{username}",
                "title": chat.title,
                "member_count": m_count,
                "created_at": asyncio.get_event_loop().time()
            }}, upsert=True
        )

        context.user_data['waiting_for_funding_link'] = False # إغلاق حالة الانتظار
        
        # رسالة النجاح التوجيهية
        success_msg = (
            f"✅ **تم إضافة قناتك بنجاح!**\n\n"
            f"📢 **القناة:** {chat.title}\n"
            f"👥 **الأعضاء:** `{m_count}`\n"
            f"🔗 **الرابط:** @{username}\n\n"
            f"💡 **كيف تحصل على أعضاء؟**\n"
            f"لكسب مقابل كل عضو ينضم لقناتك **8 أعضاء** حقيقيين، عليك الذهاب الآن إلى زر **(👥 نظام الإحالات)** ومشاركة رابطك الخاص. إذا دعوت 10 أشخاص ستحصل على **80 عضواً** لقناتك!\n\n"
            f"🔄 **خيار آخر:**\n"
            f"يمكنك أيضاً تفعيل زر **(🔄  ادارة الاعلان )** لتبادل الإعلانات مجاناً مع مئات القنوات الأخرى."
        )
        
        await update.message.reply_text(success_msg, parse_mode="Markdown")

    except Exception:
        # رسالة خطأ واحدة ذكية
        await update.message.reply_text("⚠️ **خطأ في الرابط!**\nتأكد أن القناة عامة، وأن الرابط صحيح، وأن البوت مشرف فيها بصلاحيات كاملة.")
