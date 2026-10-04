import re
from datetime import datetime


def clean_phone(phone):
    if not phone:
        return ""
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return phone


def clean_price(price_str):
    if not price_str:
        return 0.0
    match = re.search(r'[\d,]+\.?\d*', str(price_str))
    if match:
        return float(match.group().replace(',', ''))
    return 0.0


def clean_name(name):
    if not name:
        return ""
    name = ' '.join(name.split())
    name = name.title()
    return name


def clean_email(email):
    if not email:
        return ""
    email = email.strip().lower()
    return email


def parse_date(date_str, formats=None):
    if not date_str:
        return None
    if not formats:
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def validate_email(email):
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_phone(phone):
    if not phone:
        return False
    digits = re.sub(r'\D', '', phone)
    return len(digits) == 10


def validate_url(url):
    if not url:
        return False
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(pattern, url))


def deduplicate(records, key_func):
    seen = set()
    unique = []
    for record in records:
        key = key_func(record)
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def clean_records(records, required_fields=None):
    clean = []
    skipped = 0
    for record in records:
        if required_fields:
            if all(record.get(field) for field in required_fields):
                clean.append(record)
            else:
                skipped += 1
        else:
            clean.append(record)
    return clean, skipped
