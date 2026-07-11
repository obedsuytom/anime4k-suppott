import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================
# Настройки
# =========================
API_TOKEN = os.getenv("BOT_TOKEN")

# Можно указать одного админа:
# ADMIN_ID=123456789
# или несколько:
# SUPPORT_ADMIN_IDS=123456789,987654321
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
SUPPORT_ADMIN_IDS_RAW = os.getenv("SUPPORT_ADMIN_IDS", "").strip()

if SUPPORT_ADMIN_IDS_RAW:
    ADMIN_IDS = [int(x.strip()) for x in SUPPORT_ADMIN_IDS_RAW.split(",") if x.strip()]
elif ADMIN_ID_RAW:
    ADMIN_IDS = [int(ADMIN_ID_RAW)]
else:
    raise RuntimeError("Не задан ADMIN_ID или SUPPORT_ADMIN_IDS")

DB_PATH = os.getenv("SUPPORT_DB", "support.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# =========================
# База данных
# =========================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    from_role TEXT NOT NULL,
    from_user_id INTEGER NOT NULL,
    content_type TEXT,
    text TEXT,
    caption TEXT,
    telegram_message_id INTEGER,
    created_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admin_message_links (
    admin_chat_id INTEGER NOT NULL,
    admin_message_id INTEGER NOT NULL,
    ticket_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (admin_chat_id, admin_message_id)
)
""")

cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets(user_id, status)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id)")
conn.commit()

# =========================
# Вспомогательные функции
# =========================

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def user_link(user_id: int, first_name: str = None) -> str:
    safe_name = first_name or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def get_ticket(ticket_id: int):
    cursor.execute(
        "SELECT id, user_id, username, first_name, status, created_at, updated_at, closed_at FROM tickets WHERE id=?",
        (ticket_id,)
    )
    return cursor.fetchone()


def get_open_ticket(user_id: int):
    cursor.execute(
        """
        SELECT id, user_id, username, first_name, status, created_at, updated_at, closed_at
        FROM tickets
        WHERE user_id=? AND status='open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,)
    )
    return cursor.fetchone()


def create_ticket(message: types.Message):
    created_at = now_iso()
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    cursor.execute(
        """
        INSERT INTO tickets (user_id, username, first_name, status, created_at, updated_at)
        VALUES (?, ?, ?, 'open', ?, ?)
        """,
        (message.from_user.id, username, first_name, created_at, created_at)
    )
    conn.commit()
    ticket_id = cursor.lastrowid
    return get_ticket(ticket_id)


def touch_ticket(ticket_id: int):
    cursor.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now_iso(), ticket_id))
    conn.commit()


def close_ticket(ticket_id: int):
    cursor.execute(
        "UPDATE tickets SET status='closed', closed_at=?, updated_at=? WHERE id=?",
        (now_iso(), now_iso(), ticket_id)
    )
    conn.commit()


def reopen_ticket(ticket_id: int):
    cursor.execute(
        "UPDATE tickets SET status='open', closed_at=NULL, updated_at=? WHERE id=?",
        (now_iso(), ticket_id)
    )
    conn.commit()


