"""Validation and rule-based event planning helpers."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

EVENT_TYPES = ("Birthday Party", "Wedding", "College Event", "Corporate Event", "Engagement", "Anniversary")
BUDGET_SHARES = {
    "Venue": .20,
    "Food & catering": .28,
    "Decoration": .14,
    "Photography": .10,
    "Entertainment": .08,
    "Transportation": .07,
    "Miscellaneous": .08,
}
BUDGET_CATEGORIES = tuple(BUDGET_SHARES)


def validate_event(data, *, allow_past_date=False):
    errors = {}
    if not isinstance(data, dict):
        return {"event": "Provide event details as an object."}
    if str(data.get("event_type", "")).strip() not in EVENT_TYPES:
        errors["event_type"] = "Choose one of the supported event types."
    guest_value = data.get("guests", 0)
    try:
        if isinstance(guest_value, bool) or not re.fullmatch(r"\d+", str(guest_value).strip()):
            raise ValueError
        guests = int(guest_value)
        if not 1 <= guests <= 10000:
            raise ValueError
    except (TypeError, ValueError):
        errors["guests"] = "Guest count must be between 1 and 10,000."
    try:
        if isinstance(data.get("budget"), bool):
            raise ValueError
        budget = Decimal(str(data.get("budget", 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not budget.is_finite() or not Decimal("1.00") <= budget <= Decimal("100000000.00"):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        errors["budget"] = "Enter a budget greater than zero."
    date_value = data.get("event_date", "")
    try:
        if not isinstance(date_value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            raise ValueError
        selected_date = date.fromisoformat(date_value)
    except ValueError:
        errors["event_date"] = "Choose a valid event date."
    else:
        if not allow_past_date and selected_date < date.today():
            errors["event_date"] = "Choose today or a future date."
    if not isinstance(data.get("location"), str) or len(data["location"].strip()) < 2:
        errors["location"] = "Add a location or city."
    for key, label in (("theme", "theme"), ("food", "food preference")):
        if not isinstance(data.get(key), str) or len(data[key].strip()) < 2:
            errors[key] = f"Add a {label}."
    return errors


def validate_allocation(allocation, budget):
    if not isinstance(allocation, dict):
        return {"allocation": "Provide expense amounts for each budget category."}

    errors = {}
    expected = set(BUDGET_CATEGORIES)
    if set(allocation) - expected:
        errors["allocation"] = "The allocation contains an unsupported expense category."

    amounts = {}
    for category in BUDGET_CATEGORIES:
        if category not in allocation:
            errors[category] = "Enter an expense amount for this category."
            continue
        value = allocation[category]
        try:
            if isinstance(value, bool):
                raise ValueError
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
                raise ValueError
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            amounts[category] = amount
        except (InvalidOperation, TypeError, ValueError):
            errors[category] = "Enter a non-negative amount with up to two decimal places."

    try:
        budget_amount = Decimal(str(budget)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not budget_amount.is_finite() or budget_amount < 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        return {"budget": "The event budget is invalid."}

    if not errors and sum(amounts.values(), Decimal("0.00")) > budget_amount:
        errors["allocation"] = "Allocated expenses cannot exceed the total event budget."
    return errors


def validate_checklist(items):
    if not isinstance(items, list):
        return {"items": "Provide the checklist as a list of tasks."}
    if len(items) > 100:
        return {"items": "A checklist cannot contain more than 100 tasks."}

    errors = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors[f"items.{index}"] = "Each checklist task must include its text and completion state."
            continue
        task = item.get("task")
        if not isinstance(task, str) or not task.strip() or len(task.strip()) > 200:
            errors[f"items.{index}.task"] = "Task text must be between 1 and 200 characters."
        if not isinstance(item.get("completed"), bool):
            errors[f"items.{index}.completed"] = "Task completion must be true or false."
    return errors


def validate_schedule(schedule):
    if not isinstance(schedule, list) or not schedule:
        return {"schedule": "Provide at least one scheduled activity."}
    if len(schedule) > 50:
        return {"schedule": "A schedule cannot contain more than 50 activities."}

    errors = {}
    limits = {"time": 40, "title": 120, "detail": 400}
    for index, activity in enumerate(schedule):
        if not isinstance(activity, dict):
            errors[f"schedule.{index}"] = "Each schedule entry must include its timing and activity details."
            continue
        for field, limit in limits.items():
            value = activity.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                errors[f"schedule.{index}.{field}"] = (
                    f"{field.title()} must be between 1 and {limit} characters."
                )
    return errors


def _decor_preference(data):
    """Keep older saved-plan/edit clients compatible while asking no extra question."""
    if str(data.get("decor", "")).strip():
        return str(data["decor"]).strip()
    theme = str(data.get("theme", "")).strip().lower()
    if "garden" in theme or "floral" in theme:
        return "floral details and warm lighting"
    if "minimal" in theme or "modern" in theme:
        return "minimal styling and clean lines"
    if "classic" in theme or "elegan" in theme:
        return "elegant table settings and soft lighting"
    if "bright" in theme or "playful" in theme:
        return "colourful statement details"
    return "coordinated details inspired by the chosen theme"


def build_plan(data, allocation=None):
    """Create a detailed, deterministic event plan with an exact budget total."""
    budget_decimal = Decimal(str(data["budget"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    budget = float(budget_decimal)
    guests = int(data["guests"])
    event_type = str(data["event_type"]).strip()
    location = str(data["location"]).strip()
    theme = str(data["theme"]).strip()
    food = str(data["food"]).strip()
    decor = _decor_preference(data)
    date_value = str(data["event_date"])
    title = f"{theme} {event_type}".strip()

    # Keep 5% unallocated as a contingency while assigning estimates to every category.
    budget_cents = int(budget_decimal * 100)
    allocation_cents = (
        {
            name: budget_cents * int(share * 100) // 100
            for name, share in BUDGET_SHARES.items()
        }
        if allocation is None
        else {
            name: int(Decimal(str(allocation[name])).quantize(Decimal("0.01")) * 100)
            for name in BUDGET_CATEGORIES
        }
    )
    allocation = {name: cents / 100 for name, cents in allocation_cents.items()}
    allocated_total = sum(allocation_cents.values()) / 100
    remaining_budget = (budget_cents - sum(allocation_cents.values())) / 100

    type_guidance = {
        "Birthday Party": {
            "venue": "A private dining room, community hall, garden venue, or family-friendly restaurant",
            "photography": "Capture candid guest moments, the cake reveal, birthday messages, and a group portrait.",
            "entertainment": "Choose a DJ or playlist suited to the guests, with optional low-pressure games and a clear cake moment.",
            "moments": [("Guest arrival", "Welcome guests with a simple sign-in and refreshments."), ("Celebration opener", "Bring everyone together for a short welcome and the guest of honour's entrance."), ("Meal and mingling", "Serve the meal, leaving time for guests to connect."), ("Cake and main moment", "Schedule cake, photos, and any birthday messages together."), ("Games and close", "Finish with one optional group activity and a clear send-off.")],
            "tasks": ["Confirm the guest list and send invitations", "Book a venue with space for dining and activities", "Order the cake and confirm dietary needs", "Arrange music, games, and a photo moment", "Confirm setup, cleanup, and transport"],
        },
        "Wedding": {
            "venue": "A ceremony-and-reception venue, heritage property, banquet hall, or garden venue",
            "photography": "Prioritize the ceremony, couple and family portraits, meaningful details, speeches, and candid reception moments.",
            "entertainment": "Coordinate ceremony music with a reception DJ or live musicians, and plan transitions around speeches and the first dance.",
            "moments": [("Guest arrival", "Welcome guests and direct them to the ceremony area."), ("Ceremony", "Hold the ceremony with a short buffer for photographs."), ("Welcome drinks", "Move guests to the reception while the couple takes portraits."), ("Meal service", "Serve courses with clear dietary markers."), ("Toasts and first dance", "Keep speeches coordinated and allow a smooth transition to dancing."), ("Farewell", "Thank guests and coordinate the couple's departure and transport.")],
            "tasks": ["Confirm ceremony and reception venues", "Book photography, catering, and essential vendors", "Track RSVPs and meal requirements", "Finalize ceremony order and seating", "Confirm vendor arrival times and payment schedule", "Prepare a weather and transport backup"],
        },
        "College Event": {
            "venue": "A campus auditorium, open quadrangle, seminar hall, or accessible community venue",
            "photography": "Cover the stage programme, student groups, key speakers or performers, and candid audience moments.",
            "entertainment": "Build the programme around student performers, a host, or interactive activities, with tested sound and clear changeovers.",
            "moments": [("Registration", "Open check-in and direct attendees to seating."), ("Opening", "Welcome attendees and explain the event flow."), ("Main programme", "Run talks, performances, or activities with clear transitions."), ("Break and refreshments", "Provide a timed break and keep access routes clear."), ("Closing", "Share acknowledgements, next steps, and a tidy dispersal plan.")],
            "tasks": ["Confirm campus permissions and venue capacity", "Assign student leads for stage, registration, and safety", "Finalize programme, speakers, and technical requirements", "Plan accessible entry, water, and waste collection", "Promote the event and track registrations", "Create a run sheet and backup power plan"],
        },
        "Corporate Event": {
            "venue": "A conference room, hotel meeting space, coworking event floor, or business-class auditorium",
            "photography": "Prioritize speaker portraits, branded details, networking, and any award or recognition moments.",
            "entertainment": "Use a concise host-led programme, suitable background music, or a short team activity that fits the event goals.",
            "moments": [("Check-in and networking", "Welcome attendees and offer refreshments."), ("Opening remarks", "Start on time with objectives and housekeeping."), ("Core programme", "Keep presentations focused and include a short break."), ("Meal and networking", "Make dietary options clear and allow conversation time."), ("Wrap-up", "Summarize outcomes, owners, and follow-up actions.")],
            "tasks": ["Confirm event objectives, audience, and approvals", "Book a venue with AV, Wi-Fi, and accessible access", "Collect attendee and dietary requirements", "Prepare speaker briefs and presentation backups", "Assign a day-of host and vendor contact", "Share agenda, directions, and follow-up plan"],
        },
        "Engagement": {
            "venue": "A private dining room, intimate garden, boutique event space, or family home venue",
            "photography": "Capture the proposal or announcement, couple portraits, family reactions, and relaxed group photos.",
            "entertainment": "Keep the gathering personal with a shared playlist, a short toast, and an optional moment for family messages.",
            "moments": [("Guest arrival", "Welcome family and friends with light refreshments."), ("Welcome and introductions", "Bring key family members together and set a relaxed tone."), ("Engagement moment", "Plan the ring or announcement moment with the photographer ready."), ("Meal and toasts", "Serve food and invite a few short messages."), ("Photos and close", "Allow family photos before a coordinated send-off.")],
            "tasks": ["Agree on guest list and ceremony style", "Choose a venue suited to the family gathering", "Confirm food, dietary needs, and cake", "Plan the announcement or ring moment", "Arrange photography and family portraits", "Share directions and timing with guests"],
        },
        "Anniversary": {
            "venue": "A favourite restaurant, private dining room, garden venue, or intimate banquet space",
            "photography": "Plan a couple portrait, family group photos, and candid coverage of toasts and shared memories.",
            "entertainment": "Consider a favorite playlist, a short slideshow, and a few personal toasts rather than a packed programme.",
            "moments": [("Arrival and welcome", "Greet guests with music and a comfortable welcome area."), ("Opening toast", "Share a short welcome and set the tone for the celebration."), ("Meal", "Serve a relaxed meal with time for conversation."), ("Memory moment", "Show a slideshow or invite a few stories and toasts."), ("Dessert and farewell", "Close with dessert, group photos, and transport reminders.")],
            "tasks": ["Confirm the guest list and event style", "Reserve a comfortable venue and check accessibility", "Collect dietary requirements and choose the menu", "Gather photos or messages for a memory moment", "Arrange music, flowers, and photography", "Confirm timings and guest transport"],
        },
    }
    guidance = type_guidance[event_type]
    overview = (
        f"A {event_type.lower()} for {guests} guests on {date_value} in {location}, "
        f"planned around a {theme} theme and {food} food preferences. "
        f"The total budget is {format_money(budget)}, or about {format_money(budget / guests)} per guest."
    )
    venue_suggestions = [
        f"Shortlist {guidance['venue']} around {location} for at least {guests} guests.",
        "Ask each venue about capacity, accessibility, included furniture, setup access, parking, and cancellation terms.",
        f"With {format_money(allocation['Venue'])} allocated, compare itemized quotes and confirm taxes, service fees, and overtime before booking.",
    ]
    food_suggestions = [
        f"Ask caterers in {location} for a {food} menu with per-person pricing and clearly labelled dietary options.",
        f"For {guests} guests, request a tasting or sample menu and confirm the final headcount one week before the event.",
        f"Keep catering near {format_money(allocation['Food & catering'])}; include water, serving staff, and applicable delivery or service charges in quotes.",
    ]
    decoration_suggestions = [
        f"Use {theme} as the visual guide for the welcome area, tables, and one focused photo backdrop.",
        f"Build around {decor}; reuse flowers or signage across spaces and use warm, venue-safe lighting.",
        f"Prioritize a few visible details and keep purchases or rentals within {format_money(allocation['Decoration'])}.",
    ]
    photography_suggestions = [
        f"In {location}, look for a photographer whose portfolio fits a {theme} {event_type.lower()} and can {guidance['photography'].lower()}",
        f"Reserve about {format_money(allocation['Photography'])} for photography; compare coverage hours, travel, editing, and delivery terms for an event of {guests} guests.",
    ]
    entertainment_suggestions = [
        f"{guidance['entertainment']} Adapt the energy and setup to your {theme} theme and {guests} guests.",
        f"Keep entertainment near {format_money(allocation['Entertainment'])}; confirm equipment, setup time, venue rules, and the finish time before booking.",
    ]
    schedule = []
    for index, (title_part, detail) in enumerate(guidance["moments"]):
        minute = index * 60
        time_label = "Guest arrival" if minute == 0 else f"T+{minute} min"
        schedule.append({"time": time_label, "title": title_part, "detail": detail})
    schedule.insert(0, {"time": "T-60 min", "title": "Final venue and supplier check", "detail": "Confirm access, setup, AV or music, final guest count, and the day-of contact."})
    suggestions = [
        f"At {guests} guests, your working budget is {format_money(budget / guests)} per guest; use this as a quote-comparison guide.",
        f"Your remaining balance of {format_money(remaining_budget)} stays unallocated as a contingency buffer; only commit it after comparing written quotes.",
        "Confirm accessibility, weather backup, and vendor arrival times with the venue before the event week.",
    ]
    return {
        "title": title, "overview": overview, "event_type": event_type, "event_date": date_value,
        "location": location, "guests": guests, "budget": budget,
        "theme": theme, "food": food, "decor": decor,
        "allocation": allocation, "allocated_total": allocated_total,
        "remaining_budget": remaining_budget, "suggestions": suggestions,
        "schedule": schedule, "venue_suggestions": venue_suggestions,
        "food_suggestions": food_suggestions, "decoration_suggestions": decoration_suggestions,
        "photography_suggestions": photography_suggestions,
        "entertainment_suggestions": entertainment_suggestions,
        "checklist": guidance["tasks"],
        "checklist_items": [
            {"task": task, "completed": False}
            for task in guidance["tasks"]
        ],
    }


def format_money(amount):
    """Format Indian rupees consistently in generated recommendations."""
    return f"₹{amount:,.2f}" if round(float(amount), 2) % 1 else f"₹{amount:,.0f}"


def _chat_topics(text):
    topics = []
    topic_terms = {
        "budget": ("budget", "cost", "spend", "allocation", "expense", "money"),
        "food": ("food", "menu", "cater"),
        "venue": ("venue", "location", "place"),
        "decor": ("decor", "decoration", "theme", "colour", "color", "flower", "lighting"),
        "checklist": ("checklist", "schedule", "timeline", "preparation"),
    }
    for topic, terms in topic_terms.items():
        if any(term in text for term in terms):
            topics.append(topic)
    return topics


def chatbot_reply(message, event=None, history=None):
    text = " ".join(str(message).lower().strip().split())
    event = event if isinstance(event, dict) else {}
    history = history if isinstance(history, list) else []
    if not _chat_topics(text) and any(
        word in {"that", "it", "more", "same", "those", "this", "there", "one"}
        for word in text.split()
    ):
        for entry in reversed(history[-8:]):
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                continue
            topics = _chat_topics(entry["text"].lower())
            if len(topics) == 1:
                text = f"{text} {topics[0]}"
                break
    event_type = event.get("event_type") or "event"
    guests = event.get("guests")
    guest_label = f"{guests} guests" if guests else "your guests"
    theme = event.get("theme") or "your chosen theme"

    if not text:
        return "Type a question or tell me what you would like to plan."
    small_talk = text.rstrip(" .!?")
    if re.fullmatch(r"(?:hi|hello|hey)(?: there)?", small_talk):
        return "Hi! Nice to see you. What would you like to plan today?"
    if re.fullmatch(r"how are you(?: doing)?", small_talk):
        return "I’m doing well, thanks for asking! How can I help with your event today?"
    if re.fullmatch(r"how about you", small_talk):
        return "I’m doing well, thank you! What are we planning today?"
    if re.fullmatch(r"i(?:'m| am) (?:fine|good|doing well)", small_talk):
        return "Glad to hear it! What would you like to plan today?"
    if small_talk in {"good morning", "good afternoon", "good evening"}:
        greeting = small_talk.title()
        return f"{greeting}! How can I help you plan today?"
    if any(word in text for word in ("thank", "thanks")):
        return "You’re welcome! What would you like to work on next?"

    recall_fields = (
        ("event_type", ("event type", "occasion")),
        ("event_date", ("date", "day", "when")),
        ("location", ("location", "venue", "place", "where")),
        ("guests", ("guest", "people", "attendees")),
        ("budget", ("budget", "spend", "cost")),
        ("theme", ("theme", "style")),
        ("food", ("food", "menu", "catering")),
    )
    is_recall_question = any(
        phrase in text
        for phrase in ("what did i say", "what did i choose", "what is my", "what's my", "what was my", "do you remember")
    )
    if is_recall_question:
        for field, terms in recall_fields:
            if any(term in text for term in terms):
                value = event.get(field)
                if value is None or value == "":
                    return f"You haven’t shared your {field.replace('_', ' ')} yet."
                if field == "budget":
                    value = format_money(value)
                elif field == "guests":
                    value = f"{value} guests"
                elif field == "event_date":
                    try:
                        value = date.fromisoformat(value).strftime("%d %b %Y")
                    except (TypeError, ValueError):
                        pass
                return f"You told me your {field.replace('_', ' ')} is {value}."

    if any(word in text for word in ("budget", "cost", "spend", "allocation", "expense", "money")):
        if any(word in text for word in ("reduce", "save", "cut", "lower", "affordable", "cheap")):
            budget = event.get("budget")
            budget_note = f" Your current budget is {format_money(budget)}." if budget else ""
            return (
                f"Absolutely. To keep your {event_type} affordable{budget_note}\n"
                "- Compare itemized quotes and ask about service fees.\n"
                "- Choose an off-peak date and reuse or borrow decor.\n"
                "- Set priorities before trimming essentials like food or accessibility."
            )
        budget = event.get("budget")
        if budget and not validate_event(event, allow_past_date=True):
            plan = build_plan(event)
            categories = "\n".join(
                f"- {name}: {format_money(amount)}"
                for name, amount in plan["allocation"].items()
            )
            return (
                f"Sure! Here’s a starting budget for your {event_type} "
                f"({format_money(plan['budget'])} total):\n{categories}\n"
                f"\nAllocated: {format_money(plan['allocated_total'])}"
                f"\nLeft for unexpected costs: {format_money(plan['remaining_budget'])}"
            )
        return (
            "Sure! I can break the budget into venue, food, decor, photography, entertainment, "
            "transport, and miscellaneous costs. What total amount should I plan around?"
        )
    if any(word in text for word in ("food", "menu", "cater")):
        food_style = event.get("food")
        menu = f"{food_style} options" if food_style else "a menu that suits your guests"
        guest_count = f"{guests} guests" if guests else "your guests"
        return (
            f"For your {event_type}, I’d start with {menu} for {guest_count}.\n"
            "- Ask caterers for per-person prices and a written list of extra charges.\n"
            "- Confirm dietary needs, serving staff, and a tasting before booking."
        )
    if any(word in text for word in ("venue", "location", "place")):
        return (
            f"Let’s find a good fit for your {event_type} near {event.get('location', 'your preferred location')}.\n"
            f"- Check that the venue comfortably fits {event.get('guests', 'your')} guests.\n"
            "- Compare what’s included, accessibility, setup time, extra fees, and cancellation terms."
        )
    if any(word in text for word in ("decor", "decoration", "theme", "colour", "color", "flower", "lighting")):
        budget = event.get("budget")
        if any(word in text for word in ("affordable", "cheap", "low-cost", "budget-friendly")):
            try:
                budget_amount = float(budget or 0)
            except (TypeError, ValueError):
                budget_amount = 0
            decor_limit = format_money(budget_amount * .15) if budget_amount > 0 else "a modest portion of your budget"
            return (f"For an affordable {theme} look for your {event_type} for {guest_label}, aim for decor around {decor_limit}. "
                    "Try seasonal greenery, reusable table pieces, and warm lights. Focus the budget on one welcome area and one photo backdrop.")
        return (
            f"For your {theme} {event_type}, keep the look simple and consistent.\n"
            "- Repeat the same colours across the entrance and tables.\n"
            "- Reuse flowers or signage, and check venue rules before adding lights or fixtures."
        )
    if any(word in text for word in ("schedule", "timeline", "run sheet")):
        return (
            "A simple event timeline could be:\n"
            "1. Welcome guests and offer refreshments.\n"
            "2. Give a short welcome, then serve food.\n"
            "3. Leave time for the main celebration and photos.\n"
            "4. Close with a clear send-off and supplier check."
        )
    if any(word in text for word in ("checklist", "next", "start")):
        return (
            "A good next step is to confirm your guest count and date, then book the venue.\n"
            "After that, compare catering quotes, confirm key suppliers, and prepare a day-of schedule."
        )
    return (
        f"I’m happy to help with your {event_type}.\n"
        "You can ask me about the budget, venue, food, theme and decor, schedule, or checklist. "
        "Which one should we tackle?"
    )
