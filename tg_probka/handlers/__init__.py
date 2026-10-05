from aiogram import Router

from tg_probka.handlers import admin, errors, user


def build_root_router() -> Router:
    r = Router()
    r.include_router(user.router)
    r.include_router(admin.router)
    r.include_router(errors.router)
    return r