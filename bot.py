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
    get_my_debts,
    get_receipt_items,
    get_receipt_participants,
    set_receipt_payer,
    get_category_totals_last_week,  # [CHG] суммы по категориям за последнюю неделю
    clear_expenses,  # [NEW] удаление чеков, товаров и связанных долгов группы
    link_member_to_item,
)



load_dotenv()
 
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Время жизни сообщений с меню выбора.
BUTTON_MENU_DELETE_DELAY = 20


def schedule_button_message_delete(message: Message, delay: int = BUTTON_MENU_DELETE_DELAY):
    """Удаляет сообщение с inline-кнопками через delay секунд, если его ещё не убрали."""
    async def _delete_later():
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            # Сообщение могло быть удалено раньше после нажатия кнопки.
            pass

    asyncio.create_task(_delete_later())


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
                KeyboardButton(text="💸 Долги"),
                KeyboardButton(text="🧾 История покупок"),
            ],
            [
                KeyboardButton(text="➕ Добавить покупку"),
                KeyboardButton(text="📊 Категории"),
            ],
            [
                KeyboardButton(text="🧹 Очистить"),
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
        BotCommand(  # [NEW] команда меню Telegram для диаграммы категорий
            command="categories",
            description="📊 Расходы по категориям"
        ),
    ]

    await bot.set_my_commands(commands)


# =========================================================
# /start
# =========================================================

