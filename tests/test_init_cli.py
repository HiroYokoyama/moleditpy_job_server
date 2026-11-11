"""`init` writes a complete first configuration in one command."""

import sys

import pytest

from job_server.__main__ import main
from job_server.settings import Settings


@pytest.fixture
def init(tmp_path, monkeypatch, capsys):
    config = tmp_path / "settings.json"

    def run(*options):
        monkeypatch.setattr(sys, "argv", [
            "job-server", "init", "--config", str(config),
            "--data-dir", str(tmp_path / "data"), *options,
        ])
        code = main()
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    run.config = config
    return run


def lan(monkeypatch, *addresses):
    monkeypatch.setattr("job_server.listeners.available_lan_addresses", lambda: list(addresses))


def test_plain_init_stays_local_and_names_the_next_commands(init):
    code, output = init()
    assert code == 0
    settings = Settings.load(init.config)
    assert not settings.connection_urls and "Local only" in output
    assert f'create-admin --config "{init.config}"' in output


def test_tls_uses_the_only_private_address_and_creates_the_ca(init, monkeypatch):
    lan(monkeypatch, "192.168.1.20")
    code, output = init("--tls", "--tls-port", "9443", "--cpus", "3")
    assert code == 0, output
    settings = Settings.load(init.config)
    assert settings.lan_bind_address == "192.168.1.20" and settings.cpus == 3
    assert "https://192.168.1.20:9443" in output and "SHA-256 fingerprint" in output


def test_several_private_addresses_must_be_chosen_between(init, monkeypatch):
    lan(monkeypatch, "192.168.1.20", "10.0.0.5")
    code, output = init("--lan-http")
    assert code == 1 and "192.168.1.20, 10.0.0.5" in output and "--lan-address" in output
    assert not init.config.exists()
    code, output = init("--lan-http", "--lan-address", "10.0.0.5")
    assert code == 0 and "http://10.0.0.5:8766" in output


def test_an_address_without_a_listener_names_the_missing_option(init):
    code, output = init("--lan-address", "192.168.1.20")
    assert code == 1 and "--tls" in output and not init.config.exists()


def test_a_zero_budget_is_refused_rather_than_replaced(init):
    code, output = init("--cpus", "0")
    assert code == 1 and "cpus" in output and not init.config.exists()


def test_admin_is_created_in_the_same_command(init, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "synthetic-password")
    code, output = init("--admin", "admin")
    assert code == 0, output
    assert "Administrator admin created" in output and "create-admin" not in output
    from job_server.db import Database

    db = Database(Settings.load(init.config).root)
    try:
        assert db.one("SELECT 1 FROM users WHERE username='admin' AND role='admin'")
    finally:
        db.close()


def test_mistyped_admin_password_writes_nothing(init, monkeypatch):
    answers = iter(["synthetic-password", "different"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    code, output = init("--admin", "admin")
    assert code == 1 and "Passwords differ" in output and not init.config.exists()


def test_second_init_points_at_the_existing_file(init):
    assert init()[0] == 0
    code, output = init()
    assert code == 1 and str(init.config) in output


def test_status_of_a_stopped_server_says_so(init, monkeypatch, capsys):
    assert init()[0] == 0
    monkeypatch.setattr(sys, "argv", ["job-server", "status", "--config", str(init.config)])
    assert main() == 1
    assert "Server is OFF" in capsys.readouterr().err
