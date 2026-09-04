import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from vision import recognize_receipt

from database import (
    init_db,
    add_group,
    add_member,
    get_members,
    add_receipt,
    get_receipts,
    get_last_receipt,
    get_receipt_path,
    link_member_to_receipt,
    add_item,
    update_receipt_total,
    add_debt,
    get_debts,
    clear_debts,
    clear_members,
    get_my_receipts,
)



load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()


# =========================================================
# СОСТОЯНИЯ
# =========================================================

class ReceiptState(StatesGroup):
    choosing_members = State()
    waiting_confirmation = State()


# =========================================================
# ПАПКА ДЛЯ ЧЕКОВ
# =========================================================

RECEIPTS_DIR = Path("receipts")
RECEIPTS_DIR.mkdir(exist_ok=True)


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

def main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👥 Участники"),
                KeyboardButton(text="➕ Присоединиться"),
            ],
            [
                KeyboardButton(text="💸 Все долги"),
                KeyboardButton(text="🧾 Все чеки"),
                
            ],
            [
                KeyboardButton(text="📸 Загрузить чек"),
                KeyboardButton(text="🧾 Мои чеки"),
            ],
            [
                KeyboardButton(text="🧹 Очистить долги"),
                KeyboardButton(text="🗑 Очистить участников"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

    return keyboard


# =========================================================
# КОМАНДЫ TELEGRAM
# =========================================================

async def setup_bot_commands():
    commands = [
        BotCommand(
            command="start",
            description="🏠 Главное меню"
        ),
        BotCommand(
            command="join",
            description="➕ Присоединиться к группе"
        ),
        BotCommand(
            command="members",
            description="👥 Показать участников"
        ),
        BotCommand(
            command="receipts",
            description="🧾 Показать чеки"
        ),
        BotCommand(
            command="debts",
            description="💸 Показать долги"
        ),
        BotCommand(
            command="clear_debts",
            description="🧹 Очистить долги"
        ),
    ]

    await bot.set_my_commands(commands)


# =========================================================
# /start
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я бот для разделения расходов 💸\n\n"
        "Добавь меня в группу, присоединись к расчёту "
        "и отправляй фотографии чеков.\n\n"
        "Я помогу определить сумму покупки и "
        "посчитать, кто кому должен.\n\n"
        "👇 Используй кнопки ниже:",
        reply_markup=main_keyboard()
    )


# =========================================================
# /join
# =========================================================

@dp.message(Command("join"))
async def join_group(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать в Telegram-группе."
        )
        return

    await add_group(
        chat_id=message.chat.id,
        title=message.chat.title or "Без названия"
    )

    await add_member(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    await message.answer(
        f"✅ {message.from_user.first_name}, "
        "ты добавлен в группу расходов!"
    )


# =========================================================
# КНОПКА "ПРИСОЕДИНИТЬСЯ"
# =========================================================

@dp.message(lambda message: message.text == "➕ Присоединиться")
async def join_button(message: Message):
    await join_group(message)


# =========================================================
# /members
# =========================================================

@dp.message(Command("members"))
async def members(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать в группе."
        )
        return

    members_list = await get_members(message.chat.id)

    if not members_list:
        await message.answer(
            "👥 Пока никто не добавился.\n\n"
            "Нажми «➕ Присоединиться»."
        )
        return

    text = "👥 Участники группы:\n\n"

    for number, member in enumerate(members_list, start=1):
        user_id, username, first_name = member

        if username:
            name = f"{first_name} (@{username})"
        else:
            name = first_name

        text += f"{number}. {name}\n"

    await message.answer(text)


# =========================================================
# КНОПКА "УЧАСТНИКИ"
# =========================================================

@dp.message(lambda message: message.text == "👥 Участники")
async def members_button(message: Message):
    await members(message)


# =========================================================
# ПОЛУЧЕНИЕ ФОТО ЧЕКА
# =========================================================

@dp.message(lambda message: message.photo is not None)
async def receive_receipt(
    message: Message,
    state: FSMContext
):
    if message.chat.type == "private":
        await message.answer(
            "📸 Отправляй чек в группу."
        )
        return

    await add_group(
        message.chat.id,
        message.chat.title or ""
    )

    photo = message.photo[-1]

    file_name = (
        f"{message.chat.id}_"
        f"{message.from_user.id}_"
        f"{photo.file_unique_id}.jpg"
    )

    image_path = RECEIPTS_DIR / file_name

    await bot.download(
        photo,
        destination=image_path
    )

    await add_receipt(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        file_id=photo.file_id,
        image_path=str(image_path)
    )

    receipt = await get_last_receipt(
        message.chat.id
    )

    receipt_id = receipt[0]

    members = await get_members(
        message.chat.id
    )

    if not members:
        await message.answer(
            "❌ В группе пока нет участников.\n\n"
            "Сначала нажмите «➕ Присоединиться»."
        )
        return

    builder = InlineKeyboardBuilder()

    for user_id, username, first_name in members:
        builder.button(
            text=f"⬜ {first_name}",
            callback_data=f"m_{receipt_id}_{user_id}"
        )

    builder.button(
        text="✅ Готово",
        callback_data=f"done_{receipt_id}"
    )

    builder.adjust(2)

    await state.update_data(
        selected=[],
        receipt_id=receipt_id
    )

    await message.answer(
        "🧾 Чек сохранён!\n\n"
        "Выберите участников покупки:",
        reply_markup=builder.as_markup()
    )

    await state.set_state(
        ReceiptState.choosing_members
    )


# =========================================================
# КНОПКА "ЗАГРУЗИТЬ ЧЕК"
# =========================================================

@dp.message(lambda message: message.text == "📸 Загрузить чек")
async def upload_receipt_button(message: Message):
    await message.answer(
        "📸 Просто отправьте фотографию чека "
        "прямо в этот чат.\n\n"
        "После этого я распознаю его и попрошу "
        "выбрать участников покупки."
    )


# =========================================================
# ВЫБОР УЧАСТНИКОВ
# =========================================================

@dp.callback_query(
    ReceiptState.choosing_members,
    lambda c: c.data.startswith("m_")
)
async def toggle_member(
    callback: CallbackQuery,
    state: FSMContext
):
    _, receipt_id, user_id = callback.data.split("_")

    data = await state.get_data()

    selected = data.get("selected", [])

    uid = int(user_id)

    if uid in selected:
        selected.remove(uid)
    else:
        selected.append(uid)

    await state.update_data(
        selected=selected
    )

    members = await get_members(
        callback.message.chat.id
    )

    builder = InlineKeyboardBuilder()

    for member_id, username, first_name in members:
        mark = (
            "✅"
            if member_id in selected
            else "⬜"
        )

        builder.button(
            text=f"{mark} {first_name}",
            callback_data=f"m_{receipt_id}_{member_id}"
        )

    builder.button(
        text="✅ Готово",
        callback_data=f"done_{receipt_id}"
    )

    builder.adjust(2)

    await callback.message.edit_reply_markup(
        reply_markup=builder.as_markup()
    )

    await callback.answer()


# =========================================================
# ЗАВЕРШЕНИЕ ВЫБОРА УЧАСТНИКОВ
# =========================================================

@dp.callback_query(
    ReceiptState.choosing_members,
    lambda c: c.data.startswith("done_")
)
async def finish_receipt(
    callback: CallbackQuery,
    state: FSMContext
):
    receipt_id = int(
        callback.data.split("_")[1]
    )

    data = await state.get_data()

    selected = data.get("selected", [])

    if not selected:
        await callback.answer(
            "Выберите хотя бы одного человека",
            show_alert=True
        )
        return

    for uid in selected:
        await link_member_to_receipt(
            receipt_id,
            uid
        )

    await callback.message.edit_text(
        "🤖 Анализирую чек..."
    )

    path = await get_receipt_path(
        receipt_id
    )

    try:
        receipt_data = recognize_receipt(path)

    except Exception as e:
        print(
            f"❌ Ошибка распознавания чека: {e}"
        )

        await callback.message.edit_text(
            "❌ Не удалось распознать чек.\n\n"
            "Попробуйте отправить более чёткую "
            "фотографию чека."
        )

        await state.clear()
        await callback.answer()
        return

    store = receipt_data.get(
        "store",
        "Неизвестный магазин"
    )

    total = float(
        receipt_data.get("total", 0)
    )

    items = receipt_data.get(
        "items",
        []
    )

    if total <= 0:
        await callback.message.edit_text(
            "❌ Не удалось определить итоговую "
            "сумму чека.\n\n"
            "Попробуйте отправить более чёткую "
            "фотографию."
        )

        await state.clear()
        await callback.answer()
        return

    share = round(
        total / len(selected),
        2
    )

    items_text = ""

    for item in items[:10]:
        name = item.get(
            "name",
            "Неизвестный товар"
        )

        price = item.get(
            "price",
            0
        )

        items_text += (
            f"• {name} — "
            f"{price:.2f} ₽\n"
        )

    if len(items) > 10:
        items_text += "• ...\n"

    members = await get_members(
        callback.message.chat.id
    )

    names = []

    for uid, username, first_name in members:
        if uid in selected:
            names.append(first_name)

    names_text = ", ".join(names)

    await state.update_data(
        selected=selected,
        receipt_id=receipt_id,
        receipt_data=receipt_data,
        payer=callback.from_user.id
    )

    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Подтвердить",
        callback_data=f"confirm_{receipt_id}"
    )

    builder.button(
        text="❌ Отменить",
        callback_data=f"cancel_{receipt_id}"
    )

    builder.adjust(1)

    await callback.message.edit_text(
        f"🧾 Проверьте данные чека:\n\n"
        f"🏪 {store}\n\n"
        f"{items_text}\n"
        f"💰 Итого: {total:.2f} ₽\n\n"
        f"👥 Участники: {names_text}\n\n"
        f"💳 Доля каждого: {share:.2f} ₽\n\n"
        f"Если всё правильно — нажмите "
        f"«Подтвердить».",
        reply_markup=builder.as_markup()
    )

    await state.set_state(
        ReceiptState.waiting_confirmation
    )

    await callback.answer()


# =========================================================
# ПОДТВЕРЖДЕНИЕ ПОКУПКИ
# =========================================================

@dp.callback_query(
    ReceiptState.waiting_confirmation,
    lambda c: c.data.startswith("confirm_")
)
async def confirm_receipt(
    callback: CallbackQuery,
    state: FSMContext
):
    receipt_id = int(
        callback.data.split("_")[1]
    )

    data = await state.get_data()

    selected = data.get(
        "selected",
        []
    )

    receipt_data = data.get(
        "receipt_data"
    )

    payer = data.get(
        "payer"
    )

    if not selected or not receipt_data:
        await callback.message.edit_text(
            "❌ Данные чека потеряны.\n"
            "Отправьте чек ещё раз."
        )

        await state.clear()
        await callback.answer()
        return

    total = float(
        receipt_data["total"]
    )

    store = receipt_data.get(
        "store",
        "Неизвестный магазин"
    )

    items = receipt_data.get(
        "items",
        []
    )

    share = round(
        total / len(selected),
        2
    )

    for item in items:
        name = item.get(
            "name",
            "Неизвестный товар"
        )

        price = float(
            item.get("price", 0)
        )

        await add_item(
            receipt_id,
            name,
            price
        )

    await update_receipt_total(
        receipt_id,
        total,
        store
    )

    debts_created = 0

    for uid in selected:
        if uid == payer:
            continue

        await add_debt(
            chat_id=callback.message.chat.id,
            from_user=uid,
            to_user=payer,
            receipt_id=receipt_id,
            amount=share
        )

        debts_created += 1

    members = await get_members(
        callback.message.chat.id
    )

    names = {
        uid: first_name
        for uid, username, first_name in members
    }

    payer_name = names.get(
        payer,
        "Покупатель"
    )

    await callback.message.edit_text(
        f"✅ Покупка подтверждена!\n\n"
        f"🏪 {store}\n"
        f"💰 Сумма: {total:.2f} ₽\n"
        f"👥 Участников: {len(selected)}\n"
        f"💳 Доля: {share:.2f} ₽\n\n"
        f"💰 Заплатил: {payer_name}\n"
        f"💸 Создано долгов: {debts_created}"
    )

    await state.clear()

    await callback.answer(
        "Покупка подтверждена!"
    )


# =========================================================
# ОТМЕНА ПОКУПКИ
# =========================================================

@dp.callback_query(
    ReceiptState.waiting_confirmation,
    lambda c: c.data.startswith("cancel_")
)
async def cancel_receipt(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.message.edit_text(
        "❌ Покупка отменена.\n\n"
        "Долги не созданы."
    )

    await state.clear()

    await callback.answer(
        "Покупка отменена"
    )

@dp.callback_query(
    lambda c: c.data == "confirm_clear_members"
)
async def confirm_clear_members(
    callback: CallbackQuery
):
    await clear_members(
        callback.message.chat.id
    )

    await callback.message.edit_text(
        "🗑 Все участники группы очищены."
    )

    await callback.answer(
        "Участники удалены"
    )


@dp.callback_query(
    lambda c: c.data == "cancel_clear_members"
)
async def cancel_clear_members(
    callback: CallbackQuery
):
    await callback.message.edit_text(
        "❌ Очистка участников отменена."
    )

    await callback.answer(
        "Отменено"
    )

# =========================================================
# /receipts
# =========================================================

@dp.message(Command("receipts"))
async def receipts(message: Message):
    data = await get_receipts(
        message.chat.id
    )

    if not data:
        await message.answer(
            "Пока нет сохранённых чеков."
        )
        return

    text = "🧾 Последние чеки:\n\n"

    for i, (_, date) in enumerate(
        data,
        start=1
    ):
        text += f"{i}. {date}\n"

    await message.answer(text)

@dp.message(lambda message: message.text == "🧾 Все чеки")
async def all_receipts_button(message: Message):
    await receipts(message)


# =========================================================
# КНОПКА "МОИ ЧЕКИ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 Мои чеки")
async def my_receipts_button(message: Message):

    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    data = await get_my_receipts(
        message.chat.id,
        message.from_user.id
    )

    if not data:
        await message.answer(
            "🧾 Вы ещё не отправляли чеки."
        )
        return

    text = "🧾 Ваши последние чеки:\n\n"

    for i, (_, date) in enumerate(data, start=1):
        text += f"{i}. {date}\n"

    await message.answer(text)


# =========================================================
# /debts
# =========================================================

@dp.message(Command("debts"))
async def show_debts(message: Message):
    debts = await get_debts(
        message.chat.id
    )

    if not debts:
        await message.answer(
            "✅ Ни у кого нет долгов."
        )
        return

    members = await get_members(
        message.chat.id
    )

    names = {
        uid: name
        for uid, _, name in members
    }

    text = "💸 Текущие долги:\n\n"

    for from_id, to_id, amount in debts:
        from_name = names.get(
            from_id,
            "Неизвестный"
        )

        to_name = names.get(
            to_id,
            "Неизвестный"
        )

        text += (
            f"• {from_name} → "
            f"{to_name}: "
            f"{amount:.2f} ₽\n"
        )

    await message.answer(text)


# =========================================================
# КНОПКА "Все ДОЛГИ"
# =========================================================

@dp.message(lambda message: message.text == "💸 Все долги")
async def debts_button(message: Message):
    await show_debts(message)


# =========================================================
# /clear_debts
# =========================================================

@dp.message(Command("clear_debts"))
async def clear_all_debts(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту команду нужно использовать "
            "в группе."
        )
        return

    await clear_debts(
        message.chat.id
    )

    await message.answer(
        "🧹 Все долги этой группы очищены."
    )


# =========================================================
# КНОПКА "ОЧИСТИТЬ ДОЛГИ"
# =========================================================

@dp.message(lambda message: message.text == "🧹 Очистить долги")
async def clear_debts_button(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, очистить",
                    callback_data="confirm_clear_debts"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_clear_debts"
                )
            ]
        ]
    )

    await message.answer(
        "⚠️ Вы уверены, что хотите удалить ВСЕ долги в этой группе?\n\n"
        "Это действие нельзя отменить.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda callback: callback.data == "confirm_clear_debts")
async def confirm_clear_debts(callback: CallbackQuery):
    await clear_debts(callback.message.chat.id)

    await callback.message.edit_text(
        "🧹 Все долги успешно очищены."
    )

    await callback.answer("Долги очищены")


@dp.callback_query(lambda callback: callback.data == "cancel_clear_debts")
async def cancel_clear_debts(callback: CallbackQuery):
    await callback.message.edit_text(
        "❌ Очистка долгов отменена."
    )

    await callback.answer("Отменено")

# =========================================================
# КНОПКА "ОЧИСТИТЬ УЧАСТНИКОВ"
# =========================================================

@dp.message(lambda message: message.text == "🗑 Очистить участников")
async def clear_members_button(
    message: Message
):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🗑 Да, очистить",
        callback_data="confirm_clear_members"
    )

    builder.button(
        text="❌ Отмена",
        callback_data="cancel_clear_members"
    )

    builder.adjust(1)

    await message.answer(
        "⚠️ <b>Очистить всех участников?</b>\n\n"
        "Все участники этой группы будут удалены "
        "из списка.\n\n"
        "Чеки и долги при этом не удаляются.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    await init_db()

    await setup_bot_commands()

    print("✅ База данных готова")
    print("📋 Меню команд настроено")
    print("🤖 Бот запущен...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

