
1. **Scrapes** the source page for video embed links and short URLs
2. **Resolves** short links (maskr.blog, etc.) following all redirects
3. **Extracts** `.m3u8` stream URLs from intermediate and final pages
4. **Uses Selenium** (headless Chrome) for JavaScript-heavy pages
5. **Validates** found stream URLs
6. **Commits** updated playlist back to the repository

## ⏰ Update Schedule

- Runs automatically **every 5 minutes** via GitHub Actions cron
- Can also be triggered manually from the Actions tab

## 📁 Files

| File | Description |
|------|-------------|
| `playlist.m3u` | The M3U playlist file (use this in your player) |
| `playlist.json` | JSON metadata with stream details |
| `scraper.py` | The scraping script |
| `log.txt` | Latest scraper log output |

## 🚀 Setup Your Own

1. **Fork** this repository
2. Go to **Settings → Actions → General**
3. Enable **"Read and write permissions"** under Workflow permissions
4. The action will start running automatically!

## ⚠️ Manual Trigger

1. Go to the **Actions** tab
2. Click **"Update Playlist Every 5 Minutes"**
3. Click **"Run workflow"**

## 🛠️ Troubleshooting

- Check `log.txt` for detailed scraper output
- If Actions fail, check the Actions tab for error logs
- GitHub has a limit on cron frequency; actual runs may be delayed
- Make sure Actions permissions allow write access

## 📝 Notes

- GitHub Actions cron jobs may have slight delays (not exactly every 5 minutes)
- Free GitHub accounts have ~2000 minutes/month of Actions time
- The scraper preserves previously found links as fallback
