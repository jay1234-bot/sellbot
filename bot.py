import asyncio, io, logging, os, re, json
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import qrcode
from PIL import Image, ImageDraw
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatMember
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN       = "8755146987:AAHr9e55-e314QLVGcAAA9BFha1bimxVpC4"
ADMIN_IDS       = [8746242371, 8273908525]
ADMIN_GROUP_ID  = -1003888117383
LOG_CHANNEL_ID  = -1003934462319
FILE_CHANNEL_ID = -1003948066152   # ← change to your file storage channel
UPI_ID          = "balkrishan19@fam"
SUPPORT_LINK    = "https://t.me/censored_politics"
OWNER_LINK      = "https://t.me/censored_politics"
MONGO_URI       = "mongodb+srv://Vercel-Admin-atlas-lime-drum:611mPbyvnkOsw6It@atlas-lime-drum.b5yu3s7.mongodb.net/?retryWrites=true&w=majority"
DB_NAME         = "cprp"
IST             = timezone(timedelta(hours=5, minutes=30))
START_IMAGE_URL = "https://files.catbox.moe/k3zf5t.jpg"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)

# ─── MONGODB ─────────────────────────────────────────────────────────────────
mongo_client  = AsyncIOMotorClient(
    MONGO_URI,
    maxPoolSize=20,
    minPoolSize=5,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=5000,
)
db            = mongo_client[DB_NAME]
col_products  = db["products"]
col_orders    = db["orders"]
col_users     = db["users"]
col_deposits  = db["deposits"]
col_settings  = db["settings"]
col_channels  = db["force_channels"]
col_categories = db["categories"]

# ─── SMALL CAPS ───────────────────────────────────────────────────────────────
_SC = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"
)
def sc(t): return str(t).translate(_SC)
def b(t):  return f"<b>{sc(t)}</b>"   # bold smallcaps
def bi(t): return f"<b><i>{sc(t)}</i></b>"

# ─── UTILS ────────────────────────────────────────────────────────────────────
def now_ist():
    return datetime.now(IST)

def fmt_time(dt):
    if not dt: return sc("N/A")
    if isinstance(dt, str):
        try: dt = datetime.fromisoformat(dt)
        except: return sc(str(dt))
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return sc(dt.astimezone(IST).strftime("%d %b %Y %H:%M IST"))

async def get_setting(key, default=""):
    doc = await col_settings.find_one({"key": key})
    return doc["value"] if doc else default

async def set_setting(key, value):
    await col_settings.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)

async def get_runtime_links():
    """
    Admin-editable runtime settings (stored in Mongo settings collection).
    Falls back to constants for safety.
    """
    upi = (await get_setting("upi_id", UPI_ID)).strip() or UPI_ID
    support = (await get_setting("support_link", SUPPORT_LINK)).strip() or SUPPORT_LINK
    owner = (await get_setting("owner_link", OWNER_LINK)).strip() or OWNER_LINK
    start_img = (await get_setting("start_image_url", START_IMAGE_URL)).strip() or START_IMAGE_URL
    return upi, support, owner, start_img

async def register_user(user):
    await col_users.update_one(
        {"_id": user.id},
        {
            "$set": {
                "username":   user.username or "",
                "first_name": user.first_name or "",
                "last_name":  user.last_name or "",
                "full_name":  " ".join(filter(None, [user.first_name, user.last_name])),
                "last_seen":  now_ist(),
            },
            "$setOnInsert": {
                "is_banned":       False,
                "wallet_balance":  0.0,
                "total_purchases": 0,
                "joined_at":       now_ist(),
            }
        },
        upsert=True
    )

async def is_banned(user_id):
    doc = await col_users.find_one({"_id": user_id})
    return doc and doc.get("is_banned", False)

async def is_maintenance():
    return await get_setting("maintenance", "0") == "1"

def is_admin(user_id): return user_id in ADMIN_IDS

def status_emoji(s):
    return {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(s, "❓")

# ─── COLORED INLINE BUTTONS (Telegram Web) ───────────────────────────────────
def ibutton_raw(
    text: str,
    *,
    callback_data=None,
    url=None,
    style=None,  # "primary" | "success" | "danger"
):
    """
    Telegram Bot API supports colored inline buttons in some clients (notably Telegram Web)
    via InlineKeyboardButton(style=...).
    """
    kw = {"text": text}
    if style in ("primary", "success", "danger"):
        kw["style"] = style
    if url is not None:
        kw["url"] = url
        return InlineKeyboardButton(**kw)
    if callback_data is not None:
        kw["callback_data"] = callback_data
        return InlineKeyboardButton(**kw)
    raise ValueError("ibutton_raw requires url or callback_data")

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [cbutton("「🛒 ʙʀᴏᴡsᴇ ᴘʀᴏᴅᴜᴄᴛs」", callback_data="browse_0", style="primary")],
        [cbutton("「💰 ᴍʏ ᴡᴀʟʟᴇᴛ」", callback_data="wallet", style="success"),
         cbutton("「📦 ᴍʏ ᴏʀᴅᴇʀs」", callback_data="my_orders_0")],
        [cbutton("「❓ ʜᴇʟᴘ & ᴄᴏᴍᴍᴀɴᴅs」", callback_data="help")],
    ])

# ─── ADMIN TOOLS (advanced) ───────────────────────────────────────────────────
def admin_tools_kb():
    return InlineKeyboardMarkup([
        [ibutton_raw("📤 "+sc("Export Backup"), callback_data="admin_export", style="success"),
         ibutton_raw("📥 "+sc("Import Backup"), callback_data="admin_import", style="primary")],
        [ibutton_raw("🧹 "+sc("Cleanup Old Pending"), callback_data="admin_cleanup", style="danger")],
        [ibutton_raw("🔙 "+sc("Back"), callback_data="admin_menu", style="primary")],
    ])


def _auto_style(seed: str) -> str:
    """Deterministic color for any button label/callback."""
    s = (seed or "").strip().lower()
    h = 0
    for c in s:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    return ("primary", "success", "danger")[h % 3]


def cbutton(text: str, *, callback_data: str | None = None, url: str | None = None, style: str | None = None):
    """Colored button; if style omitted it auto-picks based on label+target."""
    seed = f"{text}|{callback_data or ''}|{url or ''}"
    st = style if style in ("primary", "success", "danger") else _auto_style(seed)
    return ibutton_raw(text, callback_data=callback_data, url=url, style=st)


def support_cancel_kb():
    """Used after auto-deletion notice."""
    # support should point to OWNER (as requested)
    return InlineKeyboardMarkup([
        [cbutton("💬 "+sc("Support"), url=OWNER_LINK, style="success"),
         cbutton("❌ "+sc("Cancel"), callback_data="main_menu", style="danger")],
    ])

# ─── QR CODE (UPI deep-link) ──────────────────────────────────────────────────
def generate_upi_qr(amount: float, note: str) -> io.BytesIO:
    """
    Generates a UPI QR that:
    • Any UPI app scanner → opens payment screen pre-filled with amount & UPI ID
    • Generic QR scanner  → shows UPI URL; if a UPI app is installed Android/iOS
      will offer to open it automatically
    """
    # Note: UPI ID may be changed from admin settings.
    # We embed the current constant here; pay_upi() shows the runtime value to user.
    upi_url = (
        f"upi://pay?pa={UPI_ID}"
        f"&pn=FileStore"
        f"&am={amount:.2f}"
        f"&cu=INR"
        f"&tn={note}"
    )
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a1a2e", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ─── FORCE-SUB ────────────────────────────────────────────────────────────────
async def check_force_sub(bot, user_id):
    not_joined = []
    async for ch in col_channels.find():
        try:
            member = await bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
            if member.status in (ChatMember.LEFT, ChatMember.BANNED):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined

async def send_force_sub_msg(update, not_joined):
    buttons = []
    for i, ch in enumerate(not_joined, 1):
        label = ch.get("channel_name") or f"Channel {i}"
        buttons.append([ibutton_raw(f"➕ {sc('Join')} {sc(label)}", url=ch["channel_link"], style="primary")])
    buttons.append([ibutton_raw("✅ " + sc("I've Joined — Verify"), callback_data="verify_sub", style="success")])
    text = (
        f"⚠️ {b('Access Restricted')}\n"
        f"{'━'*20}\n"
        f"{b('Join the channels below to use this bot:')}\n\n"
        + "\n".join(f"• {b(ch.get('channel_name') or ch['channel_id'])}" for ch in not_joined)
        + f"\n\n{bi('Tap Verify after joining all channels.')}"
    )
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

# ─── GUARD ────────────────────────────────────────────────────────────────────
async def guard(update, context):
    user = update.effective_user
    if not user: return True
    await register_user(user)
    if await is_banned(user.id):
        txt = f"🚫 {b('You are banned from this bot.')}"
        if update.callback_query: await update.callback_query.answer(sc("You are banned."), show_alert=True)
        else: await update.effective_message.reply_text(txt, parse_mode="HTML")
        return True
    if await is_maintenance() and not is_admin(user.id):
        txt = f"🔧 {b('Bot is under maintenance. Please try again later.')}"
        if update.callback_query: await update.callback_query.answer(sc("Under maintenance."), show_alert=True)
        else: await update.effective_message.reply_text(txt, parse_mode="HTML")
        return True
    if not is_admin(user.id):
        not_joined = await check_force_sub(context.bot, user.id)
        if not_joined:
            await send_force_sub_msg(update, not_joined)
            return True
    return False

