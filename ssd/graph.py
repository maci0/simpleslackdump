import json
import re
from collections import defaultdict
from pathlib import Path


def build_graph(dirs: list[Path]) -> dict:
    """Build a user interaction graph from one or more channel message dirs.

    Returns:
        {
            "nodes": [{"id": str, "messages": int, "replies": int}],
            "links": [{"source": str, "target": str, "value": int}],
            "channels": [str],  # channel names included
        }
    """
    # edges[(src, dst)] = count
    edges: dict[tuple[str, str], int] = defaultdict(int)
    user_messages: dict[str, int] = defaultdict(int)
    user_replies: dict[str, int] = defaultdict(int)
    channels = []

    for d in dirs:
        msg_file = d / "messages.json"
        if not msg_file.exists():
            continue
        channels.append(d.name)
        messages = json.loads(msg_file.read_text())
        for msg in messages:
            sender = msg.get("user_name") or "unknown"
            if sender == "unknown":
                continue
            user_messages[sender] += 1

            # mentions in top-level message
            for mentioned in _extract_mentions(msg.get("text", "")):
                if mentioned != sender:
                    edges[(sender, mentioned)] += 1

            # thread replies
            for reply in msg.get("thread", []):
                replier = reply.get("user_name") or "unknown"
                if replier == "unknown":
                    continue
                user_replies[replier] += 1
                # reply → thread starter
                if replier != sender:
                    edges[(replier, sender)] += 1
                # mentions in reply
                for mentioned in _extract_mentions(reply.get("text", "")):
                    if mentioned != replier:
                        edges[(replier, mentioned)] += 1

    all_users = set(user_messages) | set(user_replies)
    nodes = [
        {"id": u, "messages": user_messages[u], "replies": user_replies[u]}
        for u in sorted(all_users)
    ]
    links = [
        {"source": src, "target": dst, "value": count}
        for (src, dst), count in edges.items()
        if src in all_users and dst in all_users  # skip mentions of non-participants
    ]
    return {"nodes": nodes, "links": links, "channels": channels}


def _extract_mentions(text: str) -> list[str]:
    return re.findall(r"@(\S+)", text)


