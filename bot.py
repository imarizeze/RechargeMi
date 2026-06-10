import logging
import os
import re
import sys
import sqlite3
import uuid

import httpx
import openai
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Configuration
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Bank account details for manual transfers
BANK_NAME = os.environ.get("BANK_NAME", "Opay Bank")
BANK_ACCOUNT_NAME = os.environ.get("BANK_ACCOUNT_NAME", "Arinze Henry Eya")
BANK_ACCOUNT_NUMBER = os.environ.get("BANK_ACCOUNT_NUMBER", "8122930212")

# Admin configuration
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
ADMIN_NOTIFICATION_IDS = [int(x) for x in os.environ.get("ADMIN_NOTIFICATION_IDS", "").split(",") if x.strip().isdigit()]

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OpenAI is not None else None

# Peyflex API Configuration
PEYFLEX_API_ENDPOINT = os.environ.get("PEYFLEX_API_ENDPOINT", "")
PEYFLEX_API_KEY = os.environ.get("PEYFLEX_API_KEY", "")
PEYFLEX_API_TOKEN = os.environ.get("PEYFLEX_API_TOKEN", "")
PEYFLEX_AUTH_METHOD = os.environ.get("PEYFLEX_AUTH_METHOD", "api_key")
PEYFLEX_ACCOUNT_ID = os.environ.get("PEYFLEX_ACCOUNT_ID", "")
PEYFLEX_AIRTIME_PATH = os.environ.get("PEYFLEX_AIRTIME_PATH", "/airtime")
PEYFLEX_DATA_PATH = os.environ.get("PEYFLEX_DATA_PATH", "/data")

# Paystack Payment Gateway Configuration
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_API_URL = "https://api.paystack.co"
PAYSTACK_WEBHOOK_SECRET = os.environ.get("PAYSTACK_WEBHOOK_SECRET", "")

class PaystackPaymentClient:
    def __init__(self):
        self.secret_key = PAYSTACK_SECRET_KEY
        self.public_key = PAYSTACK_PUBLIC_KEY
        self.api_url = PAYSTACK_API_URL

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_payment(self, email: str, amount: int, metadata: dict) -> dict:
        """Initialize a payment transaction with Paystack (amount in kobo)"""
        if not self.secret_key:
            return {"success": False, "message": "Paystack secret key not configured.", "authorization_url": None}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.api_url}/transaction/initialize",
                    json={
                        "email": email,
                        "amount": amount,  # Amount in kobo (amount * 100)
                        "metadata": metadata,
                    },
                    headers=self._get_headers(),
                )
            data = response.json()
            if data.get("status") and data.get("data", {}).get("authorization_url"):
                return {
                    "success": True,
                    "message": "Payment link generated successfully",
                    "authorization_url": data["data"]["authorization_url"],
                    "access_code": data["data"]["access_code"],
                    "reference": data["data"]["reference"],
                }
            return {"success": False, "message": data.get("message", "Failed to initialize payment"), "authorization_url": None}
        except Exception as e:
            logger.exception("Paystack initialization failed: %s", e)
            return {"success": False, "message": str(e), "authorization_url": None}

    async def verify_payment(self, reference: str) -> dict:
        """Verify a Paystack payment by reference"""
        if not self.secret_key:
            return {"success": False, "message": "Paystack secret key not configured.", "data": None}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.api_url}/transaction/verify/{reference}",
                    headers=self._get_headers(),
                )
            data = response.json()
            if data.get("status") and data["data"]["status"] == "success":
                return {
                    "success": True,
                    "message": "Payment verified successfully",
                    "data": data["data"],
                }
            return {"success": False, "message": "Payment verification failed", "data": data.get("data")}
        except Exception as e:
            logger.exception("Paystack verification failed: %s", e)
            return {"success": False, "message": str(e), "data": None}

paystack_client = PaystackPaymentClient()


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS

async def notify_admins(bot: Bot, message: str) -> None:
    targets = ADMIN_NOTIFICATION_IDS or ADMIN_IDS
    for admin_id in targets:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
        except Exception as exc:
            logger.warning("Failed sending admin notification to %s: %s", admin_id, exc)

