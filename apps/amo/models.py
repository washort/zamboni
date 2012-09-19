import contextlib

from django.db import models

import multidb.pinning

#Imported for side effects (signal registration)
import amo.signals

from gelato.models.base import (SearchMixin, OnChangeMixin, UncachedManagerBase,
                                ManagerBase, ModelBase, FakeEmail)

__all__ = ['FakeEmail', 'SearchMixin', 'OnChangeMixin', 'UncachedManagerBase',
           'ManagerBase', 'ModelBase', 'manual_order']
@contextlib.contextmanager
def use_master():
    """Within this context, all queries go to the master."""
    old = getattr(multidb.pinning._locals, 'pinned', False)
    multidb.pinning.pin_this_thread()
    try:
        yield
    finally:
        multidb.pinning._locals.pinned = old


def manual_order(qs, pks, pk_name='id'):
    """
    Given a query set and a list of primary keys, return a set of objects from
    the query set in that exact order.
    """
    if not pks:
        return qs.none()
    return qs.filter(id__in=pks).extra(
            select={'_manual': 'FIELD(%s, %s)'
                % (pk_name, ','.join(map(str, pks)))},
            order_by=['_manual'])


class BlobField(models.Field):
    """MySQL blob column.

    This is for using AES_ENCYPT() to store values.
    It could maybe turn into a fancy transparent encypt/decrypt field
    like http://djangosnippets.org/snippets/2489/
    """
    description = "blob"

    def db_type(self, **kw):
        return 'blob'
