# FoundIt: campus lost & found

BCSE355L Cloud Architecture Design project. A Flask web app hosted on AWS.

Students post things they **lost** or **found** (with a photo). Everyone can browse,
search and filter the board. Each report shows **likely matches** (a lost bottle and a
found bottle near the same date), and the person who posted it marks it **returned**
with a private 6-character code.

## Features

| Feature | Where it uses AWS |
|---|---|
| Post a lost/found report with photo | Photo → S3, report → DynamoDB |
| Browse, search, filter by lost/found, category, place | DynamoDB scan + filters |
| Likely matches on every item page | Opposite type, same category, ±14 days |
| Mark returned with a private code (stored hashed) | DynamoDB update |
| Server name + availability zone in the footer | EC2 instance metadata |
| Monitoring and email alarms | CloudWatch dashboard + alarms |
| Firewall | VPC security group: port 80 open, SSH only from the team's IP |
| No AWS keys in code | IAM role attached to the EC2 instance |

## Files

```
foundit/
├── app.py              # all backend logic (pages, validation, photo upload)
├── storage.py          # database layer: DynamoDB on AWS, SQLite on a laptop
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

The EC2 instance sets itself up from the user-data script in the setup prompt: it clones
this repo, installs the requirements, writes a `.env` with

```
DYNAMODB_TABLE=foundit-reports
S3_BUCKET=foundit-photos-24bce1890
AWS_REGION=ap-south-1
```

and runs the app with gunicorn on port 80 as a systemd service.

No AWS keys are stored anywhere. On EC2, boto3 uses the IAM role attached to the instance.
