"""Utils"""
from pathlib import PosixPath


def get_pyversion():
    import sys

    _maj, _minor, *_ = sys.version_info
    return _maj, _minor


py_version = get_pyversion()

if py_version >= (3, 9):
    from importlib.resources import files
else:
    from importlib_resources import files

proj_name, *_ = __name__.split('.')

proj_files = files(proj_name)
data_files = proj_files / 'data'

STOPWORDS = frozenset(
    map(str.strip, (data_files / 'stopwords.txt').read_text().split('\n'))
)


def get_ticker_symbols():
    return (data_files / 'tickers.txt').read_text().split('\n')


def conditional_print(*args, condition, **kwargs):
    """Print on `condition`

    Intended use:

    >>> verbose = True  # in the args of a function, and then in the function do:
    >>> _print = partial(conditional_print, condition=verbose)
    >>> # ... then use _print within function
    """
    if condition:
        print(*args, **kwargs)
