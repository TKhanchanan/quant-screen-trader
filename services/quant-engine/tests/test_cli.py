from __future__ import annotations

import pytest
from quant_engine.__main__ import build_parser


def test_cli_accepts_explicit_local_options() -> None:
    args = build_parser().parse_args(
        ["--host", "::1", "--port", "9000", "--data-dir", "runtime", "--log-level", "debug"]
    )

    assert args.host == "::1"
    assert args.port == 9000
    assert str(args.data_dir) == "runtime"
    assert args.log_level == "debug"


def test_cli_rejects_network_visible_host() -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["--host", "0.0.0.0"])

    assert error.value.code == 2
