import sqlite3
import threading
from contextlib import contextmanager
from logging import getLogger
from os import makedirs
from os.path import abspath, basename, dirname, expanduser
from pathlib import Path
from typing import Type, Union

from . import BaseCache, BaseStorage, get_valid_kwargs

logger = getLogger(__name__)


class DbCache(BaseCache):
    """SQLite cache backend.

    This class is thread-safe, but note that multi-threaded SQLite usage does not increase
    performance, and can potentially reduce performance slightly. See the
    `SQLite FAQ <https://www.sqlite.org/faq.html>`_ for more details.

    Args:
        db_path: Database file path (expands user paths and creates parent dirs)
        fast_save: Speedup cache saving up to 50 times but with possibility of data loss.
            See :py:class:`.DbDict` for more info
        kwargs: Additional keyword arguments for :py:func:`sqlite3.connect`
    """

    def __init__(self, db_path: Union[Path, str] = 'http_cache', fast_save: bool = False, **kwargs):
        super().__init__(**kwargs)
        db_path = _get_db_path(db_path)
        self.responses = DbPickleDict(db_path, table_name='responses', fast_save=fast_save, **kwargs)
        self.redirects = DbDict(db_path, table_name='redirects', **kwargs)
        # self.responses = DbPickleDict(db_path, 'responses', fast_save=fast_save, **kwargs)
        # self.redirects = DbDict(db_path, 'redirects', connection_factory=self.responses._conn, **kwargs)

    def remove_expired_responses(self, *args, **kwargs):
        """Remove expired responses from the cache, with additional cleanup"""
        super().remove_expired_responses(*args, **kwargs)
        self.responses.vacuum()
        self.redirects.vacuum()


class DbDict(BaseStorage):
    """A dictionary-like interface for SQLite.

    It's possible to create multiply DbDict instances, which will be stored as separate
    tables in one database::

        d1 = DbDict('test', 'table1')
        d2 = DbDict('test', 'table2')
        d3 = DbDict('test', 'table3')

    All data will be stored in separate tables in the file ``test.sqlite``.

    Args:
        db_path: Database file path
        table_name: Table name
        connection: A :py:class:`.ConnectionFactory` to use instead of creating a new one
        kwargs: Additional keyword arguments for :py:func:`sqlite3.connect`
    """

    def __init__(self, db_path, table_name='http_cache', fast_save: bool = False, connection_factory=None, **kwargs):
        kwargs.setdefault('suppress_warnings', True)
        super().__init__(**kwargs)
        self.connection_kwargs = get_valid_kwargs(sqlite_template, kwargs)
        self.db_path = db_path
        self.fast_save = fast_save
        self.table_name = table_name

        self._init_db()
        self._can_commit = True
        # self._conn = connection_factory or ConnectionFactory(db_path, **kwargs)
        self._local_context = threading.local()

    def _init_db(self):
        with sqlite3.connect(self.db_path, **self.connection_kwargs) as con:
            con.execute(f'CREATE TABLE IF NOT EXISTS `{self.table_name}` (key PRIMARY KEY, value)')

    # @contextmanager
    # def connection(self, commit=False) -> sqlite3.Connection:
    #     with self._conn.connection(commit=commit) as connection:
    #         yield connection

    @contextmanager
    def connection(self, commit=False):
        if not hasattr(self._local_context, "con"):
            logger.debug(f'Opening connection to {self.db_path}:{self.table_name}')
            self._local_context.con = sqlite3.connect(self.db_path, **self.connection_kwargs)
            if self.fast_save:
                self._local_context.con.execute("PRAGMA synchronous = 0;")
        yield self._local_context.con
        if commit and self._can_commit:
            self._local_context.con.commit()

    def __del__(self):
        if hasattr(self._local_context, "con"):
            self._local_context.con.close()

    @contextmanager
    def bulk_commit(self):
        """Context manager used to keep a connection open for a large number of consecutive requests

        Example:

            >>> cache = DbDict('test')
            >>> with cache.bulk_commit():
            ...     for i in range(1000):
            ...         cache[f'key_{i}'] = i * 2

        """
        self._can_commit = False
        try:
            yield
            if hasattr(self._local_context, "con"):
                self._local_context.con.commit()
        finally:
            self._can_commit = True

        # self._conn.open()
        # try:
        #     yield
        #     self._conn.commit()
        # finally:
        #     self._conn.close()

    def __getitem__(self, key):
        with self.connection() as con:
            row = con.execute(f'SELECT value FROM `{self.table_name}` WHERE key=?', (key,)).fetchone()
        # raise error after the with block, otherwise the connection will be locked
        if not row:
            raise KeyError
        return row[0]

    # def close(self):
    #     """Close the connection, if currently open"""
    #     self._conn.close()

    def __setitem__(self, key, item):
        with self.connection(commit=True) as con:
            con.execute(
                f'INSERT OR REPLACE INTO `{self.table_name}` (key,value) values (?,?)',
                (key, item),
            )

    def __delitem__(self, key):
        with self.connection(True) as con:
            cur = con.execute(f'DELETE FROM `{self.table_name}` WHERE key=?', (key,))
        if not cur.rowcount:
            raise KeyError

    def __iter__(self):
        with self.connection() as con:
            for row in con.execute(f'SELECT key FROM `{self.table_name}`'):
                yield row[0]

    def __len__(self):
        with self.connection() as con:
            return con.execute(f'SELECT COUNT(key) FROM `{self.table_name}`').fetchone()[0]

    def clear(self):
        with self.connection(True) as con:
            con.execute(f'DROP TABLE IF EXISTS`{self.table_name}`')
            con.execute(f'CREATE TABLE `{self.table_name}` (key PRIMARY KEY, value)')
            con.execute('VACUUM')

    def vacuum(self):
        with self.connection(commit=True) as con:
            con.execute('VACUUM')

    def __str__(self):
        return str(dict(self.items()))


