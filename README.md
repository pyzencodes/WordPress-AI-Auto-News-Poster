````markdown
# 📰 WPHaber — WordPress News Auto Poster

Private utility that automatically fetches news articles from a source site, rewrites them with an AI model, and publishes them to a WordPress site via the REST API (with XML-RPC fallback).  
Keeps track of already-processed links in a local SQLite database so the same article is not posted twice.

> ⚠️ Private/internal tool — do **NOT** share this repository or configuration publicly.

---

## 🔍 What It Does

- Periodically fetches the latest news articles from a predefined “latest news” page.
- Parses each article (title, body content, main image, publish time).
- Sends the content to the OpenAI API to generate a cleaned / rewritten article and title.
- Publishes the processed post to your WordPress site:
  - Preferably via **WordPress REST API** with Application Password auth.
  - Falls back to **XML-RPC** if REST auth is not available.
- Stores processed URLs in `seen_links.db` so each news item is only posted once.
- Can be run **once** or in a **continuous loop** (for cron / background usage).

---

## ⚙️ Setup

### 1. Python Requirements

- Python **3.8+**
- Recommended packages:

```bash
pip install requests beautifulsoup4 lxml python-dotenv openai
````

### 2. WordPress Requirements

* A WordPress site with:

  * REST API enabled (default on modern WordPress).
  * A user with an **Application Password** (for basic auth), or valid XML-RPC login.
* (Optional) Included plugin `ingest-post/ingest-post.php`

  * Can be installed as a WordPress plugin if you want a **token-protected custom ingest endpoint** for other clients.
  * Not strictly required for the basic REST posting done by `wphaber.py`.

### 3. Environment Configuration

In the `WPHaber` folder, copy `.env.txt` to `.env` and fill in your own values:

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
WP_BASE_URL=https://your-wordpress-site.com
WP_USERNAME=your-wp-username
WP_APP_PASSWORD=your-application-password
WP_DEFAULT_CATEGORY_ID=0
WP_STATUS=publish
```

* `OPENAI_API_KEY` – your OpenAI API key.
* `WP_BASE_URL` – full base URL of your WordPress site (no trailing slash).
* `WP_USERNAME` – WordPress user that owns the Application Password.
* `WP_APP_PASSWORD` – the Application Password generated in WordPress.
* `WP_DEFAULT_CATEGORY_ID` – numeric ID of the default category (or `0` for none).
* `WP_STATUS` – post status, usually `publish` or `draft`.

> 💡 Do **not** commit real API keys, passwords or `.env` files to any public repository.

---

## ▶️ Usage

From inside the `WPHaber` directory:

### Run once (single import pass)

```bash
python wphaber.py --once
```

* Checks WordPress auth (REST, then XML-RPC fallback).
* Fetches the latest articles from the configured source.
* Skips URLs already stored in `seen_links.db`.
* Publishes new posts to WordPress, then exits.

### Run in continuous loop

```bash
python wphaber.py
```

* Same behavior as above, but:

  * After each pass, waits `CHECK_INTERVAL_SEC` seconds (configured in the script).
  * Continues running indefinitely (suitable for screen/tmux/systemd/cron wrappers).

---

## 📂 Important Files

* `wphaber.py` – main auto-poster script (fetch, rewrite, publish, deduplicate).
* `haberlerbiz.py` / `bot.py` – helper logic for parsing and content extraction.
* `seen_links.db` – SQLite database that tracks already-processed article URLs.
* `.env` – environment configuration (API keys, WordPress URL, credentials).
* `ingest-post/ingest-post.php` – optional WordPress plugin for token-based ingest API.

---

## ⚠️ Notes

* This project is designed for **private use only**.
* Respect the terms of service and legal policies of any sites you fetch content from.
* Always keep API keys, passwords and session data secure.

```
::contentReference[oaicite:0]{index=0}
```
