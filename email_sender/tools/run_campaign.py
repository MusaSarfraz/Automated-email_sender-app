"""CLI: create and run a campaign from an .xlsx, with an optional dry run.

Dry run logs what *would* be sent without contacting any mailbox — useful for
checking templates and the loop end-to-end::

    python -m email_sender.tools.run_campaign --excel leads.xlsx --dry-run

Without --dry-run it uses the configured mailbox (settings.json + Credential
Manager) and sends for real, honouring the fixed 80-second gap.
"""

from __future__ import annotations

import argparse
import sys

from ..core import campaigns
from ..core.config import SEND_GAP_SECONDS


def _print_event(kind: str, data: dict) -> None:
    if kind == "row_sent":
        tag = "WOULD SEND" if data.get("dry_run") else "SENT"
        print(f"  [{tag}] {data.get('email')}  \"{data.get('subject','')}\"")
    elif kind == "row_failed":
        print(f"  [FAILED] {data.get('email')}  {data.get('error','')}")
    elif kind == "row_recovered":
        print(f"  [RECOVERED->{data.get('result')}] {data.get('email')}")
    elif kind == "network_error":
        print(f"  [NETWORK] {data.get('email','')}  {data.get('error','')} (will retry)")
    elif kind == "auth_error":
        print(f"  [AUTH] {data.get('email','')}  {data.get('error','')} ({data.get('count')}/3)")
    elif kind == "cap_reached":
        print(f"  [CAP] {data.get('count')}/{data.get('cap')} — waiting until {data.get('until')}")
    elif kind == "state":
        print(f"  [STATE] {data.get('state')}" + (f" ({data['reason']})" if data.get("reason") else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an EmailSender campaign from an .xlsx file.")
    parser.add_argument("--excel", required=True, help="Path to the .xlsx file")
    parser.add_argument("--name", help="Campaign display name")
    parser.add_argument("--dry-run", action="store_true", help="Log instead of sending")
    args = parser.parse_args(argv)

    try:
        prog = campaigns.create_excel_campaign(args.excel, name=args.name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Could not start campaign: {exc}")
        return 2

    rows = campaigns.read_campaign_rows(prog).rows
    print(f"Campaign '{prog.name}' created with {len(rows)} rows.")
    print(f"Gap between emails: {SEND_GAP_SECONDS}s. Dry run: {args.dry_run}.")
    if not args.dry_run:
        print("Sending for real via the configured mailbox...")

    campaign = campaigns.make_campaign(prog, dry_run=args.dry_run, on_event=_print_event)
    reason = campaign.run()
    counts = campaign.counts()
    print(f"\nFinished ({reason}). Counts: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
