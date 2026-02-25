import asyncio
import logging
import asyncpg
import os
import csv
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery
)
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------

BOT_TOKEN = "8524579857:AAEfQUUJt8c33GXVXaq4_F6OZ2HYyZfLLPs"
PROVIDER_TOKEN = "7490307358:TEST:ImRx8Dbz36A0KLLx"
ADMIN_ID = 867025267
CHANNEL_ID = "@befit_products"

DATABASE_URL = os.getenv("postgresql://postgres:jjYcrnhdIHqpqdnEiBEDChBjauBRvFMx@postgres.railway.internal:5432/railway")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db = None

# ---------------- CSV INIT ----------------

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

# ---------------- DATABASE INIT ----------------

async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    async with db.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT,
            description TEXT,
            price INTEGER,
            photo_id TEXT
        )
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

# ---------------- FSM ----------------

class AddProduct(StatesGroup):
    photo = State()
    name = State()
    description = State()
    price = State()

# ---------------- START ----------------

@dp.message(Command("start"))
async def start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Catalog", callback_data="catalog")],
        [InlineKeyboardButton(text="🛒 Cart", callback_data="cart")],
        [InlineKeyboardButton(text="📦 Orders", callback_data="orders")]
    ])

    await message.answer(
        f"Hello {message.from_user.first_name}",
        reply_markup=keyboard
    )

# ---------------- ADD PRODUCT ----------------

@dp.message(Command("addproduct"))
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not admin")

    await message.answer("📷 Send photo")
    await state.set_state(AddProduct.photo)


@dp.message(AddProduct.photo)
async def add_product_photo(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("Name?")
    await state.set_state(AddProduct.name)


@dp.message(AddProduct.name)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Description?")
    await state.set_state(AddProduct.description)


@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("Price in USD?")
    await state.set_state(AddProduct.price)


@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    try:
        price = int(float(message.text) * 100)
    except:
        return await message.answer("Enter valid number")

    data = await state.get_data()

    async with db.acquire() as conn:
        await conn.execute("""
            INSERT INTO products (name, description, price, photo_id)
            VALUES ($1, $2, $3, $4)
        """, data["name"], data["description"], price, data["photo_id"])

    # Публикация в канал
    await bot.send_photo(
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

    await message.answer("✅ Product added!")
    await state.clear()

# ---------------- CATALOG ----------------

@dp.callback_query(F.data == "catalog")
async def catalog(call: CallbackQuery):
    async with db.acquire() as conn:
        products = await conn.fetch("SELECT * FROM products")

    if not products:
        return await call.message.answer("No products yet")

    for product in products:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🛒 Buy {product['price']/100}$",
                callback_data=f"buy_{product['id']}"
            )]
        ])

        await call.message.answer_photo(
            product["photo_id"],
            caption=f"{product['name']}\n\n{product['description']}",
            reply_markup=keyboard
        )

# ---------------- BUY ----------------

@dp.callback_query(F.data.startswith("buy_"))
async def buy(call: CallbackQuery):
    product_id = int(call.data.split("_")[1])

    async with db.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT * FROM products WHERE id=$1",
            product_id
        )

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=product["name"],
        description=product["description"],
        payload="payment",
        provider_token=PROVIDER_TOKEN,
        currency="USD",
        prices=[LabeledPrice(
            label=product["name"],
            amount=product["price"]
        )]
    )

# ---------------- PAYMENT ----------------

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    username = message.from_user.username or "NoUsername"
    user_id = message.from_user.id
    amount = message.successful_payment.total_amount
    payment_id = message.successful_payment.telegram_payment_charge_id

    # запись в PostgreSQL
    async with db.acquire() as conn:
        await conn.execute("""
            INSERT INTO orders (username, user_id, amount, payment_id)
            VALUES ($1, $2, $3, $4)
        """, username, user_id, amount, payment_id)

    # экспорт в CSV
    with open("orders.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now(),
            username,
            user_id,
            amount / 100,
            payment_id
        ])

    await message.answer("✅ Payment successful!")

# ---------------- RUN ----------------

async def main():
    init_csv()
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())