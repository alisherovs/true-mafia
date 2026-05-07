# true-mafia

Production-ready Telegram Mafia game bot

Python 3.11+, aiogram 3.x, async SQLAlchemy asosida yozilgan modulli Telegram Mafia bot.

## Xususiyatlar

- `/start` private flow + language selector (8 til)
- User va group uchun alohida language saqlash
- `/game` bilan registration lobby, join button, pin, live edit
- Registration timer (60s/30s reminder + auto close)
- Dynamic role distribution
- Tun/Kun fazalari (media file_id yoki local gif)
- O'yin davomida ro'yxatda bo'lmagan foydalanuvchi xabarlari auto-delete qilinadi
- Night action callbacks (Mafia/Don, Doctor, Commissar, Mistress, Lawyer, Killer, Bum)
- Day voting callback
- Win-condition hisoblash
- Final group statistikasi + private reward/profil xabari
- Economy: dollar, diamonds, inventory, wins, total games
- `/profile`, `/give`, `/giveto`, `/top`, `/settings`, `/stop`, `/leave`
- `/teamgame` hozircha tayyorlanmoqda (skeleton)
- SQLite default, PostgreSQL-ready (`DATABASE_URL` bilan)

## Tuzilma

```
mafia_bot/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── enums.py
│   ├── roles.py
│   ├── game_engine.py
│   ├── scheduler.py
│   ├── keyboards.py
│   ├── texts/
│   │   ├── uz.py
│   │   ├── ru.py
│   │   ├── en.py
│   │   ├── az.py
│   │   ├── tr.py
│   │   ├── ua.py
│   │   ├── kz.py
│   │   └── id.py
│   └── handlers/
│       ├── start.py
│       ├── language.py
│       ├── game.py
│       ├── callbacks.py
│       ├── roles.py
│       ├── profile.py
│       ├── economy.py
│       ├── settings.py
│       ├── top.py
│       └── admin.py
├── media/
│   ├── night.gif
│   ├── day.gif
│   └── README.md
├── storage/
├── .env.example
├── requirements.txt
└── README.md
```

## O'rnatish

```bash
cd mafia_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` ichida `BOT_TOKEN` va `BOT_USERNAME` ni to'ldiring.

## Ishga tushirish

```bash
python -m app.main
```

## DATABASE_URL misollar

SQLite (default):

```env
DATABASE_URL=sqlite+aiosqlite:///./storage/mafia.db
```

PostgreSQL:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/mafia_bot
```

## BotFather Commands

```text
start - Start game
game - Start registration
leave - Leave game
teamgame - Start turnire game
extend - Extend registration timeout
lang - Change language
give - Share diamonds
giveto - Give diamonds to user
profile - Profile
roles - Rules
settings - Settings
stop - Stop game
top - TOP Rating
```

## Local Media ishlatish

1. `media/night.gif` va `media/day.gif` qo'ying.
2. `.env`da:

```env
NIGHT_MEDIA_FILE_ID=
DAY_MEDIA_FILE_ID=
NIGHT_MEDIA_LOCAL=media/night.gif
DAY_MEDIA_LOCAL=media/day.gif
```

Agar `*_MEDIA_FILE_ID` bo'sh bo'lsa, local fayl yuboriladi.

## Telegram file_id ishlatish

1. Kerakli GIF/video ni bir marta botga yuboring.
2. file_id ni log yoki debug orqali oling.
3. `.env` ga qo'ying:

```env
NIGHT_MEDIA_FILE_ID=AgACAgQAAxkBAA...
DAY_MEDIA_FILE_ID=AgACAgQAAxkBAA...
```

Shunda bot local fayl o'rniga file_id ni ishlatadi.

## Restart Behavior

Server qayta ishga tushganda oldingi `registration/active` holatdagi o'yinlar xavfsiz tarzda `cancelled` holatiga o'tkaziladi. Bu noaniq state va eski callback collisionlarini oldini oladi.

## Eslatma

- Payment integration intentionally placeholder.
- `/teamgame` intentionally placeholder.
- Qolgan asosiy game loop real ishlaydi: registration → role distribution → night/day → voting → winner/rewards.
