# eBay & Facebook Marketplace Listing Generator

Create eBay and Facebook Marketplace listing copy from product photos.

## What It Does

- Upload up to 10 product photos
- Choose the item condition
- Add PN / notes / defects
- Generate eBay and Facebook Marketplace titles and descriptions
- Optionally use active eBay listings for price comps

## Use The Hosted App

Open the shared Streamlit link from the app owner.

If the app asks for secrets, the owner needs to add them in Streamlit Cloud settings.

## Run It Locally

1. Download this repo with `Code` > `Download ZIP`.
2. Unzip the folder.
3. Open Terminal in the folder.
4. Install the requirements:

```bash
python3 -m pip install -r requirements.txt
```

5. Copy `.env.example` to a new file named `.env`.
6. Add your OpenAI API key:

```text
OPENAI_API_KEY=your_openai_key_here
```

7. Start the app:

```bash
python3 -m streamlit run app.py
```

8. Open:

```text
http://localhost:8501
```

## Optional eBay Pricing

To use active eBay listing comps, add these to `.env`:

```text
EBAY_CLIENT_ID=your_ebay_client_id_here
EBAY_CLIENT_SECRET=your_ebay_client_secret_here
```

The app still works without these, but pricing will fall back to the AI-generated range.

## Important

- Do not share your real `.env` file.
- Each person should use their own OpenAI API key.
- `.env.example` is safe to share.
