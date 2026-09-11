"""Gramatyki nazw i regul specyficznych dla dostawcow (DESIGN_DECISIONS.md, scene-import P0.3+).

Klasy resolverow pozostaja w `services/scene_packages/resolvers.py` — tam jest rejestr
i tam sa wszyscy dostawcy razem. Tutaj laduje sama WIEDZA O DOSTAWCY: jak czytac nazwe
produktu, jak rozpoznac poziom przetworzenia i jak zwiazac sidecar z rastrem. Rozdzial jest
celowy: gramatyka jest czysta i testowalna bez systemu plikow, a resolver pozostaje cienka
warstwa decyzyjna.
"""
