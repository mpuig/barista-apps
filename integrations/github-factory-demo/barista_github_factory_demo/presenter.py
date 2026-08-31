"""Apps-owned presenter state, deterministic scenario controls, and cockpit UI."""

from __future__ import annotations

import hmac
import re
import threading
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from .app import DemoController

_DEMO_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
_TERMINAL_PROGRAMS = {"accepted", "failed", "refused"}
_TERMINAL_ATTEMPTS = {"succeeded", "failed", "refused"}
_STAGE_ORDER = ("brief", "approval", "plan", "build", "acceptance", "deploy")


def presenter_authorized(expected: str | None, supplied: str | None) -> bool:
    if expected is None or supplied is None:
        return False
    return hmac.compare_digest(expected.encode(), supplied.encode())


class PresenterService:
    """Serialize presenter mutations and derive a bounded public read model."""

    def __init__(self, controller: DemoController):
        self.controller = controller
        self._lock = threading.Lock()

    def launch(self, idempotency_key: str) -> dict:
        if _DEMO_KEY.fullmatch(idempotency_key) is None:
            raise HTTPException(status_code=422, detail="launch identity is invalid")
        with self._lock:
            replay = self.controller.store.get_demo_scenario(idempotency_key)
            if replay is not None:
                return {"scenario": replay, "created": False, "reason": "replayed"}
            current = self.controller.store.current_demo_scenario()
            if current is not None:
                return {
                    "scenario": current,
                    "created": False,
                    "reason": "current_scenario",
                }

            # Repair the narrow crash window between GitHub creation and the
            # local durable row before intending another root issue.
            latest = self.controller.program_forge.latest_demo_issue()
            if latest is not None:
                latest_key = latest["demo_idempotency_key"]
                known = self.controller.store.get_demo_scenario(latest_key)
                if known is None:
                    known = self._record(latest_key, latest)
                if known["reset_at"] is None:
                    return {
                        "scenario": known,
                        "created": False,
                        "reason": "recovered_current_scenario",
                    }

            issue = self.controller.program_forge.ensure_demo_issue(idempotency_key)
            scenario = self._record(idempotency_key, issue)
            return {"scenario": scenario, "created": True, "reason": "created"}

    def reset(self, idempotency_key: str) -> dict:
        if _DEMO_KEY.fullmatch(idempotency_key) is None:
            raise HTTPException(status_code=422, detail="scenario identity is invalid")
        with self._lock:
            scenario = self.controller.store.get_demo_scenario(idempotency_key)
            if scenario is None:
                raise HTTPException(status_code=404, detail="scenario not found")
            if scenario["reset_at"] is not None:
                return {"scenario": scenario, "reset": True, "reason": "replayed"}
            program = self.controller.store.get_program(scenario["program_id"])
            if program is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "scenario.starting",
                        "message": "The root issue is still being accepted. Wait for the Brief stage.",
                    },
                )
            attempts = self.controller.store.program_attempts(scenario["program_id"])
            nonterminal = [
                attempt
                for attempt in attempts
                if attempt["status"] not in _TERMINAL_ATTEMPTS
            ]
            if program["status"] not in _TERMINAL_PROGRAMS or nonterminal:
                view = _program_view(self.controller, program)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "scenario.active",
                        "message": (
                            "Reset cannot cancel active work. "
                            + view["next_action"]["instruction"]
                        ),
                        "program_id": program["program_id"],
                    },
                )
            self.controller.program_forge.close_demo_issue(scenario["issue_number"])
            reset = self.controller.store.reset_demo_scenario(idempotency_key)
            return {"scenario": reset, "reset": True, "reason": "reset"}

    def state(self) -> dict:
        scenarios = self.controller.store.list_demo_scenarios()
        programs = self.controller.store.list_programs()[:20]
        program_views = [
            _program_view(self.controller, program) for program in programs
        ]
        scenarios_by_program = {item["program_id"]: item for item in scenarios}
        for program in program_views:
            program["scenario"] = scenarios_by_program.get(program["program_id"])
        current = next((item for item in scenarios if item["reset_at"] is None), None)
        current_program = (
            next(
                (
                    item
                    for item in program_views
                    if item["program_id"] == current["program_id"]
                ),
                None,
            )
            if current
            else None
        )
        return {
            "schema_version": "v1alpha1",
            "repository": self.controller.config.repository,
            "presenter_controls": self.controller.config.presenter_token is not None,
            "current_scenario": current,
            "current_program": current_program,
            "scenarios": scenarios,
            "programs": program_views,
            "stages": list(_STAGE_ORDER),
            "refresh_after_seconds": 2,
        }

    def _record(self, idempotency_key: str, issue: dict) -> dict:
        number = issue.get("number")
        uri = issue.get("html_url")
        expected = (
            f"{self.controller.config.repository}/issues/{number}"
            if isinstance(number, int)
            else ""
        )
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or uri != expected
        ):
            raise HTTPException(
                status_code=502, detail="GitHub returned an invalid issue identity"
            )
        return self.controller.store.record_demo_scenario(
            idempotency_key=idempotency_key,
            scenario_id="deployment-status",
            issue_number=number,
            issue_uri=uri,
        )


