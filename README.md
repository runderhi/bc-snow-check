# BC snow check

A daily snow report for Revelstoke, Whistler Blackcomb, Big White, Sun Peaks, and Fernie that keeps **independent measurements** separate from **resort-reported numbers**.

- **Independent:** BC government automated snow-pillow stations (hourly snow depth, snow water equivalent, temperature) and Avalanche Canada's public forecast API (danger ratings and forecaster summaries).
- **Resort:** each resort's own snow-report page, read as best effort and shown in its own column.

GitHub runs it every morning at about 6:30 AM Pacific and publishes the page with GitHub Pages. Nothing runs on your Mac.

## Setup (about 10 minutes, all in Safari on your Mac or iPhone)

1. Go to github.com, tap **+** then **New repository**. Name it `bc-snow-check`, set it to **Public**, and create it without a README.
2. On the empty repo page, tap **uploading an existing file**. Drag in everything from this folder **including the hidden `.github` folder**. On a Mac, press Cmd+Shift+. (period) in Finder to show hidden folders before dragging. Commit the upload.
   - Check that `.github/workflows/daily.yml` appears in the repo. If the upload skipped it, use **Add file > Create new file**, type `.github/workflows/daily.yml` as the name, and paste the file's contents.
3. **Settings > Actions > General > Workflow permissions:** choose **Read and write permissions** and save.
4. **Actions tab:** select **Daily snow check**, then **Run workflow**. It takes 1–2 minutes.
5. **Settings > Pages:** set Source to **Deploy from a branch**, branch **main**, folder **/docs**, and save. After a minute the page is at `https://<your-username>.github.io/bc-snow-check/`.
6. On your iPhone, open that address in Safari, tap **Share > Add to Home Screen**.

## After the first run

Open the page and expand **Source health**. Expect some resort pages to need a parser tweak. Each run saves the visible text of every resort page in `debug/`, so you (or Claude) can see exactly what the resort published and adjust the patterns in `snowcheck.py`.

Things to check:
- **Station picks:** each card lists the station, its elevation, and distance. To force a specific station, add its ID to that resort's `pinned_stations` in `config.json` (find IDs on the BC snow station map linked at the bottom of the page).
- **Resort URLs:** if a resort moves its snow report page, update `report_url` in `config.json`.
- **Local links:** add news or community links per resort in `config.json`, e.g. `"local_links": [{"name": "Pique", "url": "https://www.piquenewsmagazine.com"}]`.

## Notes

- Station depth change is not the same as snowfall. New snow settles, so the "Agreement" column only flags gaps bigger than settling explains, using the 7-day window when available.
- Avalanche Canada public forecasts usually start in late November, and most resorts don't post snow numbers until they open. Early-season blanks are expected.
- GitHub may pause scheduled workflows in a repo with no activity for 60 days. The daily commits count as activity, so this shouldn't happen while it's working.

To run locally on a Mac: `pip3 install -r requirements.txt && python3 snowcheck.py`, then open `docs/index.html`.
