"""Replay saved observations, checking archived hashes, cash state and event identities.

Run from the repository root: venv/bin/python -m scripts.replay_intraday_paper
This reads the database and local archives only; it never fetches prices or writes trades.
"""
import argparse
import json
from core.database import SessionLocal, PaperExperimentAccount, PaperExperimentEvent
from services.intraday_market import UTC
from services.intraday_paper import ACCOUNT_ID, PaperConfig, replay_observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=ACCOUNT_ID)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    with SessionLocal() as db:
        account = db.get(PaperExperimentAccount, args.account)
        if account is None:
            raise SystemExit("Paper account not initialized")
        rows = db.query(PaperExperimentEvent).filter_by(account_id=args.account, kind="OBSERVATION").order_by(
            PaperExperimentEvent.id.desc()).limit(args.limit).all()
        failures = []
        for row in reversed(rows):
            try:
                mismatches = replay_observation(row.payload, row.occurred_at.replace(tzinfo=UTC), PaperConfig(**account.config))
                if mismatches:
                    failures.append({"event_id": row.id, "mismatches": mismatches})
            except Exception as exc:
                failures.append({"event_id": row.id, "error": str(exc)})
    print(json.dumps({"checked": len(rows), "failures": failures}, indent=2))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
