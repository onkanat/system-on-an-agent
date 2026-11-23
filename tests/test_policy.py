from __future__ import annotations

import os
from pathlib import Path

import pytest

from soa.policy import Policy, PolicyError


@pytest.fixture
def kutu(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "measure.md").write_text("decode 53,2 tok/s\n", encoding="utf-8")
    return {"root": root, "outside": outside, "tmp": tmp_path}


def test_root_allows_read_and_write(kutu):
    policy = Policy(roots=[kutu["root"]])
    dosya = kutu["root"] / "a.md"
    dosya.write_text("x", encoding="utf-8")
    assert policy.allow(dosya) == dosya
    assert policy.allow(dosya, writing=True) == dosya


def test_outside_root_is_refused(kutu):
    policy = Policy(roots=[kutu["root"]])
    with pytest.raises(PolicyError, match="outside the allowed roots"):
        policy.allow(kutu["outside"] / "measure.md")


def test_trace_widens_reading_only(kutu):
    policy = Policy(roots=[kutu["root"]])
    target = kutu["outside"] / "measure.md"
    assert policy.remember("degisim: " + str(target) + ":12 +3/-1") == 1
    assert policy.allow(target) == target
    with pytest.raises(PolicyError, match="reading only"):
        policy.allow(target, writing=True)  # a traced file may be read, not overwritten


def test_trace_directory_covers_children(kutu):
    policy = Policy(roots=[kutu["root"]])
    policy.remember_path(str(kutu["outside"]))
    assert policy.allow(kutu["outside"] / "measure.md")


def test_clear_traces_forgets(kutu):
    policy = Policy(roots=[kutu["root"]])
    target = kutu["outside"] / "measure.md"
    policy.remember_path(str(target))
    policy.clear_traces()
    with pytest.raises(PolicyError):
        policy.allow(target)


def test_secret_file_never_allowed(kutu):
    secret = kutu["outside"] / ".env"
    secret.write_text("TOKEN=1\n", encoding="utf-8")
    policy = Policy(roots=[kutu["outside"]], follow_traces=True)
    assert policy.remember_path(str(secret)) is False
    with pytest.raises(PolicyError, match="secret file refused"):
        policy.allow(secret)
    with pytest.raises(PolicyError, match="secret file refused"):
        policy.allow(kutu["outside"] / "api.pem")


def test_system_roots_are_never_followed():
    policy = Policy(roots=[])
    for forbidden in ("/etc/hosts", str(Path.home() / ".ssh" / "id_rsa"),
                      str(Path.home() / ".hermes" / "config.yaml")):
        assert policy.remember_path(forbidden) is False


def test_missing_path_is_not_followed(kutu):
    policy = Policy(roots=[kutu["root"]])
    assert policy.remember_path(str(kutu["outside"] / "nope.md")) is False
    with pytest.raises(PolicyError, match="does not exist"):
        policy.allow(kutu["outside"] / "nope.md")


def test_relative_path_resolves_under_home():
    policy = Policy(roots=[])
    with pytest.raises(PolicyError):
        policy.allow("")
    # `~` expands; a path outside the roots is still refused, but never crashes.
    with pytest.raises(PolicyError):
        policy.allow("~")


def test_exact_file_allow_list(kutu):
    target = kutu["outside"] / "measure.md"
    policy = Policy(roots=[], files=[target])
    assert policy.allow(target) == target
    with pytest.raises(PolicyError):
        policy.allow(kutu["outside"] / "other.md")


def test_follow_traces_can_be_disabled(kutu):
    policy = Policy(roots=[], follow_traces=False)
    target = kutu["outside"] / "measure.md"
    assert policy.remember_path(str(target)) is True
    with pytest.raises(PolicyError):
        policy.allow(target)
