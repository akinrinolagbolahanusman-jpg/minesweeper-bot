import os
import random
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ------------------- CONFIG -------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8949835889:AAHAVPxOTMmTtx6WoVfbKtpHzbbTr1D8png")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # set in Render env
FREE_DAILY_LIMIT = 20
UPGRADE_COST = 20
UPGRADE_DAILY_LIMIT = 100
UPGRADE_DURATION_DAYS = 30
GRID_SIZE = 5
MINES = 5

# ------------------- DATABASE -------------------
conn = sqlite3.connect("bot.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    stars INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 20,
    daily_used INTEGER DEFAULT 0,
    upgrade_expiry TEXT
)""")
conn.commit()

def get_user(user_id):
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return get_user(user_id)
    return row

def update_user(user_id, stars=None, daily_limit=None, daily_used=None, upgrade_expiry=None):
    user = get_user(user_id)
    new_stars = stars if stars is not None else user[1]
    new_limit = daily_limit if daily_limit is not None else user[2]
    new_used = daily_used if daily_used is not None else user[3]
    new_expiry = upgrade_expiry if upgrade_expiry is not None else user[4]
    c.execute("UPDATE users SET stars=?, daily_limit=?, daily_used=?, upgrade_expiry=? WHERE user_id=?",
              (new_stars, new_limit, new_used, new_expiry, user_id))
    conn.commit()

def reset_daily_if_needed(user_id):
    user = get_user(user_id)
    # We'll reset daily_used if the date changed. We store last_reset as part of daily_used? Actually we track daily_used only.
    # Simpler: we store a last_reset date in the DB (extra column). I'll add it now.
    # Let's patch: add column last_reset TEXT
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_reset TEXT")
        conn.commit()
    except:
        pass
    user = get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if user[5] != today:  # index 5 is last_reset (since we added it after upgrade_expiry)
        c.execute("UPDATE users SET daily_used=0, last_reset=? WHERE user_id=?", (today, user_id))
        conn.commit()
        # If upgrade expired, revert to free limit
        if user[4] and datetime.fromisoformat(user[4]) < datetime.now():
            update_user(user_id, daily_limit=FREE_DAILY_LIMIT, upgrade_expiry=None)

# Actually the last_reset column is after upgrade_expiry, but get_user returns tuple with old order. Easier: redefine get_user.
# Let's rewrite get_user properly.
def get_user(user_id):
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (user_id, last_reset) VALUES (?, ?)", (user_id, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        return get_user(user_id)
    return row

# Fix columns: we need to ensure last_reset exists.
# Let's just create the table with last_reset now.
c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    stars INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 20,
    daily_used INTEGER DEFAULT 0,
    upgrade_expiry TEXT,
    last_reset TEXT
)""")
conn.commit()

