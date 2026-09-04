import aiosqlite


DB_NAME = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                UNIQUE(chat_id, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                file_id TEXT,
                image_path TEXT,
                store TEXT,
                total REAL,
                processed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipt_members (
                receipt_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (receipt_id, user_id)
            )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS receipt_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER,
            name TEXT,
            price REAL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            from_user INTEGER,
            to_user INTEGER,
            receipt_id INTEGER,
            amount REAL,
            is_paid INTEGER DEFAULT 0
        )
        """)

        await db.commit()


async def add_group(chat_id: int, title: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO groups (chat_id, title)
            VALUES (?, ?)
        """, (chat_id, title))

        await db.commit()


async def add_member(
    chat_id: int,
    user_id: int,
    username: str | None,
    first_name: str
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO members
            (chat_id, user_id, username, first_name)
            VALUES (?, ?, ?, ?)
        """, (
            chat_id,
            user_id,
            username,
            first_name
        ))

        await db.commit()


async def get_members(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT user_id, username, first_name
            FROM members
            WHERE chat_id = ?
        """, (chat_id,))

        return await cursor.fetchall()


async def add_receipt(chat_id, user_id, file_id, image_path):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO receipts
            (chat_id, user_id, file_id, image_path)
            VALUES (?, ?, ?, ?)
        """, (
            chat_id,
            user_id,
            file_id,
            image_path
        ))

        await db.commit()

async def get_receipts(chat_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT image_path, created_at
            FROM receipts
            WHERE chat_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (chat_id,))

        return await cursor.fetchall()

async def get_my_receipts(chat_id: int, user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT image_path, created_at
            FROM receipts
            WHERE chat_id = ?
              AND user_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (chat_id, user_id))

        return await cursor.fetchall()

async def link_member_to_receipt(receipt_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO receipt_members
            (receipt_id, user_id)
            VALUES (?, ?)
        """, (receipt_id, user_id))

        await db.commit()


async def get_last_receipt(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id
            FROM receipts
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (chat_id,))

        return await cursor.fetchone()

async def get_receipt_path(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT image_path
            FROM receipts
            WHERE id=?
        """, (receipt_id,))

        row = await cursor.fetchone()

        return row[0]

async def add_item(receipt_id: int, name: str, price: float):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO receipt_items
        (receipt_id,name,price)
        VALUES(?,?,?)
        """,(receipt_id,name,price))

        await db.commit()


async def update_receipt_total(receipt_id:int,total:float,store:str):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        UPDATE receipts
        SET total=?, store=?, processed=1
        WHERE id=?
        """,(total,store,receipt_id))

        await db.commit()

async def add_debt(
    chat_id:int,
    from_user:int,
    to_user:int,
    receipt_id:int,
    amount:float
):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO debts
        (chat_id,from_user,to_user,receipt_id,amount)
        VALUES(?,?,?,?,?)
        """,(chat_id,from_user,to_user,receipt_id,amount))

        await db.commit()


async def get_debts(chat_id:int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
        SELECT from_user,to_user,amount
        FROM debts
        WHERE chat_id=? AND is_paid=0
        """,(chat_id,))

        return await cursor.fetchall()

async def clear_debts(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            DELETE FROM debts
            WHERE chat_id = ?
            """,
            (chat_id,)
        )

        await db.commit()

async def clear_members(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            DELETE FROM members
            WHERE chat_id = ?
            """,
            (chat_id,)
        )
        await db.commit()