class PeyflexAPIClient:
    def __init__(self):
        self.base_url = PEYFLEX_API_ENDPOINT
        self.api_key = PEYFLEX_API_KEY
        self.api_token = PEYFLEX_API_TOKEN
        self.auth_method = PEYFLEX_AUTH_METHOD
        self.account_id = PEYFLEX_ACCOUNT_ID
        self.airtime_path = PEYFLEX_AIRTIME_PATH
        self.data_path = PEYFLEX_DATA_PATH

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth_method == "api_key" and self.api_key:
            headers["x-api-key"] = self.api_key
        elif self.auth_method in {"bearer", "token"} and self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def _post(self, path: str, payload: dict) -> dict:
        if not self.base_url:
            return {"success": False, "message": "Peyflex API endpoint not configured.", "transaction_id": None}
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=self._get_headers())
            response_data = response.json()
            if response.status_code == 200 and response_data.get("success", False):
                return {"success": True, "message": response_data.get("message", "Success"), "transaction_id": response_data.get("transaction_id") or response_data.get("id")}
            return {"success": False, "message": response_data.get("message", f"Peyflex returned {response.status_code}"), "transaction_id": response_data.get("transaction_id") or response_data.get("id")}
        except Exception as e:
            logger.exception("Peyflex request failed: %s", e)
            return {"success": False, "message": str(e), "transaction_id": None}

    async def purchase_airtime(self, phone: str, amount: float, network: str) -> dict:
        payload = {
            "account_id": self.account_id,
            "network": network,
            "phone": phone,
            "amount": amount,
            "source": "telegram_bot",
        }
        return await self._post(self.airtime_path, payload)

    async def purchase_data(self, phone: str, amount: float, unit: str, network: str) -> dict:
        payload = {
            "account_id": self.account_id,
            "network": network,
            "phone": phone,
            "amount": amount,
            "unit": unit,
            "source": "telegram_bot",
        }
        return await self._post(self.data_path, payload)

peyflex_client = PeyflexAPIClient()

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
pending_purchase = {}
user_phone = {}
agent_mode = {}
pending_payments = {}  # Store pending Paystack payments: {reference: {chat_id, kind, amount, unit, network, phone}}

# UI Builders
def build_buy_menu() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="Buy", callback_data="buy_menu")],
                [InlineKeyboardButton(text="History", callback_data="show_history")]]
    return InlineKeyboardMarkup(keyboard)

def build_buy_type_menu() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="Airtime", callback_data="buy_airtime"),
                 InlineKeyboardButton(text="Data", callback_data="buy_data")]]
    return InlineKeyboardMarkup(keyboard)

def build_network_menu(kind: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="MTN", callback_data=f"buy_network:{kind}:MTN")],
                [InlineKeyboardButton(text="GLO", callback_data=f"buy_network:{kind}:GLO")],
                [InlineKeyboardButton(text="9MOBILE", callback_data=f"buy_network:{kind}:9MOBILE")],
                [InlineKeyboardButton(text="AIRTEL", callback_data=f"buy_network:{kind}:AIRTEL")]]
    return InlineKeyboardMarkup(keyboard)

def build_airtime_options_menu(network: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text=f"₦100 {network}", callback_data=f"buy_option:airtime:100:NGN:{network}"),
                 InlineKeyboardButton(text=f"₦500 {network}", callback_data=f"buy_option:airtime:500:NGN:{network}")],
                [InlineKeyboardButton(text=f"₦1000 {network}", callback_data=f"buy_option:airtime:1000:NGN:{network}")]]
    return InlineKeyboardMarkup(keyboard)

def build_data_options_menu(network: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text=f"1GB {network}", callback_data=f"buy_option:data:1:GB:{network}"),
                 InlineKeyboardButton(text=f"2GB {network}", callback_data=f"buy_option:data:2:GB:{network}")],
                [InlineKeyboardButton(text=f"5GB {network}", callback_data=f"buy_option:data:5:GB:{network}")]]
    return InlineKeyboardMarkup(keyboard)