# ─── VERIFY SUB ───────────────────────────────────────────────────────────────
async def verify_sub(update, context):
    query = update.callback_query
    await query.answer()
    not_joined = await check_force_sub(context.bot, query.from_user.id)
    if not_joined:
        buttons = [[ibutton_raw(f"➕ {sc('Join')} {sc(ch.get('channel_name','Channel'))}", url=ch["channel_link"], style="primary")] for ch in not_joined]
        buttons.append([ibutton_raw("✅ " + sc("Verify Again"), callback_data="verify_sub", style="success")])
        await query.message.reply_text(
            f"❌ {b('Still not joined all channels!')}\n{bi('Please join and verify again.')}",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        await query.message.delete()
    else:
        name = query.from_user.first_name or "User"
        await query.message.reply_text(
            f"✅ {b('Verified!')} {b('Welcome')} {b(name)}!\n\n{bi('You now have full access to the store.')}",
            parse_mode="HTML", reply_markup=main_menu_kb())
        await query.message.delete()

# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update, context):
    user = update.effective_user
    if not user: return

    # Register + fire log IMMEDIATELY before any checks
    await register_user(user)
    name = user.first_name or user.username or "User"
    asyncio.create_task(_log_start(context.bot, user, name))

    if await is_banned(user.id):
        await update.message.reply_text(f"� {b('You are banned.')}", parse_mode="HTML")
        return
    if await is_maintenance() and not is_admin(user.id):
        await update.message.reply_text(f"� {b('Bot under maintenance.')}", parse_mode="HTML")
        return

    # Check force-sub
    if not is_admin(user.id):
        not_joined = await check_force_sub(context.bot, user.id)
        if not_joined:
            await send_force_sub_msg(update, not_joined)
            return


    upi_id, support_link, owner_link, start_image_url = await get_runtime_links()
    caption = (
        f"👋 {b('Hello')} {b(name)}!\n\n"
        f"🤖 {b('I am Super Fast File Store Bot')}\n"
        f"{bi('Made by')} <a href='{owner_link}'>{b('Krishan')}</a>\n\n"
        f"{'━'*20}\n"
        f"⚡ {b('What I Offer:')}\n"
        f"• {b('Super Fast Payment Verification')}\n"
        f"• {b('Big Files Fast Forward')}\n"
        f"• {b('Instant Auto Delivery')}\n"
        f"• {b('Secure & Private')}\n"
        f"{'━'*20}\n\n"
        f"💼 {b('Want your own bot like this?')}\n"
        f"{b('Fully customizable — contact')} <a href='{owner_link}'>{b('my owner')}</a>"
    )

    if start_image_url:
        try:
            await update.message.reply_photo(
                photo=start_image_url, caption=caption,
                parse_mode="HTML", has_spoiler=True, reply_markup=main_menu_kb())
            return
        except Exception as e:
            logger.error(f"Start photo error: {e}")
    await update.message.reply_text(caption, parse_mode="HTML", reply_markup=main_menu_kb())


async def _log_start(bot, user, name):
    try:
        uname_display = f"@{user.username}" if user.username else "—"
        name_link = f"<a href='tg://user?id={user.id}'>{name}</a>"
        log_text = (
            f"🚀 {b('New User Started Bot')}\n"
            f"{'━'*20}\n"
            f"👤 {b('Name:')} {name_link}\n"
            f"🔖 {b('Username:')} {uname_display}\n"
            f"🆔 {b('User ID:')} <code>{user.id}</code>\n"
            f"📅 {b('Time:')} {fmt_time(now_ist())}\n"
            f"{'━'*20}"
        )
        await bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Log error: {e}")

# ─── BROWSE ───────────────────────────────────────────────────────────────────
async def browse_numbers(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    page = int(query.data.split("_")[1])
    # Category-first browsing
    cats = [c async for c in col_categories.find({"enabled": {"$ne": False}}).sort("name", 1)]
    if cats:
        per_page = 8
        total = len(cats)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        chunk = cats[page * per_page:(page + 1) * per_page]
        buttons = []
        for c in chunk:
            cid = str(c["_id"])
            buttons.append([cbutton(f"📂 {sc(c.get('name','Category'))}", callback_data=f"cat_{cid}")])
        nav = []
        if page > 0:
            nav.append(cbutton("◀️ " + sc("Prev"), callback_data=f"browse_{page-1}", style="primary"))
        nav.append(cbutton(f"{page+1}/{pages}", callback_data="noop", style="primary"))
        if page < pages - 1:
            nav.append(cbutton(sc("Next") + " ▶️", callback_data=f"browse_{page+1}", style="primary"))
        if nav:
            buttons.append(nav)
        buttons.append([cbutton("🔙 " + sc("Main Menu"), callback_data="main_menu", style="primary")])
        await query.message.reply_text(
            f"📂 {b('Categories')}\n{'━'*20}\n{bi('Select a category:')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        await query.message.delete()
        return

    # Fallback: old product list if no categories exist
    products = [p async for p in col_products.find({"enabled": True})]
    if not products:
        await query.message.reply_text(
            f"📦 {b('No products available right now.')}\n{bi('Check back soon!')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[cbutton("「🔙 ᴍᴇɴᴜ」", callback_data="main_menu")]]))
        await query.message.delete()
        return
    per_page = 5; total = len(products)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = products[page*per_page:(page+1)*per_page]
    buttons = []
    for p in chunk:
        pid = str(p["_id"])
        buttons.append([cbutton(f"📦 {sc(p['name'])}  •  ₹{p['price_inr']:.0f}", callback_data=f"product_{pid}")])
    nav = []
    if page > 0: nav.append(cbutton("◀️ " + sc("Prev"), callback_data=f"browse_{page-1}", style="primary"))
    nav.append(cbutton(f"{page+1}/{pages}", callback_data="noop", style="primary"))
    if page < pages-1: nav.append(cbutton(sc("Next") + " ▶️", callback_data=f"browse_{page+1}", style="primary"))
    if nav: buttons.append(nav)
    buttons.append([cbutton("🔙 " + sc("Main Menu"), callback_data="main_menu", style="primary")])
    await query.message.reply_text(
        f"🛒 {b('Available Products')}\n{'━'*20}\n{bi('Select a product to purchase:')}",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def noop_callback(update, context):
    await update.callback_query.answer()

# ─── CATEGORY VIEW ────────────────────────────────────────────────────────────
async def category_view(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context):
        return
    from bson import ObjectId
    cid = query.data.split("_", 1)[1]
    try:
        cat = await col_categories.find_one({"_id": ObjectId(cid)})
    except Exception:
        cat = None
    if not cat or cat.get("enabled") is False:
        await query.message.reply_text(f"❌ {b('Category not found.')}", parse_mode="HTML")
        await query.message.delete()
        return

    # List products in this category
    products = [p async for p in col_products.find({"enabled": True, "category_id": cid}).sort("name", 1)]
    if not products:
        await query.message.reply_text(
            f"📂 {b(cat.get('name','Category'))}\n{'━'*20}\n{bi('No products in this category yet.')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[cbutton("🔙 "+sc("Back"), callback_data="browse_0", style="primary")]]),
        )
        await query.message.delete()
        return

    buttons = []
    for p in products[:30]:
        pid = str(p["_id"])
        buttons.append([cbutton(f"📦 {sc(p['name'])}  •  ₹{p['price_inr']:.0f}", callback_data=f"product_{pid}")])
    buttons.append([cbutton("🔙 "+sc("Back"), callback_data="browse_0", style="primary")])
    await query.message.reply_text(
        f"📂 {b(cat.get('name','Category'))}\n{'━'*20}\n{bi('Select a product:')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.message.delete()

# ─── PRODUCT DETAIL ───────────────────────────────────────────────────────────
async def product_detail(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    from bson import ObjectId
    pid = query.data.split("_")[1]
    try: p = await col_products.find_one({"_id": ObjectId(pid)})
    except: p = None
    if not p:
        await query.message.reply_text(f"❌ {b('Product not found.')}", parse_mode="HTML")
        await query.message.delete()
        return
    user_doc = await col_users.find_one({"_id": query.from_user.id})
    wallet = user_doc.get("wallet_balance", 0) if user_doc else 0
    text = (
        f"📦 {b(p['name'])}\n"
        f"{'━'*20}\n"
        f"💰 {b('Price:')} {b('₹' + str(int(p['price_inr'])) + ' INR')}\n"
        f"{'━'*20}\n"
        f"{bi('Choose your payment method below:')}"
    )
    kb = InlineKeyboardMarkup([
        [cbutton("「💳 ʙᴜʏ ᴡɪᴛʜ ᴜᴘɪ」", callback_data=f"pay_upi_{pid}", style="danger")],
        [cbutton(f"「💰 ʙᴜʏ ꜰʀᴏᴍ ᴡᴀʟʟᴇᴛ (₹{wallet:.2f})」", callback_data=f"wallet_buy_{pid}", style="success")],
        [cbutton("「🔙 ʙᴀᴄᴋ」", callback_data="browse_0", style="primary")],
    ])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

# ─── WALLET BUY ───────────────────────────────────────────────────────────────
async def wallet_buy(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    from bson import ObjectId
    pid = query.data.split("_")[2]
    try: p = await col_products.find_one({"_id": ObjectId(pid)})
    except: p = None
    if not p:
        await query.message.reply_text(f"❌ {b('Product not found.')}", parse_mode="HTML")
        await query.message.delete()
        return
    user_id = query.from_user.id
    user_doc = await col_users.find_one({"_id": user_id})
    wallet = user_doc.get("wallet_balance", 0) if user_doc else 0
    price = p["price_inr"]
    if wallet < price:
        await query.message.reply_text(
            f"❌ {b('Insufficient Balance')}\n{'━'*20}\n"
            f"{b('Required:')} {b('₹' + str(int(price)))}\n"
            f"{b('Your Balance:')} {b('₹' + f'{wallet:.2f}')}\n{'━'*20}\n"
            f"{bi('Please deposit funds to continue.')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("「➕ ᴅᴇᴘᴏsɪᴛ ꜰᴜɴᴅs」", callback_data="wallet")],
                [InlineKeyboardButton("「🔙 ʙᴀᴄᴋ」",          callback_data=f"product_{pid}")],
            ]))
        await query.message.delete()
        return
    now = now_ist()
    order = {"user_id": user_id, "username": query.from_user.username or "",
             "product_id": p["_id"], "product_name": p["name"],
             "amount_inr": price, "payment_method": "wallet",
             "status": "approved", "created_at": now, "reviewed_at": now,
             "file_msg_ids": p.get("file_msg_ids", []),
             "file_msg_id": p.get("file_msg_id"),
             "file_channel_id": p.get("file_channel_id", FILE_CHANNEL_ID)}
    result = await col_orders.insert_one(order)
    order_id = str(result.inserted_id)
    await col_users.update_one({"_id": user_id}, {"$inc": {"wallet_balance": -price, "total_purchases": 1}})
    await deliver_file(context.bot, user_id, p, order_id)
    await query.message.reply_text(
        f"✅ {b('Purchase Successful!')}\n{bi('Your file has been delivered above.')} 📁",
        parse_mode="HTML", reply_markup=main_menu_kb())
    await query.message.delete()

# ─── PAY UPI ──────────────────────────────────────────────────────────────────
async def pay_upi(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    from bson import ObjectId
    pid = query.data.split("_")[2]
    try: p = await col_products.find_one({"_id": ObjectId(pid)})
    except: p = None
    if not p:
        await query.message.reply_text(f"❌ {b('Product not found.')}", parse_mode="HTML")
        await query.message.delete()
        return
    context.user_data["buy_product_id"] = pid
    upi_id, support_link, owner_link, start_image_url = await get_runtime_links()
    qr_buf = generate_upi_qr(p["price_inr"], sc(f"Order {p['name']}"))
    caption = (
        f"{'━'*20}\n"
        f"💳 {b('UPI Payment')}\n"
        f"{'━'*20}\n"
        f"💰 {b('Amount:')} {b('₹' + str(int(p['price_inr'])))}\n"
        f"🏦 {b('UPI ID:')} <code>{upi_id}</code>\n\n"
        f"📱 {b('Scan with any UPI app:')}\n"
        f"• {sc('PhonePe')}  • {sc('Google Pay')}  • {sc('Paytm')}\n"
        f"• {sc('BHIM')}  • {sc('Any UPI App')}\n\n"
        f"⚠️ {b('Pay EXACT amount shown above.')}\n"
        f"{'━'*20}\n"
        f"{bi('After payment, tap the button below to upload screenshot.')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("「📸 ɪ'ᴠᴇ ᴘᴀɪᴅ — ᴜᴘʟᴏᴀᴅ sᴄʀᴇᴇɴsʜᴏᴛ」", callback_data=f"buy_upload_{pid}")],
        [InlineKeyboardButton("🔙 " + sc("Back"),                           callback_data=f"product_{pid}")],
    ])
    await query.message.reply_photo(photo=qr_buf, caption=caption, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

async def buy_upload_prompt(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    pid = query.data.split("_")[2]
    context.user_data["buy_product_id"] = pid
    context.user_data["awaiting_buy_screenshot"] = True
    await query.message.reply_text(
        f"📸 {b('Send Payment Screenshot')}\n{'━'*20}\n"
        f"{b('Please send your payment screenshot as a photo.')}\n"
        f"{bi('Make sure the amount and UPI ID are clearly visible.')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「❌ ᴄᴀɴᴄᴇʟ」", callback_data=f"product_{pid}")]]))
    await query.message.delete()

# ─── DELIVER FILE (no forward, protect content) ───────────────────────────────
async def deliver_file(bot, user_id, product, order_id):
    """Deliver one or multiple files. Supports product['file_msg_ids'] list."""
    file_channel = product.get("file_channel_id", FILE_CHANNEL_ID)
    product_name = product.get("name", "File")

    # Support both single and multiple file IDs
    raw_ids = product.get("file_msg_ids") or []
    if not raw_ids and product.get("file_msg_id"):
        raw_ids = [product["file_msg_id"]]

    upi_id, support_link, owner_link, start_image_url = await get_runtime_links()
    caption = (
        f"🎉 {b('Enjoy! Here Is Your File')}\n"
        f"{'━'*20}\n"
        f"📦 {b(product_name)}\n"
        f"🆔 {b('Order:')} <code>{order_id[:8]}</code>\n"
        f"{'━'*20}\n\n"
        f"⚠️ {b('Caution:')}\n"
        f"{b('This file will be deleted in 1 hour.')}\n"
        f"{b('Download it now!')}\n\n"
        f"{bi('If deleted before download, contact')} "
        f"<a href='{support_link}'>{b('Support')}</a>"
    )

    if not raw_ids:
        # No buttons on delivery-related messages
        await bot.send_message(chat_id=user_id, text=caption, parse_mode="HTML")
        return

    delivered = 0
    delivered_msg_ids: list[int] = []
    for i, fid in enumerate(raw_ids):
        try:
            # First file gets caption, rest get none — no "Forwarded from" + protect_content
            msg = await bot.copy_message(
                chat_id=user_id,
                from_chat_id=file_channel,
                message_id=int(fid),
                caption=caption if i == 0 else None,
                parse_mode="HTML" if i == 0 else None,
                protect_content=True,
            )
            if msg:
                delivered_msg_ids.append(msg.message_id)
            delivered += 1
            if len(raw_ids) > 1:
                await asyncio.sleep(0.4)
        except Exception as e:
            logger.error(f"File delivery error fid={fid}: {e}")

    if delivered > 0:
        # No buttons on delivery-related messages
        await bot.send_message(
            chat_id=user_id,
            text=(f"✅ {b(str(delivered)+' File(s) Delivered Successfully!')}\n"
                  f"{bi('Download before they expire!')} ⏳"),
            parse_mode="HTML",
        )
        # Auto-delete after 1 hour, then notify user
        asyncio.create_task(_delete_delivered_after(bot, user_id, delivered_msg_ids))
        # Log delivery to admin group (with file copy)
        asyncio.create_task(_log_delivery_to_admin(bot, user_id, product, order_id, raw_ids, file_channel))
    else:
        await bot.send_message(
            chat_id=user_id,
            text=(f"✅ {b('Order Approved!')}\n\n"
                  f"⚠️ {b('File delivery failed.')}\n"
                  f"{b('Contact')} <a href='{support_link}'>{b('Support')}</a>"),
            parse_mode="HTML")


async def _log_delivery_to_admin(bot, user_id: int, product: dict, order_id: str, file_msg_ids: list[int], file_channel: int):
    """Send delivery proof to ADMIN_GROUP_ID: info + copied file(s)."""
    try:
        user = await bot.get_chat(user_id)
        uname = f"@{user.username}" if getattr(user, "username", None) else "—"
        name = " ".join([x for x in [getattr(user, "first_name", ""), getattr(user, "last_name", "")] if x]).strip() or "User"
    except Exception:
        uname = "—"
        name = "User"

    # Try to fetch order amount + payment method if available
    amount = None
    paym = None
    try:
        from bson import ObjectId
        o = await col_orders.find_one({"_id": ObjectId(order_id)})
        if o:
            amount = o.get("amount_inr")
            paym = o.get("payment_method")
    except Exception:
        pass

    product_name = product.get("name") or product.get("product_name") or "Product"
    amount_txt = f"₹{int(amount)}" if isinstance(amount, (int, float)) else "—"
    paym_txt = (str(paym).upper() if paym else "—")
    info = (
        f"📦 {b('DELIVERED')}\n"
        f"{'━'*20}\n"
        f"👤 {b('User:')} {b(name)}\n"
        f"🔖 {b('Username:')} {sc(uname)}\n"
        f"🆔 {b('User ID:')} <code>{user_id}</code>\n"
        f"{'━'*20}\n"
        f"🛒 {b('Item:')} {b(product_name)}\n"
        f"💰 {b('Price:')} {b(amount_txt)} | {b(paym_txt)}\n"
        f"🧾 {b('Order:')} <code>{order_id[:8]}</code>\n"
        f"📁 {b('Files:')} {b(str(len(file_msg_ids)))}\n"
        f"📅 {b('Time:')} {fmt_time(now_ist())}\n"
        f"{'━'*20}"
    )

    # Send info first
    try:
        await bot.send_message(chat_id=ADMIN_GROUP_ID, text=info, parse_mode="HTML")
    except Exception:
        pass

    # Copy delivered file(s) to admin group as proof
    for i, fid in enumerate(file_msg_ids[:10]):  # cap spam
        try:
            await bot.copy_message(
                chat_id=ADMIN_GROUP_ID,
                from_chat_id=file_channel,
                message_id=int(fid),
                caption=(f"{b('Delivered file proof')} • <code>{order_id[:8]}</code>" if i == 0 else None),
                parse_mode="HTML" if i == 0 else None,
                protect_content=True,
            )
            await asyncio.sleep(0.25)
        except Exception:
            pass


async def _delete_delivered_after(bot, user_id: int, msg_ids: list[int], seconds: int = 3600):
    """Delete delivered file messages after `seconds` and notify user."""
    if not msg_ids:
        return
    try:
        await asyncio.sleep(seconds)
    except Exception:
        return

    deleted = 0
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=mid)
            deleted += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    # After 1 hour, send the required message + Support/Cancel
    upi_id, support_link, owner_link, start_image_url = await get_runtime_links()
    if deleted > 0:
        text = (
            f"🗑️ {b('Your file has been deleted successfully.')}\n"
            f"{'━'*20}\n"
            f"{bi('If you have not downloaded it yet, please contact support here.')}"
        )
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [cbutton("💬 "+sc("Support"), url=owner_link, style="success"),
                 cbutton("❌ "+sc("Cancel"), callback_data="main_menu", style="danger")],
            ]),
        )

