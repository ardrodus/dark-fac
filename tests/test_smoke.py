"""Smoke test: verify the factory package is importable."""


def test_factory_importable() -> None:
    import factory  # noqa: F811

    assert hasattr(factory, "__version__")
