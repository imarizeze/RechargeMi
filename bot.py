import logging
import os
import re
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# Optional: put your bot token in a .env file alongside bot.py.
# Example .env:
#   TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
#
# If you use a .env file, install python-dotenv (it's in requirements.txt).
try:
    from dotenv import load_dotenv

    load_dotenv()  # loads .env if present
except ImportError:
    # It's okay if python-dotenv is not installed; env vars still work normally.
    pass

# Preferred way: set the token through an environment variable.
# Example (Windows PowerShell):
#   $Env:TELEGRAM_BOT_TOKEN = "<your-token-here>"
# Example (Linux/macOS):
#   export TELEGRAM_BOT_TOKEN="<your-token-here>"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Fallback option: for quick testing only (DO NOT commit real tokens to source control)
# TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Bot Implementation
# -----------------------------------------------------------------------------

def build_buy_menu() -> InlineKeyboardMarkup:
    """Build an inline keyboard for choosing between airtime and data."""
    keyboard = [
        [InlineKeyboardButton(text="Buy", callback_data="buy_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_buy_type_menu() -> InlineKeyboardMarkup:
    """Build an inline keyboard for choosing airtime vs data."""
    keyboard = [
        [
            InlineKeyboardButton(text="Airtime", callback_data="buy_airtime"),
            InlineKeyboardButton(text="Data", callback_data="buy_data"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def buy_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the 'Buy' button to show the airtime/data choice."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await query.message.reply_text(
        "What would you like to purchase?",
        reply_markup=build_buy_type_menu(),
    )


def build_airtime_options_menu() -> InlineKeyboardMarkup:
    """Build a menu of sample airtime options (auto-send when tapped)."""
    keyboard = [
        [
            InlineKeyboardButton(text="₦100 MTN", callback_data="buy_option:airtime:100:NGN:MTN"),
            InlineKeyboardButton(text="₦500 MTN", callback_data="buy_option:airtime:500:NGN:MTN"),
        ],
        [
            InlineKeyboardButton(text="₦1000 MTN", callback_data="buy_option:airtime:2000:NGN:MTN"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_data_options_menu() -> InlineKeyboardMarkup:
    """Build a menu of sample data bundle options (auto-send when tapped)."""
    keyboard = [
        [
            InlineKeyboardButton(text="1GB Data", callback_data="buy_option:data:1:GB:MTN"),
            InlineKeyboardButton(text="2GB Data", callback_data="buy_option:data:2:GB:MTN"),
        ],
        [
            InlineKeyboardButton(text="5GB Data", callback_data="buy_option:data:5:GB:MTN"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def buy_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the choice between airtime and data from the inline menu."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    if query.data == "buy_airtime":
        await query.message.reply_text(
            "Choose an airtime bundle:",
            reply_markup=build_airtime_options_menu(),
        )
    elif query.data == "buy_data":
        await query.message.reply_text(
            "Choose a data bundle:",
            reply_markup=build_data_options_menu(),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the /start command is issued."""
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.message.reply_text(
        f"Hi {name}!\n\n"
        "Send me a message like:\n"
        "  Buy airtime 500 MTN 08012345678\n"
        "  Buy data 1GB MTN 08012345678\n\n"
        "Or use /buy or /buydata commands",
        reply_markup=build_buy_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help text."""
    await update.message.reply_text(
        "I can help you submit an airtime or data purchase request.\n\n"
        "Examples:\n"
        "  Buy airtime 500 MTN 08012345678\n"
        "  Buy data 1GB MTN 08012345678\n"
        "  /buy 100 MTN 08012345678\n"
        "  /buydata 1GB MTN 08012345678\n"
        "  /help"
    )


def parse_purchase_request(text: str) -> tuple[str, float, str, str, str] | None:
    """Parse a simple airtime or data purchase request.

    Supported formats:
      - Buy airtime 100  08012345678
      - /buy 100, 1000 MTN 08012345678
      - Buy data 1GB MTN 08012345678
      - /buydata 1GB MTN 08012345678

    Returns:
      (kind, amount, unit, network, phone)
      kind: "airtime" or "data"
      unit: "NGN" for airtime, or data unit (e.g., "GB", "MB") for data
    """
    text = text.strip()
    # Normalize command prefixes
    text = re.sub(r"^/buy\b", "buy airtime", text, flags=re.IGNORECASE)
    text = re.sub(r"^/buydata\b", "buy data", text, flags=re.IGNORECASE)

    # Airtime: buy airtime <amount> <network> <phone>
    m = re.search(r"buy airtime\s+(\d+(?:\.\d+)?)\s+(\w+)\s+(\d+)", text, flags=re.IGNORECASE)
    if m:
        amount = float(m.group(1))
        network = m.group(2).upper()
        phone = m.group(3)
        return "airtime", amount, "NGN", network, phone

    # Data: buy data <amount><unit>? <network> <phone>
    m = re.search(
        r"buy data\s+(\d+(?:\.\d+)?)([A-Za-z]{1,3})?\s+(\w+)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        amount = float(m.group(1))
        unit = m.group(2).upper() if m.group(2) else "MB"
        network = m.group(3).upper()
        phone = m.group(4)
        return "data", amount, unit, network, phone

    return None


async def process_purchase(
    update: Update,
    kind: str,
    amount: float,
    unit: str,
    network: str,
    phone: str,
) -> None:
    """Respond with a purchase confirmation message."""
    await update.message.reply_text(f"Processing your {kind} request...")

    if kind == "airtime":
        await update.message.reply_text(
            f"✅ Airtime purchase request received:\n"
            f"  Amount: {amount} {unit}\n"
            f"  Network: {network}\n"
            f"  Phone: {phone}\n\n"
            "(This is a demo; no real transaction was performed.)"
        )
    else:
        await update.message.reply_text(
            f"✅ Data bundle purchase request received:\n"
            f"  Size: {amount}{unit}\n"
            f"  Network: {network}\n"
            f"  Phone: {phone}\n\n"
            "(This is a demo; no real transaction was performed.)"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # If the user just wrote /buy or /buydata, show the choice menu.
    if text.lower() in {"/buy", "/buydata"}:
        await update.message.reply_text(
            "What would you like to purchase?",
            reply_markup=build_buy_type_menu(),
        )
        return

    # If we are waiting for a phone number from this user, use it.
    chat_id = update.message.chat_id
    if chat_id in pending_purchase:
        if _is_phone_number(text):
            info = pending_purchase[chat_id]
            normalized = normalize_phone(text)
            user_phone[chat_id] = normalized

            # Ask the user to confirm before sending.
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Confirm",
                            callback_data="confirm_purchase",
                        ),
                        InlineKeyboardButton(
                            text="Cancel",
                            callback_data="cancel_purchase",
                        ),
                    ]
                ]
            )

            await update.message.reply_text(
                "Please confirm your purchase:\n"
                f"  Type: {info['kind']}\n"
                f"  Amount: {info['amount']} {info['unit']}\n"
                f"  Network: {info['network']}\n"
                f"  Phone: {normalized}\n",
                reply_markup=keyboard,
            )
        else:
            await update.message.reply_text(
                "That doesn't look like a valid phone number. "
                "Send in this format: 08012345678 or +2348012345678"
            )
        return

    # Handle /setphone and /phone commands
    if text.lower().startswith("/setphone"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not _is_phone_number(parts[1]):
            await update.message.reply_text(
                "Usage: /setphone <phone number>\n"
                "Example: /setphone 08012345678"
            )
            return
        normalized = normalize_phone(parts[1])
        user_phone[chat_id] = normalized
        await update.message.reply_text(
            f"Phone number saved: {normalized} (used for future purchases)."
        )
        return

    if text.lower().strip() == "/phone":
        value = user_phone.get(chat_id)
        if value:
            await update.message.reply_text(f"Your saved phone number is: {value}")
        else:
            await update.message.reply_text(
                "No phone number saved yet. Use /setphone <number> or buy something to set one."
            )
        return

    parsed = parse_purchase_request(text)

    if not parsed:
        await update.message.reply_text(
            "I didn't recognize that format.\n"
            "Send: Buy airtime 500 MTN 08012345678 or Buy data 1GB MTN 08012345678"
        )
        return

    kind, amount, unit, network, phone = parsed
    await process_purchase(update, kind, amount, unit, network, phone)


def verify_token() -> str:
    if TOKEN and TOKEN != "PASTE_YOUR_BOT_TOKEN_HERE":
        return TOKEN

    logger.error(
        "Telegram bot token is not set.\n"
        "Set TELEGRAM_BOT_TOKEN in your environment (recommended) or edit bot.py to set TOKEN."
    )
    sys.exit(1)


def parse_buy_option_callback_data(data: str) -> tuple[str, float, str, str] | None:
    """Parse callback_data used for buy option buttons.

    Format: buy_option:<kind>:<amount>:<unit>:<network>
    Example: buy_option:airtime:500:NGN:MTN
    """
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "buy_option":
        return None

    _, kind, amount_str, unit, network = parts
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    return kind, amount, unit, network


# Simple in-memory per-chat state. For production, use a database.
# Stores a pending purchase request when we are waiting for a phone number.
pending_purchase: dict[int, dict[str, str | float]] = {}

# Stores the user’s preferred/default phone number (per chat).
user_phone: dict[int, str] = {}


def normalize_phone(text: str) -> str:
    """Normalize a phone number for storage and comparison."""
    normalized = re.sub(r"[\s\-()]+", "", text)
    # Keep leading + if present.
    if normalized.startswith("+"):
        return "+" + normalized[1:]
    return normalized


def _is_phone_number(text: str) -> bool:
    """Detect if a text looks like a phone number.

    This is intentionally permissive (digits, +, -, spaces).
    """
    return bool(re.fullmatch(r"\+?\d{6,15}", normalize_phone(text)))


async def confirm_purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm a pending purchase and send the final request."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    chat_id = query.message.chat_id
    if chat_id not in pending_purchase or chat_id not in user_phone:
        await query.message.reply_text(
            "There is no pending purchase to confirm. Start again with /buy."
        )
        return

    info = pending_purchase.pop(chat_id)
    phone = user_phone[chat_id]

    await query.message.reply_text("Confirmed — sending your purchase request...")
    await process_purchase(
        query.message,
        info["kind"],
        info["amount"],
        info["unit"],
        info["network"],
        phone,
    )


async def cancel_purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel a pending purchase."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    chat_id = query.message.chat_id
    pending_purchase.pop(chat_id, None)
    await query.message.reply_text(
        "Purchase canceled. If you'd like to start over, tap /buy."
    )


async def buy_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle an option button press and ask for the phone number."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    parsed = parse_buy_option_callback_data(query.data)
    if not parsed:
        await query.message.reply_text("Sorry, I couldn't understand that option.")
        return

    kind, amount, unit, network = parsed
    chat_id = query.message.chat_id

    # If we already have a stored phone for this chat, use it immediately.
    if chat_id in user_phone:
        await query.message.reply_text(
            "Using your saved phone number. If you'd like to change it, send /setphone <number>."
        )
        await process_purchase(
            query.message,
            kind,
            amount,
            unit,
            network,
            user_phone[chat_id],
        )
        return

    # Store the pending purchase until the user provides a phone number.
    pending_purchase[chat_id] = {
        "kind": kind,
        "amount": amount,
        "unit": unit,
        "network": network,
    }

    await query.message.reply_text(
        "Almost there! Please send me the phone number you want to top up (e.g. 08012345678)."
    )


def main() -> None:
    token = verify_token()

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("buy", handle_message))
    app.add_handler(CommandHandler("buydata", handle_message))
    app.add_handler(CallbackQueryHandler(buy_menu_callback, pattern="^buy_menu$"))
    app.add_handler(CallbackQueryHandler(buy_type_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(buy_option_callback, pattern="^buy_option:"))
    app.add_handler(CallbackQueryHandler(confirm_purchase_callback, pattern="^confirm_purchase$"))
    app.add_handler(CallbackQueryHandler(cancel_purchase_callback, pattern="^cancel_purchase$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
