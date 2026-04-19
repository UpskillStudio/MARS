import asyncio
import queue
import threading
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="MARS — Multi-Agent Research System",
    page_icon="🔭",
    layout="wide",
)

st.title("🔭 MARS — Multi-Agent Research System")
st.caption("Hub-and-spoke multi-agent pipeline · Claude + Tavily")

# ---------------------------------------------------------------------------
# Sidebar — settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")

    adaptive = st.toggle(
        "Adaptive search (ReAct)",
        value=False,
        help="OFF: Tavily direct search — fast, cheap. ON: Claude-guided ReAct loop — deeper, higher cost.",
    )

    max_domains = st.slider(
        "Max sub-domains",
        min_value=2,
        max_value=9,
        value=5,
        help="Fewer domains = faster + cheaper. Use 2-3 for quick tests.",
    )

    uploaded_docs = st.file_uploader(
        "Upload documents (optional)",
        type=["txt", "md", "pdf"],
        accept_multiple_files=True,
    )

    st.divider()
    st.caption("Langfuse observability: set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` to enable tracing.")

# ---------------------------------------------------------------------------
# Main — topic input
# ---------------------------------------------------------------------------
topic = st.text_input(
    "Research topic",
    placeholder="e.g. impact of AI on creative industries",
)

run_btn = st.button("Run Research", type="primary", disabled=not topic.strip())

if not run_btn:
    st.stop()

# ---------------------------------------------------------------------------
# Save uploaded docs to temp files
# ---------------------------------------------------------------------------
doc_paths: list[str] = []
if uploaded_docs:
    tmp_dir = Path("output/uploads")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for f in uploaded_docs:
        p = tmp_dir / f.name
        p.write_bytes(f.read())
        doc_paths.append(str(p))

# ---------------------------------------------------------------------------
# Patched coordinator that streams progress via a queue
# ---------------------------------------------------------------------------
progress_q: queue.Queue = queue.Queue()


def _streamed_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    progress_q.put(("log", msg))


def _run_in_thread(result_holder: list):
    """Run the async coordinator in a background thread."""
    import builtins
    builtins.print = _streamed_print          # redirect all print() calls

    async def _run():
        from mars.coordinator import Coordinator
        coordinator = Coordinator(
            max_concurrency=1,
            adaptive_search=adaptive,
            max_domains=max_domains,
        )
        report = await coordinator.run(topic=topic, doc_paths=doc_paths)
        out = Path("output/report.md")
        out.parent.mkdir(exist_ok=True)
        out.write_text(report, encoding="utf-8")
        result_holder.append(report)
        progress_q.put(("done", report))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Run + stream progress
# ---------------------------------------------------------------------------
result_holder: list[str] = []
thread = threading.Thread(target=_run_in_thread, args=(result_holder,), daemon=True)
thread.start()

st.subheader("Progress")
log_area = st.empty()
logs: list[str] = []

report_placeholder = st.empty()

while thread.is_alive() or not progress_q.empty():
    try:
        kind, payload = progress_q.get(timeout=0.3)
        if kind == "log":
            logs.append(payload)
            log_area.code("\n".join(logs), language=None)
        elif kind == "done":
            break
    except queue.Empty:
        continue

thread.join()

# ---------------------------------------------------------------------------
# Render report
# ---------------------------------------------------------------------------
if result_holder:
    st.divider()
    st.subheader("Report")

    col1, col2 = st.columns([6, 1])
    with col2:
        st.download_button(
            "⬇ Download",
            data=result_holder[0],
            file_name="report.md",
            mime="text/markdown",
        )

    report_placeholder.markdown(result_holder[0])
else:
    st.error("Research run failed. Check the progress log above.")
