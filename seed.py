"""
Optional: fill the board with sample reports so the site doesn't look empty in a demo.
Run once:  python seed.py
Every sample report uses the code DEMO01 to mark it returned.
Works with both the laptop database and DynamoDB (it uses the same settings as the app).
"""
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()
from storage import make_store  # noqa: E402

store = make_store()
today = date.today()
code_hash = generate_password_hash("DEMO01")

samples = [
    # kind, title, category, location, days ago, description, name, contact, status
    ("found", "Black steel water bottle", "bottles", "Library", 1, "Left on the second-floor reading table, has a small dent near the cap.", "Arjun", "arjun@example.com", "open"),
    ("lost", "Black Milton steel bottle", "bottles", "Library", 2, "Dent near the cap, sticker of a cat on the side.", "Priya", "98400 00001", "open"),
    ("found", "Casio fx-991ES calculator", "electronics", "Academic block", 0, "Found in the classroom after the maths CAT.", "Rahul", "rahul@example.com", "open"),
    ("lost", "College ID card", "id-keys", "Food court", 3, "Blue lanyard, ID name starts with S.", "Sneha", "98400 00002", "open"),
    ("found", "Bunch of keys with red keychain", "id-keys", "Parking", 4, "Three keys, one looks like a cycle lock key.", "Security desk", "Main gate security", "open"),
    ("lost", "Data structures notebook", "books", "Labs", 5, "Classmate long-size notebook, name on the first page.", "Kiran", "kiran@example.com", "open"),
    ("found", "Grey hoodie", "bags", "Sports ground", 2, "Size M, left on the bench near the basketball court.", "Meera", "98400 00003", "open"),
    ("lost", "Brown leather wallet", "wallets", "Campus bus", 6, "Has a library card and some cash.", "Vikram", "vikram@example.com", "returned"),
    ("found", "Wired earphones", "electronics", "Auditorium", 8, "White, found under row F after the club event.", "Divya", "divya@example.com", "returned"),
]

if store.counts()[2]:
    print("The board already has reports, so nothing was added.")
else:
    for kind, title, cat, loc, ago, desc, name, contact, status in samples:
        store.create(
            kind=kind, title=title, category=cat, location=loc,
            event_date=today - timedelta(days=ago), description=desc, image_url=None,
            contact_name=name, contact_info=contact, status=status,
            manage_code_hash=code_hash, created_at=datetime.now(),
            resolved_at=datetime.now() if status == "returned" else None,
        )
    print(f"Added {len(samples)} sample reports. Their code is DEMO01.")