def _program_view(controller: DemoController, program: dict) -> dict:
    program_id = str(program["program_id"])
    attempts = controller.store.program_attempts(program_id)
    events = controller.store.program_events(program_id)[-100:]
    deployment = controller.store.latest_deployment(program_id)
    stage = _program_stage(program, deployment)
    next_action = _next_action(program, deployment, controller.config.activity_endpoint)
    return {
        **program,
        "stage": stage,
        "stage_index": _STAGE_ORDER.index(stage),
        "terminal": program["status"] in _TERMINAL_PROGRAMS,
        "attempts": attempts,
        "events": events,
        "deployment": deployment,
        "next_action": next_action,
        "activity_uri": (
            f"{controller.config.activity_endpoint}/app/activity/{program_id}"
            if controller.config.activity_enabled
            else None
        ),
        "project_uri": (
            f"https://github.com/{'users' if controller.config.github_project_owner_kind == 'user' else 'orgs'}/"
            f"{controller.config.project_owner}/projects/{controller.config.github_project_number}"
            if controller.config.project_enabled
            else None
        ),
    }


def _program_stage(program: dict, deployment: dict | None) -> str:
    if deployment is not None:
        return "deploy"
    status = program["status"]
    if status in {"brd_running", "brd_needs_input"}:
        return "brief"
    if status == "awaiting_brd_merge":
        return "approval"
    if status in {"planning", "publishing_features"}:
        return "plan"
    if status == "implementing":
        return "build"
    if status in {"accepting", "accepted", "failed", "refused"}:
        return "acceptance"
    return "brief"


def _next_action(
    program: dict, deployment: dict | None, activity_endpoint: str | None
) -> dict:
    status = program["status"]
    if status == "brd_needs_input":
        return {
            "owner": "Presenter",
            "label": "Answer the clarification",
            "instruction": "Open the root issue and post the reviewed clarification response.",
            "url": program["issue_uri"],
        }
    if status == "awaiting_brd_merge":
        return {
            "owner": "Presenter",
            "label": "Approve the BRD",
            "instruction": "Review the exact draft, mark it ready if needed, then merge it in GitHub.",
            "url": program["brd"]["pr_uri"],
        }
    if status == "implementing":
        ready = next(
            (
                feature
                for feature in program["features"]
                if feature["status"] == "awaiting_merge"
            ),
            None,
        )
        if ready:
            return {
                "owner": "Presenter",
                "label": f"Merge {ready['title']}",
                "instruction": "Review this verified feature PR and merge it to release the next dependency.",
                "url": ready["pr_uri"],
            }
        return {
            "owner": "Factory",
            "label": "Building the next feature",
            "instruction": "Wait for the active isolated attempt to publish independently verified work.",
            "url": None,
        }
    if status == "accepted" and deployment is None:
        return {
            "owner": "Presenter",
            "label": "Request deployment",
            "instruction": "Open Activity, inspect exact acceptance evidence, and request Deploy.",
            "url": (
                f"{activity_endpoint}/app/activity/{program['program_id']}"
                if activity_endpoint
                else None
            ),
        }
    if deployment is not None and deployment["state"] in {"requested", "running"}:
        return {
            "owner": "Factory",
            "label": "Publishing the accepted commit",
            "instruction": "Wait while source-owned deployment publishes, verifies health, and settles intent.",
            "url": None,
        }
    if deployment is not None and deployment["state"] == "succeeded":
        result = deployment.get("result") or {}
        return {
            "owner": "Presenter",
            "label": "Open the deployed product",
            "instruction": "Show the exact accepted product and its verified public endpoint.",
            "url": result.get("endpoint"),
        }
    if status in {"failed", "refused"} or (
        deployment is not None and deployment["state"] == "failed"
    ):
        return {
            "owner": "Presenter",
            "label": "Review retained evidence",
            "instruction": "Explain the bounded failure before choosing a new reviewed attempt.",
            "url": program["issue_uri"],
        }
    return {
        "owner": "Factory",
        "label": "Advancing automatically",
        "instruction": "Keep this cockpit open; the next authority boundary will appear here.",
        "url": None,
    }


