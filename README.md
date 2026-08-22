# STRAIT / RESILIENCE

Bilingual public-source intelligence dashboard for Taiwan Strait risk and semiconductor supply-chain resilience.

## What is automated

- RSS ingestion every 6 hours with GitHub Actions
- Stable IDs and deduplication
- Topic classification: Security / Semiconductor / Japan-Korea / Logistics
- Japanese + Korean title/summary generation through the OpenAI API
- Weekly bilingual brief generated from the latest signals
- Automatic commit of `data/articles.json` and `data/weekly.json`
- Front-end automatically reads the generated JSON without manual HTML edits

## Required one-time setup

1. Upload this repository contents to GitHub, preserving the `.github/workflows/` directory.
2. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
3. Create `OPENAI_API_KEY` and paste your API key. Never put the key in source files.
4. Optional: under **Variables**, create `OPENAI_MODEL` if you want a model other than the default `gpt-5-mini`.
5. In **Actions**, run **Update intelligence feed** once with `Run workflow`.
6. Enable GitHub Pages for the repository if it is not already enabled: **Settings → Pages → Deploy from a branch → main / root**.

Without `OPENAI_API_KEY`, ingestion still runs, but new items are marked `pending_api_key` and retain source-language text instead of fabricated translations.

## Local test

```bash
python -m pip install -r requirements.txt pytest
pytest -q
python -m http.server 8000
```

Open `http://localhost:8000`.

## Files

- `index.html` — bilingual front-end
- `data/articles.json` — generated signal feed
- `data/weekly.json` — generated weekly brief
- `scripts/update_content.py` — ingestion, classification, translation, weekly summarization
- `config/sources.json` — RSS/search feeds
- `.github/workflows/update-content.yml` — scheduled automation

## Source policy

The site keeps source name, publication date, original title and original URL. It does not republish full article text. Generated summaries should be treated as research aids and checked against the original source for high-stakes decisions.
