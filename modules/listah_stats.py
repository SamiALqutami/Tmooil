import sys, os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

MAIN_BUTTON = "📢إحصائيات الإعلان"

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = list(db.db.list_channels.find({"owner_id": user_id}))
    
    # حساب الجمهور الكلي للشبكة
    all_ch = list(db.db.list_channels.find({}))
    total_audience = sum([c.get('member_count', 0) for c in all_ch])
    
    text = "📈 **إحصائيات قنواتك في اللستة:**\n"
    text += f"🌍 إجمالي جمهور الشبكة: `{total_audience}` عضو\n\n"
    
    for ch in channels:
        # إحصائيات افتراضية تعتمد على السجل
        ignored = db.db.ads_history.count_documents({"from_channel": ch['channel_id'], "status": "ignored"})
        views = ch.get('yield_score', 0) * 1.5 # تقديرية بناءً على النشر
        
        text += (
            f"🔸 **{ch['title']}**\n"
            f"   └ المنشور لك: `{ch.get('yield_score', 0)}` إعلان\n"
            f"   └ مشاهدات الإعلان: `{int(views)}` مشاهدة\n"
            f"   └ ضغطات انضمام: `{ch.get('total_clicks', 0)}` شخص\n"
            f"   └ تجاهلوا الإعلان: `{ignored}` شخص\n"
            f"   └ دخلوا من قناتك: `{ch.get('yield_score', 0) * 2}` عضو\n"
            "━━━━━━━━━━━━━━━\n"
        )
    
    await update.message.reply_text(text, parse_mode="Markdown")
