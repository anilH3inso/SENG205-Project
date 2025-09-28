# gen_routes_tools_mermaid.py
import re, sys, pathlib

APP_FILE = pathlib.Path("care_portal/api/app_bot.py")
if not APP_FILE.exists():
    sys.exit("Could not find care_portal/api/app_bot.py — run from repo root.")

text = APP_FILE.read_text(encoding="utf-8")

# FastAPI routes: @app.get("/path") def name(...
routes = re.findall(r'@app\.(get|post)\("([^"]+)"\)\s*def\s+([A-Za-z_]\w*)', text)

# Tools: @tool("name", r"...", "help") def fn(...
tools = re.findall(
    r'@tool\(\s*"([^"]+)"\s*,\s*r?(".*?")\s*,\s*"([^"]+)"\s*\)\s*def\s+([A-Za-z_]\w*)',
    text, re.DOTALL
)

out = []
out.append("flowchart TD")
out.append('    UI["Helpdesk Chat (Tkinter)"] -->|POST /ai/stream or /ai/chat| API["FastAPI app"]')

# Routes box
if routes:
    out.append('    subgraph Routes')
    for method, path, fn in routes:
        out.append(f'        API -->|{method.upper()} {path}| {fn}["{fn}()"]')
    out.append('    end')

# Tools router
out.append('    API -->|tools first| Router["route_tools()"]')

if tools:
    out.append('    subgraph Tools')
    for name, pattern, helptext, fn in tools:
        # shorten long patterns in label
        label = helptext.replace('"', '\\"')
        out.append(f'        Router -->|{name}| T_{fn}["{fn}()\\n{name}: {label}"]')
    out.append('    end')

out.append('    Router -->|no tool matched| LLM["TinyLlama\\nensure_llm() / llm_answer()"]')

mermaid = "\n".join(out)
pathlib.Path("routes_tools.mmd").write_text(mermaid, encoding="utf-8")
print("Wrote routes_tools.mmd")
