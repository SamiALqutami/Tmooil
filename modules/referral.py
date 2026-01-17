import sys, os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db
from config import Config

MAIN_BUTTON = "👥 نظام الإحالات"

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    
    # نص الإعلان الجذاب عند مشاركة الرابط
    share_text = (
        f"🥇 بوت تمويل أعضاء: اكسب مقابل كل عضو 8 أعضاء!\n\n"
        f"قم بدعوة 10 أشخاص إلى البوت ويمكنك تمويل قناتك بـ 80 عضواً مجاناً 🎁\n\n"
        f"ابدأ الآن عبر الرابط التالي:\n{ref_link}"
    )
    
    # رابط المشاركة المباشر
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"

    text = (
        "👥 **نظام الإحالات الذكي**\n"
        "━━━━━━━━━━━━━━━\n\n"
        "🔥 قم بدعوة شخص واحد فقط واحصل على **8 أعضاء** لقناتك!\n"
        "🎁 نظام الإحالات مفتوح وغير محدود.\n\n"
        "استخدم الزر بالأسفل لمشاركة الرابط فوراً 👇"
    )
    
    keyboard = [[InlineKeyboardButton("🔗 مشاركة رابط الدعوة", url=share_url)]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def process_referral(user, referrer_id, context):
    if referrer_id and referrer_id != user.id:
        # إضافة 8 أعضاء لواجب التمويل (الأب)
        db.db.users.update_one({"user_id": referrer_id}, {"$inc": {"funded_remaining": 8, "referrals_count": 1}})
        
        # تنبيه للأب
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🥳 **مبروك! انضم شخص عبر رابطك**\nلقد حصلت على تمويل لـ **8 أعضاء** إضافيين بقناتك! 🔥"
            )
        except: pass
        
        # إشعار للمشرف عن انضمام عضو جديد
        try:
            admin_msg = f"👤 **انضمام جديد للبوت**\n\n" \
                        f"• الاسم: {user.first_name}\n" \
                        f"• اليوزر: @{user.username if user.username else 'لا يوجد'}\n" \
                        f"• المعرف: `{user.id}`"
            await context.bot.send_message(chat_id=Config.ADMIN_ID, text=admin_msg, parse_mode="Markdown")
        except: pass