def build_amount_menu(kind: str, network: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="₦100", callback_data=f"buy_amount:{kind}:100:{network}"),
                 InlineKeyboardButton(text="₦500", callback_data=f"buy_amount:{kind}:500:{network}")],
                [InlineKeyboardButton(text="₦1,000", callback_data=f"buy_amount:{kind}:1000:{network}"),
                 InlineKeyboardButton(text="₦2,000", callback_data=f"buy_amount:{kind}:2000:{network}")],
                [InlineKeyboardButton(text="₦5,000", callback_data=f"buy_amount:{kind}:5000:{network}")]]
    return InlineKeyboardMarkup(keyboard)

def build_payment_method_menu() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="💳 Card", callback_data="payment_method:card"),
                 InlineKeyboardButton(text="🏦 Bank Transfer", callback_data="payment_method:bank")]]
    return InlineKeyboardMarkup(keyboard)

# Callbacks
async def buy_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    await query.message.reply_text("What would you like to purchase?", reply_markup=build_buy_type_menu())

async def show_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    chat_id = query.message.chat_id
    history = get_history(chat_id, limit=20)
    if not history:
        await query.message.reply_text("No transactions yet.")
        return
    lines = [f"{i+1}. {h['timestamp']} — {h['kind']} {h['amount']}{h['unit']} {h['network']} cost ₦{h['cost']:.2f}" for i, h in enumerate(history)]
    await query.message.reply_text("Your last transactions:\n" + "\n".join(lines))

async def buy_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    if query.data == "buy_airtime":
        await query.message.reply_text("Choose network for airtime:", reply_markup=build_network_menu("airtime"))
    elif query.data == "buy_data":
        await query.message.reply_text("Choose network for data:", reply_markup=build_network_menu("data"))

async def buy_network_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "buy_network":
        await query.message.reply_text("Invalid selection.")
        return
    kind, network = parts[1], parts[2]
    await query.message.reply_text(f"How much? ({kind}):", reply_markup=build_amount_menu(kind, network))

