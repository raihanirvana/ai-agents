"""Bind existing services to a caller's transaction; the caller owns commit/rollback."""
from contextlib import nullcontext
from copy import copy


class TransactionDatabase:
    def __init__(self, session):
        self.session = session

    def read(self):
        return nullcontext(self.session)

    def write(self):
        return nullcontext(self.session)


def bind_service(service, session):
    bound = copy(service)
    bound.db = TransactionDatabase(session)
    return bound
