import assistant


def test_package_exposes_version() -> None:
    assert assistant.__version__ == "0.1.0"
