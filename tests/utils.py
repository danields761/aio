from unittest.mock import Mock


def mock_wraps(cb, **mock_kwargs):
    if cb is not None:
        return Mock(wraps=cb, **mock_kwargs)
    else:
        return lambda cb: Mock(wraps=cb, **mock_kwargs)
