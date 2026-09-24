# BBG Alerts Channel Setup

Use this to route Bloomberg data-health alerts to a dedicated company Telegram channel instead of the default bot chat.

Note: `data_setup/create_bbg_data_table.py` no longer sends Telegram alerts for stale workbooks. It only prints a local loader warning when `bbg_event_fields.xlsx` is old.

## 1. Create the channel

1. In Telegram, create a new channel for company alerts.
2. Add your bot to the channel.
3. Promote the bot so it can post messages.

## 2. Choose the chat identifier

You can use either:

- A channel username, for example `@company_bbg_alerts`
- A numeric chat ID if your channel is private or you prefer explicit IDs

If your channel has a public username, that is the simplest option.

## 3. Configure the alert destination

In `config/keys.env`, add:

```env
BBG_ALERT_CHAT_ID=@company_bbg_alerts
```

If `BBG_ALERT_CHAT_ID` is not set, BBG data-health alerts fall back to `TELEGRAM_CHAT_ID`.

## 4. Send a test message

Run this from the repo root:

```bash
py -3 -c "from src.core.telegram_notifier import load_telegram_chat_id, send_telegram_message; chat_id = load_telegram_chat_id('BBG_ALERT_CHAT_ID', 'TELEGRAM_CHAT_ID'); print(send_telegram_message('BBG alert channel test', chat_id=chat_id))"
```

Expected result:

- Telegram receives the test message in the alerts channel
- The command prints `{'success': True, ...}`

## 5. Active trigger path

The active BBG data-health Telegram alert is sent by:

- `scripts/check_economic_events_db_staleness.py`

It posts when one of the configured `economic_events.db` copies is stale or missing. The workbook loader still prints stale-workbook warnings to stdout/logs, but does not send Telegram messages.

## Notes

- The workbook refresh task and the overnight DB loader are separate:
  - `BBGRefreshExcel` updates the workbook on the Bloomberg machine
  - `BBGDataDaily` loads the refreshed workbook into SQLite overnight
- Keeping `BBG_ALERT_CHAT_ID` separate avoids changing other code paths that still rely on `TELEGRAM_CHAT_ID`.
