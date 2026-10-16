# Smart Benefits

A fresh, local-first rebuild of the public Smart Benefits website. This workspace did not include the original site's source code, so this is an independently implemented project based on the publicly visible pages and flows.

## What's included

- Responsive home, about, coverage comparison, contact, webinar RSVP, consultation booking, and careers pages.
- Contact messages, webinar registrations, and job applications stored in SQLite.
- Consultation booking for Monday through Saturday, with hourly start times from 9am to 5pm Eastern Time. A unique database constraint prevents two bookings for the same slot.
- Optional PDF, DOC, and DOCX resume/license uploads, limited to 10 MB each and stored outside the public static directory.
- A read-only admin dashboard at `/admin`, protected with HTTP Basic authentication and disabled until an admin password is configured.
- CSRF tokens on form submissions, server-side input validation, accessible responsive navigation, and local application tests.

Submissions are saved locally. No email, calendar, payment, analytics, or external CRM integration is configured; in particular, the app does not send submission or booking confirmations. The admin dashboard is for local review and does not currently support editing or deleting records.

## Requirements

- Python 3.10 or newer
- A modern browser for the website

## Run locally on Windows

From the project folder in PowerShell:

```powershell
Set-Location D:\theSmart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The SQLite database and private application uploads are created under `data\` the first time the app starts. That directory is ignored by Git.

The booking calendar uses the `America/New_York` time zone. Set `DATA_DIR` to change where the database and uploads are stored.

### Start automatically when opening this folder in VS Code

The auto-run task is stored in `.vscode\tasks.json`. Open the **project root** (`D:\theSmart`) in VS Code, not the `tests` subfolder. In the integrated terminal, prepare the local virtual environment once:

```powershell
Set-Location D:\theSmart
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

Reopen the project folder in VS Code. If prompted, allow automatic tasks for this workspace. The `Run Smart Benefits` task starts the development server; open <http://127.0.0.1:8000> and leave the VS Code task terminal running. You can also select **Terminal → Run Task → Run Smart Benefits** to start it manually. Stop it with **Ctrl+C**.

## Admin dashboard

Admin access is disabled unless `ADMIN_PASSWORD` is set. For a local PowerShell session, set a password before starting the server:

```powershell
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "use-a-long-unique-password"
uvicorn app.main:app --reload
```

Then visit <http://127.0.0.1:8000/admin> and enter those credentials when prompted.

For production, set `APP_ENV=production`, configure a long random `SESSION_SECRET`, and serve the app exclusively over HTTPS. Production mode refuses to start without `SESSION_SECRET`; HTTPS is required because HTTP Basic credentials and session cookies must not be sent over plain HTTP. Use a dedicated deployment secret manager, and configure a strong, unique `ADMIN_PASSWORD`.

## Tests

Install the development dependencies above. HTTP/application tests run without a server:

```powershell
python -m pytest tests\test_app.py
```

For the browser end-to-end tests, start the app in one terminal. In another activated virtual environment, install the Playwright browser and run:

```powershell
python -m playwright install chromium
python -m pytest tests\test_site.py
```

Set `BASE_URL` to target another running deployment, for example:

```powershell
$env:BASE_URL = "https://staging.example.com"
python -m pytest tests\test_site.py
```

## Deployment notes

This starter is intentionally local-first. SQLite is suitable for a single application instance; use a managed database and private object storage before running multiple instances or handling significant production traffic. Set upload limits and retention policies appropriate to your business, back up and restrict access to `DATA_DIR`, and provide any legally required privacy notices and consent language before collecting real customer or applicant information.

For a runtime-only environment, install `requirements.txt` instead of `requirements-dev.txt`.
