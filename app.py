"""PlanBot: Flask application, authentication, and JSON API."""
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import timedelta
from functools import wraps

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv(Path(__file__).with_name(".env"))

from database import (
    create_plan,
    create_user,
    delete_conversation,
    delete_plan,
    get_conversations,
    get_plan,
    get_plans,
    get_user_by_email,
    get_user_by_id,
    init_db,
    save_conversation,
    sync_plan_conversations,
    update_plan,
    update_plan_allocation,
    update_plan_checklist,
    update_plan_schedule,
    validate_conversation,
)
from planner import build_plan, chatbot_reply, validate_allocation, validate_event


def _load_development_secret():
    secret_path = Path(__file__).with_name("instance") / "session_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with secret_path.open("x", encoding="utf-8") as secret_file:
            secret_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return secret_path.read_text(encoding="utf-8").strip()


app = Flask(__name__)
configured_secret = os.environ.get("PLANBOT_SECRET_KEY")
if configured_secret and len(configured_secret) < 32:
    raise RuntimeError("PLANBOT_SECRET_KEY must contain at least 32 characters.")
app.config.update(
    SECRET_KEY=configured_secret or _load_development_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("PLANBOT_COOKIE_SECURE", "").lower() == "true"
        or os.environ.get("FLASK_ENV") == "production"
    ),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)
if not configured_secret:
    app.logger.warning(
        "PLANBOT_SECRET_KEY is unset; using a local key in the ignored instance directory."
    )
init_db()


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def current_user():
    user_id = session.get("user_id")
    return get_user_by_id(user_id) if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in to continue."), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_auth_context():
    return {"current_user": current_user(), "csrf_token": csrf_token}


@app.before_request
def enforce_authentication_and_csrf():
    user_id = session.get("user_id")
    if user_id and not get_user_by_id(user_id):
        session.clear()
        user_id = None

    if request.path == "/logout" and not user_id:
        return redirect(url_for("login"))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            if request.path.startswith("/api/"):
                return jsonify(error="The request verification token is missing or invalid."), 400
            return "The request verification token is missing or invalid.", 400


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path.startswith("/api/") or request.path in {
        "/login", "/register", "/dashboard", "/static/planner.html"
    }:
        response.headers["Cache-Control"] = "no-store"
    return response


def _normalize_email(value):
    if not isinstance(value, str):
        return ""
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return ""
    return email


def _render_auth(mode, error=None, email=""):
    return render_template("auth.html", mode=mode, error=error, email=email)


def _start_user_session(user_id):
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)


@app.get("/")
def entry():
    return redirect(url_for("home" if current_user() else "login"))


