"""Self-contained replay viewer with an explicit public/truth boundary."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .reporting import _command, _read_json, _read_ndjson, _result, _state, summarize_run


def _script_json(value: object) -> str:
    # JSON lives in a script element. Escaping '<' prevents a recorded claim
    # containing '</script>' from terminating that element and injecting HTML.
    return (
        json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _embedded_fonts() -> tuple[str, str]:
    directory = Path(__file__).parent / "assets" / "fonts"
    faces = []
    for name, family, weight in (
        ("IBMPlexSans-Regular.woff2", "IBM Plex Sans", 400),
        ("IBMPlexSans-Medium.woff2", "IBM Plex Sans", 500),
        ("IBMPlexSans-SemiBold.woff2", "IBM Plex Sans", 600),
        ("IBMPlexMono-Regular.woff2", "IBM Plex Mono", 400),
    ):
        path = directory / name
        if path.exists():
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            faces.append(
                f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
                f"font-display:swap;src:url(data:font/woff2;base64,{encoded}) format('woff2')}}"
            )
    license_path = directory / "LICENSE.txt"
    license_text = license_path.read_text() if faces and license_path.exists() else ""
    return "\n".join(faces), license_text.replace("--", "- -")


def _public_telemetry(state: dict, team: int) -> dict:
    """Project live own-team telemetry without any opposing positions."""
    return {
        "tick": state["tick"],
        "scores": state.get("scores", [0, 0]),
        "bots": [
            {key: bot[key] for key in ("id", "team", "x", "y", "objective_id") if key in bot}
            for bot in state.get("bots", [])
            if bot.get("team") == team
        ],
    }


def generate_viewer(run_dir: Path, output_path: Path | None = None) -> Path:
    """Create one portable HTML file. No server, package, or network is needed."""
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / "manifest.json")
    if not manifest:
        raise ValueError(f"Missing manifest.json in {run_dir}")
    records = _read_ndjson(run_dir / "states.ndjson")
    if not records:
        raise ValueError("A replay viewer requires at least one recorded native state")
    first = _state(records[0])
    team = manifest.get("config", {}).get("reasoner_team", 0)
    states = [
        {
            "tick": _state(record)["tick"],
            "bots": _state(record).get("bots", []),
            "scores": _state(record).get("scores", [0, 0]),
            "checksum": record.get("checksum", ""),
            "public_telemetry": _public_telemetry(_state(record), team),
        }
        for record in records
    ]
    commands = []
    for record in _read_ndjson(run_dir / "commands.ndjson"):
        envelope = _command(record)
        if envelope.get("type") not in {"assessment", "fallback"}:
            continue
        commands.append(
            {
                "receipt_tick": record.get("receipt_tick", envelope.get("cutoff_tick", 0)),
                "command": envelope,
                "result": _result(record),
            }
        )
    observations = [
        item
        for item in _read_ndjson(run_dir / "observations.ndjson")
        if item.get("type") == "observation"
    ]
    fallback_requests = {
        record["command"].get("request_id")
        for record in commands
        if record["command"].get("type") == "fallback"
    }
    failures = []
    for cycle in _read_ndjson(run_dir / "reasoning.ndjson"):
        if (
            cycle.get("type") != "reasoning_cycle"
            or cycle.get("status") in {"pending", "valid"}
            or cycle.get("request_id") in fallback_requests
        ):
            continue
        failures.append(
            {
                key: cycle.get(key)
                for key in (
                    "request_id",
                    "cutoff_tick",
                    "status",
                    "latency_seconds",
                    "validation_category",
                    "error",
                )
            }
        )
        # Busy is known at the planning boundary. Without a native fallback
        # receipt there is no exact delivery tick; show the other host outcomes
        # only at the end, rather than inventing a timing measurement.
        failures[-1]["visible_tick"] = (
            cycle.get("cutoff_tick", 0) if cycle.get("status") == "busy" else states[-1]["tick"]
        )
    summary = summarize_run(run_dir)
    payload = {
        "run_id": manifest.get("run_id"),
        "config": {
            key: manifest.get("config", {}).get(key)
            for key in ("seed", "tick_hz", "reasoner_team", "bots_per_team", "duration_ticks")
        },
        "map": {
            "size": first.get("size", 32),
            "nodes": first.get("nodes", []),
            "blocked": first.get("blocked", []),
        },
        "states": states,
        "observations": observations,
        "commands": commands,
        "failures": failures,
        "forecasts": summary["forecasts"],
        "summary": {
            key: summary[key]
            for key in (
                "forecast_coverage",
                "forecast_accuracy",
                "brier_score",
                "planning_opportunities",
                "scored_forecasts",
                "configuration",
                "measurement_kind",
                "cost_usd",
            )
        },
    }
    output_path = Path(output_path) if output_path else run_dir / "replay.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fonts, font_license = _embedded_fonts()
    template = _HTML.replace("__FONT_CSS__", fonts).replace("__FONT_LICENSE__", font_license)
    output_path.write_text(
        template.replace("__REPLAY_DATA__", _script_json(payload)), encoding="utf-8"
    )
    return output_path


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>Arena Intelligence · Recorded replay</title>
<!-- IBM Plex fonts, SIL Open Font License 1.1:
__FONT_LICENSE__
-->
<style>
__FONT_CSS__
:root{color-scheme:dark;--bg:#0b0e11;--panel:#101519;--line:#283138;--text:#e7ebec;--muted:#98a5ac;--blue:#9ad4e7;--red:#eea394;--amber:#dbc18d;--purple:#c1b3da;--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 "IBM Plex Sans",system-ui,-apple-system,sans-serif}button,select,input{font:inherit}button,select{background:transparent;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:8px 12px}button{cursor:pointer}button:hover{background:#202a31;border-color:#49575f}button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px}button:disabled{opacity:.4;cursor:default}a{color:var(--blue)}button svg{width:15px;height:15px;vertical-align:-2px;margin-right:7px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.icon-button svg{margin:0}.icon-button{padding:8px 9px}.text-button{border:0;padding:0;font-size:12px;color:var(--blue)}.text-button:hover{background:transparent}
main{max-width:1400px;margin:auto;padding:30px 36px 24px}.site-header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;padding-bottom:25px;margin-bottom:24px;border-bottom:1px solid var(--line)}.eyebrow{font:11px/1.4 var(--mono);letter-spacing:.11em;color:var(--muted);text-transform:uppercase}h1{font-size:35px;line-height:1.2;font-weight:500;letter-spacing:-.045em;margin:7px 0 5px}.subtitle{font-size:13px;color:var(--muted);margin:0}.run{font-size:12px;line-height:1.8;color:var(--muted);text-align:right}.run strong{display:block;color:var(--text);font-weight:500;font-size:13px}.run span{font-family:var(--mono);font-size:10px}.tag{font:10px/1.3 var(--mono);color:var(--muted)}
.layout{display:grid;grid-template-columns:minmax(440px,1.15fr) minmax(390px,1fr);gap:26px;align-items:start}.panel{background:var(--panel);border:1px solid var(--line);border-radius:7px;overflow:hidden}.panel-heading{padding:15px 19px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}h2{font-size:15px;font-weight:500;margin:0;letter-spacing:-.015em}h3{font-size:14px;font-weight:500;margin:0}.map-title{display:flex;align-items:baseline;gap:10px}.map-spec{font:10px var(--mono);color:var(--muted)}.arena-score{display:flex;align-items:baseline;gap:9px;font:10px var(--mono);color:var(--muted)}.arena-score strong{font:500 21px "IBM Plex Sans",system-ui,sans-serif}.arena-score .team0{color:var(--blue)}.arena-score .team1{color:var(--red)}.score-divider{font-size:14px;color:#55616a}.viewbar{padding:10px 19px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px}.viewbar select{font-size:11px;padding:5px 9px;max-width:200px}.notice{color:var(--muted);font-size:11px;line-height:1.5}.notice.truth{color:var(--purple)}.mapbox{padding:19px 22px 10px}canvas{display:block;width:100%;aspect-ratio:1;max-height:570px;max-width:570px;margin:auto;background:#0c1115}.legend{padding:0 19px 12px;display:flex;justify-content:center;gap:16px;flex-wrap:wrap;font-size:10px;color:var(--muted)}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px}.blue{background:var(--blue)}.red{background:var(--red)}.amber{border:1px solid var(--amber);background:transparent}.snapshot{padding:11px 19px;font-size:11px;color:var(--muted);line-height:1.6;border-top:1px solid var(--line)}.snapshot strong{font-weight:500;color:var(--text)}
.timeline{padding:15px 19px;background:#11181d;border-top:1px solid var(--line)}.timeline input{appearance:none;width:100%;height:3px;border-radius:2px;background:#42515b;margin:0 0 10px;cursor:pointer}.timeline input::-webkit-slider-thumb{appearance:none;width:10px;height:10px;border:2px solid #dce5e9;background:#17252e;border-radius:50%}.timeline input::-moz-range-thumb{width:7px;height:7px;border:2px solid #dce5e9;background:#17252e;border-radius:50%}.planning-rail{position:relative;height:33px;margin-bottom:6px}.planning-rail button{position:absolute;top:0;transform:translateX(-50%);min-width:44px;min-height:32px;padding:0;border:0;border-radius:0;color:#819099;font:9px var(--mono);background:transparent}.planning-rail button:hover{color:var(--text)}.planning-rail button.active{color:var(--blue)}.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.controls button,.controls select{font-size:12px;padding:7px 10px}.controls #play{background:#dce5e9;color:#172126;border-color:#dce5e9;min-width:83px}.controls #play:hover{background:#fff}.clock{margin-left:auto;font:12px var(--mono);color:var(--text)}.speed-label{font-size:10px;color:var(--muted)}
.reasoning .panel-heading{padding:16px 21px}.reasoning-meta{font:10px var(--mono);color:var(--muted)}.assessment-list{padding:0 21px 12px}.assessment{padding:19px 0;border-bottom:1px solid var(--line)}.assessment:last-child{border-bottom:0}.assessment-title{display:flex;justify-content:space-between;align-items:center;gap:10px}.status{font:9px/1.5 var(--mono);letter-spacing:.03em;padding:2px 0;color:var(--muted)}.status.accepted{color:var(--blue)}.status.rejected{color:var(--red)}.assessment-timing{margin:6px 0 15px;font:10px/1.65 var(--mono);color:var(--muted)}.priority-label{font:9px var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin:18px 0 8px}.claim{font-size:15px;line-height:1.65;color:var(--text);margin:0 0 12px}.alternative{font-size:12px;line-height:1.65;color:var(--muted);margin:0 0 12px;border-left:1px solid #46525a;padding-left:12px}.alternative strong{font-weight:500;color:#bbc5ca}.citation-row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin:10px 0 15px}.citation-label{font:9px var(--mono);color:var(--muted);margin-right:4px}.evidence-link{font:9px var(--mono);padding:3px 5px;border-radius:3px;color:var(--blue);background:#15232b;border-color:#233c48}.evidence-link:hover{background:#203947}.additional-hypothesis{margin:0;border-top:1px solid #233039;padding:9px 0}.additional-hypothesis summary{cursor:pointer;font-size:12px;color:#afbbc2;list-style:none}.additional-hypothesis summary::before{content:"+";display:inline-block;font:12px var(--mono);width:18px;color:var(--muted)}.additional-hypothesis[open] summary::before{content:"−"}.additional-hypothesis .claim{font-size:13px;margin-top:12px}.forecast-block{margin:16px 0 2px;border-top:1px solid var(--line);padding-top:14px}.forecast-heading{display:flex;justify-content:space-between;gap:8px;font:9px var(--mono);color:var(--muted);margin-bottom:10px}.forecast{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.prob{font-size:10px;color:var(--muted)}.prob strong{display:block;font:500 18px "IBM Plex Sans",system-ui,sans-serif;color:#d9e2e6;margin:2px 0 7px}.prob.leader strong{color:var(--blue)}.bar{height:3px;background:#2b353c;overflow:hidden}.bar span{display:block;background:#6d8895;height:100%}.prob.leader .bar span{background:var(--blue)}.forecast-note{font-size:10px;line-height:1.6;color:var(--muted);margin:11px 0 0}.assignment-note{font:10px/1.7 var(--mono);color:var(--muted);margin:12px 0 0}.evaluation{border-top:1px solid var(--line);padding:12px 21px;font-size:10px;color:var(--muted);line-height:1.6}.previous-assessment{padding:12px 0}.previous-assessment>summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;gap:10px;font-size:12px;color:var(--muted)}.previous-assessment>summary::after{content:"+";font:12px var(--mono);color:var(--muted)}.previous-assessment[open]>summary::after{content:"−"}.previous-assessment[open] .assessment{border:0;padding-bottom:7px}.empty{padding:25px 0;color:var(--muted);font-size:12px;line-height:1.75}.empty strong{display:block;font-size:17px;font-weight:400;color:var(--text);margin-bottom:7px}.empty button{margin-top:15px;font-size:12px;color:var(--blue)}.failure-card{padding:13px 0}.failure-card .claim{font-size:12px;margin:8px 0 0}.failure-card .muted{font-size:10px;margin:6px 0}.muted{color:var(--muted)}.small{font-size:11px}
.evidence-panel{margin-top:18px;border-left:2px solid #587380;border-top:1px solid var(--line);background:#11191e;border-radius:0 5px 5px 0}.evidence-panel .panel-heading{padding:13px 19px;border-bottom:0}.evidence-detail{padding:0 19px 17px;font-size:12px}.evidence-detail h3{font-size:16px;margin:1px 0 12px;font-weight:400}.evidence-facts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 15px}.evidence-facts dt{font:9px var(--mono);color:var(--muted);margin-bottom:3px}.evidence-facts dd{margin:0;font-size:12px;color:#d1dce1}.raw-evidence{font-size:10px;color:var(--muted)}.raw-evidence summary{cursor:pointer}.raw-evidence pre{white-space:pre-wrap;overflow-wrap:anywhere;font:10px/1.6 var(--mono);max-height:210px;overflow:auto;margin:12px 0 0}.footer{margin-top:19px;color:#697981;font:9px var(--mono);display:flex;gap:20px;justify-content:space-between}
@media(min-width:1100px) and (max-height:900px){canvas{max-height:480px;max-width:480px}.site-header{padding-bottom:18px;margin-bottom:20px}main{padding-top:22px}.mapbox{padding-top:15px}}
@media(max-width:1000px){.layout{grid-template-columns:minmax(360px,1fr) minmax(330px,.95fr);gap:18px}main{padding:24px}.mapbox{padding:15px}.legend{gap:10px}.arena-score{gap:6px}.viewbar{align-items:flex-start}.notice{max-width:220px}h1{font-size:31px}}
@media(max-width:780px){main{padding:22px 14px}.layout{grid-template-columns:1fr;gap:21px}.site-header{align-items:flex-start;gap:15px;margin-bottom:20px;padding-bottom:20px}h1{font-size:28px}.run{max-width:155px;font-size:10px}.run strong{font-size:10px;line-height:1.65}.run span{font-size:8px}.subtitle{font-size:11px;max-width:200px}.panel-heading{padding:13px 15px}.arena-score strong{font-size:19px}.viewbar{padding:9px 15px}.notice{font-size:10px;max-width:195px}.viewbar select{max-width:155px;font-size:10px}.mapbox{padding:14px 15px 9px}.legend{font-size:9px;gap:13px;justify-content:flex-start;padding:0 15px 12px}.snapshot{padding:10px 15px}.timeline{padding:14px 15px}.clock{font-size:10px}.controls{gap:6px}.controls button,.controls select{font-size:11px;padding:7px 8px}.speed-label{display:none}.reasoning .panel-heading{padding:15px 17px}.assessment-list{padding:0 17px 12px}.claim{font-size:14px}.footer{font-size:8px;line-height:1.7;gap:15px}.evidence-facts{gap:12px}}
@media(max-width:780px){.evidence-link{min-height:28px;padding:5px 7px}.planning-rail{height:44px}.planning-rail button{min-height:44px;padding:0}.controls button,.controls select{min-height:36px}.viewbar select{min-height:32px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style>
</head>
<body>
<!--
Inline playback icons are from Lucide (https://lucide.dev).
ISC License
Copyright (c) 2026 Lucide Icons and Contributors
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
The X icon is derived from Feather:
The MIT License (MIT)
Copyright (c) 2013-present Cole Bemis
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
-->
<main>
<header class="site-header"><div><div class="eyebrow">Recorded match</div><h1>Arena Intelligence</h1><p class="subtitle">A fictional strategy arena.</p></div><div class="run" id="run-info"></div></header>
<div class="layout">
<section class="panel" aria-label="Replay map and playback">
<div class="panel-heading"><div class="map-title"><h2>Arena</h2><span class="map-spec">32 × 32</span></div><div class="arena-score" aria-label="Team scores"><span>Team 0</span><strong id="score0" class="team0">0</strong><span class="score-divider">:</span><strong id="score1" class="team1">0</strong><span>Team 1</span></div></div>
<div class="viewbar"><div id="notice" class="notice"></div><select id="view" aria-label="Replay information access"><option value="observer">Team observer</option><option value="truth">Evaluator ground truth</option></select></div>
<div class="mapbox"><canvas id="map" width="900" height="900" aria-label="Grid with resource nodes, obstacles, and recorded bot positions"></canvas></div>
<div class="legend"><span><i class="dot blue"></i>Team 0</span><span><i class="dot red"></i>Team 1</span><span><i class="dot amber"></i>Opponent report</span><span>N0 / N1 / N2 · resource nodes</span></div>
<div id="snapshot" class="snapshot" aria-live="polite"></div>
<div class="timeline"><input id="scrub" type="range" min="0" max="1" value="0" aria-label="Replay tick"><div id="planning-rail" class="planning-rail" aria-label="Planning opportunities"></div><div class="controls"><button id="play" type="button">Play</button><button id="restart" type="button" aria-label="Restart replay">Restart</button><label class="speed-label" for="speed">Speed</label><select id="speed" aria-label="Playback speed"><option value="0.25">0.25×</option><option value="1" selected>1×</option><option value="4">4×</option><option value="16">16×</option><option value="64">64×</option></select><div class="clock" id="clock"></div></div></div>
</section>
<aside class="analysis-column">
<section class="panel reasoning" aria-label="Scores and reasoning history">
<div class="panel-heading"><div><h2>Assessment</h2><span id="assessment-count" class="reasoning-meta"></span></div><button id="latest-assessment" class="text-button" type="button">Latest assessment</button></div>
<div id="assessments" class="assessment-list"></div>
<div id="evaluation" class="evaluation"></div>
</section>
<section class="evidence-panel" aria-label="Selected evidence" hidden><div class="panel-heading"><div><h2>Evidence</h2><span class="tag">Delivered to the observing team</span></div><button id="close-evidence" class="icon-button" type="button" aria-label="Close evidence">×</button></div><div id="evidence-detail" class="evidence-detail"></div></section>
<div class="assignment-note"><span id="active-count">0</span> bot assignments under active advice.</div>
</aside>
</div>
<div class="footer"><span id="checksum"></span><span>Recorded events · 10 Hz simulation</span></div>
</main>
<script id="replay-data" type="application/json">__REPLAY_DATA__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("replay-data").textContent);
const $=id=>document.getElementById(id), ctx=$("map").getContext("2d");
const hz=data.config.tick_hz||10, team=data.config.reasoner_team||0, maxTick=data.states[data.states.length-1].tick;
const colors=["#9ad4e7","#eea394"], evidence=new Map();
data.observations.sort((a,b)=>a.cutoff_tick-b.cutoff_tick);
data.commands.sort((a,b)=>a.receipt_tick-b.receipt_tick);
for(const envelope of data.observations){for(const item of envelope.observation.evidence||[]){if(!evidence.has(item.id))evidence.set(item.id,{...item,first_public_tick:envelope.cutoff_tick});}}
let tick=0,playing=false,lastFrame=null,renderedHistory="",selectedEvidence=null,inspectedVisible=null;
$("scrub").max=maxTick;
const backendLabel=data.summary.configuration.startsWith("multi/")?"Claude · Analyst + challenger":data.summary.configuration.startsWith("single/")?"Claude · Single analyst":data.summary.configuration.startsWith("frequency/")?"Frequency baseline":"Scripted rehearsal";
$("run-info").append(element("strong",backendLabel),element("div","Seed "+data.config.seed+" · "+(maxTick/hz).toFixed(0)+" simulated seconds"),element("span",data.summary.measurement_kind==="live_api"?"Recorded model cost $"+data.summary.cost_usd.toFixed(3):"Recorded offline run"));
$("run-info").title="Run "+data.run_id;
function latest(records,at,key){let lo=0,hi=records.length-1,best=null;while(lo<=hi){let mid=(lo+hi)>>1;if(records[mid][key]<=at){best=records[mid];lo=mid+1;}else hi=mid-1;}return best;}
function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(className)node.className=className;return node;}
function asList(value){return Array.isArray(value)?value:[];}
function asObject(value){return value&&typeof value==="object"&&!Array.isArray(value)?value:{};}
function inspect(id,scroll=false){
 selectedEvidence=id;const item=evidence.get(id),panel=document.querySelector(".evidence-panel"),detail=$("evidence-detail");panel.hidden=false;detail.replaceChildren();if(scroll)requestAnimationFrame(()=>panel.scrollIntoView({block:"nearest",behavior:"instant"}));
 if(!item||item.first_public_tick>tick){detail.append(element("p","This reference is not present in the delivered evidence at this tick.","muted"));return;}
 const record=item.data||{},isNode=item.kind==="node_status",title=isNode?"Occupancy report · node "+record.node_id:"Sighting · bot "+record.bot_id;
 detail.append(element("h3",title));const facts=element("dl",undefined,"evidence-facts");
 const entries=[["Reference",id],["Observed",((record.observed_tick??item.tick)/hz).toFixed(1)+"s"],["Delivered",((record.delivered_tick??item.first_public_tick)/hz).toFixed(1)+"s"]];
 if(isNode){entries.push(["Friendly bots",record.friendly_count],["Sensor-visible opponents",record.opponent_count]);}else{entries.push(["Reported position",record.x+", "+record.y]);}
 for(const [label,value] of entries){const pair=element("div");pair.append(element("dt",label),element("dd",value));facts.append(pair);}detail.append(facts);
 if(isNode)detail.append(element("p","Opponent counts are a visible lower bound. An empty report does not establish absence.","small muted"));
 const raw=element("details",undefined,"raw-evidence");raw.append(element("summary","Recorded evidence"),element("pre",JSON.stringify(item,null,2)));detail.append(raw);
}
function control(at){return data.commands.filter(item=>item.result.accepted&&item.result.application_tick<=at).sort((a,b)=>b.result.application_tick-a.result.application_tick)[0]||null;}
function assignment(at){const current=control(at);return current&&current.command.type==="assessment"&&current.command.expiry_tick>at?current:null;}
function drawMap(state,observation,isTruth,active){
 const map=observation||data.map,size=map.size||32,nodes=map.nodes||data.map.nodes,blocked=map.blocked||data.map.blocked,cell=900/size,labelFont=Math.max(18,900/Math.max(1,$("map").getBoundingClientRect().width)*9);
 ctx.clearRect(0,0,900,900);ctx.fillStyle="#0c1115";ctx.fillRect(0,0,900,900);
 for(let i=0;i<=size;i++){ctx.strokeStyle=i%4===0?"#25323a":"#172127";ctx.lineWidth=i%4===0?.9:.65;ctx.beginPath();ctx.moveTo(i*cell,0);ctx.lineTo(i*cell,900);ctx.moveTo(0,i*cell);ctx.lineTo(900,i*cell);ctx.stroke();}
 for(const point of blocked){ctx.fillStyle="#253139";ctx.fillRect(point[0]*cell+1,point[1]*cell+1,cell-2,cell-2);ctx.fillStyle="#303e47";ctx.fillRect(point[0]*cell+1,point[1]*cell+1,cell-2,1.3);}
 const friendly=isTruth?state.bots:state.public_telemetry.bots,reports=new Map();if(!isTruth&&observation){for(const bot of observation.last_seen||[])reports.set(bot.id,bot);for(const bot of observation.visible_opponents||[])reports.set(bot.id,bot);}
 for(const node of nodes){const x=(node.x+.5)*cell,y=(node.y+.5)*cell;let tint="#647780";
  if(isTruth){const counts=[0,0];for(const bot of state.bots){if(Math.abs(bot.x-node.x)+Math.abs(bot.y-node.y)<=2)counts[bot.team]++;}if(counts[0]!==counts[1])tint=colors[counts[0]>counts[1]?0:1];}
  ctx.save();ctx.fillStyle=tint;ctx.globalAlpha=.1;ctx.beginPath();ctx.moveTo(x,y-cell*2.5);ctx.lineTo(x+cell*2.5,y);ctx.lineTo(x,y+cell*2.5);ctx.lineTo(x-cell*2.5,y);ctx.closePath();ctx.fill();ctx.globalAlpha=.45;ctx.strokeStyle=tint;ctx.setLineDash([3,5]);ctx.lineWidth=1;ctx.stroke();ctx.restore();
 }
 const assignments=new Map(active?asList(asObject(active.command.assessment).assignments).map(item=>[item.bot_id,item.objective_id]):[]);
 ctx.save();ctx.globalAlpha=.25;ctx.setLineDash([3,6]);ctx.lineWidth=1.2;
 for(const bot of friendly){const target=assignments.get(bot.id)??bot.objective_id,node=nodes.find(item=>item.id===target);if(!node)continue;ctx.strokeStyle=colors[bot.team];ctx.beginPath();ctx.moveTo((bot.x+.5)*cell,(bot.y+.5)*cell);ctx.lineTo((node.x+.5)*cell,(node.y+.5)*cell);ctx.stroke();}ctx.restore();
 function groupDots(bots,report){const groups=new Map();for(const bot of bots){const key=bot.team+":"+bot.x+":"+bot.y,group=groups.get(key)||{bot,count:0};group.count++;groups.set(key,group);}
  for(const group of groups.values()){const bot=group.bot,x=(bot.x+.5)*cell,y=(bot.y+.5)*cell;ctx.save();ctx.globalAlpha=report?Math.max(.35,1-(tick-(bot.observed_tick||0))/(hz*100)):1;ctx.beginPath();ctx.arc(x,y,cell*(report?.38:group.count>1?.29:.21),0,Math.PI*2);ctx.strokeStyle=report?"#dbc18d":colors[bot.team];ctx.fillStyle=report?"#22241d":colors[bot.team];ctx.lineWidth=report?1.8:1;if(!report)ctx.fill();ctx.stroke();if(!report){ctx.fillStyle="#e7f5f8";ctx.beginPath();ctx.arc(x,y,1.6,0,Math.PI*2);ctx.fill();}if(group.count>1){ctx.fillStyle=report?"#dbc18d":colors[bot.team];ctx.font=labelFont+"px IBM Plex Mono,monospace";ctx.textAlign="left";ctx.textBaseline=report?"top":"bottom";ctx.fillText("×"+group.count,x+cell*.38,y+cell*(report?.15:-.1));}ctx.restore();}
 }
 groupDots(friendly,false);groupDots([...reports.values()],true);
 for(const node of nodes){const x=(node.x+.5)*cell,y=(node.y+.5)*cell;ctx.fillStyle="#0d1418";ctx.fillRect(x-cell*.65,y-cell*.93-labelFont*.6,cell*1.3,labelFont*1.2);ctx.fillStyle="#bac8ce";ctx.font=labelFont+"px IBM Plex Mono,monospace";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("N"+node.id,x,y-cell*.93);}
 const chosen=evidence.get(selectedEvidence);if(chosen&&chosen.first_public_tick<=tick){const node=chosen.kind==="node_status"?nodes.find(item=>item.id===chosen.data.node_id):chosen.data;if(node&&Number.isFinite(node.x)&&Number.isFinite(node.y)){ctx.save();ctx.strokeStyle="#e0cc9d";ctx.lineWidth=2;ctx.setLineDash([5,4]);ctx.beginPath();ctx.arc((node.x+.5)*cell,(node.y+.5)*cell,cell*.8,0,Math.PI*2);ctx.stroke();ctx.restore();}}
 ctx.strokeStyle="#3c4a53";ctx.lineWidth=1;ctx.strokeRect(.5,.5,899,899);
}
function shorten(text,length=85){text=String(text||"");return text.length<=length?text:text.slice(0,length).replace(/\s+\S*$/,"")+"…";}
function citations(container,hypothesis){
 const ids=asList(hypothesis.evidence_ids),row=element("div",undefined,"citation-row");row.append(element("span",ids.length?"Evidence":"Prior · no supporting reports","citation-label"));
 for(const [index,id] of ids.entries()){const record=evidence.get(id),source=record?.data||{},time=((source.observed_tick??record?.tick??0)/hz).toFixed(0),label=record?(record.kind==="node_status"?"N"+source.node_id:"B"+source.bot_id)+" · "+time+"s":String(id);const button=element("button",label,"evidence-link");button.type="button";button.title="Inspect "+id;button.setAttribute("aria-label","Inspect evidence "+id);button.addEventListener("click",()=>{inspect(id,true);render();});if(index<6){row.append(button);}else{let more=row.querySelector("details");if(!more){more=element("details",undefined,"raw-evidence");more.append(element("summary","+"+(ids.length-6)+" reports"));row.append(more);}more.append(button);}}
 container.append(row);
}
function displayClaim(text){
 return String(text||"").replace(/\((?:e\d+(?:[–-](?:e)?\d+)?)(?:,\s*e\d+(?:[–-](?:e)?\d+)?)*\)/g,"")
 .replace(/\bticks?\s+(\d+)\s*(?:[–-]|to|through)\s*(\d+)/g,(_,first,last)=>(Number(first)/hz).toFixed(1)+"s to "+(Number(last)/hz).toFixed(1)+"s")
 .replace(/\bticks? (\d+)/g,(_,value)=>(Number(value)/hz).toFixed(1)+"s")
 .replace(/(\d+) ticks/g,(_,value)=>(Number(value)/hz).toFixed(1)+"s")
 .replace(/cutoff\s*\+\s*300/g,"the 30s forecast horizon").replace(/\bcutoff\b/g,"observation time")
 .replace(/\s+([,.])/g,"$1").replace(/ {2,}/g," ").trim();
}
function hypothesisContent(container,value){const hypothesis=asObject(value);container.append(element("p",displayClaim(hypothesis.claim),"claim"));if(hypothesis.alternative){const alternative=element("p",undefined,"alternative");alternative.append(element("strong","Alternative · "),document.createTextNode(displayClaim(hypothesis.alternative)));container.append(alternative);}citations(container,hypothesis);}
function history(at,isTruth){
 const visible=data.commands.filter(item=>item.receipt_tick<=at),failures=(data.failures||[]).filter(item=>item.visible_tick<=at),key=visible.length+":"+failures.length+":"+isTruth+":"+visible.map(item=>{const f=data.forecasts.find(x=>x.request_id===item.command.request_id);return f&&f.horizon_tick<=at?"h":"p";}).join("")+":"+(assignment(at)?.command.request_id||"")+":"+(control(at)?.command.type||"")+":"+(control(at)?.command.request_id||"");
 if(key===renderedHistory)return;renderedHistory=key;
 const list=$("assessments"),eventCount=visible.length+failures.length;list.replaceChildren();$("assessment-count").textContent=eventCount+(eventCount===1?" recorded event":" recorded events");
 if(!eventCount){const empty=element("div",undefined,"empty");empty.append(element("strong","Assessment pending"),element("div","The simulation keeps running while the agents analyze the observation."));const first=data.commands.find(item=>item.command.type==="assessment");if(first){const jump=element("button","Inspect first response · "+(first.result.application_tick/hz).toFixed(1)+"s");jump.type="button";jump.addEventListener("click",()=>jumpTo(first.result.application_tick));empty.append(jump);}list.append(empty);return;}
 const events=[...visible.map(item=>({...item,event_kind:"control",event_tick:item.receipt_tick})),...failures.map(item=>({...item,event_kind:"failure",event_tick:item.visible_tick}))].sort((a,b)=>b.event_tick-a.event_tick);
 const primary=events.find(item=>item.command?.type==="assessment"&&item.result.accepted)||events.find(item=>item.command?.type==="assessment");
 for(const item of events){
  if(item.event_kind==="failure"){const card=element("article",undefined,"assessment failure-card"),heading=element("div",undefined,"assessment-title");heading.append(element("h3","Cycle at "+(item.cutoff_tick/hz).toFixed(1)+"s"),element("span",item.status,"status rejected"));card.append(heading,element("p",item.status==="busy"?"A preceding reasoning cycle was still in flight.":"This cycle ended without usable advice.","claim"));if(item.status!=="busy")card.append(element("p","No native fallback delivery is recorded for this cycle. Its exact delivery tick is unavailable.","muted"));list.append(card);continue;}
  const command=item.command,result=item.result,assessment=asObject(command.assessment),card=element("article",undefined,"assessment");
  if(command.type==="fallback"){card.classList.add("failure-card");const heading=element("div",undefined,"assessment-title"),pending=result.accepted&&result.application_tick>at;heading.append(element("h3","Cycle failure at "+(command.cutoff_tick/hz).toFixed(1)+"s"),element("span",result.accepted?(pending?"proposed":"nearest fallback"):"rejected","status "+(result.accepted?"accepted":"rejected")));card.append(heading,element("p",result.accepted?(pending?"Nearest-objective fallback is scheduled at "+(result.application_tick/hz).toFixed(1)+"s.":"Nearest-objective fallback cleared preceding advice at "+(result.application_tick/hz).toFixed(1)+"s."):"Rejected fallback retained in the history; it did not clear current advice.","claim"));list.append(card);continue;}
  const active=assignment(at)?.command.request_id===command.request_id,successor=data.commands.find(other=>other.result.accepted&&other.result.application_tick>result.application_tick&&other.result.application_tick<=at&&other.result.application_tick<command.expiry_tick);
  let status="rejected";if(result.accepted)status=result.application_tick>at?"proposed":active?"applied":successor?"replaced":command.expiry_tick<=at?"expired":"accepted";
  const heading=element("div",undefined,"assessment-title");heading.append(element("h3","Observation at "+(command.cutoff_tick/hz).toFixed(1)+"s"),element("span",status,"status "+(result.accepted?"accepted":"rejected")));card.append(heading);
  const timing=element("div",undefined,"assessment-timing"),age=element("span");age.dataset.inputAge=command.cutoff_tick;timing.append(age,document.createTextNode(" · "+(result.accepted?"Applied "+(result.application_tick/hz).toFixed(1)+"s":result.reason||"Response rejected")));if(successor)timing.append(document.createTextNode(" · Replaced "+(successor.result.application_tick/hz).toFixed(1)+"s"));else if(result.accepted&&command.expiry_tick<=at)timing.append(document.createTextNode(" · Expired "+(command.expiry_tick/hz).toFixed(1)+"s"));card.append(timing);
  const hypotheses=asList(assessment.hypotheses);if(hypotheses.length){hypothesisContent(card,hypotheses[0]);}
  for(const value of hypotheses.slice(1)){const detail=element("details",undefined,"additional-hypothesis");detail.append(element("summary",shorten(displayClaim(asObject(value).claim))));hypothesisContent(detail,value);card.append(detail);}
  const forecast=asObject(assessment.forecast),probs=asList(forecast.probabilities);if(probs.length===4){const block=element("div",undefined,"forecast-block"),title=element("div",undefined,"forecast-heading"),horizon=element("span");horizon.dataset.horizonStatus=forecast.horizon_tick;title.append(element("span","Forecast for "+(forecast.horizon_tick/hz).toFixed(1)+"s"),horizon);block.append(title);const distribution=element("div",undefined,"forecast"),leader=probs.indexOf(Math.max(...probs));for(let i=0;i<4;i++){const column=element("div",i===3?"None":"Node "+i,"prob"+(i===leader?" leader":"")),value=Number(probs[i]);column.append(element("strong",Number.isFinite(value)?(value*100).toFixed(0)+"%":"invalid"));const bar=element("div",undefined,"bar"),fill=element("span");fill.style.width=(Number.isFinite(value)?Math.max(0,Math.min(100,value*100)):0)+"%";bar.append(fill);column.append(bar);distribution.append(column);}block.append(distribution,element("p","Conditional on this assignment remaining active through the forecast horizon.","forecast-note"));card.append(block);}
  const counts=[0,0,0];for(const objective of asList(assessment.assignments)){if(Number.isInteger(objective.objective_id)&&objective.objective_id>=0&&objective.objective_id<3)counts[objective.objective_id]++;}if(counts.some(Boolean))card.append(element("p",(status==="applied"?"Applied":"Recorded")+" objectives · "+counts.map((count,index)=>"N"+index+" ×"+count).join(" · "),"assignment-note"));
  const evaluated=data.forecasts.find(x=>x.request_id===command.request_id);if(evaluated&&isTruth&&evaluated.horizon_tick<=at){card.append(element("p","Evaluation · "+evaluated.status+(evaluated.status==="scored"?" · observed outcome "+(evaluated.outcome===3?"none":"node "+evaluated.outcome)+" · "+(evaluated.correct?"correct":"contradicted")+" · Brier "+evaluated.brier_score.toFixed(3):" · "+evaluated.reason),"forecast-note"));}
  if(item===primary){list.append(card);}else{const previous=element("details",undefined,"previous-assessment"),summary=element("summary");summary.append(element("span","Earlier observation · "+(command.cutoff_tick/hz).toFixed(1)+"s"),element("span",status,"status"));previous.append(summary,card);list.append(previous);}
 }
}
function render(){
 const at=Math.floor(tick),state=latest(data.states,at,"tick"),envelope=latest(data.observations,at,"cutoff_tick"),observation=envelope?.observation||null,isTruth=$("view").value==="truth",active=assignment(at);
 drawMap(state,observation,isTruth,active);$("notice").className="notice"+(isTruth?" truth":"");$("notice").textContent=isTruth?"Evaluator view · includes positions withheld from agents.":"Team "+team+" view · timestamped reports, with unknowns retained.";
 const scores=isTruth?state.scores:state.public_telemetry.scores;$("score0").textContent=scores[0];$("score1").textContent=scores[1];$("active-count").textContent=active?asList(asObject(active.command.assessment).assignments).length:0;
 $("snapshot").replaceChildren();if(isTruth){$("snapshot").append(element("strong","Complete state · "+(state.tick/hz).toFixed(1)+"s"),document.createTextNode(" · evaluator ground truth"));}else if(envelope){$("snapshot").append(element("strong","Friendly telemetry · "+(state.tick/hz).toFixed(1)+"s"),document.createTextNode(" · Agent observation · "+(envelope.cutoff_tick/hz).toFixed(1)+"s ("+((at-envelope.cutoff_tick)/hz).toFixed(1)+"s old). Opposing reports retain source age."));}else{$("snapshot").textContent="Friendly telemetry · "+(state.tick/hz).toFixed(1)+"s. No agent observation has been delivered.";}
 $("snapshot").title="Friendly positions and public scores update continuously; opponent reports retain their original timestamps.";
 $("clock").textContent=(at/hz).toFixed(1)+" / "+(maxTick/hz).toFixed(1)+"s";$("scrub").value=at;$("scrub").setAttribute("aria-valuetext",(at/hz).toFixed(1)+" simulated seconds");$("checksum").textContent=isTruth?"State checksum · "+state.checksum:"Observer view · friendly telemetry + delivered evidence";
 history(at,isTruth);for(const span of document.querySelectorAll("[data-input-age]"))span.textContent="Input age "+((at-Number(span.dataset.inputAge))/hz).toFixed(1)+"s";for(const span of document.querySelectorAll("[data-horizon-status]")){const seconds=(Number(span.dataset.horizonStatus)-at)/hz;span.textContent=seconds>0?seconds.toFixed(1)+"s remaining":"Horizon passed";}
 for(const button of $("planning-rail").querySelectorAll("button"))button.classList.toggle("active",Number(button.dataset.tick)===(envelope?.cutoff_tick));
 $("evaluation").textContent=isTruth&&at>=maxTick?"Covered forecasts · "+data.summary.scored_forecasts+" / "+data.summary.planning_opportunities+". Accuracy · "+(data.summary.forecast_accuracy===null?"unavailable":(data.summary.forecast_accuracy*100).toFixed(1)+"%")+". "+(data.summary.measurement_kind==="offline"?"Offline replay; no LLM improvement claim.":"Results apply to this recorded match."):isTruth?"Ground-truth outcomes appear after each forecast horizon.":"Forecast outcomes are available in the separate evaluator view.";
 if(selectedEvidence){const visible=Boolean(evidence.get(selectedEvidence)?.first_public_tick<=at);if(visible!==inspectedVisible){inspect(selectedEvidence);inspectedVisible=visible;}}
}
function jumpTo(at){tick=Math.max(0,Math.min(maxTick,at));setPlaying(false);render();}
// Lucide icon geometry. Attribution and licenses are included in this HTML.
function icon(name){
 const shapes={play:[["path",{d:"M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"}]],pause:[["rect",{x:14,y:3,width:5,height:18,rx:1}],["rect",{x:5,y:3,width:5,height:18,rx:1}]],restart:[["path",{d:"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"}],["path",{d:"M3 3v5h5"}]],latest:[["path",{d:"M7 7h10v10"}],["path",{d:"M7 17 17 7"}]],close:[["path",{d:"M18 6 6 18"}],["path",{d:"m6 6 12 12"}]]},svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("aria-hidden","true");for(const [tag,attributes] of shapes[name]||[]){const child=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [key,value] of Object.entries(attributes))child.setAttribute(key,value);svg.append(child);}return svg;
}
function setPlaying(value){playing=value;lastFrame=null;$("play").replaceChildren(icon(value?"pause":"play"),element("span",value?"Pause":"Play"));}

$("play").addEventListener("click",()=>{if(tick>=maxTick)tick=0;setPlaying(!playing);render();});$("restart").addEventListener("click",()=>{tick=0;setPlaying(false);render();});$("scrub").addEventListener("input",event=>{tick=Number(event.target.value);setPlaying(false);render();});$("view").addEventListener("change",()=>{renderedHistory="";render();});
$("map").addEventListener("click",event=>{if($("view").value!=="observer")return;const envelope=latest(data.observations,Math.floor(tick),"cutoff_tick");if(!envelope)return;const observation=envelope.observation,rect=$("map").getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width*(observation.size||32),y=(event.clientY-rect.top)/rect.height*(observation.size||32),reports=[...(observation.visible_opponents||[]),...(observation.last_seen||[])];const report=reports.find(bot=>Math.hypot(bot.x+.5-x,bot.y+.5-y)<.6);if(report?.evidence_id){inspect(report.evidence_id,true);render();}});
function frame(now){if(playing){if(lastFrame!==null){tick=Math.min(maxTick,tick+(now-lastFrame)/1000*hz*Number($("speed").value));render();if(tick>=maxTick)setPlaying(false);}lastFrame=now;}requestAnimationFrame(frame);}
$("restart").replaceChildren(icon("restart"),element("span","Restart"));$("close-evidence").replaceChildren(icon("close"));$("latest-assessment").append(icon("latest"));
$("close-evidence").addEventListener("click",()=>{selectedEvidence=null;inspectedVisible=null;document.querySelector(".evidence-panel").hidden=true;render();});
const usable=data.commands.filter(item=>item.command.type==="assessment"&&item.result.accepted),latestAssessment=usable[usable.length-1];$("latest-assessment").disabled=!latestAssessment;$("latest-assessment").addEventListener("click",()=>{if(latestAssessment)jumpTo(latestAssessment.result.application_tick);});
for(const forecast of data.forecasts){const button=element("button",(forecast.cutoff_tick/hz).toFixed(0)+"s");button.type="button";button.dataset.tick=forecast.cutoff_tick;const position=maxTick?forecast.cutoff_tick/maxTick:0;button.style.left="calc("+(position*100)+"% + "+(5-position*10)+"px)";button.setAttribute("aria-label","Jump to observation at "+(forecast.cutoff_tick/hz)+" seconds");button.addEventListener("click",()=>jumpTo(forecast.cutoff_tick));$("planning-rail").append(button);}
window.addEventListener("resize",render);setPlaying(false);render();document.fonts.ready.then(render);requestAnimationFrame(frame);
</script>
</body>
</html>
"""  # noqa: E501 - embedded HTML/CSS/JS has independent formatting.