# ─── SCREENSHOT HANDLER ───────────────────────────────────────────────────────
async def screenshot_handler(update, context):
    if await guard(update, context): return
    user = update.effective_user

    # Buy screenshot
    if context.user_data.get("awaiting_buy_screenshot"):
        context.user_data.pop("awaiting_buy_screenshot")
        pid     = context.user_data.get("buy_product_id")
        file_id = update.message.photo[-1].file_id if update.message.photo else None
        if not file_id:
            await update.message.reply_text(f"❌ {b('Please send a photo.')}", parse_mode="HTML")
            return
        from bson import ObjectId
        try: p = await col_products.find_one({"_id": ObjectId(pid)})
        except: p = None
        if not p:
            await update.message.reply_text(f"❌ {b('Session expired. Please start again.')}", parse_mode="HTML", reply_markup=main_menu_kb())
            return
        order = {"user_id": user.id, "username": user.username or "",
                 "product_id": p["_id"], "product_name": p["name"],
                 "amount_inr": p["price_inr"], "payment_method": "upi",
                 "screenshot_file_id": file_id, "status": "pending",
                 "created_at": now_ist(),
                 "file_msg_ids": p.get("file_msg_ids", []),
                 "file_msg_id": p.get("file_msg_id"),
                 "file_channel_id": p.get("file_channel_id", FILE_CHANNEL_ID)}
        result   = await col_orders.insert_one(order)
        order_id = str(result.inserted_id)
        uname    = f"@{user.username}" if user.username else f"ID: {user.id}"
        admin_text = (
            f"🆕 {b('New Order')}\n"
            f"{'━'*20}\n"
            f"👤 {b('User:')} {sc(uname)} | <code>{user.id}</code>\n"
            f"📦 {b('Product:')} {b(p['name'])}\n"
            f"💰 {b('Amount:')} {b('₹' + str(int(p['price_inr'])) + ' INR')} | {b('UPI')}\n"
            f"🆔 {b('Order:')} <code>{order_id[:8]}</code>\n"
            f"📅 {b('Time:')} {fmt_time(now_ist())}\n"
            f"{'━'*20}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("「✅ ᴀᴘᴘʀᴏᴠᴇ」", callback_data=f"approve_order_{order_id}"),
            InlineKeyboardButton("「❌ ʀᴇᴊᴇᴄᴛ」",  callback_data=f"reject_order_{order_id}"),
        ]])
        try:
            await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=file_id,
                caption=admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Admin group error: {e}")
        await update.message.reply_text(
            f"⏳ {b('Payment Submitted!')}\n{'━'*20}\n"
            f"{b('Your payment is under review.')}\n"
            f"{bi('You will be notified once approved.')}",
            parse_mode="HTML", reply_markup=main_menu_kb())
        return

    # Deposit screenshot
    if context.user_data.get("awaiting_deposit_screenshot"):
        context.user_data.pop("awaiting_deposit_screenshot")
        dep_inr = context.user_data.get("dep_inr", 0)
        file_id = update.message.photo[-1].file_id if update.message.photo else None
        if not file_id:
            await update.message.reply_text(f"❌ {b('Please send a photo.')}", parse_mode="HTML")
            return
        dep    = {"user_id": user.id, "amount_inr": dep_inr,
                  "screenshot_file_id": file_id, "status": "pending", "created_at": now_ist()}
        result = await col_deposits.insert_one(dep)
        dep_id = str(result.inserted_id)
        uname  = f"@{user.username}" if user.username else f"ID: {user.id}"
        admin_text = (
            f"💳 {b('Deposit Request')}\n"
            f"{'━'*20}\n"
            f"👤 {b('User:')} {sc(uname)} | <code>{user.id}</code>\n"
            f"💵 {b('Amount:')} {b('₹' + str(int(dep_inr)) + ' INR')} | {b('UPI')}\n"
            f"🆔 {b('Dep ID:')} <code>{dep_id[:8]}</code>\n"
            f"📅 {b('Time:')} {fmt_time(now_ist())}\n"
            f"{'━'*20}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("「✅ ᴀᴘᴘʀᴏᴠᴇ」", callback_data=f"approve_deposit_{dep_id}"),
            InlineKeyboardButton("「❌ ʀᴇᴊᴇᴄᴛ」",  callback_data=f"reject_deposit_{dep_id}"),
        ]])
        try:
            await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=file_id,
                caption=admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Admin group error: {e}")
        await update.message.reply_text(
            f"⏳ {b('Deposit Submitted!')}\n{'━'*20}\n"
            f"{b('Your deposit is under review.')}\n"
            f"{bi('Funds will be credited once approved.')}",
            parse_mode="HTML", reply_markup=main_menu_kb())
        return

    # Admin import backup (expects a document with JSON)
    if context.user_data.get("awaiting_import_backup") and user and is_admin(user.id):
        # We accept as a document or plain text JSON (fallback)
        context.user_data.pop("awaiting_import_backup", None)
        try:
            if update.message.document:
                f = await update.message.document.get_file()
                raw = (await f.download_as_bytearray()).decode("utf-8", errors="ignore")
            else:
                raw = update.message.text or ""
            payload = json.loads(raw)
            # Upsert settings
            for s in payload.get("settings") or []:
                key = str(s.get("key") or "").strip()
                val = str(s.get("value") or "")
                if key:
                    await set_setting(key, val)
            # Upsert channels
            for ch in payload.get("force_channels") or []:
                ch_id = str(ch.get("channel_id") or "").strip()
                if not ch_id:
                    continue
                await col_channels.update_one(
                    {"channel_id": ch_id},
                    {"$set": {"channel_id": ch_id, "channel_link": ch.get("channel_link", ""), "channel_name": ch.get("channel_name", ch_id)}},
                    upsert=True,
                )
            # Upsert products by name (simple + safe)
            for p in payload.get("products") or []:
                name = str(p.get("name") or "").strip()
                if not name:
                    continue
                doc = {
                    "name": name,
                    "price_inr": float(p.get("price_inr") or 0),
                    "file_msg_ids": [int(x) for x in (p.get("file_msg_ids") or []) if str(x).isdigit()],
                    "file_msg_id": int(p.get("file_msg_id")) if str(p.get("file_msg_id") or "").isdigit() else None,
                    "file_channel_id": int(p.get("file_channel_id")) if str(p.get("file_channel_id") or "").lstrip("-").isdigit() else FILE_CHANNEL_ID,
                    "enabled": bool(p.get("enabled", True)),
                    "created_at": now_ist(),
                }
                await col_products.update_one({"name": name}, {"$set": doc}, upsert=True)

            await update.message.reply_text(f"✅ {b('Backup imported successfully!')}", parse_mode="HTML", reply_markup=admin_tools_kb())
        except Exception as e:
            logger.error(f"Import backup failed: {e}")
            await update.message.reply_text(f"❌ {b('Import failed. Invalid JSON or file.')}", parse_mode="HTML", reply_markup=admin_tools_kb())
        return

