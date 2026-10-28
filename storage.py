"""
Where FoundIt keeps its reports.

  * Laptop: a local SQLite file (nothing to install)
  * AWS:    an Amazon DynamoDB table (set DYNAMODB_TABLE in .env)

Both stores return reports as plain dicts with the same keys, so the rest of the
app doesn't care which one is in use.
"""
import os
from datetime import date, datetime

FIELDS = ("kind", "title", "category", "location", "event_date", "description", "image_url",
          "contact_name", "contact_info", "status", "manage_code_hash", "created_at", "resolved_at")


def _matches_filters(r, q, kind, category, location, show_returned):
    if q and q.lower() not in f"{r['title']} {r.get('description') or ''}".lower():
        return False
    if kind and r["kind"] != kind:
        return False
    if category and r["category"] != category:
        return False
    if location and r["location"] != location:
        return False
    if not show_returned and r["status"] != "open":
        return False
    return True


def _newest_first(rows):
    return sorted(rows, key=lambda r: (r["event_date"], r["id"]), reverse=True)


# --------------------------------------------------------------------- DynamoDB (AWS)
class DynamoStore:
    """Table: partition key "id" (Number). Item id=0 is a counter that hands out
    the next report number (No. 0001, 0002, ...)."""

    def __init__(self, table_name, region):
        import boto3  # credentials come from the EC2 IAM role, never from code
        self.name = table_name
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def label(self):
        return f"Amazon DynamoDB ({self.name})"

    @staticmethod
    def _from_item(item):
        r = {k: item.get(k) for k in FIELDS}
        r["id"] = int(item["id"])
        r["event_date"] = date.fromisoformat(item["event_date"])
        r["created_at"] = datetime.fromisoformat(item["created_at"])
        r["resolved_at"] = datetime.fromisoformat(item["resolved_at"]) if item.get("resolved_at") else None
        return r

    def _scan_all(self):
        items, kwargs = [], {}
        while True:
            page = self.table.scan(**kwargs)
            items.extend(page["Items"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return [self._from_item(i) for i in items if int(i["id"]) != 0]

    def list(self, q="", kind="", category="", location="", show_returned=False, limit=60):
        rows = [r for r in self._scan_all() if _matches_filters(r, q, kind, category, location, show_returned)]
        return _newest_first(rows)[:limit]

    def counts(self):
        rows = self._scan_all()
        open_n = sum(1 for r in rows if r["status"] == "open")
        return open_n, len(rows) - open_n, len(rows)

    def get(self, report_id):
        item = self.table.get_item(Key={"id": report_id}).get("Item")
        return self._from_item(item) if item and report_id != 0 else None

    def create(self, **data):
        counter = self.table.update_item(
            Key={"id": 0}, UpdateExpression="ADD next_id :one",
            ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
        new_id = int(counter["Attributes"]["next_id"])
        item = {"id": new_id}
        for k in FIELDS:
            v = data.get(k)
            if v is None:
                continue
            item[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
        self.table.put_item(Item=item)
        return new_id

    def mark_returned(self, report_id):
        self.table.update_item(
            Key={"id": report_id},
            UpdateExpression="SET #s = :r, resolved_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":r": "returned", ":t": datetime.now().isoformat()})

    def candidates(self, kind, category):
        return [r for r in self._scan_all()
                if r["kind"] == kind and r["category"] == category and r["status"] == "open"]


# --------------------------------------------------------------------- SQLite (laptop)
class SQLStore:
    def __init__(self, url):
        from sqlalchemy import (Column, Date, DateTime, Integer, MetaData, String, Table, Text,
                                create_engine)
        self.engine = create_engine(url, pool_pre_ping=True)
        md = MetaData()
        self.t = Table(
            "reports", md,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("kind", String(10), nullable=False),
            Column("title", String(120), nullable=False),
            Column("category", String(30), nullable=False),
            Column("location", String(40), nullable=False),
            Column("event_date", Date, nullable=False),
            Column("description", Text),
            Column("image_url", String(500)),
            Column("contact_name", String(80), nullable=False),
            Column("contact_info", String(120), nullable=False),
            Column("status", String(10), nullable=False),
            Column("manage_code_hash", String(255), nullable=False),
            Column("created_at", DateTime, nullable=False),
            Column("resolved_at", DateTime),
        )
        md.create_all(self.engine)

    def label(self):
        return "SQLite file"

    def _rows(self, stmt):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]

    def list(self, q="", kind="", category="", location="", show_returned=False, limit=60):
        from sqlalchemy import desc, or_, select
        t = self.t
        stmt = select(t).order_by(desc(t.c.event_date), desc(t.c.id))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(t.c.title.like(like), t.c.description.like(like)))
        if kind:
            stmt = stmt.where(t.c.kind == kind)
        if category:
            stmt = stmt.where(t.c.category == category)
        if location:
            stmt = stmt.where(t.c.location == location)
        if not show_returned:
            stmt = stmt.where(t.c.status == "open")
        return self._rows(stmt.limit(limit))

    def counts(self):
        from sqlalchemy import func, select
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(self.t)).scalar()
            returned = conn.execute(select(func.count()).select_from(self.t)
                                    .where(self.t.c.status == "returned")).scalar()
        return total - returned, returned, total

    def get(self, report_id):
        from sqlalchemy import select
        rows = self._rows(select(self.t).where(self.t.c.id == report_id))
        return rows[0] if rows else None

    def create(self, **data):
        from sqlalchemy import insert
        with self.engine.begin() as conn:
            res = conn.execute(insert(self.t).values(**{k: data.get(k) for k in FIELDS}))
            return res.inserted_primary_key[0]

    def mark_returned(self, report_id):
        from sqlalchemy import update
        with self.engine.begin() as conn:
            conn.execute(update(self.t).where(self.t.c.id == report_id)
                         .values(status="returned", resolved_at=datetime.now()))

    def candidates(self, kind, category):
        from sqlalchemy import select
        t = self.t
        return self._rows(select(t).where(t.c.kind == kind, t.c.category == category, t.c.status == "open"))


def make_store():
    table = os.getenv("DYNAMODB_TABLE", "").strip()
    if table:
        return DynamoStore(table, os.getenv("AWS_REGION", "ap-south-1"))
    return SQLStore(os.getenv("DATABASE_URL", "sqlite:///foundit.db"))
