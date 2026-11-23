"""``Result.error_class`` — düşme sebebi metinden değil SINIFTAN okunur.

Neden (23.11.2026): geri düşme ve raporlama kararı `"connection refused" gibi metinlere
bakıyordu; sağlayıcı/kütüphane mesajı değiştiğinde karar sessizce bozuluyordu. Sınıf
tür adından türetilir, mesaj metnine hiç bakılmaz.
"""

from __future__ import annotations

import json

import pytest

from soa.bus import Bus
from soa.connectors import ConnectorRegistry
from soa.memory import Memory
from soa.planner import LLMProvider, Planner
from soa.skills import SkillLibrary
from soa.types import ERROR_CLASSES, Message, Result, classify_error


class PatlayanKonnektör:
    kind = "test"

    def __init__(self, exc: BaseException) -> None:
        self.name = "patlak"
        self.kind = "test"
        self._exc = exc

    def send(self, payload, meta=None):  # noqa: ANN001, ANN201 - test çifti
        raise self._exc


def _bus(exc: BaseException) -> Bus:
    kayit = ConnectorRegistry()
    kayit.register(PatlayanKonnektör(exc))
    return Bus(Memory(), SkillLibrary(), kayit)


@pytest.mark.parametrize(
    ("exc", "beklenen"),
    [
        (ConnectionError("connection refused"), "connector"),
        (TimeoutError("timed out"), "timeout"),
        (ValueError("bad payload"), "protocol"),
        (KeyError("yok"), "protocol"),
        (PermissionError("denied"), "policy"),
        (RuntimeError("tanımsız"), "unknown"),
    ],
)
def test_adim_hatasi_siniflandirilir(exc, beklenen):
    sonuc = _bus(exc).dispatch_step("connector.send", {"connector": "patlak", "payload": "x"})
    assert sonuc.ok is False
    assert sonuc.error_class == beklenen
    assert sonuc.error_class in ERROR_CLASSES


def test_policy_hatasi_policy_sinifi():
    class PolicyError(Exception):
        pass

    sonuc = _bus(PolicyError("izin yok")).dispatch_step(
        "connector.send", {"connector": "patlak", "payload": "x"}
    )
    assert sonuc.error_class == "policy"


def test_bilinmeyen_kanal_protocol():
    sonuc = _bus(RuntimeError("x")).dispatch(Message("kanal_yok", "eylem"))
    assert sonuc.error_class == "protocol"


def test_bilinmeyen_eylem_protocol():
    sonuc = _bus(RuntimeError("x")).dispatch_step("memory.olmayan_eylem", {})
    assert sonuc.error_class == "protocol"


def test_olmayan_konnektor_protocol():
    sonuc = _bus(RuntimeError("x")).dispatch_step("connector.send", {"connector": "yok", "payload": "x"})
    assert sonuc.error_class == "protocol"


def test_basarili_sonucta_sinif_yok():
    sonuc = Result(ok=True, data="ok")
    assert sonuc.error_class is None
    assert sonuc.text() == "ok"


def test_metin_sinifi_gosterir():
    assert Result(ok=False, error="patladı", error_class="timeout").text() == "[timeout] patladı"


def test_sinifsiz_hata_metni_degismez():
    assert Result(ok=False, error="patladı").text() == "patladı"


def test_classify_error_none():
    assert classify_error(None) == "unknown"


class Sağlayıcı(LLMProvider):
    def __init__(self, davranış) -> None:  # noqa: ANN001 - test
        self.davranış = davranış
        self.çağrı = 0

    def complete(self, prompt: str, *, system: str | None = None) -> str:  # noqa: ARG002
        self.çağrı += 1
        if isinstance(self.davranış, BaseException):
            raise self.davranış
        return self.davranış


def test_saglayici_cokerse_kural_planina_duser_ve_sebebi_yazar():
    plan = Planner(llm=Sağlayıcı(ConnectionError("down"))).plan("echo merhaba")
    assert plan.source == "rules"
    assert plan.fallback_reason == "provider"
    assert plan.steps


def test_saglayici_bozuk_json_dondurursa_protocol():
    plan = Planner(llm=Sağlayıcı("bu json değil")).plan("echo merhaba")
    assert plan.source == "rules"
    assert plan.fallback_reason == "protocol"


def test_saglayici_gecersiz_bicim_dondurursa_protocol():
    plan = Planner(llm=Sağlayıcı(json.dumps({"adım": 1}))).plan("echo merhaba")
    assert plan.source == "rules"
    assert plan.fallback_reason == "protocol"


def test_saglayici_calisirsa_sebep_yok():
    adım = json.dumps([{"action": "system.list_skills", "args": {}, "description": "liste"}])
    plan = Planner(llm=Sağlayıcı(adım)).plan("echo merhaba")
    assert plan.source == "llm"
    assert plan.fallback_reason is None


def test_saglayici_yoksa_sebep_de_yok():
    plan = Planner().plan("echo merhaba")
    assert plan.source == "rules"
    assert plan.fallback_reason is None


def test_rapor_dusen_adimin_sinifini_tasir():
    from soa.planner import Plan, Step
    from soa.system import RunReport

    sonuc = _bus(ConnectionError("down")).dispatch_step(
        "connector.send", {"connector": "patlak", "payload": "x"}
    )
    plan = Plan(goal="x", steps=[Step("connector.send", {"connector": "patlak"})])
    rapor = RunReport(goal="x", plan=plan, results=[sonuc], ok=False)
    assert rapor.ok is False
    assert rapor.error_class == "connector"
    veri = rapor.as_dict()
    assert veri["error_class"] == "connector"
    assert veri["results"][-1]["error_class"] == "connector"
    assert veri["fallback_reason"] is None  # sağlayıcı yok, düşme yok


def test_rapor_basarili_kosumda_sinif_yok():
    from soa.system import System

    rapor = System().run("list skills")
    assert rapor.ok is True
    assert rapor.error_class is None


def test_zaman_asimi_provider_yerine_timeout():
    plan = Planner(llm=Sağlayıcı(TimeoutError("yavaş"))).plan("echo merhaba")
    assert plan.fallback_reason == "timeout"
