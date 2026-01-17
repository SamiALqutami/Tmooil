import sys, os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

# تأمين المسارات
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- الإعدادات ---
# هذا النص هو ما سيظهر في القائمة الرئيسية للبوت
MAIN_BUTTON = "👨‍💻 التواصل مع الدعم" 
OFFICIAL_CHANNEL_LINK = "https://t.me/ML5044"
ADMIN_USERNAME = "@SamiAlqutami"

async def setup(application):
    """
    هذه الدالة يتم استدعاؤها تلقائياً من main.py عند تشغيل البوت.
    تقوم بتسجيل الموديول وزره في النظام.
    """
    # ليس هناك حاجة لمعالجات خاصة هنا لأن main.py يتعرف على MAIN_BUTTON تلقائياً
    pass

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هذه الدالة يتم استدعاؤها فور ضغط المستخدم على زر 'التواصل مع الدعم'
    """
    text = (
        "👋 **مرحباً بك في مركز الدعم الفني**\n"
        "━━━━━━━━━━━━━━━\n\n"
        "نحن هنا لمساعدتك، يمكنك اختيار أحد الخيارات التالية:\n\n"
        "📖 **تعليمات البوت:** لمعرفة كيفية استخدام أنظمة التمويل واللستة.\n"
        "👨‍💼 **مراسلة المشرف:** للتواصل المباشر عند وجود مشكلة تقنية.\n"
    )

    # بناء الأزرار الشفافة (Inline)
    keyboard = [
        [InlineKeyboardButton("📖 تعليمات البوت", url=OFFICIAL_CHANNEL_LINK)],
        [InlineKeyboardButton("👨‍💼 مراسلة المشرف", url=f"https://t.me/{ADMIN_USERNAME.replace('@','')}")],
    ]

    # إرسال الرسالة
    if update.message:
        await update.message.reply_text(
            text, 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )

