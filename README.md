# PlanBot | AI Event Planning Buddy

PlanBot is a Flask and SQLite event-planning assistant. Its guided planning workspace turns a host's event details into an automatically saved plan with a balanced budget, venue and menu ideas, a day-of schedule, and a persistent checklist.

## Features

- Responsive editorial landing page with event photography, event categories, product steps, and navigation to a separate planner workspace.
- Split planning workspace with a live event-details summary alongside the assistant and persistent recent conversations.
- Guided event setup for birthdays, weddings, college events, corporate events, engagements, and anniversaries.
- Automatic SQLite saving as soon as a plan is generated; repeated submissions of the same details reuse the existing plan.
- Seven-category budget plan: venue, food and catering, decoration, photography, entertainment, transportation, and miscellaneous, with an unallocated buffer shown as the remaining balance.
- Event plans based on the submitted event type, date, location, guest count, budget, theme, and food preferences.
- Event-specific overview, venue, decoration, food, photography, and entertainment recommendations, plus a relative schedule and preparation checklist.
- Estimated expense allocations are calculated from the submitted budget, with the remaining balance shown as a contingency.
- Edit category estimates from a saved plan, see the allocated and remaining totals update immediately, and save validated amounts to SQLite.
- Saved-plan dashboard with totals, search, category filters, date and budget sorting, details, edits, and deletion.
- Per-plan checklist tasks and completion states stored in SQLite, with automatically updated completion percentage, task add/remove, and refresh-safe persistence.
- Generated event schedules that can be edited and saved with the corresponding plan.
- Validation, loading feedback, retry handling, confirmation before deletion, and user notifications.
- Account registration, login/logout, password hashing, CSRF protection, and user-private event plans and chat history.
- Guest access from the login screen: explore the public home page, use the planner and chatbot, and generate plan previews without an account. Guest conversations and generated previews remain in that browser; sign in to save plans and conversations to a private account.

## Chat response engine

This project does not have an external AI API or model key configured. PlanBot clearly identifies its built-in rule-based assistant in the planner UI. It creates event-specific plans from local planning rules, and provides rule-based answers to common follow-up questions. No messages or event details are sent to a third-party AI provider.

## Technology

- Python 3.10+
- Flask
- SQLite (Python standard library)
- HTML5, CSS3, and vanilla JavaScript
- Font Awesome icons

## Run in VS Code

1. Open this folder in VS Code and create a virtual environment in the terminal:

   **Windows PowerShell**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **macOS / Linux**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the backend dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a local environment file from the example:

   **PowerShell**

   ```powershell
   Copy-Item .env.example .env
   ```

   The app loads `.env` when it starts. The blank secret is safe for local development because the app creates and persists a local key in the ignored `instance/` folder. For deployment, set a private random `PLANBOT_SECRET_KEY` with at least 32 characters and set `PLANBOT_COOKIE_SECURE=true` when using HTTPS. Never commit `.env` or expose the secret in frontend code.

   `PLANBOT_DATABASE_PATH` is optional. Leave it blank to keep using `planbot.db` next to `app.py`, or set it to an absolute path for another persistent SQLite file.

4. Start the server and open <http://127.0.0.1:5000>:

   ```powershell
   python app.py
   ```

   Alternatively, open **Run and Debug** and press **F5** with **PlanBot: Run Flask** selected. Choose **PlanBot: Open in Chrome** to open the running site in the configured browser debugger.

The `planbot.db` SQLite file is created next to `app.py` automatically. Existing databases are migrated to support duplicate-safe automatic saves without removing saved plans. The database is ignored by Git and stays on the machine running the app.

## Use the app

1. Select **AI Planner** or **Start planning** and answer the guided prompts. Event types appear as selectable cards; other suggestions are quick replies.
2. PlanBot generates and saves the plan automatically. The chat shows its budget snapshot and links to the dashboard.
3. Open **My plans** to search, filter, sort, view, or edit saved plans. Delete asks for confirmation.
4. Choose **View Details** to review the overview, update and save the event schedule, and edit expense estimates in the allocation chart. Allocated expenses cannot exceed the plan budget. Mark preparation tasks complete or add/remove tasks; task progress and schedule changes are saved to the plan and survive refreshes.
5. Ask the chatbot follow-up questions about budget, venues, food, decor, and planning steps.

