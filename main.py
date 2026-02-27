import asyncio
import logging
import asyncpg
import os
import csv
from datetime import datetime
from aiogram.types import FSInputFile

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
PROVIDER_TOKEN = "7490307358:TEST:ImRx8Dbz36A0KLLx"
ADMIN_ID = 867025267
CHANNEL_ID = "@befit_products"

DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db = None

# ================= CSV INIT =================

def init_csv():
    if not os.path.exists("orders.csv"):
        with open("orders.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Date",
                "Username",
                "User ID",
                "Amount (USD)",
                "Payment ID"
            ])

# ================= DATABASE INIT =================

async def init_db():
    global db

    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not set!")

    db = await asyncpg.create_pool(DATABASE_URL)

    async with db.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT,
            description TEXT,
            price INTEGER,
            photo_id TEXT,
            channel_message_id BIGINT
        )
        """)

        # Добавляем колонку если уже существует старая таблица без неё
        await conn.execute("""
            ALTER TABLE products
            ADD COLUMN IF NOT EXISTS channel_message_id BIGINT
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id SERIAL PRIMARY KEY,
            username TEXT,
            product_id INTEGER
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            username TEXT,
            user_id BIGINT,
            amount INTEGER,
            payment_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """)

# ================= FSM =================

class AddProduct(StatesGroup):
    photo = State()
    name = State()
    description = State()
    price = State()

# ================= START =================

@dp.message(Command("start"))
async def start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛍 Catalog", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Cart", callback_data="cart")
        ],
        [
            InlineKeyboardButton(text="📦 Orders", callback_data="orders"),
            InlineKeyboardButton(text="🆘 Support", callback_data="support")
        ],
        [
            InlineKeyboardButton(
                text="📢 Our Channel",
                url="https://t.me/befit_products"
            )
        ]
    ])

    await message.answer(
        f"Hello {message.from_user.first_name}, choose:",
        reply_markup=keyboard
    )

# ================= ADD PRODUCT =================

@dp.message(Command("addproduct"))
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not admin")

    await message.answer("📷 Send product photo")
    await state.set_state(AddProduct.photo)

@dp.message(AddProduct.photo)
async def add_product_photo(message: Message, state: FSMContext):
    if not message.photo:
        return await message.answer("Send photo!")

    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("Send product name")
    await state.set_state(AddProduct.name)

@dp.message(AddProduct.name)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Send description")
    await state.set_state(AddProduct.description)

@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("Send price in USD")
    await state.set_state(AddProduct.price)

@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    try:
        price = int(float(message.text) * 100)
    except:
        return await message.answer("Enter valid number")

    data = await state.get_data()

    # Сначала публикуем в канал, получаем message_id
    channel_msg = await bot.send_photo(
        chat_id=CHANNEL_ID,
        photo=data["photo_id"],
        caption=f"""
🔥 NEW PRODUCT

🛍 {data['name']}

{data['description']}

💰 Price: {price/100}$

