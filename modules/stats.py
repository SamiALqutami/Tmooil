import sys, os
from telegram import Update
from telegram.ext import ContextTypes

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

MAIN_BUTTON = "📊 إحصائيات التمويل"

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # 1. بيانات المستخدم الشخصية
    user_data = db.db.users.find_one({"user_id": user_id}) or {}
    ref_count = user_data.get("referrals_count", 0)
    funded_remaining = user_data.get("funded_remaining", 0)
    total_received = user_data.get("total_received", 0)

    # 2. إحصائيات الشبكة (استخراج عدد الأعضاء الكلي)
    total_channels = db.db.channels.count_documents({})
    
    # عملية الجمع البرمجية لعدد الأعضاء
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$member_count"}}}]
    members_res = list(db.db.channels.aggregate(pipeline))
    total_members = members_res[0]['total'] if members_res else 0

    # 3. حساب الترتيب العالمي
    rank = db.db.users.count_documents({"referrals_count": {"$gt": ref_count}}) + 1

    text = (
        "📊 **تقرير الأداء والنمو**\n"
        "━━━━━━━━━━━━━━━\n\n"
        "👤 **إحصائياتك الشخصية:**\n"
        f"🏆 ترتيبك العالمي: `{rank}#`\n"
        f"👥 عدد دعواتك: `{ref_count}`\n"
        f"✅ منضمون لقنواتك: `{total_received}` عضو\n"
        f"⏳ تمويل متبقي: `{funded_remaining}` عضو\n\n"
        "🌐 **إحصائيات البوت الكلية:**\n"
        f"📢 قنوات ومجموعات: `{total_channels}`\n"
        f"💎 إجمالي أعضاء البوت: `{total_members:,}` عضو\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💡 *شارك رابطك الآن لزيادة ترتيبك العالمي والحصول على تمويل ضخم!* 🚀"
    )

    await update.message.reply_text(text, parse_mode="Markdown")
