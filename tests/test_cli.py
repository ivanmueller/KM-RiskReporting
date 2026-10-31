from riskparse.cli import main


def test_config_check_default(capsys):
    assert main(["config-check"]) == 0
    assert "sqlite" in capsys.readouterr().out


def test_config_refusal_exit_code(tmp_path, capsys):
    p = tmp_path / "bad.toml"
    p.write_text('[environment]\nname="prod"\n[database]\nbackend="sqlite"\n')
    assert main(["--config", str(p), "config-check"]) == 3
    assert "refused" in capsys.readouterr().err


def test_db_upgrade_register_and_verify(tmp_path, capsys):
    cfg = tmp_path / "s.toml"
    cfg.write_text(f'[environment]\nname="test"\n[database]\nbackend="sqlite"\n'
                   f'sqlite_path="{(tmp_path / "c.db").as_posix()}"\n'
                   f'[paths]\narchive_root="{(tmp_path / "arc").as_posix()}"\n')
    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"synthetic")
    assert main(["--config", str(cfg), "db-upgrade"]) == 0
    assert main(["--config", str(cfg), "register-document", str(doc), "--actor", "t"]) == 0
    assert main(["--config", str(cfg), "audit-verify"]) == 0
    assert "NEW" in capsys.readouterr().out


def test_inventory_template_and_validate(tmp_path):
    assert main(["inventory-template", "--out", str(tmp_path), "--csv"]) == 0
    x = tmp_path / "October_2026_Source_Inventory_TEMPLATE.xlsx"
    assert main(["inventory-validate", str(x)]) == 0
    assert main(["inventory-validate", str(x), "--strict"]) == 2


def test_demo_smoke_cli(tmp_path):
    assert main(["demo-smoke", "--workdir", str(tmp_path / "demo")]) == 0


def test_inventory_validate_rejects_csv_cleanly(tmp_path, capsys):
    p = tmp_path / "m.csv"
    p.write_text("a,b\n")
    assert main(["inventory-validate", str(p)]) == 1
    assert "workbook" in capsys.readouterr().err
