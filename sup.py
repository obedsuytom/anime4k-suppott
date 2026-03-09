import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
import os

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# хранение связи сообщений
user_messages = {}

# старт
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("Добрый день! Опишите свою проблему.")

# сообщения пользователей
@dp.message_handler(lambda message: message.from_user.id != ADMIN_ID)
async def user_message(message: types.Message):

    await message.answer("Ожидайте нашего ответа.")

    forwarded = await bot.forward_message(
        ADMIN_ID,
        message.from_user.id,
        message.message_id
    )

    user_messages[forwarded.message_id] = message.from_user.id


# ответы админа
@dp.message_handler(lambda message: message.from_user.id == ADMIN_ID and message.reply_to_message)
async def admin_reply(message: types.Message):

    replied_id = message.reply_to_message.message_id

    if replied_id in user_messages:
        user_id = user_messages[replied_id]

        await bot.send_message(
            user_id,
            f"Ответ поддержки:\n\n{message.text}"
        )

if __name__ == "__main__":
    executor.start_polling(dp)