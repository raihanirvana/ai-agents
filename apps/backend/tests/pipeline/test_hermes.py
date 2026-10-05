from pathlib import Path
import os
import pytest
from app.pipeline.hermes import HermesDriver


@pytest.mark.skipif(os.name != 'posix', reason='POSIX venv symlink')
def test_venv_interpreter_symlink_is_not_resolved_to_base_python(tmp_path):
    executable = tmp_path / 'base-python'
    executable.write_text('fixture')
    venv_python = tmp_path / 'venv' / 'bin' / 'python'
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(executable)
    driver = HermesDriver(venv_python, tmp_path / 'private', None)
    assert driver.python == venv_python.absolute()
    assert driver.python != venv_python.resolve()