# ─── APPROVE / REJECT ORDER ───────────────────────────────────────────────────
async def approve_order(update, context):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer(sc("Not authorized."), show_alert=True); return
    await query.answer()
    from bson import ObjectId
    order_id = query.data.split("_")[2]
    try: order = await col_orders.find_one({"_id": ObjectId(order_id)})
    except: order = None
    if not order or order["status"] != "pending":
        await query.answer(sc("Already processed."), show_alert=True); return
    now = now_ist()
    await col_orders.update_one({"_id": order["_id"]},
        {"$set": {"status": "approved", "reviewed_by": query.from_user.id, "reviewed_at": now}})
    await col_users.update_one({"_id": order["user_id"]}, {"$inc": {"total_purchases": 1}})
    p = {"name": order["product_name"],
         "file_msg_ids": order.get("file_msg_ids", []),
         "file_msg_id": order.get("file_msg_id"),
         "file_channel_id": order.get("file_channel_id", FILE_CHANNEL_ID)}
    await deliver_file(context.bot, order["user_id"], p, order_id)
    # Log to log channel
    try:
        uname = f"@{order.get('username','')}" if order.get('username') else f"ID:{order['user_id']}"
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=(f"✅ {b('Sale Confirmed')}\n{'━'*20}\n"
                  f"📦 {b(order['product_name'])} | {b('₹' + str(int(order['amount_inr'])))}\n"
                  f"👤 {sc(uname)}\n{'━'*20}"),
            parse_mode="HTML")
    except Exception: pass
    try:
        new_cap = (query.message.caption or "") + f"\n\n✅ {sc('Approved by')} @{query.from_user.username or query.from_user.id}"
        if query.message.photo: await query.message.edit_caption(caption=new_cap, parse_mode="HTML")
        else: await query.message.edit_text(text=new_cap, parse_mode="HTML")
    except Exception: pass

async def reject_order(update, context):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer(sc("Not authorized."), show_alert=True); return
    await query.answer()
    from bson import ObjectId
    order_id = query.data.split("_")[2]
    try: order = await col_orders.find_one({"_id": ObjectId(order_id)})
    except: order = None
    if not order or order["status"] != "pending":
        await query.answer(sc("Already processed."), show_alert=True); return
    await col_orders.update_one({"_id": order["_id"]},
        {"$set": {"status": "rejected", "reviewed_by": query.from_user.id, "reviewed_at": now_ist()}})
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(f"❌ {b('Order Rejected')}\n{'━'*20}\n"
                  f"{b('Your payment could not be verified.')}\n"
                  f"{b('Contact')} <a href='{SUPPORT_LINK}'>{b('Support')}</a> {b('if you need help.')}"),
            parse_mode="HTML", reply_markup=main_menu_kb())
    except Exception: pass
    try:
        new_cap = (query.message.caption or "") + f"\n\n❌ {sc('Rejected by')} @{query.from_user.username or query.from_user.id}"
        if query.message.photo: await query.message.edit_caption(caption=new_cap, parse_mode="HTML")
        else: await query.message.edit_text(text=new_cap, parse_mode="HTML")
    except Exception: pass

# ─── APPROVE / REJECT DEPOSIT ─────────────────────────────────────────────────
async def approve_deposit(update, context):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer(sc("Not authorized."), show_alert=True); return
    await query.answer()
    from bson import ObjectId
    dep_id = query.data.split("_")[2]
    try: dep = await col_deposits.find_one({"_id": ObjectId(dep_id)})
    except: dep = None
    if not dep or dep["status"] != "pending":
        await query.answer(sc("Already processed."), show_alert=True); return
    await col_deposits.update_one({"_id": dep["_id"]},
        {"$set": {"status": "approved", "reviewed_by": query.from_user.id, "reviewed_at": now_ist()}})
    await col_users.update_one({"_id": dep["user_id"]}, {"$inc": {"wallet_balance": dep["amount_inr"]}})
    try:
        await context.bot.send_message(
            chat_id=dep["user_id"],
            text=(f"✅ {b('Deposit Approved!')}\n{'━'*20}\n"
                  f"💵 {b('₹' + str(int(dep['amount_inr'])) + ' INR')} {b('credited to your wallet!')}\n"
                  f"{bi('Happy shopping!')} 🛒"),
            parse_mode="HTML", reply_markup=main_menu_kb())
    except Exception: pass
    try:
        new_cap = (query.message.caption or "") + f"\n\n✅ {sc('Approved by')} @{query.from_user.username or query.from_user.id}"
        if query.message.photo: await query.message.edit_caption(caption=new_cap, parse_mode="HTML")
        else: await query.message.edit_text(text=new_cap, parse_mode="HTML")
    except Exception: pass

async def reject_deposit(update, context):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer(sc("Not authorized."), show_alert=True); return
    await query.answer()
    from bson import ObjectId
    dep_id = query.data.split("_")[2]
    try: dep = await col_deposits.find_one({"_id": ObjectId(dep_id)})
    except: dep = None
    if not dep or dep["status"] != "pending":
        await query.answer(sc("Already processed."), show_alert=True); return
    await col_deposits.update_one({"_id": dep["_id"]},
        {"$set": {"status": "rejected", "reviewed_by": query.from_user.id, "reviewed_at": now_ist()}})
    try:
        await context.bot.send_message(
            chat_id=dep["user_id"],
            text=(f"❌ {b('Deposit Rejected')}\n{'━'*20}\n"
                  f"{b('Contact')} <a href='{SUPPORT_LINK}'>{b('Support')}</a> {b('if you need help.')}"),
            parse_mode="HTML")
    except Exception: pass
    try:
        new_cap = (query.message.caption or "") + f"\n\n❌ {sc('Rejected by')} @{query.from_user.username or query.from_user.id}"
        if query.message.photo: await query.message.edit_caption(caption=new_cap, parse_mode="HTML")
        else: await query.message.edit_text(text=new_cap, parse_mode="HTML")
    except Exception: pass

# ─── WALLET ───────────────────────────────────────────────────────────────────
async def wallet(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    user_doc = await col_users.find_one({"_id": query.from_user.id})
    bal = user_doc.get("wallet_balance", 0) if user_doc else 0
    text = (
        f"💰 {b('My Wallet')}\n{'━'*20}\n"
        f"💵 {b('Balance:')} {b('₹' + f'{bal:.2f}' + ' INR')}\n"
        f"{'━'*20}\n{bi('Deposit funds to buy products.')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("「➕ ᴅᴇᴘᴏsɪᴛ ᴠɪᴀ ᴜᴘɪ」", callback_data="deposit_upi")],
        [InlineKeyboardButton("「📋 ᴅᴇᴘᴏsɪᴛ ʜɪsᴛᴏʀʏ」", callback_data="dep_hist_0")],
        [InlineKeyboardButton("「🔙 ᴍᴀɪɴ ᴍᴇɴᴜ」",       callback_data="main_menu")],
    ])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

async def deposit_upi_cb(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    context.user_data["awaiting_dep_amount"] = True
    await query.message.reply_text(
        f"💳 {b('UPI Deposit')}\n{'━'*20}\n"
        f"{b('Enter the amount you want to deposit in INR:')}\n"
        f"{bi('Minimum: ₹20')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「❌ ᴄᴀɴᴄᴇʟ」", callback_data="wallet")]]))
    await query.message.delete()

# ─── MY ORDERS ────────────────────────────────────────────────────────────────
async def my_orders(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    page = int(query.data.split("_")[2])
    user_id = query.from_user.id
    orders = [o async for o in col_orders.find({"user_id": user_id}).sort("created_at", -1)]
    if not orders:
        await query.message.reply_text(
            f"📦 {b('No Orders Yet')}\n{bi('Browse products and make your first purchase!')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🛒 ʙʀᴏᴡsᴇ」", callback_data="browse_0"),
                                                InlineKeyboardButton("「🔙 ᴍᴇɴᴜ」",   callback_data="main_menu")]]))
        await query.message.delete()
        return
    per_page = 5; total = len(orders)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = orders[page*per_page:(page+1)*per_page]
    buttons = []
    for o in chunk:
        oid = str(o["_id"])
        buttons.append([InlineKeyboardButton(
            f"#{oid[:6]} | {sc(o['product_name'])} | ₹{o['amount_inr']:.0f} | {status_emoji(o['status'])}",
            callback_data=f"order_detail_{oid}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"my_orders_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages-1: nav.append(InlineKeyboardButton("▶️", callback_data=f"my_orders_{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("「🔙 ᴍᴇɴᴜ」", callback_data="main_menu")])
    await query.message.reply_text(
        f"�� {b('My Orders')}\n{'━'*20}",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def order_detail(update, context):
    query = update.callback_query
    await query.answer()
    from bson import ObjectId
    oid = query.data.split("_")[2]
    try: o = await col_orders.find_one({"_id": ObjectId(oid), "user_id": query.from_user.id})
    except: o = None
    if not o:
        await query.message.reply_text(f"❌ {b('Order not found.')}", parse_mode="HTML")
        await query.message.delete()
        return
    text = (
        f"📦 {b('Order #' + oid[:8])}\n{'━'*20}\n"
        f"📦 {b(o['product_name'])}\n"
        f"💰 {b('₹' + str(int(o['amount_inr'])) + ' INR')} | {b(o['payment_method'].upper())}\n"
        f"📊 {status_emoji(o['status'])} {b(o['status'].title())}\n"
        f"�� {fmt_time(o['created_at'])}\n{'━'*20}"
    )
    buttons = [[InlineKeyboardButton("「🔙 ᴍʏ ᴏʀᴅᴇʀs」", callback_data="my_orders_0")]]
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def dep_hist(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    page = int(query.data.split("_")[2])
    user_id = query.from_user.id
    deps = [d async for d in col_deposits.find({"user_id": user_id}).sort("created_at", -1)]
    if not deps:
        await query.message.reply_text(
            f"📋 {b('No Deposits Yet')}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🔙 ᴡᴀʟʟᴇᴛ」", callback_data="wallet")]]))
        await query.message.delete()
        return
    per_page = 5; total = len(deps)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = deps[page*per_page:(page+1)*per_page]
    lines = [f"📋 {b('Deposit History')}\n{'━'*20}"]
    for d in chunk:
        lines.append(f"#{str(d['_id'])[:6]} | {b('₹' + str(int(d['amount_inr'])))} | {status_emoji(d['status'])} | {fmt_time(d['created_at'])[:11]}")
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"dep_hist_{page-1}"))
    if page < pages-1: nav.append(InlineKeyboardButton("▶️", callback_data=f"dep_hist_{page+1}"))
    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton("「🔙 ᴡᴀʟʟᴇᴛ」", callback_data="wallet")])
    await query.message.reply_text("\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def help_cb(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        f"❓ {b('How To Buy')}\n{'━'*20}\n"
        f"1️⃣ {b('Browse products')}\n"
        f"2️⃣ {b('Choose payment method')}\n"
        f"3️⃣ {b('Pay via UPI or Wallet')}\n"
        f"4️⃣ {b('Upload payment screenshot')}\n"
        f"5️⃣ {b('Admin verifies & file is delivered')} ✅\n"
        f"{'━'*20}\n"
        f"💬 {b('Need help? Contact')} <a href='{SUPPORT_LINK}'>{b('Support')}</a>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("「💬 ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ」", url=SUPPORT_LINK)],
        [InlineKeyboardButton("「🔙 ᴍᴀɪɴ ᴍᴇɴᴜ」",       callback_data="main_menu")],
    ])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

