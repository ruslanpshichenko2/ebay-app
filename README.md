# eBay & Marketplace Listing Generator

This app helps you turn product photos into listing copy for:

- eBay
- Facebook Marketplace

## What You Need

- A Mac
- Python 3.9 or newer
- An OpenAI API key

## First-Time Setup

From the project folder, run:

```bash
python3 -m pip install -r requirements.txt
```

Create a `.env` file in this folder and add:

```text
OPENAI_API_KEY=your_openai_key_here
```

You can copy `.env.example` and replace the placeholder value.

## Run The App

Option 1:

Double-click:

`launch_marketplace_app.command`

Option 2:

Run it manually:

```bash
cd /Users/ruslanpshichenko/ebay-app
python3 -m streamlit run app.py
```

Then open:

```text
http://localhost:8501
```

## Share With A Friend

Share this folder:

- `app.py`
- `launch_marketplace_app.command`
- `requirements.txt`
- `.env.example`
- `README.md`

Do not share your real `.env` file. Each person should add their own OpenAI API key.

## GitHub Pages For eBay OAuth

This project includes these pages in `docs/`:

- `privacy.html`
- `accepted.html`
- `declined.html`

If you publish this repo with GitHub Pages, your eBay OAuth URLs can be:

- `https://YOUR-USERNAME.github.io/REPO-NAME/privacy.html`
- `https://YOUR-USERNAME.github.io/REPO-NAME/accepted.html`
- `https://YOUR-USERNAME.github.io/REPO-NAME/declined.html`
