"""Olay akışı ve koşu kaydı — aşama 4 sözleşmesi.

Kabul (plandan): `System.run` olay yayar (`plan_ready`, `step_started`, `step_finished`,
`token`) ve koşu kaydı JSONL'e yazılır. Bu testler sözleşmeyi tek tek ölçer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from soa.events import EVENT_KINDS, Event, RunLog, new_run_id, read_runs, run_dir, runs
from soa.planner import LLMProvider
from soa.system import System


def _olaylar(sistem: System, hedef: str, **kw):
    toplanan: list[Event] = []
    rapor = sistem.run(hedef, on_event=toplanan.append, **kw)
    return rapor, toplanan


# --- olay akışı ---------------------------------------------------------------


def test_olay_sirasi_plan_adim_kapanis():
    rapor, olaylar = _olaylar(System(), "list skills")
    adlar = [o.kind for o in olaylar]
    assert adlar[0] == "plan_ready"
    assert adlar[-1] == "run_finished"
    assert "step_started" in adlar and "step_finished" in adlar
    assert set(adlar) <= set(EVENT_KINDS)
    # plan_ready adım sayısını taşır: arayüz ilerleme çubuğunu bundan kurar
    assert olaylar[0].data["steps"] == len(rapor.plan.steps)
    assert olaylar[0].run_id == rapor.run_id


def test_adim_olaylari_sirayla_eslesir():
    _, olaylar = _olaylar(System(), "list skills then list connectors")
    baslayan = [o for o in olaylar if o.kind == "step_started"]
    biten = [o for o in olaylar if o.kind == "step_finished"]
    assert [o.index for o in baslayan] == list(range(1, len(baslayan) + 1))
    assert [o.index for o in biten] == [o.index for o in baslayan]
    assert all(o.action for o in baslayan)


def test_dusen_adim_olayi_hata_sinifini_tasir():
    from soa.connectors import ConnectorRegistry

    class Patlak:
        name = "patlak"
        kind = "test"

        def send(self, payload, meta=None):  # noqa: ANN001, ANN201
            raise ConnectionError("down")

    class Planlayıcı:
        def plan(self, goal, skill_names):  # noqa: ANN001, ANN201
            from soa.planner import Plan, Step

            return Plan(goal=goal, steps=[Step("connector.send",
                                               {"connector": "patlak", "payload": "x"})])

    kayit = ConnectorRegistry([Patlak()])
    sistem = System(connectors=kayit, planner=Planlayıcı())
    rapor, olaylar = _olaylar(sistem, "patla")
    assert rapor.ok is False
    kapanis = [o for o in olaylar if o.kind == "step_finished"][-1]
    assert kapanis.ok is False
    assert kapanis.error_class == "connector"
    assert olaylar[-1].kind == "run_finished"
    assert olaylar[-1].ok is False
    assert olaylar[-1].error_class == "connector"


def test_on_event_verilmezse_kosum_calisir():
    rapor = System().run("list skills")
    assert rapor.ok is True
    assert rapor.run_id  # kimlik her koşuda üretilir


def test_olay_jsonl_satiri_bos_alanlari_atlar():
    satir = Event(kind="token", run_id="r1", text="ab").as_dict()
    assert satir == {"at": satir["at"], "run_id": "r1", "kind": "token", "text": "ab"}
    assert "error" not in satir


# --- koşu kaydı ---------------------------------------------------------------


def test_kosu_kaydi_yazilir_ve_okunur(tmp_path, monkeypatch):
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path))
    sistem = System()
    rapor = sistem.run("list skills")
    dosya = tmp_path / (Path(RunLog(tmp_path).path()).name)
    assert dosya.exists()
    satirlar = [json.loads(s) for s in dosya.read_text(encoding="utf-8").splitlines()]
    assert satirlar[0]["kind"] == "plan_ready"
    assert satirlar[-1]["kind"] == "run_finished"
    assert {s["run_id"] for s in satirlar} == {rapor.run_id}


def test_kayit_kapatilabilir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path))
    System().run("list skills", run_log=False)
    assert list(tmp_path.glob("*.jsonl")) == []


def test_cagri_bazinda_kayit_dizini(tmp_path):
    log = RunLog(tmp_path / "ozel")
    System().run("list skills", run_log=log)
    assert any((tmp_path / "ozel").glob("*.jsonl"))


def test_run_dir_env_ile_degisir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path / "x"))
    assert run_dir() == tmp_path / "x"


def test_bozuk_satir_okumayi_durdurmaz(tmp_path):
    dosya = tmp_path / "2026-01-01.jsonl"
    dosya.write_text('{"kind":"token","run_id":"a"}\nbozuk\n{"kind":"token","run_id":"b"}\n',
                     encoding="utf-8")
    satirlar = read_runs(tmp_path)
    assert [s["run_id"] for s in satirlar] == ["b", "a"]


def test_runs_ozeti_kosu_bazinda(tmp_path, monkeypatch):
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path))
    sistem = System()
    sistem.run("list skills")
    sistem.run("list connectors")
    özet = runs(tmp_path)
    assert len(özet) == 2
    assert all(k["ok"] is True for k in özet)
    assert all(k["started"] and k["finished"] for k in özet)


# --- token akışı --------------------------------------------------------------


class AkışlıSağlayıcı(LLMProvider):
    """Planı parça parça veren sağlayıcı: token olayları gerçekten akar."""

    def __init__(self) -> None:
        self.parçalar = ['[{"action": "system.list_skills",', ' "args": {}, "description": "x"}]']

    def complete(self, prompt: str, *, system: str | None = None) -> str:  # noqa: ARG002
        return "".join(self.parçalar)

    def stream(self, prompt: str, *, system: str | None = None):  # noqa: ARG002
        yield from self.parçalar


def test_token_olaylari_akis_sirasinda_gelir():
    sistem = System(llm=AkışlıSağlayıcı())
    rapor, olaylar = _olaylar(sistem, "list skills")
    tokenler = [o.text for o in olaylar if o.kind == "token"]
    assert "".join(tokenler) == "".join(AkışlıSağlayıcı().parçalar)
    assert rapor.plan.source == "llm"
    # token olayları plan_ready'den ÖNCE gelir (plan yazılırken akar)
    assert [o.kind for o in olaylar].index("token") < [o.kind for o in olaylar].index("plan_ready")


def test_akissiz_saglayici_tek_parca_verir():
    class Düz(LLMProvider):
        def complete(self, prompt: str, *, system: str | None = None) -> str:  # noqa: ARG002
            return '[{"action": "system.list_skills", "args": {}, "description": "x"}]'

    _, olaylar = _olaylar(System(llm=Düz()), "list skills")
    assert len([o for o in olaylar if o.kind == "token"]) == 1


def test_eski_planlayici_imzasi_bozulmaz():
    """Üçüncü taraf planlayıcı `plan(goal, skill_names)` yazmışsa akış onu kırmaz."""

    class EskiPlanlayıcı:
        def plan(self, goal, skill_names):  # noqa: ANN001, ANN201 - eski imza
            from soa.planner import Plan, Step

            return Plan(goal=goal, steps=[Step("system.noop")])

    _, olaylar = _olaylar(System(planner=EskiPlanlayıcı()), "hiçbir şey")
    assert [o.kind for o in olaylar][-1] == "run_finished"


def test_kosu_kimligi_jsonl_ile_raporda_ayni(tmp_path, monkeypatch):
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path))
    rapor = System().run("list skills")
    satirlar = [json.loads(s) for s in next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert satirlar[-1]["run_id"] == rapor.run_id == rapor.as_dict()["run_id"]