def render_html(graph: dict, title: str = "Communication Graph") -> str:
    import json as _json
    data_json = _json.dumps(graph)
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
  #info {{ position: fixed; top: 16px; left: 16px; background: rgba(22,27,34,0.92); border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; font-size: 13px; line-height: 1.6; max-width: 260px; }}
  #info h2 {{ font-size: 15px; color: #e6edf3; margin-bottom: 6px; }}
  #info .meta {{ color: #8b949e; font-size: 12px; margin-bottom: 10px; }}
  #info .legend {{ margin-top: 10px; }}
  #info .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 12px; color: #8b949e; }}
  #info .dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  #tooltip {{ position: fixed; background: rgba(22,27,34,0.95); border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; font-size: 12px; line-height: 1.7; pointer-events: none; display: none; max-width: 220px; }}
  #tooltip strong {{ color: #e6edf3; font-size: 13px; }}
  svg {{ width: 100vw; height: 100vh; }}
  .link {{ stroke-opacity: 0.35; }}
  .node circle {{ cursor: pointer; transition: opacity 0.15s; }}
  .node text {{ pointer-events: none; font-size: 11px; fill: #8b949e; }}
</style>
</head>
<body>
<div id="info">
  <h2>&#128221; Communication Graph</h2>
  <div class="meta">{channels_str}</div>
  <div>{n_nodes} users &nbsp;·&nbsp; {n_links} connections</div>
  <div class="legend" style="margin-top:10px">
    <div style="font-size:11px;color:#8b949e;margin-bottom:4px">Node size = activity level</div>
    <div class="legend-row"><span class="dot" style="background:#1f6feb"></span> low activity</div>
    <div class="legend-row"><span class="dot" style="background:#388bfd"></span> medium</div>
    <div class="legend-row"><span class="dot" style="background:#f78166"></span> high activity</div>
  </div>
  <div style="margin-top:10px;font-size:11px;color:#6e7681">Drag · Scroll to zoom · Click to highlight</div>
</div>
<div id="tooltip"></div>
<svg id="graph"></svg>

<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js" integrity="sha384-CjloA8y00+1SDAUkjs099PVfnY2KmDC2BZnws9kh8D/lX1s46w6EPhpXdqMfjK6i" crossorigin="anonymous"></script>
<script>
const raw = {data_json};
const nodes = raw.nodes.map(d => ({{...d}}));
const links = raw.links.map(d => ({{...d}}));

const svg = d3.select("#svg").attr("id","graph").select(function(){{ return this; }});
const svgEl = document.getElementById("graph");
const W = window.innerWidth, H = window.innerHeight;
const tooltip = document.getElementById("tooltip");

const maxMsgs = d3.max(nodes, d => d.messages + d.replies) || 1;
const r = d => Math.max(5, Math.min(28, 5 + 22 * Math.sqrt((d.messages + d.replies) / maxMsgs)));

const colorScale = d3.scaleSequential()
  .domain([0, maxMsgs])
  .interpolator(d3.interpolateRgbBasis(["#1f6feb","#388bfd","#58a6ff","#ffa657","#f78166"]));

const maxLinkVal = d3.max(links, d => d.value) || 1;
const linkW = d => Math.max(0.5, Math.min(6, 0.5 + 5 * d.value / maxLinkVal));

const d3svg = d3.select("#graph");
const g = d3svg.append("g");

d3svg.call(d3.zoom().scaleExtent([0.1, 8]).on("zoom", e => g.attr("transform", e.transform)));

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(d => 60 + 80 / (d.value || 1)))
  .force("charge", d3.forceManyBody().strength(-220))
  .force("center", d3.forceCenter(W / 2, H / 2))
  .force("collision", d3.forceCollide().radius(d => r(d) + 4));

const link = g.append("g").selectAll("line")
  .data(links).join("line")
  .attr("class","link")
  .attr("stroke","#30363d")
  .attr("stroke-width", d => linkW(d));

const node = g.append("g").selectAll("g")
  .data(nodes).join("g")
  .attr("class","node")
  .call(d3.drag()
    .on("start", (e, d) => {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
    .on("drag", (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
    .on("end", (e, d) => {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }}));

node.append("circle")
  .attr("r", d => r(d))
  .attr("fill", d => colorScale(d.messages + d.replies))
  .attr("stroke", "#0d1117")
  .attr("stroke-width", 1.5);

node.append("text")
  .attr("dy", d => r(d) + 12)
  .attr("text-anchor","middle")
  .text(d => d.id);

// highlight on click
let selected = null;
node.on("click", (e, d) => {{
  e.stopPropagation();
  if (selected === d.id) {{
    selected = null;
    node.select("circle").style("opacity", 1);
    link.style("opacity", 1).attr("stroke","#30363d");
  }} else {{
    selected = d.id;
    const neighbors = new Set([d.id]);
    links.forEach(l => {{
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (s === d.id) neighbors.add(t);
      if (t === d.id) neighbors.add(s);
    }});
    node.select("circle").style("opacity", n => neighbors.has(n.id) ? 1 : 0.15);
    link.style("opacity", l => {{
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return (s === d.id || t === d.id) ? 1 : 0.05;
    }}).attr("stroke", l => {{
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return (s === d.id || t === d.id) ? "#58a6ff" : "#30363d";
    }});
  }}
}});
d3svg.on("click", () => {{
  selected = null;
  node.select("circle").style("opacity", 1);
  link.style("opacity", 1).attr("stroke","#30363d");
}});

// tooltip
node.on("mousemove", (e, d) => {{
  const total = d.messages + d.replies;
  const sent = links.filter(l => (typeof l.source === "object" ? l.source.id : l.source) === d.id).length;
  const recv = links.filter(l => (typeof l.target === "object" ? l.target.id : l.target) === d.id).length;
  tooltip.innerHTML = `<strong>${{d.id}}</strong><br>Messages: ${{d.messages}}<br>Replies: ${{d.replies}}<br>Mentions sent: ${{sent}}<br>Mentioned by: ${{recv}}`;
  tooltip.style.display = "block";
  tooltip.style.left = (e.clientX + 14) + "px";
  tooltip.style.top = (e.clientY - 10) + "px";
}}).on("mouseleave", () => {{ tooltip.style.display = "none"; }});

sim.on("tick", () => {{
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
}});
</script>
</body>
</html>"""
