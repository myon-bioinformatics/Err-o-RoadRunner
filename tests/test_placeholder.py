from triage import EXIT_OK, main


def test_package_entrypoint_exists() -> None:
    assert main(["--exit-code", "0"]) == EXIT_OK
