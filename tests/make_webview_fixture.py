"""Write simulated snapshots for Wox's opt-in Windows WebView2 integration test."""

import json
import sys
from pathlib import Path

from src.dashboard import LABELS, Session, render, script_update


def fixture() -> dict:
    session = Session(state="running", dry_run=True)
    session.feed("➤ Developer tools")
    for index in range(35):
        session.feed(f"→ Simulated cache {index} (1 items, 1MB dry)")
    page = json.loads(render("clean", session, LABELS, {}).preview_data)["html"]
    steps = [
        """
        const area = document.getElementById('items');
        const p = area.querySelector('.item p');
        const animation = document.querySelector('.status-icon svg').getAnimations()[0];
        const range = document.createRange(); range.selectNodeContents(p);
        getSelection().removeAllRanges(); getSelection().addRange(range);
        area.dispatchEvent(new WheelEvent('wheel', {deltaY:-120}));
        area.scrollTop = 100; area.dispatchEvent(new Event('scroll'));
        window.testState = {area, p, animation, origin:performance.timeOrigin,
          selection:getSelection().toString(), time:animation.currentTime};
        """
    ]
    for index in range(20):
        session.feed(f"→ More simulated cache {index} (1 items, 2MB dry)")
        steps.append(json.loads(script_update(session, LABELS).preview_data)["script"])
    steps.append("""
        const t = window.testState;
        if (document.getElementById('items') !== t.area) throw Error('container replaced');
        if (t.area.scrollTop !== 100) throw Error('reader pulled to bottom: '+t.area.scrollTop);
        if (t.area.querySelector('.item p') !== t.p) throw Error('item replaced');
        if (getSelection().toString() !== t.selection) throw Error('selection lost');
        const animation = document.querySelector('.status-icon svg').getAnimations()[0];
        if (animation !== t.animation || animation.currentTime <= t.time) throw Error('animation restarted');
        if (performance.timeOrigin !== t.origin) throw Error('document navigated');
        t.area.dispatchEvent(new WheelEvent('wheel', {deltaY:120}));
        t.area.scrollTop=t.area.scrollHeight; t.area.dispatchEvent(new Event('scroll'));
        """)
    session.feed("→ Final simulated item (1 items, 3MB dry)")
    steps.append(json.loads(script_update(session, LABELS).preview_data)["script"])
    steps.append("""
        const a = window.testState.area;
        if (a.scrollHeight-a.clientHeight-a.scrollTop > 4) throw Error('follow did not resume');
        """)
    session.finish(0)
    steps.append(json.loads(script_update(session, LABELS).preview_data)["script"])
    steps.append("""
        if (document.body.className !== 'completed') throw Error('completion missing');
        if (performance.timeOrigin !== window.testState.origin) throw Error('completion navigated');
        """)
    return {"HTML": page, "Steps": steps}


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(fixture()), encoding="utf-8")
