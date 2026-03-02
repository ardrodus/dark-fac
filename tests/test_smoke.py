"""Smoke test: verify the dark_factory package is importable."""


def test_dark_factory_importable() -> None:
    import dark_factory

    assert hasattr(dark_factory, "__version__")
