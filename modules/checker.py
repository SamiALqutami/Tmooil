import sys, os, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

# تأمين المسارات لضمان الوصول لقاعدة البيانات
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

# الإعدادات الرسمية المطلوبة
OFFICIAL_CHANNEL_URL = "https://t.me/ML554H"
OFFICIAL_CHANNEL_ID = -1003645403007 

async def setup(application):
    """ربط معالج زر التحقق ليعمل في كافة أرجاء البوت"""
    application.add_handler(CallbackQueryHandler(check_again_callback, pattern="^check_sub$"))

async def is_user_member(bot, chat_id, user_id):
    """فحص اشتراك حقيقي ودقيق"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        # السماح للعضو، المشرف، المالك، والمقيد (restricted)
        return member.status in ['member', 'administrator', 'creator', 'restricted']
    except Exception:
        return False

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المحرك الرئيسي - يضمن عدم ظهور الرسالة للمشتركين الفعليين"""
    user_id = update.effective_user.id
    
    # 1. جلب بيانات المستخدم
    user_data = db.db.users.find_one({"user_id": user_id}) or {}
    referrer_id = user_data.get("referred_by")

    # 2. فحص القناة الرسمية أولاً (الأولوية القصوى)
    is_in_official = await is_user_member(context.bot, OFFICIAL_CHANNEL_ID, user_id)
    
    # 3. إذا كان مشتركاً في القناة الرسمية
    if is_in_official:
        # أ) إذا كان مستخدماً عادياً (بدون إحالة) -> اسمح له فوراً واختم حسابه
        if not referrer_id:
            db.db.users.update_one({"user_id": user_id}, {"$set": {"is_verified": True}})
            return True
        
        # ب) إذا كان قادماً عبر إحالة وقد أكمل الـ 5 قنوات سابقاً
        if user_data.get("reward_complete") or user_data.get("is_verified"):
            return True

    # 4. بناء قائمة القنوات المطلوبة (للمحيلين أو غير المشتركين بالرسمية)
    required_channels = []
    
    # القناة الرسمية تظهر دائماً كأول قناة إذا لم يشترك
    if not is_in_official:
        required_channels.append({"title": "قناة البوت الرسمية ✅", "url": OFFICIAL_CHANNEL_URL})

    # إذا كان لديه محيل، نضيف قنوات عشوائية حتى يصل المجموع لـ 5
    if referrer_id and not user_data.get("reward_complete"):
        funding_channels = list(db.db.channels.find({"channel_id": {"$ne": OFFICIAL_CHANNEL_ID}}).limit(10))
        for ch in funding_channels:
            if len(required_channels) >= 5: break
            if not await is_user_member(context.bot, ch['channel_id'], user_id):
                required_channels.append({
                    "title": ch['title'], 
                    "url": f"https://t.me/{ch['username'].replace('@','')}"
                })

    # 5. عرض الرسالة إذا وجد نقص في الاشتراكات
    if required_channels:
        # إرسال 4 أعضاء للمحيل (نصف الجائزة) عند أول ظهور للرسالة
        if referrer_id and not user_data.get("notified_step1"):
            await notify_referrer_step1(context, referrer_id)
            db.db.users.update_one({"user_id": user_id}, {"$set": {"notified_step1": True}})
            
        await send_sub_msg(update, context, required_channels[0], bool(referrer_id))
        return False

    # 6. النجاح النهائي (المستخدم اشترك في كل شيء)
    if referrer_id and not user_data.get("reward_complete"):
        await give_final_rewards(context, referrer_id, user_id)
        db.db.users.update_one({"user_id": user_id}, {"$set": {"reward_complete": True}})
    
    # ختم المستخدم لمنع ظهور الرسالة مستقبلاً
    db.db.users.update_one({"user_id": user_id}, {"$set": {"is_verified": True}})
    return True

async def send_sub_msg(update, context, channel, is_ref):
    """إظهار قناة واحدة فقط لتسهيل العملية على المستخدم"""
    if is_ref:
        text = (
            "⚠️ **عذراً، يجب عليك الإشتراك في 5 قنوات للدخول إلى عالم التمويل المجاني**\n\n"
            "اشترك في القناة أدناه ثم اضغط على زر (التحقق) للبدء في حصد الأعضاء مجاناً 🚀\n\n"
            "💡 يمكنك أيضاً إرسال أمر /start احتياطاً إذا لم يعمل الزر.\n\n"
            f"📍 القناة الحالية: **{channel['title']}**"
        )
    else:
        text = "⚠️ **يجب عليك الاشتراك في قناة البوت الرسمية أولاً للاستمرار:**"

    kb = [
        [InlineKeyboardButton(f"📢 اشترك هنا: {channel['title']}", url=channel['url'])],
        [InlineKeyboardButton("✅ التحقق من الاشتراك", callback_data="check_sub")]
    ]
    
    # التعديل بدل الإرسال الجديد لمنع إغراق الشات
    if update.callback_query:
        try: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except: pass
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def check_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج زر التحقق - هو القلب النابض للنظام"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # نقوم بإعادة الفحص
    if await check_subscription(update, context):
        await query.answer("✅ تم التحقق بنجاح! أهلاً بك.", show_alert=True)
        try: await query.message.delete()
        except: pass
        # العودة للقائمة الرئيسية
        from main import start
        await start(update, context)
    else:
        await query.answer("❌ لم تشترك في القناة المطلوبة بعد، يرجى الاشتراك والمحاولة مرة أخرى.", show_alert=True)

# --- [ دوال المكافآت ] ---

async def notify_referrer_step1(context, r_id):
    try:
        db.db.users.update_one({"user_id": r_id}, {"$inc": {"funded_remaining": 4}})
        await context.bot.send_message(r_id, "👤 **إحالة جديدة!** انضم مستخدم عبر رابطك، حصلت على **4 أعضاء** مؤقتاً، وستحصل على 4 إضافيين فور تفاعله.")
    except: pass

async def give_final_rewards(context, r_id, user_id):
    try:
        # إضافة 4 أعضاء إضافيين للمحيل (المجموع 8)
        db.db.users.update_one({"user_id": r_id}, {"$inc": {"funded_remaining": 4}})
        await context.bot.send_message(r_id, "✅ **تفاعل الإحالة!** اكتملت اشتراكات المستخدم، تم منحك 4 أعضاء إضافيين (الإجمالي 8).")
        
        # جائزة الحفيد (4 أعضاء للجد)
        ref_data = db.db.users.find_one({"user_id": r_id})
        if ref_data and ref_data.get("referred_by"):
            grandparent_id = ref_data["referred_by"]
            db.db.users.update_one({"user_id": grandparent_id}, {"$inc": {"funded_remaining": 4}})
            await context.bot.send_message(grandparent_id, "🎁 **جائزة الحفيد!** حصلت على 4 أعضاء إضافيين بسبب نشاط شبكتك.")
    except: pass
