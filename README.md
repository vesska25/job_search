# Bank Job Monitor

Weekly monitor of German vacancies at banks. It filters for Finance / Risk / Regulatory **leadership** roles,
deduplicates with SQLite and sends a Telegram digest. Runs on GitHub Actions, so no PC has to be on.

```
config/banks.yaml     278 institutions (source, ATS type, flags, notes)
config/settings.yaml  all filters, LLM, HTTP and Telegram settings
src/                  main, scrapers (ATS adapters), filters, storage, notifications, utils
tools/verify_banks.py live verification + ATS discovery
data/jobs.db          seen-jobs database (committed back by the workflow)
```

## Current status

Sources are verified live on GitHub Actions (the development sandbox cannot reach bank sites). A bank is marked
`verification_status: ok` only after the **Verify bank sources** workflow got vacancies (or, for banks flagged
`allow_empty`, a correctly parsed empty list) from its official career page; failing sources are disabled
automatically with the reason in `notes`. `docs/bank_coverage.txt` lists every bank of the original list: which are
monitored, which are duplicates of another entry, and which are not monitored and why (JavaScript-only portals,
robots.txt forbids automation, no public career page, excluded by the user).

- Schedule: **Fridays 15:00 UTC** (17:00 Germany in summer time, 16:00 in winter), see `.github/workflows/job-monitor.yml`.
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (required); `ANTHROPIC_API_KEY` (optional, only with `llm.enabled`).
- Delivery check: `python -m src.main --test-telegram` (or the `test_telegram` workflow input) sends a sample digest
  of stored vacancies and marks nothing as notified.
- Profile (`config/settings.yaml`): Finance, Risk, Regulatory, Treasury **and Organisation / IT** leadership roles in Germany.
- Tools (Actions tab): *Verify bank sources*, *Diagnose bank source*, *Probe URL* (grep a page or JSON endpoint).

## 1. Install locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure banks
Edit `config/banks.yaml`. Key fields: `jobs_url`, `source_type`, `enabled`, `germany_only` (the page already
lists only German jobs, so location text is not checked), `options` (adapter settings), `alias_of` (same employer
as another entry, so it is not monitored twice, e.g. Credit Suisse -> UBS). Filters live in `config/settings.yaml`.

## 3. Create the Telegram bot
1. In Telegram, open **@BotFather**, send `/newbot`, follow the prompts, copy the **token**.
2. Open a chat with your new bot and send it any message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message.chat.id`; that is your **chat id**.

## 4. Telegram credentials (environment variables)
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
export ANTHROPIC_API_KEY="..."   # optional, only if llm.enabled: true
```
Never commit these.

## 5. Run locally
```bash
python -m src.main --dry-run              # scrape + filter, no Telegram, no DB writes
python -m src.main --baseline             # first real run: record current matches silently
python -m src.main                        # normal run: digest of NEW matches via Telegram
python -m src.main --bank aktivbank --dry-run --verbose   # single bank, shows why jobs match
```

## 6. Run tests
```bash
python -m pytest
```
Tests use fixtures only; no network.

## 7. GitHub Secrets
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional: `ANTHROPIC_API_KEY`).

## 8. Enable GitHub Actions
Actions tab -> enable workflows. `Weekly bank job monitor` runs Fridays 15:00 UTC (17:00 Germany in summer time, 16:00 in winter) and can be started manually
(*Run workflow*). Do the first run with **baseline** ticked so you are not flooded with every open vacancy.
The workflow commits `data/jobs.db` back to the repo (Settings -> Actions -> General -> Workflow permissions must
allow read and write). The database holds only job titles/URLs; keep the repo private if you prefer.

## 9. Add another bank
Append to `config/banks.yaml` (unique `id`):
```yaml
- id: examplebank
  name: Example Bank AG
  jobs_url: "https://examplebank.jobs.personio.de/"
  source_type: personio          # personio | smartrecruiters | workday | successfactors | softgarden | beesite | custom_api | custom_html | sparkasse_jobmarket | vr_jobs | sitemap_jobs
  germany_only: true             # true only if the page itself guarantees Germany-only jobs
  enabled: true
  options: {}
```
Then `python -m src.main --bank examplebank --dry-run --verbose`, and `python -m tools.verify_banks --apply`.

Adapter options: BeeSite (`source_type: beesite`; used by Commerzbank and Deutsche Bank, `options.api_url`, `language`, `criteria`), Workday `options.applied_facets` (Germany facet id from the site), SuccessFactors
`options.params`, SmartRecruiters `options.company`, `custom_html` `options.selectors` (`no_link: true` for accordion lists without per-job links) / `link_pattern` /
`pagination`, `custom_api` `options.api_url/items_path/fields/pagination` (see `src/scrapers/custom_api.py`).
Shared platforms with their own adapter: `sparkasse_jobmarket` (all Sparkassen via `sparkasse.de/api/job-market`,
`options.bank_code` = BLZ), `vr_jobs` (Volks- and Raiffeisenbanken via the vr.de exchange sitemap, `options.vr_slug`,
`allow_empty`), `sitemap_jobs` (sites that list every vacancy in a sitemap: `sitemap_url`, `url_regex`, `skip_slug`,
`detail`, `max_pages`). `custom_html` selectors also support `title_attr` / `title_strip` (title from a link attribute) and fall back
to the URL slug when a link has no text.
If a career page is JavaScript-rendered, find the JSON request in the browser's network tab and use `custom_api`.

## 10. Add another ATS adapter
1. Create `src/scrapers/myats.py` with a class extending `BaseScraper`, implementing `fetch_jobs(bank) -> list[Job]`
   (use `self.http`, split a pure `parse()` out so it is testable) and raise `ScraperError` on unexpected layouts.
2. Register it in `src/scrapers/__init__.py` (`SCRAPERS["myats"] = MyAtsScraper`).
3. Add a fixture in `tests/fixtures/` and a test in `tests/test_scrapers.py`.

## How filtering works
1. **Germany** (`filters/location.py`): skipped if `germany_only`; else country field, explicit "Germany", German
   city list vs. foreign list. Unknown location is rejected (`accept_unknown_location`).
2. **Seniority** (`filters/seniority.py`): hard exclusions (intern, Werkstudent, Junior...) always win; soft
   exclusions (Analyst, Specialist, Expert, Consultant...) lose against a leadership keyword; "Senior" alone is
   never leadership; "Senior Manager ..." is *ambiguous*: accepted with management signals in the description,
   otherwise handled by `borderline_policy` (default: included and flagged "(borderline)").
3. **Function** (`filters/function.py`): Finance / Risk / Regulatory / Treasury terms in title or department
   (or >= 3 hits in the description).
4. **LLM** (optional, `llm.enabled`): only for borderline cases, capped by `max_calls_per_run`; it can never
   override a foreign country. The system works fully without it.
5. **Dedup**: a job is known if its stable ID or canonical URL (tracking params removed) is already stored. If
   Telegram delivery fails, the jobs stay `notified = 0` and are retried next run.

## Compliance
robots.txt is checked per host (if it cannot be fetched, the host is skipped), 1 s minimum delay per host,
explicit User-Agent, retries with backoff. Sites that block automation are not circumvented: leave them disabled.

## Finding a source for a new bank
Run **Diagnose bank source** (Actions tab) with the career page URL. It reports robots.txt, embedded job data and
endpoint strings in the site's public scripts; give a `.js` URL plus a regex to see how a page loads its jobs.
