# FoundIt: campus lost & found

BCSE355L Cloud Architecture Design project. A Flask web app hosted on AWS.

Students post things they **lost** or **found** (with a photo). Everyone can browse,
search and filter the board. Each report shows **likely matches** (a lost bottle and a
found bottle near the same date), and the person who posted it marks it **returned**
with a private 6-character code.

## Features

| Feature | Where it uses AWS |
|---|---|
| Post a lost/found report with photo | Photo → S3, report → RDS |
| Browse, search, filter by lost/found, category, place | RDS queries |
| Likely matches on every item page | RDS query (opposite type, same category, ±14 days) |
| Mark returned with a private code (stored hashed) | RDS update |
| Footer and "How it's built" page show server name + availability zone | EC2 metadata, proves the load balancer works |
| `/health` endpoint | ELB health check |
| Photo upload triggers a function | S3 → Lambda → CloudWatch Logs |

## Files

```
foundit/
├── app.py              # all backend logic
├── seed.py             # optional: adds 9 sample reports (code DEMO01)
├── requirements.txt
├── .env.example        # settings: laptop mode vs AWS mode
├── static/style.css
└── templates/
    ├── base.html       # header + footer (footer shows which server answered)
    ├── _icons.html     # icons + the luggage-tag card
    ├── index.html      # the board: hero, filters, tags
    ├── report.html     # report form
    ├── item.html       # item page, matches, mark returned
    ├── how.html        # "How it's built": live server info + AWS flow
    └── 404.html
```

## Run on your laptop (Windows)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python seed.py
python app.py
```

Open http://127.0.0.1:5001

`python seed.py` is optional; it fills the board with sample reports.

## Run on AWS (EC2)

In `.env`, comment out the laptop lines and fill in the AWS ones:

```
DATABASE_URL=mysql+pymysql://admin:PASSWORD@RDS-ENDPOINT:3306/foundit
S3_BUCKET=foundit-photos-24bce1890
AWS_REGION=ap-south-1
```

Then run with gunicorn on port 80:

```
sudo venv/bin/gunicorn -w 2 -b 0.0.0.0:80 app:app
```

No AWS keys are stored anywhere. On EC2, boto3 uses the IAM role attached to the
instance. The `reports` table is created automatically on first start.
