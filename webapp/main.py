from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from math import floor
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.models import DollarTransaction, GambleMinesGame, GambleUserStats, User  # noqa: E402

app = FastAPI(title="Qimor - Mines", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.urandom(32),
    session_cookie="qimor_session",
    max_age=2592000,  # 30 days
)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))

# ─── Constants ──────────────────────────────────────────────────────────────
GRID_SIZE = 36
GRID_WIDTH = 6
SOLO_MINE_COUNT = 8
MIN_BET = 10
DAILY_WIN_LIMIT = 50_000
COOLDOWN_SECONDS = 20
HOUSE_PAYOUT_FACTOR = 0.92

VISIBLE_MULTIPLIERS = {
    0: 1.00, 1: 1.04, 2: 1.12, 3: 1.28, 4: 1.65, 5: 2.20,
    6: 3.00, 7: 4.10, 8: 5.40, 9: 7.00, 10: 9.00, 11: 11.00,
    12: 12.50, 13: 13.50, 14: 14.20, 15: 15.00,
}

# ─── Helpers ────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _solo_multiplier(opened_count: int) -> float:
    return VISIBLE_MULTIPLIERS.get(opened_count, 15.0)


def _solo_payout(bet: int, opened_count: int) -> int:
    return floor(bet * _solo_multiplier(opened_count) * HOUSE_PAYOUT_FACTOR)


def _new_solo_state(user_id: int, name: str) -> dict:
    mines = sorted(secrets.SystemRandom().sample(range(GRID_SIZE), SOLO_MINE_COUNT))
    return {
        "mode": "solo",
        "players": [user_id],
        "turn": user_id,
        "mines": mines,
        "solo_opened": [],
        "names": {str(user_id): name},
    }


async def get_db():
    async with SessionLocal() as session:
        yield session


# ─── Auth helpers ───────────────────────────────────────────────────────────
def get_current_user_id(request: Request) -> int | None:
    return request.session.get("user_id")


def require_user(request: Request) -> int:
    user_id = get_current_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user_id


# ─── Pages ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if get_current_user_id(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request, telegram_id: int = Form(...), name: str = Form("")):
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                display_name=name or "Player",
                dollar=0,
                diamonds=0,
            )
            session.add(user)
            await session.commit()
        elif name and user.display_name != name:
            user.display_name = name
            await session.commit()
    request.session["user_id"] = telegram_id
    request.session["user_name"] = name or user.display_name
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = require_user(request)
    user = (
        await db.execute(select(User).where(User.telegram_id == user_id))
    ).scalar_one_or_none()
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/"})

    # Weekly top
    since = _utcnow() - timedelta(days=7)
    winner_expr = func.coalesce(GambleMinesGame.winner_telegram_id, GambleMinesGame.user_telegram_id).label("winner_id")
    top_rows = (
        await db.execute(
            select(
                winner_expr,
                func.coalesce(func.sum(GambleMinesGame.payout), 0).label("total_won"),
                func.count(GambleMinesGame.id).label("games_count"),
            )
            .where(
                GambleMinesGame.status == "cashed",
                GambleMinesGame.payout > 0,
                GambleMinesGame.ended_at.is_not(None),
                GambleMinesGame.ended_at >= since,
            )
            .group_by(winner_expr)
            .order_by(desc("total_won"))
            .limit(10)
        )
    ).all()

    top = []
    for idx, row in enumerate(top_rows, 1):
        u = (
            await db.execute(select(User).where(User.telegram_id == int(row.winner_id)))
        ).scalar_one_or_none()
        top.append({
            "rank": idx,
            "name": u.display_name if u else str(row.winner_id),
            "won": int(row.total_won or 0),
            "games": int(row.games_count or 0),
        })

    # Recent history (last 20)
    history_rows = (
        await db.execute(
            select(GambleMinesGame)
            .where(
                GambleMinesGame.user_telegram_id == user_id,
                GambleMinesGame.status.in_(["cashed", "lost"]),
            )
            .order_by(GambleMinesGame.ended_at.desc())
            .limit(20)
        )
    ).scalars().all()

    history = []
    for g in history_rows:
        history.append({
            "id": g.id,
            "bet": int(g.bet or 0),
            "payout": int(g.payout or 0),
            "status": g.status,
            "kind": g.game_kind,
            "ended_at": g.ended_at.strftime("%Y-%m-%d %H:%M") if g.ended_at else "-",
        })

    # Stats
    stats = (
        await db.execute(select(GambleUserStats).where(GambleUserStats.user_telegram_id == user_id))
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "top": top,
            "history": history,
            "stats": stats,
        },
    )


