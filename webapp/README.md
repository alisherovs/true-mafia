# Qimor Web Sayt — Mines

Bot bazasidan foydalanuvchi, alohida FastAPI web sayt. Faqat **1 kishilik (solo) Mines** o'yini.

## Xususiyatlar

- **Telegram ID bilan kirish** — bot bilan bir xil akkaunt
- **Balans** — botdagi dollar/almos bilan sinxron
- **Mines o'yini** — 6×6 grid, 8 ta mina, x koefisient
- **Haftalik TOP 10** — eng yaxshi qimorvozlar
- **O'yin tarixi** — oxirgi 20 ta o'yin
- **Responsive UI** — mobil va desktop

## Ishga tushirish

```bash
cd webapp
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Yoki:

```bash
cd webapp
python3 -m uvicorn main:app --host 0.0.0.0 --port 8001
```

## URL'lar

| Yo'l | Tavsif |
|------|--------|
| `/` | Login sahifasi |
| `/dashboard` | Balans, TOP 10, tarix |
| `/mines` | Mines o'yini |
| `/logout` | Chiqish |

## Baza

Bot bilan **bir xil** `SessionLocal` va modellardan foydalanadi:
- `User` — balans (dollar, diamonds)
- `GambleMinesGame` — o'yinlar
- `GambleUserStats` — statistika
- `DollarTransaction` — tranzaksiyalar

## Eslatma

Bot kodiga tegmaydi, faqat yangi `webapp/` papkasi qo'shildi.
