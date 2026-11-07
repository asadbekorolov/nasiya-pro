from fastapi import APIRouter

from app.api.v1.endpoints import admin, agent, auth, health, store

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(store.router)
api_router.include_router(agent.router)
api_router.include_router(admin.router)
