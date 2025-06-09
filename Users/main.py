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

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password = REDIS_KEY, ssl=True, decode_responses=True)

SERVICE_BUS_CONNECTION_STRING = os.getenv("SERVICE_BUS_CONNECTION_STRING")
SERVICE_BUS_QUEUE = os.getenv("LOG_QUEUE")

async def send_log(message: str):
    async with ServiceBusClient.from_connection_string(SERVICE_BUS_CONNECTION_STRING) as client:
        sender = client.get_queue_sender(queue_name=SERVICE_BUS_QUEUE)
        async with sender:
            await sender.send_messages(ServiceBusMessage(message))


# Encryption helpers
def encrypt(text: str) -> str:
    return fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()

# REST API route to get user by id
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    cache_key = f"user:{user_id}"
    cached_user = await redis_client.get(cache_key)
    if cached_user:
        user = json.loads(cached_user)
        user["email"] = decrypt(user["email"])
        return user

    query = "SELECT id, name, email FROM users WHERE id = %(user_id)s"
    user = await db_fetch_one(query=query, values={"user_id": user_id})
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user["email"] = decrypt(user["email"])

    # Cache for 5 minutes
    user_to_cache = user.copy()
    user_to_cache["email"] = encrypt(user["email"])
    await redis_client.setex(cache_key, 300, json.dumps(user_to_cache))
    await send_log(f"User {user_id} retrieved successfully")

    return user

# GraphQL setup
class User(graphene.ObjectType):
    id = graphene.Int()
    name = graphene.String()
    email = graphene.String()

class Query(graphene.ObjectType):
    users = graphene.List(User)

    async def resolve_users(root, info):
        cached = await redis_client.get("users:all")
        if cached:
            users = json.loads(cached)
            for u in users:
                u["email"] = decrypt(u["email"])
            return [User(**u) for u in users]

        query = "SELECT id, name, email FROM users"
        users = await db_fetch_all(query=query)

        for u in users:
            u["email"] = decrypt(u["email"])

        await redis_client.setex("users:all", 300, json.dumps(users))
        return [User(**u) for u in users]

schema = graphene.Schema(query=Query)

app.mount("/graphql", GraphQLApp(schema=schema, on_get=make_graphiql_handler()))


@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    keys = await redis_client.dbsize()
    return {
        "service": "user-service",
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