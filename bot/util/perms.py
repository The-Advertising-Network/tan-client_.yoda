#######################################################
# Permission management functions
#######################################################
from __future__ import annotations

import json
import os
import logging
from pathlib import Path
from typing import Dict, List, Union, Optional

from discord.ext import commands

# Path to the role permissions file (relative to project root)
# perms.py is located at bot/util/perms.py, so go up two parents to project root
_ROLEPERMS_FILENAME = Path(__file__).resolve().parents[2] / "data" / "roleperms.json"

# Top-level key used in the JSON file
_TOP_KEY = "role_perms"


def _ensure_file_exists() -> None:
    """Ensure the roleperms.json file exists; if not, create a minimal structure.
    This does not overwrite an existing file.
    """
    if not _ROLEPERMS_FILENAME.exists():
        _ROLEPERMS_FILENAME.parent.mkdir(parents=True, exist_ok=True)
        default = { _TOP_KEY: {} }
        _ROLEPERMS_FILENAME.write_text(json.dumps(default, indent=2), encoding="utf-8")


def load_role_perms() -> Dict[str, List[str]]:
    """Load and return the permissions mapping.

    Returns a dict mapping permission name -> list of role ID strings.
    Nulls and missing values are normalized to empty lists.
    """
    _ensure_file_exists()
    try:
        with _ROLEPERMS_FILENAME.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        # If the JSON is invalid, return empty mapping (do not crash the bot)
        logging.exception("Invalid JSON in roleperms file %s", _ROLEPERMS_FILENAME)
        return {}

    raw = data.get(_TOP_KEY, {}) if isinstance(data, dict) else {}
    normalized: Dict[str, List[str]] = {}

    for perm, value in raw.items():
        if value is None:
            normalized[perm] = []
        elif isinstance(value, list):
            # store role IDs as strings
            normalized[perm] = [str(x) for x in value if x is not None]
        else:
            # If value is a single scalar (unexpected), coerce to single-item list
            normalized[perm] = [str(value)]

    return normalized


def save_role_perms(perms: Dict[str, List[Union[str, int]]]) -> None:
    """Persist the permissions mapping back to disk.

    The function writes under the top-level key defined in _TOP_KEY.
    Role IDs are stored as strings.
    """
    _ensure_file_exists()
    # Normalize data to lists of strings
    safe: Dict[str, List[str]] = {}
    for perm, roles in perms.items():
        if roles is None:
            safe[perm] = []
        else:
            safe[perm] = [str(r) for r in roles]

    out = { _TOP_KEY: safe }

    # Write atomically: write to temp file then replace
    tmp_path = _ROLEPERMS_FILENAME.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp_path), str(_ROLEPERMS_FILENAME))


def get_permissions() -> List[str]:
    """Return the list of known permission names."""
    perms = load_role_perms()
    return list(perms.keys())


def get_roles_for_permission(permission: str) -> List[str]:
    """Return the list of role IDs (strings) assigned to `permission`.

    If the permission does not exist, returns an empty list.
    """
    perms = load_role_perms()
    return list(perms.get(permission, []))


def role_has_permission(role_id: Union[str, int], permission: str) -> bool:
    """Check whether the given role (ID or str) has the specified permission."""
    role_s = str(role_id)
    roles = get_roles_for_permission(permission)
    return role_s in roles


def add_role_to_permission(role_id: Union[str, int], permission: str) -> bool:
    """Add a role ID to a permission.

    Returns True if the role was added, False if it was already present.
    """
    perms = load_role_perms()
    roles = perms.get(permission)
    if roles is None:
        roles = []
        perms[permission] = roles

    role_s = str(role_id)
    if role_s in roles:
        return False

    roles.append(role_s)
    # Ensure uniqueness
    perms[permission] = list(dict.fromkeys(roles))
    save_role_perms(perms)
    return True


def remove_role_from_permission(role_id: Union[str, int], permission: str) -> bool:
    """Remove a role ID from a permission.

    Returns True if the role was removed, False if it was not present.
    """
    perms = load_role_perms()
    roles = perms.get(permission, [])
    role_s = str(role_id)
    if role_s not in roles:
        return False
    roles = [r for r in roles if r != role_s]
    perms[permission] = roles
    save_role_perms(perms)
    return True


def set_roles_for_permission(permission: str, role_ids: Optional[List[Union[str, int]]]) -> None:
    """Replace the role list for `permission` with `role_ids`.

    If role_ids is None, the permission will be set to an empty list.
    """
    perms = load_role_perms()
    perms[permission] = [str(r) for r in (role_ids or [])]
    save_role_perms(perms)


def find_permissions_for_role(role_id: Union[str, int]) -> List[str]:
    """Return a list of permission names that the given role ID has."""
    role_s = str(role_id)
    perms = load_role_perms()
    return [perm for perm, roles in perms.items() if role_s in roles]


# New: check whether a member (discord.Member-like object) has a named permission
def member_has_permission(member, permission: str) -> bool:
    """Return True if the member has any role mapped to `permission`.

    This expects `member.roles` to be an iterable of role-like objects with an `id` attribute.
    """
    try:
        member_role_ids = {str(r.id) for r in getattr(member, "roles", [])}
        logging.debug("member_role_ids=%s", member_role_ids)
    except Exception:
        # If the member object is not as expected, deny permission safely
        return False
    role_ids = set(get_roles_for_permission(permission) or [])
    logging.debug("required_role_ids=%s", role_ids)
    return bool(member_role_ids.intersection(role_ids))


# New: a convenient check decorator for command functions
def has_permission(permission: str):
    """Return a decorator that checks the invoking user has `permission`.

    The check allows server administrators / members with manage_guild OR administrator to bypass the role mapping.
    Use as:
        @has_permission("manage_economy")
        async def some_command(ctx, ...):
            ...
    Works with both regular commands and application command contexts (ctx.author expected).
    """
    async def predicate(ctx):
        # explicitly disallow in DMs
        if getattr(ctx, "guild", None) is None:
            raise commands.CheckFailure("This command cannot be used in DMs.")
        author = getattr(ctx, "author", None)
        if author is None:
            raise commands.CheckFailure("Unable to determine invoking user.")
        # Allow the guild owner to bypass checks
        guild = getattr(ctx, "guild", None)
        try:
            if guild is not None and getattr(guild, "owner_id", None) == getattr(author, "id", None):
                return True
        except Exception:
            # ignore attribute access issues and continue
            pass

        # Allow members with Manage Guild or Administrator permissions to bypass checks
        gperms = getattr(author, "guild_permissions", None)
        if gperms and (gperms.manage_guild or gperms.administrator):
            return True
        if member_has_permission(author, permission):
            return True
        # Deny with a clear message
        raise commands.CheckFailure(f"You do not have the required permission: {permission}")

    return commands.check(predicate)


# Provide a small convenience alias names that match natural language
has_role_permission = role_has_permission
add_role = add_role_to_permission
remove_role = remove_role_from_permission


# If the module is run directly, print a short status (handy for manual checks)
if __name__ == "__main__":
    import pprint
    print(f"Using roleperms file: {_ROLEPERMS_FILENAME}")
    pprint.pprint(load_role_perms())
