"""Tiny local HTTP front end for the shared advisor core."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .core import advise
from .feedback import append_feedback
from .storage import events, revise
from .labels import parse_rating


def dashboard_data():
    rows = events()
    from .config import config
    from .quota import budget, slopes
    from .storage import connect, pairs
    from .catalog import options
    from .features import CATEGORIES
    cfg = config()
    with connect() as db: comparisons = pairs(db)
    preferred = {p["a"] for p in comparisons if p["preference"] is not None}
    collection = {"collected":sum(r["source"]=="live" and not r["indicators"].get("shadow") and (r["rating"] is not None or r["id"] in preferred) for r in rows),"target":cfg["collection_target"]}
    coverage = [{"model":o["id"],"effort":o["effort"],"category":c,"count":sum(r["model"]==o["id"] and r["engine"]==o["engine"] and r["effort"]==o["effort"] and r["features"]["category"]==c and r["source"]=="live" for r in rows)} for o in options() for c in CATEGORIES]
    models = {}
    for row in rows:
        stat = models.setdefault(row["model"], {"turns": 0, "rated": 0, "good": 0, "spend": 0., "weak_reward_sum": 0.})
        stat["turns"] += 1
        stat["spend"] += row["indicators"].get("cost_usd", 0)
        stat["weak_reward_sum"] += row["label"]["reward"]
        if row["rating"] is not None:
            stat["rated"] += 1; stat["good"] += row["rating"]
    for stat in models.values():
        stat["rated_pass_rate"] = stat["good"]/stat["rated"] if stat["rated"] else None
    picks = [r for r in rows if r["source"] == "live" and not r["indicators"].get("manual_override") and r["rating"] is not None]
    return {"collection":collection,"coverage":coverage,"quota":{e:budget(rows,cfg,e) for e in ("codex","claude")},"quota_slopes":slopes(rows),"pairs":comparisons,"recent": list(reversed(rows[-60:])), "calibration": [r for r in rows if r["source"] == "backlog"][-20:],
            "stats": {"models": models, "spend_usd": sum(s["spend"] for s in models.values()), "rated_router_picks": len(picks), "good": sum(r["rating"] == 1 for r in picks), "overkill": sum("o" in r["tags"] for r in picks), "underpowered": sum("u" in r["tags"] for r in picks)}}


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Task advisor</title><style>
body{font:16px system-ui,sans-serif;max-width:780px;margin:40px auto;padding:0 18px;color:#17202a;background:#f6f7f9}
h1{margin-bottom:4px}textarea{width:100%;min-height:150px;box-sizing:border-box;padding:12px}select,input,button{padding:9px;margin:5px 5px 5px 0}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px;border-bottom:1px solid #dfe3e8}pre{white-space:pre-wrap;font-size:13px}button{background:#1f62d0;color:white;border:0;border-radius:5px;cursor:pointer}.card{background:white;border:1px solid #dfe3e8;border-radius:8px;padding:18px;margin-top:18px;box-shadow:0 2px 8px #0001}.hidden{display:none}.tiers{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.tier{background:#f1f4f8;padding:10px;border-radius:5px}.muted{color:#647080;font-size:14px}label{display:block;margin-top:12px}
@media(max-width:600px){.tiers{grid-template-columns:1fr}}
</style></head><body><h1>Task advisor</h1><p class="muted">Local prediction from graded trials; no model call is made.</p>
<form id="advice"><label for="task">What should the AI do?</label><textarea id="task" required></textarea>
<label for="stakes">Stakes</label><select id="stakes"><option>low</option><option selected>medium</option><option>high</option></select>
<button>Advise</button></form><section id="result" class="card hidden"></section>
<section id="feedback" class="card hidden"><h2>Report the outcome</h2><form id="outcome">
<label>What did you actually use?</label><input id="used" placeholder="mid tier, coding agent" required>
<label>Outcome</label><select id="status"><option>pass</option><option>partial</option><option>fail</option></select>
<label>Minutes spent fixing</label><input id="minutes" type="number" min="0" step="0.1" value="0" required>
<label>Actual cost (optional USD)</label><input id="cost" type="number" min="0" step="0.000001">
<button>Save outcome</button></form><p id="saved" class="muted"></p></section>
<script>
let adviceId=null;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const byId=id=>document.getElementById(id), resultEl=byId('result'), feedbackEl=byId('feedback');
document.querySelector('#advice').onsubmit=async e=>{e.preventDefault();let response=await fetch('/api/advise',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({task_text:byId('task').value,stakes:byId('stakes').value})});let a=await response.json();if(!response.ok){resultEl.textContent=a.error;resultEl.classList.remove('hidden');return}adviceId=a.advice_id;let tiers=a.tiers.map(t=>`<div class="tier"><b>${esc(t.model_tier)}</b> / ${esc(t.reasoning_effort)}<br>${Math.round(t.predicted_pass_chance*100)}% pass<br>$${t.rough_cost_usd.toFixed(6)}</div>`).join('');resultEl.innerHTML=`<h2>${a.complexity.score}/100 · ${esc(a.complexity.label)}</h2><p>${a.complexity.reasons.map(esc).join(' · ')}</p><p><b>Use:</b> ${esc(a.recommendation.model_tier)} tier / ${esc(a.recommendation.reasoning_effort)} effort<br><b>Setup:</b> ${esc(a.agent_setup)}<br><b>Verify:</b> ${esc(a.verification)}<br><b>Confidence:</b> ${esc(a.confidence.flag)}</p><div class="tiers">${tiers}</div>`;resultEl.classList.remove('hidden');feedbackEl.classList.remove('hidden')};
document.querySelector('#outcome').onsubmit=async e=>{e.preventDefault();let body={advice_id:adviceId,what_user_used:byId('used').value,outcome:byId('status').value,minutes_spent_fixing:Number(byId('minutes').value),cost_usd:byId('cost').value===''?null:Number(byId('cost').value)};let response=await fetch('/api/feedback',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});let data=await response.json();byId('saved').textContent=response.ok?'Outcome saved locally.':data.error};
</script></body></html>"""


