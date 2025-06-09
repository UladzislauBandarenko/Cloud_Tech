import os
import json
import asyncio
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import psycopg2
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from cryptography.fernet import Fernet
import redis.asyncio as redis

# Load env variables
load_dotenv()

# FastAPI app
app = FastAPI()

# ENV
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB")

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_KEY = os.getenv('REDIS_KEY')

SERVICE_BUS_CONNECTION_STRING = os.getenv("SERVICE_BUS_CONNECTION_STRING")
LOAN_QUEUE = os.getenv("LOAN_QUEUE")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
fernet = Fernet(ENCRYPTION_KEY.encode())

# Redis client
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_KEY, ssl=True, decode_responses=True)

# PostgreSQL
def get_pg_connection():
    return psycopg2.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        sslmode="require"
    )

executor = ThreadPoolExecutor(max_workers=5)

async def db_execute(query: str, values: dict = None):
    def blocking():
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                conn.commit()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, blocking)

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

# Encryption
def encrypt(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()

# Message Queue
async def send_to_queue(payload: dict):
    async with ServiceBusClient.from_connection_string(SERVICE_BUS_CONNECTION_STRING) as client:
        sender = client.get_queue_sender(queue_name=LOAN_QUEUE)
        async with sender:
            msg = ServiceBusMessage(json.dumps(payload))
            await sender.send_messages(msg)

LOG_QUEUE = os.getenv("LOG_QUEUE")

async def send_log(message: str, level: str = "INFO"):
    log_payload = {
        "level": level,
        "message": message
    }
    async with ServiceBusClient.from_connection_string(SERVICE_BUS_CONNECTION_STRING) as client:
        sender = client.get_queue_sender(queue_name=LOG_QUEUE)
        async with sender:
            msg = ServiceBusMessage(json.dumps(log_payload))
            await sender.send_messages(msg)


# Pydantic model
class LoanRequest(BaseModel):
    user_id: int
    book_id: int

# Endpoint
@app.post("/loans")
async def create_loans(ln: LoanRequest):
    user_query = "SELECT id FROM users WHERE id = %(id)s"
    user = await db_fetch_one(user_query, {"id": ln.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    book_query = "SELECT id FROM books WHERE id = %(id)s"
    book = await db_fetch_one(book_query, {"id": ln.book_id})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
   
    insert_loan = """
        INSERT INTO loans (user_id, book_id)
        VALUES (%(user)s, %(book)s)
        RETURNING id
    """

    def get_ln_id():
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_loan, {
                    "user": ln.user_id,
                    "book": ln.book_id,
                })
                return cur.fetchone()[0]
    loop = asyncio.get_running_loop()
    ln_id = await loop.run_in_executor(executor, get_ln_id)


    # Send to books
    queue_payload = {
        "book_id": ln.book_id,
        "user_id": ln.user_id,
        "free": False
    }

    await send_log(f"Loan created: user={ln.user_id}, book={ln.book_id}")

    await send_to_queue(queue_payload)

    return {"loan_id": ln_id, "status": "completed"}

# Endpoint
@app.post("/loans/free")
async def free_loans(ln: LoanRequest):
    user_query = "SELECT id FROM users WHERE id = %(id)s"
    user = await db_fetch_one(user_query, {"id": ln.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    book_query = "SELECT id FROM books WHERE id = %(id)s"
    book = await db_fetch_one(book_query, {"id": ln.book_id})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
   
    # Send to books
    queue_payload = {
        "book_id": ln.book_id,
        "user_id": ln.user_id,
        "free": True
    }

    await send_log(f"Loan freed: user={ln.user_id}, book={ln.book_id}")

    await send_to_queue(queue_payload)

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    keys = await redis_client.dbsize()
    return {
        "service": "loan-service",
        "cached_keys": keys
    }

@app.on_event("startup")
async def startup():
    await redis_client.ping()

@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()
    executor.shutdown(wait=True)
    