async def send_main_menu(message: Message):
    """Показывает актуальное главное меню и принудительно обновляет Reply Keyboard."""
    await message.answer(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(CommandStart())
async def start(message: Message):
    await send_main_menu(message)



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


@dp.message(lambda message: message.text == "➕ Добавить покупку")
async def add_purchase_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Сканировать чек", callback_data="add_purchase:scan")
    builder.button(text="✍️ Ручной ввод", callback_data="add_purchase:manual")
    builder.button(text="⬅️ Назад", callback_data="add_purchase:back")
    builder.adjust(1)
    sent = await message.answer("➕ <b>Добавить покупку</b>\n\nВыберите способ:", reply_markup=builder.as_markup(), parse_mode="HTML")
    schedule_button_message_delete(sent)


@dp.callback_query(lambda c: c.data == "add_purchase:back")
async def add_purchase_back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@dp.callback_query(lambda c: c.data == "add_purchase:scan")
async def add_purchase_scan(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.type == "private":
        await callback.answer("Сканировать чек нужно в группе.", show_alert=True)
        return
    await state.clear()
    await state.set_state(ReceiptState.waiting_receipt_photo)
    await callback.message.edit_text("📷 <b>Сканирование чека</b>\n\nОтправьте фотографию чека в этот чат.")
    await callback.answer()


@dp.callback_query(lambda c: c.data == "add_purchase:manual")
async def manual_purchase_start_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await manual_purchase_start(callback.message, state)
    await callback.answer()


@dp.message(lambda message: message.text == "✍️ Ручной ввод")
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
    sent = await message.answer(  # [CHG]
        "🏷 Выберите категорию покупки:",  # [CHG]
        reply_markup=purchase_categories_keyboard("manual_cat")  # [CHG] тот же список, что на скриншоте
    )
    schedule_button_message_delete(sent)


@dp.callback_query(ReceiptState.manual_category, lambda c: c.data.startswith("manual_cat:"))
async def manual_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    try:
        await callback.message.delete()
    except Exception:
        pass

    if category == "✏️ Своя категория":
        await state.set_state(ReceiptState.manual_category_custom)
        await callback.message.answer(
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
    sent = await message.answer(
        "💳 Кто оплатил покупку?",
        reply_markup=builder.as_markup()
    )
    schedule_button_message_delete(sent)


@dp.callback_query(ReceiptState.manual_payer, lambda c: c.data.startswith("manual_payer:"))
async def manual_payer(callback: CallbackQuery, state: FSMContext):
    payer_id = int(callback.data.split(":", 1)[1])
    try:
        await callback.message.delete()
    except Exception:
        pass
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

@dp.message(ReceiptState.waiting_receipt_photo, F.photo)
async def receive_receipt(message: Message, state: FSMContext):
    if message.chat.type == "private":
        await state.clear()
        await message.answer("📸 Отправляй чек в группу.")
        return

    await add_group(message.chat.id, message.chat.title or "")
    photo = message.photo[-1]
    file_name = f"{message.chat.id}_{message.from_user.id}_{photo.file_unique_id}.jpg"
    image_path = RECEIPTS_DIR / file_name

    await bot.download(photo, destination=image_path)
    await add_receipt(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        file_id=photo.file_id,
        image_path=str(image_path)
    )
    receipt = await get_last_receipt(message.chat.id)
    receipt_id = receipt[0]

    await state.update_data(receipt_id=receipt_id, payer=message.from_user.id)
    await message.answer("🤖 Анализирую чек...")

    path = await get_receipt_path(receipt_id)
    try:
        receipt_data = recognize_receipt(path)
    except Exception as e:
        print(f"❌ Ошибка распознавания чека: {e}")
        await message.answer("❌ Не удалось распознать чек.\n\nПопробуйте отправить более чёткую фотографию чека.")
        await state.clear()
        return

    store = receipt_data.get("store", "Неизвестный магазин")
    total = float(receipt_data.get("total", 0))
    items = receipt_data.get("items", [])

    if total <= 0:
        await message.answer("❌ Не удалось определить итоговую сумму чека.\n\nПопробуйте отправить более чёткую фотографию.")
        await state.clear()
        return

    members = await get_members(message.chat.id)
    if not members:
        await state.clear()
        await message.answer("❌ В группе пока нет участников.\n\nСначала нажмите «➕ Присоединиться».")
        return

    # Все товары и все участники отмечены по умолчанию.
    # Пользователь может снять галочку с конкретного товара у конкретного человека.
    item_participants = {
        str(i): [uid for uid, _, _ in members]
        for i, _ in enumerate(items)
    }

    await state.update_data(
        receipt_id=receipt_id,
        receipt_data=receipt_data,
        item_participants=item_participants,
        receipt_members=[uid for uid, _, _ in members],
        payer=message.from_user.id,
    )
    await state.set_state(ReceiptState.choosing_members)
    await show_item_participants(message, state)


async def item_participants_markup(state: FSMContext, chat_id: int):
    data = await state.get_data()
    receipt_data = data.get("receipt_data", {})
    items = receipt_data.get("items", [])
    matrix = data.get("item_participants", {})
    members = await get_members(chat_id)

    builder = InlineKeyboardBuilder()
    for index, item in enumerate(items):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        builder.button(text=f"🛒 {name} — {price:.2f} ₽", callback_data=f"item_info:{index}")
        selected = set(matrix.get(str(index), []))
        for uid, username, first_name in members:
            mark = "✅" if uid in selected else "⬜"
            person = first_name or (f"@{username}" if username else str(uid))
            builder.button(text=f"{mark} {person}", callback_data=f"item_member:{index}:{uid}")

    builder.button(text="➡️ Далее", callback_data="items_done")
    builder.adjust(1)
    return builder.as_markup()


async def show_item_participants(message: Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("receipt_data", {}).get("items", [])
    if not items:
        await message.answer("⚠️ В чеке не удалось распознать отдельные товары.\n\nПерейдём к общей сумме чека.")
        await state.set_state(ReceiptState.receipt_category)
        sent = await message.answer(
            "🏷 Выберите категорию покупки:",
            reply_markup=purchase_categories_keyboard("receipt_cat")
        )
        schedule_button_message_delete(sent)
        return

    await message.answer(
        "🧾 <b>Кто за что платит?</b>\n\n"
        "По умолчанию все товары отмечены у всех участников.\n"
        "Нажмите на галочку рядом с человеком, чтобы убрать его с конкретного товара.\n\n"
        "Например: молоко → 2 человека, хлеб → 3 человека.",
        reply_markup=await item_participants_markup(state, message.chat.id),
        parse_mode="HTML"
    )


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data.startswith("item_member:"))
async def toggle_item_member(callback: CallbackQuery, state: FSMContext):
    _, item_index, user_id = callback.data.split(":")
    item_index = int(item_index)
    uid = int(user_id)
    data = await state.get_data()
    matrix = data.get("item_participants", {})
    selected = set(matrix.get(str(item_index), []))

    if uid in selected:
        selected.remove(uid)
    else:
        selected.add(uid)

    matrix[str(item_index)] = list(selected)
    await state.update_data(item_participants=matrix)
    await callback.message.edit_reply_markup(
        reply_markup=await item_participants_markup(state, callback.message.chat.id)
    )
    await callback.answer()


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data.startswith("item_info:"))
async def item_info(callback: CallbackQuery):
    await callback.answer("Нажимайте на имя участника, чтобы включить/выключить его для этого товара.", show_alert=True)


@dp.callback_query(ReceiptState.choosing_members, lambda c: c.data == "items_done")
async def finish_item_participants(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    receipt_data = data.get("receipt_data", {})
    items = receipt_data.get("items", [])
    matrix = data.get("item_participants", {})

    if not items:
        await callback.answer()
        return

    for index, item in enumerate(items):
        if not matrix.get(str(index), []):
            name = item.get("name", "товар")
            await callback.answer(f"Для товара «{name}» выберите хотя бы одного участника.", show_alert=True)
            return

    members = await get_members(callback.message.chat.id)
    all_selected = sorted({uid for ids in matrix.values() for uid in ids})
    for uid in all_selected:
        await link_member_to_receipt(data["receipt_id"], uid)

    total = float(receipt_data.get("total", 0))
    store = receipt_data.get("store", "Неизвестный магазин")
    items_text = ""
    for index, item in enumerate(items[:10]):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        count = len(matrix.get(str(index), []))
        items_text += f"• {name} — {price:.2f} ₽ · {count} чел.\n"
    if len(items) > 10:
        items_text += "• ...\n"

    names = {uid: (first_name or (f"@{username}" if username else str(uid))) for uid, username, first_name in members}
    selected_names = ", ".join(names[uid] for uid in all_selected if uid in names)

    await state.update_data(
        selected=all_selected,
        items_text=items_text,
        names_text=selected_names,
        store=store,
        total=total,
    )
    await state.set_state(ReceiptState.receipt_category)
    await callback.message.edit_text(
        "🏷 Выберите категорию покупки:\n\n"
        "Товары уже распределены между участниками. Категория нужна для статистики.",
        reply_markup=purchase_categories_keyboard("receipt_cat")
    )
    await callback.answer()


# =========================================================
# КАТЕГОРИЯ ДЛЯ ЧЕКА
# =========================================================

async def send_receipt_confirmation(message: Message, state: FSMContext, *, edit: bool):
    data = await state.get_data()
    receipt_id = data["receipt_id"]
    store = data.get("store", "Неизвестный магазин")
    items_text = data.get("items_text", "")
    total = float(data.get("total", 0))
    category = data.get("receipt_category", "Прочее")
    matrix = data.get("item_participants", {})
    members = await get_members(message.chat.id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) for uid, username, first_name in members}

    lines = [f"🧾 <b>Проверьте распределение чека</b>", "", f"🏪 {store}", f"🏷 Категория: {category}", "", "🛒 Товары:"]
    items = data.get("receipt_data", {}).get("items", [])
    for index, item in enumerate(items):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        selected = [names[uid] for uid in matrix.get(str(index), []) if uid in names]
        lines.append(f"• {name} — {price:.2f} ₽")
        lines.append(f"  👥 {', '.join(selected)}")

    lines += ["", f"💰 Итого: {total:.2f} ₽", "", "Если всё верно — подтвердите."]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"confirm_{receipt_id}")
    builder.button(text="❌ Отменить", callback_data=f"cancel_{receipt_id}")
    builder.adjust(1)

    await state.set_state(ReceiptState.waiting_confirmation)
    text = "\n".join(lines)
    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@dp.callback_query(ReceiptState.receipt_category, lambda c: c.data.startswith("receipt_cat:"))
async def receipt_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    try:
        await callback.message.delete()
    except Exception:
        pass
    if category == "✏️ Своя категория":
        await state.set_state(ReceiptState.receipt_category_custom)
        await callback.message.answer("✏️ Введите свою категорию одним сообщением.\nНапример: «Дом», «Аптека» или «Питомцы».")
        await callback.answer()
        return
    await state.update_data(receipt_category=category)
    await send_receipt_confirmation(callback.message, state, edit=True)
    await callback.answer()


@dp.message(ReceiptState.receipt_category_custom)
async def receipt_custom_category(message: Message, state: FSMContext):
    category = (message.text or "").strip()
    if not category or len(category) > 50:
        await message.answer("❌ Категория должна содержать от 1 до 50 символов.")
        return
    await state.update_data(receipt_category=category.capitalize())
    await send_receipt_confirmation(message, state, edit=False)


# =========================================================
# ПОДТВЕРЖДЕНИЕ ПОКУПКИ
# =========================================================

@dp.callback_query(ReceiptState.waiting_confirmation, lambda c: c.data.startswith("confirm_"))
async def confirm_receipt(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    receipt_id = int(callback.data.split("_")[1])
    receipt_data = data.get("receipt_data")
    matrix = data.get("item_participants", {})
    payer = int(data.get("payer", callback.from_user.id))

    if not receipt_data:
        await callback.message.edit_text("❌ Данные чека потеряны.\nОтправьте чек ещё раз.")
        await state.clear()
        await callback.answer()
        return

    total = float(receipt_data.get("total", 0))
    store = receipt_data.get("store", "Неизвестный магазин")
    category = data.get("receipt_category", "Прочее")
    items = receipt_data.get("items", [])

    # Сохраняем товары и их индивидуальных участников.
    for index, item in enumerate(items):
        name = item.get("name", "Неизвестный товар")
        price = float(item.get("price", 0))
        item_id = await add_item(receipt_id, name, price, category)
        for uid in matrix.get(str(index), []):
            await link_member_to_item(item_id, uid)

    await set_receipt_payer(receipt_id, payer)
    await update_receipt_total(receipt_id, total, store, category)

    # Для каждого товара сумма делится только между теми, у кого стоит галочка.
    debt_by_user = {}
    for index, item in enumerate(items):
        price = float(item.get("price", 0))
        participants = matrix.get(str(index), [])
        if not participants:
            continue
        base_share = round(price / len(participants), 2)
        running = 0.0
        for pos, uid in enumerate(participants):
            if pos == len(participants) - 1:
                share = round(price - running, 2)
            else:
                share = base_share
            running += share
            if uid != payer:
                debt_by_user[uid] = round(debt_by_user.get(uid, 0) + share, 2)

    for uid, amount in debt_by_user.items():
        if amount > 0:
            await add_debt(
                chat_id=callback.message.chat.id,
                from_user=uid,
                to_user=payer,
                receipt_id=receipt_id,
                amount=amount
            )

    members = await get_members(callback.message.chat.id)
    names = {uid: (first_name or (f"@{username}" if username else str(uid))) for uid, username, first_name in members}
    payer_name = names.get(payer, "Покупатель")

    debt_lines = []
    for uid, amount in sorted(debt_by_user.items(), key=lambda x: names.get(x[0], str(x[0]))):
        debt_lines.append(f"• {names.get(uid, str(uid))} — {amount:.2f} ₽")

    await callback.message.edit_text(
        f"✅ <b>Чек подтверждён!</b>\n\n"
        f"🏪 {store}\n"
        f"🏷 Категория: {category}\n"
        f"💰 Итого: {total:.2f} ₽\n"
        f"💳 Оплатил: {payer_name}\n\n"
        f"💸 Долги по товарам:\n" + ("\n".join(debt_lines) if debt_lines else "• Долгов нет"),
        parse_mode="HTML"
    )
    await state.clear()
    await callback.answer("Чек подтверждён!")


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

    sent = await message.answer(
        "🧾 Все чеки:\n\n"
        "Нажмите на чек, чтобы открыть полную информацию:",
        reply_markup=builder.as_markup(),
    )
    schedule_button_message_delete(sent)


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
# МЕНЮ "ЧЕКИ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 История покупок")
async def checks_menu(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧾 Все чеки", callback_data="checks:all")
    builder.button(text="🧾 Мои чеки", callback_data="checks:mine")
    builder.button(text="⬅️ Назад", callback_data="checks:back")
    builder.adjust(1)

    sent = await message.answer(
        "🧾 <b>История покупок</b>\n\nВыберите, что открыть:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    schedule_button_message_delete(sent)


@dp.callback_query(lambda c: c.data == "checks:all")
async def checks_all(callback: CallbackQuery):
    await callback.message.delete()
    await receipts(callback.message)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "checks:mine")
async def checks_mine(callback: CallbackQuery):
    await callback.message.delete()
    await my_receipts_button(callback.message, user_id=callback.from_user.id)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "checks:back")
async def checks_back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# =========================================================
# КНОПКА "МОИ ЧЕКИ"
# =========================================================

@dp.message(lambda message: message.text == "🧾 Мои чеки")
async def my_receipts_button(message: Message, user_id: int | None = None):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    # При открытии через inline-меню message.from_user — это бот,
    # поэтому обязательно используем реального пользователя callback.
    actual_user_id = user_id if user_id is not None else message.from_user.id
    data = await get_my_receipts(
        message.chat.id,
        actual_user_id
    )

    if not data:
        await message.answer(
            "🧾 В вашей истории пока нет покупок.\n\n"
            "Добавьте покупку вручную или загрузите чек."
        )
        return

    builder = InlineKeyboardBuilder()

    for receipt_id, date, store, total, payer_id, source in data:
        source_icon = "✍️" if source == "manual" else "📸"
        store_name = store or "Покупка"
        total_text = f"{float(total):.2f} ₽" if total is not None else "сумма не указана"
        builder.button(
            text=f"{source_icon} {date} — {store_name} — {total_text}",
            callback_data=f"receipt:{receipt_id}",
        )

    builder.adjust(1)

    sent = await message.answer(
        "🧾 <b>Моя история покупок</b>\n\n"
        "✍️ — ручной ввод, 📸 — чек.\n"
        "Нажмите на покупку, чтобы открыть подробности.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    schedule_button_message_delete(sent)


# =========================================================
# КНОПКА / КОМАНДА "МОИ ДОЛГИ"
# =========================================================

async def show_my_debts(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "❌ Эту функцию нужно использовать в группе."
        )
        return

    debts = await get_my_debts(
        message.chat.id,
        message.from_user.id
    )

    if not debts:
        await message.answer(
            "✅ У вас нет непогашенных долгов."
        )
        return

    members = await get_members(message.chat.id)
    names = {
        uid: (first_name or (f"@{username}" if username else str(uid)))
        for uid, username, first_name in members
    }

    total = 0.0
    lines = ["💳 <b>Мои долги</b>", ""]

    for _, to_user, amount, receipt_id, created_at, store in debts:
        amount = float(amount)
        total += amount
        creditor = names.get(to_user, "Неизвестный")
        store_name = store or "Покупка"
        lines.append(
            f"• Вам нужно отдать <b>{amount:.2f} ₽</b> — {creditor}\n"
            f"  🧾 {store_name} · {created_at}"
        )

    lines.append("")
    lines.append(f"💰 <b>Всего: {total:.2f} ₽</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("my_debts"))
async def my_debts_command(message: Message):
    await show_my_debts(message)




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
# КНОПКА "ДОЛГИ" — ВЛОЖЕННОЕ МЕНЮ
# =========================================================

def debts_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💸 Все долги",
                    callback_data="debts_all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Мои долги",
                    callback_data="debts_mine"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="debts_back"
                )
            ],
        ]
    )