PRESENTER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'self'; style-src 'nonce-__NONCE__'; script-src 'nonce-__NONCE__'; connect-src 'self'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Factory presenter · Barista</title>
<style nonce="__NONCE__">
:root{color-scheme:light;--bg:oklch(97% .006 80);--paper:oklch(99.4% .003 80);--sink:oklch(94.5% .009 76);--ink:oklch(24% .018 55);--ink2:oklch(39% .017 55);--muted:oklch(52% .014 58);--line:oklch(86% .01 72);--crema:oklch(68% .13 70);--crema2:oklch(53% .11 58);--ok:oklch(57% .13 153);--wait:oklch(61% .09 246);--bad:oklch(56% .17 25);--r:12px;--ease:cubic-bezier(.16,1,.3,1);font-family:"Aptos","Segoe UI",sans-serif}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:oklch(20% .012 62);--paper:oklch(24% .013 62);--sink:oklch(18% .011 62);--ink:oklch(93% .01 80);--ink2:oklch(82% .012 78);--muted:oklch(68% .012 70);--line:oklch(37% .013 62);--crema:oklch(77% .13 76);--crema2:oklch(82% .1 76);--ok:oklch(72% .14 158);--wait:oklch(74% .09 248);--bad:oklch(70% .16 25)}}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;color:var(--ink);background:var(--bg);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}button,input{font:inherit}button,a{touch-action:manipulation}a{color:inherit;text-underline-offset:.2em}::selection{background:color-mix(in oklch,var(--crema) 25%,transparent)}:focus-visible{outline:3px solid color-mix(in oklch,var(--crema) 65%,transparent);outline-offset:3px}*{scrollbar-color:var(--line) var(--bg)}
.shell{width:min(1540px,100%);margin:auto;padding:clamp(18px,3vw,44px)}.top{display:flex;align-items:center;justify-content:space-between;gap:24px;padding-bottom:20px;border-bottom:1px solid var(--line)}.brand{display:flex;align-items:center;gap:11px;text-decoration:none}.mark{display:grid;width:34px;height:34px;place-items:center;border-radius:9px;background:var(--ink);color:var(--paper);font-weight:800}.brand strong{display:block;letter-spacing:-.02em}.brand small{display:block;color:var(--muted);font-size:11px;letter-spacing:.12em;text-transform:uppercase}.controls{display:flex;align-items:center;gap:8px}.btn{min-height:42px;padding:0 15px;border:1px solid var(--line);border-radius:9px;color:var(--ink);background:var(--paper);font-weight:700;cursor:pointer;transition:transform .18s var(--ease),background .18s var(--ease)}.btn:hover{background:var(--sink)}.btn:active{transform:translateY(1px)}.btn.primary{border-color:var(--ink);color:var(--paper);background:var(--ink)}.btn[disabled]{cursor:not-allowed;opacity:.48}.live{display:flex;align-items:center;gap:7px;margin-right:8px;color:var(--muted);font-size:12px}.live i{width:8px;height:8px;border-radius:50%;background:var(--ok)}
.hero{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,.45fr);gap:clamp(36px,7vw,110px);padding:clamp(52px,7vw,96px) 0 42px}.hero h1{max-width:16ch;margin:0;font-size:clamp(40px,6vw,82px);font-weight:760;line-height:.96;letter-spacing:-.035em;text-wrap:balance}.hero h1 span{color:var(--crema2)}.hero .summary{align-self:end;max-width:44ch}.summary p{margin:0;color:var(--ink2);font-size:clamp(16px,1.5vw,20px)}.exact{margin-top:18px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}
.notice{display:none;margin:0 0 24px;padding:13px 15px;border:1px solid var(--line);border-radius:9px;background:var(--paper);color:var(--ink2)}.notice[data-show=true]{display:block}.notice[data-kind=error]{color:var(--bad)}
.stage-rail{display:grid;grid-template-columns:repeat(6,1fr);margin:0 0 clamp(38px,6vw,76px);padding:0;list-style:none;border-top:1px solid var(--line)}.stage-rail li{position:relative;padding:18px 8px 0 0;color:var(--muted);font-size:12px;font-weight:700}.stage-rail li::before{position:absolute;top:-5px;left:0;width:9px;height:9px;border:2px solid var(--bg);border-radius:50%;background:var(--line);box-shadow:0 0 0 1px var(--line);content:""}.stage-rail li.done{color:var(--ink2)}.stage-rail li.done::before{background:var(--ok)}.stage-rail li.now{color:var(--ink)}.stage-rail li.now::before{background:var(--crema);box-shadow:0 0 0 1px var(--crema),0 0 0 6px color-mix(in oklch,var(--crema) 18%,transparent)}
.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(300px,.65fr);gap:clamp(36px,6vw,84px)}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:18px;padding-bottom:12px;border-bottom:1px solid var(--line)}h2{margin:0;font-size:13px;letter-spacing:.1em;text-transform:uppercase}.section-head span{color:var(--muted);font-size:12px}.next{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:28px;margin-bottom:46px;padding:clamp(24px,4vw,42px);border-radius:var(--r);background:var(--ink);color:var(--paper)}.next small{display:block;margin-bottom:10px;color:color-mix(in oklch,var(--paper) 68%,var(--ink));font-weight:700}.next h3{margin:0;font-size:clamp(24px,3vw,38px);line-height:1.05;letter-spacing:-.025em}.next p{max-width:60ch;margin:12px 0 0;color:color-mix(in oklch,var(--paper) 78%,var(--ink))}.next a{color:var(--paper);font-weight:800;white-space:nowrap}
.features{margin:0;padding:0;list-style:none}.feature{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:14px;align-items:start;padding:18px 0;border-bottom:1px solid var(--line)}.feature .ordinal{display:grid;width:28px;height:28px;place-items:center;border-radius:50%;background:var(--sink);font-size:12px;font-weight:800}.feature h3{margin:1px 0 3px;font-size:16px}.feature p{margin:0;color:var(--muted);font-size:13px}.deps{margin-top:7px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--muted)}.state{padding:4px 8px;border-radius:99px;background:var(--sink);font-size:11px;font-weight:800;text-transform:capitalize}.state[data-state=merged],.state[data-state=accepted],.state[data-state=succeeded]{color:var(--ok)}.state[data-state=running],.state[data-state=awaiting_merge]{color:var(--crema2)}.state[data-state=blocked],.state[data-state=awaiting_input]{color:var(--wait)}.state[data-state=failed],.state[data-state=refused]{color:var(--bad)}
.ledger section{padding:0 0 28px}.ledger section+section{padding-top:28px;border-top:1px solid var(--line)}dl{margin:12px 0 0}dl div{display:grid;grid-template-columns:86px minmax(0,1fr);gap:14px;padding:7px 0}dt{color:var(--muted);font-size:12px}dd{min-width:0;margin:0;font-size:13px;overflow-wrap:anywhere}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}.links,.attempts,.recent{margin:12px 0 0;padding:0;list-style:none}.links li,.attempts li,.recent li{padding:10px 0;border-bottom:1px solid var(--line)}.links a{display:flex;justify-content:space-between;gap:15px;font-weight:700;text-decoration:none}.links a:hover{text-decoration:underline}.attempts strong{display:flex;justify-content:space-between;gap:10px;font-size:12px}.attempts small{display:block;margin-top:2px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;overflow-wrap:anywhere}.recent button{width:100%;padding:0;border:0;color:inherit;background:none;text-align:left;cursor:pointer}.recent strong{display:block;font-size:13px}.recent small{color:var(--muted);font-size:11px}.empty{padding:44px 0;color:var(--muted)}
dialog{width:min(430px,calc(100% - 32px));padding:0;border:0;border-radius:14px;color:var(--ink);background:var(--paper);box-shadow:0 22px 70px oklch(15% .02 55/.28)}dialog::backdrop{background:oklch(15% .02 55/.48);backdrop-filter:blur(3px)}.dialog-body{padding:28px}.dialog-body h2{font-size:20px;letter-spacing:-.015em;text-transform:none}.dialog-body p{color:var(--muted)}.dialog-body label{display:block;margin:20px 0 7px;font-size:12px;font-weight:800}.dialog-body input{width:100%;min-height:44px;padding:0 12px;border:1px solid var(--line);border-radius:8px;color:var(--ink);background:var(--bg)}.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:22px}
@media(max-width:820px){.shell{padding:16px}.top{align-items:flex-start}.controls{flex-wrap:wrap;justify-content:flex-end}.live{width:100%;justify-content:flex-end}.hero,.grid{grid-template-columns:1fr}.hero{gap:24px;padding:48px 0 32px}.summary{align-self:auto}.stage-rail{grid-template-columns:repeat(3,minmax(0,1fr));gap:26px 0;border-top:0}.stage-rail li{border-top:1px solid var(--line)}.next{grid-template-columns:1fr}.feature{grid-template-columns:32px minmax(0,1fr)}.feature .state{grid-column:2}.ledger{padding-top:28px;border-top:1px solid var(--line)}}
@media(max-width:560px){.top{align-items:stretch;flex-direction:column}.controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));width:100%}.controls .btn{padding:0 8px}.live{grid-column:1/-1;justify-content:flex-start}.hero{padding-top:40px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}.stage-rail li.now::before{box-shadow:0 0 0 1px var(--crema)}}
</style>
</head>
<body>
<!-- THESIS: one live authority boundary dominates; refuses a tile dashboard. OWN-WORLD: porcelain and espresso, crema state, receipt identities, semantic rails. STORY: see what Factory is doing, perform the one human action, verify settlement. FIRST VIEWPORT: stage statement left, next action right, six-stage rail above dependency workbench. FORM: presenter rundown, Apps-owned extension of the Barista operator world, seed apps-015-rundown. FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md. -->
<div class="shell">
<header class="top"><a class="brand" href="https://beta.barista.sh/app/activity"><span class="mark">B</span><span><strong>Barista Factory</strong><small>Presenter cockpit</small></span></a><div class="controls"><span class="live"><i></i><span id="sync">Connecting</span></span><button class="btn" id="unlock">Unlock controls</button><button class="btn primary" id="launch" disabled>Launch scenario</button><button class="btn" id="reset" disabled>Reset</button></div></header>
<div class="hero"><h1 id="headline">Ready for a <span>clean run.</span></h1><div class="summary"><p id="summary">Checking the controller’s authoritative program state.</p><div class="exact" id="identity">No active scenario</div></div></div>
<p class="notice" id="notice" role="status" aria-live="polite"></p>
<ol class="stage-rail" id="stages" aria-label="Delivery stages"></ol>
<div class="grid"><main><section class="next" id="next"><div><small id="next-owner">Factory</small><h3 id="next-label">Waiting for state</h3><p id="next-copy">The next authority boundary will appear here.</p></div><a id="next-link" hidden target="_blank" rel="noopener noreferrer">Open action</a></section><section><div class="section-head"><h2>Dependency workbench</h2><span id="feature-count">0 features</span></div><ol class="features" id="features"><li class="empty">No approved plan yet.</li></ol></section></main><aside class="ledger"><section><div class="section-head"><h2>Exact evidence</h2><span id="status">—</span></div><dl id="evidence"></dl></section><section><div class="section-head"><h2>Attempts</h2><span id="attempt-count">0</span></div><ul class="attempts" id="attempts"><li class="empty">No attempts yet.</li></ul></section><section><div class="section-head"><h2>Open alongside</h2></div><ul class="links" id="links"></ul></section><section><div class="section-head"><h2>Recent programs</h2></div><ul class="recent" id="recent"></ul></section></aside></div>
</div>
<dialog id="token-dialog"><form method="dialog" class="dialog-body" id="token-form"><h2>Unlock presenter controls</h2><p>Enter the separate presenter token. It stays in this browser tab and is never included in cockpit state.</p><label for="token">Presenter token</label><input id="token" type="password" autocomplete="off" required minlength="32"><div class="dialog-actions"><button class="btn" value="cancel">Cancel</button><button class="btn primary" value="default">Unlock</button></div></form></dialog>
<script nonce="__NONCE__">
const $=id=>document.getElementById(id);const terminal=new Set(['accepted','failed','refused']);let state=null,selected=null,busy=false;const tokenKey='barista.presenter.token';
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}function safeUrl(value){if(typeof value!=='string'||!value)return null;try{const u=new URL(value,location.origin);return u.protocol==='https:'?u.href:null}catch{return null}}function clear(n){n.replaceChildren()}function fmt(ts){return ts?new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(ts*1000)):'—'}
function notice(message,kind='info'){const n=$('notice');n.textContent=message;n.dataset.show=Boolean(message);n.dataset.kind=kind}function unlocked(){return Boolean(sessionStorage.getItem(tokenKey))}function syncControls(){const has=state?.presenter_controls;$('unlock').hidden=!has;$('unlock').textContent=unlocked()?'Controls unlocked':'Unlock controls';$('launch').disabled=busy||!has||!unlocked()||Boolean(state?.current_scenario);const scenario=state?.current_scenario;const program=scenario&&state?.programs.find(p=>p.program_id===scenario.program_id);$('reset').disabled=busy||!has||!unlocked()||!scenario||!program||!program.terminal}
function pickProgram(){if(selected){const found=state.programs.find(p=>p.program_id===selected);if(found)return found}return state.current_program||null}
function render(){const p=pickProgram();selected=p?.program_id||null;const active=state.current_scenario&&p?.program_id===state.current_scenario.program_id;$('sync').textContent=`Live · ${new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;if(!p){$('headline').replaceChildren(document.createTextNode('Ready for a '),el('span','','clean run.'));$('summary').textContent='Preflight is separate. Unlock controls, launch the reviewed scenario, then follow each authority boundary here.';$('identity').textContent='No active scenario';}else{const label=p.next_action.label.toLowerCase();$('headline').replaceChildren(document.createTextNode(active?'Now: ':'Evidence: '),el('span','',label+'.'));$('summary').textContent=p.next_action.instruction;$('identity').textContent=`${p.program_id} · issue #${p.issue_number} · updated ${fmt(p.updated_at)}`}$('status').textContent=p?.status?.replaceAll('_',' ')||'ready';renderStages(p);renderNext(p);renderFeatures(p);renderEvidence(p);renderAttempts(p);renderLinks(p);renderRecent();syncControls()}
function renderStages(p){const names={brief:'Brief',approval:'BRD approval',plan:'Plan',build:'Features',acceptance:'Acceptance',deploy:'Deploy'};clear($('stages'));state.stages.forEach((s,i)=>{const li=el('li','',names[s]);if(p&&i<p.stage_index)li.classList.add('done');if(p&&i===p.stage_index)li.classList.add('now');li.setAttribute('aria-current',p&&i===p.stage_index?'step':'false');$('stages').append(li)})}
function renderNext(p){const a=p?.next_action||{owner:'Presenter',label:'Launch the reviewed scenario',instruction:'One launch creates one inert root issue; duplicate requests converge.',url:null};$('next-owner').textContent=`${a.owner} owns the next move`;$('next-label').textContent=a.label;$('next-copy').textContent=a.instruction;const u=safeUrl(a.url);$('next-link').hidden=!u;if(u)$('next-link').href=u}
function renderFeatures(p){const root=$('features');clear(root);const fs=p?.features||[];$('feature-count').textContent=`${fs.length} ${fs.length===1?'feature':'features'}`;if(!fs.length){root.append(el('li','empty','The verified plan will appear after BRD approval.'));return}fs.forEach((f,i)=>{const li=el('li','feature');li.append(el('span','ordinal',String(i+1)));const body=el('div');body.append(el('h3','',f.title));body.append(el('p','',f.summary));body.append(el('div','deps',f.dependencies.length?`Waits for: ${f.dependencies.join(', ')}`:'No dependencies'));li.append(body);li.append(el('span','state',f.status.replaceAll('_',' '))).dataset.state=f.status;root.append(li)})}
function pair(term,value,mono=false){const d=el('div');d.append(el('dt','',term));d.append(el('dd',mono?'mono':'',value||'—'));return d}function renderEvidence(p){const d=$('evidence');clear(d);if(!p){d.append(pair('Controller','Healthy'));d.append(pair('Cleanup','No active scenario'));return}d.append(pair('Program',p.program_id,true));d.append(pair('Root issue',`#${p.issue_number}`));d.append(pair('BRD digest',p.brd.digest,true));d.append(pair('Plan digest',p.plan_digest,true));d.append(pair('Accepted',p.acceptance?.assembled_commit,true));const dep=p.deployment;d.append(pair('Deployment',dep?.state));d.append(pair('Image',dep?.result?.image_digest,true));const cleanup=p.scenario?.reset_at?'Reset; evidence retained':(state.current_scenario?.program_id===p.program_id&&p.terminal?'Terminal; reset available':(p.terminal?'Terminal evidence retained':'Preserving active work'));d.append(pair('Cleanup',cleanup))}
function renderAttempts(p){const root=$('attempts');clear(root);const as=p?.attempts||[];$('attempt-count').textContent=String(as.length);if(!as.length){root.append(el('li','empty','No correlated attempts yet.'));return}as.slice().reverse().slice(0,8).forEach(a=>{const li=el('li');const row=el('strong');row.append(el('span','',a.feature_id||a.workflow_kind));const s=el('span','state',a.status.replaceAll('_',' '));s.dataset.state=a.status;row.append(s);li.append(row);li.append(el('small','',a.run_name));root.append(li)})}
function addLink(root,label,url){const u=safeUrl(url);if(!u)return;const li=el('li'),a=el('a');a.href=u;a.target='_blank';a.rel='noopener noreferrer';a.append(el('span','',label));a.append(el('span','','Open'));li.append(a);root.append(li)}function renderLinks(p){const root=$('links');clear(root);addLink(root,'Repository',state.repository);if(p){addLink(root,'Root issue',p.issue_uri);addLink(root,'BRD pull request',p.brd.pr_uri);addLink(root,'Cloud activity',p.activity_uri);addLink(root,'GitHub Project',p.project_uri);addLink(root,'Deployed product',p.deployment?.result?.endpoint)}}
function renderRecent(){const root=$('recent');clear(root);state.programs.slice(0,8).forEach(p=>{const li=el('li'),b=el('button');b.type='button';b.append(el('strong','',`${p.program_id} · ${p.status.replaceAll('_',' ')}`));b.append(el('small','',`Issue #${p.issue_number} · ${fmt(p.updated_at)}`));b.onclick=()=>{selected=p.program_id;render()};li.append(b);root.append(li)})}
async function refresh(){try{const r=await fetch('/presenter/api/state',{cache:'no-store'});if(!r.ok)throw new Error(`State returned ${r.status}`);state=await r.json();render()}catch(e){$('sync').textContent='Disconnected';notice(`${e.message}. The cockpit will retry automatically.`,'error')}}async function mutate(path,body){busy=true;syncControls();notice('Working…');try{const r=await fetch(path,{method:'POST',headers:{'content-type':'application/json','authorization':`Bearer ${sessionStorage.getItem(tokenKey)||''}`},body:JSON.stringify(body)});const data=await r.json().catch(()=>({}));if(r.status===401){sessionStorage.removeItem(tokenKey);throw new Error('Presenter token was refused. Unlock controls and try again.')}if(!r.ok){const detail=data.detail;throw new Error(typeof detail==='string'?detail:detail?.message||`Request returned ${r.status}`)}notice(path.endsWith('launch')?(data.created?'Scenario launched. GitHub delivery is starting.':'Using the current scenario; no duplicate was created.'):'Scenario reset. A fresh launch is available.');await refresh()}catch(e){notice(e.message,'error')}finally{busy=false;syncControls()}}
$('unlock').onclick=()=>{if(unlocked()){sessionStorage.removeItem(tokenKey);syncControls();notice('Presenter controls locked.');return}$('token-dialog').showModal();$('token').focus()};$('token-form').addEventListener('submit',e=>{if(e.submitter?.value==='cancel')return;const value=$('token').value.trim();if(value.length<32){e.preventDefault();notice('Presenter token must contain at least 32 characters.','error');return}sessionStorage.setItem(tokenKey,value);$('token').value='';syncControls();notice('Presenter controls unlocked for this tab.')});$('launch').onclick=()=>mutate('/presenter/api/scenarios/deployment-status/launch',{idempotency_key:`demo-${crypto.randomUUID()}`});$('reset').onclick=()=>{const s=state.current_scenario;if(s&&confirm('Reset this terminal scenario? The root issue will close; evidence remains.'))mutate('/presenter/api/scenarios/reset',{idempotency_key:s.idempotency_key})};
refresh();setInterval(refresh,2000);
</script>
</body></html>"""


def presenter_html(nonce: str) -> str:
    return PRESENTER_HTML.replace("__NONCE__", nonce)
