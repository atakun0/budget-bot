import asyncio
import os
from io import BytesIO  # [NEW] картинка диаграммы в памяти, без сохранения на диск
from pathlib import Path

import matplotlib  # [NEW] библиотека для круговой диаграммы

matplotlib.use("Agg")  # [NEW] режим без окна — бот рисует график в фоне
import matplotlib.pyplot as plt  # [NEW] построение pie-chart
from matplotlib import font_manager  # [NEW] шрифт с кириллицей для подписей

from aiogram import Bot, Dispatcher, F  # [CHG] F — фильтр «только фото» для загрузки чека
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile,  # [NEW] отправка PNG диаграммы в Telegram
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
    get_receipt_items,
    get_receipt_participants,
    set_receipt_payer,
    get_category_totals_last_week,  # [CHG] суммы по категориям за последнюю неделю
    clear_expenses,  # [NEW] удаление чеков, товаров и связанных долгов группы
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
    waiting_receipt_photo = State()  # [NEW] ждём фото только после кнопки «Загрузить чек»
    choosing_members = State()
    receipt_category = State()  # [NEW] выбор категории после распознавания чека
    receipt_category_custom = State()  # [NEW] своя категория для чека
    waiting_confirmation = State()
    manual_amount = State()
    manual_category = State()
    manual_category_custom = State()
    manual_payer = State()
    manual_participants = State()
    manual_confirmation = State()


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
                KeyboardButton(text="✍️ Добавить покупку"),
            ],
            [
                KeyboardButton(text="🧾 Мои чеки"),
                KeyboardButton(text="📊 Категории"),  # [NEW] кнопка круговой диаграммы расходов
            ],
            [
                KeyboardButton(text="🧹 Очистить долги"),
                KeyboardButton(text="🗑 Очистить участников"),
            ],
            [
                KeyboardButton(text="🧾 Очистить список трат"),  # [NEW] широкая кнопка: одна на весь нижний ряд
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
        BotCommand(  # [NEW] команда меню Telegram для диаграммы категорий
            command="categories",
            description="📊 Расходы по категориям"
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
# РУЧНОЕ ДОБАВЛЕНИЕ ПОКУПКИ (F2)
# =========================================================

MANUAL_CATEGORIES = [
    "Продукты",
    "Коммуналка",
    "Транспорт",
    "Развлечения",
    "Прочее",
    "✏️ Своя категория",
]


def purchase_categories_keyboard(callback_prefix: str):  # [NEW] один список категорий для ручного ввода и для чека
    builder = InlineKeyboardBuilder()  # [NEW]
    for category in MANUAL_CATEGORIES:  # [NEW]
        builder.button(text=category, callback_data=f"{callback_prefix}:{category}")  # [NEW]
    builder.adjust(2)  # [NEW]
    return builder.as_markup()  # [NEW]


@dp.message(lambda message: message.text == "✍️ Добавить покупку")
async def manual_purchase_start(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await message.answer("❌ Ручную покупку нужно добавлять в группе.")
        return

    members = await get_members(message.chat.id)
    if not members:
        await message.answer(
            "❌ В группе пока нет участников.\n\n"
            "Сначала нажмите «➕ Присоединиться»."
        )
        return

    await state.clear()
    await state.set_state(ReceiptState.manual_amount)
    await message.answer(
        "✍️ <b>Добавление покупки вручную</b>\n\n"
        "Введите сумму покупки в рублях.\n"
        "Например: <code>1250.50</code> или <code>1250,50</code>",
        parse_mode="HTML"
    )


@dp.message(ReceiptState.manual_amount)
async def manual_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".").replace(" ", "")
    try:
        amount = float(text)
    except ValueError:
        await message.answer("❌ Не понял сумму. Введите число, например <code>850.50</code>.", parse_mode="HTML")
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше нуля.")
        return

    if amount > 10_000_000:
        await message.answer("❌ Сумма слишком большая. Проверьте ввод.")
        return

    await state.update_data(manual_amount=round(amount, 2))
    await state.set_state(ReceiptState.manual_category)  # [CHG] категория через общую клавиатуру
    await message.answer(  # [CHG]
        "🏷 Выберите категорию покупки:",  # [CHG]
        reply_markup=purchase_categories_keyboard("manual_cat")  # [CHG] тот же список, что на скриншоте
    )


@dp.callback_query(ReceiptState.manual_category, lambda c: c.data.startswith("manual_cat:"))
async def manual_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]

    if category == "✏️ Своя категория":
        await state.set_state(ReceiptState.manual_category_custom)
        await callback.message.edit_text(
            "✏️ Введите свою категорию одним сообщением.\n"
            "Например: «Дом», «Аптека» или «Питомцы»."
        )
        await callback.answer()
        return

    await state.update_data(manual_category=category)
    await show_manual_payer(callback.message, state)
    await callback.answer()


@dp.message(ReceiptState.manual_category_custom)
async def manual_custom_category(message: Message, state: FSMContext):
    category = (message.text or "").strip()
    if not category or len(category) > 50:
        await message.answer("❌ Категория должна содержать от 1 до 50 символов.")
        return

    # [FIX] Принудительно капитализируем первую букву (например: "аптека" -> "Аптека"), 
    # чтобы на графике не плодились одинаковые категории с разным регистром
    category = category.capitalize() 

    await state.update_data(manual_category=category) # [CHG] Сохраняем обработанную строку
    await show_manual_payer(message, state)


async def show_manual_payer(message: Message, state: FSMContext):
    members = await get_members(message.chat.id)
    builder = InlineKeyboardBuilder()

    for user_id, username, first_name in members:
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(text=f"💳 {name}", callback_data=f"manual_payer:{user_id}")
    builder.adjust(1)

    await state.set_state(ReceiptState.manual_payer)
    await message.answer(
        "💳 Кто оплатил покупку?",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(ReceiptState.manual_payer, lambda c: c.data.startswith("manual_payer:"))
async def manual_payer(callback: CallbackQuery, state: FSMContext):
    payer_id = int(callback.data.split(":", 1)[1])
    await state.update_data(manual_payer=payer_id, manual_selected=[])

    await show_manual_participants(callback.message, state)
    await callback.answer()


async def show_manual_participants(message: Message, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("manual_selected", []))
    members = await get_members(message.chat.id)

    builder = InlineKeyboardBuilder()
    for user_id, username, first_name in members:
        mark = "✅" if user_id in selected else "⬜"
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(
            text=f"{mark} {name}",
            callback_data=f"manual_member:{user_id}"
        )

    builder.button(text="✅ Готово", callback_data="manual_members_done")
    builder.adjust(2)

    await state.set_state(ReceiptState.manual_participants)
    await message.answer(
        "👥 Кто участвует в покупке?\n\n"
        "Выберите одного или нескольких участников. "
        "Оплачивающий тоже может быть выбран.",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(ReceiptState.manual_participants, lambda c: c.data.startswith("manual_member:"))
async def manual_toggle_participant(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get("manual_selected", []))

    if uid in selected:
        selected.remove(uid)
    else:
        selected.add(uid)

    await state.update_data(manual_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=await manual_participants_markup(callback.message.chat.id, selected)
    )
    await callback.answer()


async def manual_participants_markup(chat_id: int, selected):
    members = await get_members(chat_id)
    builder = InlineKeyboardBuilder()

    for user_id, username, first_name in members:
        mark = "✅" if user_id in selected else "⬜"
        name = first_name or (f"@{username}" if username else str(user_id))
        builder.button(
            text=f"{mark} {name}",
            callback_data=f"manual_member:{user_id}"
        )

    builder.button(text="✅ Готово", callback_data="manual_members_done")
    builder.adjust(2)
    return builder.as_markup()


@dp.callback_query(ReceiptState.manual_participants, lambda c: c.data == "manual_members_done")
async def manual_members_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("manual_selected", [])

    if not selected:
        await callback.answer("Выберите хотя бы одного участника.", show_alert=True)
        return

    amount = float(data["manual_amount"])
    category = data["manual_category"]
    payer = int(data["manual_payer"])
    share = round(amount / len(selected), 2)

    members = await get_members(callback.message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    selected_names = ", ".join(names.get(uid, str(uid)) for uid in selected)
    payer_name = names.get(payer, "Неизвестный")

    await state.set_state(ReceiptState.manual_confirmation)
    await callback.message.edit_text(
        "✍️ <b>Проверьте покупку</b>\n\n"
        f"💰 Сумма: {amount:.2f} ₽\n"
        f"🏷 Категория: {category}\n"
        f"💳 Оплатил: {payer_name}\n"
        f"👥 Участники: {selected_names}\n"
        f"💸 Доля каждого: {share:.2f} ₽\n\n"
        "Если всё верно — подтвердите.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="manual_confirm"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="manual_cancel"),
            ]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(ReceiptState.manual_confirmation, lambda c: c.data == "manual_confirm")
async def manual_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = callback.message.chat.id
    amount = float(data["manual_amount"])
    category = data["manual_category"]
    payer = int(data["manual_payer"])
    selected = data["manual_selected"]

    await add_group(chat_id, callback.message.chat.title or "")
    await add_receipt(
        chat_id=chat_id,
        user_id=callback.from_user.id,
        file_id=None,
        image_path=None
    )
    receipt = await get_last_receipt(chat_id)
    receipt_id = receipt[0]

    for uid in selected:
        await link_member_to_receipt(receipt_id, uid)

    await set_receipt_payer(receipt_id, payer)
    await update_receipt_total(receipt_id, amount, "Ручной ввод", category)

    members = await get_members(chat_id)
    payer_name = next(
        (
            first_name or (f"@{username}" if username else str(uid))
            for uid, username, first_name in members
            if uid == payer
        ),
        "Неизвестный"
    )

    share = round(amount / len(selected), 2)
    for uid in selected:
        if uid == payer:
            continue
        await add_debt(
            chat_id=chat_id,
            from_user=uid,
            to_user=payer,
            receipt_id=receipt_id,
            amount=share
        )

    await callback.message.edit_text(
        "✅ <b>Покупка добавлена!</b>\n\n"
        f"💰 {amount:.2f} ₽\n"
        f"🏷 {category}\n"
        f"💳 Оплатил: {payer_name}\n"
        f"👥 Участников: {len(selected)}\n"
        f"💸 Доля: {share:.2f} ₽",
        parse_mode="HTML"
    )
    await state.clear()
    await callback.answer("Покупка сохранена!")


@dp.callback_query(ReceiptState.manual_confirmation, lambda c: c.data == "manual_cancel")
async def manual_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Добавление покупки отменено.\n\nДолги не созданы.")
    await state.clear()
    await callback.answer("Отменено")


# =========================================================
# ПОЛУЧЕНИЕ ФОТО ЧЕКА
# =========================================================

@dp.message(ReceiptState.waiting_receipt_photo, F.photo)  # [CHG] фото чека принимаем только после кнопки
async def receive_receipt(
    message: Message,
    state: FSMContext
):
    if message.chat.type == "private":
        await state.clear()  # [NEW] сбрасываем ожидание, если вдруг нажали в личке
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
        await state.clear()  # [NEW] не оставляем режим ожидания чека
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
async def upload_receipt_button(message: Message, state: FSMContext):  # [CHG] включаем режим ожидания фото
    if message.chat.type == "private":  # [NEW]
        await message.answer(  # [NEW]
            "❌ Эту функцию нужно использовать в группе."  # [NEW]
        )
        return  # [NEW]

    members = await get_members(message.chat.id)  # [NEW]
    if not members:  # [NEW]
        await message.answer(  # [NEW]
            "❌ В группе пока нет участников.\n\n"
            "Сначала нажмите «➕ Присоединиться»."
        )
        return  # [NEW]

    await state.set_state(ReceiptState.waiting_receipt_photo)  # [NEW] дальше ждём только фото чека
    await message.answer(  # [CHG]
        "📸 Отправьте фотографию чека в этот чат.\n\n"
        "Другие файлы не подойдут. Если пришлёте не фото — "
        "я напомню один раз, и загрузку нужно будет начать заново."
    )


@dp.message(ReceiptState.waiting_receipt_photo)  # [NEW] любое сообщение после кнопки, если это не фото
async def receipt_wait_not_photo(message: Message, state: FSMContext):  # [NEW]
    await state.clear()  # [NEW] выключаем ожидание: чтобы добавить чек, кнопку жмут снова
    await message.answer(  # [NEW]
        "❌ Пожалуйста, отправьте фото чека.\n\n"
        "Нажмите «📸 Загрузить чек», "
        "чтобы попробовать снова."
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
        payer=callback.from_user.id,
        items_text=items_text,  # [NEW] текст товаров для экрана подтверждения после выбора категории
        names_text=names_text,  # [NEW]
        share=share,  # [NEW]
        store=store,  # [NEW]
        total=total,  # [NEW]
    )

    await state.set_state(ReceiptState.receipt_category)  # [NEW] сначала категория, потом подтверждение
    await callback.message.edit_text(  # [NEW]
        "🏷 Выберите категорию покупки:\n\n"  # [NEW]
        "Все товары из этого чека будут записаны в выбранную категорию "  # [NEW]
        "и попадут на круговую диаграмму.",  # [NEW]
        reply_markup=purchase_categories_keyboard("receipt_cat"),  # [NEW] тот же список, что у ручной покупки
    )  # [NEW]

    await callback.answer()


# =========================================================
# [NEW] КАТЕГОРИЯ ДЛЯ ЧЕКА — ТОТ ЖЕ СПИСОК, ЧТО У РУЧНОЙ ПОКУПКИ
# =========================================================

async def send_receipt_confirmation(message: Message, state: FSMContext, *, edit: bool):  # [NEW] экран проверки чека после выбора категории
    data = await state.get_data()  # [NEW]
    receipt_id = data["receipt_id"]  # [NEW]
    store = data.get("store", "Неизвестный магазин")  # [NEW]
    items_text = data.get("items_text", "")  # [NEW]
    total = float(data.get("total", 0))  # [NEW]
    names_text = data.get("names_text", "")  # [NEW]
    share = float(data.get("share", 0))  # [NEW]
    category = data.get("receipt_category", "Прочее")  # [NEW]

    builder = InlineKeyboardBuilder()  # [NEW]
    builder.button(text="✅ Подтвердить", callback_data=f"confirm_{receipt_id}")  # [NEW]
    builder.button(text="❌ Отменить", callback_data=f"cancel_{receipt_id}")  # [NEW]
    builder.adjust(1)  # [NEW]

    text = (  # [NEW]
        f"🧾 Проверьте данные чека:\n\n"
        f"🏪 {store}\n"
        f"🏷 Категория: {category}\n\n"
        f"{items_text}\n"
        f"💰 Итого: {total:.2f} ₽\n\n"
        f"👥 Участники: {names_text}\n\n"
        f"💳 Доля каждого: {share:.2f} ₽\n\n"
        f"Если всё правильно — нажмите "
        f"«Подтвердить»."
    )

    await state.set_state(ReceiptState.waiting_confirmation)  # [NEW]
    if edit:  # [NEW]
        await message.edit_text(text, reply_markup=builder.as_markup())  # [NEW]
    else:  # [NEW]
        await message.answer(text, reply_markup=builder.as_markup())  # [NEW]


@dp.callback_query(ReceiptState.receipt_category, lambda c: c.data.startswith("receipt_cat:"))  # [NEW]
async def receipt_category(callback: CallbackQuery, state: FSMContext):  # [NEW]
    category = callback.data.split(":", 1)[1]  # [NEW]

    if category == "✏️ Своя категория":  # [NEW]
        await state.set_state(ReceiptState.receipt_category_custom)  # [NEW]
        await callback.message.edit_text(  # [NEW]
            "✏️ Введите свою категорию одним сообщением.\n"
            "Например: «Дом», «Аптека» или «Питомцы»."
        )
        await callback.answer()  # [NEW]
        return  # [NEW]

    await state.update_data(receipt_category=category)  # [NEW] все товары чека попадут в эту категорию
    await send_receipt_confirmation(callback.message, state, edit=True)  # [NEW]
    await callback.answer()  # [NEW]


@dp.message(ReceiptState.receipt_category_custom)  # [NEW]
async def receipt_custom_category(message: Message, state: FSMContext):  # [NEW]
    category = (message.text or "").strip()  # [NEW]
    if not category or len(category) > 50:  # [NEW]
        await message.answer("❌ Категория должна содержать от 1 до 50 символов.")  # [NEW]
        return  # [NEW]

    # [FIX] Также капитализируем кастомную категорию для фото-чеков для одинакового отображения
    category = category.capitalize()

    await state.update_data(receipt_category=category)  # [NEW]
    await send_receipt_confirmation(message, state, edit=False)  # [NEW]


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
            price,
            data.get("receipt_category", "Прочее"),  # [NEW] товар чека пишется в выбранную категорию
        )

    # Запоминаем пользователя, который подтвердил оплату чека.
    await set_receipt_payer(receipt_id, payer)

    category = data.get("receipt_category", "Прочее")  # [NEW] категория всего чека для диаграммы

    await update_receipt_total(
        receipt_id,
        total,
        store,
        category,  # [CHG] раньше чек сохранялся без категории
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
        f"🏷 Категория: {category}\n"  # [NEW]
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

def receipts_list_kb(data):
    builder = InlineKeyboardBuilder()
    for receipt_id, _, created_at, *_ in data:
        builder.button(
            text=f"🧾 {created_at}",
            callback_data=f"receipt:{receipt_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


@dp.message(Command("receipts"))
async def receipts(message: Message):
    data = await get_receipts(message.chat.id)

    if not data:
        await message.answer("Пока нет сохранённых чеков.")
        return

    members = await get_members(message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    builder = InlineKeyboardBuilder()

    for receipt in data:
        (
            receipt_id,
            _,
            created_at,
            user_id,
            username,
            first_name,
            store,
            total,
            payer_id,
        ) = receipt

        buyer_name = names.get(
            payer_id,
            first_name or (f"@{username}" if username else str(user_id))
        )

        builder.button(
            text=f"🧾 {created_at} — 👤 {buyer_name}",
            callback_data=f"receipt:{receipt_id}",
        )

    builder.adjust(1)

    await message.answer(
        "🧾 Все чеки:\n\n"
        "Нажмите на чек, чтобы открыть полную информацию:",
        reply_markup=builder.as_markup(),
    )


@dp.message(lambda message: message.text == "🧾 Все чеки")
async def all_receipts_button(message: Message):
    await receipts(message)


@dp.callback_query(lambda c: c.data.startswith("receipt:"))
async def receipt_details(callback: CallbackQuery):
    receipt_id = int(callback.data.split(":", 1)[1])

    data = await get_receipts(callback.message.chat.id)
    receipt = next((r for r in data if r[0] == receipt_id), None)

    if not receipt:
        await callback.answer("Чек не найден.", show_alert=True)
        return

    (
        _,
        image_path,
        created_at,
        user_id,
        username,
        first_name,
        store,
        total,
        payer_id,
    ) = receipt

    members = await get_members(callback.message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    buyer_name = names.get(
        payer_id,
        first_name or (f"@{username}" if username else str(user_id))
    )

    items = await get_receipt_items(receipt_id)
    participants = await get_receipt_participants(receipt_id)

    lines = [
        f"🧾 Чек #{receipt_id}",
        f"📅 Дата: {created_at}",
        f"👤 Кто купил: {buyer_name}",
    ]

    if store:
        lines.append(f"🏪 Магазин: {store}")

    total_value = float(total or 0)
    lines.append(f"💰 Итого: {total_value:.2f} ₽")

    lines.append("")
    lines.append("🛒 Товары:")

    if items:
        for item in items:
            name = item[0]  # [CHG] название товара
            price = item[1]  # [CHG]
            item_category = item[2] if len(item) > 2 else None  # [NEW] категория позиции
            if item_category:  # [NEW]
                lines.append(f"• {name} — {float(price):.2f} ₽ ({item_category})")  # [NEW]
            else:  # [NEW]
                lines.append(f"• {name} — {float(price):.2f} ₽")
    else:
        lines.append("• Нет распознанных товаров.")

    lines.append("")
    lines.append("💸 Кто сколько должен:")

    if participants and total_value > 0:
        base_share = round(total_value / len(participants), 2)
        running = 0.0

        for index, (uid, username, participant_first_name) in enumerate(participants):
            name = participant_first_name or (
                f"@{username}" if username else str(uid)
            )

            # Последнему участнику отдаём остаток от округления.
            if index == len(participants) - 1:
                amount = round(total_value - running, 2)
            else:
                amount = base_share

            running += amount

            if payer_id and uid == payer_id:
                lines.append(f"• {name} — 0 ₽ (оплатил чек)")
            else:
                lines.append(f"• {name} — {amount:.2f} ₽")

    elif participants:
        for uid, username, participant_first_name in participants:
            name = participant_first_name or (
                f"@{username}" if username else str(uid)
            )
            lines.append(f"• {name} — сумма не определена")
    else:
        lines.append("• Участники не указаны.")

    # Фото чека при просмотре списка чеков не читаем и не отправляем.
    # Показываем только сохранённые данные: товары, цены и расчёт долгов.
    await callback.message.answer("\n".join(lines))

    await callback.answer()

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

    builder = InlineKeyboardBuilder()

    for receipt_id, date in data:
        builder.button(
            text=f"🧾 {date}",
            callback_data=f"receipt:{receipt_id}",
        )

    builder.adjust(1)

    await message.answer(
        "🧾 Ваши последние чеки:\n\n"
        "Нажмите на чек, чтобы открыть товары, цены и расчёт долгов:",
        reply_markup=builder.as_markup(),
    )


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
# [NEW] КРУГОВАЯ ДИАГРАММА РАСХОДОВ ПО КАТЕГОРИЯМ
# =========================================================

def _first_existing_font(paths):  # [NEW] берём первый найденный файл шрифта
    for path in paths:  # [NEW]
        if os.path.exists(path):  # [NEW]
            return font_manager.FontProperties(fname=path)  # [NEW]
    return font_manager.FontProperties()  # [NEW]


def _cyrillic_fonts():  # [CHG] обычный и жирный шрифт для заголовков и подписей
    regular = _first_existing_font([  # [CHG]
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
    ])
    bold = _first_existing_font([  # [NEW] жирный вариант для заголовка и суммы
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\tahomabd.ttf",
    ])
    return regular, bold  # [NEW]


def build_category_pie_chart(rows):  # [CHG] вертикальная картинка: на телефоне текст не сжимается
    labels = [str(row[0]) for row in rows]  # [NEW] названия категорий
    values = [float(row[1]) for row in rows]  # [NEW] суммы по категориям
    total = sum(values)  # [NEW] итог в центре кольца
    font, font_bold = _cyrillic_fonts()  # [CHG]

    bg = "#F6F3EE"  # [NEW] тёплый фон вместо резкого белого
    ink = "#1F2A37"  # [NEW] цвет заголовков
    muted = "#5C6B7A"  # [NEW] цвет второстепенного текста
    palette = [  # [CHG] спокойная палитра, без кислотных цветов tab20
        "#5B8DEF",
        "#3DBE9A",
        "#F3B23C",
        "#7B6CFF",
        "#F07A5A",
        "#4AA8D8",
        "#C26BD4",
        "#6B8F71",
        "#E08AB0",
        "#8A9BB5",
    ]
    colors = [palette[i % len(palette)] for i in range(len(values))]  # [CHG]

    legend_rows = max(len(values), 3)  # [NEW] высота картинки растёт вместе с легендой
    fig_h = 9.2 + legend_rows * 0.55  # [NEW]
    fig, ax = plt.subplots(figsize=(8.2, fig_h), facecolor=bg)  # [CHG] узкий портрет — крупнее на экране телефона
    ax.set_facecolor(bg)  # [NEW]

    def slice_label(pct):  # [NEW] мелкие сектора не подписываем, чтобы не наслаивался текст
        return f"{pct:.0f}%" if pct >= 8 else ""  # [CHG] порог чуть выше: крупные цифры читаются лучше

    wedges, _, autotexts = ax.pie(  # [CHG] кольцо с зазорами между секторами
        values,
        autopct=slice_label,
        startangle=90,
        pctdistance=0.78,
        colors=colors,
        radius=1.18,  # [NEW] круг крупнее относительно холста
        wedgeprops={
            "width": 0.50,
            "edgecolor": bg,
            "linewidth": 4.0,
            "antialiased": True,
        },
    )

    for autotext in autotexts:  # [CHG] проценты крупно, чтобы читались после сжатия Telegram
        autotext.set_fontproperties(font_bold)
        autotext.set_fontsize(25)
        autotext.set_color("#FFFFFF")

    ax.text(  # [NEW] подпись в центре кольца
        0,
        0.14,
        "Итого",
        ha="center",
        va="center",
        fontproperties=font,
        fontsize=22,
        color=muted,
    )
    ax.text(  # [NEW] сумма в центре
        0,
        -0.14,
        f"{total:,.0f} ₽".replace(",", " "),
        ha="center",
        va="center",
        fontproperties=font_bold,
        fontsize=28,
        color=ink,
    )

    ax.set_title("")  # [CHG] заголовок рисуем сами, чтобы разделить жирный и обычный текст
    ax.text(  # [CHG]
        0.5,
        1.28,
        "Расходы по категориям",
        transform=ax.transAxes,
        fontproperties=font_bold,
        fontsize=30,
        color=ink,
        ha="center",
        va="bottom",
    )
    ax.text(  # [NEW]
        0.5,
        1.14,
        "за последние 7 дней",
        transform=ax.transAxes,
        fontproperties=font,
        fontsize=24,
        color=muted,
        ha="center",
        va="bottom",
    )

    legend_labels = [  # [CHG] одна строка крупным шрифтом — удобнее на телефоне
        f"  {label}   {value:,.0f} ₽   {value / total * 100:.0f}%".replace(",", " ")
        for label, value in zip(labels, values)
    ]
    legend = ax.legend(  # [CHG] легенда под кругом, а не сбоку — не мельчает на узком экране
        wedges,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        frameon=False,
        handlelength=1.4,
        handleheight=1.4,
        markerscale=1.6,
        borderaxespad=0.0,
        labelspacing=1.15,
        fontsize=22,
    )
    for text in legend.get_texts():  # [NEW] подписи легенды крупнее и темнее
        text.set_fontproperties(font)
        text.set_fontsize(22)
        text.set_color(ink)
        text.set_va("center")

    ax.set_aspect("equal")  # [NEW] круг без сжатия
    fig.subplots_adjust(left=0.06, right=0.94, top=0.78, bottom=0.22)  # [CHG] запас сверху и снизу под крупный текст

    buffer = BytesIO()  # [NEW] сохраняем картинку в память
    fig.savefig(  # [CHG] высокий dpi: Telegram сожмёт картинку, буквы останутся чёткими
        buffer,
        format="png",
        dpi=180,
        facecolor=bg,
        bbox_inches="tight",
        pad_inches=0.45,
    )
    plt.close(fig)  # [NEW] освобождаем память matplotlib
    buffer.seek(0)  # [NEW] курсор в начало буфера перед отправкой
    return buffer.getvalue()  # [NEW] байты готовой картинки


@dp.message(Command("categories"))  # [NEW] команда /categories — как /debts и /receipts
async def show_category_chart(message: Message):
    if message.chat.type == "private":  # [NEW] статистика группы доступна только в группе
        await message.answer(
            "❌ Эту команду нужно использовать в группе."
        )
        return

    rows = await get_category_totals_last_week(message.chat.id)  # [CHG] данные за последние 7 дней

    if not rows:  # [NEW] нет подтверждённых покупок с суммой
        await message.answer(
            "📊 За последнюю неделю ещё нет расходов по категориям.\n\n"  # [CHG]
            "Добавьте покупку кнопкой «✍️ Добавить покупку» "
            "или подтвердите чек и выберите категорию."  # [CHG]
        )
        return

    total = sum(float(amount) for _, amount in rows)  # [NEW] общая сумма для подписи
    lines = ["📊 Расходы по категориям за 7 дней:\n"]  # [CHG] текстовая расшифровка рядом с графиком
    for category, amount in rows:
        share = (float(amount) / total) * 100 if total else 0
        lines.append(f"• {category}: {float(amount):.2f} ₽ ({share:.1f}%)")
    lines.append(f"\n💰 Итого: {total:.2f} ₽")

    caption = "\n".join(lines)  # [NEW] текстовая расшифровка долей бюджета
    photo_bytes = build_category_pie_chart(rows)  # [NEW] строим круговую диаграмму
    photo = BufferedInputFile(photo_bytes, filename="categories.png")  # [NEW] файл для Telegram

    if len(caption) > 1000:  # [NEW] если подпись не влезает, шлём график и текст отдельно
        await message.answer_photo(photo=photo, caption="📊 Расходы по категориям за 7 дней")  # [CHG]
        await message.answer(caption)
    else:
        await message.answer_photo(photo=photo, caption=caption)  # [NEW] график + расшифровка одним сообщением


@dp.message(lambda message: message.text == "📊 Категории")  # [NEW] кнопка панели, как у «Все долги»
async def categories_button(message: Message):
    await show_category_chart(message)  # [NEW] та же логика, что у slash-команды


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
# [NEW] КНОПКА "ОЧИСТИТЬ СПИСОК ТРАТ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 Очистить список трат")  # [NEW]
async def clear_expenses_button(message: Message):  # [NEW]
    if message.chat.type == "private":  # [NEW] как у остальных кнопок очистки
        await message.answer(  # [NEW]
            "❌ Эту функцию нужно использовать в группе."  # [NEW]
        )
        return  # [NEW]

    builder = InlineKeyboardBuilder()  # [NEW] подтверждение, как у «Очистить участников»

    builder.button(  # [NEW]
        text="🗑 Да, очистить",  # [NEW]
        callback_data="confirm_clear_expenses"  # [NEW]
    )

    builder.button(  # [NEW]
        text="❌ Отмена",  # [NEW]
        callback_data="cancel_clear_expenses"  # [NEW]
    )

    builder.adjust(1)  # [NEW]

    await message.answer(  # [NEW]
        "⚠️ <b>Очистить список трат?</b>\n\n"  # [NEW]
        "Будут удалены все чеки и ручные покупки этой группы.\n\n"  # [CHG] долги больше не удаляем
        "Долги и участники группы останутся.\n\n"  # [CHG]
        "Это действие нельзя отменить.",  # [NEW]
        reply_markup=builder.as_markup(),  # [NEW]
        parse_mode="HTML"  # [NEW]
    )


@dp.callback_query(lambda c: c.data == "confirm_clear_expenses")  # [NEW]
async def confirm_clear_expenses(callback: CallbackQuery):  # [NEW]
    await clear_expenses(callback.message.chat.id)  # [NEW]

    await callback.message.edit_text(  # [NEW]
        "🧾 Список трат очищен.\n\n"  # [NEW]
        "Чеки и покупки этой группы удалены."  # [CHG]
    )

    await callback.answer("Траты удалены")  # [NEW]


@dp.callback_query(lambda c: c.data == "cancel_clear_expenses")  # [NEW]
async def cancel_clear_expenses(callback: CallbackQuery):  # [NEW]
    await callback.message.edit_text(  # [NEW]
        "❌ Очистка списка трат отменена."  # [NEW]
    )

    await callback.answer("Отменено")  # [NEW]


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

