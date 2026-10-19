"""
FoundIt - campus lost & found portal (BCSE355L AWS project).

People report things they lost or found (with a photo). Others browse, filter and
search the reports, the app suggests likely matches between lost and found items,
and the person who reported an item marks it returned with a private code.

The same code runs in two modes, chosen by the .env file:
  * Laptop: SQLite database file + photos saved in static/uploads
  * AWS:    RDS MySQL database + photos uploaded to S3 (via the EC2 IAM role)
"""
import os
import secrets
import socket
import urllib.request
import uuid
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import (Column, Date, DateTime, Integer, MetaData, String, Table, Text,
                        and_, create_engine, desc, func, insert, or_, select, update)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# ---------------------------------------------------------------- configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///foundit.db")
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

CATEGORIES = {
    "electronics": "Electronics",
    "id-keys": "ID cards & keys",
    "books": "Books & notes",
    "bottles": "Bottles & lunchboxes",
    "bags": "Bags & clothing",
    "wallets": "Wallets & purses",
    "other": "Something else",
}
LOCATIONS = [
    "Academic block", "Library", "Labs", "Food court", "Hostel",
    "Sports ground", "Auditorium", "Parking", "Campus bus", "Somewhere else",
]
KINDS = {"lost": "Lost", "found": "Found"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB photo limit

# ---------------------------------------------------------------- database
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=280)
metadata = MetaData()

reports = Table(
    "reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(10), nullable=False),          # lost / found
    Column("title", String(120), nullable=False),
    Column("category", String(30), nullable=False),
    Column("location", String(40), nullable=False),
    Column("event_date", Date, nullable=False),           # day it was lost / found
    Column("description", Text),
    Column("image_url", String(500)),
    Column("contact_name", String(80), nullable=False),
    Column("contact_info", String(120), nullable=False),
    Column("status", String(10), nullable=False, default="open"),  # open / returned
    Column("manage_code_hash", String(255), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("resolved_at", DateTime),
)

metadata.create_all(engine)  # creates the table on first start (SQLite or RDS)

# ---------------------------------------------------------------- photo storage
_s3 = None


def s3_client():
    global _s3
    if _s3 is None:
        import boto3  # only needed in AWS mode
        _s3 = boto3.client("s3", region_name=AWS_REGION)  # credentials come from the EC2 IAM role
    return _s3


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_photo(file):
    """Store the photo in S3 (AWS) or static/uploads (laptop). Returns the URL."""
    ext = file.filename.rsplit(".", 1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    if S3_BUCKET:
        key = f"uploads/{name}"  # the Lambda trigger listens on this prefix
        s3_client().upload_fileobj(file, S3_BUCKET, key, ExtraArgs={"ContentType": file.mimetype})
        return f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{key}"
    folder = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, name))
    return url_for("static", filename=f"uploads/{name}")


# ---------------------------------------------------------------- server identity
_server = None


def _ec2_metadata(path, token):
    req = urllib.request.Request(f"http://169.254.169.254/latest/meta-data/{path}",
                                 headers={"X-aws-ec2-metadata-token": token})
    return urllib.request.urlopen(req, timeout=0.3).read().decode()


def server_identity():
    """Hostname + availability zone. On EC2 this comes from the instance metadata
    service (IMDSv2); on a laptop the lookup fails fast and we say 'your laptop'."""
    global _server
    if _server is None:
        info = {"host": socket.gethostname(), "zone": "your laptop", "instance": None}
        try:
            req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                         headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"})
            token = urllib.request.urlopen(req, timeout=0.3).read().decode()
            info["zone"] = _ec2_metadata("placement/availability-zone", token)
            info["instance"] = _ec2_metadata("instance-id", token)
        except Exception:
            pass
        _server = info
    return _server


@app.context_processor
def inject_globals():
    return {
        "server": server_identity(),
        "db_mode": "Amazon RDS (MySQL)" if DATABASE_URL.startswith("mysql") else "SQLite file",
        "storage_mode": f"Amazon S3 ({S3_BUCKET})" if S3_BUCKET else "Local folder",
        "CATEGORIES": CATEGORIES,
        "LOCATIONS": LOCATIONS,
        "KINDS": KINDS,
        "today": date.today(),
    }


@app.template_filter("tagno")
def tag_number(n):
    return f"{n:04d}"


@app.template_filter("nicedate")
def nice_date(d):
    if not d:
        return ""
    days = (date.today() - (d.date() if isinstance(d, datetime) else d)).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if 1 < days < 7:
        return f"{days} days ago"
    return d.strftime("%d %b %Y")


# ---------------------------------------------------------------- helpers
def get_report(report_id):
    with engine.connect() as conn:
        row = conn.execute(select(reports).where(reports.c.id == report_id)).mappings().first()
    if row is None:
        abort(404)
    return row


