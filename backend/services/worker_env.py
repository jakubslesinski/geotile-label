"""Środowisko dla odłączonych procesów roboczych odpalanych z backendu.

`main.py` ustawia `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` **celowo**: pula renderu kafli
ma `min(8, cpu)` wątków, więc BLAS mnożony przez każdego workera rozsadziłby maszynę
(oversubscription). Ta decyzja jest słuszna dla procesu serwera.

Problem: `subprocess.Popen(...)` bez własnego `env` **dziedziczy** te zmienne, więc worker
liczący w osobnym procesie — gdzie nie ma z czym konkurować — również dostaje jeden wątek.
Na 16-rdzeniowej maszynie to czysta strata. Zmierzone na embeddingach DINO (dinov2_vits14,
512 chipów): 29,8 ms/chip przy `OMP=1` vs 3,5 ms/chip przy pełnych wątkach — **8,6×**, i to
bez udziału GPU, więc zysk dotyczy też instalacji bazowej z torchem CPU-only.

Zostawiamy zapas rdzeni: worker jest zadaniem tła, a backend i UI mają pozostać responsywne
w trakcie jego pracy. Nadpisanie: `GEOTILE_WORKER_THREADS`.
"""

from __future__ import annotations

import os

_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

# Ile rdzeni zostawiamy backendowi + UI, żeby aplikacja nie zamarła na czas analizy.
# Zapas PROPORCJONALNY, nie stały: przy stałych 2 rdzeniach 4-rdzeniowy laptop oddawałby
# połowę maszyny (2 z 4), a 16-rdzeniowa stacja tylko 12%. Górny limit 2 — powyżej 8 rdzeni
# większy zapas nie jest już potrzebny, żeby UI zostało płynne.
_HEADROOM_MAX = 2
_HEADROOM_DIVISOR = 4


def _headroom_cores(cpu: int) -> int:
    return max(1, min(_HEADROOM_MAX, cpu // _HEADROOM_DIVISOR))

# Duży matmul MKL (np. macierz podobieństw NxN w `near_duplicate_pairs`) wywala proces na
# Windows — abort bez wyjątku Pythona, więc `except` w workerze go NIE złapie: proces znika,
# a `state.json` zostaje na "running". Odtworzone na xView3 (11 146 obiektów, macierz 0,46 GB):
# proces ginie z kodem 127 NIEZALEŻNIE od liczby wątków (padało tak samo przy OMP=1, czyli
# jeszcze przed podniesieniem limitów) — a z warstwą SEQUENTIAL kończy się poprawnie.
_MKL_THREADING_LAYER = "SEQUENTIAL"


def worker_cpu_threads() -> int:
    """Liczba wątków obliczeniowych dla procesu roboczego (≥1)."""
    override = os.environ.get("GEOTILE_WORKER_THREADS")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass  # bezsensowna wartość nie może wywalić startu workera
    cpu = os.cpu_count() or 4
    return max(1, cpu - _headroom_cores(cpu))


def build_worker_env(threads: int | None = None) -> dict[str, str]:
    """Kopia środowiska z limitami wątków podniesionymi do wartości sensownej dla workera."""
    env = os.environ.copy()
    value = str(worker_cpu_threads() if threads is None else max(1, threads))
    for var in _THREAD_VARS:
        env[var] = value
    # Nie nadpisujemy jawnego wyboru użytkownika — inaczej wymuszamy warstwę sekwencyjną,
    # bo bez niej duży matmul przewraca proces (patrz komentarz przy stałej).
    env.setdefault("MKL_THREADING_LAYER", _MKL_THREADING_LAYER)
    return env
