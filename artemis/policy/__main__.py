# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Approver CLI (the out-of-band channel): ``python -m artemis.policy list|approve|deny|audit``.

Run on the host, by the person who owns the phone. The agent process can only *request*.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time

from artemis.policy.approvals import ApprovalStore
from artemis.policy.audit import AuditLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m artemis.policy", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="pending approval requests")
    for name in ("approve", "deny"):
        p = sub.add_parser(name)
        p.add_argument("id")
    audit = sub.add_parser("audit", help="show the last decisions")
    audit.add_argument("-n", type=int, default=20)
    args = parser.parse_args(argv)

    store = ApprovalStore()
    if args.cmd == "list":
        pending = store.list_pending()
        for row in pending:
            left = int(row["expires_at"] - time.time())
            print(f"{row['id']}  {left:>4}s  [{row['origin']}] {row['summary']}")
        if not pending:
            print("No pending request.")
        return 0
    if args.cmd in {"approve", "deny"}:
        ok = store.decide(args.id, args.cmd == "approve", f"local:{getpass.getuser()}")
        print("done" if ok else "not pending (unknown, expired or already decided)")
        return 0 if ok else 1
    for entry in AuditLog().read()[-args.n :]:
        print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