def reset_daily_if_needed(user_id):
    user = get_user(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if user[5] != today:  # index 5 = last_reset
        c.execute("UPDATE users SET daily_used=0, last_reset=? WHERE user_id=?", (today, user_id))
        conn.commit()
        # If upgrade expired, revert to free limit
        if user[4] and datetime.fromisoformat(user[4]) < datetime.now():
            update_user(user_id, daily_limit=FREE_DAILY_LIMIT, upgrade_expiry=None)

# ------------------- GAME STATE -------------------
games = {}  # user_id -> game dict

def new_game():
    grid = [[0]*GRID_SIZE for _ in range(GRID_SIZE)]
    mines = set()
    while len(mines) < MINES:
        x = random.randint(0, GRID_SIZE-1)
        y = random.randint(0, GRID_SIZE-1)
        if (x,y) not in mines:
            mines.add((x,y))
    for (x,y) in mines:
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                nx, ny = x+dx, y+dy
                if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and (nx,ny) not in mines:
                    grid[nx][ny] += 1
    return {"grid": grid, "mines": mines, "revealed": set(), "game_over": False, "won": False}

def display_board(game):
    # Create a keyboard layout 5x5
    buttons = []
    for x in range(GRID_SIZE):
        row = []
        for y in range(GRID_SIZE):
            if (x,y) in game["revealed"]:
                val = game["grid"][x][y]
                if val == 0:
                    text = "·"
                else:
                    text = str(val)
            else:
                text = "?"
            row.append(InlineKeyboardButton(text, callback_data=f"{x},{y}"))
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

# ------------------- HELPERS -------------------
def check_win(game):
    return len(game["revealed"]) == GRID_SIZE*GRID_SIZE - MINES

def reveal_cell(user_id, game, x, y):
    if game["game_over"] or game["won"]:
        return game
    if (x,y) in game["mines"]:
        game["game_over"] = True
        # reveal all mines
        for mine in game["mines"]:
            game["revealed"].add(mine)
        return game
    if (x,y) in game["revealed"]:
        return game
    # BFS reveal
    stack = [(x,y)]
    while stack:
        cx, cy = stack.pop()
        if (cx,cy) in game["revealed"]:
            continue
        game["revealed"].add((cx,cy))
        if game["grid"][cx][cy] == 0:
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    nx, ny = cx+dx, cy+dy
                    if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and (nx,ny) not in game["revealed"]:
                        stack.append((nx,ny))
    if check_win(game):
        game["won"] = True
    return game

# ------------------- BOT COMMANDS -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_daily_if_needed(user_id)
    user = get_user(user_id)
    await update.message.reply_text(
        "👋 Hey! I'm your **Minesweeper Bot**! 💣🚩\n\n"
        "Type /play to start a game (5x5 grid, 5 mines).\n"
        "You have **{}** plays left today.\n"
        "Win a game to earn ⭐ stars!\n"
        "Spend **20** stars to get **100 daily plays** for a month!\n\n"
        "Use /stars to check your balance.".format(user[3] if user[3] < user[2] else "free")
    , parse_mode="Markdown")

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if user[3] >= user[2]:
        await update.message.reply_text("🎮 You're out of plays today! Come back tomorrow, or upgrade with /buy (20⭐).")
        return
    # Increase used count
    update_user(user_id, daily_used=user[3] + 1)
    game = new_game()
    games[user_id] = game
    await update.message.reply_text("💣 New game started! Tap a cell to reveal.\nGood luck!", reply_markup=display_board(game))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    if user_id not in games:
        await query.answer("Game not found. Start a new one with /play")
        return
    game = games[user_id]
    x, y = map(int, query.data.split(","))
    game = reveal_cell(user_id, game, x, y)
    games[user_id] = game

    if game["game_over"]:
        await query.edit_message_text("💥 **BOOM!** You hit a mine! Game over.\nThe mines were at: \n" +
                                      "\n".join([f"({mx},{my})" for mx,my in game["mines"]]),
                                      reply_markup=None, parse_mode="Markdown")
        del games[user_id]
    elif game["won"]:
        # award star
        user = get_user(user_id)
        update_user(user_id, stars=user[1] + 1)
        await query.edit_message_text("🎉 **You won!** Cleared the field!\nYou earned 1 ⭐ star!\nUse /stars to check.",
                                      reply_markup=None, parse_mode="Markdown")
        del games[user_id]
    else:
        await query.edit_message_text("Tap a cell…", reply_markup=display_board(game))

async def stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_daily_if_needed(user_id)
    user = get_user(user_id)
    await update.message.reply_text(
        f"⭐ **Your stars:** {user[1]}\n"
        f"🎮 **Daily plays left:** {user[2] - user[3]} / {user[2]}\n"
        f"📅 Upgrade active: {user[4] if user[4] else 'No'}"
    , parse_mode="Markdown")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if user[1] < UPGRADE_COST:
        await update.message.reply_text(f"❌ You need **{UPGRADE_COST}** stars to upgrade. You have **{user[1]}**. Keep playing!")
        return
    # Apply upgrade
    expiry = (datetime.now() + timedelta(days=UPGRADE_DURATION_DAYS)).isoformat()
    update_user(user_id, stars=user[1] - UPGRADE_COST, daily_limit=UPGRADE_DAILY_LIMIT, upgrade_expiry=expiry)
    await update.message.reply_text("🎉 **Upgraded!** You now have 100 daily plays for a month. Enjoy!")

# Secret command (no slash) - just detect when a user sends the magic word
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text and text.strip().lower() == "grantpass":
        user_id = update.effective_user.id
        reset_daily_if_needed(user_id)
        expiry = (datetime.now() + timedelta(days=UPGRADE_DURATION_DAYS)).isoformat()
        update_user(user_id, daily_limit=UPGRADE_DAILY_LIMIT, upgrade_expiry=expiry)
        await update.message.reply_text("🤫 *whispers* ... Granted. You now have 100 daily plays for a month. Don't tell anyone.")
    else:
        # Ignore other messages
        pass

# ------------------- MAIN -------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("play", play))
    application.add_handler(CommandHandler("stars", stars))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("help", start))  # reuse start

    # Handle grantpass (message without slash)
    application.add_handler(CommandHandler("grantpass", lambda u,c: None))  # avoid treating as command
    # Instead, we use a MessageHandler for non-command messages
    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set webhook if WEBHOOK_URL is provided
    if WEBHOOK_URL:
        application.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)),
                                url_path=BOT_TOKEN,
                                webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        application.run_polling()

if __name__ == "__main__":
    main()
