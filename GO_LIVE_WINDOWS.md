# Go-Live on Windows — macro-terminal

Total time: ~15 minutes (mostly waiting for installers).

---

## A. Install three free tools (one-time, ~5 min)

You only do this once on your machine. If you already have any of these, skip that line.

### A1. Python (if not installed)

1. Open https://www.python.org/downloads/
2. Click the big yellow **"Download Python 3.x"** button.
3. Run the installer. **IMPORTANT:** on the first installer screen, tick the box that says **"Add python.exe to PATH"** before clicking Install Now.

### A2. Git for Windows

1. Open https://git-scm.com/download/win
2. Download and run the installer.
3. Accept all the defaults. (This gives you `git`, plus a tool called **Git Bash** that lets the `.sh` scripts run on Windows.)

### A3. GitHub CLI

1. Open https://cli.github.com/
2. Click "Download for Windows" → run the installer.

After all three are installed, **close any open File Explorer or terminal windows and open a fresh Git Bash** so the new tools are picked up.

### Verify everything is installed

Open Git Bash (Start menu → type "Git Bash") and paste this:

```bash
python --version && git --version && gh --version
```

You should see three version numbers. If any line says "command not found", that tool didn't install correctly — re-run its installer.

---

## B. Find your Economic_dashboard folder

1. Open **File Explorer** (Windows key + E).
2. Navigate to wherever you keep the `Economic_dashboard` folder you originally selected for Cowork. Common locations:
   - `Documents\Economic_dashboard`
   - `Desktop\Economic_dashboard`
   - `OneDrive\Documents\Economic_dashboard`
3. Open it. You should see a `macro-terminal` subfolder inside.
4. **Double-click `macro-terminal`** to enter it.

You should now be looking at a folder that contains `bootstrap.sh`, `smoke_test.sh`, `README.md`, and subfolders like `etl`, `public`, `data`.

---

## C. Open Git Bash inside that folder

1. Right-click on **empty white space** inside the `macro-terminal` folder window.
2. In the right-click menu, look for **"Open Git Bash here"** or **"Git Bash Here"**. (On Windows 11, you may need to click "Show more options" first.)
3. A black terminal window opens, already pointed at the right folder. Verify by typing:

```bash
pwd
ls
```

You should see the path ending in `/macro-terminal` and the file listing.

---

## D. Get a free FRED API key (30 sec)

1. Open https://fredaccount.stlouisfed.org/apikeys
2. Click **"Sign Up"** if you don't have an account, or **"Sign In"**.
3. Click **"Request API Key"**.
4. Type any short reason ("personal dashboard").
5. You get a 32-character hex string. Copy it.

---

## E. Pre-flight smoke test (~2 min)

In your Git Bash window inside `macro-terminal`, paste these one at a time:

```bash
pip install -r requirements.txt
```

(Wait for it to finish. ~30 seconds.)

```bash
export FRED_KEY=PASTE_YOUR_32_CHAR_KEY_HERE
```

(Replace `PASTE_YOUR_32_CHAR_KEY_HERE` with your actual key. Nothing prints — that's normal.)

```bash
bash smoke_test.sh
```

If you see **`✅ SMOKE TEST PASSED`** at the end, everything works locally and you can deploy.

If it fails, copy the error and tell me — I'll debug.

---

## F. Authenticate `gh` once

```bash
gh auth login
```

It asks four questions:

1. **Where to log in?** → press Enter (GitHub.com)
2. **Protocol?** → press Enter (HTTPS)
3. **Authenticate Git?** → type `Y` and press Enter
4. **How to authenticate?** → choose **"Login with a web browser"**

It prints an 8-character code and opens your browser. Paste the code, click Authorize. Done.

---

## G. Deploy

```bash
bash bootstrap.sh
```

The script asks three things:

1. **GitHub repo name** → press Enter for `macro-terminal`
2. **Visibility** → press Enter for `public`
3. **FRED API key** → paste your key, press Enter (it's hidden as you type — that's normal)

Then it asks **"Run smoke test now?"** — type `n` (you already ran it).

Wait ~60 seconds. The script ends by printing your live URL:

```
✓ Live URL:  https://YOUR_USERNAME.github.io/macro-terminal/
```

---

## H. Watch the deploy + open the site

```bash
gh run watch
```

This shows the deploy progress live. When you see two green check marks (one for `macro-etl`, one for `deploy-pages`), open the URL from step G in your browser. **First deploy takes about 3-5 minutes.**

If the URL gives a 404, GitHub Pages is still spinning up — wait one more minute and refresh.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `bash: command not found` | You're in PowerShell or CMD, not Git Bash. Right-click in the folder → "Git Bash Here". |
| `pip: command not found` | Python didn't install with PATH. Reinstall and tick "Add python.exe to PATH". |
| `gh: command not found` | GitHub CLI didn't install. Re-run its installer, close and reopen Git Bash. |
| `bootstrap.sh: gh not authenticated` | Run `gh auth login` first (step F). |
| `Permission denied (publickey)` | The bootstrap pushes via HTTPS, not SSH — this shouldn't happen. If it does, run `gh auth login` again and choose HTTPS. |
| Live URL gives 404 after 5 min | Repo Settings → Pages → ensure Source is "GitHub Actions". |

---

## After you're live

* **Force a fresh data pull anytime:** `gh workflow run macro-etl`
* **Watch the latest run:** `gh run watch`
* **Disable the cron** (e.g., during a long break): `gh workflow disable macro-etl`
* **Re-enable:** `gh workflow enable macro-etl`