async def buy_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 4 or parts[0] != "buy_amount":
        await query.message.reply_text("Invalid selection.")
        return
    kind, amount_str, network = parts[1], parts[2], parts[3]
    try:
        amount = float(amount_str)
    except ValueError:
        await query.message.reply_text("Invalid amount.")
        return
    chat_id = query.message.chat_id
    pending_purchase[chat_id] = {"kind": kind, "amount": amount, "unit": "NGN" if kind == "airtime" else "GB", "network": network}
    await query.message.reply_text(f"Send phone number (e.g. 08012345678):")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.message.reply_text(
        f"Hi {name}!\n\nSend:\n  Buy airtime 500 MTN 08012345678\n  Buy data 1GB MTN 08012345678\n\n"
        "Commands: /buy, /buydata, /ask <question>, /agent",
        reply_markup=build_buy_menu(),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Commands: /start, /buy, /buydata, /ask <question>, /agent")

# Parsers
def parse_purchase_request(text: str) -> tuple[str, float, str, str, str] | None:
    text = text.strip()
    # Normalize user phrasing and common typos like "want to by" -> "buy"
    text = re.sub(r"\b(i\s+)?want\s+to\s+(?:by|buy)\b", "buy", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(i\s+)?want\s+to\s+buy\s+airtime\b", "buy airtime", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(i\s+)?want\s+to\s+buy\s+data\b", "buy data", text, flags=re.IGNORECASE)
    text = re.sub(r"^/buy\b", "buy airtime", text, flags=re.IGNORECASE)
    text = re.sub(r"^/buydata\b", "buy data", text, flags=re.IGNORECASE)
    m = re.search(r"buy airtime\s+(\d+(?:\.\d+)?)\s+(\w+)\s+(\d+)", text, flags=re.IGNORECASE)
    if m:
        return "airtime", float(m.group(1)), "NGN", m.group(2).upper(), m.group(3)
    m = re.search(r"buy data\s+(\d+(?:\.\d+)?)([A-Za-z]{1,3})?\s+(\w+)\s+(\d+)", text, flags=re.IGNORECASE)
    if m:
        return "data", float(m.group(1)), (m.group(2).upper() if m.group(2) else "MB"), m.group(3).upper(), m.group(4)
    return None

def get_bundle_cost(kind: str, amount: float, unit: str, network: str) -> float | None:
    bundles = BUNDLE_CATALOG.get(kind, [])
    for bundle in bundles:
        if bundle.get("amount") == amount and bundle.get("unit") == unit and bundle.get("network") == network:
            return bundle.get("cost")
    return None

async def get_ai_response(prompt: str) -> str:
    if openai_client is None:
        return "AI not configured."
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a helpful assistant for a Telegram airtime/data bot."},
                      {"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("OpenAI failed")
        return "AI service unavailable."

def normalize_phone(text: str) -> str:
    normalized = re.sub(r"[\s\-()]+", "", text)
    return ("+" + normalized[1:]) if normalized.startswith("+") else normalized

def _is_phone_number(text: str) -> bool:
    return bool(re.fullmatch(r"\+?\d{6,15}", normalize_phone(text)))

def parse_buy_option_callback_data(data: str) -> tuple[str, float, str, str] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "buy_option": return None
    try:
        return parts[1], float(parts[2]), parts[3], parts[4]
    except ValueError:
        return None

# Main Flow
async def process_purchase(update: Update, kind: str, amount: float, unit: str, network: str, phone: str) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    cost = get_bundle_cost(kind, amount, unit, network) or (amount if kind == "airtime" else 0.0)
    pending_purchase[chat_id] = {"kind": kind, "amount": amount, "unit": unit, "network": network, "phone": phone, "cost": cost}
    await update.message.reply_text(f"Placing {kind} purchase for {phone} on {network}.\nCost: ₦{cost:.2f}")
    if kind == "airtime":
        result = await peyflex_client.purchase_airtime(phone, amount, network)
    else:
        result = await peyflex_client.purchase_data(phone, amount, unit, network)
    status = "success" if result.get("success") else "failed"
    transaction_id = result.get("transaction_id") or (f"PEYFLEX-{uuid.uuid4().hex[:12].upper()}" if status == "success" else None)
    append_transaction(chat_id, {"kind": kind, "amount": amount, "unit": unit, "network": network, "phone": phone, "cost": cost, "status": status, "balance_after": 0.0, "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z", "transaction_id": transaction_id})
    pending_purchase.pop(chat_id, None)
    if status == "success":
        await update.message.reply_text(f"Success! Airtime/data purchased from Peyflex. ID: {transaction_id}")
    else:
        await update.message.reply_text(f"Purchase failed: {result.get('message', 'Unknown error')}.")

async def buy_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    parsed = parse_buy_option_callback_data(query.data)
    if not parsed:
        await query.message.reply_text("Invalid option.")
        return
    kind, amount, unit, network = parsed
    chat_id = query.message.chat_id
    if chat_id in user_phone:
        await query.message.reply_text("Using saved phone.")
        await process_purchase(query.message, kind, amount, unit, network, user_phone[chat_id])
        return
    pending_purchase[chat_id] = {"kind": kind, "amount": amount, "unit": unit, "network": network}
    await query.message.reply_text("Send phone number (e.g. 08012345678):")

async def confirm_purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    chat_id = query.message.chat_id
    if chat_id not in pending_purchase or chat_id not in user_phone:
        await query.message.reply_text("No pending purchase.")
        return
    await query.message.reply_text("Choose payment method:", reply_markup=build_payment_method_menu())

async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    chat_id = query.message.chat_id
    if chat_id not in pending_purchase or chat_id not in user_phone:
        await query.message.reply_text("No pending purchase.")
        return
    parts = query.data.split(":")
    if len(parts) != 2 or parts[0] != "payment_method":
        await query.message.reply_text("Invalid selection.")
        return
    payment_method = parts[1]
    info = pending_purchase[chat_id]
    
    if payment_method == "card":
        # Initiate Paystack payment
        amount_kobo = int(info["amount"] * 100)  # Convert to kobo
        email = f"user_{chat_id}@telegram.local"  # Unique email per user
        metadata = {
            "chat_id": chat_id,
            "kind": info["kind"],
            "amount": info["amount"],
            "unit": info["unit"],
            "network": info["network"],
            "phone": user_phone[chat_id],
        }
        result = await paystack_client.initialize_payment(email, amount_kobo, metadata)
        if result["success"]:
            reference = result["reference"]
            pending_payments[reference] = metadata
            # Store payment record in database
            append_payment(chat_id, reference, "pending", info["kind"], info["amount"], info["unit"], info["network"], user_phone[chat_id])
            pending_purchase.pop(chat_id, None)
            await query.message.reply_text(
                f"Click the link below to complete your payment:\n{result['authorization_url']}\n\n"
                f"After payment, the airtime/data will be credited to {user_phone[chat_id]} on {info['network']}."
            )
        else:
            await query.message.reply_text(f"Failed to initialize payment: {result['message']}")
    elif payment_method == "bank":
        # Bank transfer - direct purchase
        info = pending_purchase.pop(chat_id)
        await query.message.reply_text("Processing bank transfer...")
        await process_purchase(query.message, info["kind"], info["amount"], info["unit"], info["network"], user_phone[chat_id])

async def cancel_purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    pending_purchase.pop(query.message.chat_id, None)
    await query.message.reply_text("Cancelled. Tap /buy to start over.")

async def bank_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 2 or parts[0] != "bank_paid":
        await query.message.reply_text("Invalid action.")
        return
    reference = parts[1]
    if reference not in pending_payments:
        await query.message.reply_text("Payment reference not found.")
        return
    # mark payment awaiting verification
    update_payment_status(reference, "awaiting_verification")
    await query.message.reply_text(
        "Thanks — we've recorded your payment and will verify it shortly. You'll be notified when it's credited."
    )

async def paystack_webhook_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process Paystack payment confirmation webhook"""
    # This is called via /paystack_webhook reference=xxx
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Invalid webhook call.")
        return
    
    reference = context.args[0]
    if reference not in pending_payments:
        await update.message.reply_text(f"Payment reference {reference} not found.")
        return
    
    # Verify payment with Paystack
    verification = await paystack_client.verify_payment(reference)
    if not verification["success"]:
        await update.message.reply_text(f"Payment verification failed: {verification['message']}")
        update_payment_status(reference, "failed")
        return
    
    # Payment verified - process the purchase
    metadata = pending_payments.pop(reference)
    chat_id = metadata["chat_id"]
    kind = metadata["kind"]
    amount = metadata["amount"]
    unit = metadata["unit"]
    network = metadata["network"]
    phone = metadata["phone"]
    
    # Send notification to user
    try:
        bot = context.bot
        await bot.send_message(
            chat_id=chat_id,
            text="✅ Payment confirmed! Crediting your account now..."
        )
    except Exception as e:
        logger.exception("Failed to send confirmation message: %s", e)
    
    # Call Peyflex API to credit the user
    if kind == "airtime":
        result = await peyflex_client.purchase_airtime(phone, amount, network)
    else:
        result = await peyflex_client.purchase_data(phone, amount, unit, network)
    
    status = "success" if result.get("success") else "failed"
    transaction_id = result.get("transaction_id") or (f"PAYSTACK-{reference}" if status == "success" else None)
    
    # Log transaction
    append_transaction(chat_id, {
        "kind": kind,
        "amount": amount,
        "unit": unit,
        "network": network,
        "phone": phone,
        "cost": amount,  # Paystack will handle the cost
        "status": status,
        "balance_after": 0.0,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "transaction_id": transaction_id,
    })
    
    # Update payment status
    update_payment_status(reference, status)
    
    # Send final message to user
    try:
        bot = context.bot
        if status == "success":
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Success! {amount} {unit} of {kind} credited to {phone} on {network}.\\nTransaction ID: {transaction_id}"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Failed to credit account: {result.get('message', 'Unknown error')}"
            )
    except Exception as e:
        logger.exception("Failed to send final message: %s", e)

async def verify_bank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if not is_admin(user_id):
        await update.message.reply_text("Unauthorized. Only admins can verify bank payments.")
        logger.warning("Unauthorized /verify_bank attempt by user %s", user_id)
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage: /verify_bank <reference>")
        return
    reference = context.args[0].strip()
    # Look up in payments table
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, status, kind, amount, unit, network, phone FROM payments WHERE reference = ?", (reference,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await update.message.reply_text(f"Reference {reference} not found.")
        return
    chat_id_db, status_db, kind_db, amount_db, unit_db, network_db, phone_db = row
    if status_db == "success":
        await update.message.reply_text("This payment is already verified.")
        return
    # Prefer in-memory pending_payments if present
    metadata = pending_payments.pop(reference, None)
    if metadata:
        chat_id = metadata.get("chat_id", chat_id_db)
        kind = metadata.get("kind", kind_db)
        amount = metadata.get("amount", amount_db)
        unit = metadata.get("unit", unit_db)
        network = metadata.get("network", network_db)
        phone = metadata.get("phone", phone_db)
    else:
        chat_id = chat_id_db
        kind = kind_db
        amount = amount_db
        unit = unit_db
        network = network_db
        phone = phone_db
    await update.message.reply_text(f"Verifying and processing payment {reference}...")
    if kind == "airtime":
        result = await peyflex_client.purchase_airtime(phone, amount, network)
    else:
        result = await peyflex_client.purchase_data(phone, amount, unit, network)
    status = "success" if result.get("success") else "failed"
    transaction_id = result.get("transaction_id") or (f"BANK-{reference}" if status == "success" else None)
    append_transaction(chat_id, {
        "kind": kind,
        "amount": amount,
        "unit": unit,
        "network": network,
        "phone": phone,
        "cost": amount,
        "status": status,
        "balance_after": 0.0,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "transaction_id": transaction_id,
    })
    update_payment_status(reference, status)
    try:
        bot = update.get_bot()
        if status == "success":
            user_text = f"✅ Success! {amount} {unit} of {kind} credited to {phone} on {network}.\nTransaction ID: {transaction_id}"
            await bot.send_message(chat_id=chat_id, text=user_text)
            await notify_admins(bot, f"Admin verification complete for {reference}: SUCCESS. User {chat_id} credited {amount} {unit} on {network}.")
        else:
            user_text = f"❌ Failed to credit account: {result.get('message', 'Unknown error')}"
            await bot.send_message(chat_id=chat_id, text=user_text)
            await notify_admins(bot, f"Admin verification complete for {reference}: FAILED. User {chat_id} attempted {amount} {unit} on {network}. Error: {result.get('message', 'Unknown error')}")
    except Exception as exc:
        logger.exception("Failed to notify user/admin after /verify_bank: %s", exc)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    lowered = text.lower()
    if lowered in {"/buy", "/buydata"}:
        await update.message.reply_text("What would you like to purchase?", reply_markup=build_buy_type_menu())
        return
    if re.search(r"\b(i\s+)?want\s+to\s+buy\b", lowered):
        if "airtime" in lowered and "data" not in lowered:
            await update.message.reply_text("Choose network for airtime:", reply_markup=build_network_menu("airtime"))
            return
        if "data" in lowered and "airtime" not in lowered:
            await update.message.reply_text("Choose network for data:", reply_markup=build_network_menu("data"))
            return
        await update.message.reply_text("What would you like to buy?", reply_markup=build_buy_type_menu())
        return
    chat_id = update.message.chat_id
    if agent_mode.get(chat_id) and not text.startswith("/"):
        answer = await get_ai_response(text)
        await update.message.reply_text(answer)
        return
    if chat_id in pending_purchase:
        if _is_phone_number(text):
            info = pending_purchase[chat_id]
            normalized = normalize_phone(text)
            user_phone[chat_id] = normalized
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text="Confirm", callback_data="confirm_purchase"), InlineKeyboardButton(text="Cancel", callback_data="cancel_purchase")]])
            await update.message.reply_text(f"Confirm:\nType: {info['kind']}\nAmount: {info['amount']} {info['unit']}\nNetwork: {info['network']}\nPhone: {normalized}\n", reply_markup=keyboard)
        else:
            await update.message.reply_text("Invalid phone. Send: 08012345678 or +2348012345678")
        return
    if text.lower().startswith("/setphone"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not _is_phone_number(parts[1]):
            await update.message.reply_text("Usage: /setphone <number>")
            return
        user_phone[chat_id] = normalize_phone(parts[1])
        await update.message.reply_text(f"Saved: {user_phone[chat_id]}")
        return
    if text.lower().strip() == "/phone":
        value = user_phone.get(chat_id)
        await update.message.reply_text(f"Your phone: {value}" if value else "No phone saved.")
        return
    if text.lower().startswith("/history"):
        history = get_history(chat_id, limit=20)
        if not history:
            await update.message.reply_text("No transactions.")
            return
        lines = [f"{i+1}. {h['timestamp']} — {h['kind']} {h['amount']}{h['unit']} {h['network']} ₦{h['cost']:.2f}" for i, h in enumerate(history)]
        await update.message.reply_text("Transactions:\n" + "\n".join(lines))
        return
    if text.lower().startswith("/clearhistory"):
        clear_history(chat_id)
        await update.message.reply_text("History cleared.")
        return
    if text.lower().startswith("/ask") or text.lower().startswith("/ai"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            await update.message.reply_text("Usage: /ask <question>")
            return
        answer = await get_ai_response(parts[1])
        await update.message.reply_text(answer)
        return
    if text.lower().startswith("/agent"):
        await agent_command(update, context)
        return
    if text.lower().startswith("/exitagent"):
        await exit_agent_command(update, context)
        return
    parsed = parse_purchase_request(text)
    if not parsed:
        if OPENAI_API_KEY and openai is not None:
            answer = await get_ai_response(text)
            await update.message.reply_text(answer)
            return
        await update.message.reply_text("Unknown format. Send: Buy airtime 500 MTN 08012345678")
        return
    await process_purchase(update, *parsed)

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text: return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) == 1:
        await update.message.reply_text("Usage: /ask <question>")
        return
    answer = await get_ai_response(parts[1])
    await update.message.reply_text(answer)

async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None: return
    agent_mode[chat_id] = True
    await update.message.reply_text("AI mode ON. Send text to chat. /exitagent to stop.")

async def exit_agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None: return
    if agent_mode.pop(chat_id, None):
        await update.message.reply_text("AI mode OFF.")
    else:
        await update.message.reply_text("AI mode not active.")

def verify_token() -> str:
    if TOKEN and TOKEN != "PASTE_YOUR_BOT_TOKEN_HERE": return TOKEN
    logger.error("Token not set. Set TELEGRAM_BOT_TOKEN.")
    sys.exit(1)

# State Storage
pending_purchase: dict[int, dict[str, str | float]] = {}
user_phone: dict[int, str] = {}
agent_mode: dict[int, bool] = {}

# Database
TRANSACTION_DB_FILE = os.environ.get("TRANSACTION_DB_FILE", "data/history.db")
if os.path.isabs(TRANSACTION_DB_FILE):
    raise RuntimeError("TRANSACTION_DB_FILE must be relative")

def init_transaction_db() -> None:
    # ensure directory exists
    db_dir = os.path.dirname(TRANSACTION_DB_FILE) or "."
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                unit TEXT NOT NULL,
                network TEXT NOT NULL,
                phone TEXT NOT NULL,
                cost REAL NOT NULL,
                status TEXT NOT NULL,
                balance_after REAL NOT NULL,
                timestamp TEXT NOT NULL,
                transaction_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                reference TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                unit TEXT NOT NULL,
                network TEXT NOT NULL,
                phone TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
    conn.close()

def append_transaction(chat_id: int, entry: dict) -> None:
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("""
            INSERT INTO transactions (chat_id, kind, amount, unit, network, phone, cost, status, balance_after, timestamp, transaction_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, entry.get("kind"), entry.get("amount"), entry.get("unit"), entry.get("network"), entry.get("phone"), entry.get("cost"), entry.get("status"), entry.get("balance_after"), entry.get("timestamp"), entry.get("transaction_id")))
    conn.close()

def get_history(chat_id: int, limit: int = 20) -> list[dict]:
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT kind, amount, unit, network, phone, cost, status, balance_after, timestamp FROM transactions WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, limit))
    rows = cur.fetchall()
    conn.close()
    return [{"kind": r[0], "amount": r[1], "unit": r[2], "network": r[3], "phone": r[4], "cost": r[5], "status": r[6], "balance_after": r[7], "timestamp": r[8]} for r in rows]