def log_ticket_message(ticket_id: int, from_role: str, from_user_id: int, message: types.Message):
    cursor.execute(
        """
        INSERT INTO ticket_messages
        (ticket_id, from_role, from_user_id, content_type, text, caption, telegram_message_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            from_role,
            from_user_id,
            message.content_type,
            message.text or "",
            message.caption or "",
            message.message_id,
            now_iso()
        )
    )
    conn.commit()


def register_admin_link(admin_chat_id: int, admin_message_id: int, ticket_id: int, user_id: int):
    cursor.execute(
        """
        INSERT OR REPLACE INTO admin_message_links
        (admin_chat_id, admin_message_id, ticket_id, user_id)
        VALUES (?, ?, ?, ?)
        """,
        (admin_chat_id, admin_message_id, ticket_id, user_id)
    )
    conn.commit()


def get_link_by_admin_message(admin_chat_id: int, admin_message_id: int):
    cursor.execute(
        """
        SELECT ticket_id, user_id
        FROM admin_message_links
        WHERE admin_chat_id=? AND admin_message_id=?
        """,
        (admin_chat_id, admin_message_id)
    )
    return cursor.fetchone()


def ticket_status_text(status: str) -> str:
    if status == "open":
        return "🟢 открыт"
    if status == "closed":
        return "🔴 закрыт"
    return status


def admin_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("📩 Ответить", callback_data=f"support_reply_hint|{ticket_id}"),
            InlineKeyboardButton("✅ Закрыть", callback_data=f"support_close|{ticket_id}")
        ],
        [InlineKeyboardButton("📋 Инфо тикета", callback_data=f"support_info|{ticket_id}")]
    ])


def user_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Закрыть тикет", callback_data=f"user_close_ticket|{ticket_id}")],
        [InlineKeyboardButton("📋 Мои тикеты", callback_data="my_tickets")]
    ])


def main_user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📝 Создать тикет", callback_data="new_ticket")],
        [InlineKeyboardButton("📋 Мои тикеты", callback_data="my_tickets")]
    ])


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🟢 Открытые тикеты", callback_data="admin_open_tickets")],
        [InlineKeyboardButton("📋 Все тикеты", callback_data="admin_all_tickets")]
    ])


def format_ticket_header(ticket) -> str:
    ticket_id, user_id, username, first_name, status, created_at, updated_at, closed_at = ticket
    username_text = f"@{username}" if username else "—"
    return (
        f"📩 <b>Тикет #{ticket_id}</b>\n"
        f"Статус: <b>{ticket_status_text(status)}</b>\n"
        f"Пользователь: {user_link(user_id, first_name)}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: {username_text}\n"
        f"Создан: <code>{created_at}</code>\n"
        f"Обновлён: <code>{updated_at}</code>\n\n"
        f"Чтобы ответить пользователю — <b>ответьте реплаем</b> на это сообщение или на сообщение пользователя.\n"
        f"Также можно: <code>/reply {ticket_id} текст ответа</code>"
    )


def format_ticket_short(ticket) -> str:
    ticket_id, user_id, username, first_name, status, created_at, updated_at, closed_at = ticket
    return (
        f"#{ticket_id} — {ticket_status_text(status)}\n"
        f"Создан: {created_at}\n"
        f"Обновлён: {updated_at}"
    )


async def notify_admins_about_user_message(ticket, message: types.Message):
    ticket_id, user_id, username, first_name, status, created_at, updated_at, closed_at = ticket

    for admin_id in ADMIN_IDS:
        try:
            header = await bot.send_message(
                admin_id,
                format_ticket_header(ticket),
                reply_markup=admin_ticket_kb(ticket_id),
                disable_web_page_preview=True
            )
            register_admin_link(admin_id, header.message_id, ticket_id, user_id)

            copied = await bot.copy_message(
                chat_id=admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            # copy_message в aiogram v2 возвращает MessageId с полем message_id
            copied_message_id = getattr(copied, "message_id", None)
            if copied_message_id:
                register_admin_link(admin_id, copied_message_id, ticket_id, user_id)
        except Exception as e:
            logging.exception("Ошибка отправки тикета админу %s: %s", admin_id, e)


async def send_admin_answer_to_user(ticket_id: int, user_id: int, admin_message: types.Message):
    ticket = get_ticket(ticket_id)
    if not ticket:
        await admin_message.answer("❌ Тикет не найден.")
        return

    if ticket[4] == "closed":
        await admin_message.answer("❌ Тикет закрыт. Сначала откройте его командой /open_ticket ID.")
        return

    if admin_message.content_type == types.ContentType.TEXT:
        await bot.send_message(
            user_id,
            f"💬 <b>Ответ поддержки по тикету #{ticket_id}:</b>\n\n{admin_message.html_text}",
            reply_markup=user_ticket_kb(ticket_id),
            disable_web_page_preview=True
        )
    else:
        await bot.send_message(
            user_id,
            f"💬 <b>Ответ поддержки по тикету #{ticket_id}:</b>",
            reply_markup=user_ticket_kb(ticket_id)
        )
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=admin_message.chat.id,
            message_id=admin_message.message_id
        )

    log_ticket_message(ticket_id, "admin", admin_message.from_user.id, admin_message)
    touch_ticket(ticket_id)
    await admin_message.answer(f"✅ Ответ отправлен пользователю по тикету #{ticket_id}")


# =========================
# Команды пользователя
# =========================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    if is_admin(message.from_user.id):
        await message.answer(
            "👨‍💻 <b>Панель поддержки</b>\n\n"
            "Команды:\n"
            "/tickets — открытые тикеты\n"
            "/all_tickets — все тикеты\n"
            "/ticket ID — информация о тикете\n"
            "/reply ID текст — ответить пользователю\n"
            "/close_ticket ID — закрыть тикет\n"
            "/open_ticket ID — открыть тикет\n\n"
            "Также можно просто ответить реплаем на сообщение пользователя.",
            reply_markup=admin_main_kb()
        )
        return

    await message.answer(
        "👋 Добрый день!\n\n"
        "Это бот поддержки. Нажмите <b>Создать тикет</b> или просто отправьте сообщение с описанием проблемы.\n\n"
        "Можно отправлять текст, фото, видео, документы, голосовые и другие файлы.",
        reply_markup=main_user_kb()
    )


@dp.message_handler(commands=["new", "newticket"])
async def new_ticket_cmd(message: types.Message):
    if is_admin(message.from_user.id):
        return

    open_ticket = get_open_ticket(message.from_user.id)
    if open_ticket:
        await message.answer(
            f"У вас уже есть открытый тикет #{open_ticket[0]}.\n"
            "Отправьте сообщение сюда — оно будет добавлено в этот тикет.",
            reply_markup=user_ticket_kb(open_ticket[0])
        )
        return

    ticket = create_ticket(message)
    await message.answer(
        f"✅ Тикет #{ticket[0]} создан.\n\n"
        "Теперь отправьте описание проблемы. Можно прикрепить фото, видео или файл.",
        reply_markup=user_ticket_kb(ticket[0])
    )


@dp.message_handler(commands=["mytickets", "my_tickets"])
async def my_tickets_cmd(message: types.Message):
    if is_admin(message.from_user.id):
        return
    await show_user_tickets(message.chat.id, message.from_user.id)


async def show_user_tickets(chat_id: int, user_id: int):
    cursor.execute(
        """
        SELECT id, user_id, username, first_name, status, created_at, updated_at, closed_at
        FROM tickets
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await bot.send_message(chat_id, "У вас пока нет тикетов.", reply_markup=main_user_kb())
        return

    buttons = []
    text = "📋 <b>Ваши тикеты:</b>\n\n"
    for ticket in rows:
        text += format_ticket_short(ticket) + "\n\n"
        buttons.append([InlineKeyboardButton(f"Тикет #{ticket[0]} — {ticket_status_text(ticket[4])}", callback_data=f"user_ticket_info|{ticket[0]}")])

    buttons.append([InlineKeyboardButton("📝 Новый тикет", callback_data="new_ticket")])
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# =========================
# Команды админа
# =========================
@dp.message_handler(commands=["tickets"])
async def admin_open_tickets_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await show_admin_tickets(message.chat.id, only_open=True)


@dp.message_handler(commands=["all_tickets"])
async def admin_all_tickets_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await show_admin_tickets(message.chat.id, only_open=False)


async def show_admin_tickets(chat_id: int, only_open: bool = True):
    if only_open:
        cursor.execute(
            """
            SELECT id, user_id, username, first_name, status, created_at, updated_at, closed_at
            FROM tickets
            WHERE status='open'
            ORDER BY updated_at DESC
            LIMIT 20
            """
        )
        title = "🟢 <b>Открытые тикеты:</b>"
    else:
        cursor.execute(
            """
            SELECT id, user_id, username, first_name, status, created_at, updated_at, closed_at
            FROM tickets
            ORDER BY updated_at DESC
            LIMIT 20
            """
        )
        title = "📋 <b>Все тикеты:</b>"

    rows = cursor.fetchall()
    if not rows:
        await bot.send_message(chat_id, "Тикетов нет.", reply_markup=admin_main_kb())
        return

    text = title + "\n\n"
    buttons = []

    for ticket in rows:
        ticket_id, user_id, username, first_name, status, created_at, updated_at, closed_at = ticket
        text += f"#{ticket_id} — {ticket_status_text(status)} — {first_name or user_id}\n"
        buttons.append([InlineKeyboardButton(f"Открыть #{ticket_id}", callback_data=f"support_info|{ticket_id}")])

    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message_handler(commands=["ticket_info", "ticket"])
async def admin_ticket_info_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.get_args().strip()
    if not args or not args.isdigit():
        await message.answer("Использование: <code>/ticket 123</code>")
        return

    await send_ticket_info(message.chat.id, int(args))


async def send_ticket_info(chat_id: int, ticket_id: int):
    ticket = get_ticket(ticket_id)
    if not ticket:
        await bot.send_message(chat_id, "❌ Тикет не найден.")
        return

    cursor.execute(
        """
        SELECT from_role, content_type, text, caption, created_at
        FROM ticket_messages
        WHERE ticket_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (ticket_id,)
    )
    messages = cursor.fetchall()

    text = format_ticket_header(ticket) + "\n\n<b>Последние сообщения:</b>\n"
    if not messages:
        text += "— сообщений пока нет —"
    else:
        for from_role, content_type, text_msg, caption, created_at in reversed(messages):
            role = "👤 пользователь" if from_role == "user" else "👨‍💻 поддержка"
            body = text_msg or caption or f"[{content_type}]"
            if len(body) > 120:
                body = body[:117] + "..."
            text += f"\n{created_at} — {role}: {body}"

    sent = await bot.send_message(chat_id, text, reply_markup=admin_ticket_kb(ticket_id), disable_web_page_preview=True)
    register_admin_link(chat_id, sent.message_id, ticket_id, ticket[1])


@dp.message_handler(commands=["reply"])
async def admin_reply_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.get_args()
    parts = args.split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Использование: <code>/reply 123 текст ответа</code>")
        return

    ticket_id = int(parts[0])
    answer_text = parts[1]
    ticket = get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Тикет не найден.")
        return

    if ticket[4] == "closed":
        await message.answer("❌ Тикет закрыт. Сначала откройте его командой /open_ticket ID.")
        return

    user_id = ticket[1]
    await bot.send_message(
        user_id,
        f"💬 <b>Ответ поддержки по тикету #{ticket_id}:</b>\n\n{answer_text}",
        reply_markup=user_ticket_kb(ticket_id),
        disable_web_page_preview=True
    )
    log_ticket_message(ticket_id, "admin", message.from_user.id, message)
    touch_ticket(ticket_id)
    await message.answer(f"✅ Ответ отправлен пользователю по тикету #{ticket_id}")


@dp.message_handler(commands=["close_ticket"])
async def admin_close_ticket_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.get_args().strip()
    if not args.isdigit():
        await message.answer("Использование: <code>/close_ticket 123</code>")
        return

    ticket_id = int(args)
    ticket = get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Тикет не найден.")
        return

    close_ticket(ticket_id)
    await message.answer(f"✅ Тикет #{ticket_id} закрыт.")
    try:
        await bot.send_message(ticket[1], f"✅ Ваш тикет #{ticket_id} закрыт.", reply_markup=main_user_kb())
    except Exception:
        pass


@dp.message_handler(commands=["open_ticket"])
async def admin_open_ticket_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.get_args().strip()
    if not args.isdigit():
        await message.answer("Использование: <code>/open_ticket 123</code>")
        return

    ticket_id = int(args)
    ticket = get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Тикет не найден.")
        return

    reopen_ticket(ticket_id)
    await message.answer(f"✅ Тикет #{ticket_id} открыт.")
    try:
        await bot.send_message(ticket[1], f"🟢 Ваш тикет #{ticket_id} снова открыт.", reply_markup=user_ticket_kb(ticket_id))
    except Exception:
        pass


# =========================
# Callback-кнопки
# =========================
@dp.callback_query_handler(lambda call: call.data == "new_ticket")
async def new_ticket_callback(call: types.CallbackQuery):
    if is_admin(call.from_user.id):
        await call.answer()
        return

    open_ticket = get_open_ticket(call.from_user.id)
    if open_ticket:
        await call.message.answer(
            f"У вас уже есть открытый тикет #{open_ticket[0]}.\n"
            "Отправьте сообщение сюда — оно будет добавлено в этот тикет.",
            reply_markup=user_ticket_kb(open_ticket[0])
        )
    else:
        # создаём искусственный тикет по callback-сообщению
        class DummyMessage:
            from_user = call.from_user
        ticket = create_ticket(DummyMessage())
        await call.message.answer(
            f"✅ Тикет #{ticket[0]} создан.\n\n"
            "Теперь отправьте описание проблемы. Можно прикрепить фото, видео или файл.",
            reply_markup=user_ticket_kb(ticket[0])
        )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == "my_tickets")
async def my_tickets_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await show_user_tickets(call.message.chat.id, call.from_user.id)
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("user_ticket_info|"))
async def user_ticket_info_callback(call: types.CallbackQuery):
    ticket_id = int(call.data.split("|", 1)[1])
    ticket = get_ticket(ticket_id)

    if not ticket or ticket[1] != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return

    await call.message.answer(format_ticket_short(ticket), reply_markup=user_ticket_kb(ticket_id))
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("user_close_ticket|"))
async def user_close_ticket_callback(call: types.CallbackQuery):
    ticket_id = int(call.data.split("|", 1)[1])
    ticket = get_ticket(ticket_id)

    if not ticket or ticket[1] != call.from_user.id:
        await call.answer("Тикет не найден", show_alert=True)
        return

    close_ticket(ticket_id)
    await call.message.answer(f"✅ Тикет #{ticket_id} закрыт.", reply_markup=main_user_kb())

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"✅ Пользователь закрыл тикет #{ticket_id}.")
        except Exception:
            pass

    await call.answer("Тикет закрыт")


@dp.callback_query_handler(lambda call: call.data == "admin_open_tickets")
async def admin_open_tickets_callback(call: types.CallbackQuery):
    if is_admin(call.from_user.id):
        await show_admin_tickets(call.message.chat.id, only_open=True)
    await call.answer()


@dp.callback_query_handler(lambda call: call.data == "admin_all_tickets")
async def admin_all_tickets_callback(call: types.CallbackQuery):
    if is_admin(call.from_user.id):
        await show_admin_tickets(call.message.chat.id, only_open=False)
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("support_info|"))
async def support_info_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ticket_id = int(call.data.split("|", 1)[1])
    await send_ticket_info(call.message.chat.id, ticket_id)
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("support_reply_hint|"))
async def support_reply_hint_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ticket_id = int(call.data.split("|", 1)[1])
    await call.message.answer(
        f"Чтобы ответить по тикету #{ticket_id}:\n\n"
        f"1. Ответьте реплаем на сообщение пользователя.\n"
        f"2. Или используйте команду:\n<code>/reply {ticket_id} текст ответа</code>\n\n"
        f"Можно отправлять текст, фото, видео, документы и другие файлы."
    )
    await call.answer()


@dp.callback_query_handler(lambda call: call.data.startswith("support_close|"))
async def support_close_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ticket_id = int(call.data.split("|", 1)[1])
    ticket = get_ticket(ticket_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return

    close_ticket(ticket_id)
    await call.message.answer(f"✅ Тикет #{ticket_id} закрыт.")
    try:
        await bot.send_message(ticket[1], f"✅ Ваш тикет #{ticket_id} закрыт.", reply_markup=main_user_kb())
    except Exception:
        pass
    await call.answer("Тикет закрыт")


# =========================
# Ответы админа реплаем
# =========================
@dp.message_handler(lambda message: is_admin(message.from_user.id) and message.reply_to_message, content_types=types.ContentTypes.ANY)
async def admin_reply_by_reply(message: types.Message):
    link = get_link_by_admin_message(message.chat.id, message.reply_to_message.message_id)
    if not link:
        return

    ticket_id, user_id = link
    await send_admin_answer_to_user(ticket_id, user_id, message)


# =========================
# Сообщения пользователей
# =========================
@dp.message_handler(lambda message: not is_admin(message.from_user.id), content_types=types.ContentTypes.ANY)
async def user_message(message: types.Message):
    ticket = get_open_ticket(message.from_user.id)

    if not ticket:
        ticket = create_ticket(message)
        await message.answer(
            f"✅ Создан тикет #{ticket[0]}.\n"
            "Ваше сообщение отправлено в поддержку.",
            reply_markup=user_ticket_kb(ticket[0])
        )
    else:
        await message.answer(
            f"✅ Сообщение добавлено в тикет #{ticket[0]}.\n"
            "Ожидайте ответа поддержки.",
            reply_markup=user_ticket_kb(ticket[0])
        )

    log_ticket_message(ticket[0], "user", message.from_user.id, message)
    touch_ticket(ticket[0])
    ticket = get_ticket(ticket[0])
    await notify_admins_about_user_message(ticket, message)


# =========================
# Прочие сообщения админов
# =========================
@dp.message_handler(lambda message: is_admin(message.from_user.id), content_types=types.ContentTypes.ANY)
async def admin_other_message(message: types.Message):
    await message.answer(
        "Сообщение не привязано к тикету.\n\n"
        "Ответьте реплаем на сообщение пользователя или используйте:\n"
        "<code>/reply ID текст</code>"
    )


if __name__ == "__main__":
    logging.info("Support bot started. Admins: %s", ADMIN_IDS)
    executor.start_polling(dp, skip_updates=True)
