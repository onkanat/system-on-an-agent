"""Shared message and result types used on the bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Hata sınıfları: bir adımın NEDEN düştüğünü metin eşleştirmesi yapmadan söyler.
# Ölçüldü (23.11.2026): tüketici taraf "connection refused" gibi metinlere bakıyordu;
# sağlayıcı hata mesajını değiştirdiğinde geri düşme kararı sessizce bozuluyordu.
ERROR_CLASSES = ("connector", "provider", "policy", "protocol", "timeout", "unknown")


def classify_error(exc: BaseException | None) -> str:
    """Bir istisnayı ``ERROR_CLASSES`` içinden tek bir sınıfa eşle.

    Sınıflandırma TÜR adına dayanır, metne değil: alt sistemler kendi istisna
    sınıflarını adlandırmakla yükümlü (``PolicyError`` → policy, sağlayıcı
    hataları → provider), böylece mesaj metni değişse de karar kaymaz.
    """
    if exc is None:
        return "unknown"
    adlar = {tip.__name__ for tip in type(exc).__mro__}
    if "PolicyError" in adlar or "PermissionError" in adlar:
        return "policy"
    if "TimeoutError" in adlar or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connector"
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError, IndexError)):
        return "protocol"
    if "Provider" in type(exc).__name__:
        return "provider"
    return "unknown"


@dataclass
class Message:
    """A unit of work routed by the bus."""

    channel: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "core"


@dataclass
class Result:
    """Outcome of a bus dispatch or a plan step."""

    ok: bool
    data: Any = None
    error: str | None = None
    action: str | None = None
    # Set when the step failed by raising: the bus turns the exception into a
    # failed Result instead of letting it escape, and keeps it here so callers
    # (the run loop, add-on hooks) can still inspect what actually went wrong.
    exception: BaseException | None = None
    # ``ERROR_CLASSES`` içinden bir değer; geri düşme/raporlama kararı buna bakar.
    # None: hata yok ya da sınıflandırılmadı (eski çağıranlar için geriye uyumlu).
    error_class: str | None = None

    def text(self) -> str:
        """Human-readable form of the result."""
        if not self.ok:
            etiket = f"[{self.error_class}] " if self.error_class else ""
            return f"{etiket}{self.error or 'error'}"
        if self.data is None:
            return "ok"
        return str(self.data)
