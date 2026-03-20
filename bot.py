import logging
import os
import re
import sys

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# IMPORTANT: do not embed your token in code. Use environment variables or secret management.

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
    """Build an inline keyboard for choosing between airtime/data/balance/history."""
    keyboard = [
        [InlineKeyboardButton(text="Buy", callback_data="buy_menu")],
        [InlineKeyboardButton(text="My Balance", callback_data="show_balance")],
        [InlineKeyboardButton(text="History", callback_data="show_history")],
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


async def show_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user wallet balance over inline button."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    chat_id = query.message.chat_id
    balance = user_wallet.get(chat_id, 0.0)
    await query.message.reply_text(
        f"Your wallet balance is: ₦{balance:.2f}\n"
        "Top up with /deposit <amount> (e.g., /deposit 1000)."
    )


async def show_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user transaction history via inline button."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    chat_id = query.message.chat_id
    history = get_history(chat_id, limit=20)
    if not history:
        await query.message.reply_text("No transactions yet. Make a purchase first.")
        return

    lines = [
        f"{i+1}. {h['timestamp']} — {h['kind']} {h['amount']}{h['unit']} {h['network']} cost ₦{h['cost']:.2f}"
        for i, h in enumerate(history)
    ]
    await query.message.reply_text("Your last transactions:\n" + "\n".join(lines))


def build_airtime_options_menu(network: str) -> InlineKeyboardMarkup:
    """Build a menu of sample airtime options (network-specific)."""
    keyboard = [
        [
            InlineKeyboardButton(text=f"₦100 {network}", callback_data=f"buy_option:airtime:100:NGN:{network}"),
            InlineKeyboardButton(text=f"₦500 {network}", callback_data=f"buy_option:airtime:500:NGN:{network}"),
        ],
        [
            InlineKeyboardButton(text=f"₦1000 {network}", callback_data=f"buy_option:airtime:1000:NGN:{network}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_data_options_menu(network: str) -> InlineKeyboardMarkup:
    """Build a menu of sample data bundle options (network-specific)."""
    keyboard = [
        [
            InlineKeyboardButton(text=f"1GB {network}", callback_data=f"buy_option:data:1:GB:{network}"),
            InlineKeyboardButton(text=f"2GB {network}", callback_data=f"buy_option:data:2:GB:{network}"),
        ],
        [
            InlineKeyboardButton(text=f"5GB {network}", callback_data=f"buy_option:data:5:GB:{network}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_network_menu(kind: str) -> InlineKeyboardMarkup:
    """Show network choices before bundle selection."""
    keyboard = [
        [InlineKeyboardButton(text="MTN", callback_data=f"buy_network:{kind}:MTN")],
        [InlineKeyboardButton(text="GLO", callback_data=f"buy_network:{kind}:GLO")],
        [InlineKeyboardButton(text="9MOBILE", callback_data=f"buy_network:{kind}:9MOBILE")],
        [InlineKeyboardButton(text="AIRTEL", callback_data=f"buy_network:{kind}:AIRTEL")],
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
            "Choose network for airtime:",
            reply_markup=build_network_menu("airtime"),
        )
    elif query.data == "buy_data":
        await query.message.reply_text(
            "Choose network for data:",
            reply_markup=build_network_menu("data"),
        )


async def buy_network_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle network selection and show kind-specific bundles for that network."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "buy_network":
        await query.message.reply_text("Invalid selection data.")
        return

    kind = parts[1]
    network = parts[2]

    if kind == "airtime":
        await query.message.reply_text(
            f"Choose an airtime bundle for {network}:",
            reply_markup=build_airtime_options_menu(network),
        )
    elif kind == "data":
        await query.message.reply_text(
            f"Choose a data bundle for {network}:",
            reply_markup=build_data_options_menu(network),
        )
    else:
        await query.message.reply_text("Invalid bundle kind.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the /start command is issued."""
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.message.reply_text(
        f"Hi {name}!\n\n"
        "Send me a message like:\n"
        "  Buy airtime 500 MTN 08012345678\n"
        "  Buy data 1GB MTN 08012345678\n\n"
        "Or use /buy or /buydata commands\n"
        "Wallet commands: /balance, /deposit <amount>",
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
        "Wallet: /balance, /deposit <amount>\n"
        "/help"
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


def get_bundle_cost(kind: str, amount: float, unit: str, network: str) -> float | None:
    """Return configured cost for a bundle or None if unknown."""
    bundles = BUNDLE_CATALOG.get(kind, [])
    for bundle in bundles:
        if (
            bundle.get("amount") == amount
            and bundle.get("unit") == unit
            and bundle.get("network") == network
        ):
            return bundle.get("cost")
    return None


async def process_purchase(
    update: Update,
    kind: str,
    amount: float,
    unit: str,
    network: str,
    phone: str,
) -> None:
    """Respond with a purchase confirmation message (with price deduction)."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    cost = get_bundle_cost(kind, amount, unit, network)

    if cost is None:
        # Fallback: direct charges for airtime equal amount and data as 0.
        cost = amount if kind == "airtime" else 0.0

    balance = user_wallet.get(chat_id, 0.0) if chat_id is not None else 0.0

    if balance < cost:
        missing = cost - balance
        await update.message.reply_text(
            f"💸 Insufficient wallet balance for this bundle."
            f"\nRequired: ₦{cost:.2f} | Current: ₦{balance:.2f}"
            f"\nPlease deposit at least ₦{missing:.2f} with /deposit <amount>."
        )
        return

    # Deduct.
    user_wallet[chat_id] = balance - cost

    # Record in transaction history.
    append_transaction(
        chat_id,
        {
            "kind": kind,
            "amount": amount,
            "unit": unit,
            "network": network,
            "phone": phone,
            "cost": cost,
            "status": "success",
            "balance_after": user_wallet[chat_id],
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        },
    )

    await update.message.reply_text(f"Processing your {kind} request (cost ₦{cost:.2f})...")
    if kind == "airtime":
        await update.message.reply_text(
            f"✅ Airtime purchase confirmed:\n"
            f"  Amount: {amount} {unit}\n"
            f"  Network: {network}\n"
            f"  Phone: {phone}\n"
            f"  Cost: ₦{cost:.2f}\n"
            f"  New wallet balance: ₦{user_wallet[chat_id]:.2f}\n\n"
            "(Demo only; no real transaction was performed.)"
        )
    else:
        await update.message.reply_text(
            f"✅ Data bundle purchase confirmed:\n"
            f"  Size: {amount}{unit}\n"
            f"  Network: {network}\n"
            f"  Phone: {phone}\n"
            f"  Cost: ₦{cost:.2f}\n"
            f"  New wallet balance: ₦{user_wallet[chat_id]:.2f}\n\n"
            "(Demo only; no real transaction was performed.)"
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

    if text.lower().startswith("/history"):
        history = get_history(chat_id, limit=20)
        if not history:
            await update.message.reply_text("No transactions yet. Make a purchase first.")
            return
        lines = [
            f"{i+1}. {h['timestamp']} — {h['kind']} {h['amount']}{h['unit']} {h['network']} cost ₦{h['cost']:.2f}"
            for i, h in enumerate(history)
        ]
        await update.message.reply_text(
            "Your last transactions:\n" + "\n".join(lines)
        )
        return

    if text.lower().startswith("/clearhistory"):
        clear_history(chat_id)
        await update.message.reply_text("Your transaction history has been cleared.")
        return

    if text.lower().startswith("/balance"):
        balance = user_wallet.get(chat_id, 0.0)
        await update.message.reply_text(
            f"Your wallet balance is ₦{balance:.2f}.\n"
            "Use /deposit <amount> to add funds."
        )
        return

    if text.lower().startswith("/deposit"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            await update.message.reply_text("Usage: /deposit <amount>")
            return
        try:
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("Invalid deposit amount; send a number like 500 or 1000")
            return
        if amount <= 0:
            await update.message.reply_text("Deposit must be greater than zero.")
            return

        user_wallet[chat_id] = user_wallet.get(chat_id, 0.0) + amount
        await update.message.reply_text(
            f"Deposit successful. New balance: ₦{user_wallet[chat_id]:.2f}"
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

# Stores user wallet balances for price driven purchases.
user_wallet: dict[int, float] = {}

# Transaction history persistence (SQLite) for production-friendliness.
TRANSACTION_DB_FILE = os.environ.get("TRANSACTION_DB_FILE", "history.db")

# Sanitize path so only relative local paths are allowed by default, preventing accidental injections.
if os.path.isabs(TRANSACTION_DB_FILE):
    raise RuntimeError("TRANSACTION_DB_FILE must be relative path to avoid insecure absolute paths")


def init_transaction_db() -> None:
    import sqlite3

    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute(
            """
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
                timestamp TEXT NOT NULL
            )
            """
        )
    conn.close()


def append_transaction(chat_id: int, entry: dict) -> None:
    import sqlite3

    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute(
            """
            INSERT INTO transactions (
                chat_id, kind, amount, unit, network, phone,
                cost, status, balance_after, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                entry.get("kind"),
                entry.get("amount"),
                entry.get("unit"),
                entry.get("network"),
                entry.get("phone"),
                entry.get("cost"),
                entry.get("status"),
                entry.get("balance_after"),
                entry.get("timestamp"),
            ),
        )
    conn.close()


def get_history(chat_id: int, limit: int = 20) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT kind, amount, unit, network, phone, cost, status, balance_after, timestamp
        FROM transactions
        WHERE chat_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (chat_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "kind": r[0],
            "amount": r[1],
            "unit": r[2],
            "network": r[3],
            "phone": r[4],
            "cost": r[5],
            "status": r[6],
            "balance_after": r[7],
            "timestamp": r[8],
        }
        for r in rows
    ]


def clear_history(chat_id: int) -> None:
    import sqlite3

    conn = sqlite3.connect(TRANSACTION_DB_FILE, check_same_thread=False)
    with conn:
        conn.execute("DELETE FROM transactions WHERE chat_id = ?", (chat_id,))
    conn.close()


# Bundle catalog with price data.
BUNDLE_CATALOG = {
    "airtime": [
        # MTN
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "MTN", "cost": 100.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "MTN", "cost": 500.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "MTN", "cost": 1000.0},
        # GLO
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "GLO", "cost": 98.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "GLO", "cost": 490.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "GLO", "cost": 980.0},
        # 9MOBILE
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "9MOBILE", "cost": 99.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "9MOBILE", "cost": 495.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "9MOBILE", "cost": 995.0},
        # AIRTEL
        {"id": "A100", "label": "₦100", "amount": 100.0, "unit": "NGN", "network": "AIRTEL", "cost": 101.0},
        {"id": "A500", "label": "₦500", "amount": 500.0, "unit": "NGN", "network": "AIRTEL", "cost": 505.0},
        {"id": "A1000", "label": "₦1000", "amount": 1000.0, "unit": "NGN", "network": "AIRTEL", "cost": 1010.0},
    ],
    "data": [
        # MTN
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "MTN", "cost": 250.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "MTN", "cost": 450.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "MTN", "cost": 1000.0},
        # GLO
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "GLO", "cost": 245.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "GLO", "cost": 440.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "GLO", "cost": 980.0},
        # 9MOBILE
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "9MOBILE", "cost": 255.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "9MOBILE", "cost": 460.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "9MOBILE", "cost": 1020.0},
        # AIRTEL
        {"id": "D1", "label": "1GB", "amount": 1.0, "unit": "GB", "network": "AIRTEL", "cost": 252.0},
        {"id": "D2", "label": "2GB", "amount": 2.0, "unit": "GB", "network": "AIRTEL", "cost": 455.0},
        {"id": "D5", "label": "5GB", "amount": 5.0, "unit": "GB", "network": "AIRTEL", "cost": 1015.0},
    ],
}


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
    init_transaction_db()
    token = verify_token()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("buy", handle_message))
    app.add_handler(CommandHandler("buydata", handle_message))
    app.add_handler(CallbackQueryHandler(buy_menu_callback, pattern="^buy_menu$"))
    app.add_handler(CallbackQueryHandler(show_balance_callback, pattern="^show_balance$"))
    app.add_handler(CallbackQueryHandler(show_history_callback, pattern="^show_history$"))
    app.add_handler(CallbackQueryHandler(buy_type_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(buy_network_callback, pattern="^buy_network:"))
    app.add_handler(CallbackQueryHandler(buy_option_callback, pattern="^buy_option:"))
    app.add_handler(CallbackQueryHandler(confirm_purchase_callback, pattern="^confirm_purchase$"))
    app.add_handler(CallbackQueryHandler(cancel_purchase_callback, pattern="^cancel_purchase$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    bot = Bot(TOKEN)
    bot.delete_webhook(drop_pending_updates=True)

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