The site opens on the login screen. Choose **Skip for now** to visit the public home page, explore event categories, and use the planner as a guest. Guest chats and generated plan previews are stored in browser local storage and are not uploaded to the server. Create an account or log in to save plans and conversations to your private SQLite account; saved account data persists across refreshes and browsers when the same account is used. Passwords are stored as Werkzeug password hashes, never as plain text. Mutating API requests and form submissions require a session CSRF token.

Plans already present in an upgraded database have no owner. They remain unassigned and are not shown to any account; they are not automatically claimed by the first user to register.

Chat conversations are saved to the server for the signed-in account. Existing browser-only conversations are not automatically uploaded or assigned to an account.

The guest-count suggestions use representative counts (20, 40, 75, and 120). You can type a specific count instead. The schedule is relative to the event start time because the planner does not request an actual start time.

## API overview

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/plans` | List saved plans |
| `GET` | `/api/plans/<id>` | Read a saved plan |
| `POST` | `/api/plans` | Validate and automatically save a plan; identical submissions are idempotent |
| `PUT` | `/api/plans/<id>` | Validate and update a saved plan |
| `PUT` | `/api/plans/<id>/budget` | Validate and save the seven expense-category allocations |
| `PUT` | `/api/plans/<id>/checklist` | Save a plan's checklist tasks and completion states |
| `PUT` | `/api/plans/<id>/schedule` | Validate and save a plan's event schedule |
| `DELETE` | `/api/plans/<id>` | Delete a saved plan |
| `POST` | `/api/plan-preview` | Generate a plan without saving |
| `POST` | `/api/chat` | Get a rule-based follow-up response |
| `GET` | `/api/auth/me` | Return the current account, if signed in |
| `GET` | `/api/auth/csrf` | Obtain the session CSRF token for API requests |
| `GET` | `/api/conversations` | List the signed-in user's conversations |
| `PUT` | `/api/conversations/<id>` | Save a conversation for the signed-in user |
| `DELETE` | `/api/conversations/<id>` | Delete one of the signed-in user's conversations |

All plan, chat, and conversation APIs require authentication. Every state-changing API request must send the session token in the `X-CSRF-Token` header. Registration and login forms use a hidden `csrf_token` field. The `HttpOnly` and `SameSite=Lax` session cookie is marked `Secure` when `PLANBOT_COOKIE_SECURE=true` or `FLASK_ENV=production`.

Plan creation and updates accept JSON with `event_type`, `event_date` (`YYYY-MM-DD`), `location`, `guests`, `budget`, `theme`, and `food`. `decor` is optional and is derived from the chosen theme when omitted. Budget updates accept `{"allocation": {"Venue": 1000, "Food & catering": 2000, "Decoration": 500, "Photography": 500, "Entertainment": 500, "Transportation": 300, "Miscellaneous": 200}}`; every category is required, amounts must be non-negative with at most two decimal places, and their sum must not exceed the plan's budget.

Checklist updates accept `{"items": [{"task": "Confirm the venue", "completed": true}]}`. Schedule updates accept `{"schedule": [{"time": "09:30 AM", "title": "Guest arrival", "detail": "Welcome attendees."}]}`. Both are stored in the saved plan's JSON and scoped by its plan ID.

## Project structure

```text
PlanBot/
|-- app.py
|-- database.py
|-- planner.py
|-- .env.example
|-- requirements.txt
|-- README.md
|-- .gitignore
|-- .vscode/launch.json
|-- tests/
|   `-- test_plan_generation.py
|-- templates/
|   |-- index.html
|   |-- auth.html
|   `-- dashboard.html
`-- static/
    |-- planner.html
    |-- css/
    `-- js/
```

This layout follows Flask's server-rendered frontend pattern: Jinja pages and static assets are served by the same app, while `/api/...` routes provide the frontend-to-backend connection. The SQLite database is created locally at `planbot.db` on first startup and is excluded from version control.
## Website Live Link: https://planbot-event-planning-assistant-1.onrender.com
## GitHub notes

The SQLite database, virtual environments, Python caches, and local environment files are excluded by `.gitignore`. The hero photograph is by [Mathis Payet Descombes on Unsplash](https://unsplash.com/photos/wedding-reception-tables-with-floral-centerpieces-wYWeVmlGWHM); it and the fonts load from public CDNs. The event planning, chat, and persistence logic run locally.
