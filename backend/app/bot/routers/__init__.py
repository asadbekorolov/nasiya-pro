from aiogram import Router

from app.bot.routers import start


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start.create_router())
    return root