@dp.message(lambda message: message.text == "💸 Долги")
async def debts_button(message: Message):
    sent = await message.answer(
        "💸 <b>Долги</b>\n\nВыберите, что хотите посмотреть:",
        reply_markup=debts_menu_keyboard(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


async def delete_debts_menu(callback: CallbackQuery):
    # После выбора пункта убираем промежуточное сообщение с меню,
    # чтобы в чате не оставались старые кнопки.
    try:
        await callback.message.delete()
    except Exception:
        # Если Telegram не разрешил удалить сообщение, хотя бы убираем кнопки.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@dp.callback_query(lambda c: c.data == "debts_all")
async def debts_all_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    await show_debts(callback.message)


@dp.callback_query(lambda c: c.data == "debts_mine")
async def debts_mine_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer()
    await show_my_debts(callback.message)


@dp.callback_query(lambda c: c.data == "debts_back")
async def debts_back_callback(callback: CallbackQuery):
    await delete_debts_menu(callback)
    await callback.answer("Назад")


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
# КНОПКА "ОЧИСТИТЬ" — ВЛОЖЕННОЕ МЕНЮ
# =========================================================

def clear_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧹 Очистить долги",
                    callback_data="clear_menu_debts"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Очистить участников",
                    callback_data="clear_menu_members"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧾 Очистить список трат",
                    callback_data="clear_menu_expenses"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="clear_menu_back"
                )
            ],
        ]
    )


