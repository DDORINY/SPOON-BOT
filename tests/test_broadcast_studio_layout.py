"""UI-only studio composition; existing security and data contracts stay intact."""
import subprocess
from pathlib import Path


def test_real_and_simulator_dom_layout():
    result=subprocess.run(['node','tests/studio_layout_ui_check.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_broadcast_assets_preserve_security_boundary():
    root=Path('src/spoon_local/web/static')
    html=(root/'index.html').read_text()
    assert '/static/studio.css' in html and '/static/studio.js' in html
    script=(root/'studio.js').read_text()
    for forbidden in ['innerHTML','getDisplayMedia','getUserMedia','fetch(','location.href','/auth/spoon/start']:
        assert forbidden not in script
    css=(root/'studio.css').read_text()
    assert not css.lstrip().startswith('<')
    assert 'min-width:0' in css and 'overflow:auto' in css
    assert '@media(max-width:1100px)' in css and '@media(max-width:760px)' in css


def test_simulator_and_automation_do_not_remount_managed_cards():
    result=subprocess.run(['node','tests/layout_ownership_ui_check.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_seoul_compact_time_rendering():
    result=subprocess.run(['node','tests/ui_time_check.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_immediate_automation_switch_save_failure_rollback():
    result=subprocess.run(['node','tests/automation_switch_ui_check.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
