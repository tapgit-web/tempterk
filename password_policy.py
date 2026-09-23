"""Password lifetime metadata, shared by account creation and sign-in."""
from datetime import datetime, timedelta, timezone

PASSWORD_EXPIRY_DAYS = 90
PASSWORD_EXPIRY_OPTIONS = (1, 30, 60, 90)


def password_changed_at():
    return datetime.now(timezone.utc).isoformat()


def password_expiry(user):
    try:
        changed = datetime.fromisoformat(user['password_changed_at'])
        if changed.tzinfo is None:
            return None
        days = user.get('password_expiry_days', PASSWORD_EXPIRY_DAYS)
        if type(days) is not int or days not in PASSWORD_EXPIRY_OPTIONS:
            return None
        return changed + timedelta(days=days)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def password_expired(user, now=None):
    now = now or datetime.now(timezone.utc)
    expiry = password_expiry(user)
    return expiry is None or now >= expiry or expiry - now > timedelta(days=user.get('password_expiry_days', PASSWORD_EXPIRY_DAYS))


def password_expiry_label(user):
    expiry = password_expiry(user)
    if password_expired(user):
        return 'EXPIRED - change required'
    return expiry.astimezone().strftime('%d-%m-%y %H:%M')
