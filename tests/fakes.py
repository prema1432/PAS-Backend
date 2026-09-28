"""In-memory Supabase stand-ins shared by the test-suite.

They live here rather than in `conftest.py` so any test module can import them
directly; `conftest.py` only wires them into fixtures.

Two shapes matter and both are realistic:

* a **read** that works and a **write** that comes back empty — the row was
  deleted in between, or RLS silently filtered it;
* a **write** that raises — a unique violation, a permission error, a dropped
  connection.

The routers must answer those differently (4xx when they know why, 500 when the
write simply did not land), so the fakes let a test choose which one it wants.
"""


class FakeResponse:
    """Stand-in for supabase PostgrestAPIResponse."""

    def __init__(self, data):
        self.data = data


class FakeTable:
    """In-memory stand-in for a supabase table builder (eq filters are ANDed)."""

    def __init__(self, rows: list):
        self._rows = rows
        self._filters: dict = {}
        self._payload: dict | None = None
        self._delete = False
        self._desc = False
        self._limit: int | None = None

    def select(self, *_):
        return self

    def order(self, _column, desc: bool = False):
        self._desc = desc
        return self

    def insert(self, payload: dict):
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._payload = payload
        return self

    def delete(self):
        self._delete = True
        return self

    def eq(self, column: str, value):
        self._filters[column] = value
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def _matches(self, row: dict) -> bool:
        return all(row.get(col) == val for col, val in self._filters.items())

    # The real table carries global unique indexes on customers.email and
    # customers.phone (any owner); the fake mirrors them so 409 paths behave
    # like live Postgres.
    _UNIQUE_COLUMNS = ("email", "phone")

    def _raise_on_unique_violation(
        self, candidate: dict, exclude: list[dict] | None = None
    ) -> None:
        """Reject a write whose email/phone duplicates ANY row, any owner."""
        excluded_ids = {id(row) for row in exclude or []}
        for column in self._UNIQUE_COLUMNS:
            value = candidate.get(column)
            if value is None:
                continue
            for row in self._rows:
                if id(row) in excluded_ids:
                    continue
                if row.get(column) == value:
                    raise Exception(
                        f'duplicate key value violates unique constraint "customers_{column}_key"'
                    )

    def execute(self):
        matched = [row for row in self._rows if self._matches(row)]

        if self._delete:
            for row in matched:
                self._rows.remove(row)
            return FakeResponse(matched)

        if self._payload is not None and self._filters:
            matched_rows = list(matched)
            for row in matched_rows:
                merged = row | self._payload
                self._raise_on_unique_violation(merged, exclude=matched_rows)
                row.update(self._payload)
            return FakeResponse(matched)

        if self._payload is not None:  # insert
            self._raise_on_unique_violation(self._payload)
            row = {
                "id": max((r["id"] for r in self._rows), default=0) + 1,
                **self._payload,
            }
            self._rows.append(row)
            return FakeResponse([row])

        result = sorted(matched, key=lambda r: r["id"], reverse=self._desc)
        if self._limit is not None:
            result = result[: self._limit]
        return FakeResponse(result)


class EmptyTable:
    """A table builder where nothing ever comes back (every write matched no rows)."""

    def __getattr__(self, name):
        # Any builder method (eq, order, limit, ...) chains back to itself.
        return lambda *args, **kwargs: self

    def execute(self):
        return FakeResponse([])


class FakeSupabaseClient:
    """Stand-in for the supabase Client: `.table(name)` returns a builder."""

    def __init__(self, stores: dict[str, list]):
        self._stores = stores

    def table(self, name: str) -> FakeTable:
        return FakeTable(self._stores.setdefault(name, []))


class WriteEmptyClient(FakeSupabaseClient):
    """Reads behave normally, writes report no rows.

    Used for the routers' 500 paths: the insert/update reached Postgres but no
    row came back, so the caller must not be told it succeeded.
    """

    def __init__(self, stores: dict[str, list], tables: set[str] | None = None):
        super().__init__(stores)
        self._tables = tables

    def table(self, name: str) -> FakeTable:
        table = super().table(name)
        if self._tables is None or name in self._tables:
            table.insert = lambda payload: EmptyTable()
            table.update = lambda payload: EmptyTable()
        return table


class WriteErrorClient(FakeSupabaseClient):
    """Reads behave normally, writes raise the error the test chose."""

    def __init__(self, stores: dict[str, list], error: Exception, tables: set[str] | None = None):
        super().__init__(stores)
        self._error = error
        self._tables = tables

    def table(self, name: str) -> FakeTable:
        table = super().table(name)
        if self._tables is None or name in self._tables:

            def explode(*args, **kwargs):
                raise self._error

            table.insert = explode
            table.update = explode
        return table