# ─── Mines Game ───────────────────────────────────────────────────────────────
@app.get("/mines", response_class=HTMLResponse)
async def mines_page(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = require_user(request)
    user = (
        await db.execute(select(User).where(User.telegram_id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/"})

    # Check active game
    active = (
        await db.execute(
            select(GambleMinesGame)
            .where(
                GambleMinesGame.user_telegram_id == user_id,
                GambleMinesGame.status.in_(["waiting", "active"]),
            )
            .order_by(GambleMinesGame.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    game_data = None
    if active and active.status == "active" and active.game_kind == "solo":
        state = json.loads(active.mines_json or "{}")
        opened = set(state.get("solo_opened", []))
        mines = set(state.get("mines", []))
        cells = []
        for i in range(GRID_SIZE):
            cells.append({
                "index": i,
                "opened": i in opened,
                "is_mine": i in mines if active.status == "lost" else None,
            })
        game_data = {
            "id": active.id,
            "bet": int(active.bet or 0),
            "payout": int(active.payout or 0),
            "multiplier": float(active.multiplier or 1.0),
            "opened_count": len(opened),
            "cells": cells,
            "status": active.status,
        }

    return templates.TemplateResponse(
        "mines.html",
        {
            "request": request,
            "user": user,
            "game": game_data,
            "min_bet": MIN_BET,
        },
    )


@app.post("/mines/start")
async def mines_start(request: Request, bet: int = Form(...), db: AsyncSession = Depends(get_db)):
    user_id = require_user(request)
    if bet < MIN_BET:
        return JSONResponse({"ok": False, "error": f"Minimal stavka: {MIN_BET}$"})

    async with db.begin():
        user = (
            await db.execute(select(User).where(User.telegram_id == user_id).with_for_update())
        ).scalar_one_or_none()
        if user is None:
            return JSONResponse({"ok": False, "error": "User topilmadi."})

        # Check existing active game
        active = (
            await db.execute(
                select(GambleMinesGame)
                .where(
                    GambleMinesGame.user_telegram_id == user_id,
                    GambleMinesGame.status.in_(["waiting", "active"]),
                )
            )
        ).scalar_one_or_none()
        if active:
            return JSONResponse({"ok": False, "error": "Sizda davom etayotgan o'yin bor."})

        # Cooldown
        stats = (
            await db.execute(select(GambleUserStats).where(GambleUserStats.user_telegram_id == user_id))
        ).scalar_one_or_none()
        now = _utcnow()
        if stats and stats.last_started_at:
            last = stats.last_started_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last + timedelta(seconds=COOLDOWN_SECONDS) > now:
                wait = int((last + timedelta(seconds=COOLDOWN_SECONDS) - now).total_seconds())
                return JSONResponse({"ok": False, "error": f"Cooldown: {wait}s"})

        if int(user.dollar or 0) < bet:
            return JSONResponse({"ok": False, "error": "Balans yetarli emas."})

        user.dollar = int(user.dollar or 0) - bet
        state = _new_solo_state(user_id, user.display_name or "Player")
        game = GambleMinesGame(
            user_telegram_id=user_id,
            opponent_telegram_id=None,
            winner_telegram_id=None,
            chat_id=0,
            bet=bet,
            mine_count=SOLO_MINE_COUNT,
            mines_json=json.dumps(state, ensure_ascii=False),
            opened_json=json.dumps({"picks": {}, "order": []}, ensure_ascii=False),
            status="active",
            game_kind="solo",
            multiplier=1.0,
            payout=0,
            token=secrets.token_hex(5),
            last_action_at=now,
        )
        db.add(game)
        if stats is None:
            stats = GambleUserStats(user_telegram_id=user_id, total_bet=bet, last_started_at=now)
            db.add(stats)
        else:
            stats.total_bet = int(stats.total_bet or 0) + bet
            stats.last_started_at = now

        db.add(
            DollarTransaction(
                user_telegram_id=user_id,
                user_name=(user.display_name or "User")[:255],
                amount=-bet,
                balance_after=int(user.dollar or 0),
                action="web_gamble_solo_bet",
                note=f"Web mines stavka",
            )
        )
        await db.flush()
        game_id = int(game.id)

    cells = [{"index": i, "opened": False, "is_mine": None} for i in range(GRID_SIZE)]
    return JSONResponse({
        "ok": True,
        "game_id": game_id,
        "bet": bet,
        "cells": cells,
        "mine_count": SOLO_MINE_COUNT,
    })


@app.post("/mines/open")
async def mines_open(request: Request, game_id: int = Form(...), cell: int = Form(...), db: AsyncSession = Depends(get_db)):
    user_id = require_user(request)
    if cell < 0 or cell >= GRID_SIZE:
        return JSONResponse({"ok": False, "error": "Noto'g'ri katak."})

    async with db.begin():
        game = (
            await db.execute(
                select(GambleMinesGame)
                .where(GambleMinesGame.id == game_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if game is None or game.status != "active":
            return JSONResponse({"ok": False, "error": "O'yin topilmadi yoki yakunlangan."})
        if int(game.user_telegram_id) != user_id:
            return JSONResponse({"ok": False, "error": "Bu sizning o'yiningiz emas."})

        state = json.loads(game.mines_json or "{}")
        mines = set(state.get("mines", []))
        opened = set(state.get("solo_opened", []))

        if cell in opened:
            return JSONResponse({"ok": False, "error": "Bu katak allaqachon ochilgan."})

        now = _utcnow()
        game.last_action_at = now

        if cell in mines:
            game.status = "lost"
            game.payout = 0
            game.ended_at = now
            stats = (
                await db.execute(
                    select(GambleUserStats).where(GambleUserStats.user_telegram_id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if stats:
                stats.win_streak = 0
            await db.flush()
            return JSONResponse({
                "ok": True,
                "result": "mine",
                "cell": cell,
                "mines": list(mines),
                "status": "lost",
            })

        opened.add(cell)
        state["solo_opened"] = sorted(opened)
        opened_count = len(opened)
        game.multiplier = _solo_multiplier(opened_count)
        game.payout = _solo_payout(int(game.bet or 0), opened_count)
        game.mines_json = json.dumps(state, ensure_ascii=False)
        await db.flush()

    return JSONResponse({
        "ok": True,
        "result": "safe",
        "cell": cell,
        "multiplier": float(game.multiplier),
        "payout": int(game.payout or 0),
        "opened_count": opened_count,
        "status": "active",
    })


@app.post("/mines/cashout")
async def mines_cashout(request: Request, game_id: int = Form(...), db: AsyncSession = Depends(get_db)):
    user_id = require_user(request)

    async with db.begin():
        game = (
            await db.execute(
                select(GambleMinesGame)
                .where(GambleMinesGame.id == game_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if game is None or game.status != "active":
            return JSONResponse({"ok": False, "error": "O'yin topilmadi yoki yakunlangan."})
        if int(game.user_telegram_id) != user_id:
            return JSONResponse({"ok": False, "error": "Bu sizning o'yiningiz emas."})

        state = json.loads(game.mines_json or "{}")
        opened_count = len(state.get("solo_opened", []))
        if opened_count <= 0:
            return JSONResponse({"ok": False, "error": "Avval kamida bitta safe katak oching."})

        payout = _solo_payout(int(game.bet or 0), opened_count)
        user = (
            await db.execute(select(User).where(User.telegram_id == user_id).with_for_update())
        ).scalar_one_or_none()
        if user is None:
            return JSONResponse({"ok": False, "error": "User topilmadi."})

        now = _utcnow()
        user.dollar = int(user.dollar or 0) + payout
        game.status = "cashed"
        game.payout = payout
        game.ended_at = now
        game.last_action_at = now

        stats = (
            await db.execute(
                select(GambleUserStats).where(GambleUserStats.user_telegram_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if stats:
            stats.win_streak = int(stats.win_streak or 0) + 1
            stats.total_payout = int(stats.total_payout or 0) + payout

        db.add(
            DollarTransaction(
                user_telegram_id=user_id,
                user_name=(user.display_name or "User")[:255],
                amount=payout,
                balance_after=int(user.dollar or 0),
                action="web_gamble_solo_cashout",
                note=f"Web mines cashout #{game.id}: {opened_count} safe",
            )
        )
        await db.flush()

    return JSONResponse({
        "ok": True,
        "payout": payout,
        "multiplier": float(game.multiplier),
        "opened_count": opened_count,
        "status": "cashed",
    })


# ─── Health check ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}