DASHBOARD = r"""<section class="card"><h2>Collection progress</h2><p id="collection"></p><h3>Quota</h3><div id="quota"></div><h3>Coverage: model / effort / category</h3><div style="overflow:auto"><table id="coverage"></table></div></section><section class="card"><h2>Live turns</h2><p>Features and outcomes only. Ratings can be changed at any time.</p><pre id="stats"></pre><div id="feed"></div></section>
<section class="card"><h2>Optional calibration</h2><p>20 past turns, shown as feature summaries only. No text is retained. Skip any you cannot recognize.</p><button onclick="document.getElementById('calibration').hidden=true">Skip calibration</button><div id="calibration"></div></section>
<script>
async function rateTurn(id,rating){let r=await fetch('/api/rate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,rating})});if(!r.ok)alert('Rating was not saved');await refreshFeed()}
function turnCard(r){let d=document.createElement('div');d.className='card';let p=document.createElement('p');p.textContent=`${r.time} | ${r.model} / ${r.effort} | ${r.features.length_bucket} ${r.features.category} | ${r.label.source} ${Math.round(r.label.reward*100)}% | $${(r.indicators.cost_usd||0).toFixed(6)} | ${r.rating===null?'unrated':r.rating?'good':'bad'} ${r.tags.join('')}`;d.append(p);
for(let [label,value] of [['👍','+'],['👎','-'],['Clear','']]){let b=document.createElement('button');b.textContent=label;b.onclick=()=>rateTurn(r.id,value);d.append(b)}
for(let [tag,label] of Object.entries({v:'Verification',o:'Overkill',u:'Underpowered',w:'Wrong',p:'Partial',s:'Slow',c:'Costly'})){let b=document.createElement('button');b.textContent=label+(r.tags.includes(tag)?' ✓':'');b.onclick=()=>{let tags=new Set(r.tags);tags.has(tag)?tags.delete(tag):tags.add(tag);rateTurn(r.id,(r.rating===1?'+':'-')+[...tags].join(''))};d.append(b)}return d}
async function refreshFeed(){try{let response=await fetch('/api/turns',{cache:'no-store'});if(!response.ok)return;let data=await response.json();document.getElementById('stats').textContent=JSON.stringify(data.stats,null,2);document.getElementById('collection').textContent=`collected ${data.collection.collected}/${data.collection.target}`;document.getElementById('quota').innerHTML=Object.entries(data.quota).map(([engine,q])=>`<p><b>${esc(engine)}</b> | ${q.paused?'Exploration paused':'Exploration available'}<br>$${q.spend.toFixed(3)} estimated daily spend; ${q.quota.toFixed(2)} quota points used today<br>${Object.entries(q.account.windows||{}).map(([name,w])=>`${esc(name)}: ${(100-(w.quota_used_pct||0)).toFixed(1)}% left, resets in ${(w.minutes_to_reset||0).toFixed(0)} minutes`).join('<br>')||'Remaining quota and reset time unknown'}</p>`).join('');let grid=new Map();for(let r of data.coverage){let key=r.model+' / '+r.effort;if(!grid.has(key))grid.set(key,{});grid.get(key)[r.category]=r.count}let categories=['code','math','research','writing','general'];document.getElementById('coverage').innerHTML='<tr><th>Model / effort</th>'+categories.map(c=>`<th>${c}</th>`).join('')+'</tr>'+[...grid].map(([key,counts])=>`<tr><td>${esc(key)}</td>`+categories.map(c=>`<td>${counts[c]||0}</td>`).join('')+'</tr>').join('');for(let [id,rows] of [['feed',data.recent],['calibration',data.calibration]])document.getElementById(id).replaceChildren(...rows.map(turnCard))}catch(e){document.getElementById('stats').textContent='Dashboard disconnected; retrying…'}}
refreshFeed();setInterval(refreshFeed,2000);
</script>"""
HTML = HTML.replace("</body>", DASHBOARD + "</body>")


