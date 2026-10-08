from functools import wraps
from flask import abort
from flask_login import current_user

ROLES = ("it_admin", "admin", "operator", "mechanic", "ei_c", "user")

IT_ADMIN = "it_admin"
ADMIN    = "admin"
OPERATOR = "operator"
MECHANIC = "mechanic"
EICC     = "ei_c"
USER     = "user"

# Convenience groupings
ADMIN_ROLES    = (IT_ADMIN, ADMIN)
FIELD_ROLES    = (OPERATOR, MECHANIC, EICC)
ALL_ROLES      = ADMIN_ROLES + FIELD_ROLES + (USER,)


def require_role(*roles):
    """Restrict a route to users with one of the given roles."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator
