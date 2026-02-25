import asyncio
import logging
import sqlite3

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

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE ----------------

def init_db():
    conn = sqlite3.connect("befit.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        price INTEGER,
        photo_id TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        product_id INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        product TEXT,
        price INTEGER,
        is_paid INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()

init_db()

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
        [
            InlineKeyboardButton(text="🛍 Catalog", callback_data="catalog"),
            InlineKeyboardButton(text="🛒 Cart", callback_data="cart"),
        ],
        [
            InlineKeyboardButton(text="📦 Orders", callback_data="orders"),
            InlineKeyboardButton(text="🆘 Support", callback_data="support"),
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

# ---------------- ADD PRODUCT ----------------

@dp.message(Command("addproduct"))
async def add_product_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not admin")

    await message.answer("📷 Send product photo")
    await state.set_state(AddProduct.photo)


@dp.message(AddProduct.photo)
async def add_product_photo(message: Message, state: FSMContext):
    if not message.photo:
        return await message.answer("Send a photo!")

    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("📝 Send product name")
    await state.set_state(AddProduct.name)


@dp.message(AddProduct.name)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📄 Send description")
    await state.set_state(AddProduct.description)


@dp.message(AddProduct.description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("💰 Send price in dollars")
    await state.set_state(AddProduct.price)


@dp.message(AddProduct.price)
async def add_product_price(message: Message, state: FSMContext):
    try:
        price = int(float(message.text) * 100)
    except:
        return await message.answer("Enter valid number")

    data = await state.get_data()

    conn = sqlite3.connect("befit.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO products (name, description, price, photo_id)
        VALUES (?, ?, ?, ?)
    """, (
        data["name"],
        data["description"],
        price,
        data["photo_id"]
    ))

    conn.commit()
    conn.close()

    # Публикация в канал
    await bot.send_photo(
        chat_id=CHANNEL_ID,
        photo=data["photo_id"],
        caption=f"""
🔥 NEW PRODUCT

🛍 {data['name']}

{data['description']}

💰 Price: {price/100}$

🛒 to make a purchase, write here @befitProduct_bot
"""
    )

    await message.answer("✅ Product added and published!")
    await state.clear()

# ---------------- CALLBACKS ----------------

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    await call.answer()
    username = call.from_user.username or str(call.from_user.id)

    # CATALOG
    if call.data == "catalog":
        conn = sqlite3.connect("befit.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, description, price, photo_id FROM products")
        products = cursor.fetchall()
        conn.close()

        if not products:
            return await call.message.answer("No products yet.")

        for product_id, name, description, price, photo_id in products:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"🛒 Add to cart {price/100}$",
                    callback_data=f"addcart_{product_id}"
                )]
            ])

            await call.message.answer_photo(
                photo=photo_id,
                caption=f"{name}\n\n{description}\n\nPrice: {price/100}$",
                reply_markup=keyboard
            )

    # ADD TO CART
    elif call.data.startswith("addcart_"):
        product_id = call.data.split("_")[1]

        conn = sqlite3.connect("befit.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO cart (username, product_id) VALUES (?, ?)",
            (username, product_id)
        )
        conn.commit()
        conn.close()

        await call.message.answer("🛒 Added to cart!")

    # VIEW CART
    elif call.data == "cart":
        conn = sqlite3.connect("befit.db")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.name, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.username=?
        """, (username,))

        items = cursor.fetchall()
        conn.close()

        if not items:
            return await call.message.answer("🛒 Cart is empty.")

        text = "🛒 Your cart:\n\n"
        total = 0

        for name, price in items:
            text += f"{name} - {price/100}$\n"
            total += price

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💳 Pay {total/100}$",
                callback_data="pay_cart"
            )]
        ])

        await call.message.answer(text, reply_markup=keyboard)

    # PAY CART
    elif call.data == "pay_cart":
        conn = sqlite3.connect("befit.db")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.name, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.username=?
        """, (username,))

        items = cursor.fetchall()
        conn.close()

        if not items:
            return

        prices = [LabeledPrice(label=name, amount=price) for name, price in items]

        await bot.send_invoice(
            chat_id=call.message.chat.id,
            title="Cart payment",
            description="Payment for products",
            payload="cart_payment",
            provider_token=PROVIDER_TOKEN,
            currency="USD",
            prices=prices
        )

    # ORDERS
    elif call.data == "orders":
        conn = sqlite3.connect("befit.db")
        cursor = conn.cursor()

        cursor.execute(
            "SELECT product, is_paid FROM orders WHERE username=?",
            (username,)
        )

        orders = cursor.fetchall()
        conn.close()

        if not orders:
            return await call.message.answer("No orders yet.")

        text = "📦 Orders:\n\n"
        for product, paid in orders:
            status = "✅ Paid" if paid else "⏳ Waiting"
            text += f"{product} - {status}\n"

        await call.message.answer(text)

    elif call.data == "support":
        await call.message.answer("Contact support: @imdenya")

# ---------------- PAYMENT ----------------

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    username = message.from_user.username or str(message.from_user.id)

    conn = sqlite3.connect("befit.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO orders (username, product, price, is_paid)
        VALUES (?, ?, ?, 1)
    """, (
        username,
        "Cart payment",
        message.successful_payment.total_amount
    ))

    cursor.execute("DELETE FROM cart WHERE username=?", (username,))

    conn.commit()
    conn.close()

    await message.answer("✅ Payment successful!")

# ---------------- RUN ----------------

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())