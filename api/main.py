import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from callback_dispatch import start_all_dispatchers, stop_all_dispatchers
from config import DEV_CALLBACK_RECEIVER
from database import init_db
from routers import dev_callback_receiver, tenant_auth, tenant_callbacks, tenant_messages, tenants


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        await start_all_dispatchers()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize app: {e}", exc_info=True)
        raise
    yield
    try:
        await stop_all_dispatchers()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error during shutdown: {e}", exc_info=True)


app = FastAPI(title="Grey TG API", version="0.1.0", lifespan=lifespan)

# CORS middleware - must be added FIRST, before routers
# This ensures CORS headers are added to all responses, including errors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

app.include_router(tenants.router)
app.include_router(tenant_auth.router)
app.include_router(tenant_messages.router)
app.include_router(tenant_callbacks.router)
if DEV_CALLBACK_RECEIVER:
    app.include_router(dev_callback_receiver.router)


@app.get("/")
def root():
    return {"message": "Grey TG API", "docs": "/docs"}


@app.get("/health")
def health():
    return {"ok": True}


