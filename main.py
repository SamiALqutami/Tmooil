import os
import importlib
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from config import Config
from db import db

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- [ بناء القائمة الأساسية ] ---

async def get_main_reply_keyboard(user_id):
    """توليد أزرار أساسية ديناميكية من الموديولات المحقونة"""
    buttons = []
    
    # جلب الأزرار من الموديولات المسجلة في Config
    for mod_path, button_text in Config.DYNAMIC_BUTTONS.items():
        buttons.append(KeyboardButton(button_text))
    
    # إضافة زر الإدارة للمشرف فقط
    if user_id == Config.ADMIN_ID:
        buttons.append(KeyboardButton("🛠️ لوحة الإدارة"))
        
    # توزيع الأزرار (2 في كل صف)
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="اختر من القائمة أدناه...")

# --- [ محرك الحقن التلقائي ] ---

async def load_modules(application):
    """تحميل الموديولات وربط الأزرار والـ Handlers تلقائياً"""
    modules_dir = os.path.join(os.path.dirname(__file__), "modules")
    if not os.path.exists(modules_dir):
        os.makedirs(modules_dir)
        return

    for filename in os.listdir(modules_dir):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = f"modules.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                
                # تنفيذ دالة setup لربط أي معالجات إضافية
                if hasattr(module, "setup"):
                    await module.setup(application)
                    
                # تسجيل الزر الرئيسي للموديول
                if hasattr(module, "MAIN_BUTTON"):
                    Config.DYNAMIC_BUTTONS[module_name] = module.MAIN_BUTTON
                    logger.info(f"✅ تم حقن موديول: {filename}")
            except Exception as e:
                logger.error(f"⚠️ خطأ أثناء تحميل الموديول {filename}: {e}")

# --- [ المعالجات الرئيسية ] ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر البداية والإحالات"""
    user = update.effective_user
    
    # 1. معالجة نظام الإحالة (المستوى الأول والثاني)
    referrer_id = None
    if context.args and context.args[0].isdigit():
        referrer_id = int(context.args[0])
    
    # استدعاء موديول الإحالات لمعالجة النقاط والتنبيهات
    try:
        from modules.referral import process_referral
        await process_referral(user, referrer_id, context)
    except Exception as e:
        logger.error(f"Error in referral processing: {e}")

    # 2. التحقق من الاشتراك الإجباري قبل الدخول
    try:
        from modules.checker import check_subscription
        if not await check_subscription(update, context):
            return # توقف إذا لم يشترك
    except ImportError:
        logger.warning("Checker module not found, skipping sub check.")

    # 3. عرض القائمة الرئيسية
    reply_markup = await get_main_reply_keyboard(user.id)
    welcome_text = (
        f"🏆 **مرحباً بك يا {user.first_name} في بوت التمويل!**\n\n"
        "لقد تم تفعيل حسابك بنجاح. استخدم الأزرار أدناه للتحكم 👇"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من ضغط الأزرار الأساسية أو إرسال روابط القنوات"""
    text = update.message.text
    user_id = update.effective_user.id

    # أولاً: التحقق من الاشتراك الإجباري في كل ضغطة زر (لضمان بقائه في القنوات)
    try:
        from modules.checker import check_subscription
        if not await check_subscription(update, context):
            return
    except: pass

    # ثانياً: البحث عن الموديول المطابق لنص الزر
    for mod_path, button_text in Config.DYNAMIC_BUTTONS.items():
        if text == button_text:
            module = importlib.import_module(mod_path)
            if hasattr(module, "show_main"):
                return await module.show_main(update, context)

    # ثالثاً: إذا أرسل المستخدم رابط قناة (لموديول التمويل)
    if text.startswith("https://t.me/"):
        try:
            from modules.funding import handle_new_channel
            return await handle_new_channel(update, context)
        except: pass

    # رابعاً: لوحة الإدارة
    if text == "🛠️ لوحة الإدارة" and user_id == Config.ADMIN_ID:
        try:
            from modules.admin import admin_panel
            return await admin_panel(update, context)
        except: pass

# --- [ تشغيل البوت ] ---

def main():
    # إنشاء التطبيق
    application = Application.builder().token(Config.BOT_TOKEN).build()

    # تحميل الموديولات قبل البدء
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    loop.run_until_complete(load_modules(application))

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("🚀 البوت يعمل الآن بنظام الأزرار الأساسية والتمويل الذكي...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