@app.get("/home")
def home():
    return render_template("index.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "GET":
        return _render_auth("register")

    email = _normalize_email(request.form.get("email"))
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if not email:
        return _render_auth("register", "Enter a valid email address.", request.form.get("email", "")), 400
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        return _render_auth(
            "register",
            "Use a password between 12 and 128 characters.",
            email,
        ), 400
    if password != confirmation:
        return _render_auth("register", "The passwords do not match.", email), 400

    try:
        user_id = create_user(email, generate_password_hash(password))
    except sqlite3.IntegrityError:
        return _render_auth("register", "An account with that email already exists.", email), 409
    _start_user_session(user_id)
    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "GET":
        return _render_auth("login")

    email = _normalize_email(request.form.get("email"))
    password = request.form.get("password", "")
    user = get_user_by_email(email) if email else None
    if not user or not check_password_hash(user["password_hash"], password):
        return _render_auth("login", "Email or password is incorrect.", email), 401
    _start_user_session(user["id"])
    return redirect(url_for("home"))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/api/auth/csrf")
def auth_csrf():
    return jsonify(csrf_token=csrf_token())


@app.get("/api/auth/me")
def auth_me():
    user = current_user()
    return jsonify(user=user)


@app.post("/api/auth/logout")
@login_required
def auth_logout():
    session.clear()
    return jsonify(message="You have been logged out.")


@app.get("/api/plans")
@login_required
def plans_list():
    return jsonify(get_plans(session["user_id"]))


@app.get("/api/plans/<int:plan_id>")
@login_required
def plan_detail(plan_id):
    plan = get_plan(plan_id, session["user_id"])
    if not plan:
        return jsonify(error="Plan not found."), 404
    return jsonify(plan)


@app.post("/api/plans")
@login_required
def plans_create():
    data = request.get_json(silent=True) or {}
    errors = validate_event(data)
    if errors:
        return jsonify(error="Please check the event details.", fields=errors), 400
    plan = build_plan(data)
    plan_id, created = create_plan(plan, session["user_id"])
    result = get_plan(plan_id, session["user_id"])
    result["already_saved"] = not created
    return jsonify(result), 201 if created else 200


@app.put("/api/plans/<int:plan_id>")
@login_required
def plans_update(plan_id):
    existing_plan = get_plan(plan_id, session["user_id"])
    if not existing_plan:
        return jsonify(error="Plan not found."), 404
    data = request.get_json(silent=True) or {}
    errors = validate_event(data, allow_past_date=True)
    if errors:
        return jsonify(error="Please check the event details.", fields=errors), 400
    allocation = existing_plan.get("allocation")
    if allocation and not validate_allocation(allocation, data["budget"]):
        plan = build_plan(data, allocation=allocation)
    else:
        plan = build_plan(data)
    if existing_plan["event_type"] == plan["event_type"]:
        plan["schedule"] = existing_plan["schedule"]
        plan["checklist_items"] = existing_plan["checklist_items"]
        plan["checklist"] = [item["task"] for item in existing_plan["checklist_items"]]
    try:
        update_plan(plan_id, session["user_id"], plan)
    except sqlite3.IntegrityError:
        return jsonify(error="Another saved plan already has these same event details."), 409
    updated_plan = get_plan(plan_id, session["user_id"])
    sync_plan_conversations(session["user_id"], updated_plan)
    return jsonify(updated_plan)


@app.put("/api/plans/<int:plan_id>/budget")
@login_required
def plans_budget_update(plan_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "allocation" not in data:
        return jsonify(error="Provide expense amounts for each budget category."), 400
    plan, errors = update_plan_allocation(plan_id, session["user_id"], data["allocation"])
    if plan is None and errors is None:
        return jsonify(error="Plan not found."), 404
    if errors:
        return jsonify(error="Please check the expense amounts.", fields=errors), 400
    sync_plan_conversations(session["user_id"], plan)
    return jsonify(plan)


@app.put("/api/plans/<int:plan_id>/checklist")
@login_required
def plans_checklist_update(plan_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "items" not in data:
        return jsonify(error="Provide checklist tasks and completion states."), 400
    plan, errors = update_plan_checklist(plan_id, session["user_id"], data["items"])
    if plan is None and errors is None:
        return jsonify(error="Plan not found."), 404
    if errors:
        return jsonify(error="Please check the checklist tasks.", fields=errors), 400
    sync_plan_conversations(session["user_id"], plan)
    return jsonify(plan)


@app.put("/api/plans/<int:plan_id>/schedule")
@login_required
def plans_schedule_update(plan_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "schedule" not in data:
        return jsonify(error="Provide scheduled activities."), 400
    plan, errors = update_plan_schedule(plan_id, session["user_id"], data["schedule"])
    if plan is None and errors is None:
        return jsonify(error="Plan not found."), 404
    if errors:
        return jsonify(error="Please check the scheduled activities.", fields=errors), 400
    sync_plan_conversations(session["user_id"], plan)
    return jsonify(plan)


@app.delete("/api/plans/<int:plan_id>")
@login_required
def plans_delete(plan_id):
    if not delete_plan(plan_id, session["user_id"]):
        return jsonify(error="Plan not found."), 404
    return jsonify(message="Plan deleted.")


@app.get("/api/conversations")
@login_required
def conversations_list():
    return jsonify(get_conversations(session["user_id"]))


@app.put("/api/conversations/<conversation_id>")
@login_required
def conversations_save(conversation_id):
    conversation = request.get_json(silent=True)
    errors = validate_conversation(conversation, conversation_id)
    if errors:
        return jsonify(error="Please check the conversation data.", fields=errors), 400
    save_conversation(session["user_id"], conversation)
    return jsonify(conversation)


@app.delete("/api/conversations/<conversation_id>")
@login_required
def conversations_delete(conversation_id):
    if not delete_conversation(session["user_id"], conversation_id):
        return jsonify(error="Conversation not found."), 404
    return jsonify(message="Conversation deleted.")


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="Send the chat message as a JSON object."), 400
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify(error="Message cannot be empty."), 400
    event = data.get("event", {})
    if not isinstance(event, dict):
        event = {}
    history = data.get("history", [])
    if not isinstance(history, list) or len(history) > 12:
        return jsonify(error="Chat history must contain no more than 12 messages."), 400
    for item in history:
        if (
            not isinstance(item, dict)
            or item.get("role") not in {"user", "assistant"}
            or not isinstance(item.get("text", ""), str)
            or len(item.get("text", "")) > 8000
        ):
            return jsonify(error="Chat history contains an invalid message."), 400
    return jsonify(reply=chatbot_reply(message, event, history))


@app.post("/api/plan-preview")
def plan_preview():
    data = request.get_json(silent=True) or {}
    errors = validate_event(data)
    if errors:
        return jsonify(error="Please check the event details.", fields=errors), 400
    return jsonify(build_plan(data))


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="That resource was not found."), 404
    return render_template("index.html"), 404


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="The request is too large."), 413


@app.errorhandler(500)
def server_error(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="Something went wrong. Please try again."), 500
    return "Something went wrong. Please try again.", 500


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
