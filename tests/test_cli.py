import json
from fdt.cli import main
from conftest import FILES


def test_cli_build_inspect_and_run(tmp_path,capsys):
    db=tmp_path/'demo.sqlite';out=tmp_path/'output.json'
    assert main(['build','--csv',str(FILES[0]),'--db',str(db),'--out',str(out)])==0
    assert json.loads(out.read_text())['user_id']=='USR-DEMO-002'
    assert main(['inspect','--db',str(db),'--out',str(out)])==0
    assert main(['run','--db',str(db),'--mode','forecast','--paths','20','--horizon-days','7','--out',str(out)])==0
    assert json.loads(out.read_text())['mode']=='forecast'
    assert main(['run','--db',str(db),'--mode','goal'])==2
    assert 'SCHEMA_VALIDATION' in capsys.readouterr().out


def test_list_modes(capsys):
    assert main(['list-modes'])==0
    assert len(json.loads(capsys.readouterr().out)['modes'])==5


def test_missing_database_error(tmp_path,capsys):
    assert main(['inspect','--db',str(tmp_path/'missing.sqlite')])==2
    assert 'STORE_NOT_FOUND' in capsys.readouterr().out
