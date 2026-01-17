import sys, os, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

MAIN_BUTTON = "🔄 إدارة اعلان قناتك"
# حالات الحوار (Conversation States)
SET_AD_TEXT, SET_AD_PHOTO, SET_GOAL = range(3)

async def setup(application):
    # معالج الأزرار التفاعلية بنظام Regex شامل
    application.add_handler(CallbackQueryHandler(manage_actions, pattern="^(manage_list_|toggle_list_|view_ad_|list_main).*$"))
    
    # حوار إعداد الإعلان (نص + صورة + هدف)
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_ad_text, pattern="^set_ad_"),
            CallbackQueryHandler(ask_goal, pattern="^set_goal_")
        ],
        states={
            SET_AD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ad_text)],
            SET_AD_PHOTO: [
                MessageHandler(filters.PHOTO, save_ad_photo),
                MessageHandler(filters.TEXT & filters.Regex("^تخطي$"), skip_photo)
            ],
            SET_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_goal)]
        },
        fallbacks=[CallbackQueryHandler(show_main, pattern="^list_main$")],
        allow_reentry=True
    )
    application.add_handler(conv)

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = list(db.db.list_channels.find({"owner_id": user_id}))
    
    if not channels:
        msg = "📂 **لا توجد قنوات مضافة.**\nاستخدم زر '➕ إضافة قناة' أولاً."
        if update.callback_query: await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        else: await update.message.reply_text(msg, parse_mode="Markdown")
        return

    text = "📂 **قنواتك في نظام اللستة:**\nاختر قناة للتحكم في النشر والإعلانات:"
    kb = [[InlineKeyboardButton(f"{'🟢' if c.get('list_active') else '🔴'} {c['title']}", callback_data=f"manage_list_{c['channel_id']}")] for c in channels]
    
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def manage_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "list_main": return await show_main(update, context)
    
    ch_id = int(data.split("_")[-1])
    ch = db.db.list_channels.find_one({"channel_id": ch_id})

    if data.startswith("toggle_list_"):
        new_st = not ch.get("list_active", False)
        db.db.list_channels.update_one({"channel_id": ch_id}, {"$set": {"list_active": new_st}})
        alert = "🚀 تم تفعيل النشر! سيظهر إعلانك في القنوات الأخرى فوراً." if new_st else "🛑 تم إيقاف النشر."
        await query.answer(alert, show_alert=True)
        return await show_manage_panel(query, ch_id)

    if data.startswith("view_ad_"):
        return await preview_ad(query, ch)

    await show_manage_panel(query, ch_id)

async def show_manage_panel(query, ch_id):
    ch = db.db.list_channels.find_one({"channel_id": ch_id})
    status = "🟢 نشط (إعلانك ينشر الآن)" if ch.get("list_active") else "🔴 متوقف (إعلانك مخفي)"
    
    text = (
        f"⚙️ **إدارة القناة: {ch['title']}**\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📢 **حالة النشر:** {status}\n"
        f"🎯 **هدف الأعضاء:** `{ch.get('custom_target', 0)}` عضو\n"
        f"━━━━━━━━━━━━━━━\n"
        "⚠️ **قواعد النظام الصارمة:**\n"
        "1️⃣ لا تحذف أي إعلان ينشر في قناتك، سيقوم النظام بإعادة نشره أو حظرك.\n"
        "2️⃣ لا تلغِ صلاحيات البوت، وإلا سيتم حذف إعلانك من جميع القنوات الأخرى فوراً.\n"
        "3️⃣ التبادل يتم بشكل عادل (كل 6 ساعات ينتقل إعلانك لقناة جديدة).\n"
    )
    
    kb = [
        [InlineKeyboardButton("✅ تشغيل / إيقاف النشر", callback_data=f"toggle_list_{ch_id}")],
        [InlineKeyboardButton("📝 ضبط الإعلان (نص وصورة)", callback_data=f"set_ad_{ch_id}")],
        [InlineKeyboardButton("🎯 تحديد هدف الأعضاء", callback_data=f"set_goal_{ch_id}")],
        [InlineKeyboardButton("👁️ عرض الإعلان الحالي", callback_data=f"view_ad_{ch_id}")],
        [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="list_main")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# --- [ حوار إعداد الإعلان ] ---

async def ask_ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['tmp_ch'] = query.data.split("_")[-1]
    await query.edit_message_text("📝 **أرسل نص الإعلان الآن:**\n(يجب ألا يتجاوز 300 حرف، سيتم إضافة زر الانضمام تلقائياً)")
    return SET_AD_TEXT

async def save_ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ad_text'] = update.message.text[:300]
    await update.message.reply_text("📸 **أرسل صورة الإعلان الآن:**\n(أو أرسل كلمة `تخطي` إذا كنت تريد إعلاناً نصياً فقط)")
    return SET_AD_PHOTO

async def save_ad_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id
    ch_id = int(context.user_data['tmp_ch'])
    db.db.list_channels.update_one({"channel_id": ch_id}, {"$set": {"ad_text": context.user_data['ad_text'], "ad_photo": photo_id}})
    await update.message.reply_text("✅ **تم حفظ الإعلان بالصورة!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ العودة للإدارة", callback_data=f"manage_list_{ch_id}")]]))
    return ConversationHandler.END

async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ch_id = int(context.user_data['tmp_ch'])
    db.db.list_channels.update_one({"channel_id": ch_id}, {"$set": {"ad_text": context.user_data['ad_text'], "ad_photo": None}})
    await update.message.reply_text("✅ **تم حفظ الإعلان (نص فقط)!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ العودة للإدارة", callback_data=f"manage_list_{ch_id}")]]))
    return ConversationHandler.END

# --- [ عرض الإعلان (Preview) ] ---

async def preview_ad(query, ch):
    text = f"🖼️ **معاينة إعلانك:**\n\n{ch.get('ad_text', 'لا يوجد نص')}"
    kb = [
        [InlineKeyboardButton("✅ انضمام للقناة", url=f"https://t.me/{ch['username'].replace('@','')}")],
        [InlineKeyboardButton("❌ تجاهل الإعلان", callback_data="ignore_ad")]
    ]
    
    if ch.get('ad_photo'):
        await query.message.reply_photo(ch['ad_photo'], caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    
    await query.answer("هذا هو الشكل الذي سيظهر به إعلانك")

# --- [ الأهداف ] ---

async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data['tmp_ch'] = query.data.split("_")[-1]
    await query.edit_message_text("🎯 **كم عدد الأعضاء الذين تطمح لجذبهم؟**\nأرسل رقماً فقط (مثلاً: 100)")
    return SET_GOAL

async def save_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("⚠️ يرجى إرسال أرقام فقط!")
        return SET_GOAL
    ch_id = int(context.user_data['tmp_ch'])
    db.db.list_channels.update_one({"channel_id": ch_id}, {"$set": {"custom_target": int(update.message.text)}})
    await update.message.reply_text("✅ **تم تحديد الهدف بنجاح!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ العودة للإدارة", callback_data=f"manage_list_{ch_id}")]]))
    return ConversationHandler.END