def extension_event(payload):
    from .features import EXTRA_KEYS
    from .storage import make_event, add
    from .catalog import models
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), dict) or not isinstance(payload.get("recommendation"), dict):
        raise ValueError("invalid event schema")
    keys = EXTRA_KEYS | {"length_bucket", "code", "math", "multistep", "question", "category", "stakes", "base_predictions", "turn_index"}
    engine = {"claude-web": "claude"}.get(payload["engine"], payload["engine"])
    if engine not in {"chatgpt", "claude"}: raise ValueError("invalid engine")
    if type(payload["rating"]) is not int or payload["rating"] not in (0, 1): raise ValueError("invalid rating")
    tags = payload["tags"]
    if not isinstance(tags, list) or any(not isinstance(t, str) or t not in tuple("vouwpsc") for t in tags): raise ValueError("invalid tags")
    features = {k: v for k, v in payload["features"].items() if k in keys}
    rec = payload["recommendation"]
    catalog_engine = "codex" if engine == "chatgpt" else "claude"
    if not any(m["engine"] == catalog_engine and m["id"] == rec["id"] and rec["effort"] in m["efforts"] for m in models()): raise ValueError("invalid recommendation")
    # Existing schema: chosen recommendation is model/effort/predicted; the
    # observed site model is a catalog enum in previous_model, never page text.
    site_model = payload.get("siteModel", "unknown")
    features["previous_model"] = site_model if site_model in {m["id"] for m in models()} else "unknown"
    event = make_event(features, engine, rec["id"], rec["effort"], source="live", predicted=rec["predicted"])
    event.update(rating=payload["rating"], tags=tags)
    return add(event)  # add filters again and rebuilds policy in the transaction.


class AdvisorHandler(BaseHTTPRequestHandler):
    def _extension(self):
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"} and origin.startswith(("chrome-extension://", "moz-extension://"))

    def do_OPTIONS(self):
        if self.path != "/api/ext/event" or not self._extension():
            self._json(403, {"error": "extension origin required"}); return
        self._json(200, {})

    def _local(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (origin is None or origin in {"http://" + h for h in allowed})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if self.path == "/api/ext/event" and self._extension():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._local():
            self._json(403, {"error": "local origin required"}); return
        if self.path == "/api/turns":
            self._json(200, dashboard_data()); return
        if self.path != "/":
            self.send_error(404)
            return
        encoded = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        allowed = self._extension() if self.path == "/api/ext/event" else self._local()
        if not allowed or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(403, {"error": "local JSON request required"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1000000: raise ValueError("invalid request length")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/api/ext/event":
                result = extension_event(payload)
            elif self.path == "/api/rate":
                result = revise(payload["id"], rating=parse_rating(payload["rating"]))
            elif self.path == "/api/advise":
                result = advise(payload["task_text"], payload.get("stakes", "medium"))
            elif self.path == "/api/feedback":
                result = append_feedback(
                    payload["advice_id"], payload["what_user_used"], payload["outcome"],
                    payload["minutes_spent_fixing"], payload.get("cost_usd"),
                )
            else:
                self.send_error(404)
                return
            self._json(200, result)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "Invalid request fields"})

    def log_message(self, format: str, *args: object) -> None:
        pass  # URLs and request bodies must never become persistent access logs.


def serve(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), AdvisorHandler)
    print(f"Task advisor: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

