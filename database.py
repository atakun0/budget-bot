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
                payer_id INTEGER,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Миграции для уже существующей базы.
        try:
            await db.execute("ALTER TABLE receipts ADD COLUMN payer_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE receipts ADD COLUMN category TEXT")
        except Exception:
            pass

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
            SELECT r.id, r.image_path, r.created_at,
                   r.user_id, m.username, m.first_name,
                   r.store, r.total, r.payer_id
            FROM receipts r
            LEFT JOIN members m
              ON m.chat_id = r.chat_id AND m.user_id = r.user_id
            WHERE r.chat_id = ?
            ORDER BY r.created_at DESC
            LIMIT 10
        """, (chat_id,))
        return await cursor.fetchall()


async def get_receipt_items(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT name, price
            FROM receipt_items
            WHERE receipt_id = ?
            ORDER BY id ASC
        """, (receipt_id,))
        return await cursor.fetchall()


async def get_my_receipts(chat_id: int, user_id: int):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
            SELECT id, created_at
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

async def set_receipt_payer(receipt_id: int, payer_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE receipts
            SET payer_id = ?
            WHERE id = ?
        """, (payer_id, receipt_id))
        await db.commit()


async def get_receipt_participants(receipt_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT m.user_id, m.username, m.first_name
            FROM receipt_members rm
            JOIN members m ON m.user_id = rm.user_id
            JOIN receipts r
              ON r.id = rm.receipt_id
             AND m.chat_id = r.chat_id
            WHERE rm.receipt_id = ?
            ORDER BY m.first_name ASC
        """, (receipt_id,))
        return await cursor.fetchall()


async def add_item(receipt_id: int, name: str, price: float):
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO receipt_items
        (receipt_id,name,price)
        VALUES(?,?,?)
        """,(receipt_id,name,price))

        await db.commit()


async def update_receipt_total(
    receipt_id: int,
    total: float,
    store: str,
    category: str | None = None
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            UPDATE receipts
            SET total=?, store=?, category=?, processed=1
            WHERE id=?
            """,
            (total, store, category, receipt_id)
        )
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