class DbPickleDict(DbDict):
    """Same as :class:`DbDict`, but pickles values before saving"""

    def __setitem__(self, key, item):
        super().__setitem__(key, sqlite3.Binary(self.serialize(item)))

    def __getitem__(self, key):
        return self.deserialize(super().__getitem__(key))


class ConnectionFactory:
    """Class that wraps SQLite connections. May be used to either create multiple (shared)
    short-lived connections, or one long-lived connection per thread.
    """

    def __init__(self, db_path, fast_save: bool = False, **kwargs):
        self.db_path = db_path
        self.fast_save = fast_save

        # Allow concurrent reads, and use a lock to prevent concurrent writes
        self.connection_kwargs = get_valid_kwargs(sqlite_template, kwargs)
        self.connection_kwargs.setdefault('check_same_thread', False)
        self._lock = threading.RLock()
        self._threadlocal = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new connection object"""
        logger.debug(f'Opening connection to {self.db_path}')
        connection = sqlite3.connect(self.db_path, **self.connection_kwargs)
        if self.fast_save:
            connection.execute('PRAGMA synchronous = 0')
        return connection

    @contextmanager
    def connection(self, commit=False) -> sqlite3.Connection:
        """Get the currently active connection, if any; otherwise create a new connection that will
        be closed after use."""
        if self.is_open:
            # TODO: commit afterward if commit=True, except during bulk_commit?
            yield self._threadlocal.connection
        else:
            ctx = self.autocommit_connection if commit else self.closing_connection
            with ctx() as connection:
                yield connection

    @contextmanager
    def autocommit_connection(self) -> sqlite3.Connection:
        # Use a lock for write operations
        with self._lock, self.closing_connection() as connection:
            yield connection
            connection.commit()

    @contextmanager
    def closing_connection(self) -> sqlite3.Connection:
        connection = self._get_connection()
        try:
            yield connection
        finally:
            connection.close()

    @property
    def is_open(self) -> bool:
        return getattr(self._threadlocal, 'connection', None) is not None

    def open(self):
        if not self.is_open:
            self._threadlocal.connection = self._get_connection()

    def close(self):
        if self.is_open:
            self._threadlocal.connection.close()
            self._threadlocal.connection = None

    def commit(self):
        if self.is_open:
            self._threadlocal.connection.commit()


def _get_db_path(db_path):
    """Get resolved path for database file"""
    # Allow paths with user directories (~/*), and add file extension if not specified
    db_path = abspath(expanduser(str(db_path)))
    if '.' not in basename(db_path):
        db_path += '.sqlite'
    # Make sure parent dirs exist
    makedirs(dirname(db_path), exist_ok=True)
    return db_path


def sqlite_template(
    timeout: float = 5.0,
    detect_types: int = 0,
    isolation_level: str = None,
    check_same_thread: bool = True,
    factory: Type = None,
    cached_statements: int = 100,
    uri: bool = False,
):
    """Template function to get an accurate signature for the builtin :py:func:`sqlite3.connect`"""