@dp.message(lambda message: message.text == "🧹 Очистить")
async def clear_menu_button(message: Message):
    if message.chat.type == "private":
        await message.answer("❌ Эту функцию нужно использовать в группе.")
        return

    sent = await message.answer(
        "🧹 <b>Очистить</b>\n\nВыберите, что хотите удалить:",
        reply_markup=clear_menu_keyboard(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


async def delete_clear_menu(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@dp.callback_query(lambda c: c.data == "clear_menu_back")
async def clear_menu_back_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer("Назад")


@dp.callback_query(lambda c: c.data == "clear_menu_debts")
async def clear_menu_debts_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_debts_confirmation(callback.message)


@dp.callback_query(lambda c: c.data == "clear_menu_members")
async def clear_menu_members_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_members_confirmation(callback.message)


@dp.callback_query(lambda c: c.data == "clear_menu_expenses")
async def clear_menu_expenses_callback(callback: CallbackQuery):
    await delete_clear_menu(callback)
    await callback.answer()
    await send_clear_expenses_confirmation(callback.message)


async def send_clear_debts_confirmation(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, очистить", callback_data="confirm_clear_debts"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_clear_debts")
            ]
        ]
    )
    sent = await message.answer(
        "⚠️ Вы уверены, что хотите удалить ВСЕ долги в этой группе?\n\n"
        "Это действие нельзя отменить.",
        reply_markup=keyboard
    )
    schedule_button_message_delete(sent)


