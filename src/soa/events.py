"""Olay akışı ve koşu kaydı (aşama 4).

Neden: `System.run` bugüne kadar yalnız **bitmiş** bir rapor döndürüyordu; arayüz
cevabı ancak iş bitince gösterebiliyordu ve koşunun kendisi hiçbir yerde kayıtlı
kalmıyordu (yeniden üretilemiyordu). Bu modül iki şeyi ayırır:

* ``Event`` — koşu sırasında olan biten (plan hazır, adım başladı/bitti, token).
  Çağıran isterse anında tüketir (akış), istemezse yok sayar.
* ``RunLog`` — aynı olayların günlük JSONL dosyasına satır satır yazımı. Satırlar
  bağımsızdır: yarıda kesilen koşu da okunabilir kalır (JSONL'in tek amacı bu).

Kayıt yeri varsayılanı ``~/.local/share/soa/runs/<gün>.jsonl``; ``SOA_RUN_DIR``
ile değiştirilebilir (testler ve ürünler kendi dizinini verir).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# Kapanmış sözleşme: yeni tür eklemek geriye uyumludur, adları değiştirmek değil.
EVENT_KINDS = (
    "plan_ready",
    "step_started",
    "step_finished",
    "token",
    "run_finished",
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    """Koşu sırasında üretilen tek bir olay."""

    kind: str
    run_id: str
    at: str = field(default_factory=_now)
    index: int | None = None
    action: str | None = None
    text: str = ""
    ok: bool | None = None
    error: str | None = None
    error_class: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSONL satırı. Boş alanlar yazılmaz: kayıt okunur kalsın."""
        satir: dict[str, Any] = {"at": self.at, "run_id": self.run_id, "kind": self.kind}
        for ad in ("index", "action", "ok", "error", "error_class"):
            deger = getattr(self, ad)
            if deger is not None:
                satir[ad] = deger
        if self.text:
            satir["text"] = self.text
        if self.data:
            satir["data"] = self.data
        return satir


EventSink = Callable[[Event], None]


def run_dir() -> Path:
    """Koşu kayıtlarının dizini (``SOA_RUN_DIR`` varsa o)."""
    ham = os.environ.get("SOA_RUN_DIR") or "~/.local/share/soa/runs"
    return Path(os.path.expanduser(ham))


class RunLog:
    """Olayları günlük JSONL dosyasına yazan kanca."""

    def __init__(self, dizin: Path | str | None = None) -> None:
        self.dizin = Path(dizin) if dizin is not None else run_dir()

    def path(self, gün: str | None = None) -> Path:
        ad = gün or datetime.now().strftime("%Y-%m-%d")
        return self.dizin / f"{ad}.jsonl"

    def __call__(self, event: Event) -> None:
        self.write(event)

    def write(self, event: Event) -> Path:
        hedef = self.path()
        hedef.parent.mkdir(parents=True, exist_ok=True)
        satir = json.dumps(event.as_dict(), ensure_ascii=False)
        # "a" + tek write: koşu yarıda kesse de yazılan satırlar bütün kalır.
        with hedef.open("a", encoding="utf-8") as dosya:
            dosya.write(satir + "\n")
        return hedef


def read_runs(dizin: Path | str | None = None, *, limit: int = 20, gün: str | None = None) -> list[dict]:
    """Son koşu satırları (en yeni önce). Bozuk satır atlanır, koşu durmaz."""
    kok = Path(dizin) if dizin is not None else run_dir()
    dosyalar = [kok / f"{gün}.jsonl"] if gün else sorted(kok.glob("*.jsonl"))
    satirlar: list[dict] = []
    for dosya in dosyalar:
        if not dosya.exists():
            continue
        for ham in dosya.read_text(encoding="utf-8").splitlines():
            ham = ham.strip()
            if not ham:
                continue
            try:
                satirlar.append(json.loads(ham))
            except json.JSONDecodeError:
                continue
    satirlar.reverse()
    return satirlar[:limit]


def runs(dizin: Path | str | None = None, *, limit: int = 20) -> list[dict]:
    """Koşu bazında özet: run_id → ilk/son olay, adım sayısı, sonuç."""
    özet: dict[str, dict] = {}
    # read_runs en yeniyi önce verir: ilk görülen satır koşunun SON olayıdır,
    # her adımda "started" geriye yazılır → döngü bitince en eski olay kalır.
    for satir in read_runs(dizin, limit=100_000):
        rid = satir.get("run_id")
        if not rid:
            continue
        kayit = özet.setdefault(
            rid,
            {
                "run_id": rid,
                "started": None,
                "finished": None,
                "goal": None,
                "steps": 0,
                "ok": None,
                "error_class": None,
                "tokens": 0,
            },
        )
        kayit["started"] = satir.get("at")
        if kayit["finished"] is None:
            kayit["finished"] = satir.get("at")
        hedef = (satir.get("data") or {}).get("goal")
        if hedef and not kayit["goal"]:
            kayit["goal"] = hedef
        if satir["kind"] == "plan_ready":
            kayit["steps"] = int(satir.get("data", {}).get("steps") or 0)
            kayit["source"] = satir.get("data", {}).get("source")
        elif satir["kind"] == "token":
            kayit["tokens"] += 1
        elif satir["kind"] == "run_finished":
            kayit["ok"] = satir.get("ok")
            kayit["error_class"] = satir.get("error_class")
    return list(özet.values())[:limit]


def compose(*sinks: EventSink | None) -> EventSink:
    """Birden çok kancayı tek kancaya indir (yok sayılanlar atlanır)."""
    aktif: Iterable[EventSink] = [s for s in sinks if s is not None]
    aktif = list(aktif)

    def _sink(event: Event) -> None:
        for sink in aktif:
            sink(event)

    return _sink


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