async def main_menu_cb(update, context):
    query = update.callback_query
    await query.answer()
    if await guard(update, context): return
    welcome = await get_setting("welcome_message",
        f"👋 {b('Welcome to the Store!')}\n\n{bi('Browse our products and buy instantly.')} 🛍️")
    await query.message.reply_text(welcome, parse_mode="HTML", reply_markup=main_menu_kb())
    await query.message.delete()

# ─── TEXT HANDLER ─────────────────────────────────────────────────────────────
async def text_handler(update, context):
    if await guard(update, context): return
    user = update.effective_user

    if context.user_data.get("awaiting_dep_amount"):
        try: amount = float(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(f"❌ {b('Invalid number. Please try again:')}", parse_mode="HTML")
            return
        if amount < 20:
            await update.message.reply_text(f"❌ {b('Minimum deposit is ₹20. Enter again:')}", parse_mode="HTML")
            return
        context.user_data.pop("awaiting_dep_amount")
        context.user_data["dep_inr"] = amount
        context.user_data["awaiting_deposit_screenshot"] = True
        qr_buf = generate_upi_qr(amount, "Deposit")
        await update.message.reply_photo(
            photo=qr_buf,
            caption=(
                f"{'━'*20}\n💳 {b('UPI Deposit')}\n{'━'*20}\n"
                f"💰 {b('Amount:')} {b('₹' + str(int(amount)))}\n"
                f"🏦 {b('UPI ID:')} <code>{UPI_ID}</code>\n\n"
                f"📱 {b('Scan with any UPI app')}\n"
                f"⚠️ {b('Pay EXACT amount')}\n{'━'*20}\n"
                f"{bi('After payment, send your screenshot below.')}"
            ),
            parse_mode="HTML")
        await update.message.reply_text(
            f"📸 {b('Send your payment screenshot now:')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「❌ ᴄᴀɴᴄᴇʟ」", callback_data="wallet")]]))
        return

    if context.user_data.get("awaiting_product_name"):
        context.user_data.pop("awaiting_product_name")
        context.user_data["new_product_name"] = update.message.text.strip()
        context.user_data["awaiting_product_price"] = True
        await update.message.reply_text(
            f"✅ {b('Name:')} {b(context.user_data['new_product_name'])}\n\n💰 {b('Enter price in INR:')}",
            parse_mode="HTML")
        return

    if context.user_data.get("awaiting_product_price"):
        try: price = float(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(f"❌ {b('Invalid price. Enter a number e.g. 299')}", parse_mode="HTML")
            return
        context.user_data.pop("awaiting_product_price")
        context.user_data["new_product_price"] = price
        context.user_data["awaiting_product_file_msg_id"] = True
        await update.message.reply_text(
            f"✅ {b('Price:')} {b('₹' + str(int(price)))}\n\n"
            f"📁 {b('Enter File Message ID(s) from your file channel:')}\n\n"
            f"{b('Single file:')} <code>12345</code>\n"
            f"{b('Multiple files:')} <code>12345 67890 11111</code>\n\n"
            f"{bi('Forward each file to @userinfobot to get its message ID.')}\n"
            f"{bi('Send 0 to skip.')}",
            parse_mode="HTML")
        return

    if context.user_data.get("awaiting_product_file_msg_id"):
        context.user_data.pop("awaiting_product_file_msg_id")
        raw = update.message.text.strip()
        # Support multiple IDs: "123 456 789" or "123,456,789"
        parts = [x.strip() for x in raw.replace(",", " ").split() if x.strip().isdigit()]
        file_ids = [int(x) for x in parts] if parts else []
        name  = context.user_data.pop("new_product_name", "Product")
        price = context.user_data.pop("new_product_price", 0)
        # Optional: category assignment
        cat_id = context.user_data.pop("new_product_category_id", "")
        product = {"name": name, "price_inr": price,
                   "file_msg_ids": file_ids,
                   "file_msg_id": file_ids[0] if file_ids else None,
                   "file_channel_id": FILE_CHANNEL_ID,
                   "enabled": True, "created_at": now_ist()}
        if cat_id:
            product["category_id"] = cat_id
        result = await col_products.insert_one(product)
        ids_display = " | ".join(str(x) for x in file_ids) if file_ids else sc("Not set")
        await update.message.reply_text(
            f"✅ {b('Product Added!')}\n{'━'*20}\n"
            f"📦 {b(name)}\n💰 {b('₹' + str(int(price)))}\n"
            f"📁 {b(str(len(file_ids)) + ' File(s):')} {sc(ids_display)}\n"
            f"🆔 <code>{str(result.inserted_id)[:8]}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🔙 ᴀᴅᴍɪɴ」", callback_data="admin_menu")]]))
        return

    if context.user_data.get("awaiting_new_category_name") and user and is_admin(user.id):
        context.user_data.pop("awaiting_new_category_name", None)
        name = (update.message.text or "").strip()
        if not name:
            await update.message.reply_text(f"❌ {b('Name required.')}", parse_mode="HTML")
            return
        await col_categories.update_one({"name": name}, {"$set": {"name": name, "enabled": True, "created_at": now_ist()}}, upsert=True)
        await update.message.reply_text(f"✅ {b('Category added!')}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[cbutton("🔙 "+sc("Back"), callback_data="admin_cats", style="primary")]]))
        return

    if context.user_data.get("awaiting_rename_category_id") and user and is_admin(user.id):
        cid = context.user_data.pop("awaiting_rename_category_id", "")
        name = (update.message.text or "").strip()
        if not name:
            await update.message.reply_text(f"❌ {b('Name required.')}", parse_mode="HTML")
            return
        try:
            from bson import ObjectId
            await col_categories.update_one({"_id": ObjectId(cid)}, {"$set": {"name": name}})
        except Exception:
            pass
        await update.message.reply_text(f"✅ {b('Renamed!')}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[cbutton("🔙 "+sc("Back"), callback_data="admin_cats", style="primary")]]))
        return

    if context.user_data.get("admin_edit_balance_uid"):
        uid = context.user_data.pop("admin_edit_balance_uid")
        try: delta = float(update.message.text.strip())
        except ValueError:
            await update.message.reply_text(f"❌ {b('Invalid amount.')}", parse_mode="HTML")
            return
        await col_users.update_one({"_id": uid}, {"$inc": {"wallet_balance": delta}})
        user_doc = await col_users.find_one({"_id": uid})
        bal = user_doc.get("wallet_balance", 0) if user_doc else 0
        sign = "+" if delta >= 0 else ""
        await update.message.reply_text(
            f"✅ {b('Balance Updated')}\n{b(sign + str(delta) + ' INR')}\n{b('New Balance:')} {b('₹' + f'{bal:.2f}')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🔙 ᴀᴅᴍɪɴ」", callback_data="admin_menu")]]))
        return

    if context.user_data.get("awaiting_search_user"):
        context.user_data.pop("awaiting_search_user")
        val = update.message.text.strip().lstrip("@")
        row = await col_users.find_one({"_id": int(val)}) if val.isdigit() else await col_users.find_one({"username": val})
        if not row:
            await update.message.reply_text(f"❌ {b('User not found.')}", parse_mode="HTML")
            return
        await _show_user_profile(update, context, row, via_message=True)
        return

    if context.user_data.get("awaiting_welcome_msg"):
        context.user_data.pop("awaiting_welcome_msg")
        await set_setting("welcome_message", update.message.text)
        await update.message.reply_text(
            f"✅ {b('Welcome message updated!')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🔙 ᴀᴅᴍɪɴ」", callback_data="admin_menu")]]))
        return

    if context.user_data.get("awaiting_setting_key") and user and is_admin(user.id):
        key = context.user_data.pop("awaiting_setting_key", "")
        val = (update.message.text or "").strip()
        if key == "start_image_url" and val.upper() == "OFF":
            val = ""
        if key == "auto_broadcast_interval_min":
            if not val.isdigit():
                await update.message.reply_text(f"❌ {b('Send a number in minutes (example: 60).')}", parse_mode="HTML")
                context.user_data["awaiting_setting_key"] = key
                return
            minutes = max(10, int(val))
            val = str(minutes)
        if key in ("support_link", "owner_link") and val and not (val.startswith("http://") or val.startswith("https://")):
            await update.message.reply_text(f"❌ {b('Please send a valid link starting with https://')}", parse_mode="HTML")
            context.user_data["awaiting_setting_key"] = key
            return
        await set_setting(key, val)
        await update.message.reply_text(
            f"✅ {b('Updated!')}\n{'━'*20}\n<b>{escape(key)}</b> = <code>{escape(val) if val else 'OFF'}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Settings"), callback_data="admin_settings")]]),
        )
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data.pop("awaiting_broadcast")
        users = [u async for u in col_users.find({"is_banned": {"$ne": True}})]
        context.user_data["broadcast_msg_id"]  = update.message.message_id
        context.user_data["broadcast_chat_id"] = update.message.chat_id
        kb = InlineKeyboardMarkup([[
            ibutton_raw(f"✅ " + sc(f"Send to {len(users)} users"), callback_data="broadcast_confirm", style="success"),
            ibutton_raw("「❌ ᴄᴀɴᴄᴇʟ」", callback_data="admin_menu", style="danger"),
        ]])
        await update.message.reply_text(
            f"📢 {b(f'Send to {len(users)} users?')}", parse_mode="HTML", reply_markup=kb)
        return

    if context.user_data.get("awaiting_channel_id"):
        context.user_data.pop("awaiting_channel_id")
        parts = update.message.text.strip().split()
        if len(parts) < 2:
            await update.message.reply_text(f"❌ {b('Format: channel_id invite_link Name')}", parse_mode="HTML")
            return
        ch_id, ch_link = parts[0], parts[1]
        ch_name = " ".join(parts[2:]) if len(parts) > 2 else ch_id
        await col_channels.update_one({"channel_id": ch_id},
            {"$set": {"channel_id": ch_id, "channel_link": ch_link, "channel_name": ch_name}}, upsert=True)
        await update.message.reply_text(
            f"✅ {b('Channel added:')} {b(ch_name)}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("「🔙 ᴀᴅᴍɪɴ」", callback_data="admin_menu")]]))
        return
# ADMIN PANEL
async def admin_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(f'❌ {b(chr(78)+chr(111)+chr(116)+chr(32)+chr(97)+chr(117)+chr(116)+chr(104)+chr(111)+chr(114)+chr(105)+chr(122)+chr(101)+chr(100)+chr(46))}', parse_mode='HTML')
        return
    await update.message.reply_text(f'🔧 {b(chr(65)+chr(100)+chr(109)+chr(105)+chr(110)+chr(32)+chr(80)+chr(97)+chr(110)+chr(101)+chr(108))}', parse_mode='HTML', reply_markup=admin_main_kb())

def admin_main_kb():
    return InlineKeyboardMarkup([
        [ibutton_raw('📦 '+sc('Products'),  callback_data='admin_products', style='primary'),
         ibutton_raw('💰 '+sc('Orders'),    callback_data='admin_orders_all_0', style='primary'),
         ibutton_raw('💳 '+sc('Deposits'),  callback_data='admin_deps_all_0', style='success')],
        [ibutton_raw('👥 '+sc('Users'),     callback_data='admin_users', style='primary'),
         ibutton_raw('📊 '+sc('Stats'),     callback_data='admin_stats', style='primary'),
         ibutton_raw('📢 '+sc('Broadcast'), callback_data='admin_broadcast', style='success')],
        [ibutton_raw('🧰 '+sc('Tools'),     callback_data='admin_tools', style='success')],
        [ibutton_raw('📂 '+sc('Categories'), callback_data='admin_cats', style='primary')],
        [ibutton_raw('📡 '+sc('Channels'),  callback_data='admin_channels', style='primary'),
         ibutton_raw('⚙️ '+sc('Settings'),  callback_data='admin_settings', style='primary'),
         ibutton_raw('❌ '+sc('Close'),      callback_data='admin_close', style='danger')],
    ])

async def admin_menu_cb(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    await query.message.reply_text(f'🔧 {b(chr(65)+chr(100)+chr(109)+chr(105)+chr(110)+chr(32)+chr(80)+chr(97)+chr(110)+chr(101)+chr(108))}', parse_mode='HTML', reply_markup=admin_main_kb())
    await query.message.delete()

async def admin_close(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.delete()

async def admin_products(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    products = [p async for p in col_products.find().sort('name', 1)]
    buttons = [[InlineKeyboardButton('➕ '+sc('Add Product'), callback_data='add_product')]]
    for p in products:
        pid = str(p['_id'])
        icon = '✅' if p.get('enabled') else '❌'
        buttons.append([InlineKeyboardButton(f'{icon} {sc(p[chr(110)+chr(97)+chr(109)+chr(101)])} | ₹{p[chr(112)+chr(114)+chr(105)+chr(99)+chr(101)+chr(95)+chr(105)+chr(110)+chr(114)]:.0f}', callback_data=f'admin_product_{pid}')])
    buttons.append([InlineKeyboardButton('🔙 '+sc('Back'), callback_data='admin_menu')])
    await query.message.reply_text(f'📦 {b(chr(80)+chr(114)+chr(111)+chr(100)+chr(117)+chr(99)+chr(116)+chr(115))}', parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def add_product(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data['awaiting_product_name'] = True
    # Allow category selection during add flow (optional)
    cats = [c async for c in col_categories.find({"enabled": {"$ne": False}}).sort("name", 1)]
    if cats:
        context.user_data["awaiting_product_category"] = True
        # We'll ask for category after name, unless admin chooses now
    await query.message.reply_text(f'📦 {b(chr(69)+chr(110)+chr(116)+chr(101)+chr(114)+chr(32)+chr(112)+chr(114)+chr(111)+chr(100)+chr(117)+chr(99)+chr(116)+chr(32)+chr(110)+chr(97)+chr(109)+chr(101)+chr(58))}', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ '+sc('Cancel'), callback_data='admin_products')]]))
    await query.message.delete()



async def admin_product_detail(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    from bson import ObjectId
    pid = query.data.split("_")[2]
    try: p = await col_products.find_one({"_id": ObjectId(pid)})
    except: p = None
    if not p:
        await query.message.reply_text(f"Product not found."); await query.message.delete(); return
    fids = p.get("file_msg_ids") or ([p["file_msg_id"]] if p.get("file_msg_id") else [])
    text = (f"📦 {b(p['name'])}\n{'━'*20}\n"
            f"💰 {b('₹'+str(int(p['price_inr']))+' INR')}\n"
            f"📁 {b(str(len(fids))+' File(s)')} {sc(str(fids) if fids else 'Not set')}\n"
            f"{'✅' if p.get('enabled') else '❌'} {b('Enabled' if p.get('enabled') else 'Disabled')}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔛 "+sc("Toggle"), callback_data=f"toggle_product_{pid}"),
         InlineKeyboardButton("🗑️ "+sc("Delete"), callback_data=f"del_product_{pid}")],
        [InlineKeyboardButton("🔙 "+sc("Back"),   callback_data="admin_products")],
    ])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

async def toggle_product(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    from bson import ObjectId
    pid = query.data.split("_")[2]
    try: p = await col_products.find_one({"_id": ObjectId(pid)})
    except: p = None
    if not p: return
    new_val = not p.get("enabled", True)
    await col_products.update_one({"_id": p["_id"]}, {"$set": {"enabled": new_val}})
    await query.answer(sc("Enabled") if new_val else sc("Disabled"), show_alert=True)
    query.data = f"admin_product_{pid}"
    await admin_product_detail(update, context)

async def del_product(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    from bson import ObjectId
    pid = query.data.split("_")[2]
    try: await col_products.delete_one({"_id": ObjectId(pid)})
    except: pass
    await query.answer(sc("Deleted!"), show_alert=True)
    query.data = "admin_products"
    await admin_products(update, context)

async def admin_orders(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    parts = query.data.split("_"); sf = parts[2]; page = int(parts[3])
    filt = {} if sf == "all" else {"status": sf}
    orders = [o async for o in col_orders.find(filt).sort("created_at", -1)]
    filter_btns = [
        InlineKeyboardButton("⏳ "+sc("Pending"),  callback_data="admin_orders_pending_0"),
        InlineKeyboardButton("✅ "+sc("Approved"), callback_data="admin_orders_approved_0"),
        InlineKeyboardButton("❌ "+sc("Rejected"), callback_data="admin_orders_rejected_0"),
    ]
    per_page = 5; total = len(orders)
    pages = max(1, (total+per_page-1)//per_page); page = max(0, min(page, pages-1))
    chunk = orders[page*per_page:(page+1)*per_page]
    buttons = [filter_btns]
    for o in chunk:
        oid = str(o["_id"])
        buttons.append([InlineKeyboardButton(f"#{oid[:6]} {sc(o['product_name'])} ₹{o['amount_inr']:.0f} {status_emoji(o['status'])}", callback_data=f"admin_order_view_{oid}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_orders_{sf}_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages-1: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_orders_{sf}_{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 "+sc("Admin"), callback_data="admin_menu")])
    await query.message.reply_text(f"�� {b('Orders ('+sf.title()+')')}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def admin_order_view(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    from bson import ObjectId
    oid = query.data.split("_")[3]
    try: o = await col_orders.find_one({"_id": ObjectId(oid)})
    except: o = None
    if not o: await query.message.reply_text("Not found."); await query.message.delete(); return
    text = (f"📦 {b('Order #'+oid[:8])}\n{'━'*20}\n"
            f"�� {b('@'+o.get('username','N/A'))} | <code>{o['user_id']}</code>\n"
            f"📦 {b(o['product_name'])}\n💰 {b('₹'+str(int(o['amount_inr']))+' | '+o['payment_method'].upper())}\n"
            f"📊 {status_emoji(o['status'])} {b(o['status'].title())}\n📅 {fmt_time(o['created_at'])}")
    buttons = []
    if o["status"] == "pending":
        buttons.append([InlineKeyboardButton("「✅ ᴀᴘᴘʀᴏᴠᴇ」", callback_data=f"approve_order_{oid}"),
                        InlineKeyboardButton("「❌ ʀᴇᴊᴇᴄᴛ」",  callback_data=f"reject_order_{oid}")])
    buttons.append([InlineKeyboardButton("🔙 "+sc("Orders"), callback_data="admin_orders_all_0")])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def admin_deps(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    parts = query.data.split("_"); sf = parts[2]; page = int(parts[3])
    filt = {} if sf == "all" else {"status": sf}
    deps = [d async for d in col_deposits.find(filt).sort("created_at", -1)]
    filter_btns = [
        InlineKeyboardButton("⏳ "+sc("Pending"),  callback_data="admin_deps_pending_0"),
        InlineKeyboardButton("✅ "+sc("Approved"), callback_data="admin_deps_approved_0"),
        InlineKeyboardButton("❌ "+sc("Rejected"), callback_data="admin_deps_rejected_0"),
    ]
    per_page = 5; total = len(deps)
    pages = max(1, (total+per_page-1)//per_page); page = max(0, min(page, pages-1))
    chunk = deps[page*per_page:(page+1)*per_page]
    buttons = [filter_btns]
    for d in chunk:
        did = str(d["_id"])
        buttons.append([InlineKeyboardButton(f"#{did[:6]} uid:{d['user_id']} ₹{d['amount_inr']:.0f} {status_emoji(d['status'])}", callback_data=f"admin_dep_view_{did}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_deps_{sf}_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page < pages-1: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_deps_{sf}_{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 "+sc("Admin"), callback_data="admin_menu")])
    await query.message.reply_text(f"💳 {b('Deposits ('+sf.title()+')')}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def admin_dep_view(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    from bson import ObjectId
    did = query.data.split("_")[3]
    try: d = await col_deposits.find_one({"_id": ObjectId(did)})
    except: d = None
    if not d: await query.message.reply_text("Not found."); await query.message.delete(); return
    text = (f"💳 {b('Deposit #'+did[:8])}\n{'━'*20}\n"
            f"👤 {b('User:')} <code>{d['user_id']}</code>\n"
            f"💵 {b('₹'+str(int(d['amount_inr']))+' INR | UPI')}\n"
            f"📊 {status_emoji(d['status'])} {b(d['status'].title())}\n📅 {fmt_time(d['created_at'])}")
    buttons = []
    if d["status"] == "pending":
        buttons.append([InlineKeyboardButton("「✅ ᴀᴘᴘʀᴏᴠᴇ」", callback_data=f"approve_deposit_{did}"),
                        InlineKeyboardButton("「❌ ʀᴇᴊᴇᴄᴛ」",  callback_data=f"reject_deposit_{did}")])
    buttons.append([InlineKeyboardButton("🔙 "+sc("Deposits"), callback_data="admin_deps_all_0")])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()

async def admin_users(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 "+sc("Search User"),  callback_data="admin_search_user"),
         InlineKeyboardButton("💰 "+sc("Edit Wallet"),  callback_data="admin_edit_wallet")],
        [InlineKeyboardButton("🔙 "+sc("Back"),         callback_data="admin_menu")],
    ])
    await query.message.reply_text(f"👥 {b('Users Manager')}", parse_mode="HTML", reply_markup=kb)
    await query.message.delete()

async def admin_search_user(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data["awaiting_search_user"] = True
    await query.message.reply_text(f"🔍 {b('Enter user ID or @username:')}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Cancel"), callback_data="admin_users")]]))
    await query.message.delete()

async def admin_edit_wallet(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data["awaiting_search_user"] = True
    context.user_data["wallet_action"] = True
    await query.message.reply_text(f"💰 {b('Enter user ID or @username to edit wallet:')}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Cancel"), callback_data="admin_users")]]))
    await query.message.delete()

async def _show_user_profile(update, context, row, via_message=False):
    uid = row["_id"]
    uname_display = f"@{row.get('username','')}" if row.get('username') else "—"
    name_link = f"<a href='tg://user?id={uid}'>{row.get('full_name') or row.get('first_name','?')}</a>"
    text = (
        f"👤 {b('Name:')} {name_link}\n"
        f"🔖 {b('Username:')} {uname_display}\n"
        f"🆔 {b('User ID:')} <code>{uid}</code>\n"
        f"💰 {b('Wallet:')} {b('₹' + str(round(row.get('wallet_balance', 0), 2)))}\n"
        f"🛒 {b('Purchases:')} {b(str(row.get('total_purchases',0)))}\n"
        f"🚫 {b('Banned:')} {b('Yes' if row.get('is_banned') else 'No')}\n"
        f"📅 {b('Joined:')} {fmt_time(row.get('joined_at'))}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 "+sc("Ban"),          callback_data=f"ban_uid_{uid}"),
         InlineKeyboardButton("✅ "+sc("Unban"),         callback_data=f"unban_uid_{uid}")],
        [InlineKeyboardButton("💰 "+sc("Edit Balance"), callback_data=f"editbal_uid_{uid}")],
        [InlineKeyboardButton("🔙 "+sc("Back"),         callback_data="admin_users")],
    ])
    if via_message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        await update.callback_query.message.delete()

