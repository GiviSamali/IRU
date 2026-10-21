"""Export measurements from a copied SQLite database, without running IRU/tasks.

Pass every Orchestrator and Worker task ID for one scenario. Run separately on
baseline and candidate copies. This tool measures recorded events, not correctness
of real artifacts; retain independent scenario verification alongside the JSON.
"""
import argparse
import collections
import json
from pathlib import Path
import sqlite3


def measurements(path, owner, task_ids, label):
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        raise ValueError("At least one task ID is required")
    placeholders = ",".join("?" for _ in ids)
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        events = [dict(row) for row in connection.execute(
            f"SELECT poll_task_id,task_id,route,phase,total_tokens,prompt_tokens,completion_tokens,request_ok "
            f"FROM llm_usage_events WHERE user_id=? AND (poll_task_id IN ({placeholders}) OR task_id IN ({placeholders}))",
            (owner, *ids, *ids))]
        jobs = [dict(row) for row in connection.execute(
            f"SELECT task_id,state,payload,report,created_at,updated_at FROM worker_jobs WHERE owner_user_id=? AND task_id IN ({placeholders})",
            (owner, *ids))]
        known = {row["task_id"] for row in jobs}
        turns = [dict(row) for row in connection.execute(
            f"SELECT task_id,response,created_at FROM orchestrator_turns WHERE owner_user_id=? AND task_id IN ({placeholders})",
            (owner, *ids))]
        known.update(row["task_id"] for row in turns)
        linked_ids = set()
        for turn in turns:
            response = json.loads(turn["response"] or "{}")
            linked_ids.update(response.get("worker_task_ids") or [])
            if response.get("worker_task_id"):
                linked_ids.add(response["worker_task_id"])
        unselected = sorted(linked_ids - set(ids))
        outcomes = []
        for job in jobs:
            payload = json.loads(job["payload"])
            report = payload.get("worker_report") or json.loads(job["report"] or "{}")
            valid = report.get("task_id") == job["task_id"] and report.get("status") == job["state"]
            outcomes.append({"task_id":job["task_id"], "state":job["state"],
                "report_consistent":valid,
                "goal_completed":report.get("goal_completed") if valid else None,
                "artifact_count":len(report.get("artifacts") or []) if valid else None,
                "started_at":payload.get("worker_started_at"), "admitted_at":job["created_at"],
                "updated_at":job["updated_at"]})
        phases = collections.Counter((row["route"], row["phase"]) for row in events)
        terminal = {"success","partial","failed","cancelled","unknown","blocked"}
        ended = bool(jobs) and all(row["state"] in terminal for row in jobs)
        token_totals = {name:sum(row[name] for row in events)
            if events and all(type(row[name]) is int and row[name] >= 0 for row in events) else None
            for name in ("total_tokens", "prompt_tokens", "completion_tokens")}
        return {"label":label, "owner_user_id":owner, "task_ids":ids,
            "missing_task_ids":sorted(set(ids)-known),
            "unselected_worker_task_ids":unselected,
            "recorded_llm_calls":len(events), "llm_events_available":bool(events),
            **token_totals,
            "token_counts_complete":bool(events) and all(value is not None for value in token_totals.values()),
            "failed_llm_requests":sum(row["request_ok"] == 0 for row in events),
            "phases":[{"route":route,"phase":phase,"calls":count} for (route,phase),count in phases.items()],
            "all_workers_terminal":ended,
            "queue_span_seconds":max(row["updated_at"] for row in jobs)-min(row["created_at"] for row in jobs) if ended else None,
            "scenario_span_seconds":max(row["updated_at"] for row in jobs)-min([row["created_at"] for row in jobs]+[row["created_at"] for row in turns]) if ended else None,
            "worker_outcomes":outcomes, "artifact_correctness":"requires_user_verification",
            "all_requested_ids_present":set(ids).issubset(known),
            "linked_workers_included":not unselected}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Copied baseline/candidate SQLite database")
    parser.add_argument("--owner", required=True, type=int)
    parser.add_argument("--task-id", required=True, action="append")
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = measurements(args.db, args.owner, args.task_id, args.label)
    # Avoid silently overwriting a prior measurement.
    with Path(args.output).open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    if result["missing_task_ids"] or result["unselected_worker_task_ids"]:
        raise SystemExit("Export incomplete: requested/linked task IDs missing; see output")


if __name__ == "__main__":
    main()
