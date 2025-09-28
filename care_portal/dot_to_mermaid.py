# dot_to_mermaid.py
import sys, pydot, re

def to_id(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', s)

def dot_to_mermaid(dot_path: str, out_path: str, direction="TD"):
    graphs = pydot.graph_from_dot_file(dot_path)
    if not graphs:
        raise SystemExit("Could not parse DOT")
    g = graphs[0]

    lines = [f"flowchart {direction}"]
    nodes_done = set()

    # Nodes
    for node in g.get_nodes():
        name = node.get_name().strip('"')
        if name in ('node', 'graph', 'edge'):
            continue
        nid = to_id(name)
        label = (node.get_label() or name).strip('"')
        shape = (node.get_shape() or "").lower()
        # map common shapes
        if shape in ("diamond",):
            fmt = f'{nid}{{"{label}"}}'
        elif shape in ("ellipse", "oval", "circle"):
            fmt = f'{nid}("{label}")'
        else:
            fmt = f'{nid}["{label}"]'
        if nid not in nodes_done:
            lines.append(f"    {fmt}")
            nodes_done.add(nid)

    # Edges
    for edge in g.get_edges():
        src = to_id(edge.get_source().strip('"'))
        dst = to_id(edge.get_destination().strip('"'))
        lbl = edge.get_label()
        if lbl:
            lbl = lbl.strip('"')
            lines.append(f'    {src} -->|{lbl}| {dst}')
        else:
            lines.append(f'    {src} --> {dst}')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 dot_to_mermaid.py input.dot output.mmd [LR|TB|TD]")
        sys.exit(1)
    dot_to_mermaid(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "TD")
    print(f"Wrote {sys.argv[2]}")