async def ban_uid(update, context):
    query = update.callback_query; await query.answer()
    uid = int(query.data.split("_")[2])
    await col_users.update_one({"_id": uid}, {"$set": {"is_banned": True}})
    await query.answer(sc("Banned!"), show_alert=True)

async def unban_uid(update, context):
    query = update.callback_query; await query.answer()
    uid = int(query.data.split("_")[2])
    await col_users.update_one({"_id": uid}, {"$set": {"is_banned": False}})
    await query.answer(sc("Unbanned!"), show_alert=True)

async def editbal_uid(update, context):
    query = update.callback_query; await query.answer()
    uid = int(query.data.split("_")[2])
    context.user_data["admin_edit_balance_uid"] = uid
    await query.message.reply_text(f"💰 {b('Enter amount to add/deduct (e.g. 500 or -200):')}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Cancel"), callback_data="admin_users")]]))
    await query.message.delete()

async def admin_stats(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    total_users    = await col_users.count_documents({})
    total_products = await col_products.count_documents({})
    total_orders   = await col_orders.count_documents({})
    pending_orders = await col_orders.count_documents({"status": "pending"})
    pending_deps   = await col_deposits.count_documents({"status": "pending"})
    revenue = 0
    async for doc in col_orders.aggregate([{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$amount_inr"}}}]):
        revenue = doc.get("total", 0)
    text = (f"📊 {b('Bot Statistics')}\n{'━'*20}\n"
            f"👥 {b('Users:')} {b(str(total_users))}\n"
            f"📦 {b('Products:')} {b(str(total_products))}\n"
            f"🛒 {b('Total Orders:')} {b(str(total_orders))}\n"
            f"⏳ {b('Pending Orders:')} {b(str(pending_orders))}\n"
            f"💳 {b('Pending Deposits:')} {b(str(pending_deps))}\n"
            f"💵 {b('Revenue:')} {b('₹'+str(int(revenue)))}\n{'━'*20}")
    await query.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Admin"), callback_data="admin_menu")]]))
    await query.message.delete()

async def admin_channels(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    channels = [ch async for ch in col_channels.find()]
    lines = [f"📡 {b('Force Subscribe Channels')}\n{'━'*20}"]
    buttons = []
    for ch in channels:
        lines.append(f"• {b(ch.get('channel_name','?'))} | {sc(ch['channel_id'])}")
        buttons.append([cbutton(f"🗑️ "+sc("Remove "+ch.get('channel_name','?')), callback_data=f"del_channel_{ch['channel_id']}", style="danger")])
    if not channels: lines.append(bi("No channels added yet."))
    buttons.append([cbutton("➕ "+sc("Add Channel"), callback_data="add_channel", style="success")])
    buttons.append([cbutton("🔙 "+sc("Back"),        callback_data="admin_menu", style="primary")])
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()


async def admin_cats(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    cats = [c async for c in col_categories.find({}).sort("name", 1)]
    lines = [f"📂 {b('Categories')}\n{'━'*20}"]
    buttons = []
    for c in cats:
        cid = str(c["_id"])
        name = c.get("name", "Category")
        enabled = c.get("enabled", True) is not False
        lines.append(f"• {'✅' if enabled else '❌'} {b(name)}")
        buttons.append([
            cbutton(("✅ " if enabled else "❌ ") + sc(name), callback_data=f"admin_cat_{cid}"),
        ])
    if not cats:
        lines.append(bi("No categories yet. Add one now."))
    buttons.append([cbutton("➕ "+sc("Add Category"), callback_data="admin_cat_add", style="success")])
    buttons.append([cbutton("🔙 "+sc("Back"), callback_data="admin_menu", style="primary")])
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    await query.message.delete()


async def admin_cat_detail(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    from bson import ObjectId
    cid = query.data.split("_", 2)[2]
    try:
        c = await col_categories.find_one({"_id": ObjectId(cid)})
    except Exception:
        c = None
    if not c:
        await query.answer(sc("Not found."), show_alert=True)
        return
    enabled = c.get("enabled", True) is not False
    name = c.get("name", "Category")
    text = (
        f"📂 {b('Category')}\n{'━'*20}\n"
        f"🏷️ {b('Name:')} {b(name)}\n"
        f"🔘 {b('Status:')} {b('Enabled' if enabled else 'Disabled')}\n"
        f"{'━'*20}"
    )
    kb = InlineKeyboardMarkup([
        [cbutton("🔛 "+sc("Toggle"), callback_data=f"admin_cat_toggle_{cid}", style="primary"),
         cbutton("✏️ "+sc("Rename"), callback_data=f"admin_cat_rename_{cid}", style="success")],
        [cbutton("🗑️ "+sc("Delete"), callback_data=f"admin_cat_del_{cid}", style="danger")],
        [cbutton("🔙 "+sc("Back"), callback_data="admin_cats", style="primary")],
    ])
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await query.message.delete()


async def admin_cat_add(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    context.user_data["awaiting_new_category_name"] = True
    await query.message.reply_text(
        f"➕ {b('Add Category')}\n{'━'*20}\n{bi('Send category name:')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[cbutton("❌ "+sc("Cancel"), callback_data="admin_cats", style="danger")]]),
    )
    await query.message.delete()


async def admin_cat_toggle(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    from bson import ObjectId
    cid = query.data.split("_", 3)[3]
    try:
        c = await col_categories.find_one({"_id": ObjectId(cid)})
    except Exception:
        c = None
    if not c:
        return
    new_val = not (c.get("enabled", True) is not False)
    await col_categories.update_one({"_id": c["_id"]}, {"$set": {"enabled": new_val}})
    await query.answer(sc("Enabled") if new_val else sc("Disabled"), show_alert=True)
    query.data = f"admin_cat_{cid}"
    await admin_cat_detail(update, context)


async def admin_cat_del(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    from bson import ObjectId
    cid = query.data.split("_", 3)[3]
    try:
        await col_categories.delete_one({"_id": ObjectId(cid)})
        # Also unlink products from this category
        await col_products.update_many({"category_id": cid}, {"$unset": {"category_id": ""}})
    except Exception:
        pass
    await query.answer(sc("Deleted!"), show_alert=True)
    query.data = "admin_cats"
    await admin_cats(update, context)


async def admin_cat_rename(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    cid = query.data.split("_", 3)[3]
    context.user_data["awaiting_rename_category_id"] = cid
    await query.message.reply_text(
        f"✏️ {b('Rename Category')}\n{'━'*20}\n{bi('Send new category name:')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[cbutton("❌ "+sc("Cancel"), callback_data="admin_cats", style="danger")]]),
    )
    await query.message.delete()

async def add_channel(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data["awaiting_channel_id"] = True
    await query.message.reply_text(
        f"📡 {b('Add Force Subscribe Channel')}\n{'━'*20}\n"
        f"{b('Send in this format:')}\n<code>channel_id invite_link Channel Name</code>\n\n"
        f"{bi('Example:')}\n<code>-1001234567890 https://t.me/mychannel My Channel</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[cbutton("❌ "+sc("Cancel"), callback_data="admin_channels", style="danger")]]))
    await query.message.delete()

async def del_channel_cb(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    ch_id = query.data.split("del_channel_")[1]
    await col_channels.delete_one({"channel_id": ch_id})
    await query.answer(sc("Removed!"), show_alert=True)
    query.data = "admin_channels"
    await admin_channels(update, context)

async def admin_settings(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    maint = await get_setting("maintenance", "0") == "1"
    ab_on = await get_setting("auto_broadcast_enabled", "0") == "1"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔧 "+sc("Maintenance: ON→OFF" if maint else "Maintenance: OFF→ON"), callback_data="toggle_maintenance")],
        [InlineKeyboardButton("📣 "+sc("Auto Broadcast: ON→OFF" if ab_on else "Auto Broadcast: OFF→ON"), callback_data="toggle_auto_broadcast")],
        [InlineKeyboardButton("🕒 "+sc("Set Auto Interval"), callback_data="set_auto_broadcast_interval")],
        [InlineKeyboardButton("📝 "+sc("Set Auto Message"), callback_data="set_auto_broadcast_message")],
        [InlineKeyboardButton("🏦 "+sc("UPI ID"),          callback_data="set_upi_id")],
        [InlineKeyboardButton("💬 "+sc("Support Link"),    callback_data="set_support_link")],
        [InlineKeyboardButton("👑 "+sc("Owner Link"),      callback_data="set_owner_link")],
        [InlineKeyboardButton("🖼️ "+sc("Start Image"),     callback_data="set_start_image_url")],
        [InlineKeyboardButton("📝 "+sc("Welcome Message"), callback_data="edit_welcome_msg")],
        [InlineKeyboardButton("🔙 "+sc("Back"),            callback_data="admin_menu")],
    ])
    await query.message.reply_text(f"⚙️ {b('Settings')}", parse_mode="HTML", reply_markup=kb)
    await query.message.delete()


async def _ask_setting_text(update, context, key: str, title_html: str, hint_html: str):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    context.user_data["awaiting_setting_key"] = key
    await query.message.reply_text(
        f"{title_html}\n{'━'*20}\n{hint_html}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ "+sc("Cancel"), callback_data="admin_settings")]]),
    )
    await query.message.delete()


async def set_upi_id_cb(update, context):
    await _ask_setting_text(
        update, context,
        "upi_id",
        f"🏦 {b('Set UPI ID')}",
        bi("Send the new UPI ID. Example: name@bank"),
    )


async def set_support_link_cb(update, context):
    await _ask_setting_text(
        update, context,
        "support_link",
        f"💬 {b('Set Support Link')}",
        bi("Send Telegram support link. Example: https://t.me/youruser"),
    )


async def set_owner_link_cb(update, context):
    await _ask_setting_text(
        update, context,
        "owner_link",
        f"👑 {b('Set Owner Link')}",
        bi("Send Telegram owner link. Example: https://t.me/youruser"),
    )


async def set_start_image_url_cb(update, context):
    await _ask_setting_text(
        update, context,
        "start_image_url",
        f"🖼️ {b('Set Start Image URL')}",
        bi("Send direct image URL (https://...) or type OFF to disable start image."),
    )

async def toggle_maintenance(update, context):
    query = update.callback_query; await query.answer()
    new = "0" if await get_setting("maintenance","0") == "1" else "1"
    await set_setting("maintenance", new)
    await query.answer(sc("Maintenance ON" if new=="1" else "Maintenance OFF"), show_alert=True)
    await admin_settings(update, context)


async def toggle_auto_broadcast(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    new = "0" if await get_setting("auto_broadcast_enabled", "0") == "1" else "1"
    await set_setting("auto_broadcast_enabled", new)
    await query.answer(sc("Auto broadcast ON" if new == "1" else "Auto broadcast OFF"), show_alert=True)
    await admin_settings(update, context)


async def set_auto_broadcast_interval_cb(update, context):
    await _ask_setting_text(
        update, context,
        "auto_broadcast_interval_min",
        f"🕒 {b('Set Auto Broadcast Interval')}",
        bi("Send minutes. Example: 60 (for every 1 hour). Minimum 10."),
    )


async def set_auto_broadcast_message_cb(update, context):
    await _ask_setting_text(
        update, context,
        "auto_broadcast_message",
        f"📝 {b('Set Auto Broadcast Message')}",
        bi("Send the message text that bot will broadcast automatically."),
    )

async def edit_welcome_msg(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data["awaiting_welcome_msg"] = True
    await query.message.reply_text(f"📝 {b('Send new welcome message:')}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 "+sc("Cancel"), callback_data="admin_settings")]]))
    await query.message.delete()

async def admin_broadcast(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    context.user_data["awaiting_broadcast"] = True
    await query.message.reply_text(f"📢 {b('Send the message to broadcast:')}", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ "+sc("Cancel"), callback_data="admin_menu")]]))
    await query.message.delete()

async def broadcast_confirm(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): return
    msg_id  = context.user_data.pop("broadcast_msg_id", None)
    chat_id = context.user_data.pop("broadcast_chat_id", None)
    if not msg_id: return
    users = [u async for u in col_users.find({"is_banned": {"$ne": True}})]
    success = 0
    for u in users:
        try:
            await context.bot.copy_message(chat_id=u["_id"], from_chat_id=chat_id, message_id=msg_id)
            success += 1
        except Exception: pass
    await query.message.reply_text(f"✅ {b(f'Broadcast sent to {success}/{len(users)} users.')}", parse_mode="HTML")
    await query.message.delete()


async def _auto_broadcast_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job: broadcast configured message to all users."""
    try:
        enabled = await get_setting("auto_broadcast_enabled", "0") == "1"
        if not enabled:
            return
        msg = await get_setting("auto_broadcast_message", "")
        if not msg.strip():
            return
        users_cursor = col_users.find({"is_banned": {"$ne": True}}, {"_id": 1})
        sent = 0
        async for u in users_cursor:
            try:
                await context.bot.send_message(chat_id=u["_id"], text=apply_custom_emoji_html(msg), parse_mode="HTML")
                sent += 1
                if sent % 25 == 0:
                    await asyncio.sleep(0.8)
            except Exception:
                continue
        # Optional: log summary
        try:
            await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"📣 {b('Auto broadcast sent:')} {b(str(sent))}", parse_mode="HTML")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"auto broadcast job error: {e}")


async def admin_tools(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    await query.message.reply_text(f"🧰 {b('Admin Tools')}", parse_mode="HTML", reply_markup=admin_tools_kb())
    await query.message.delete()


def _safe_jsonable(doc: dict) -> dict:
    out = {}
    for k, v in (doc or {}).items():
        if k == "_id":
            out[k] = str(v)
        elif isinstance(v, (datetime,)):
            out[k] = v.isoformat()
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) if hasattr(x, "binary") or str(type(x)).endswith("ObjectId'>") else x for x in v]
        else:
            out[k] = v
    return out


async def admin_export(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    # Keep export small + useful: settings, channels, products
    settings = [s async for s in col_settings.find({})]
    channels = [c async for c in col_channels.find({})]
    products = [p async for p in col_products.find({})]

    payload = {
        "exported_at": now_ist().isoformat(),
        "settings": [_safe_jsonable(s) for s in settings],
        "force_channels": [_safe_jsonable(c) for c in channels],
        "products": [_safe_jsonable(p) for p in products],
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(raw)
    buf.name = f"store_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    buf.seek(0)
    await query.message.reply_document(document=buf, caption=sc("Backup exported."), reply_markup=admin_tools_kb())
    await query.message.delete()


async def admin_import(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    context.user_data["awaiting_import_backup"] = True
    await query.message.reply_text(
        f"📥 {b('Import Backup')}\n{'━'*20}\n"
        f"{bi('Send the exported .json file now.')}\n\n"
        f"⚠️ {b('Note:')} {b('This will upsert settings/channels/products by keys.')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ "+sc("Cancel"), callback_data="admin_tools")]]),
    )
    await query.message.delete()


async def admin_cleanup(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    # Cleanup: mark very old pending orders/deposits as rejected (30 days)
    cutoff = now_ist() - timedelta(days=30)
    o = await col_orders.update_many({"status": "pending", "created_at": {"$lt": cutoff}}, {"$set": {"status": "rejected", "reviewed_at": now_ist(), "reviewed_by": query.from_user.id}})
    d = await col_deposits.update_many({"status": "pending", "created_at": {"$lt": cutoff}}, {"$set": {"status": "rejected", "reviewed_at": now_ist(), "reviewed_by": query.from_user.id}})
    await query.answer(sc("Cleanup done."), show_alert=True)
    await query.message.reply_text(
        f"🧹 {b('Cleanup Complete')}\n{'━'*20}\n"
        f"📦 {b('Orders updated:')} {b(str(o.modified_count))}\n"
        f"💳 {b('Deposits updated:')} {b(str(d.modified_count))}",
        parse_mode="HTML",
        reply_markup=admin_tools_kb(),
    )
    await query.message.delete()

async def addchannel_cmd(update, context):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(f"Usage: /addchannel channel_id invite_link Name"); return
    ch_id, ch_link = args[0], args[1]
    ch_name = " ".join(args[2:]) if len(args) > 2 else ch_id
    await col_channels.update_one({"channel_id": ch_id},
        {"$set": {"channel_id": ch_id, "channel_link": ch_link, "channel_name": ch_name}}, upsert=True)
    await update.message.reply_text(f"✅ {b('Channel added:')} {b(ch_name)}", parse_mode="HTML")

async def removechannel_cmd(update, context):
    if not is_admin(update.effective_user.id): return
    if not context.args: return
    await col_channels.delete_one({"channel_id": context.args[0]})
    await update.message.reply_text(f"✅ {b('Channel removed.')}", parse_mode="HTML")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("admin",         admin_cmd))
    app.add_handler(CommandHandler("addchannel",    addchannel_cmd))
    app.add_handler(CommandHandler("removechannel", removechannel_cmd))
    app.add_handler(CallbackQueryHandler(verify_sub,            pattern="^verify_sub$"))
    app.add_handler(CallbackQueryHandler(main_menu_cb,          pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(browse_numbers,        pattern=r"^browse_\d+$"))
    app.add_handler(CallbackQueryHandler(noop_callback,         pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(product_detail,        pattern=r"^product_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(category_view,         pattern=r"^cat_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(wallet_buy,            pattern=r"^wallet_buy_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(pay_upi,               pattern=r"^pay_upi_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(buy_upload_prompt,     pattern=r"^buy_upload_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(wallet,                pattern="^wallet$"))
    app.add_handler(CallbackQueryHandler(deposit_upi_cb,        pattern="^deposit_upi$"))
    app.add_handler(CallbackQueryHandler(my_orders,             pattern=r"^my_orders_\d+$"))
    app.add_handler(CallbackQueryHandler(order_detail,          pattern=r"^order_detail_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(dep_hist,              pattern=r"^dep_hist_\d+$"))
    app.add_handler(CallbackQueryHandler(help_cb,               pattern="^help$"))
    app.add_handler(CallbackQueryHandler(approve_order,         pattern=r"^approve_order_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(reject_order,          pattern=r"^reject_order_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(approve_deposit,       pattern=r"^approve_deposit_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(reject_deposit,        pattern=r"^reject_deposit_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_menu_cb,         pattern="^admin_menu$"))
    app.add_handler(CallbackQueryHandler(admin_close,           pattern="^admin_close$"))
    app.add_handler(CallbackQueryHandler(admin_products,        pattern="^admin_products$"))
    app.add_handler(CallbackQueryHandler(add_product,           pattern="^add_product$"))
    app.add_handler(CallbackQueryHandler(admin_product_detail,  pattern=r"^admin_product_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(toggle_product,        pattern=r"^toggle_product_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(del_product,           pattern=r"^del_product_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_orders,          pattern=r"^admin_orders_[a-z]+_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_order_view,      pattern=r"^admin_order_view_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_deps,            pattern=r"^admin_deps_[a-z]+_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_dep_view,        pattern=r"^admin_dep_view_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_users,           pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_search_user,     pattern="^admin_search_user$"))
    app.add_handler(CallbackQueryHandler(admin_edit_wallet,     pattern="^admin_edit_wallet$"))
    app.add_handler(CallbackQueryHandler(ban_uid,               pattern=r"^ban_uid_\d+$"))
    app.add_handler(CallbackQueryHandler(unban_uid,             pattern=r"^unban_uid_\d+$"))
    app.add_handler(CallbackQueryHandler(editbal_uid,           pattern=r"^editbal_uid_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_stats,           pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_channels,        pattern="^admin_channels$"))
    app.add_handler(CallbackQueryHandler(add_channel,           pattern="^add_channel$"))
    app.add_handler(CallbackQueryHandler(admin_cats,            pattern="^admin_cats$"))
    app.add_handler(CallbackQueryHandler(admin_cat_add,         pattern="^admin_cat_add$"))
    app.add_handler(CallbackQueryHandler(admin_cat_detail,      pattern=r"^admin_cat_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_cat_toggle,      pattern=r"^admin_cat_toggle_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_cat_del,         pattern=r"^admin_cat_del_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_cat_rename,      pattern=r"^admin_cat_rename_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(del_channel_cb,        pattern=r"^del_channel_"))
    app.add_handler(CallbackQueryHandler(admin_settings,        pattern="^admin_settings$"))
    app.add_handler(CallbackQueryHandler(toggle_maintenance,    pattern="^toggle_maintenance$"))
    app.add_handler(CallbackQueryHandler(toggle_auto_broadcast, pattern="^toggle_auto_broadcast$"))
    app.add_handler(CallbackQueryHandler(set_auto_broadcast_interval_cb, pattern="^set_auto_broadcast_interval$"))
    app.add_handler(CallbackQueryHandler(set_auto_broadcast_message_cb,  pattern="^set_auto_broadcast_message$"))
    app.add_handler(CallbackQueryHandler(set_upi_id_cb,         pattern="^set_upi_id$"))
    app.add_handler(CallbackQueryHandler(set_support_link_cb,   pattern="^set_support_link$"))
    app.add_handler(CallbackQueryHandler(set_owner_link_cb,     pattern="^set_owner_link$"))
    app.add_handler(CallbackQueryHandler(set_start_image_url_cb,pattern="^set_start_image_url$"))
    app.add_handler(CallbackQueryHandler(edit_welcome_msg,      pattern="^edit_welcome_msg$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast,       pattern="^admin_broadcast$"))
    app.add_handler(CallbackQueryHandler(broadcast_confirm,     pattern="^broadcast_confirm$"))
    app.add_handler(CallbackQueryHandler(admin_tools,           pattern="^admin_tools$"))
    app.add_handler(CallbackQueryHandler(admin_export,          pattern="^admin_export$"))
    app.add_handler(CallbackQueryHandler(admin_import,          pattern="^admin_import$"))
    app.add_handler(CallbackQueryHandler(admin_cleanup,         pattern="^admin_cleanup$"))
    app.add_handler(MessageHandler(filters.PHOTO,                   screenshot_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    # Auto broadcast scheduler (reads settings each run)
    try:
        app.job_queue.run_repeating(_auto_broadcast_job, interval=60, first=60)
    except Exception as e:
        logger.error(f"job queue init failed: {e}")
    logger.info("✅ Store Bot Started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