🛒 @befitProduct_bot
"""
    )

    # Сохраняем товар вместе с channel_message_id за один запрос
    async with db.acquire() as conn:
        await conn.execute("""
            INSERT INTO products (name, description, price, photo_id, channel_message_id)
            VALUES ($1, $2, $3, $4, $5)
        """, data["name"], data["description"], price, data["photo_id"], channel_msg.message_id)

    await message.answer("✅ Product added & published!")
    await state.clear()


# ================= EXPORT TO CSV =================

@dp.message(Command("export"))
async def export_orders(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not admin")

    async with db.acquire() as conn:
        orders = await conn.fetch("""
            SELECT username, user_id, amount, payment_id, created_at
            FROM orders
            ORDER BY created_at DESC
        """)

    if not orders:
        return await message.answer("No orders yet.")

    filename = "export_orders.csv"

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Username", "User ID", "Amount (USD)", "Payment ID"])

        for order in orders:
            writer.writerow([
                order["created_at"],
                order["username"],
                order["user_id"],
                order["amount"] / 100,
                order["payment_id"]
            ])

    await message.answer_document(FSInputFile(filename))

# ================= CATALOG =================

@dp.callback_query(F.data == "catalog")
async def catalog(call: CallbackQuery):
    async with db.acquire() as conn:
        products = await conn.fetch("SELECT * FROM products")

    if not products:
        return await call.message.answer("No products yet.")

    is_admin = call.from_user.id == ADMIN_ID

    for product in products:
        # Кнопка "Добавить в корзину" для всех
        buttons = [[InlineKeyboardButton(
            text=f"🛒 Add to cart {product['price']/100}$",
            callback_data=f"buy_{product['id']}"
        )]]

        # Кнопка удаления — только для админа
        if is_admin:
            buttons.append([InlineKeyboardButton(
                text="🗑 Delete product",
                callback_data=f"delete_{product['id']}"
            )])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await call.message.answer_photo(
            product["photo_id"],
            caption=f"{product['name']}\n\n{product['description']}",
            reply_markup=keyboard
        )

# ================= DELETE PRODUCT =================

@dp.callback_query(F.data.startswith("delete_"))
async def delete_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ You are not admin", show_alert=True)

    product_id = int(call.data.split("_")[1])

    async with db.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name, channel_message_id FROM products WHERE id=$1", product_id
        )

        if not product:
            return await call.answer("Product not found.", show_alert=True)

        # Удаляем товар из корзин пользователей
        await conn.execute(
            "DELETE FROM cart WHERE product_id=$1", product_id
        )

        # Удаляем сам товар
        await conn.execute(
            "DELETE FROM products WHERE id=$1", product_id
        )

    # Удаляем сообщение из канала
    if product["channel_message_id"]:
        try:
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=product["channel_message_id"])
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение из канала: {e}")

    await call.message.delete()
    await call.answer(f"✅ '{product['name']}' deleted.", show_alert=True)

# ================= ADD TO CART =================

@dp.callback_query(F.data.startswith("buy_"))
async def add_to_cart(call: CallbackQuery):
    product_id = int(call.data.split("_")[1])
    username = call.from_user.username or str(call.from_user.id)

    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO cart (username, product_id) VALUES ($1, $2)",
            username,
            product_id
        )

    await call.answer("Added to cart 🛒", show_alert=True)

# ================= VIEW CART =================

@dp.callback_query(F.data == "cart")
async def view_cart(call: CallbackQuery):
    username = call.from_user.username or str(call.from_user.id)

    async with db.acquire() as conn:
        items = await conn.fetch("""
            SELECT p.name, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.username=$1
        """, username)

    if not items:
        return await call.message.answer("🛒 Cart is empty.")

    text = "🛒 Your cart:\n\n"
    total = 0

    for item in items:
        text += f"{item['name']} - {item['price']/100}$\n"
        total += item["price"]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💳 Pay {total/100}$",
            callback_data="pay_cart"
        )]
    ])

    await call.message.answer(text, reply_markup=keyboard)

# ================= PAY CART (CASH) =================

@dp.callback_query(F.data == "pay_cart")
async def pay_cart(call: CallbackQuery):
    username = call.from_user.username or str(call.from_user.id)
    user_id = call.from_user.id

    async with db.acquire() as conn:
        items = await conn.fetch("""
            SELECT p.name, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.username=$1
        """, username)

    if not items:
        return

    total = sum(item["price"] for item in items)
    order_lines = "\n".join(f"• {item['name']} — {item['price']/100}$" for item in items)

    # Сообщение пользователю
    await call.message.answer(
        f"💵 <b>Оплата наличными</b>\n\n"
        f"Ваш заказ:\n{order_lines}\n\n"
        f"💰 <b>Итого: {total/100}$</b>\n\n"
        f"Свяжитесь с нами для оплаты: @imdenya\n"
        f"Укажите ваш username при обращении.",
        parse_mode="HTML"
    )

    # Уведомление админу
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🛎 <b>Новый заказ!</b>\n\n"
             f"👤 Пользователь: @{username} (ID: {user_id})\n\n"
             f"🛍 Товары:\n{order_lines}\n\n"
             f"💰 Сумма: {total/100}$",
        parse_mode="HTML"
    )

    # Сохраняем заказ в БД
    async with db.acquire() as conn:
        await conn.execute("""
            INSERT INTO orders (username, user_id, amount, payment_id)
            VALUES ($1, $2, $3, $4)
        """, username, user_id, total, f"CASH-{user_id}-{int(datetime.now().timestamp())}")

        await conn.execute(
            "DELETE FROM cart WHERE username=$1", username
        )

    # Сохраняем в CSV
    with open("orders.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now(),
            username,
            user_id,
            total / 100,
            f"CASH-{user_id}"
        ])

# ================= SUPPORT =================

@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery):
    await call.message.answer("Contact support: @imdenya")

# ================= ORDERS =================

@dp.callback_query(F.data == "orders")
async def view_orders(call: CallbackQuery):
    username = call.from_user.username or str(call.from_user.id)

    async with db.acquire() as conn:
        orders = await conn.fetch("""
            SELECT amount, payment_id, created_at
            FROM orders
            WHERE username=$1
            ORDER BY created_at DESC
        """, username)

    if not orders:
        return await call.message.answer("No orders yet.")

    text = "📦 Your orders:\n\n"

    for order in orders:
        text += f"💰 {order['amount']/100}$ | ID: {order['payment_id']}\n"

    await call.message.answer(text)



# ================= RUN =================

async def main():
    init_csv()
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())