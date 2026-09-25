"""Layers 3 and 4 behind one provider interface. Offline: StubProvider (no network, output marked as stub). Live: the
user's Claude key from an environment variable, only with an explicit live flag. consistency and thesis_gap are computed
in code from numbers the model extracts; model text is never treated as data."""
