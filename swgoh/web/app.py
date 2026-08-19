"""FastAPI review UI.

A thin HTTP layer over :mod:`swgoh.web.service`. Pages are server-rendered with
Jinja2; mutations follow the Post/Redirect/Get pattern so a refresh never
re-submits a rule change.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from swgoh.make_strategy import MIN_CHARACTER_RELIC_LEVEL
from swgoh.plan_my_defense import OPTIONS_FILE, PLAN_FILE, load_csv, parse_unit_list
from swgoh.web import emailer, service

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.bootstrap()
    yield


app = FastAPI(title="SWGOH GAC Strategy Planner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

RULE_GROUPS = [
    ("strategy_rejections", "Rejected counters"),
    ("locked_matchups", "Locked matchups"),
    ("reserved_units", "Reserved units"),
    ("leader_exemptions", "Exempt leaders"),
    ("offense_team_locks", "Always-offense teams (5v5)"),
    ("offense_team_locks_3v3", "Always-offense teams (3v3)"),
]


def _redirect(path: str, message: str) -> RedirectResponse:
    from urllib.parse import urlencode

    query = urlencode({"msg": message}) if message else ""
    url = f"{path}?{query}" if query else path
    return RedirectResponse(url=url, status_code=303)


def _strategy_rows(strategy_df: pd.DataFrame) -> list[dict]:
    rows = []
    for record in strategy_df.to_dict("records"):
        counter_units = record.get("chosen_counter_units") or []
        defense_units = record.get("defense_units") or []
        record["chosen_counter_units"] = counter_units
        record["defense_units"] = defense_units
        record["counter_units_repr"] = repr(list(counter_units))
        rows.append(record)
    return rows


@app.get("/")
def attack_page(request: Request, msg: str = ""):
    strategy_df, warnings = service.rebuild_strategy()
    active_warnings = {key: values for key, values in warnings.items() if values}
    rule_tables = [
        (key, label, service.load_rule_rows(key))
        for key, label in RULE_GROUPS
    ]
    rule_tables = [(k, lbl, rows) for k, lbl, rows in rule_tables if rows]

    return templates.TemplateResponse(
        request,
        "attack.html",
        {
            "message": msg,
            "rows": _strategy_rows(strategy_df),
            "warnings": active_warnings,
            "rule_tables": rule_tables,
            "settings": service.load_pipeline_settings(),
            "email_configured": emailer.is_configured(),
            "min_relic": MIN_CHARACTER_RELIC_LEVEL,
            "active": "attack",
        },
    )


@app.get("/defense")
def defense_page(request: Request, combat_type: str = "characters", msg: str = ""):
    service.rebuild_defense_plan()
    options_df = load_csv(OPTIONS_FILE)
    plan_df = load_csv(PLAN_FILE)

    planned_keys = set()
    if not plan_df.empty and "team_units" in plan_df.columns:
        for _, row in plan_df.iterrows():
            planned_keys.add(
                (str(row.get("leader", "")), tuple(parse_unit_list(row.get("team_units", []))))
            )

    rows = []
    if not options_df.empty:
        for _, row in options_df.iterrows():
            if str(row.get("combat_type", "")).strip() != combat_type:
                continue
            units = parse_unit_list(row.get("team_units", []))
            rows.append({
                "leader": row.get("leader", ""),
                "team_units": units,
                "perceived_strength": row.get("perceived_strength", ""),
                "times_seen": row.get("times_seen", ""),
                "avg_counter_win_percent": row.get("avg_counter_win_percent", ""),
                "in_plan": (str(row.get("leader", "")), tuple(units)) in planned_keys,
            })

    return templates.TemplateResponse(
        request,
        "defense.html",
        {"message": msg, "rows": rows, "combat_type": combat_type, "active": "defense"},
    )


@app.get("/setup")
def setup_page(request: Request, msg: str = ""):
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "message": msg,
            "settings": service.load_pipeline_settings(),
            "status": service.pipeline_status(),
            "log_tail": service.read_log_tail(),
            "email_settings": emailer.load_email_settings(),
            "active": "setup",
        },
    )


# --- mutations (Post/Redirect/Get) -----------------------------------------


@app.post("/reject")
def post_reject(
    combat_type: str = Form(""),
    defense_leader: str = Form(""),
    defense_name: str = Form(""),
    counter_leader: str = Form(""),
    counter_units: str = Form(""),
    reason: str = Form(""),
):
    msg = service.reject_counter(
        combat_type, defense_leader, defense_name, counter_leader, counter_units, reason
    )
    return _redirect("/", msg)


@app.post("/lock-matchup")
def post_lock_matchup(
    combat_type: str = Form(""),
    defense_leader: str = Form(""),
    defense_name: str = Form(""),
    counter_leader: str = Form(""),
    counter_units: str = Form(""),
    reason: str = Form(""),
):
    msg = service.lock_matchup(
        combat_type, defense_leader, defense_name, counter_leader, counter_units, reason
    )
    return _redirect("/", msg)


@app.post("/reserve")
def post_reserve(unit: str = Form(""), reason: str = Form("")):
    return _redirect("/", service.reserve_unit(unit, reason))


@app.post("/exempt-leader")
def post_exempt_leader(leader: str = Form(""), reason: str = Form("")):
    return _redirect("/", service.exempt_leader(leader, reason))


@app.post("/lock-offense-team")
def post_lock_offense_team(
    counter_leader: str = Form(""),
    counter_units: str = Form(""),
    defense_leader: str = Form(""),
    defense_name: str = Form(""),
    reason: str = Form(""),
):
    gac_format = service.load_pipeline_settings()["gac_format"]
    msg = service.lock_offense_team(
        counter_leader, counter_units, defense_leader, defense_name, reason, gac_format
    )
    return _redirect("/", msg)


@app.post("/manual-lock-offense-team")
def post_manual_lock_offense_team(
    leader: str = Form(""),
    team_units: str = Form(""),
    reason: str = Form(""),
):
    gac_format = service.load_pipeline_settings()["gac_format"]
    msg = service.lock_offense_team(leader, team_units, "manual", "Manual entry", reason, gac_format)
    return _redirect("/", msg)


@app.post("/remove-rule")
def post_remove_rule(rule_key: str = Form(""), row_index: int = Form(...)):
    return _redirect("/", service.remove_rule(rule_key, row_index))


@app.post("/save-setup")
def post_save_setup(
    my_player_id: str = Form(""),
    enemy_player_id: str = Form(""),
    history_limit: str = Form(""),
    gac_format: str = Form(""),
    counter_season_id: str = Form(""),
):
    service.save_pipeline_settings(
        my_player_id,
        enemy_player_id,
        history_limit,
        gac_format,
        counter_season_id,
    )
    return _redirect("/setup", "Saved pipeline settings.")


@app.post("/start-pipeline")
def post_start_pipeline():
    settings = service.load_pipeline_settings()
    return _redirect("/setup", service.start_pipeline(settings))


@app.post("/stop-pipeline")
def post_stop_pipeline():
    return _redirect("/setup", service.stop_pipeline())


@app.get("/counter-options")
def get_counter_options(combat_type: str = "", defense_leader: str = ""):
    """Feeds the manual-assignment picker on the attack page."""
    return JSONResponse(
        {"options": service.counter_options_for_defense(combat_type, defense_leader)}
    )


@app.post("/assign-counter")
def post_assign_counter(
    combat_type: str = Form(""),
    defense_leader: str = Form(""),
    defense_name: str = Form(""),
    counter_leader: str = Form(""),
    counter_units: str = Form(""),
    reason: str = Form(""),
):
    msg = service.assign_counter(
        combat_type, defense_leader, defense_name, counter_leader, counter_units, reason
    )
    return _redirect("/", msg)


@app.post("/email-plan")
def post_email_plan():
    strategy_df, _ = service.rebuild_strategy()
    gac_format = service.load_pipeline_settings()["gac_format"]
    return _redirect("/", emailer.send_plan_email(strategy_df, gac_format))


@app.post("/save-email")
def post_save_email(
    gmail_address: str = Form(""),
    app_password: str = Form(""),
    recipient: str = Form(""),
):
    emailer.save_email_settings(gmail_address, app_password, recipient)
    return _redirect("/setup", "Saved email settings.")


@app.get("/pipeline-status")
def get_pipeline_status():
    """Polled by the setup page so it can live-update without a full reload."""
    status = service.pipeline_status()
    return JSONResponse(
        {
            "state": status["state"],
            "detail": status["detail"],
            "returncode": status["returncode"],
            "log_tail": service.read_log_tail(),
        }
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)


if __name__ == "__main__":
    main()
