import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_MENTION_RE = re.compile(r"@([A-Za-z0-9_.\-]+)")


def build_graph(dirs: list[Path]) -> dict[str, Any]:
    """Build a user interaction graph from one or more channel message dirs.

    Reads each messages.json once, buffering (sender, text) pairs for the
    mention scan which runs after all users are known. Standalone thread dumps
    under thread_*/thread.json are also collected in the same pass.
    """
    edges: dict[tuple[str, str], int] = defaultdict(int)
    user_messages: dict[str, int] = defaultdict(int)
    user_replies: dict[str, int] = defaultdict(int)
    channels = []
    # Buffer (sender, text) pairs for mention scan after all users known
    msg_texts: list[tuple[str, str]] = []

    for d in dirs:
        if not d.is_dir():
            continue
        msg_file = d / "messages.json"
        if msg_file.exists():
            channels.append(d.name)
            messages = json.loads(msg_file.read_text())
            for msg in messages:
                sender = msg.get("user_name") or "unknown"
                if sender != "unknown":
                    user_messages[sender] += 1
                    msg_texts.append((sender, msg.get("text", "")))
                for reply in msg.get("thread", []):
                    replier = reply.get("user_name") or "unknown"
                    if replier == "unknown":
                        continue
                    user_replies[replier] += 1
                    if replier != sender:
                        edges[(replier, sender)] += 1
                    msg_texts.append((replier, reply.get("text", "")))

        # standalone thread dumps (thread_<ts>/thread.json)
        for thread_dir in d.iterdir():
            if not thread_dir.is_dir() or not thread_dir.name.startswith("thread_"):
                continue
            tf = thread_dir / "thread.json"
            if not tf.exists():
                continue
            for r in json.loads(tf.read_text()):
                replier = r.get("user_name") or "unknown"
                if replier != "unknown":
                    user_replies[replier] += 1
                    msg_texts.append((replier, r.get("text", "")))

    all_users = (frozenset(user_messages) | frozenset(user_replies)) - {"unknown"}

    # Mention scan: regex extracts @word candidates, set lookup confirms known user
    for sender, text in msg_texts:
        if "@" not in text or sender == "unknown":
            continue
        for raw in _MENTION_RE.findall(text):
            for candidate in _mention_candidates(raw):
                if candidate in all_users and candidate != sender:
                    edges[(sender, candidate)] += 1
                    break

    nodes = [
        {"id": u, "messages": user_messages[u], "replies": user_replies[u]}
        for u in sorted(all_users)
    ]
    links = [
        {"source": src, "target": dst, "value": count}
        for (src, dst), count in edges.items()
        if src in all_users and dst in all_users
    ]
    return {"nodes": nodes, "links": links, "channels": channels}


def _mention_candidates(raw: str) -> list[str]:
    """Strip trailing punctuation from a @mention token to find the real name."""
    candidates = []
    s = raw
    while s:
        candidates.append(s)
        if s[-1] in ".,!?:;)\"'":
            s = s[:-1]
        else:
            break
    return candidates


def render_html(graph: dict[str, Any], title: str = "Communication Graph") -> str:
    data_json = json.dumps(graph).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    channels_str = ", ".join(graph["channels"]) if graph["channels"] else "unknown"
    n_nodes = len(graph["nodes"])
    n_links = len(graph["links"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; overflow: hidden; }}
