from err_o_roadrunner import idle_status


def test_idle_until_failure() -> None:
    assert idle_status() == "idle"
