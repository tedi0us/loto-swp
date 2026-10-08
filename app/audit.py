import json
from flask import request
from app.db import get_db

# Action constants
LOGIN                = "LOGIN"
LOGIN_FAILED         = "LOGIN_FAILED"
LOGOUT               = "LOGOUT"
PASSWORD_CHANGED     = "PASSWORD_CHANGED"
PROFILE_COMPLETED    = "PROFILE_COMPLETED"
ACCESS_DENIED        = "ACCESS_DENIED"

USER_CREATED         = "USER_CREATED"
USER_UPDATED         = "USER_UPDATED"
USER_DELETED         = "USER_DELETED"

CONTRACTOR_CREATED   = "CONTRACTOR_CREATED"
CONTRACTOR_UPDATED   = "CONTRACTOR_UPDATED"

EQUIPMENT_CREATED    = "EQUIPMENT_CREATED"
EQUIPMENT_UPDATED    = "EQUIPMENT_UPDATED"

SWP_CREATED          = "SWP_CREATED"
SWP_UPDATED          = "SWP_UPDATED"
SWP_SUBMITTED        = "SWP_SUBMITTED"
SWP_FORWARDED        = "SWP_FORWARDED"
SWP_APPROVED         = "SWP_APPROVED"
SWP_RECALLED         = "SWP_RECALLED"
SWP_CLOSED           = "SWP_CLOSED"

LOTO_CREATED         = "LOTO_CREATED"
LOTO_UPDATED         = "LOTO_UPDATED"
LOTO_SUBMITTED       = "LOTO_SUBMITTED"
LOTO_FORWARDED       = "LOTO_FORWARDED"
LOTO_ACTIVATED       = "LOTO_ACTIVATED"
LOTO_RELEASED        = "LOTO_RELEASED"

ALL_ACTIONS = (
    LOGIN, LOGIN_FAILED, LOGOUT, PASSWORD_CHANGED, PROFILE_COMPLETED, ACCESS_DENIED,
    USER_CREATED, USER_UPDATED, USER_DELETED,
    CONTRACTOR_CREATED, CONTRACTOR_UPDATED,
    EQUIPMENT_CREATED, EQUIPMENT_UPDATED,
    SWP_CREATED, SWP_UPDATED, SWP_SUBMITTED, SWP_FORWARDED, SWP_APPROVED, SWP_RECALLED, SWP_CLOSED,
    LOTO_CREATED, LOTO_UPDATED, LOTO_SUBMITTED, LOTO_FORWARDED, LOTO_ACTIVATED, LOTO_RELEASED,
)


def _get_ip():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def log(action, user_id=None, username=None, resource=None,
        resource_id=None, detail=None, success=True):
    if isinstance(detail, dict):
        detail = json.dumps(detail)
    get_db().execute(
        """INSERT INTO audit_log
               (user_id, username, ip_address, user_agent,
                action, resource, resource_id, detail, success)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            username,
            _get_ip(),
            (request.headers.get("User-Agent") or "")[:512],
            action,
            resource,
            str(resource_id) if resource_id is not None else None,
            detail,
            1 if success else 0,
        ),
    )
    get_db().commit()
