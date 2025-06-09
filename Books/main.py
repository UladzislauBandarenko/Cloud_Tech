import os
import base64
import json
import psycopg2
import asyncio
from fastapi import FastAPI, HTTPException
import redis.asyncio as redis
from cryptography.fernet import Fernet
import graphene
from starlette_graphene3 import GraphQLApp, make_graphiql_handler
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage

load_dotenv()

app = FastAPI()

# Load environment variables
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB")

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_KEY = os.getenv('REDIS_KEY')

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
fernet = Fernet(ENCRYPTION_KEY.encode())
SERVICE_BUS_CONNECTION_STRING = os.getenv("SERVICE_BUS_CONNECTION_STRING")
LOAN_QUEUE = os.getenv("LOAN_QUEUE")
LOG_QUEUE = os.getenv("LOG_QUEUE")

# Create synchronous psycopg2 connection (will be run in thread pool)
def get_pg_connection():
    return psycopg2.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        sslmode="require"
    )

# Helper to run blocking DB calls in executor
executor = ThreadPoolExecutor(max_workers=5)

async def db_fetch_one(query: str, values: dict):
    def blocking():
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                row = cur.fetchone()
                if row is None:
                    return None
                colnames = [desc[0] for desc in cur.description]
                return dict(zip(colnames, row))
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, blocking)

async def db_fetch_all(query: str):
    def blocking():
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
                colnames = [desc[0] for desc in cur.description]
                return [dict(zip(colnames, row)) for row in rows]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, blocking)

def mark_book(book_id: int, freed: bool):
    update_query = """
        UPDATE books
        SET available = %s
        WHERE id = %s;
    """
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(update_query, (book_id, freed))
                conn.commit()
                print(f"[✓] Marked book id={book_id} as unavailable")
    except Exception as e:
        print(f"[ERROR] Failed to mark book as unavailable: {e}")

async def send_log(message: str, level: str = "INFO"):
    async with ServiceBusClient.from_connection_string(SERVICE_BUS_CONNECTION_STRING) as client:
        sender = client.get_queue_sender(queue_name=LOG_QUEUE)
        async with sender:
            msg = ServiceBusMessage(json.dumps({
                "level": level,
                "message": message
            }))
            await sender.send_messages(msg)

async def process_loan(msg):
    try:
        data = json.loads(str(msg))
        book_id = data["book_id"]
        freed = data["free"]

        await mark_book(book_id, freed)
        await send_log(f"Book loaned : {book_id}")

    except Exception as e:
        await send_log(f"Error processing transaction: {e}", "ERROR")

async def consume():
    async with ServiceBusClient.from_connection_string(SERVICE_BUS_CONNECTION_STRING) as client:
        receiver = client.get_queue_receiver(queue_name=LOAN_QUEUE)
        async with receiver:
            async for msg in receiver:
                await process_loan(msg)
                await receiver.complete_message(msg)

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password = REDIS_KEY, ssl=True, decode_responses=True)

# Encryption helpers
def encrypt(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()

@app.get("/books")
async def get_books():
    query = "SELECT * FROM books ORDER BY id;"
    books = await db_fetch_all(query)
    return books

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    keys = await redis_client.dbsize()
    return {
        "service": "books-service",
        "cached_keys": keys
    }

@app.on_event("startup")
async def startup():
    # just test redis connection
    await redis_client.ping()

@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()
    executor.shutdown(wait=True)
    