#sidebar {{ position: fixed; top: 16px; left: 16px; background: rgba(22,27,34,0.94); border: 1px solid #30363d; border-radius: 10px; padding: 16px 18px; font-size: 13px; line-height: 1.6; width: 230px; z-index: 10; }}
#sidebar h2 {{ font-size: 14px; color: #e6edf3; margin-bottom: 4px; }}
#sidebar .meta {{ color: #8b949e; font-size: 11px; margin-bottom: 12px; }}
.btn-group {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
.btn {{ background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 5px 10px; border-radius: 6px; font-size: 12px; cursor: pointer; transition: background 0.15s, border-color 0.15s; }}
.btn:hover {{ background: #30363d; border-color: #58a6ff; }}
.btn.active {{ background: #1f6feb; border-color: #58a6ff; color: #fff; }}
.legend-title {{ font-size: 11px; color: #6e7681; margin-bottom: 6px; }}
.legend-bar {{ height: 10px; border-radius: 5px; background: linear-gradient(to right, #0e1a8e, #2563eb, #06b6d4, #10b981, #f59e0b, #ef4444); margin-bottom: 4px; }}
.legend-labels {{ display: flex; justify-content: space-between; font-size: 10px; color: #6e7681; }}
.hint {{ margin-top: 10px; font-size: 11px; color: #6e7681; }}
#tooltip {{ position: fixed; background: rgba(22,27,34,0.97); border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px; font-size: 12px; line-height: 1.8; pointer-events: none; display: none; max-width: 200px; z-index: 20; }}
#tooltip strong {{ color: #e6edf3; font-size: 13px; display: block; margin-bottom: 2px; }}
svg {{ width: 100vw; height: 100vh; cursor: grab; }}
svg:active {{ cursor: grabbing; }}
.link {{ stroke-opacity: 0.3; }}
.node text {{ pointer-events: none; fill: #8b949e; }}
</style>
</head>
<body>
<div id="sidebar">
  <h2>&#9752; Communication Graph</h2>
  <div class="meta">{channels_str}</div>
  <div style="margin-bottom:12px">{n_nodes} users &nbsp;·&nbsp; {n_links} connections</div>

  <div class="btn-group">
    <button class="btn active" id="btn-force" onclick="setLayout('force')">&#9678; Force</button>
    <button class="btn" id="btn-circle" onclick="setLayout('circle')">&#9711; Circle</button>
    <button class="btn" id="btn-grid" onclick="setLayout('grid')">&#9783; Grid</button>
  </div>

  <div class="legend-title">Activity level</div>
  <div class="legend-bar"></div>
  <div class="legend-labels"><span>low</span><span>high</span></div>
  <div class="hint">Drag &nbsp;·&nbsp; Scroll to zoom &nbsp;·&nbsp; Click to highlight</div>
</div>
<div id="tooltip"></div>
<svg id="graph"></svg>

<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js" integrity="sha384-CjloA8y00+1SDAUkjs099PVfnY2KmDC2BZnws9kh8D/lX1s46w6EPhpXdqMfjK6i" crossorigin="anonymous"></script>
<script>
const raw = {data_json};
const nodes = raw.nodes.map(d => ({{...d}}));
const links = raw.links.map(d => ({{...d}}));

const W = window.innerWidth, H = window.innerHeight;
const tooltip = document.getElementById("tooltip");

// ── Rank-based color scale ──────────────────────────────────────────────────
// Sort by activity so each user gets a distinct percentile color,
// regardless of the absolute value distribution.
const sorted = [...nodes].sort((a,b) => (a.messages+a.replies) - (b.messages+b.replies));
const rankMap = new Map(sorted.map((d,i) => [d.id, nodes.length > 1 ? i/(nodes.length-1) : 0.5]));
const palette = t => d3.interpolateRgbBasis(["#0e1a8e","#2563eb","#06b6d4","#10b981","#f59e0b","#ef4444"])(t);
const colorOf = d => palette(rankMap.get(d.id) ?? 0.5);

// ── Node sizing ─────────────────────────────────────────────────────────────
const maxActivity = d3.max(nodes, d => d.messages + d.replies) || 1;
const rScale = d3.scaleSqrt().domain([0, maxActivity]).range([5, 26]);
const r = d => rScale(d.messages + d.replies);

// ── Link width ──────────────────────────────────────────────────────────────
const maxVal = d3.max(links, d => d.value) || 1;
const lw = d => Math.max(0.8, Math.min(6, 0.8 + 5 * d.value / maxVal));

// ── SVG setup ───────────────────────────────────────────────────────────────
const d3svg = d3.select("#graph");
const g = d3svg.append("g");
const zoom = d3.zoom().scaleExtent([0.05, 12]).on("zoom", e => g.attr("transform", e.transform));
d3svg.call(zoom);

// ── Simulation ──────────────────────────────────────────────────────────────
const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(d => 50 + 60 / Math.sqrt(d.value || 1)))
  .force("charge", d3.forceManyBody().strength(d => -120 - r(d) * 5))
  .force("center", d3.forceCenter(W/2, H/2).strength(0.08))
  .force("x", d3.forceX(W/2).strength(0.06))
  .force("y", d3.forceY(H/2).strength(0.06))
  .force("collision", d3.forceCollide().radius(d => r(d) + 3));

// ── Render ───────────────────────────────────────────────────────────────────
const link = g.append("g").selectAll("line")
  .data(links).join("line")
  .attr("class","link")
  .attr("stroke","#4b5563")
  .attr("stroke-width", d => lw(d));

const node = g.append("g").selectAll("g")
  .data(nodes).join("g")
  .attr("class","node")
  .call(d3.drag()
    .on("start", (e,d) => {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
    .on("drag",  (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
    .on("end",   (e,d) => {{ if (!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}));

node.append("circle")
  .attr("r", d => r(d))
  .attr("fill", d => colorOf(d))
  .attr("stroke", "#0d1117")
  .attr("stroke-width", 1.5);

node.append("text")
  .attr("dy", d => r(d) + 11)
  .attr("text-anchor","middle")
  .attr("font-size", d => Math.min(12, Math.max(8, r(d) * 0.8)) + "px")
  .text(d => d.id);

const PAD = 200;
sim.on("tick", () => {{
  // Clamp so isolated nodes can't escape arbitrarily far
  nodes.forEach(d => {{
    d.x = Math.max(-PAD, Math.min(W+PAD, d.x));
    d.y = Math.max(-PAD, Math.min(H+PAD, d.y));
  }});
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
}});

// ── Highlight on click ───────────────────────────────────────────────────────
let selected = null;
node.on("click", (e,d) => {{
  e.stopPropagation();
  if (selected === d.id) {{ clearHighlight(); return; }}
  selected = d.id;
  const nbrs = new Set([d.id]);
  links.forEach(l => {{
    const s = l.source.id ?? l.source, t = l.target.id ?? l.target;
    if (s===d.id) nbrs.add(t);
    if (t===d.id) nbrs.add(s);
  }});
  node.select("circle").style("opacity", n => nbrs.has(n.id) ? 1 : 0.1);
  node.select("text").style("opacity", n => nbrs.has(n.id) ? 1 : 0.15);
  link.style("opacity", l => {{
    const s=l.source.id??l.source, t=l.target.id??l.target;
    return (s===d.id||t===d.id) ? 1 : 0.04;
  }}).attr("stroke", l => {{
    const s=l.source.id??l.source, t=l.target.id??l.target;
    return (s===d.id||t===d.id) ? "#58a6ff" : "#4b5563";
  }});
}});
function clearHighlight() {{
  selected = null;
  node.select("circle").style("opacity",1);
  node.select("text").style("opacity",1);
  link.style("opacity",1).attr("stroke","#4b5563");
}}
d3svg.on("click", clearHighlight);

// ── Tooltip ──────────────────────────────────────────────────────────────────
node.on("mousemove", (e,d) => {{
  const out = links.filter(l=>(l.source.id??l.source)===d.id).length;
  const inn = links.filter(l=>(l.target.id??l.target)===d.id).length;
  tooltip.innerHTML = `<strong>${{d.id}}</strong>Messages: ${{d.messages}}<br>Replies: ${{d.replies}}<br>Talks to: ${{out}} users<br>Talked to by: ${{inn}} users`;
  tooltip.style.display="block";
  tooltip.style.left=(e.clientX+16)+"px";
  tooltip.style.top=(e.clientY-10)+"px";
}}).on("mouseleave", ()=>{{ tooltip.style.display="none"; }});

// ── Layout buttons ───────────────────────────────────────────────────────────
const LAYOUTS = ["force","circle","grid"];
function setLayout(mode) {{
  LAYOUTS.forEach(l => document.getElementById("btn-"+l).classList.toggle("active", l===mode));
  sim.stop();
  if (mode==="force") {{
    nodes.forEach(d => {{ d.fx=null; d.fy=null; }});
    sim.alpha(0.5).restart();
    return;
  }}
  const cx=W/2, cy=H/2;
  if (mode==="circle") {{
    const n=nodes.length, R=Math.min(W,H)*0.38;
    nodes.forEach((d,i) => {{ d.fx=cx+R*Math.cos(2*Math.PI*i/n-Math.PI/2); d.fy=cy+R*Math.sin(2*Math.PI*i/n-Math.PI/2); }});
  }}
  if (mode==="grid") {{
    const cols=Math.ceil(Math.sqrt(nodes.length)), spacing=Math.min(W,H)*0.85/cols;
    nodes.forEach((d,i) => {{ d.fx=cx+(i%cols-(cols-1)/2)*spacing; d.fy=cy+(Math.floor(i/cols)-(Math.floor((nodes.length-1)/cols))/2)*spacing; }});
  }}
  sim.alpha(0.1).restart();
}}
</script>
</body>
</html>"""
