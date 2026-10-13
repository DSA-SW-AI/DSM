"""Normalise approval-related flags in db.users from "true"/"false" strings to real booleans.

Usage (from the project root):
    python normalize_approval_flags.py            # dry run: only reports what would change
    python normalize_approval_flags.py --apply    # write the changes

Only string values "true"/"false" (any case) are converted. Values that are already
booleans, or missing, are left untouched.
"""
import sys
from pymongo import MongoClient

FLAGS = (
    "is_approval_role",
    "is_so_approver",
    "is_ad_approver",
    "is_dd_approver",
    "is_final_approver",
    "is_final_approval",
    "is_cdsa_approver",
)


def main(apply_changes):
    users = MongoClient("mongodb://localhost:27017/")["DSM"]["users"]
    changed_docs = 0
    for user in users.find({"$or": [{f: {"$type": "string"}} for f in FLAGS]}):
        updates = {}
        for f in FLAGS:
            v = user.get(f)
            if isinstance(v, str):
                lv = v.strip().lower()
                if lv in ("true", "false"):
                    updates[f] = (lv == "true")
        if not updates:
            continue
        changed_docs += 1
        print(f"{user.get('email')}: {updates}")
        if apply_changes:
            users.update_one({"_id": user["_id"]}, {"$set": updates})
    mode = "updated" if apply_changes else "would update"
    print(f"\n{mode} {changed_docs} user document(s).")
    if not apply_changes:
        print("Dry run only. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