def find_matches(item, limit=4):
    """Likely matches: opposite kind, same category, still open, within 14 days.
    Same location ranks first, then closest date."""
    opposite = "found" if item["kind"] == "lost" else "lost"
    lo, hi = item["event_date"] - timedelta(days=14), item["event_date"] + timedelta(days=14)
    stmt = select(reports).where(and_(
        reports.c.kind == opposite,
        reports.c.category == item["category"],
        reports.c.status == "open",
        reports.c.event_date.between(lo, hi),
    ))
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    rows = sorted(rows, key=lambda r: (r["location"] != item["location"],
                                       abs((r["event_date"] - item["event_date"]).days)))
    return rows[:limit]


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "")
    category = request.args.get("category", "")
    location = request.args.get("location", "")
    show_returned = request.args.get("returned") == "1"

    stmt = select(reports).order_by(desc(reports.c.event_date), desc(reports.c.id))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(reports.c.title.like(like), reports.c.description.like(like)))
    if kind in KINDS:
        stmt = stmt.where(reports.c.kind == kind)
    if category in CATEGORIES:
        stmt = stmt.where(reports.c.category == category)
    if location in LOCATIONS:
        stmt = stmt.where(reports.c.location == location)
    if not show_returned:
        stmt = stmt.where(reports.c.status == "open")

    with engine.connect() as conn:
        rows = conn.execute(stmt.limit(60)).mappings().all()
        returned_count = conn.execute(
            select(func.count()).select_from(reports).where(reports.c.status == "returned")).scalar()
        open_count = conn.execute(
            select(func.count()).select_from(reports).where(reports.c.status == "open")).scalar()

    filtered = bool(q or kind or category or location or show_returned)
    return render_template("index.html", items=rows, q=q, kind=kind, category=category,
                           location=location, show_returned=show_returned, filtered=filtered,
                           returned_count=returned_count, open_count=open_count)


@app.route("/report", methods=["GET", "POST"])
def report():
    if request.method == "GET":
        kind = request.args.get("kind", "lost")
        return render_template("report.html", form={"kind": kind if kind in KINDS else "lost"}, errors={})

    f = request.form
    data = {k: f.get(k, "").strip() for k in
            ("kind", "title", "category", "location", "event_date", "description",
             "contact_name", "contact_info")}
    photo = request.files.get("photo")
    errors = {}

    if data["kind"] not in KINDS:
        errors["kind"] = "Choose whether you lost or found it."
    if not data["title"]:
        errors["title"] = "Give the item a short name, like \"Black Milton bottle\"."
    if data["category"] not in CATEGORIES:
        errors["category"] = "Pick the closest category."
    if data["location"] not in LOCATIONS:
        errors["location"] = "Pick where it was lost or found."
    try:
        event_date = datetime.strptime(data["event_date"], "%Y-%m-%d").date()
        if event_date > date.today():
            errors["event_date"] = "The date can't be in the future."
    except ValueError:
        errors["event_date"] = "Pick the date."
        event_date = None
    if not data["contact_name"]:
        errors["contact_name"] = "Add your name."
    if not data["contact_info"]:
        errors["contact_info"] = "Add a phone number or email so people can reach you."
    if photo and photo.filename and not allowed_file(photo.filename):
        errors["photo"] = "Use a PNG, JPG, GIF or WEBP photo."
    if data["kind"] == "found" and not (photo and photo.filename) and "photo" not in errors:
        errors["photo"] = "Add a photo so the owner can recognise it."

    if errors:
        return render_template("report.html", form=data, errors=errors), 400

    image_url = save_photo(photo) if photo and photo.filename else None
    manage_code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))

    with engine.begin() as conn:
        result = conn.execute(insert(reports).values(
            kind=data["kind"], title=data["title"], category=data["category"],
            location=data["location"], event_date=event_date, description=data["description"],
            image_url=image_url, contact_name=data["contact_name"],
            contact_info=data["contact_info"], status="open",
            manage_code_hash=generate_password_hash(manage_code), created_at=datetime.now(),
        ))
        new_id = result.inserted_primary_key[0]

    return redirect(url_for("item", report_id=new_id, new=manage_code))


@app.route("/item/<int:report_id>")
def item(report_id):
    row = get_report(report_id)
    new_code = request.args.get("new")  # shown once, right after reporting
    matches = find_matches(row) if row["status"] == "open" else []
    return render_template("item.html", item=row, matches=matches, new_code=new_code)


@app.route("/item/<int:report_id>/returned", methods=["POST"])
def mark_returned(report_id):
    row = get_report(report_id)
    code = request.form.get("code", "").strip().upper()
    if row["status"] == "returned":
        flash("This item is already marked as returned.", "info")
    elif not code or not check_password_hash(row["manage_code_hash"], code):
        flash("That code doesn't match. Use the 6-character code you got when you reported this item.", "error")
    else:
        with engine.begin() as conn:
            conn.execute(update(reports).where(reports.c.id == report_id)
                         .values(status="returned", resolved_at=datetime.now()))
        flash("Marked as returned. Thanks for closing the loop.", "success")
    return redirect(url_for("item", report_id=report_id))


@app.route("/how-it-works")
def how_it_works():
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(reports)).scalar()
    return render_template("how.html", total=total)


@app.route("/health")
def health():
    """Load balancer health check."""
    return "OK", 200


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(_):
    flash("That photo is over 5 MB. Pick a smaller one.", "error")
    return redirect(url_for("report"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