def clear_history(chat_id: int) -> None:
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("DELETE FROM transactions WHERE chat_id = ?", (chat_id,))
    conn.close()

def append_payment(chat_id: int, reference: str, status: str, kind: str, amount: float, unit: str, network: str, phone: str) -> None:
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("""
            INSERT INTO payments (chat_id, reference, status, kind, amount, unit, network, phone, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, reference, status, kind, amount, unit, network, phone, __import__("datetime").datetime.utcnow().isoformat() + "Z"))
    conn.close()

def update_payment_status(reference: str, status: str) -> None:
    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("UPDATE payments SET status = ? WHERE reference = ?", (status, reference))
    conn.close()

# Bundle Catalog
BUNDLE_CATALOG = {
    "airtime": [
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "MTN", "cost": 100.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "MTN", "cost": 500.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "MTN", "cost": 1000.0},
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "GLO", "cost": 98.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "GLO", "cost": 490.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "GLO", "cost": 980.0},
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "9MOBILE", "cost": 99.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "9MOBILE", "cost": 495.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "9MOBILE", "cost": 995.0},
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "AIRTEL", "cost": 101.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "AIRTEL", "cost": 505.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "AIRTEL", "cost": 1010.0},
    ],
    "data": [
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "MTN", "cost": 250.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "MTN", "cost": 450.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "MTN", "cost": 1000.0},
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "GLO", "cost": 245.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "GLO", "cost": 440.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "GLO", "cost": 980.0},
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "9MOBILE", "cost": 255.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "9MOBILE", "cost": 460.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "9MOBILE", "cost": 1020.0},
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "AIRTEL", "cost": 252.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "AIRTEL", "cost": 455.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "AIRTEL", "cost": 1015.0},
    ],
}

def main() -> None:
    init_transaction_db()
    token = verify_token()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("ai", ask_command))
    app.add_handler(CommandHandler("agent", agent_command))
    app.add_handler(CommandHandler("exitagent", exit_agent_command))
    app.add_handler(CommandHandler("buy", handle_message))
    app.add_handler(CommandHandler("buydata", handle_message))
    app.add_handler(CommandHandler("paystack_webhook", paystack_webhook_handler))
    app.add_handler(CommandHandler("verify_bank", verify_bank_command))
    
    app.add_handler(CallbackQueryHandler(buy_menu_callback, pattern="^buy_menu$"))
    app.add_handler(CallbackQueryHandler(show_history_callback, pattern="^show_history$"))
    app.add_handler(CallbackQueryHandler(buy_type_callback, pattern="^buy_(airtime|data)$"))
    app.add_handler(CallbackQueryHandler(buy_network_callback, pattern="^buy_network:"))
    app.add_handler(CallbackQueryHandler(buy_amount_callback, pattern="^buy_amount:"))
    app.add_handler(CallbackQueryHandler(buy_option_callback, pattern="^buy_option:"))
    app.add_handler(CallbackQueryHandler(payment_method_callback, pattern="^payment_method:"))
    app.add_handler(CallbackQueryHandler(bank_paid_callback, pattern="^bank_paid:"))
    app.add_handler(CallbackQueryHandler(confirm_purchase_callback, pattern="^confirm_purchase$"))
    app.add_handler(CallbackQueryHandler(cancel_purchase_callback, pattern="^cancel_purchase$"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    bot = Bot(TOKEN)
    bot.delete_webhook(drop_pending_updates=True)
    
    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
