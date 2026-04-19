# eBay & Facebook Marketplace Listing Generator

This app turns product photos into listing copy for:

- eBay
- Facebook Marketplace

## Download From GitHub

1. Open this repo in your browser.
2. Click the green `Code` button.
3. Click `Download ZIP`.
4. Unzip the folder on your computer.

## What You Need

- Python 3.9 or newer
- An OpenAI API key

## Set Up

Open Terminal in the project folder and run:

```bash
python3 -m pip install -r requirements.txt
```

Create a file named `.env` in the project folder and add:

```text
OPENAI_API_KEY=your_openai_key_here
```

You can copy `.env.example` and replace the placeholder values.

## Run The App

Option 1:

Double-click:

`launch_marketplace_app.command`

Option 2:

Run it manually:

```bash
python3 -m streamlit run app.py
```

Then open:

```text
http://localhost:8501
```

## Important

- Do not share your real `.env` file.
- Each person should use their own OpenAI API key.
- `.env.example` is safe to share.
