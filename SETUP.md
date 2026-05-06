# SteadyEye Backend — Setup

End-to-end setup, from creating the Telegram bot to verifying the webhook in production.

## 1. Create the Telegram bot

1. Open a chat with [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot`.
3. Suggested name: `SteadyEye Founder Bot`.
4. Suggested username: `SteadyEyeFounderBot` (must end in `Bot` and be globally unique).
5. Save the bot token BotFather returns — this is `TELEGRAM_BOT_TOKEN`.

## 2. Get your personal Telegram chat ID

1. Open a chat with the bot and send `/start`, then any message.
2. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser (replace `<TOKEN>`).
3. Find `"chat":{"id":NUMBER` in the JSON. That number is `TELEGRAM_OWNER_CHAT_ID`.

## 3. Local development

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in values
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The API will be at `http://127.0.0.1:8000/api/v1/`. Admin at `http://127.0.0.1:8000/admin/`.

For local Telegram testing, use [ngrok](https://ngrok.com) or similar to expose your local server, then set the webhook to that URL (see step 6).

## 4. Push to GitHub

This `backend/` directory is its own git repo (not part of the iOS repo).

```bash
cd backend
git remote add origin git@github.com:USERNAME/steadyeye-backend.git
git push -u origin main
```

## 5. Deploy to Railway

1. New Railway project → **Deploy from GitHub repo** → select `steadyeye-backend`.
2. Add the **PostgreSQL** plugin to the project. Railway will inject `DATABASE_URL` automatically.
3. Set environment variables in the Railway service:
   - `SECRET_KEY` — generate a long random string
   - `DEBUG` — `False`
   - `ALLOWED_HOSTS` — `your-app.up.railway.app` (comma-separated if multiple)
   - `CSRF_TRUSTED_ORIGINS` — `https://your-app.up.railway.app`
   - `TELEGRAM_BOT_TOKEN` — from step 1
   - `TELEGRAM_OWNER_CHAT_ID` — from step 2
   - `TELEGRAM_WEBHOOK_SECRET` — random string (e.g. `openssl rand -hex 32`)
   - `REVENUECAT_SECRET_API_KEY` — RC dashboard → Project settings → API keys → New secret API key (label `attribution-gateway`). Used by `POST /api/v1/attribution/fetch/`.
4. Deploy. Railway will run `python manage.py migrate` (release phase) then `gunicorn config.wsgi`.
5. Verify health: `curl https://your-app.up.railway.app/api/v1/health/` → `{"status":"ok"}`.

## 6. Set the Telegram webhook

```bash
curl -F "url=https://YOUR_RAILWAY_URL/api/v1/telegram/webhook/" \
     -F "secret_token=YOUR_TELEGRAM_WEBHOOK_SECRET" \
     https://api.telegram.org/bot<TOKEN>/setWebhook
```

## 7. Verify webhook

```bash
curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

Expect `"url":"https://YOUR_RAILWAY_URL/api/v1/telegram/webhook/"` and no `last_error_message`.

## 8. Test end-to-end

Send an inbound message:

```bash
curl -X POST https://YOUR_RAILWAY_URL/api/v1/messages/ \
  -H "Content-Type: application/json" \
  -d '{"user_uuid":"TEST123","text":"Hello","metadata":{"app_version":"1.4"},"email":"test@example.com"}'
```

- Verify the message arrives in your Telegram chat with the bot.
- Reply to that message in Telegram.
- Fetch both messages:

```bash
curl "https://YOUR_RAILWAY_URL/api/v1/messages/?user_uuid=TEST123"
```

You should see both inbound and outbound messages in chronological order.
