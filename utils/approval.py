"""Single source of truth for deciding whether an account is an *approval* account.

A person (e.g. an SO / AD / DD / Director) can hold two accounts:
  * a personal account    -> ``is_approval_role`` is false
  * an approval account   -> ``is_approval_role`` is true

For the dual-account roles (so, ad, dd, director) the stored ``is_approval_role``
flag is authoritative: the role name and the ``is_*_approver`` flags must NOT
promote a personal account to an approval account.

All other roles keep the legacy behaviour (role / approver-flag inference), because
accounts such as registry or cdsa have always been created with the flag unset or
"false" and still rely on that inference.
"""

# Roles that can hold both a personal and an approval account.
DUAL_ACCOUNT_ROLES = ("so", "ad", "dd", "director")

# Roles that have historically been treated as approval roles.
LEGACY_APPROVAL_ROLES = (
    "director", "registry", "central_registry", "cdsa", "dcdsa", "so1_doa",
    "civilian_head_cao", "civilian_head", "deputy_civilian_head_cao",
    "so", "ad", "dd",
)

APPROVER_FLAGS = (
    "is_final_approver", "is_final_approval",
    "is_so_approver", "is_ad_approver", "is_dd_approver",
)


def is_truthy(value):
    """True only for a real boolean True (flags are stored as booleans in the database)."""
    return value is True


def resolve_is_approval_role(user):
    """Return True only if ``user`` should be treated as an approval account."""
    if not user:
        return False

    role = str(user.get("role") or "").lower()
    flag = user.get("is_approval_role")

    if flag is not None:
        if is_truthy(flag):
            return True
        if role in DUAL_ACCOUNT_ROLES:
            # Personal account of an SO/AD/DD/Director: explicit false wins.
            return False

    return (
        role in LEGACY_APPROVAL_ROLES
        or any(is_truthy(user.get(k)) for k in APPROVER_FLAGS)
    )


def is_personal_dual_account(user):
    """True for an so/ad/dd/director account explicitly flagged as personal."""
    if not user:
        return False
    role = str(user.get("role") or "").lower()
    return (
        role in DUAL_ACCOUNT_ROLES
        and user.get("is_approval_role") is not None
        and not is_truthy(user.get("is_approval_role"))
    )
