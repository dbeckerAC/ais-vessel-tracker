from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.aisstream.client import AisStreamService
from app.api.routes import router
from app.config import Settings
from app.openwaters.client import OpenWatersService
from app.persistence.database import Database
from app.services.broadcaster import Broadcaster
from app.services.persistence_writer import PersistenceWriter
from app.services.position_ingestor import PositionIngestor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Services:
    settings: Settings
    database: Database
    writer: PersistenceWriter
    broadcaster: Broadcaster
    ingestor: PositionIngestor
    ais: AisStreamService
    openwaters: OpenWatersService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    database = Database(settings.database_url)
    await database.connect()
    await database.migrate()
    seeded = await database.seed_tracked_vessels(settings.vessels_config_path)
    if seeded:
        logger.info("seeded %s tracked vessels", seeded)

    broadcaster = Broadcaster()
    writer = PersistenceWriter(database)
    ingestor = PositionIngestor(
        database, writer, broadcaster, settings.history_sample_seconds
    )
    await ingestor.initialize()
    ais = AisStreamService(settings, database, writer, ingestor)
    openwaters = OpenWatersService(settings, database, ingestor)
    services = Services(
        settings, database, writer, broadcaster, ingestor, ais, openwaters
    )
    app.state.services = services

    writer_task = asyncio.create_task(writer.run(), name="persistence-writer")
    ais_task = asyncio.create_task(ais.run(), name="aisstream-client")
    openwaters_task = asyncio.create_task(
        openwaters.run(), name="openwaters-client"
    )
    try:
        yield
    finally:
        ais.stop()
        openwaters.stop()
        try:
            await asyncio.wait_for(openwaters_task, timeout=10)
        except asyncio.TimeoutError:
            openwaters_task.cancel()
            await asyncio.gather(openwaters_task, return_exceptions=True)
        try:
            await asyncio.wait_for(ais_task, timeout=10)
        except asyncio.TimeoutError:
            ais_task.cancel()
            await asyncio.gather(ais_task, return_exceptions=True)
        writer.stop()
        try:
            await asyncio.wait_for(writer_task, timeout=15)
        except asyncio.TimeoutError:
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
        await database.close()


app = FastAPI(title="AIS Track Archive", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=Settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.include_router(router)


STATIC_DIR = Path("/app/static")
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        requested = (STATIC_DIR / path).resolve()
        if path and requested.is_file() and STATIC_DIR.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return JSONResponse(
            {
                "name": "AIS Track Archive",
                "frontend": "not built",
                "docs": "/docs",
            }
        )