async def send_clear_members_confirmation(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, очистить", callback_data="confirm_clear_members")
    builder.button(text="❌ Отмена", callback_data="cancel_clear_members")
    builder.adjust(1)
    sent = await message.answer(
        "⚠️ <b>Очистить всех участников?</b>\n\n"
        "Все участники этой группы будут удалены из списка.\n\n"
        "Чеки и долги при этом не удаляются.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


async def send_clear_expenses_confirmation(message: Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, очистить", callback_data="confirm_clear_expenses")
    builder.button(text="❌ Отмена", callback_data="cancel_clear_expenses")
    builder.adjust(1)
    sent = await message.answer(
        "⚠️ <b>Очистить список трат?</b>\n\n"
        "Будут удалены все чеки и ручные покупки этой группы.\n\n"
        "Долги и участники группы останутся.\n\n"
        "Это действие нельзя отменить.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    schedule_button_message_delete(sent)


# Старые текстовые кнопки оставляем для совместимости, если они где-то ещё используются.



@dp.callback_query(lambda callback: callback.data == "confirm_clear_debts")
async def confirm_clear_debts(callback: CallbackQuery):
    await clear_debts(callback.message.chat.id)

    await callback.message.delete()
    await callback.answer("Долги очищены")


@dp.callback_query(lambda callback: callback.data == "cancel_clear_debts")
async def cancel_clear_debts(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")

# =========================================================
# =========================================================
# ПОДТВЕРЖДЕНИЯ ОЧИСТКИ
# =========================================================

@dp.callback_query(lambda c: c.data == "confirm_clear_members")
async def confirm_clear_members(callback: CallbackQuery):
    await clear_members(callback.message.chat.id)
    await callback.message.delete()
    await callback.answer("Участники удалены")


@dp.callback_query(lambda c: c.data == "cancel_clear_members")
async def cancel_clear_members(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


@dp.callback_query(lambda c: c.data == "confirm_clear_expenses")
async def confirm_clear_expenses(callback: CallbackQuery):
    await clear_expenses(callback.message.chat.id)
    await callback.message.delete()
    await callback.answer("Траты удалены")


@dp.callback_query(lambda c: c.data == "cancel_clear_expenses")
async def cancel_clear_expenses(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


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

