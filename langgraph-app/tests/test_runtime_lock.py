from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_runtime_lock_rejects_second_process_and_releases(tmp_path):
    from lg_assistant.runtime import single_instance
    probe = "from pathlib import Path; from lg_assistant.runtime import single_instance; import sys;\nwith single_instance(Path(sys.argv[1])): print('acquired')"
    cwd = Path(__file__).resolve().parents[1]
    with single_instance(tmp_path):
        held = subprocess.run([sys.executable, "-c", probe, str(tmp_path)], cwd=cwd, capture_output=True, text=True)
        assert held.returncode != 0
        assert "RuntimeError" in held.stderr
    released = subprocess.run([sys.executable, "-c", probe, str(tmp_path)], cwd=cwd, capture_output=True, text=True)
    assert released.returncode == 0, released.stderr
    assert "acquired" in released.stdout
